"""Inventory tools: restock existing SKUs (as dated batches/lots), add brand-new SKUs."""
import uuid
from datetime import date

from db.engine import get_session
from db.models import Product, StockReceipt
from tools._batches import create_batch
from tools._resolve import resolve_product, resolve_product_by_barcode

VALID_GST_SLABS = {0, 0.05, 0.12, 0.18}


def receive_stock(
    item_name: str,
    quantity: float,
    cost_price: float,
    mrp: float,
    gst_slab: float | None = None,
    hsn_code: str | None = None,
    expiry_date: str | None = None,
) -> str:
    """Record inbound stock for an existing product as a new batch/lot.

    Always creates a NEW batch rather than overwriting the product's existing
    pricing — a fresh delivery can carry a different cost, MRP, GST slab, or
    expiry than stock already on the shelf. Selling later draws down whichever
    live batch expires soonest first (FEFO); batches with no expiry sell after
    ones that have one, oldest-received first.

    Args:
        item_name: Product name or partial name to restock.
        quantity: Units received (must be positive).
        cost_price: Cost price for this batch.
        mrp: MRP for this batch.
        gst_slab: GST slab for this batch (0, 0.05, 0.12, 0.18). Omit to reuse
            the product's current slab.
        hsn_code: HSN code for this batch. Omit to reuse the product's current one.
        expiry_date: This batch's expiry date as YYYY-MM-DD, if the product is
            perishable. Omit for non-perishables — never invent one.

    Returns:
        Confirmation string with the new batch id and total stock across all
        batches, or REFUSED/NOT_FOUND/AMBIGUOUS.
    """
    if quantity <= 0:
        return "REFUSED: Quantity must be positive."

    with get_session() as session:
        result = resolve_product(session, item_name)
        if isinstance(result, str):
            return result
        product = result

        slab = gst_slab if gst_slab is not None else product.gst_slab
        if slab not in VALID_GST_SLABS:
            return f"REFUSED: gst_slab must be one of {sorted(VALID_GST_SLABS)}."
        hsn = hsn_code.strip() if hsn_code else product.hsn_code

        parsed_expiry = None
        if expiry_date and expiry_date.strip():
            try:
                parsed_expiry = date.fromisoformat(expiry_date.strip())
            except ValueError:
                return "REFUSED: expiry_date must be in YYYY-MM-DD format."

        batch = create_batch(
            session, product, quantity, cost_price, mrp, slab, hsn, expiry_date=parsed_expiry
        )
        session.add(StockReceipt(sku_id=product.sku_id, quantity=quantity))
        session.commit()
        session.refresh(product)

        expiry_note = f", expiry {parsed_expiry.isoformat()}" if parsed_expiry else ", no expiry"
        return (
            f"Success: Restocked {quantity} units of {product.name} as a new batch "
            f"(batch_id={batch.batch_id}{expiry_note}). Total stock across all batches: {product.quantity}."
        )


def add_new_product(
    name: str,
    cost_price: float,
    mrp: float,
    gst_slab: float,
    hsn_code: str,
    reorder_level: float = 5.0,
    barcode: str | None = None,
    confirm_duplicate: bool = False,
) -> str:
    """Add a brand-new product SKU with zero starting stock (use receive_stock to bring in units).

    Args:
        name: New product name.
        cost_price: Cost price; must not exceed mrp.
        mrp: Maximum retail price.
        gst_slab: One of 0, 0.05, 0.12, 0.18.
        hsn_code: HSN code for the product.
        reorder_level: Stock level at which to warn for reorder (default 5.0).
        barcode: Barcode/UPC digits, if known (e.g. read from a product photo)
            — lets a future photo scan of this item match it directly. Omit
            if no barcode was given.
        confirm_duplicate: Pass True only on a retry after the owner has explicitly
            confirmed they want a second product with a name near-identical to an
            existing one (see DUPLICATE_NAME below). Leave False on a first attempt.

    Returns:
        Success string with the new sku_id, REFUSED, or a "DUPLICATE_NAME: ..." string
        naming the existing near-match — ask the owner to confirm before retrying with
        confirm_duplicate=True.
    """
    if cost_price > mrp:
        return "REFUSED: Cannot add product where cost price exceeds MRP."
    if gst_slab not in VALID_GST_SLABS:
        return f"REFUSED: gst_slab must be one of {sorted(VALID_GST_SLABS)}."

    if not confirm_duplicate:
        with get_session() as session:
            existing = resolve_product(session, name.strip())
        if not isinstance(existing, str):
            return (
                f"DUPLICATE_NAME: A product named '{existing.name}' already exists "
                f"(sku_id={existing.sku_id}). Ask the owner to confirm they want a separate "
                "new product before retrying with confirm_duplicate=True — otherwise use "
                "receive_stock/set_product_barcode on the existing one instead."
            )

    with get_session() as session:
        sku_id = uuid.uuid4().hex[:12].upper()
        product = Product(
            sku_id=sku_id,
            name=name.strip(),
            cost_price=cost_price,
            mrp=mrp,
            quantity=0.0,
            reorder_level=reorder_level,
            gst_slab=gst_slab,
            hsn_code=hsn_code,
            barcode=barcode.strip() if barcode else None,
        )
        session.add(product)
        session.commit()

        return f"Success: Added new product '{product.name}' (sku_id={sku_id})."


def set_product_barcode(item_name: str, barcode: str, chat_id: str) -> str:
    """Attach or correct a barcode on an existing product — e.g. after a photo scan read a
    barcode that didn't match anything and the owner then says which product it belongs to.

    Args:
        item_name: Product name or partial name to attach the barcode to.
        barcode: The barcode digits to save.
        chat_id: Telegram chat id (accepted for agent wiring parity, unused for scope).

    Returns:
        Confirmation string, or REFUSED/NOT_FOUND/AMBIGUOUS.
    """
    barcode = barcode.strip()
    if not barcode:
        return "REFUSED: barcode cannot be empty."

    with get_session() as session:
        result = resolve_product(session, item_name)
        if isinstance(result, str):
            return result
        product = result

        existing = resolve_product_by_barcode(session, barcode)
        if existing is not None and existing.sku_id != product.sku_id:
            return f"REFUSED: barcode {barcode} is already assigned to '{existing.name}'."

        product.barcode = barcode
        session.add(product)
        session.commit()

        return f"Success: Barcode {barcode} is now linked to '{product.name}'."
