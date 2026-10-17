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
    import tools.stock as stock_mod
    importlib.reload(stock_mod)
    import tools.billing as billing_mod
    importlib.reload(billing_mod)

    engine_mod.init_db()
    seed_mod.seed_if_empty()

    yield {
        "check_stock": stock_mod.check_stock,
        "add_to_bill_draft": billing_mod.add_to_bill_draft,
        "view_bill_draft": billing_mod.view_bill_draft,
        "finalize_bill": billing_mod.finalize_bill,
        "claim_message": billing_mod.claim_message,
        "engine": engine_mod,
    }

    os.remove(path)
    wal = path + "-wal"
    shm = path + "-shm"
    for extra in (wal, shm):
        if os.path.exists(extra):
            os.remove(extra)


def test_check_stock_returns_qty(store):
    result = store["check_stock"]("Maggi")
    assert "100" in result
    assert "MRP" in result


def test_check_stock_typo_fuzzy_match(store):
    result = store["check_stock"]("maggie")
    assert result.startswith("Maggi 70g")


def test_check_stock_ambiguous(store):
    result = store["check_stock"]("loose")
    assert result.startswith("AMBIGUOUS")


def test_check_stock_not_found(store):
    result = store["check_stock"]("nonexistent item xyz")
    assert result.startswith("NOT_FOUND")


def test_add_to_bill_draft_within_stock(store):
    result = store["add_to_bill_draft"]("chat1", "Maggi", 5)
    assert "Added 5" in result


def test_add_to_bill_draft_oversell_refused(store):
    result = store["add_to_bill_draft"]("chat1", "Amul Butter", 999)
    assert result.startswith("REFUSED")

    from sqlmodel import Session, select
    from db.models import Product

    with Session(store["engine"].engine) as session:
        product = session.exec(
            select(Product).where(Product.sku_id == "BUTTER-AMUL-100G")
        ).one()
        assert product.quantity == 30  # unchanged


def test_view_bill_draft_does_not_finalize(store):
    empty = store["view_bill_draft"]("chat1")
    assert empty.startswith("No bill in progress")

    store["add_to_bill_draft"]("chat1", "Maggi", 4)
    preview = store["view_bill_draft"]("chat1")
    assert "PREVIEW" in preview
    assert "Maggi" in preview
    assert "TOTAL" in preview

    from sqlmodel import Session, select
    from db.models import Product

    with Session(store["engine"].engine) as session:
        product = session.exec(
            select(Product).where(Product.sku_id == "MAGGI-70G")
        ).one()
        assert product.quantity == 100  # unchanged, preview did not decrement

    # draft still there and finalize-able after preview
    result = store["finalize_bill"]("chat1", "cash")
    assert "TOTAL" in result


def test_finalize_bill_gst_and_stock(store):
    store["add_to_bill_draft"]("chat1", "Maggi", 4)
    result = store["finalize_bill"]("chat1", "upi")
    assert "TOTAL" in result
    assert "Maggi" in result

    from sqlmodel import Session, select
    from db.models import Product, Sale

    with Session(store["engine"].engine) as session:
        product = session.exec(
            select(Product).where(Product.sku_id == "MAGGI-70G")
        ).one()
        assert product.quantity == 96  # 100 - 4

        sale = session.exec(select(Sale)).one()
        items = json.loads(sale.items_json)
        assert items[0]["qty"] == 4
        taxable = 14.0 * 4
        expected_gst = round(taxable * 0.12, 2)
        assert round(sale.cgst + sale.sgst, 2) == expected_gst
        assert sale.total == round(taxable + expected_gst, 2)

    # draft cleared
    second = store["finalize_bill"]("chat1", "upi")
    assert second.startswith("REFUSED")


def test_finalize_empty_draft_refused(store):
    result = store["finalize_bill"]("no-such-chat", "cash")
    assert result.startswith("REFUSED")


def test_claim_message_idempotent(store):
    assert store["claim_message"](111) is True
    assert store["claim_message"](111) is False
