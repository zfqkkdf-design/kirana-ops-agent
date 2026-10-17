import importlib
import json
import os
import tempfile

import pytest


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
    import tools._batches as batches_mod
    importlib.reload(batches_mod)
    import tools.inventory as inventory_mod
    importlib.reload(inventory_mod)
    import tools.billing as billing_mod
    importlib.reload(billing_mod)
    import tools.stock as stock_mod
    importlib.reload(stock_mod)
    import tools.expiry as expiry_mod
    importlib.reload(expiry_mod)

    engine_mod.init_db()
    seed_mod.seed_if_empty()

    yield {
        "receive_stock": inventory_mod.receive_stock,
        "add_to_bill_draft": billing_mod.add_to_bill_draft,
        "finalize_bill": billing_mod.finalize_bill,
        "check_stock": stock_mod.check_stock,
        "expiry_report": expiry_mod.expiry_report,
        "product_batches": expiry_mod.product_batches,
        "engine": engine_mod,
    }

    os.remove(path)
    wal = path + "-wal"
    shm = path + "-shm"
    for extra in (wal, shm):
        if os.path.exists(extra):
            os.remove(extra)


def test_receive_stock_rejects_bad_expiry(store):
    result = store["receive_stock"]("Maggi", 10, 10.0, 14.0, expiry_date="not-a-date")
    assert result.startswith("REFUSED")


def test_receive_stock_with_expiry_shows_in_check_stock(store):
    store["receive_stock"]("Maggi", 10, 10.0, 14.0, expiry_date="2026-08-01")
    result = store["check_stock"]("Maggi")
    assert "Nearest expiry: 2026-08-01" in result


def test_finalize_bill_consumes_soonest_expiry_first(store):
    # A batch with a tracked expiry always outranks a no-expiry batch under FEFO
    # (non-perishable/untracked stock is assumed shelf-stable), even though the
    # legacy no-expiry stock here was received earlier.
    store["receive_stock"]("Maggi", 20, 11.0, 15.0, expiry_date="2027-01-01")
    store["add_to_bill_draft"]("chat1", "Maggi", 105)
    result = store["finalize_bill"]("chat1", "cash")
    assert "TOTAL" in result

    from sqlmodel import Session, select
    from db.models import Batch

    with Session(store["engine"].engine) as session:
        batches = session.exec(select(Batch).where(Batch.sku_id == "MAGGI-70G")).all()
        legacy = next(b for b in batches if b.expiry_date is None)
        new_batch = next(b for b in batches if b.expiry_date is not None)
        assert new_batch.quantity == 0  # dated batch fully drawn first
        assert legacy.quantity == 15  # 100 - 85 spillover


def test_finalize_bill_prefers_earlier_expiry_over_no_expiry(store):
    store["receive_stock"]("Amul Butter", 10, 45.0, 55.0, expiry_date="2026-09-01")
    # Amul Butter seeded with 30 units, no expiry (legacy) -> after this, one batch
    # has an expiry, one doesn't; the expiring one should be drawn first.
    store["add_to_bill_draft"]("chat1", "Amul Butter", 5)
    store["finalize_bill"]("chat1", "cash")

    from sqlmodel import Session, select
    from db.models import Batch

    with Session(store["engine"].engine) as session:
        batches = session.exec(select(Batch).where(Batch.sku_id == "BUTTER-AMUL-100G")).all()
        expiring = next(b for b in batches if b.expiry_date is not None)
        legacy = next(b for b in batches if b.expiry_date is None)
        assert expiring.quantity == 5  # 10 - 5, drawn first
        assert legacy.quantity == 30  # untouched


def test_expiry_report_lists_near_expiry_batches(store):
    from datetime import date, timedelta

    soon = (date.today() + timedelta(days=5)).isoformat()
    far = (date.today() + timedelta(days=365)).isoformat()
    store["receive_stock"]("Maggi", 5, 10.0, 14.0, expiry_date=soon)
    store["receive_stock"]("Amul Butter", 5, 45.0, 55.0, expiry_date=far)

    result = store["expiry_report"]("chat1")
    assert "Maggi" in result
    assert "Amul Butter" not in result


def test_expiry_report_empty_when_nothing_expiring(store):
    result = store["expiry_report"]("chat1")
    assert result.startswith("No batches expiring")


def test_product_batches_breakdown(store):
    store["receive_stock"]("Maggi", 20, 11.0, 15.0, expiry_date="2027-01-01")
    result = store["product_batches"]("Maggi")
    assert "Maggi 70g batches" in result
    assert "MRP ₹14.0" in result  # legacy batch, FEFO-first
    assert "MRP ₹15.0" in result  # new batch


def test_product_batches_no_batches_yet(store):
    result = store["product_batches"]("Tata Salt")
    assert "no batches recorded" in result


def test_receive_stock_reuses_current_gst_and_hsn_when_omitted(store):
    store["receive_stock"]("Maggi", 10, 10.0, 14.0)

    from sqlmodel import Session, select
    from db.models import Batch, Product

    with Session(store["engine"].engine) as session:
        product = session.exec(select(Product).where(Product.sku_id == "MAGGI-70G")).one()
        batches = session.exec(select(Batch).where(Batch.sku_id == "MAGGI-70G")).all()
        new_batch = next(b for b in batches if b.cost_price == 10.0 and b.mrp == 14.0 and b.quantity == 10)
        assert new_batch.gst_slab == product.gst_slab
        assert new_batch.hsn_code == product.hsn_code
