"""Shared FEFO batch logic: allocate stock for billing, keep Product's cache in sync.

A product's stock can live in two places: real Batch rows (created by
receive_stock), or — for a product that's never had a batch created for it yet
(freshly seeded, or added via add_new_product and never restocked) — directly
on Product.quantity/cost_price/mrp/gst_slab/hsn_code, treated as a single
implicit legacy lot. allocate_fefo() and sync_product_cache() both understand
this so callers (billing.py, inventory.py) never need to branch on it.
"""
import uuid
from datetime import datetime
from typing import Union

from sqlmodel import Session, select

from db.models import Batch, Product

# Legacy pre-batch stock is folded into its own batch the first time a real
# batch is created for a sku (see create_batch) — sorted first among no-expiry
# batches so it sells before the newly arrived lot, matching FEFO intuition.
LEGACY_RECEIVED_AT = datetime.min


def _sort_key(batch: Batch):
    return (batch.expiry_date is None, batch.expiry_date, batch.received_at)


def fefo_batches(session: Session, sku_id: str) -> list[Batch]:
    """Live (qty > 0) batches for a sku, FEFO-sorted: soonest expiry first, then
    no-expiry batches, ties broken by receipt order (oldest first)."""
    batches = session.exec(
        select(Batch).where(Batch.sku_id == sku_id, Batch.quantity > 0)
    ).all()
    return sorted(batches, key=_sort_key)


def sync_product_cache(session: Session, product: Product) -> None:
    """Refresh Product's cached quantity/cost/mrp/gst/hsn from its batches.

    No-op if the product has no batches at all — then Product's own fields are
    the ground truth and the caller is responsible for updating them directly.
    """
    all_batches = session.exec(select(Batch).where(Batch.sku_id == product.sku_id)).all()
    if not all_batches:
        session.add(product)
        return

    product.quantity = sum(b.quantity for b in all_batches if b.quantity > 0)
    live = sorted([b for b in all_batches if b.quantity > 0], key=_sort_key)
    if live:
        lead = live[0]
        product.cost_price = lead.cost_price
        product.mrp = lead.mrp
        product.gst_slab = lead.gst_slab
        product.hsn_code = lead.hsn_code
    session.add(product)


def create_batch(
    session: Session,
    product: Product,
    quantity: float,
    cost_price: float,
    mrp: float,
    gst_slab: float,
    hsn_code: str,
    expiry_date=None,
) -> Batch:
    """Create a new batch/lot for a product and refresh its cached display fields.

    If this is the product's first-ever batch and it's carrying pre-batch
    (legacy/seeded) quantity, that quantity is folded into its own batch first
    so it isn't lost and still sells before the new lot under FEFO.
    """
    has_existing = session.exec(
        select(Batch.batch_id).where(Batch.sku_id == product.sku_id)
    ).first()
    if has_existing is None and product.quantity > 0:
        session.add(Batch(
            batch_id=uuid.uuid4().hex[:12].upper(),
            sku_id=product.sku_id,
            quantity=product.quantity,
            cost_price=product.cost_price,
            mrp=product.mrp,
            gst_slab=product.gst_slab,
            hsn_code=product.hsn_code,
            expiry_date=None,
            received_at=LEGACY_RECEIVED_AT,
        ))

    batch = Batch(
        batch_id=uuid.uuid4().hex[:12].upper(),
        sku_id=product.sku_id,
        quantity=quantity,
        cost_price=cost_price,
        mrp=mrp,
        gst_slab=gst_slab,
        hsn_code=hsn_code,
        expiry_date=expiry_date,
    )
    session.add(batch)
    session.flush()
    sync_product_cache(session, product)
    return batch


def allocate_fefo(session: Session, sku_id: str, quantity: float) -> Union[list[dict], str]:
    """Simulate drawing `quantity` units FEFO, without mutating anything.

    Returns a list of {"batch_id", "mrp", "gst_slab", "hsn_code", "qty"} dicts
    covering the full quantity (batch_id is None for the legacy no-batch pool),
    or "INSUFFICIENT_STOCK" if there isn't enough live stock to cover it.
    """
    batches = fefo_batches(session, sku_id)
    if not batches:
        product = session.get(Product, sku_id)
        if product is None or product.quantity < quantity:
            return "INSUFFICIENT_STOCK"
        return [{
            "batch_id": None,
            "mrp": product.mrp,
            "gst_slab": product.gst_slab,
            "hsn_code": product.hsn_code,
            "qty": quantity,
        }]

    remaining = quantity
    allocation: list[dict] = []
    for batch in batches:
        if remaining <= 1e-9:
            break
        take = min(batch.quantity, remaining)
        allocation.append({
            "batch_id": batch.batch_id,
            "mrp": batch.mrp,
            "gst_slab": batch.gst_slab,
            "hsn_code": batch.hsn_code,
            "qty": take,
        })
        remaining -= take
    if remaining > 1e-9:
        return "INSUFFICIENT_STOCK"
    return allocation
