"""Bill-draft and finalize tools, plus the idempotency helper."""
import json
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from langchain_core.tools import InjectedToolArg

from db.engine import get_session
from db.models import Batch, BillDraft, ProcessedMessage, Product, Sale
from tools._batches import allocate_fefo, sync_product_cache
from tools._resolve import resolve_product
from tools.khata import _adjust_khata

VALID_PAYMENT_MODES = {"cash", "upi", "card", "khata"}


def _round2(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _load_items(draft: BillDraft) -> list[dict]:
    return json.loads(draft.items_json) if draft.items_json else []


def _compute_totals(items: list[dict]) -> tuple[float, float, float, float]:
    subtotal = 0.0
    cgst_total = 0.0
    sgst_total = 0.0
    for item in items:
        taxable = item["mrp"] * item["qty"]
        gst = taxable * item["gst_slab"]
        cgst_total += _round2(gst / 2)
        sgst_total += _round2(gst / 2)
        subtotal += taxable

    subtotal = _round2(subtotal)
    cgst_total = _round2(cgst_total)
    sgst_total = _round2(sgst_total)
    total = _round2(subtotal + cgst_total + sgst_total)
    return subtotal, cgst_total, sgst_total, total


def _format_bill_markdown(
    title: str, items: list[dict], subtotal: float, cgst_total: float, sgst_total: float, total: float
) -> str:
    """One fixed Telegram-Markdown layout for every bill display — draft preview,
    finalize confirmation — so the shape is identical regardless of which model
    is relaying it. The model is instructed to relay this verbatim, not reformat it.
    """
    lines = "\n".join(f"- {i['qty']} × {i['name']} @ ₹{i['mrp']}" for i in items)
    return (
        f"**{title}:**\n{lines}\n\n"
        f"**Subtotal:** ₹{subtotal}\n"
        f"**CGST:** ₹{cgst_total}\n"
        f"**SGST:** ₹{sgst_total}\n"
        f"**TOTAL:** ₹{total}"
    )


def view_bill_draft(chat_id: str) -> str:
    """Show the current in-progress bill for this chat WITHOUT finalizing it — stock and draft are untouched.

    Args:
        chat_id: Telegram chat id whose draft should be previewed.

    Returns:
        A Telegram-Markdown preview of line items and running totals, or a message
        saying the bill is empty.
    """
    with get_session() as session:
        draft = session.get(BillDraft, chat_id)
        items = _load_items(draft) if draft else []
        if not items:
            return "No bill in progress for this chat."

        subtotal, cgst_total, sgst_total, total = _compute_totals(items)
        return _format_bill_markdown(
            "Bill in progress (PREVIEW, not finalized)", items, subtotal, cgst_total, sgst_total, total
        )


def add_to_bill_draft(
    chat_id: str,
    item_name: str,
    quantity: float,
    sku_id: Annotated[str | None, InjectedToolArg] = None,
) -> str:
    """Add a quantity of an item to the in-progress bill for this chat.

    Draws the added quantity FEFO across the product's live batches — if the
    running total for this item spans more than one batch (e.g. leftover older
    stock plus a newer, differently priced delivery), the draft carries a
    separate line per batch at that batch's own MRP/GST, not one blended price.

    Args:
        chat_id: Telegram chat id the draft belongs to.
        item_name: Product name or partial name.
        quantity: Quantity to add (merged with any existing line(s) for the same SKU).
        sku_id: Internal only — set automatically when confirming a tap-to-select
            pick. Never pass this yourself.

    Returns:
        Confirmation string, or REFUSED/NOT_FOUND/AMBIGUOUS.
    """
    if quantity <= 0:
        return "REFUSED: Quantity must be positive."

    with get_session() as session:
        result = resolve_product(session, item_name, sku_id=sku_id)
        if isinstance(result, str):
            return result
        product = result

        draft = session.get(BillDraft, chat_id)
        if draft is None:
            draft = BillDraft(chat_id=chat_id, items_json="[]")

        items = _load_items(draft)
        existing_qty = sum(i["qty"] for i in items if i["sku_id"] == product.sku_id)
        new_total_qty = existing_qty + quantity

        allocation = allocate_fefo(session, product.sku_id, new_total_qty)
        if isinstance(allocation, str):
            return f"REFUSED: Only {product.quantity} {product.name} left in stock."

        items = [i for i in items if i["sku_id"] != product.sku_id]
        for alloc in allocation:
            items.append({
                "sku_id": product.sku_id,
                "batch_id": alloc["batch_id"],
                "name": product.name,
                "qty": alloc["qty"],
                "mrp": alloc["mrp"],
                "gst_slab": alloc["gst_slab"],
                "hsn": alloc["hsn_code"],
            })

        draft.items_json = json.dumps(items)
        session.add(draft)
        session.commit()
        return f"Added {quantity} x {product.name} to the bill. Running qty: {new_total_qty}."


def modify_active_draft(
    chat_id: str,
    item_name: str,
    new_quantity: float,
    sku_id: Annotated[str | None, InjectedToolArg] = None,
) -> str:
    """Set the quantity of an item already on the in-progress bill to an absolute value.

    Use this for mid-turn edits like "make it 6 Maggi" (sets qty to 6) or "drop the
    butter" (new_quantity=0 removes the line entirely). This sets the line's qty
    directly — it does not add to the existing amount. Physical stock is never
    touched here; only the draft.

    Args:
        chat_id: Telegram chat id whose draft should be edited.
        item_name: Product name or partial name of the line to change.
        new_quantity: Absolute quantity to set. 0 removes the line from the draft.
        sku_id: Internal only — set automatically when confirming a tap-to-select
            pick. Never pass this yourself.

    Returns:
        Confirmation string, or REFUSED/NOT_FOUND/AMBIGUOUS.
    """
    with get_session() as session:
        draft = session.get(BillDraft, chat_id)
        items = _load_items(draft) if draft else []
        if not items:
            return "REFUSED: No bill in progress for this chat."

        result = resolve_product(session, item_name, sku_id=sku_id)
        if isinstance(result, str):
            return result
        product = result

        existing_lines = [i for i in items if i["sku_id"] == product.sku_id]
        if not existing_lines:
            return f"REFUSED: {product.name} is not on this bill."

        remaining_items = [i for i in items if i["sku_id"] != product.sku_id]

        if new_quantity == 0:
            draft.items_json = json.dumps(remaining_items)
            session.add(draft)
            session.commit()
            return f"Dropped {product.name} from the bill."

        if new_quantity < 0:
            return "REFUSED: Quantity cannot be negative."

        allocation = allocate_fefo(session, product.sku_id, new_quantity)
        if isinstance(allocation, str):
            return f"REFUSED: Only {product.quantity} {product.name} left in stock."

        for alloc in allocation:
            remaining_items.append({
                "sku_id": product.sku_id,
                "batch_id": alloc["batch_id"],
                "name": product.name,
                "qty": alloc["qty"],
                "mrp": alloc["mrp"],
                "gst_slab": alloc["gst_slab"],
                "hsn": alloc["hsn_code"],
            })

        draft.items_json = json.dumps(remaining_items)
        session.add(draft)
        session.commit()
        return f"Updated {product.name} to {new_quantity} on the bill."


def finalize_bill(
    chat_id: str,
    payment_mode: str,
    customer_name: str | None = None,
    payment_reference: str | None = None,
    customer_id: Annotated[str | None, InjectedToolArg] = None,
) -> str:
    """Finalize the in-progress bill for this chat: checks stock, computes GST, decrements stock, records the sale.

    Args:
        chat_id: Telegram chat id whose draft should be finalized.
        payment_mode: One of "cash", "upi", "card", "khata". "khata" puts the total on
            the customer's credit ledger instead of collecting payment now — requires
            customer_name (an existing or brand-new customer; refused if ambiguous).
        customer_name: Required when payment_mode is "khata"; ignored otherwise.
        payment_reference: Optional UPI transaction id / card auth code / other note
            the owner volunteers (e.g. "finalize upi, ref 8842991"). Never invent one —
            leave unset if the owner didn't give it. No real gateway; not verified.
        customer_id: Internal only — set automatically when confirming a
            tap-to-select pick. Never pass this yourself.

    Returns:
        Human-readable bill summary, or REFUSED with a reason.
    """
    mode = payment_mode.strip().lower()
    if mode not in VALID_PAYMENT_MODES:
        return f"REFUSED: payment_mode must be one of {sorted(VALID_PAYMENT_MODES)}."
    if mode == "khata" and not (customer_name and customer_name.strip()):
        return "REFUSED: customer_name required for khata payment."

    with get_session() as session:
        draft = session.get(BillDraft, chat_id)
        if draft is None:
            return "REFUSED: No bill in progress for this chat."

        items = _load_items(draft)
        if not items:
            return "REFUSED: Bill is empty."

        # Race-safe re-check against live stock — per-batch where the line has
        # one, else against the product's own legacy (pre-batch) quantity.
        products_by_sku: dict[str, Product] = {}
        batches_by_id: dict[str, Batch] = {}
        for item in items:
            product = products_by_sku.get(item["sku_id"]) or session.get(Product, item["sku_id"])
            if product is None:
                return (
                    f"REFUSED: Only 0 {item['name']} left in stock "
                    f"(bill wants {item['qty']}). Finalize aborted, nothing changed."
                )
            products_by_sku[item["sku_id"]] = product

            batch_id = item.get("batch_id")
            if batch_id:
                batch = session.get(Batch, batch_id)
                if batch is None or batch.quantity < item["qty"]:
                    available = batch.quantity if batch else 0
                    return (
                        f"REFUSED: Only {available} {item['name']} left in stock "
                        f"(bill wants {item['qty']}). Finalize aborted, nothing changed."
                    )
                batches_by_id[batch_id] = batch
            elif product.quantity < item["qty"]:
                return (
                    f"REFUSED: Only {product.quantity} {item['name']} left in stock "
                    f"(bill wants {item['qty']}). Finalize aborted, nothing changed."
                )

        subtotal, cgst_total, sgst_total, total = _compute_totals(items)

        khata_customer_id = None
        if mode == "khata":
            ledger, error = _adjust_khata(
                session, customer_name, total, "charge", create_if_missing=True, customer_id=customer_id
            )
            if error:
                return error
            khata_customer_id = ledger.customer_id

        for item in items:
            batch_id = item.get("batch_id")
            if batch_id:
                batches_by_id[batch_id].quantity -= item["qty"]
                session.add(batches_by_id[batch_id])
            else:
                products_by_sku[item["sku_id"]].quantity -= item["qty"]
                session.add(products_by_sku[item["sku_id"]])

        for product in products_by_sku.values():
            sync_product_cache(session, product)

        sale = Sale(
            chat_id=chat_id,
            items_json=json.dumps(items),
            subtotal=subtotal,
            cgst=cgst_total,
            sgst=sgst_total,
            total=total,
            payment_mode=mode,
            payment_reference=payment_reference.strip() if payment_reference and payment_reference.strip() else None,
            customer_id=khata_customer_id,
        )
        session.add(sale)
        session.delete(draft)
        session.commit()
        session.refresh(sale)

        khata_note = f" on {customer_name.strip()}'s khata" if mode == "khata" else ""
        ref_note = f" · Ref: {sale.payment_reference}" if sale.payment_reference else ""
        return _format_bill_markdown(
            f"Bill #{sale.invoice_id} finalized ({mode.upper()}{khata_note}{ref_note})",
            items, subtotal, cgst_total, sgst_total, total,
        )


def claim_message(message_id: int) -> bool:
    """Mark a Telegram message as processed. Returns False if already seen (idempotency guard)."""
    with get_session() as session:
        if session.get(ProcessedMessage, message_id):
            return False
        session.add(ProcessedMessage(message_id=message_id))
        session.commit()
        return True
