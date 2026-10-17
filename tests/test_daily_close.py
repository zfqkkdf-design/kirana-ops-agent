import importlib
import json
import os
import tempfile
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

import pytest


def _round2(value):
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@pytest.fixture()
def store(monkeypatch):
    """Fresh temp-SQLite DB per test; reload db/tools modules against it."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")

    import db.engine as engine_mod
    importlib.reload(engine_mod)
    import db.seed as seed_mod
    importlib.reload(seed_mod)
    import tools._resolve as resolve_mod
    importlib.reload(resolve_mod)
    import tools.management as management_mod
    importlib.reload(management_mod)

    engine_mod.init_db()
    seed_mod.seed_if_empty()

    yield {
        "close_day_summary": management_mod.close_day_summary,
        "engine": engine_mod,
    }

    os.remove(path)
    wal = path + "-wal"
    shm = path + "-shm"
    for extra in (wal, shm):
        if os.path.exists(extra):
            os.remove(extra)


def _add_sale(engine_mod, items, payment_mode, subtotal, cgst, sgst, total):
    from sqlmodel import Session

    from db.models import Sale

    with Session(engine_mod.engine) as session:
        session.add(
            Sale(
                timestamp=datetime.utcnow(),
                chat_id="chat1",
                items_json=json.dumps(items),
                subtotal=subtotal,
                cgst=cgst,
                sgst=sgst,
                total=total,
                payment_mode=payment_mode,
            )
        )
        session.commit()


def test_empty_day_zeroed(store):
    result = store["close_day_summary"]("chat1")
    assert "Sales: 0" in result
    assert "Subtotal: ₹0.00" in result
    assert "TOTAL: ₹0.00" in result
    assert "cash: ₹0.00" in result
    assert "upi: ₹0.00" in result
    assert "card: ₹0.00" in result
    assert "khata: ₹0.00" in result
    assert "No sales yet today." in result
    assert "None" in result


def test_precise_revenue_decimal_aggregation(store):
    # Sale 1: Maggi (0.12 slab, mrp 14.0) x4, cash
    maggi_items = [{"sku_id": "MAGGI-70G", "name": "Maggi 70g", "qty": 4, "mrp": 14.0, "gst_slab": 0.12, "hsn": "1902"}]
    taxable1 = 14.0 * 4
    gst1 = taxable1 * 0.12
    cgst1 = _round2(gst1 / 2)
    sgst1 = _round2(gst1 / 2)
    sub1 = _round2(taxable1)
    total1 = _round2(sub1 + cgst1 + sgst1)
    _add_sale(store["engine"], maggi_items, "cash", sub1, cgst1, sgst1, total1)

    butter_items = [{"sku_id": "BUTTER-AMUL-100G", "name": "Amul Butter 100g", "qty": 2, "mrp": 58.0, "gst_slab": 0.12, "hsn": "0405"}]
    taxable2 = 58.0 * 2
    gst2 = taxable2 * 0.12
    cgst2 = _round2(gst2 / 2)
    sgst2 = _round2(gst2 / 2)
    sub2 = _round2(taxable2)
    total2 = _round2(sub2 + cgst2 + sgst2)
    _add_sale(store["engine"], butter_items, "upi", sub2, cgst2, sgst2, total2)

    result = store["close_day_summary"]("chat1")

    expected_subtotal = _round2(Decimal(str(sub1)) + Decimal(str(sub2)))
    expected_cgst = _round2(Decimal(str(cgst1)) + Decimal(str(cgst2)))
    expected_sgst = _round2(Decimal(str(sgst1)) + Decimal(str(sgst2)))
    expected_total = _round2(Decimal(str(total1)) + Decimal(str(total2)))

    assert "Sales: 2" in result
    assert f"Subtotal: ₹{expected_subtotal}" in result
    assert f"CGST: ₹{expected_cgst}" in result
    assert f"SGST: ₹{expected_sgst}" in result
    assert f"TOTAL: ₹{expected_total}" in result
    assert f"cash: ₹{sub1 and _round2(total1)}" in result
    assert f"upi: ₹{_round2(total2)}" in result


def test_sales_velocity_top_items_sorted(store):
    _add_sale(
        store["engine"],
        [{"sku_id": "MAGGI-70G", "name": "Maggi 70g", "qty": 2, "mrp": 14.0, "gst_slab": 0.12, "hsn": "1902"}],
        "cash",
        28.0, 1.68, 1.68, 31.36,
    )
    _add_sale(
        store["engine"],
        [
            {"sku_id": "MAGGI-70G", "name": "Maggi 70g", "qty": 8, "mrp": 14.0, "gst_slab": 0.12, "hsn": "1902"},
            {"sku_id": "BUTTER-AMUL-100G", "name": "Amul Butter 100g", "qty": 5, "mrp": 58.0, "gst_slab": 0.12, "hsn": "0405"},
        ],
        "upi",
        112.0 + 290.0, 0, 0, 0,
    )
    _add_sale(
        store["engine"],
        [{"sku_id": "SALT-TATA-1KG", "name": "Tata Salt 1kg", "qty": 1, "mrp": 25.0, "gst_slab": 0.0, "hsn": "2501"}],
        "card",
        25.0, 0, 0, 25.0,
    )

    result = store["close_day_summary"]("chat1")
    top_section = result.split("Top items:")[1].split("Low stock:")[0]

    assert top_section.index("Maggi") < top_section.index("Amul Butter") < top_section.index("Tata Salt")
    assert "1. Maggi 70g — 10 sold" in top_section


def test_reorder_flags_low_stock(store):
    from sqlmodel import Session, select

    from db.models import Product

    with Session(store["engine"].engine) as session:
        product = session.exec(select(Product).where(Product.sku_id == "MAGGI-70G")).one()
        product.quantity = product.reorder_level - 1
        session.add(product)
        session.commit()

    result = store["close_day_summary"]("chat1")
    low_stock_section = result.split("Low stock:")[1]
    assert "Maggi 70g" in low_stock_section
