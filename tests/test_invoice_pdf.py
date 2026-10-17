import importlib
import json
import os
import tempfile
from datetime import datetime

import pytest


@pytest.fixture()
def store(monkeypatch):
    """Fresh temp-SQLite DB per test; reload db/reporting modules against it."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")

    import db.engine as engine_mod
    importlib.reload(engine_mod)
    import db.seed as seed_mod
    importlib.reload(seed_mod)
    import reporting.invoice_factory as invoice_mod
    importlib.reload(invoice_mod)

    engine_mod.init_db()
    seed_mod.seed_if_empty()

    yield {
        "generate_pdf_invoice": invoice_mod.generate_pdf_invoice,
        "engine": engine_mod,
    }

    os.remove(path)
    wal = path + "-wal"
    shm = path + "-shm"
    for extra in (wal, shm):
        if os.path.exists(extra):
            os.remove(extra)


def _add_sale(engine_mod):
    from sqlmodel import Session

    from db.models import Sale

    items = [{"sku_id": "MAGGI-70G", "name": "Maggi 70g", "qty": 4, "mrp": 14.0, "gst_slab": 0.12, "hsn": "1902"}]
    with Session(engine_mod.engine) as session:
        sale = Sale(
            timestamp=datetime.utcnow(),
            chat_id="chat1",
            items_json=json.dumps(items),
            subtotal=56.0,
            cgst=3.36,
            sgst=3.36,
            total=62.72,
            payment_mode="cash",
        )
        session.add(sale)
        session.commit()
        session.refresh(sale)
        return sale.invoice_id


def test_successful_compilation(store):
    invoice_id = _add_sale(store["engine"])
    result = store["generate_pdf_invoice"](invoice_id)
    try:
        assert isinstance(result, str)
        assert os.path.isfile(result)
    finally:
        if os.path.isfile(result):
            os.remove(result)


def test_file_integrity(store):
    invoice_id = _add_sale(store["engine"])
    result = store["generate_pdf_invoice"](invoice_id)
    try:
        with open(result, "rb") as f:
            assert f.read(4) == b"%PDF"
    finally:
        if os.path.isfile(result):
            os.remove(result)


def test_missing_invoice(store):
    result = store["generate_pdf_invoice"](999999)
    assert result == "ERROR: Invoice ID not found."
