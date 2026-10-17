import importlib
import os
import tempfile

import pytest


@pytest.fixture()
def store(monkeypatch):
    """Fresh temp-SQLite DB per test; reload db/tools.preferences modules against it."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")

    import db.engine as engine_mod
    importlib.reload(engine_mod)
    import db.seed as seed_mod
    importlib.reload(seed_mod)
    import tools.billing as billing_mod
    importlib.reload(billing_mod)
    import tools.preferences as prefs_mod
    importlib.reload(prefs_mod)

    engine_mod.init_db()
    seed_mod.seed_if_empty()

    yield {
        "set_owner_preference": prefs_mod.set_owner_preference,
        "get_owner_preferences": prefs_mod.get_owner_preferences,
        "format_preferences_preamble": prefs_mod.format_preferences_preamble,
        "engine": engine_mod,
        "models": __import__("db.models", fromlist=["OwnerPreference"]),
    }

    os.remove(path)
    wal = path + "-wal"
    shm = path + "-shm"
    for extra in (wal, shm):
        if os.path.exists(extra):
            os.remove(extra)


def test_preference_persistence(store):
    result = store["set_owner_preference"]("default_payment_mode", "upi", "chat1")
    assert result == "Success: Preference saved. default_payment_mode is now set to upi."

    from sqlmodel import Session

    OwnerPreference = store["models"].OwnerPreference
    with Session(store["engine"].engine) as session:
        row = session.get(OwnerPreference, ("chat1", "default_payment_mode"))
        assert row is not None
        assert row.value == "upi"


def test_upsert_cleanliness(store):
    store["set_owner_preference"]("default_payment_mode", "upi", "chat1")
    store["set_owner_preference"]("default_payment_mode", "cash", "chat1")

    from sqlmodel import select, Session

    OwnerPreference = store["models"].OwnerPreference
    with Session(store["engine"].engine) as session:
        rows = session.exec(
            select(OwnerPreference).where(
                OwnerPreference.chat_id == "chat1",
                OwnerPreference.key == "default_payment_mode",
            )
        ).all()
    assert len(rows) == 1
    assert rows[0].value == "cash"


def test_dict_extraction(store):
    store["set_owner_preference"]("default_payment_mode", "upi", "chat1")
    store["set_owner_preference"]("preferred_brand_atta", "Aashirvaad", "chat1")

    prefs = store["get_owner_preferences"]("chat1")
    assert prefs == {"default_payment_mode": "upi", "preferred_brand_atta": "Aashirvaad"}


def test_invalid_payment_mode_refused(store):
    result = store["set_owner_preference"]("default_payment_mode", "netbanking", "chat1")
    assert result == "REFUSED: Invalid payment mode configuration option."
    assert store["get_owner_preferences"]("chat1") == {}


def test_preamble_always_emits_fixed_block(store):
    empty_preamble = store["format_preferences_preamble"]({})
    assert "[ACTIVE OWNER STANDING PREFERENCES]" in empty_preamble
    assert "- Default Payment Mode: None Specified" in empty_preamble
    assert "- Preferred Brand for Atta: None Specified" in empty_preamble

    store["set_owner_preference"]("default_payment_mode", "upi", "chat1")
    prefs = store["get_owner_preferences"]("chat1")
    filled_preamble = store["format_preferences_preamble"](prefs)
    assert "- Default Payment Mode: upi" in filled_preamble
    assert "- Preferred Brand for Atta: None Specified" in filled_preamble
