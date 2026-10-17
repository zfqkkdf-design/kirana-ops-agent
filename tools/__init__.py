from tools.billing import (
    add_to_bill_draft,
    claim_message,
    finalize_bill,
    modify_active_draft,
    view_bill_draft,
)
from tools.inventory import add_new_product, receive_stock, set_product_barcode
from tools.expiry import expiry_report, product_batches
from tools.khata import get_khata_balance, khata_dues_report, modify_khata_credit
from tools.management import close_day_summary, low_stock_report
from tools.reorder import reorder_suggestions, update_reorder_level
from tools.stock import check_stock
from tools.preferences import set_owner_preference
from tools.schedule import set_khata_reminder_schedule, set_weekly_deck_schedule
from reporting.invoice_factory import generate_pdf_invoice
from reporting.analytics_factory import generate_sales_deck

STORE_TOOLS = [
    check_stock,
    add_to_bill_draft,
    view_bill_draft,
    finalize_bill,
    modify_khata_credit,
    get_khata_balance,
    khata_dues_report,
    receive_stock,
    add_new_product,
    modify_active_draft,
    close_day_summary,
    low_stock_report,
    generate_pdf_invoice,
    generate_sales_deck,
    set_owner_preference,
    reorder_suggestions,
    update_reorder_level,
    expiry_report,
    product_batches,
    set_weekly_deck_schedule,
    set_khata_reminder_schedule,
    set_product_barcode,
]

__all__ = [
    "STORE_TOOLS",
    "check_stock",
    "add_to_bill_draft",
    "view_bill_draft",
    "finalize_bill",
    "modify_khata_credit",
    "get_khata_balance",
    "khata_dues_report",
    "claim_message",
    "receive_stock",
    "add_new_product",
    "modify_active_draft",
    "close_day_summary",
    "low_stock_report",
    "generate_pdf_invoice",
    "generate_sales_deck",
    "set_owner_preference",
    "set_weekly_deck_schedule",
    "set_khata_reminder_schedule",
    "set_product_barcode",
    "reorder_suggestions",
    "update_reorder_level",
    "expiry_report",
    "product_batches",
]
