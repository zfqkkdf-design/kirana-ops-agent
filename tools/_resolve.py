"""Shared product name resolution: substring match, falling back to fuzzy match for typos."""
import difflib
from typing import Union

from sqlmodel import Session, select

from db.models import Product

FUZZY_CUTOFF = 0.7
FUZZY_TIE_MARGIN = 0.05


def _fuzzy_score(needle: str, product: Product) -> float:
    name_lower = product.name.lower()
    tokens = name_lower.replace(",", " ").split()
    candidates = [name_lower, *tokens]
    return max(difflib.SequenceMatcher(None, needle, c).ratio() for c in candidates)


def resolve_product(session: Session, item_name: str, sku_id: str | None = None) -> Union[Product, str]:
    """Return the matching Product, or a NOT_FOUND/AMBIGUOUS string.

    sku_id, when given, resolves by exact primary key instead of name matching —
    used only to confirm an already-disambiguated tap-to-select pick, where
    re-running name matching on the picked label could re-trigger the same
    ambiguity (e.g. picking "Maggi" when "Maggi 70g" and "Maggi Masala" both exist).
    """
    if sku_id is not None:
        product = session.get(Product, sku_id)
        return product if product else f"NOT_FOUND: No product matching '{item_name}'."

    needle = item_name.strip().lower()
    products = session.exec(select(Product)).all()

    substring_matches = [p for p in products if needle in p.name.lower()]
    if len(substring_matches) == 1:
        return substring_matches[0]
    if len(substring_matches) > 1:
        names = "' and '".join(p.name for p in substring_matches)
        return f"AMBIGUOUS: Found '{names}'. Clarify which one."

    scored = [(_fuzzy_score(needle, p), p) for p in products]
    scored = [(score, p) for score, p in scored if score >= FUZZY_CUTOFF]
    if not scored:
        return f"NOT_FOUND: No product matching '{item_name}'."

    top_score = max(score for score, _ in scored)
    top_matches = [p for score, p in scored if score >= top_score - FUZZY_TIE_MARGIN]
    if len(top_matches) > 1:
        names = "' and '".join(p.name for p in top_matches)
        return f"AMBIGUOUS: Found '{names}'. Clarify which one."
    return top_matches[0]


def resolve_product_by_barcode(session: Session, barcode: str) -> Product | None:
    """Return the Product with an exact barcode match, or None if unset/no match.

    Exact match only — unlike resolve_product's name matching, a decoded barcode
    is either the right digits or it isn't, so there's no fuzzy/substring case.
    """
    return session.exec(select(Product).where(Product.barcode == barcode)).first()
