"""Batch expiry visibility: near-expiry/expired alerts, per-product batch breakdown."""
from datetime import date, timedelta
from typing import Annotated

from langchain_core.tools import InjectedToolArg
from sqlmodel import select

from db.engine import get_session
from db.models import Batch, Product
from tools._batches import fefo_batches
from tools._resolve import resolve_product

NEAR_EXPIRY_DAYS = 14


def expiry_report(chat_id: str) -> str:
    """List batches expiring soon (or already expired) across all products, store-wide.

    Args:
        chat_id: Telegram chat id (accepted for agent wiring parity, unused for scope —
            this is store-wide, not per-chat).

    Returns:
        Markdown list of near-expiry/expired batches sorted soonest-first, or a
        message saying nothing is close to expiring.
    """
    today = date.today()
    cutoff = today + timedelta(days=NEAR_EXPIRY_DAYS)
    with get_session() as session:
        batches = session.exec(
            select(Batch).where(Batch.quantity > 0, Batch.expiry_date.is_not(None), Batch.expiry_date <= cutoff)
        ).all()
        if not batches:
            return f"No batches expiring within {NEAR_EXPIRY_DAYS} days."
        batches.sort(key=lambda b: b.expiry_date)
        products = {p.sku_id: p.name for p in session.exec(select(Product)).all()}

        lines = ["**Batches expiring soon:**"]
        for batch in batches:
            name = products.get(batch.sku_id, batch.sku_id)
            days_left = (batch.expiry_date - today).days
            status = "EXPIRED" if days_left < 0 else f"{days_left}d left"
            lines.append(
                f"- {name}: {batch.quantity:g} units, expires {batch.expiry_date.isoformat()} ({status})"
            )
        return "\n".join(lines)


def product_batches(
    item_name: str, sku_id: Annotated[str | None, InjectedToolArg] = None
) -> str:
    """Show every live batch (lot) for a product: quantity, cost, MRP, GST slab, expiry.

    Args:
        item_name: Product name or partial name.
        sku_id: Internal only — set automatically when confirming a tap-to-select
            pick. Never pass this yourself.

    Returns:
        Markdown breakdown of live batches in FEFO (sell) order, or NOT_FOUND/AMBIGUOUS.
    """
    with get_session() as session:
        result = resolve_product(session, item_name, sku_id=sku_id)
        if isinstance(result, str):
            return result
        product = result

        batches = fefo_batches(session, product.sku_id)
        if not batches:
            return f"{product.name}: no batches recorded (all stock is untracked/legacy)."

        lines = [f"**{product.name} batches (FEFO sell order):**"]
        for batch in batches:
            expiry = batch.expiry_date.isoformat() if batch.expiry_date else "none"
            lines.append(
                f"- {batch.quantity:g} units, cost ₹{batch.cost_price}, MRP ₹{batch.mrp}, "
                f"GST {batch.gst_slab * 100:.0f}%, expiry {expiry}"
            )
        return "\n".join(lines)
