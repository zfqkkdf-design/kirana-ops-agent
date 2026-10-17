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
    import tools.inventory as inventory_mod
    importlib.reload(inventory_mod)
    import tools.billing as billing_mod
    importlib.reload(billing_mod)

    engine_mod.init_db()
    seed_mod.seed_if_empty()

    yield {
        "receive_stock": inventory_mod.receive_stock,
        "add_new_product": inventory_mod.add_new_product,
        "modify_active_draft": billing_mod.modify_active_draft,
        "add_to_bill_draft": billing_mod.add_to_bill_draft,
        "engine": engine_mod,
    }

    os.remove(path)
    wal = path + "-wal"
    shm = path + "-shm"
    for extra in (wal, shm):
        if os.path.exists(extra):
            os.remove(extra)


def test_receive_stock_increments_quantity(store):
    result = store["receive_stock"]("Maggi", 20, 11.0, 15.0)
    assert "Restock" in result
    assert "120" in result

    from sqlmodel import Session, select
    from db.models import Batch, Product

    with Session(store["engine"].engine) as session:
        product = session.exec(
            select(Product).where(Product.sku_id == "MAGGI-70G")
        ).one()
        # Total stock is the sum of all batches...
        assert product.quantity == 120

        batches = session.exec(select(Batch).where(Batch.sku_id == "MAGGI-70G")).all()
        assert len(batches) == 2  # pre-existing seeded stock folded into its own legacy batch, plus the new one
        new_batch = next(b for b in batches if b.cost_price == 11.0)
        assert new_batch.mrp == 15.0
        assert new_batch.quantity == 20

        # ...but the cached display price mirrors the FEFO (next-to-sell) batch,
        # which is the older legacy stock, not the newly arrived (pricier) lot.
        assert product.cost_price == 10.0
        assert product.mrp == 14.0


def test_receive_stock_different_batches_bill_split_by_price(store):
    """Two batches of the same product at different MRPs -> billing a quantity
    spanning both draws FEFO and produces two separate priced lines."""
    store["receive_stock"]("Maggi", 20, 11.0, 15.0)

    result = store["add_to_bill_draft"]("chat1", "Maggi", 110)
    assert "Added 110" in result

    from sqlmodel import Session
    from db.models import BillDraft

    with Session(store["engine"].engine) as session:
        draft = session.get(BillDraft, "chat1")
        items = json.loads(draft.items_json)
        assert len(items) == 2
        by_mrp = {i["mrp"]: i["qty"] for i in items}
        assert by_mrp[14.0] == 100  # all of the legacy batch
        assert by_mrp[15.0] == 10  # spills into the new batch


def test_add_new_product_pricing_compliance_refused(store):
    result = store["add_new_product"]("Fancy Chips", 100, 50, 0.12, "1905")
    assert result.startswith("REFUSED")

    from sqlmodel import Session, select
    from db.models import Product

    with Session(store["engine"].engine) as session:
        products = session.exec(
            select(Product).where(Product.name.contains("Fancy Chips"))
        ).all()
        assert products == []


def test_modify_active_draft_sets_absolute_quantity(store):
    store["add_to_bill_draft"]("chat1", "Maggi", 2)
    result = store["modify_active_draft"]("chat1", "Maggi", 5)
    assert "5" in result

    from sqlmodel import Session, select
    from db.models import BillDraft, Product

    with Session(store["engine"].engine) as session:
        draft = session.get(BillDraft, "chat1")
        items = json.loads(draft.items_json)
        assert items[0]["qty"] == 5

        product = session.exec(
            select(Product).where(Product.sku_id == "MAGGI-70G")
        ).one()
        assert product.quantity == 100  # physical stock untouched


def test_modify_active_draft_zero_drops_line(store):
    store["add_to_bill_draft"]("chat1", "Maggi", 2)
    result = store["modify_active_draft"]("chat1", "Maggi", 0)
    assert "Dropped" in result

    from sqlmodel import Session
    from db.models import BillDraft

    with Session(store["engine"].engine) as session:
        draft = session.get(BillDraft, "chat1")
        items = json.loads(draft.items_json)
        assert all(i["sku_id"] != "MAGGI-70G" for i in items)
