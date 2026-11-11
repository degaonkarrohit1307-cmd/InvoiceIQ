
from app.services.field_extraction import extract_invoice_fields
from app.services.math_verification import CheckResult, run_math_verification


HALL_MARTINEZ_OCR = """
Invoice no: 33289699
Date of issue:
Seller:
Hall-Martinez
3197 Jillian Forks Suite 115
Kimchester, HI 43821
Tax Id: 941-80-9599
IBAN: GB32HGAS20083011657056
ITEMS
No. Description
1 Nintendo GameCube Console
DOL-001 Black w/ Controller,
Power, A/V,
SUMMARY
Total
12/24/2012
Client:
Haynes-Woods
USNV Gilbert
FPO AP 32645
Tax Id: 953-82-2864
Qty UM Net price Net worth VAT [%] Gross
worth
5,00 each 79,99 399,95 10% 439,95
VAT [%] Net worth VAT Gross worth
10% 399,95 40,00 439,95
$ 399,95 $ 40,00 $ 439,95
"""


def test_hall_martinez_extracts_invoice_information():
    fields = extract_invoice_fields(HALL_MARTINEZ_OCR)

    assert fields.invoice_number == "33289699"
    assert fields.invoice_date == "2012-12-24"
    assert fields.supplier_name == "Hall-Martinez"
    assert fields.buyer_name == "Haynes-Woods"
    assert fields.currency == "USD"


def test_hall_martinez_extracts_financial_totals():
    fields = extract_invoice_fields(HALL_MARTINEZ_OCR)

    assert fields.subtotal == 399.95
    assert fields.total_tax == 40.00
    assert fields.total_amount == 439.95


def test_hall_martinez_extracts_one_line_item():
    fields = extract_invoice_fields(HALL_MARTINEZ_OCR)

    assert len(fields.line_items) == 1

    item = fields.line_items[0]

    assert item.description == (
        "Nintendo GameCube Console "
        "DOL-001 Black w/ Controller, "
        "Power, A/V,"
    )
    assert item.quantity == 5.00
    assert item.unit_price == 79.99
    assert item.taxable_amount == 399.95
    assert item.tax_rate == 10.0
    assert item.line_total == 399.95


def test_hall_martinez_math_verification_passes():
    fields = extract_invoice_fields(HALL_MARTINEZ_OCR)

    result = run_math_verification(fields)

    assert result.overall == CheckResult.PASS


def test_hall_martinez_does_not_treat_vat_percentage_as_grand_total():
    fields = extract_invoice_fields(HALL_MARTINEZ_OCR)

    assert fields.total_amount == 439.95
    assert fields.total_amount != 10.00
