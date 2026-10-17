"""GST-correct PDF invoice factory: renders a finalized Sale row as HTML, compiles via WeasyPrint.

Supports two layouts: "a4" (itemized tax invoice for filing/printing on a normal
printer) and "thermal" (narrow receipt for a small thermal/POS printer).
"""
import json
import os
from decimal import ROUND_HALF_UP, Decimal

from weasyprint import HTML

from db.engine import get_session
from db.models import KhataLedger, Sale

SHOP_NAME_DEFAULT = "Kirana Ops Store"
SHOP_GSTIN_DEFAULT = "29AAAAA0000A1Z5"
SHOP_ADDRESS = "123 Market Road, City — 560001"
SHOP_PHONE = "+91-98765-43210"

VALID_PDF_FORMATS = ("a4", "thermal")


def _round2(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _khata_info(session, sale: Sale) -> dict | None:
    """Billed-to customer name + balance *after* this sale, or None for a cash/upi/card sale.

    Takes the caller's already-open session — get_session() holds a non-reentrant
    lock, so a nested `with get_session()` here would deadlock.
    """
    if not sale.customer_id:
        return None
    ledger = session.get(KhataLedger, sale.customer_id)
    if ledger is None:
        return None
    return {"name": ledger.name, "balance": _round2(ledger.balance)}


# ---------------------------------------------------------------------------
# A4 tax invoice
# ---------------------------------------------------------------------------

def _line_rows_html(items: list[dict]) -> str:
    rows = []
    for item in items:
        taxable = _round2(item["mrp"] * item["qty"])
        gst = taxable * item["gst_slab"]
        cgst = _round2(gst / 2)
        sgst = _round2(gst / 2)
        line_total = _round2(taxable + cgst + sgst)
        rows.append(
            "<tr>"
            f"<td>{item['name']}</td>"
            f"<td>{item['hsn']}</td>"
            f"<td>{item['qty']}</td>"
            f"<td>₹{item['mrp']}</td>"
            f"<td>₹{taxable}</td>"
            f"<td>{item['gst_slab'] * 100:.0f}%</td>"
            f"<td>₹{cgst}</td>"
            f"<td>₹{sgst}</td>"
            f"<td>₹{line_total}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def _khata_block_html(khata: dict | None) -> str:
    if not khata:
        return ""
    return (
        '<div class="khata">'
        f"Billed to: <strong>{khata['name']}</strong><br>"
        f"Khata balance after this bill: ₹{khata['balance']}"
        "</div>"
    )


def _logo_img_html(logo_path: str | None, css_class: str) -> str:
    if not logo_path or not os.path.isfile(logo_path):
        return ""
    return f'<img class="{css_class}" src="file://{logo_path}">'


def _build_a4_html(
    sale: Sale, items: list[dict], khata: dict | None, shop_name: str, shop_gstin: str, logo_path: str | None = None
) -> str:
    return f"""
    <html>
    <head>
    <style>
      @page {{ size: A4; margin: 18mm; }}
      body {{ font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 12px; color: #222; }}
      .header {{ border-bottom: 3px solid #222; padding-bottom: 10px; margin-bottom: 14px;
                 display: flex; align-items: center; gap: 14px; }}
      .header-text {{ flex: 1; }}
      .logo {{ width: 60px; height: 60px; object-fit: cover; border-radius: 6px; }}
      h1 {{ font-size: 22px; margin: 0 0 4px 0; letter-spacing: 0.5px; }}
      .shop-meta {{ color: #555; font-size: 11px; line-height: 1.5; }}
      .invoice-meta {{ margin: 14px 0; font-size: 12px; line-height: 1.6; }}
      .invoice-meta td {{ padding: 1px 12px 1px 0; }}
      .khata {{ margin: 10px 0 16px 0; padding: 8px 12px; background: #f4f4f4;
                border-left: 3px solid #222; font-size: 12px; line-height: 1.6; }}
      table.items {{ width: 100%; border-collapse: collapse; margin-top: 6px; }}
      table.items th {{ background: #222; color: #fff; padding: 6px 8px; text-align: right; font-size: 11px; }}
      table.items th:first-child {{ text-align: left; }}
      table.items td {{ border-bottom: 1px solid #ddd; padding: 6px 8px; text-align: right; }}
      table.items td:first-child {{ text-align: left; }}
      table.items tr:nth-child(even) {{ background: #fafafa; }}
      .footer {{ margin-top: 18px; text-align: right; font-size: 13px; line-height: 1.8; }}
      .footer .total {{ font-size: 16px; font-weight: bold; border-top: 2px solid #222; padding-top: 6px; }}
      .thanks {{ margin-top: 24px; text-align: center; color: #777; font-size: 11px; }}
    </style>
    </head>
    <body>
      <div class="header">
        {_logo_img_html(logo_path, "logo")}
        <div class="header-text">
          <h1>{shop_name}</h1>
          <div class="shop-meta">
            {SHOP_ADDRESS}<br>
            Tel: {SHOP_PHONE} &nbsp;·&nbsp; GSTIN: {shop_gstin}
          </div>
        </div>
      </div>
      <table class="invoice-meta">
        <tr><td><strong>Invoice #</strong></td><td>{sale.invoice_id}</td></tr>
        <tr><td><strong>Date</strong></td><td>{sale.timestamp.isoformat(sep=" ", timespec="minutes")}</td></tr>
        <tr><td><strong>Payment mode</strong></td><td>{sale.payment_mode.upper()}</td></tr>
        {f'<tr><td><strong>Reference</strong></td><td>{sale.payment_reference}</td></tr>' if sale.payment_reference else ''}
      </table>
      {_khata_block_html(khata)}
      <table class="items">
        <thead>
          <tr>
            <th>Item</th><th>HSN</th><th>Qty</th><th>Rate</th><th>Taxable</th>
            <th>GST%</th><th>CGST</th><th>SGST</th><th>Line total</th>
          </tr>
        </thead>
        <tbody>
          {_line_rows_html(items)}
        </tbody>
      </table>
      <div class="footer">
        Subtotal: ₹{sale.subtotal}<br>
        CGST: ₹{sale.cgst}<br>
        SGST: ₹{sale.sgst}<br>
        <span class="total">TOTAL: ₹{sale.total}</span>
      </div>
      <div class="thanks">Thank you for shopping with us!</div>
    </body>
    </html>
    """


# ---------------------------------------------------------------------------
# Thermal receipt (narrow, small-printer friendly)
# ---------------------------------------------------------------------------

def _thermal_line_rows_html(items: list[dict]) -> str:
    rows = []
    for item in items:
        line_total = _round2(item["mrp"] * item["qty"])
        rows.append(
            '<div class="row item">'
            f'<span class="name">{item["name"]}</span>'
            f'<span class="qty">{item["qty"]}</span>'
            f'<span class="amt">₹{line_total}</span>'
            "</div>"
        )
    return "\n".join(rows)


def _thermal_khata_html(khata: dict | None) -> str:
    if not khata:
        return ""
    return (
        '<div class="sep"></div>'
        f'<div class="row"><span>Billed to</span><span>{khata["name"]}</span></div>'
        f'<div class="row"><span>Khata balance</span><span>₹{khata["balance"]}</span></div>'
    )


def _thermal_page_height_mm(items: list[dict], khata: dict | None, has_reference: bool) -> int:
    """WeasyPrint 69 doesn't shrink `size: 80mm auto` to content, so estimate an
    explicit page height instead — a bit tall is fine for a receipt (the thermal
    printer just cuts at content end); a fixed A4-height page with a mostly-blank
    bottom half is the actual bug this avoids.
    """
    header_mm = 34
    meta_mm = 18 + (6 if has_reference else 0)
    per_item_mm = 6 * len(items)
    totals_mm = 24
    khata_mm = 16 if khata else 0
    footer_mm = 20
    return header_mm + meta_mm + per_item_mm + totals_mm + khata_mm + footer_mm


def _build_thermal_html(
    sale: Sale, items: list[dict], khata: dict | None, shop_name: str, shop_gstin: str, logo_path: str | None = None
) -> str:
    page_height_mm = _thermal_page_height_mm(items, khata, bool(sale.payment_reference))
    if logo_path and os.path.isfile(logo_path):
        page_height_mm += 20
    return f"""
    <html>
    <head>
    <style>
      @page {{ size: 80mm {page_height_mm}mm; margin: 4mm; }}
      body {{ font-family: 'Courier New', monospace; font-size: 11px; color: #000; width: 72mm; }}
      .center {{ text-align: center; }}
      .logo {{ width: 40px; height: 40px; object-fit: cover; border-radius: 4px; margin-bottom: 4px; }}
      h1 {{ font-size: 15px; margin: 0 0 2px 0; }}
      .shop-meta {{ font-size: 9px; line-height: 1.4; }}
      .sep {{ border-top: 1px dashed #000; margin: 6px 0; }}
      .row {{ display: flex; justify-content: space-between; padding: 1px 0; }}
      .row.item .name {{ flex: 1; }}
      .row.item .qty {{ width: 20px; text-align: center; }}
      .row.item .amt {{ width: 60px; text-align: right; }}
      .total-row {{ font-weight: bold; font-size: 13px; }}
      .thanks {{ margin-top: 10px; font-size: 10px; }}
    </style>
    </head>
    <body>
      <div class="center">
        {_logo_img_html(logo_path, "logo")}
        <h1>{shop_name}</h1>
        <div class="shop-meta">
          {SHOP_ADDRESS}<br>
          Tel: {SHOP_PHONE}<br>
          GSTIN: {shop_gstin}
        </div>
      </div>
      <div class="sep"></div>
      <div class="row"><span>Invoice #</span><span>{sale.invoice_id}</span></div>
      <div class="row"><span>Date</span><span>{sale.timestamp.strftime("%d-%m-%Y %H:%M")}</span></div>
      <div class="row"><span>Payment</span><span>{sale.payment_mode.upper()}</span></div>
      {f'<div class="row"><span>Reference</span><span>{sale.payment_reference}</span></div>' if sale.payment_reference else ''}
      <div class="sep"></div>
      {_thermal_line_rows_html(items)}
      <div class="sep"></div>
      <div class="row"><span>Subtotal</span><span>₹{sale.subtotal}</span></div>
      <div class="row"><span>CGST</span><span>₹{sale.cgst}</span></div>
      <div class="row"><span>SGST</span><span>₹{sale.sgst}</span></div>
      <div class="row total-row"><span>TOTAL</span><span>₹{sale.total}</span></div>
      {_thermal_khata_html(khata)}
      <div class="sep"></div>
      <div class="center thanks">THANK YOU!<br>Glad to see you again!</div>
    </body>
    </html>
    """


def generate_pdf_invoice(invoice_id: int, format: str = "a4") -> str:
    """Render a finalized sale as a PDF invoice and write it to disk.

    Args:
        invoice_id: The Bill # / Sale.invoice_id returned by finalize_bill.
        format: "a4" for a full itemized tax invoice, or "thermal" for a narrow
            receipt suited to a small thermal/POS printer. Defaults to "a4".

    Returns:
        Absolute filesystem path to the generated PDF, or an "ERROR: ..." string
        if the invoice doesn't exist or the format is invalid.
    """
    norm_format = format.strip().lower()
    if norm_format not in VALID_PDF_FORMATS:
        return f"ERROR: Invalid PDF format {format!r}. Use 'a4' or 'thermal'."

    with get_session() as session:
        sale = session.get(Sale, invoice_id)
        if sale is None:
            return "ERROR: Invoice ID not found."
        items = json.loads(sale.items_json)
        khata = _khata_info(session, sale)
        chat_id = sale.chat_id

    # Owner-set shop name/GSTIN (set_owner_preference) so branded invoices persist
    # across chats — deferred import + fetched after the lock above is released,
    # since get_owner_preferences() opens its own get_session() and the lock isn't
    # reentrant.
    from tools.preferences import get_owner_preferences

    prefs = get_owner_preferences(chat_id)
    shop_name = prefs.get("shop_name") or SHOP_NAME_DEFAULT
    shop_gstin = prefs.get("shop_gstin") or SHOP_GSTIN_DEFAULT
    logo_path = prefs.get("shop_logo_path")

    if norm_format == "thermal":
        html_content = _build_thermal_html(sale, items, khata, shop_name, shop_gstin, logo_path)
    else:
        html_content = _build_a4_html(sale, items, khata, shop_name, shop_gstin, logo_path)

    target_path = f"/tmp/invoice_{invoice_id}_{norm_format}.pdf"
    HTML(string=html_content).write_pdf(target_path)
    return os.path.abspath(target_path)
