"""Stock query tool."""
from typing import Annotated

from langchain_core.tools import InjectedToolArg

from db.engine import get_session
from tools._batches import fefo_batches
from tools._resolve import resolve_product


def check_stock(item_name: str, sku_id: Annotated[str | None, InjectedToolArg] = None) -> str:
    """Look up stock, MRP, and reorder status for a product by name (case-insensitive substring match).

    Args:
        item_name: The product name or partial name the shopkeeper mentioned.
        sku_id: Internal only — set automatically when confirming a tap-to-select
            pick. Never pass this yourself.

    Returns:
        A string with quantity, MRP, reorder warning, and — when the product has
        more than one live batch — a per-batch breakdown (FEFO sell order) so the
        total isn't just a single opaque number. NOT_FOUND / AMBIGUOUS otherwise.
    """
    with get_session() as session:
        result = resolve_product(session, item_name, sku_id=sku_id)
        if isinstance(result, str):
            return result
        product = result
        reorder_note = ""
        if product.quantity <= product.reorder_level:
            reorder_note = " LOW STOCK — reorder soon."

        batches = fefo_batches(session, product.sku_id)
        summary = (
            f"{product.name}: {product.quantity} in stock (total across all batches), "
            f"MRP ₹{product.mrp}.{reorder_note}"
        )
        if len(batches) <= 1:
            return summary

        lines = [summary, "Batches (FEFO sell order):"]
        for batch in batches:
            expiry = batch.expiry_date.isoformat() if batch.expiry_date else "none"
            lines.append(f"- {batch.quantity:g} units, MRP ₹{batch.mrp}, expiry {expiry}")
        return "\n".join(lines)
