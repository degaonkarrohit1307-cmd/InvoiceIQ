"""Generates synthetic Phase 3 invoice fixtures. Run: ./venv/bin/python3 tests/fixtures/generate_fixtures.py"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

FIXTURES_DIR = Path(__file__).parent

FULL_INVOICE_LINES = [
    "INVOICE", "",
    "Invoice No: INV-2026-0001",
    "Invoice Date: 15/03/2026",
    "Due Date: 30/03/2026", "",
    "Supplier: Acme Trading Co",
    "Supplier GSTIN: 27AAAAA0000A1Z5", "",
    "Buyer: Widget Buyers Pvt Ltd",
    "Buyer GSTIN: 07BBBBB1111B2Z6", "",
    "Description          Quantity  Unit Price  Line Total",
    "Widget A              2         100.00      200.00",
    "Widget B              5         50.00       250.00",
    "Service Fee           1         75.00       75.00", "",
    "Subtotal: Rs.525.00",
    "CGST @9%: Rs.23.63",
    "SGST @9%: Rs.23.63",
    "IGST: Rs.0.00",
    "Total Tax: Rs.47.25",
    "Grand Total: Rs.572.25",
]
MISSING_FIELDS_INVOICE_LINES = [
    "INVOICE", "", "Some Company Name", "",
    "Description   Quantity  Unit Price  Line Total",
    "Mystery Item   1         10.00       10.00", "",
    "Thank you for your business.",
]

def _draw_text_pdf(path, lines):
    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    c.setFont("Helvetica", 11)
    y = height - 60
    for line in lines:
        c.drawString(60, y, line)
        y -= 16
    c.save()

def _draw_scanned_style_image(path, lines):
    img = Image.new("RGB", (900, 700), color="white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    y = 30
    for line in lines:
        draw.text((30, y), line, fill="black", font=font)
        y += 28
    img.save(path)

def _write_malformed_pdf(path):
    path.write_bytes(b"%PDF-1.4\n" + b"this is not a real pdf body, just garbage bytes" * 5)

def main():
    _draw_text_pdf(FIXTURES_DIR / "text_invoice_full.pdf", FULL_INVOICE_LINES)
    _draw_text_pdf(FIXTURES_DIR / "text_invoice_missing_fields.pdf", MISSING_FIELDS_INVOICE_LINES)
    _draw_scanned_style_image(FIXTURES_DIR / "scanned_invoice.png", FULL_INVOICE_LINES)
    _write_malformed_pdf(FIXTURES_DIR / "malformed_invoice.pdf")
    print("Fixtures generated.")

if __name__ == "__main__":
    main()
