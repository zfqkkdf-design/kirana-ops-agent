"""Standing owner preferences: persist across /newchat, injected into every turn's prompt."""
import re

from sqlmodel import select

from db.engine import get_session
from db.models import OwnerPreference
from tools.billing import VALID_PAYMENT_MODES
from reporting.invoice_factory import VALID_PDF_FORMATS


def _normalize_key(key: str) -> str:
    return re.sub(r"\s+", "_", key.strip().lower())


def set_owner_preference(key: str, value: str, chat_id: str) -> str:
    """Set or update a standing owner preference that persists across /newchat.

    Args:
        key: Preference name, e.g. "default_payment_mode" or "preferred_brand_atta".
        value: The preference value.
        chat_id: Telegram chat id to scope the preference to.

    Returns:
        "Success: Preference saved. {key} is now set to {value}." on success, or a
        "REFUSED: ..." string if the key/value is invalid.
    """
    norm_key = _normalize_key(key)
    norm_value = value.strip()

    if not norm_key:
        return "REFUSED: preference key cannot be empty."
    if not norm_value:
        return "REFUSED: preference value cannot be empty."

    if norm_key == "default_payment_mode":
        norm_value = norm_value.lower()
        if norm_value not in VALID_PAYMENT_MODES:
            return "REFUSED: Invalid payment mode configuration option."

    if norm_key == "default_pdf_format":
        norm_value = norm_value.lower()
        if norm_value not in VALID_PDF_FORMATS:
            return "REFUSED: Invalid PDF format configuration option."

    with get_session() as session:
        existing = session.get(OwnerPreference, (chat_id, norm_key))
        if existing:
            existing.value = norm_value
            session.add(existing)
        else:
            session.add(OwnerPreference(chat_id=chat_id, key=norm_key, value=norm_value))
        session.commit()

    return f"Success: Preference saved. {norm_key} is now set to {norm_value}."


def get_owner_preferences(chat_id: str) -> dict:
    """Read all standing preferences for a chat_id as a flat dict. Read-only."""
    with get_session() as session:
        rows = session.exec(select(OwnerPreference).where(OwnerPreference.chat_id == chat_id)).all()
    return {row.key: row.value for row in rows}


def format_preferences_preamble(prefs: dict) -> str:
    """Render the fixed two-line ACTIVE preferences block. Always emitted, even when empty."""
    return (
        "[ACTIVE OWNER STANDING PREFERENCES]\n"
        f"- Default Payment Mode: {prefs.get('default_payment_mode', 'None Specified')}\n"
        f"- Default PDF Format: {prefs.get('default_pdf_format', 'None Specified')}\n"
        f"- Preferred Brand for Atta: {prefs.get('preferred_brand_atta', 'None Specified')}\n"
        f"- Shop Name (on invoices): {prefs.get('shop_name', 'None Specified')}\n"
        f"- Shop GSTIN (on invoices): {prefs.get('shop_gstin', 'None Specified')}\n"
        f"- Preferred Language: {prefs.get('preferred_language', 'None Specified')}\n"
        "\n"
        "Apply these configuration preferences implicitly to transactions or product selections "
        "unless the user explicitly defines a conflicting override instruction in their active "
        "message turn."
    )
