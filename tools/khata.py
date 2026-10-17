"""Khata (credit ledger) tools: charge, settle, balance lookup."""
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from langchain_core.tools import InjectedToolArg
from sqlmodel import Session, col, select

from db.engine import get_session
from db.models import KhataLedger

VALID_ACTIONS = {"charge", "settle"}


def _round2(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _find_ledgers(session: Session, customer_name: str) -> list[KhataLedger]:
    needle = customer_name.strip().lower()
    return list(session.exec(select(KhataLedger).where(col(KhataLedger.name).ilike(f"%{needle}%"))).all())


def _adjust_khata(
    session: Session,
    customer_name: str,
    amount: float,
    action: str,
    *,
    create_if_missing: bool,
    customer_id: str | None = None,
) -> tuple[KhataLedger | None, str | None]:
    """Apply a charge/settle to a customer's khata ledger within the given session.

    customer_id, when given, resolves by exact primary key instead of name matching —
    used only to confirm an already-disambiguated tap-to-select pick, where
    re-running name matching on the picked label could re-trigger the same
    ambiguity (e.g. picking "Ramesh" when "Ramesh" and "Ramesh Kumar" both exist).

    Returns (ledger, error). On error, ledger is None and the caller must not commit
    any mutation tied to this adjustment.
    """
    if action not in VALID_ACTIONS:
        return None, f"REFUSED: action must be one of {sorted(VALID_ACTIONS)}."
    if amount <= 0:
        return None, "REFUSED: Amount must be positive."
    name = customer_name.strip()
    if not name:
        return None, "REFUSED: customer_name is required."

    if customer_id is not None:
        ledger = session.get(KhataLedger, customer_id)
        if ledger is None:
            return None, f"NOT_FOUND: No credit ledger exists for '{name}'."
    else:
        matches = _find_ledgers(session, name)

        if not matches:
            if action == "settle":
                return None, f"REFUSED: No credit ledger exists for '{name}'. Nothing to settle."
            if not create_if_missing:
                return None, f"NOT_FOUND: No credit ledger exists for '{name}'."
            ledger = KhataLedger(customer_id=uuid.uuid4().hex, name=name, balance=0.0)
            session.add(ledger)
            session.flush()
        elif len(matches) > 1:
            names = "' and '".join(m.name for m in matches)
            return None, f"AMBIGUOUS: Found '{names}'. Clarify which customer."
        else:
            ledger = matches[0]

    if action == "charge":
        ledger.balance = _round2(Decimal(str(ledger.balance)) + Decimal(str(amount)))
    else:  # settle
        if amount > ledger.balance:
            return None, (
                f"REFUSED: Settle amount ₹{amount} exceeds {ledger.name}'s outstanding "
                f"balance ₹{ledger.balance}."
            )
        ledger.balance = _round2(Decimal(str(ledger.balance)) - Decimal(str(amount)))

    session.add(ledger)
    return ledger, None


def modify_khata_credit(
    customer_name: str,
    amount: float,
    action: str,
    chat_id: str | None = None,
    customer_id: Annotated[str | None, InjectedToolArg] = None,
) -> str:
    """Charge or settle a customer's khata (credit ledger).

    Args:
        customer_name: Customer's name (fuzzy substring match against existing ledgers).
        amount: Positive rupee amount.
        action: "charge" to put money on their credit (self-heals a new ledger if the
            customer doesn't exist yet), or "settle" to record a payment against an
            existing balance (refused if no ledger exists, or if it would overpay).
        chat_id: Telegram chat id, for passthrough/logging only.
        customer_id: Internal only — set automatically when confirming a
            tap-to-select pick. Never pass this yourself.

    Returns:
        Success string with the updated balance, or REFUSED/NOT_FOUND/AMBIGUOUS.
    """
    with get_session() as session:
        was_new = customer_id is None and not _find_ledgers(session, customer_name)
        ledger, error = _adjust_khata(
            session, customer_name, amount, action, create_if_missing=True, customer_id=customer_id
        )
        if error:
            return error
        session.commit()
        session.refresh(ledger)
        created_note = " (new khata opened)" if was_new and action == "charge" else ""
        return f"Success: {ledger.name}'s balance updated{created_note}. Current Balance: ₹{ledger.balance}."


def get_khata_balance(
    customer_name: str,
    customer_id: Annotated[str | None, InjectedToolArg] = None,
) -> str:
    """Look up a customer's outstanding khata (credit) balance.

    Args:
        customer_name: Customer's name (fuzzy substring match).
        customer_id: Internal only — set automatically when confirming a
            tap-to-select pick. Never pass this yourself.

    Returns:
        Balance string, or NOT_FOUND/AMBIGUOUS.
    """
    with get_session() as session:
        if customer_id is not None:
            ledger = session.get(KhataLedger, customer_id)
            if ledger is None:
                return f"NOT_FOUND: No credit ledger exists for '{customer_name}'."
            return f"{ledger.name}'s outstanding balance: ₹{ledger.balance}."

        matches = _find_ledgers(session, customer_name)
        if not matches:
            return f"NOT_FOUND: No credit ledger exists for '{customer_name}'."
        if len(matches) > 1:
            names = "' and '".join(m.name for m in matches)
            return f"AMBIGUOUS: Found '{names}'. Clarify which customer."
        ledger = matches[0]
        return f"{ledger.name}'s outstanding balance: ₹{ledger.balance}."


def khata_dues_report(chat_id: str) -> str:
    """Report every customer with an outstanding khata (credit) balance, highest first.

    Store-wide, read-only, makes no changes. Reminds the owner who still owes money —
    there's no separate channel to message customers directly, so this surfaces dues
    to the owner rather than notifying customers.

    Args:
        chat_id: Telegram chat id (accepted for agent wiring parity, unused for scope).

    Returns:
        Multi-line text listing each customer with a positive balance, sorted
        highest-first, or a plain "no outstanding dues" line if nothing qualifies.
    """
    with get_session() as session:
        ledgers = session.exec(select(KhataLedger).where(KhataLedger.balance > 0)).all()

    if not ledgers:
        return "No outstanding khata dues."

    ledgers = sorted(ledgers, key=lambda l: l.balance, reverse=True)
    total = _round2(sum(Decimal(str(l.balance)) for l in ledgers))
    lines = ["**Outstanding khata dues:**"]
    for ledger in ledgers:
        lines.append(f"- {ledger.name}: ₹{ledger.balance}")
    lines.append("")
    lines.append(f"**Total outstanding:** ₹{total}")
    return "\n".join(lines)
