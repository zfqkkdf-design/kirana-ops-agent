"""Velocity-based reorder suggestions: how much to reorder and when reorder_level is off."""
import json
from datetime import datetime, timedelta

from sqlmodel import select

from db.engine import get_session
from db.models import Product, Sale, StockReceipt
from tools._resolve import resolve_product

DEFAULT_LOOKBACK_DAYS = 14
DEFAULT_REFILL_PERIOD_DAYS = 7.0
REORDER_LEVEL_MISMATCH_THRESHOLD = 0.3


def _daily_velocity(sales: list, sku_id: str, now: datetime) -> float:
    """Units of sku_id sold per day, averaged over the span since its earliest sale
    within the lookback window — not a flat lookback_days divisor, so a burst
    concentrated in the last couple of days (e.g. 100 units in 2 days) reads as
    ~50/day rather than being diluted down by quiet days earlier in the window."""
    total_qty = 0.0
    earliest = None
    for sale in sales:
        for item in json.loads(sale.items_json):
            if item["sku_id"] == sku_id:
                total_qty += item["qty"]
                if earliest is None or sale.timestamp < earliest:
                    earliest = sale.timestamp

    if total_qty == 0:
        return 0.0
    span_days = max(1.0, (now - earliest).total_seconds() / 86400)
    return total_qty / span_days


def _refill_period_days(receipts: list, default: float = DEFAULT_REFILL_PERIOD_DAYS) -> float:
    """Average gap between consecutive stock receipts for a sku, or `default` if
    there's fewer than two receipts to measure a gap from."""
    if len(receipts) < 2:
        return default
    timestamps = sorted(r.timestamp for r in receipts)
    gaps = [(b - a).total_seconds() / 86400 for a, b in zip(timestamps, timestamps[1:])]
    return sum(gaps) / len(gaps)


def compute_reorder_suggestions(chat_id: str, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> list[dict]:
    """Per-product reorder math: projected need over the next refill cycle vs current stock.

    Store-wide, read-only. Skips products with no sales in the lookback window and
    no stock-receipt history — nothing to infer a suggestion from yet.
    """
    now = datetime.utcnow()
    since = now - timedelta(days=lookback_days)

    with get_session() as session:
        products = session.exec(select(Product)).all()
        sales = session.exec(select(Sale).where(Sale.timestamp >= since)).all()
        receipts_by_sku: dict[str, list] = {}
        for receipt in session.exec(select(StockReceipt)).all():
            receipts_by_sku.setdefault(receipt.sku_id, []).append(receipt)

        results = []
        for product in products:
            receipts = receipts_by_sku.get(product.sku_id, [])
            velocity = _daily_velocity(sales, product.sku_id, now)
            if velocity == 0 and not receipts:
                continue

            refill_period = _refill_period_days(receipts)
            projected_need = velocity * refill_period
            suggested_qty = max(0.0, round(projected_need - product.quantity, 1))

            suggested_reorder_level = None
            denom = max(product.reorder_level, 1.0)
            if abs(projected_need - product.reorder_level) / denom > REORDER_LEVEL_MISMATCH_THRESHOLD:
                suggested_reorder_level = round(projected_need, 1)

            results.append({
                "name": product.name,
                "sku_id": product.sku_id,
                "current_stock": product.quantity,
                "daily_velocity": round(velocity, 2),
                "refill_period_days": round(refill_period, 1),
                "suggested_qty": suggested_qty,
                "reorder_level": product.reorder_level,
                "suggested_reorder_level": suggested_reorder_level,
            })

    return results


def reorder_suggestions(chat_id: str) -> str:
    """Report reorder quantities and reorder-level adjustments based on recent sales velocity.

    Store-wide, read-only, makes no changes. For each product with recent sales or
    stock-receipt history, projects demand over its estimated refill cycle (from the
    gap between past restocks, defaulting to 7 days if there isn't enough restock
    history yet) and compares it to current stock to suggest a reorder quantity — or
    flags that no reorder is needed and the reorder level may be set too high.

    Args:
        chat_id: Telegram chat id (accepted for agent wiring parity, unused for scope).

    Returns:
        Multi-line text, one line per product with a suggestion, or a plain
        "not enough data yet" line if nothing qualifies.
    """
    suggestions = compute_reorder_suggestions(chat_id)
    if not suggestions:
        return "Not enough sales/restock history yet to suggest reorders."

    lines = ["**Reorder suggestions:**"]
    for s in suggestions:
        block = [f"**{s['name']}**"]
        if s["suggested_qty"] > 0:
            block.append(f"Reorder qty: ~{s['suggested_qty']:g} units")
            block.append(f"Selling: ~{s['daily_velocity']:g}/day")
            block.append(f"Refill cycle: ~{s['refill_period_days']:g}d")
        else:
            block.append("Reorder: not needed — current stock covers the next cycle")
        if s["suggested_reorder_level"] is not None:
            block.append(
                f"Suggested reorder level: {s['suggested_reorder_level']:g} (currently {s['reorder_level']:g})"
            )
        lines.append("\n".join(block))

    lines.append("")
    lines.append('Say e.g. "set Maggi\'s reorder level to 3" to act on a suggestion.')
    return "\n\n".join(lines)


def update_reorder_level(item_name: str, reorder_level: float, chat_id: str) -> str:
    """Set a product's reorder level (the stock threshold that triggers a low-stock flag).

    Args:
        item_name: Product name or partial name.
        reorder_level: New reorder level (must be non-negative).
        chat_id: Telegram chat id (accepted for agent wiring parity, unused for scope).

    Returns:
        Confirmation string, or REFUSED/NOT_FOUND/AMBIGUOUS.
    """
    if reorder_level < 0:
        return "REFUSED: reorder_level cannot be negative."

    with get_session() as session:
        result = resolve_product(session, item_name)
        if isinstance(result, str):
            return result
        product = result

        product.reorder_level = reorder_level
        session.add(product)
        session.commit()

        return f"Success: {product.name}'s reorder level is now {reorder_level:g}."
