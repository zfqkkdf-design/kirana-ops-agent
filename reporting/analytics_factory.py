"""PowerPoint sales analysis deck factory: themed report over today, a custom date range, or the store's overall history."""
import os
from datetime import date

from reporting.invoice_factory import SHOP_NAME_DEFAULT
from scripts.generate_kirana_report import build as build_deck
from tools.management import compute_day_close, compute_overall_summary, compute_summary_for_range
from tools.preferences import get_owner_preferences

VALID_DECK_PERIODS = ("today", "overall")


def _period_label(period_start: str, period_end: str) -> str:
    if not period_start or not period_end:
        return "No sales yet"
    if period_start == period_end:
        return period_start
    return f"{period_start} – {period_end}"


def _metrics_to_deck_fields(metrics: dict) -> dict:
    return {
        "financials": {
            "total_revenue": metrics["total"],
            "sales_count": metrics["sales_count"],
            "subtotal": metrics["subtotal"],
            "cgst": metrics["cgst"],
            "sgst": metrics["sgst"],
        },
        "top_items": [
            {"name": entry.get("name"), "qty_sold": entry.get("qty")}
            for entry in metrics["top_items"]
        ],
        "inventory_alerts": [
            {"name": p.get("name"), "qty": p.get("quantity"), "reorder_level": p.get("reorder_level")}
            for p in metrics["low_stock"]
        ],
    }


def _build_deck_data(chat_id: str, period: str, start_date: str | None, end_date: str | None) -> dict:
    prefs = get_owner_preferences(chat_id)
    store_name = prefs.get("shop_name") or SHOP_NAME_DEFAULT
    logo_path = prefs.get("shop_logo_path")
    today = date.today().isoformat()

    if start_date and end_date:
        metrics = compute_summary_for_range(chat_id, start_date, end_date)
        label = _period_label(metrics["period_start"], metrics["period_end"])
        data = {
            "store_name": store_name,
            "period_label": label,
            "report_date": today,
            "report_type": "Sales Analysis Report",
            "report_tagline": f"Revenue, top sellers, and inventory health for {label}.",
        }
    elif period == "today":
        metrics = compute_day_close(chat_id)
        data = {
            "store_name": store_name,
            "period_label": metrics["date"],
            "report_date": metrics["date"],
            "report_type": "Daily Sales Report",
            "report_tagline": "Today's revenue, top sellers, and inventory health.",
        }
    else:
        metrics = compute_overall_summary(chat_id)
        data = {
            "store_name": store_name,
            "period_label": _period_label(metrics["period_start"], metrics["period_end"]),
            "report_date": today,
            "report_type": "Sales Analysis Report",
            "report_tagline": "Store performance, top sellers, and inventory health overview.",
        }

    data["logo_path"] = logo_path
    data.update(_metrics_to_deck_fields(metrics))
    return data


def generate_sales_deck(
    chat_id: str,
    period: str = "overall",
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Build a themed PowerPoint sales analysis deck.

    Args:
        chat_id: Telegram chat id, used to scope owner preferences (shop name) and
            agent wiring parity.
        period: "today" for just today's close, or "overall" (default) for the
            store's entire sales history. Ignored if start_date/end_date are given.
            Pick "today" only when the owner's wording is explicitly about today
            (e.g. "today's report", "daily deck"); default to "overall" for a
            general "sales analysis deck" / "PPTX" request. Invalid values fall
            back to "overall".
        start_date: Inclusive range start, "YYYY-MM-DD". Use this (with end_date)
            for any specific period the owner names in words — "yesterday",
            "last month", "this week", "1st to 15th" — by resolving it yourself
            into concrete dates against the current date before calling. Leave
            both start_date and end_date unset for "today" or "overall".
        end_date: Inclusive range end, "YYYY-MM-DD". Required together with
            start_date.

    Returns:
        Absolute filesystem path to the generated PPTX, or a "REFUSED: ..."
        string if start_date/end_date were given but are invalid or reversed.
    """
    if start_date and end_date:
        try:
            data = _build_deck_data(chat_id, period, start_date, end_date)
        except ValueError as exc:
            return f"REFUSED: invalid date range ({exc})."
        report_date = f"{start_date}_to_{end_date}"
    else:
        if period not in VALID_DECK_PERIODS:
            period = "overall"
        data = _build_deck_data(chat_id, period, None, None)
        report_date = data["report_date"]

    target_path = f"/tmp/sales_{report_date}.pptx"
    build_deck(data, target_path)
    return os.path.abspath(target_path)
