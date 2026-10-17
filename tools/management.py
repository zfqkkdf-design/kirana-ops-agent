"""Daily close aggregation tool: revenue, payment mix, top sellers, reorder alerts."""
import json
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlmodel import select

from db.engine import get_session
from db.models import Product, Sale

VALID_PAYMENT_MODES = ("cash", "upi", "card", "khata")


def _round2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _today_bounds() -> tuple[datetime, datetime]:
    """Local calendar day 00:00:00-23:59:59.999999, expressed as naive UTC.

    Sale.timestamp is written with datetime.utcnow(), so bounds must match that
    clock, not local time, for the range query to line up.
    """
    now_utc = datetime.utcnow()
    start = datetime.combine(now_utc.date(), time.min)
    end = start + timedelta(days=1) - timedelta(microseconds=1)
    return start, end


def _date_bounds(start_date: str, end_date: str) -> tuple[datetime, datetime]:
    """Inclusive [start_date, end_date] calendar range as naive-UTC datetime bounds.

    Raises ValueError on unparseable dates or start_date after end_date — the
    caller (a tool-facing function) is expected to turn that into a refusal
    rather than let a traceback surface to the owner.
    """
    start = datetime.combine(datetime.strptime(start_date, "%Y-%m-%d").date(), time.min)
    end = datetime.combine(datetime.strptime(end_date, "%Y-%m-%d").date(), time.min) + timedelta(days=1) - timedelta(microseconds=1)
    if start > end:
        raise ValueError("start_date is after end_date")
    return start, end


def _aggregate_sales(sales: list) -> dict:
    """Shared revenue/tax/payment-mix/velocity rollup over an already-fetched list of Sale rows."""
    subtotal = Decimal("0")
    cgst = Decimal("0")
    sgst = Decimal("0")
    total = Decimal("0")
    payments = {mode: Decimal("0") for mode in VALID_PAYMENT_MODES}
    velocity: dict[str, dict] = {}

    for sale in sales:
        subtotal += Decimal(str(sale.subtotal))
        cgst += Decimal(str(sale.cgst))
        sgst += Decimal(str(sale.sgst))
        total += Decimal(str(sale.total))
        payments[sale.payment_mode] = payments.get(sale.payment_mode, Decimal("0")) + Decimal(str(sale.total))

        for item in json.loads(sale.items_json):
            entry = velocity.setdefault(item["sku_id"], {"name": item["name"], "qty": 0})
            entry["qty"] += item["qty"]

    top_items = sorted(velocity.items(), key=lambda kv: (-kv[1]["qty"], kv[0]))[:6]

    return {
        "sales_count": len(sales),
        "subtotal": _round2(subtotal),
        "cgst": _round2(cgst),
        "sgst": _round2(sgst),
        "total": _round2(total),
        "payments": {mode: _round2(amount) for mode, amount in payments.items()},
        "top_items": [{"name": entry["name"], "qty": entry["qty"]} for _, entry in top_items],
    }


def _current_low_stock(session) -> list[dict]:
    rows = session.exec(select(Product)).all()
    return [
        {"name": p.name, "quantity": p.quantity, "reorder_level": p.reorder_level}
        for p in rows
        if p.quantity <= p.reorder_level
    ]


def compute_day_close(chat_id: str) -> dict:
    """Aggregate today's store-wide daily close into a structured dict.

    Store-wide for "today" — not filtered to this chat. Read-only, makes no changes.

    Args:
        chat_id: Telegram chat id (accepted for agent wiring parity, unused for scope).

    Returns:
        Dict with date, sales_count, subtotal/cgst/sgst/total (Decimal), payments
        (dict of mode -> Decimal), top_items (list of up to 6 {name, qty}), and
        low_stock (list of {name, quantity, reorder_level}).
    """
    start, end = _today_bounds()

    with get_session() as session:
        sales = session.exec(
            select(Sale).where(Sale.timestamp >= start, Sale.timestamp <= end)
        ).all()
        result = _aggregate_sales(sales)
        result["low_stock"] = _current_low_stock(session)

    result["date"] = start.date().isoformat()
    return result


def compute_overall_summary(chat_id: str) -> dict:
    """Aggregate the store's entire sales history into a structured dict.

    Store-wide, across all recorded sales (not scoped to today) — backs the
    business-analysis deck, which reports on the store overall rather than a
    single day. Read-only, makes no changes.

    Args:
        chat_id: Telegram chat id (accepted for agent wiring parity, unused for scope).

    Returns:
        Dict with period_start/period_end (ISO dates, None if no sales yet),
        sales_count, subtotal/cgst/sgst/total (Decimal), payments (dict of mode ->
        Decimal), top_items (list of up to 6 {name, qty}), and low_stock
        (list of {name, quantity, reorder_level}, current stock state).
    """
    with get_session() as session:
        sales = session.exec(select(Sale)).all()
        result = _aggregate_sales(sales)
        result["low_stock"] = _current_low_stock(session)

        timestamps = [sale.timestamp for sale in sales]

    result["period_start"] = min(timestamps).date().isoformat() if timestamps else None
    result["period_end"] = max(timestamps).date().isoformat() if timestamps else None
    return result


def compute_summary_for_range(chat_id: str, start_date: str, end_date: str) -> dict:
    """Aggregate sales over an explicit inclusive date range into a structured dict.

    Store-wide, read-only, makes no changes. Backs date-scoped analysis decks —
    "yesterday's sales", "last month", "this week" — where the caller (the model)
    has already resolved the natural-language period into concrete ISO dates.

    Args:
        chat_id: Telegram chat id (accepted for agent wiring parity, unused for scope).
        start_date: Inclusive range start, "YYYY-MM-DD".
        end_date: Inclusive range end, "YYYY-MM-DD". Must not be before start_date.

    Returns:
        Dict with period_start/period_end (the requested range, echoed back),
        sales_count, subtotal/cgst/sgst/total (Decimal), payments (dict of mode ->
        Decimal), top_items (list of up to 6 {name, qty}), and low_stock
        (list of {name, quantity, reorder_level}, current stock state).

    Raises:
        ValueError: start_date/end_date aren't valid "YYYY-MM-DD" dates, or
            start_date is after end_date.
    """
    start, end = _date_bounds(start_date, end_date)

    with get_session() as session:
        sales = session.exec(
            select(Sale).where(Sale.timestamp >= start, Sale.timestamp <= end)
        ).all()
        result = _aggregate_sales(sales)
        result["low_stock"] = _current_low_stock(session)

    result["period_start"] = start.date().isoformat()
    result["period_end"] = end.date().isoformat()
    return result


def low_stock_report(chat_id: str) -> str:
    """Report products at or below their reorder level, right now — no revenue/tax involved.

    Store-wide, read-only, makes no changes. Standalone from close_day_summary so a
    plain "what's running out?" doesn't drag the owner through a full daily close.

    Args:
        chat_id: Telegram chat id (accepted for agent wiring parity, unused for scope).

    Returns:
        Multi-line text listing each low-stock product with its quantity and reorder
        level, or a plain "no low stock" line if nothing qualifies.
    """
    with get_session() as session:
        rows = session.exec(select(Product)).all()
        low_stock = [
            {"name": p.name, "quantity": p.quantity, "reorder_level": p.reorder_level}
            for p in rows
            if p.quantity <= p.reorder_level
        ]

    if not low_stock:
        return "No products at or below their reorder level."

    lines = ["**Low stock:**"]
    for product in low_stock:
        lines.append(f"- {product['name']} — qty {product['quantity']} (reorder {product['reorder_level']})")
    return "\n".join(lines)


def close_day_summary(chat_id: str) -> str:
    """Compile today's store-wide daily close: revenue, tax, payment mix, top sellers, low stock.

    Store-wide for "today" — not filtered to this chat. Read-only, makes no changes.

    Args:
        chat_id: Telegram chat id (accepted for agent wiring parity, unused for scope).

    Returns:
        Multi-line text summary of today's sales, tax, payment breakdown, top-3
        sellers by quantity, and any products at or below their reorder level.
    """
    metrics = compute_day_close(chat_id)

    lines = [
        f"**Daily close ({metrics['date']}):**",
        f"Sales: {metrics['sales_count']}",
        f"Subtotal: ₹{metrics['subtotal']}  CGST: ₹{metrics['cgst']}  SGST: ₹{metrics['sgst']}",
        f"TOTAL: ₹{metrics['total']}",
        "",
        "**Payments:**",
    ]
    for mode in VALID_PAYMENT_MODES:
        lines.append(f"- {mode}: ₹{metrics['payments'][mode]}")

    lines.append("")
    lines.append("**Top items:**")
    if metrics["top_items"]:
        for idx, entry in enumerate(metrics["top_items"], start=1):
            lines.append(f"{idx}. {entry['name']} — {entry['qty']} sold")
    else:
        lines.append("No sales yet today.")

    lines.append("")
    lines.append("**Low stock:**")
    if metrics["low_stock"]:
        for product in metrics["low_stock"]:
            lines.append(f"- {product['name']} — qty {product['quantity']} (reorder {product['reorder_level']})")
    else:
        lines.append("None")

    return "\n".join(lines)
