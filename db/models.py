"""SQLModel tables for the kirana store."""
from datetime import date, datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Product(SQLModel, table=True):
    """A SKU. cost_price/mrp/quantity/gst_slab/hsn_code are a cache mirroring the
    FEFO (next-to-sell) Batch once any batch exists for this sku — see
    tools/_batches.py:sync_product_cache. Before a product's first batch, these
    are the ground truth values set at creation/legacy data."""

    sku_id: str = Field(primary_key=True)
    name: str = Field(index=True)
    cost_price: float
    mrp: float
    quantity: float
    reorder_level: float
    gst_slab: float  # 0, 0.05, 0.12, 0.18
    hsn_code: str
    barcode: Optional[str] = Field(default=None, index=True)


class Batch(SQLModel, table=True):
    """A dated lot of a product — its own cost/MRP/GST/expiry, independent of any
    other batch of the same sku. Billing draws batches FEFO (soonest expiry first,
    no-expiry batches last, ties broken by received_at)."""

    batch_id: str = Field(primary_key=True)
    sku_id: str = Field(index=True)
    quantity: float
    cost_price: float
    mrp: float
    gst_slab: float
    hsn_code: str
    expiry_date: Optional[date] = None
    received_at: datetime = Field(default_factory=datetime.utcnow)


class KhataLedger(SQLModel, table=True):
    customer_id: str = Field(primary_key=True)
    name: str
    balance: float = 0.0


class BillDraft(SQLModel, table=True):
    chat_id: str = Field(primary_key=True)
    items_json: str = "[]"  # list of {sku_id, name, qty, mrp, gst_slab, hsn}
    payment_mode_override: Optional[str] = None


class Sale(SQLModel, table=True):
    invoice_id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    chat_id: str
    items_json: str
    subtotal: float
    cgst: float
    sgst: float
    total: float
    payment_mode: str
    payment_reference: Optional[str] = None
    customer_id: Optional[str] = None


class StockReceipt(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    sku_id: str = Field(index=True)
    quantity: float
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ProcessedMessage(SQLModel, table=True):
    message_id: int = Field(primary_key=True)
    processed_at: datetime = Field(default_factory=datetime.utcnow)


class OwnerPreference(SQLModel, table=True):
    chat_id: str = Field(primary_key=True)
    key: str = Field(primary_key=True)
    value: str
