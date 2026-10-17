"""Idempotent seed of real kirana SKUs."""
from sqlmodel import select

from db.engine import get_session
from db.models import Product

SEED_PRODUCTS = [
    dict(sku_id="ATTA-AASHIR-5KG", name="Aashirvaad Atta 5kg", cost_price=210.0, mrp=249.0,
         quantity=40, reorder_level=8, gst_slab=0.0, hsn_code="1101"),
    dict(sku_id="SALT-TATA-1KG", name="Tata Salt 1kg", cost_price=18.0, mrp=25.0,
         quantity=60, reorder_level=10, gst_slab=0.0, hsn_code="2501"),
    dict(sku_id="BUTTER-AMUL-100G", name="Amul Butter 100g", cost_price=48.0, mrp=58.0,
         quantity=30, reorder_level=6, gst_slab=0.12, hsn_code="0405"),
    dict(sku_id="MAGGI-70G", name="Maggi 70g", cost_price=10.0, mrp=14.0,
         quantity=100, reorder_level=20, gst_slab=0.12, hsn_code="1902"),
    dict(sku_id="OIL-FORTUNE-1L", name="Fortune Sunflower Oil 1L", cost_price=130.0, mrp=155.0,
         quantity=25, reorder_level=5, gst_slab=0.05, hsn_code="1512"),
    dict(sku_id="SUGAR-LOOSE-KG", name="Sugar (loose)", cost_price=38.0, mrp=45.0,
         quantity=80, reorder_level=15, gst_slab=0.0, hsn_code="1701"),
    dict(sku_id="RICE-LOOSE-KG", name="Rice (loose)", cost_price=45.0, mrp=55.0,
         quantity=80, reorder_level=15, gst_slab=0.0, hsn_code="1006"),
    dict(sku_id="DAL-LOOSE-KG", name="Dal (loose)", cost_price=90.0, mrp=110.0,
         quantity=50, reorder_level=10, gst_slab=0.0, hsn_code="0713"),
    dict(sku_id="SURF-EXCEL-1KG", name="Surf Excel 1kg", cost_price=110.0, mrp=135.0,
         quantity=20, reorder_level=5, gst_slab=0.18, hsn_code="3402"),
    dict(sku_id="PARLE-G-100G", name="Parle-G 100g", cost_price=8.0, mrp=10.0,
         quantity=120, reorder_level=25, gst_slab=0.05, hsn_code="1905"),
]


def seed_if_empty() -> None:
    with get_session() as session:
        existing = session.exec(select(Product)).first()
        if existing:
            return
        for data in SEED_PRODUCTS:
            session.add(Product(**data))
        session.commit()
