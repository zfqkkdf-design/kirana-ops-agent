import importlib
import json
import os
import tempfile
from datetime import datetime

import pytest
from pptx import Presentation


@pytest.fixture()
def store(monkeypatch):
    """Fresh temp-SQLite DB per test; reload db/management/reporting modules against it."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")

    import db.engine as engine_mod
    importlib.reload(engine_mod)
    import db.seed as seed_mod
    importlib.reload(seed_mod)
    import tools.management as management_mod
    importlib.reload(management_mod)
    import reporting.analytics_factory as analytics_mod
    importlib.reload(analytics_mod)

    engine_mod.init_db()
    seed_mod.seed_if_empty()

    yield {
        "generate_sales_deck": analytics_mod.generate_sales_deck,
        "engine": engine_mod,
    }

    os.remove(path)
    wal = path + "-wal"
    shm = path + "-shm"
    for extra in (wal, shm):
        if os.path.exists(extra):
            os.remove(extra)


def _add_sales(engine_mod):
    from sqlmodel import Session

    from db.models import Sale

    items_a = [{"sku_id": "MAGGI-70G", "name": "Maggi 70g", "qty": 4, "mrp": 14.0, "gst_slab": 0.12, "hsn": "1902"}]
    items_b = [{"sku_id": "LAYS-52G", "name": "Lays 52g", "qty": 2, "mrp": 20.0, "gst_slab": 0.12, "hsn": "2005"}]
    with Session(engine_mod.engine) as session:
        session.add(Sale(
            timestamp=datetime.utcnow(),
            chat_id="chat1",
            items_json=json.dumps(items_a),
            subtotal=56.0, cgst=3.36, sgst=3.36, total=62.72,
            payment_mode="cash",
        ))
        session.add(Sale(
            timestamp=datetime.utcnow(),
            chat_id="chat1",
            items_json=json.dumps(items_b),
            subtotal=40.0, cgst=2.4, sgst=2.4, total=44.8,
            payment_mode="upi",
        ))
        session.commit()


def _cleanup(path):
    if path and os.path.isfile(path):
        os.remove(path)
    if os.path.isfile("/tmp/temp_chart.png"):
        os.remove("/tmp/temp_chart.png")


def test_successful_deck_assembly(store):
    _add_sales(store["engine"])
    result = store["generate_sales_deck"]("chat1")
    try:
        assert isinstance(result, str)
        assert os.path.isfile(result)
    finally:
        _cleanup(result)


def test_slide_structure_integrity(store):
    _add_sales(store["engine"])
    result = store["generate_sales_deck"]("chat1")
    try:
        prs = Presentation(result)
        # cover, agenda, financial summary, top items, >=1 inventory alerts page, closing
        assert len(prs.slides) >= 6
    finally:
        _cleanup(result)


def test_clean_file_termination(store):
    _add_sales(store["engine"])
    result = store["generate_sales_deck"]("chat1")
    try:
        with open(result, "rb") as f:
            assert f.read(2) == b"PK"
    finally:
        _cleanup(result)
