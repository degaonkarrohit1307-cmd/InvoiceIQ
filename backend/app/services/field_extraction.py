"""Structured invoice field extraction via deterministic regex/heuristics (no LLM required)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.normalization import (
    GSTIN_PATTERN,
    detect_currency,
    normalize_gstin,
    normalize_whitespace,
    parse_amount,
    parse_date,
)

_INVOICE_NUMBER_RE = re.compile(
    r"(?:invoice\s*(?:no\.?|number|#)|bill\s*(?:no\.?|number))\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-\/]*)",
    re.IGNORECASE,
)
_DATE_TOKEN = (
    r"[0-9]{1,2}[\/\-\.][0-9]{1,2}[\/\-\.][0-9]{2,4}|[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"|[A-Za-z]+ [0-9]{1,2},? [0-9]{4}|[0-9]{1,2} [A-Za-z]+ [0-9]{4}"
    r"|[0-9]{1,2}-[A-Za-z]{3,9}-[0-9]{4}"
)
_INVOICE_DATE_RE = re.compile(
    rf"(?:invoice\s*date|date\s*of\s*(?:invoice|issue)|issue\s*date)"
    rf"\s*[:\-]?\s*({_DATE_TOKEN})",
    re.IGNORECASE,
)
_DUE_DATE_RE = re.compile(
    rf"(?:due\s*date)\s*[:\-]?\s*({_DATE_TOKEN})",
    re.IGNORECASE,
)

# Add this new regex
_ANY_DATE_RE = re.compile(
    rf"(?<![A-Za-z0-9])({_DATE_TOKEN})(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# Keep your existing supplier regex unchanged
_SUPPLIER_NAME_RE = re.compile(
    r"(?:supplier|vendor|seller|from)\s*[:\-]\s*(.+?)"
    r"(?=\s+(?:supplier\s+gstin|tax\s*id|gstin|vat(?:\s*(?:id|no\.?))?|"
    r"iban|swift|items?\b|invoice\s*(?:no\.?|number|date)|"
    r"date\s+of\s+(?:issue|invoice)|buyer|customer|bill\s*to|"
    r"subtotal|grand\s+total|total\s+amount|gross\s+worth)\b"
    r"|\s+\d{5}\b|$)",
    re.IGNORECASE,
)
_BUYER_NAME_RE = re.compile(
    r"\b(?:buyer|customer|client|bill\s*to|to)\b\s*[:\-]\s*(.+?)"
    r"(?=\s+(?:buyer\s+gstin|supplier\s+gstin|unit|tax\s*id|gstin|vat|iban|items?|"
    r"net\s*price|net\s*worth|gross\s*worth)\b|\s+\d{5}\b|$)",
    re.IGNORECASE,
)
_SUPPLIER_GSTIN_RE = re.compile(r"(?:supplier|vendor|seller)[^\n]{0,40}?GSTIN\s*[:\-]?\s*([0-9A-Z]{15})", re.IGNORECASE)
_BUYER_GSTIN_RE = re.compile(r"(?:buyer|customer)[^\n]{0,40}?GSTIN\s*[:\-]?\s*([0-9A-Z]{15})", re.IGNORECASE)

_AMOUNT_VALUE = (
    r"(?:[₹$€£]|Rs\.?|INR)?\s*[+-]?"
    r"(?:\d{1,3}(?:[ ,.\u00A0\u202F]\d{3})+|\d+)"
    r"(?:[.,]\d{1,2})?"
)
_SUBTOTAL_RE = re.compile(rf"(?:sub\s*total|taxable\s*(?:amount|value))\s*[:\-]?\s*({_AMOUNT_VALUE})", re.IGNORECASE)
_CGST_RE = re.compile(rf"CGST\s*(?:@\s*[\d.]+\s*%)?\s*[:\-]?\s*({_AMOUNT_VALUE})", re.IGNORECASE)
_SGST_RE = re.compile(rf"SGST\s*(?:@\s*[\d.]+\s*%)?\s*[:\-]?\s*({_AMOUNT_VALUE})", re.IGNORECASE)
_IGST_RE = re.compile(rf"IGST\s*(?:@\s*[\d.]+\s*%)?\s*[:\-]?\s*({_AMOUNT_VALUE})", re.IGNORECASE)
_TOTAL_TAX_RE = re.compile(rf"total\s*tax\s*[:\-]?\s*({_AMOUNT_VALUE})", re.IGNORECASE)
_NET_WORTH_VAT_RE = re.compile(
    rf"net\s*worth\s+vat(?:\s*\[\s*%\s*\])?\s*"
    rf"({_AMOUNT_VALUE})\s+({_AMOUNT_VALUE})",
    re.IGNORECASE,
)
_GRAND_TOTAL_RE = re.compile(
    rf"(?:grand\s*total(?:\s+amount)?|total\s*amount|total\s*invoice\s*value|"
    rf"amount\s*payable|gross\s*worth)\s*[:=\-]?\s*({_AMOUNT_VALUE})",
    re.IGNORECASE,
)
_LINE_ITEM_ROW_RE = re.compile(
    r"^(?P<description>.+?)\s{2,}(?P<quantity>\d+(?:\.\d+)?)\s{2,}"
    r"(?P<unit_price>[\d,]+\.?\d*)\s{2,}(?P<line_total>[\d,]+\.?\d*)\s*$"
)

_CRITICAL_FIELDS = ("invoice_number", "invoice_date", "supplier_name", "total_amount")


@dataclass
class ExtractedLineItem:
    description: str | None
    quantity: float | None
    unit_price: float | None
    taxable_amount: float | None
    tax_rate: float | None
    line_total: float | None


@dataclass
class ExtractedInvoiceFields:
    invoice_number: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    supplier_name: str | None = None
    supplier_gstin: str | None = None
    buyer_name: str | None = None
    buyer_gstin: str | None = None
    subtotal: float | None = None
    cgst: float | None = None
    sgst: float | None = None
    igst: float | None = None
    total_tax: float | None = None
    total_amount: float | None = None
    currency: str | None = None
    line_items: list[ExtractedLineItem] = field(default_factory=list)
    missing_critical_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _first_match(pattern: re.Pattern, text: str) -> str | None:
    m = pattern.search(text)
    return m.group(1).strip() if m else None

def _last_match(pattern: re.Pattern, text: str) -> str | None:
    matches = pattern.findall(text)
    return matches[-1].strip() if matches else None

def _extract_gstins(text: str) -> tuple[str | None, str | None]:
    supplier = _first_match(_SUPPLIER_GSTIN_RE, text)
    buyer = _first_match(_BUYER_GSTIN_RE, text)
    if supplier or buyer:
        return supplier, buyer
    all_gstins = GSTIN_PATTERN.findall(text)
    return (all_gstins[0] if all_gstins else None, all_gstins[1] if len(all_gstins) >= 2 else None)

def _extract_name_from_next_line(text: str, label_pattern: str) -> str | None:
    """Extract a supplier or buyer name when OCR places it below its label."""
    lines = [line.strip() for line in text.splitlines()]

    for index, line in enumerate(lines):
        if re.fullmatch(label_pattern, line, re.IGNORECASE):
            for next_line in lines[index + 1:]:
                if not next_line:
                    continue

                # Do not mistake another field label for a person's/company name.
                if re.match(
                    r"^(?:tax\s*id|gstin|iban|swift|items?|summary|"
                    r"invoice\s*(?:no\.?|number|date)|date\s+of\s+(?:issue|invoice)|"
                    r"buyer|customer|client|seller|supplier|vendor)\s*[:\-]?$",
                    next_line,
                    re.IGNORECASE,
                ):
                    break

                return normalize_whitespace(next_line)

    return None


def _extract_buyer_name_label(text: str) -> str | None:
    """Handle common 'Name: ...' buyer labels without consuming address/contact fields."""
    match = re.search(
        r"\bname\s*[:\-]\s*(.+?)"
        r"(?=\s+(?:address|mobile|phone|contact|gstin|tax\s*id)\s*[:\-]|\n|$)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    value = normalize_whitespace(match.group(1))
    return value or None


def _extract_supplier_heading(text: str) -> str | None:
    """Conservative fallback for a standalone supplier heading above its address."""
    lines = [normalize_whitespace(line) for line in text.splitlines()]
    lines = [line for line in lines if line]
    stop = next(
        (i for i, line in enumerate(lines)
         if re.search(r"\b(?:invoice\s*(?:no|number|date)|date\s+of\s+issue|name\s*:)\b",
                      line, re.IGNORECASE)),
        min(len(lines), 14),
    )
    excluded = {
        "TAX INVOICE", "INVOICE", "CASH MEMO", "CASH BILL",
        "RETAIL INVOICE", "PURCHASE INVOICE",
    }
    for index in range(1, min(stop, len(lines) - 1)):
        candidate = re.sub(r"^[\W_]+|[\W_]+$", "", lines[index]).strip()
        if not candidate or candidate.upper() in excluded:
            continue
        if re.search(r"[:@]|\d", candidate):
            continue
        words = candidate.split()
        if not (2 <= len(words) <= 6):
            continue
        if candidate.upper() != candidate:
            continue
        following = lines[index + 1]
        looks_like_address = (
            "," in following
            or re.search(r"\b(?:road|street|st\.|nagar|plaza|market|"
                         r"pune|mumbai|daund|pin\s*[-:]?\s*\d{5,6})\b",
                         following, re.IGNORECASE)
            or re.search(r"\b\d{5,6}\b", following)
        )
        if looks_like_address:
            return normalize_whitespace(candidate)
    return None


def _extract_summary_amounts(text: str) -> tuple[float | None, float | None, float | None]:
    """Extract subtotal, tax and gross total from a VAT summary row."""
    money_pattern = re.compile(
        r"(?<![\w%])(?:\d{1,3}(?:[ \u00A0\u202F]\d{3})+|\d+)[,.]\d{2}(?!\w)"
    )

    for line in reversed(text.splitlines()):
        stripped = line.strip()

        if not stripped:
            continue

        amounts = money_pattern.findall(stripped)

        if len(amounts) == 3 and "%" in stripped:
            return tuple(parse_amount(value) for value in amounts)

    return None, None, None

def _extract_line_items(text: str) -> list[ExtractedLineItem]:
    """Extract line items from common tabular OCR layouts without assuming one vendor format."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    items_start = next(
        (i for i, line in enumerate(lines)
         if re.fullmatch(r"ITEMS?", line, re.IGNORECASE)),
        None,
    )
    summary_start = next(
        (i for i, line in enumerate(lines)
         if re.match(r"^SUMMARY\b", line, re.IGNORECASE)),
        None,
    )

    # Generic table fallback: Description | Quantity | Unit Price | Line Total.
    if items_start is None or summary_start is None:
        header_index = next(
            (
                i for i, line in enumerate(lines)
                if re.search(r"\bdescription\b", line, re.IGNORECASE)
                and re.search(r"\bquantity\b", line, re.IGNORECASE)
                and re.search(r"\bunit\s+price\b", line, re.IGNORECASE)
                and re.search(r"\bline\s+total\b", line, re.IGNORECASE)
            ),
            None,
        )
        if header_index is None:
            return []

        result = []
        numeric_token = re.compile(r"^[+-]?\d[\d,.]*$")
        for line in lines[header_index + 1:]:
            if re.match(
                r"^(?:subtotal|grand\s+total|total\s+amount|total\s+tax)\b",
                line, re.IGNORECASE,
            ):
                break
            parts = line.rsplit(maxsplit=3)
            if len(parts) != 4:
                continue
            description, quantity_raw, unit_price_raw, line_total_raw = parts
            if not all(numeric_token.fullmatch(value) for value in (
                quantity_raw, unit_price_raw, line_total_raw
            )):
                continue
            quantity = parse_amount(quantity_raw)
            unit_price = parse_amount(unit_price_raw)
            line_total = parse_amount(line_total_raw)
            if quantity is None or unit_price is None or line_total is None:
                continue
            result.append(ExtractedLineItem(
                description=normalize_whitespace(description),
                quantity=quantity,
                unit_price=unit_price,
                taxable_amount=line_total,
                tax_rate=None,
                line_total=line_total,
            ))
        return result

    item_section = lines[items_start + 1:summary_start]
    quantity_each_re = re.compile(r"(?P<quantity>\d+[.,]\d{2})\s+each\b", re.IGNORECASE)
    quantity_end_re = re.compile(r"(?P<quantity>\d+[.,]\d{2})\s*$")
    numbered_re = re.compile(r"^\s*(?P<number>\d{1,3})[.):]?\s+(?P<body>.*)$")
    money_re = re.compile(
        r"(?<![\w])(?:\d{1,3}(?:[ \u00A0\u202F]\d{3})+|\d+)[,.]\d{2}(?!\w)"
    )

    records: list[dict] = []
    current: dict | None = None

    for line in item_section:
        numbered = numbered_re.match(line)
        # Some OCR engines turn the first numeric item marker into short
        # alphabetic noise. Accept it only when the same line has a quantity,
        # "each", and a full monetary row; ordinary description text is not enough.
        amounts_on_line = money_re.findall(line)
        ocr_marker = (
            not numbered
            and re.match(r"^[A-Za-z]{1,3}\s+", line) is not None
            and quantity_each_re.search(line) is not None
            and len(amounts_on_line) >= 4
        )

        if numbered or ocr_marker:
            if current is not None:
                records.append(current)

            body = numbered.group("body") if numbered else re.sub(
                r"^[A-Za-z]{1,3}\s+", "", line, count=1
            )
            quantity_match = quantity_each_re.search(body) or quantity_end_re.search(body)
            description = (
                body[:quantity_match.start()].strip()
                if quantity_match else body.strip()
            )
            quantity = (
                parse_amount(quantity_match.group("quantity"))
                if quantity_match else None
            )
            current = {"description": description, "quantity": quantity}
        elif current is not None:
            # Wrapped descriptions belong to the preceding item until the next
            # confidently detected item marker.
            current["description"] = normalize_whitespace(
                current["description"] + " " + line
            )

    if current is not None:
        records.append(current)

    # Some invoice formats put item descriptions and prices on the same row;
    # others put the numeric columns in a separate aligned OCR row.
    price_header = next(
        (
            i for i, line in enumerate(lines)
            if re.search(r"net\s+price\s+net\s+worth\s+vat", line, re.IGNORECASE)
        ),
        None,
    )
    price_rows: list[dict] = []
    if price_header is not None:
        vat_re = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
        for line in lines[price_header + 1:]:
            if re.match(r"^(?:summary|total\b)", line, re.IGNORECASE):
                break
            amounts = money_re.findall(line)
            rate_match = vat_re.search(line)
            if len(amounts) < 2 or rate_match is None:
                continue

            quantity_match = quantity_each_re.search(line)
            if quantity_match and len(amounts) >= 4:
                # quantity, unit price, taxable/net amount, gross amount
                unit_price_raw, taxable_raw = amounts[1], amounts[2]
            else:
                unit_price_raw, taxable_raw = amounts[0], amounts[1]

            price_rows.append({
                "quantity": (
                    parse_amount(quantity_match.group("quantity"))
                    if quantity_match else None
                ),
                "unit_price": parse_amount(unit_price_raw),
                "taxable_amount": parse_amount(taxable_raw),
                "tax_rate": parse_amount(rate_match.group(1)),
            })

    # When descriptions and numeric rows are in one section, item records and
    # price rows retain document order. Do not manufacture missing rows.
    result: list[ExtractedLineItem] = []
    for index, record in enumerate(records):
        price = price_rows[index] if index < len(price_rows) else {}
        taxable_amount = price.get("taxable_amount")
        result.append(ExtractedLineItem(
            description=normalize_whitespace(record["description"]) or None,
            quantity=(
                record["quantity"]
                if record["quantity"] is not None
                else price.get("quantity")
            ),
            unit_price=price.get("unit_price"),
            taxable_amount=taxable_amount,
            tax_rate=price.get("tax_rate"),
            line_total=taxable_amount,
        ))
    return result





def _structured_ocr_lines(ocr_pages) -> list[dict]:
    """Build text lines while preserving individual OCR word geometry."""
    structured_lines: list[dict] = []
    if not ocr_pages:
        return structured_lines

    for page_index, page in enumerate(ocr_pages):
        words = getattr(page, "words", None) or []
        if not words:
            continue

        grouped: dict[tuple, list] = {}

        for word in words:
            word_text = normalize_whitespace(getattr(word, "text", ""))
            if not word_text:
                continue

            block = getattr(word, "block_number", None)
            paragraph = getattr(word, "paragraph_number", None)
            line = getattr(word, "line_number", None)

            if block is not None and paragraph is not None and line is not None:
                key = (page_index, block, paragraph, line)
            else:
                top = int(getattr(word, "top", 0) or 0)
                key = (page_index, "y", round(top / 12))

            grouped.setdefault(key, []).append(word)

        for key, line_words in grouped.items():
            line_words.sort(
                key=lambda item: int(getattr(item, "left", 0) or 0)
            )

            text = normalize_whitespace(" ".join(
                str(getattr(item, "text", ""))
                for item in line_words
            ))

            if not text:
                continue

            confidences = [
                float(item.confidence)
                for item in line_words
                if getattr(item, "confidence", None) is not None
                and float(item.confidence) >= 0
            ]

            word_data = [
                {
                    "text": normalize_whitespace(getattr(item, "text", "")),
                    "left": int(getattr(item, "left", 0) or 0),
                    "top": int(getattr(item, "top", 0) or 0),
                    "width": int(getattr(item, "width", 0) or 0),
                    "height": int(getattr(item, "height", 0) or 0),
                    "confidence": (
                        float(item.confidence)
                        if getattr(item, "confidence", None) is not None
                        else None
                    ),
                }
                for item in line_words
            ]

            structured_lines.append({
                "page": page_index,
                "top": min(
                    int(getattr(item, "top", 0) or 0)
                    for item in line_words
                ),
                "text": text,
                "confidence": (
                    sum(confidences) / len(confidences)
                    if confidences else None
                ),
                "words": word_data,
            })

    return sorted(
        structured_lines,
        key=lambda item: (item["page"], item["top"])
    )

def _extract_structured_summary_amounts(lines: list[dict]) -> tuple[float | None, float | None]:
    """Read clearly labelled summary amounts from OCR lines; do not infer item columns."""
    money_re = re.compile(
        r"(?<![A-Za-z0-9])(?:₹|Rs\.?\s*)?([0-9]{1,3}(?:[, ][0-9]{3})*(?:\.[0-9]{1,2})|[0-9]+\.[0-9]{1,2})(?![A-Za-z0-9])"
    )
    subtotal = total_tax = None
    for line in lines:
        text = line["text"]
        # Require a clear taxable/subtotal label. Repeated OCR words such as
        # "Taxable Amount Amount" are tolerated, but amounts are not guessed.
        if re.search(r"\b(?:taxable\s+(?:amount|value)|sub\s*total)\b", text, re.I):
            values = money_re.findall(text)
            if values:
                subtotal = parse_amount(values[-1])
        if re.search(r"\btotal\s+(?:gst|tax)\b", text, re.I):
            values = money_re.findall(text)
            if values:
                total_tax = parse_amount(values[-1])
    return subtotal, total_tax


def _extract_structured_supplier_heading(lines: list[dict]) -> str | None:
    """Use the first OCR heading only when it is a plausible business name."""
    for line in lines[:5]:
        candidate = line["text"]
        candidate = re.sub(r"\b(?:TAX\s+INVOICE|INVOICE|CASH\s+MEMO|CASH\s+BILL)\b", " ", candidate, flags=re.I)
        candidate = normalize_whitespace(candidate).strip(" -:|_")
        if not candidate or re.search(r"\d|@|:|\\", candidate):
            continue
        words = candidate.split()
        if 2 <= len(words) <= 6 and candidate.upper() == candidate:
            return candidate
    return None

def extract_invoice_fields(raw_text: str, ocr_pages=None) -> ExtractedInvoiceFields:
    if not raw_text or not raw_text.strip():
        result = ExtractedInvoiceFields()
        result.missing_critical_fields = list(_CRITICAL_FIELDS)
        result.warnings.append(
            "No text was available to extract fields from."
        )
        return result

    # Structured OCR is optional. Existing text-only callers retain their behaviour.
    structured_lines = _structured_ocr_lines(ocr_pages)
    structured_subtotal, structured_total_tax = _extract_structured_summary_amounts(structured_lines)

    # Extract labelled dates first.
    invoice_date_raw = _first_match(_INVOICE_DATE_RE, raw_text)
    due_date_raw = _first_match(_DUE_DATE_RE, raw_text)

    # Fallback for an unlabelled date, only when exactly
    # one distinct valid date exists in the OCR text.
    if not invoice_date_raw and not due_date_raw:
        date_candidates = []

        for match in _ANY_DATE_RE.finditer(raw_text):
            candidate = match.group(1).strip()
            parsed = parse_date(candidate)

            if parsed:
                date_candidates.append((candidate, parsed))

        unique_dates = {
            parsed.isoformat()
            for _, parsed in date_candidates
        }

        if len(unique_dates) == 1:
            invoice_date_raw = date_candidates[0][0]

    invoice_date = parse_date(invoice_date_raw)
    due_date = parse_date(due_date_raw)

    # Extract GSTIN values.
    supplier_gstin, buyer_gstin = _extract_gstins(raw_text)

    # Extract subtotal and VAT from the summary section.
    net_worth_vat_matches = list(
        _NET_WORTH_VAT_RE.finditer(raw_text)
    )

    summary_subtotal, summary_tax, summary_total = (
        _extract_summary_amounts(raw_text)
    )

    if net_worth_vat_matches:
        summary_match = net_worth_vat_matches[-1]
        subtotal_raw = summary_match.group(1)
        total_tax_raw = summary_match.group(2)
    else:
        subtotal_raw = _first_match(_SUBTOTAL_RE, raw_text)
        total_tax_raw = _first_match(_TOTAL_TAX_RE, raw_text)

    if subtotal_raw is None and summary_subtotal is not None:
        subtotal_raw = str(summary_subtotal)

    if total_tax_raw is None and summary_tax is not None:
        total_tax_raw = str(summary_tax)

    if subtotal_raw is None and structured_subtotal is not None:
        subtotal_raw = str(structured_subtotal)
    if total_tax_raw is None and structured_total_tax is not None:
        total_tax_raw = str(structured_total_tax)

    # A side-by-side "Seller: Client:" heading followed by flattened names
    # does not provide a trustworthy boundary between the two parties.
    side_by_side_party_labels = re.search(
        r"\bseller\s*:\s*client\s*:", raw_text, re.IGNORECASE
    ) is not None

    supplier_name_raw = _first_match(_SUPPLIER_NAME_RE, raw_text)
    if not supplier_name_raw:
        supplier_name_raw = _extract_name_from_next_line(
            raw_text, r"seller\s*:"
        )
    if not supplier_name_raw:
        supplier_name_raw = _extract_supplier_heading(raw_text)
    if not supplier_name_raw and structured_lines:
        supplier_name_raw = _extract_structured_supplier_heading(structured_lines)

    buyer_name_raw = _first_match(_BUYER_NAME_RE, raw_text)
    if not buyer_name_raw:
        buyer_name_raw = _extract_buyer_name_label(raw_text)
    if not buyer_name_raw:
        buyer_name_raw = _extract_name_from_next_line(
            raw_text, r"client\s*:"
        )

    if side_by_side_party_labels:
        # Flattened OCR may concatenate both names without a separator.
        # Discard captures that cannot be assigned to a party with confidence.
        supplier_name_raw = None
        buyer_name_raw = None

    grand_total_raw = (
        str(summary_total)
        if summary_total is not None
        else _last_match(_GRAND_TOTAL_RE, raw_text)
    )

    # Construct the extracted invoice result.
    result = ExtractedInvoiceFields(
        invoice_number=normalize_whitespace(
            _first_match(_INVOICE_NUMBER_RE, raw_text)
        ),
        invoice_date=invoice_date.isoformat() if invoice_date else None,
        due_date=due_date.isoformat() if due_date else None,
        supplier_name=normalize_whitespace(supplier_name_raw),
        supplier_gstin=normalize_gstin(supplier_gstin),
        buyer_name=normalize_whitespace(buyer_name_raw),
        buyer_gstin=normalize_gstin(buyer_gstin),
        subtotal=parse_amount(subtotal_raw),
        cgst=parse_amount(_first_match(_CGST_RE, raw_text)),
        sgst=parse_amount(_first_match(_SGST_RE, raw_text)),
        igst=parse_amount(_first_match(_IGST_RE, raw_text)),
        total_tax=parse_amount(total_tax_raw),
        total_amount=parse_amount(grand_total_raw),
        currency=detect_currency(raw_text),
        line_items=_extract_line_items(raw_text),
    )

    if structured_lines and not result.line_items:
        result.warnings.append(
            "Structured OCR word positions were available, but the line-item columns could not be assigned reliably; no line items were inferred."
        )

    if invoice_date_raw and not invoice_date:
        result.warnings.append(
            f"Found an invoice date value ('{invoice_date_raw}') "
            "but could not parse its format."
        )

    if due_date_raw and not due_date:
        result.warnings.append(
            f"Found a due date value ('{due_date_raw}') "
            "but could not parse its format."
        )

    if side_by_side_party_labels and not result.supplier_name and not result.buyer_name:
        result.warnings.append(
            "Seller and client names could not be separated reliably from the OCR text."
        )

    field_map = {
        "invoice_number": result.invoice_number,
        "invoice_date": result.invoice_date,
        "supplier_name": result.supplier_name,
        "total_amount": result.total_amount,
    }

    result.missing_critical_fields = [
        name for name, value in field_map.items()
        if value is None
    ]

    return result
