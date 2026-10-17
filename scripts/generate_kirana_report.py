"""
Kirana Store Sales Report generator.

Reuses the "Portfolio" theme (dark green / cream / orange, Gowun Batang font)
pulled from images/Karan kirana.pptx as design reference.

Usage:
    python3 generate_kirana_report.py data.json output.pptx
    python3 generate_kirana_report.py --sample output.pptx   # use built-in sample data

Data shape (all fields optional — missing/malformed values fall back to safe
defaults rather than breaking the deck):
{
  "store_name": "Kirana Store",
  "period_label": "Q3 2026",
  "report_date": "2026-07-13",
  "financials": {
    "total_revenue": 2979.92,
    "sales_count": 8,
    "subtotal": 2666.00,
    "cgst": 156.96,
    "sgst": 156.96
  },
  "top_items": [
    {"name": "Maggi 70g", "qty_sold": 78.0}
  ],                      # only the first 6 are shown
  "inventory_alerts": [
    {"name": "Lays Chips", "qty": 0.0, "reorder_level": 5.0}
  ]                       # unlimited — overflow spills onto extra slides
}
"""

import json
import os
import sys
from pptx import Presentation
from pptx.util import Emu, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE

# ---- Theme (from Karan kirana.pptx) ----
DARK = RGBColor(0x13, 0x38, 0x17)      # dk1 - deep green, headers/primary text/accents
BG = RGBColor(0xE9, 0xEB, 0xE8)        # lt1 - cream background
CARD_BG = RGBColor(0xFF, 0xFF, 0xFF)
ACCENT = RGBColor(0xEE, 0x76, 0x30)    # lt2 - orange accent, used for emphasis/alerts
GREEN_ACCENT = RGBColor(0x63, 0xA4, 0x6C)  # accent6
SUBTEXT = RGBColor(0x55, 0x55, 0x55)
MUTED = RGBColor(0x66, 0x66, 0x66)
ROW_LINE = RGBColor(0xC2, 0xC7, 0xC0)

FONT = "Gowun Batang"

SLIDE_W = Emu(9144000)
SLIDE_H = Emu(5143500)

TOP_ITEMS_LIMIT = 6
ALERT_ROWS_PER_PAGE = 6

# --- data-safety helpers -----------------------------------------------------

def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_str(value, default=""):
    if value is None:
        return default
    return str(value)


def safe_list(value):
    return value if isinstance(value, list) else []


def truncate_two_lines(text, max_chars=42):
    """Collapse whitespace and clip to roughly two wrapped lines at the cover title size."""
    text = " ".join(safe_str(text, "Kirana Store").split())
    if not text:
        return "Kirana Store"
    limit = max_chars * 2
    if len(text) <= limit:
        return text
    clipped = text[:limit].rsplit(" ", 1)[0].rstrip()
    return (clipped or text[:limit]) + "…"


# --- shape helpers ------------------------------------------------------------

def _set_bg(slide, color):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = color


def _no_line(shape):
    shape.line.fill.background()


def add_rect(slide, x, y, w, h, color, line=False):
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(int(x)), Emu(int(y)), Emu(int(w)), Emu(int(h)))
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    if not line:
        _no_line(shape)
    shape.shadow.inherit = False
    return shape


def add_text(slide, x, y, w, h, runs, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, line_spacing=None):
    """runs: list of paragraphs, each a list of (text, size_pt, bold, color) tuples."""
    box = slide.shapes.add_textbox(Emu(int(x)), Emu(int(y)), Emu(int(w)), Emu(int(h)))
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    for i, para_runs in enumerate(runs):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        if line_spacing:
            p.line_spacing = line_spacing
        for text, size, bold, color in para_runs:
            r = p.add_run()
            r.text = text
            r.font.name = FONT
            r.font.size = Pt(size)
            r.font.bold = bold
            r.font.color.rgb = color
    return box


def add_footer(slide, store_name, report_date):
    add_rect(slide, 228600, 4678950, 8686800, 5700, DARK)
    add_text(slide, 228600, 4700000, 4343400, 300000,
              [[(f"{store_name} ©", 9, False, MUTED)]])
    add_text(slide, 7426775, 4700000, 1502400, 300000,
              [[(report_date, 9, False, MUTED)]], align=PP_ALIGN.RIGHT)


# --- slides ---------------------------------------------------------------

def slide_cover(prs, data):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_bg(slide, BG)
    store_name = truncate_two_lines(data.get("store_name", "Kirana Store"))
    period = safe_str(data.get("period_label", ""))
    report_date = safe_str(data.get("report_date", ""))

    add_text(slide, 228600, 168075, 1889400, 324300,
              [[("Sales Report", 12, False, MUTED)]])
    add_text(slide, 2400300, 168075, 1889400, 324300,
              [[(period, 12, False, MUTED)]])
    add_text(slide, 6743700, 168075, 1889400, 324300,
              [[(report_date, 12, False, MUTED)]], align=PP_ALIGN.RIGHT)

    add_text(slide, 228600, 1650000, 6232800, 1150000,
              [[(store_name, 36, True, DARK)]])

    logo_path = data.get("logo_path")
    if logo_path and os.path.isfile(logo_path):
        slide.shapes.add_picture(logo_path, Emu(7715400), Emu(1650000), Emu(1200000), Emu(1200000))

    add_rect(slide, 228600, 2856025, 800000, 22000, DARK)
    report_type = safe_str(data.get("report_type"), "Sales Analysis Report")
    report_tagline = safe_str(
        data.get("report_tagline"),
        "Store performance, top sellers, and inventory health overview.",
    )
    add_text(slide, 228600, 2950000, 4343400, 670200,
              [[(report_type, 13, True, DARK)],
               [(report_tagline, 11, False, SUBTEXT)]])

    add_footer(slide, store_name, report_date)
    return slide


def slide_agenda(prs, data):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_bg(slide, BG)
    add_text(slide, 228600, 167750, 5257800, 310500,
              [[("Contents", 20, True, DARK)]])

    sections = [
        ("Financial Summary", "Revenue, tax, and transaction totals"),
        ("Top Performing Items", "Products driving today's sales"),
        ("Inventory Alerts", "Items requiring reorder attention"),
    ]
    y = 788749
    row_h = 620500
    for name, desc in sections:
        add_text(slide, 228600, y, 4792800, 310500,
                  [[(name, 13, True, DARK)]])
        add_text(slide, 228600, y + 320000, 6000000, 280000,
                  [[(desc, 10, False, MUTED)]])
        add_rect(slide, 228600, y + row_h - 20000, 8686800, 5700, DARK)
        y += row_h

    add_footer(slide, data.get("store_name", "Kirana Store"), data.get("report_date", ""))
    return slide


def slide_financial_summary(prs, data):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_bg(slide, BG)
    fin = data.get("financials") or {}
    report_date = safe_str(data.get("report_date", ""))

    add_text(slide, 457200, 400000, 5000000, 700000,
              [[("Financial Summary", 26, True, DARK)],
               [(report_date, 12, False, SUBTEXT)]])

    cgst = safe_float(fin.get('cgst'))
    sgst = safe_float(fin.get('sgst'))
    cards = [
        ("Total Revenue", f"₹{safe_float(fin.get('total_revenue')):,.2f}", DARK),
        ("Sales Count", f"{safe_float(fin.get('sales_count')):g}", DARK),
        ("Subtotal", f"₹{safe_float(fin.get('subtotal')):,.2f}", DARK),
        ("GST (Total)", f"₹{cgst + sgst:,.2f}", DARK),
        ("CGST", f"₹{cgst:,.2f}", SUBTEXT),
        ("SGST", f"₹{sgst:,.2f}", SUBTEXT),
    ]

    card_w, card_h, gap = 2600000, 1300000, 180000
    x0, y0 = 457200, 1350000
    for i, (label, value, color) in enumerate(cards):
        col = i % 3
        row = i // 3
        x = x0 + col * (card_w + gap)
        y = y0 + row * (card_h + gap)
        add_rect(slide, x, y, card_w, card_h, CARD_BG)
        add_rect(slide, x, y, card_w, 40000, DARK)
        add_text(slide, x + 150000, y + 200000, card_w - 300000, card_h - 300000,
                  [[(label.upper(), 10, True, MUTED)],
                   [(value, 22, True, color)]])

    add_footer(slide, data.get("store_name", "Kirana Store"), report_date)
    return slide


def slide_top_items(prs, data):
    items = safe_list(data.get("top_items"))[:TOP_ITEMS_LIMIT]

    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_bg(slide, BG)

    add_text(slide, 229450, 164350, 8686800, 485400,
              [[("Top Performing Items", 22, True, DARK)]])
    add_text(slide, 229450, 600000, 8686800, 300000,
              [[("Products with the highest sales volume today", 11, False, SUBTEXT)]])

    col_item_x, col_qty_x = 228600, 6187200
    col_w = 2728200
    header_y = 1213525
    add_text(slide, col_item_x, header_y, col_w, 324300,
              [[("ITEM", 10, True, MUTED)]])
    add_text(slide, col_qty_x, header_y, col_w, 324300,
              [[("QUANTITY SOLD", 10, True, MUTED)]], align=PP_ALIGN.RIGHT)
    add_rect(slide, 228600, header_y + 324300, 8686800, 5700, DARK)

    row_h = 450000
    y = header_y + 324300 + 150000
    if not items:
        add_text(slide, col_item_x, y, 8686800, row_h,
                  [[("No sales recorded yet today.", 13, False, SUBTEXT)]])
    for item in items:
        name = safe_str(item.get("name"), "Unknown item")
        qty = safe_float(item.get("qty_sold", item.get("qty")))
        add_text(slide, col_item_x, y, 5500000, row_h,
                  [[(name, 14, True, DARK)]], anchor=MSO_ANCHOR.MIDDLE)
        add_text(slide, col_qty_x, y, col_w, row_h,
                  [[(f"{qty:g} sold", 14, False, SUBTEXT)]], anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.RIGHT)
        add_rect(slide, 228600, y + row_h - 20000, 8686800, 5700, ROW_LINE)
        y += row_h

    add_footer(slide, data.get("store_name", "Kirana Store"), data.get("report_date", ""))
    return slide


def _chunk(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def slide_inventory_alerts_page(prs, data, alerts_page, page_num, total_pages):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_bg(slide, BG)

    title = "Inventory Alerts"
    if total_pages > 1:
        title += f" ({page_num}/{total_pages})"
    add_text(slide, 229450, 164350, 8686800, 485400,
              [[(title, 22, True, DARK)]])
    add_text(slide, 229450, 600000, 8686800, 300000,
              [[("Items requiring immediate reorder attention", 11, False, SUBTEXT)]])

    if not alerts_page:
        add_text(slide, 229450, 1400000, 8000000, 500000,
                  [[("No inventory alerts — all stock levels healthy.", 13, False, SUBTEXT)]])
    else:
        y = 1300000
        row_h = 500000
        start_index = (page_num - 1) * ALERT_ROWS_PER_PAGE
        for offset, alert in enumerate(alerts_page, 1):
            i = start_index + offset
            add_text(slide, 228600, y, 500000, row_h,
                      [[(f"{i}.", 16, True, ACCENT)]], anchor=MSO_ANCHOR.MIDDLE)
            add_text(slide, 750000, y, 3800000, row_h,
                      [[(safe_str(alert.get("name"), "Unknown item"), 15, True, DARK)]], anchor=MSO_ANCHOR.MIDDLE)
            qty = safe_float(alert.get("qty", alert.get("quantity")))
            reorder = safe_float(alert.get("reorder_level"))
            add_text(slide, 4800000, y, 4100000, row_h,
                      [[(f"qty {qty:g}  (reorder at {reorder:g})", 12, False, SUBTEXT)]],
                      anchor=MSO_ANCHOR.MIDDLE, align=PP_ALIGN.RIGHT)
            add_rect(slide, 228600, y + row_h - 20000, 8686800, 5700, ROW_LINE)
            y += row_h

    add_footer(slide, data.get("store_name", "Kirana Store"), data.get("report_date", ""))
    return slide


def slides_inventory_alerts(prs, data):
    """Paginates unlimited alerts across as many slides as needed, ALERT_ROWS_PER_PAGE per slide."""
    alerts = safe_list(data.get("inventory_alerts"))
    pages = list(_chunk(alerts, ALERT_ROWS_PER_PAGE)) or [[]]
    total_pages = len(pages)
    for page_num, page_alerts in enumerate(pages, 1):
        slide_inventory_alerts_page(prs, data, page_alerts, page_num, total_pages)


def slide_closing(prs, data):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_bg(slide, BG)
    add_text(slide, 228600, 1909750, 6232800, 946200,
              [[("Thank You", 36, True, DARK)]])
    add_text(slide, 228600, 2856025, 5000000, 400000,
              [[("End of daily performance report.", 12, False, SUBTEXT)]])
    add_footer(slide, data.get("store_name", "Kirana Store"), data.get("report_date", ""))
    return slide


def build(data, output_path):
    data = data or {}
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    slide_cover(prs, data)
    slide_agenda(prs, data)
    slide_financial_summary(prs, data)
    slide_top_items(prs, data)
    slides_inventory_alerts(prs, data)  # 1+ slides, uncapped
    slide_closing(prs, data)

    prs.save(output_path)


SAMPLE_DATA = {
    "store_name": "Kirana Store",
    "period_label": "Q3 2026",
    "report_date": "2026-07-13",
    "financials": {
        "total_revenue": 2979.92,
        "sales_count": 8,
        "subtotal": 2666.00,
        "cgst": 156.96,
        "sgst": 156.96,
    },
    "top_items": [
        {"name": "Maggi 70g", "qty_sold": 78.0},
        {"name": "Amul Butter 100g", "qty_sold": 25.0},
        {"name": "Tata Salt 1kg", "qty_sold": 2.0},
    ],
    "inventory_alerts": [
        {"name": "Lays Chips", "qty": 0.0, "reorder_level": 5.0},
        {"name": "Y", "qty": 0.0, "reorder_level": 5.0},
        {"name": "Amul Butter 100g", "qty": 5.0, "reorder_level": 6.0},
    ],
}


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: generate_kirana_report.py <data.json|--sample> <output.pptx>")
        sys.exit(1)

    src, out_path = sys.argv[1], sys.argv[2]
    if src == "--sample":
        payload = SAMPLE_DATA
    else:
        with open(src) as f:
            payload = json.load(f)

    build(payload, out_path)
    print(f"Wrote {out_path}")
