import importlib
import os
import tempfile
import threading

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
    import tools.khata as khata_mod
    importlib.reload(khata_mod)
    import tools.billing as billing_mod
    importlib.reload(billing_mod)

    engine_mod.init_db()
    seed_mod.seed_if_empty()

    yield {
        "modify_khata_credit": khata_mod.modify_khata_credit,
        "get_khata_balance": khata_mod.get_khata_balance,
        "add_to_bill_draft": billing_mod.add_to_bill_draft,
        "finalize_bill": billing_mod.finalize_bill,
        "engine": engine_mod,
    }

    os.remove(path)
    for extra in (path + "-wal", path + "-shm"):
        if os.path.exists(extra):
            os.remove(extra)


def test_new_customer_generation(store):
    result = store["modify_khata_credit"]("Ramesh", 100, "charge")
    assert "Success" in result
    assert "100.0" in result

    from sqlmodel import Session, select
    from db.models import KhataLedger

    with Session(store["engine"].engine) as session:
        ledgers = session.exec(select(KhataLedger)).all()
        assert len(ledgers) == 1
        assert ledgers[0].name == "Ramesh"
        assert ledgers[0].balance == 100.0


def test_debt_incrementation(store):
    store["modify_khata_credit"]("Ramesh", 100, "charge")
    store["modify_khata_credit"]("Ramesh", 500, "charge")
    result = store["get_khata_balance"]("Ramesh")
    assert "600.0" in result


def test_debt_settlement(store):
    store["modify_khata_credit"]("Ramesh", 500, "charge")
    store["modify_khata_credit"]("Ramesh", 300, "settle")
    result = store["get_khata_balance"]("Ramesh")
    assert "200.0" in result


def test_credit_billing_finalize(store):
    store["add_to_bill_draft"]("chat1", "Maggi", 4)
    result = store["finalize_bill"]("chat1", "khata", customer_name="Ramesh")
    assert "TOTAL" in result

    from sqlmodel import Session, select
    from db.models import KhataLedger, Product, Sale

    with Session(store["engine"].engine) as session:
        product = session.exec(select(Product).where(Product.sku_id == "MAGGI-70G")).one()
        assert product.quantity == 96

        sale = session.exec(select(Sale)).one()
        assert sale.payment_mode == "khata"
        assert sale.customer_id is not None

        ledger = session.exec(select(KhataLedger).where(KhataLedger.customer_id == sale.customer_id)).one()
        assert ledger.balance == sale.total


def test_fuzzy_name_matching(store):
    store["modify_khata_credit"]("Ramesh Kumar", 100, "charge")
    result = store["get_khata_balance"]("ramesh")
    assert "100.0" in result
    charge_result = store["modify_khata_credit"]("ramesh", 50, "charge")
    assert "150.0" in charge_result


def test_settle_on_nonexistent_khata_refused(store):
    result = store["modify_khata_credit"]("Nobody", 100, "settle")
    assert result.startswith("REFUSED")

    from sqlmodel import Session, select
    from db.models import KhataLedger

    with Session(store["engine"].engine) as session:
        assert session.exec(select(KhataLedger)).all() == []


def test_settle_overpay_refused(store):
    store["modify_khata_credit"]("Ramesh", 100, "charge")
    result = store["modify_khata_credit"]("Ramesh", 500, "settle")
    assert result.startswith("REFUSED")

    balance = store["get_khata_balance"]("Ramesh")
    assert "100.0" in balance  # unchanged


def test_concurrent_charge_and_balance_no_race(store):
    """Simulates the agent firing modify_khata_credit + get_khata_balance as parallel
    tool calls in one turn: the balance read must never see a half-committed charge."""
    results = {}

    def charge():
        results["charge"] = store["modify_khata_credit"]("Ramesh", 500, "charge")

    def balance():
        results["balance"] = store["get_khata_balance"]("Ramesh")

    t1 = threading.Thread(target=charge)
    t2 = threading.Thread(target=balance)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert "Success" in results["charge"]
    # Global DB lock serializes tool calls, so the balance read is either
    # NOT_FOUND (ran first) or sees the full committed ₹500 — never a partial state.
    assert results["balance"].startswith("NOT_FOUND") or "500.0" in results["balance"]
