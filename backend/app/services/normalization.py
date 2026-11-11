"""Normalization helpers: dates, amounts (₹/Rs./INR), GSTIN, whitespace. Format-only, no business validation."""
import re
from datetime import date, datetime

_WHITESPACE_RE = re.compile(r"\s+")

_DATE_FORMATS = [
    "%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y",
    "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y",
    "%m/%d/%Y", "%m-%d-%Y",
    "%d-%b-%Y",
]

_AMOUNT_CLEAN_RE = re.compile(r"(?:Rs\.?|INR|USD|EUR|GBP)|[₹$€£,\s]", re.IGNORECASE)

GSTIN_PATTERN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}\b")


def normalize_whitespace(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _WHITESPACE_RE.sub(" ", value).strip()
    return cleaned or None


def normalize_gstin(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().upper().replace(" ", "")
    return cleaned or None


def is_valid_gstin_format(value: str | None) -> bool:
    """Structural/local format check ONLY. Does NOT verify the GSTIN is active or real."""
    if not value:
        return False
    return bool(GSTIN_PATTERN.fullmatch(value.strip().upper()))


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    cleaned = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(value: str | None) -> float | None:
    if not value:
        return None

    # Keep only digits, separators and signs.
    cleaned = "".join(
        char for char in value.strip()
        if char.isdigit() or char in ",.+-"
    )

    if not cleaned:
        return None

    sign = ""
    if cleaned.startswith(("-", "+")):
        sign = cleaned[0]
        cleaned = cleaned[1:]

    if not cleaned:
        return None

    # Both comma and dot: the last separator is the decimal separator.
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")

    # Comma only: determine whether it is decimal or thousands separator.
    elif "," in cleaned:
        parts = cleaned.split(",")

        if len(parts[-1]) in (1, 2):
            cleaned = "".join(parts[:-1]) + "." + parts[-1]
        else:
            cleaned = "".join(parts)

    # Dot only: handle multiple separators and thousands grouping.
    elif "." in cleaned:
        parts = cleaned.split(".")

        if len(parts) > 2:
            if len(parts[-1]) in (1, 2):
                cleaned = "".join(parts[:-1]) + "." + parts[-1]
            else:
                cleaned = "".join(parts)

        elif len(parts[-1]) == 3:
            cleaned = "".join(parts)

    try:
        return float(sign + cleaned)
    except ValueError:
        return None


def detect_currency(text: str) -> str | None:
    if "₹" in text or re.search(r"\bINR\b", text) or re.search(r"\bRs\.?\b", text):
        return "INR"
    if "$" in text or re.search(r"\bUSD\b", text):
        return "USD"
    if "€" in text or re.search(r"\bEUR\b", text):
        return "EUR"
    if "£" in text or re.search(r"\bGBP\b", text):
        return "GBP"
    return None
