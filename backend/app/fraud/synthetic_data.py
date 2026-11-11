"""
Synthetic invoice data generator (Phase 4B).

IMPORTANT: This data is entirely synthetic and generated for ML
development/training purposes only. It does NOT represent real-world
fraud statistics, real invoices, or real companies/individuals. Fraud
scenarios here are simplified, explainable heuristics chosen to give a
Phase 4C model something structured to learn from -- they are not a
claim about how real invoice fraud actually looks or how common it is.

Reproducibility: every generator instance uses its own `random.Random(seed)`
instance rather than the global `random` module, so generation is fully
deterministic for a given seed and doesn't interfere with (or get
interfered by) any other code using `random` elsewhere in the process.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field


class FraudType(str, enum.Enum):
    """
    Modular fraud/anomaly scenario types. Adding a new scenario later means
    adding a new enum value + a `_mutate_<name>` method on the generator --
    nothing else needs to change.
    """
    MATH_MISMATCH = "MATH_MISMATCH"                    # grand total doesn't match subtotal + tax - discount
    TAX_MISMATCH = "TAX_MISMATCH"                       # CGST+SGST (or IGST) doesn't match total_tax
    ABNORMAL_DISCOUNT = "ABNORMAL_DISCOUNT"              # discount exceeds or nearly exceeds subtotal
    DUPLICATE_INVOICE_NUMBER = "DUPLICATE_INVOICE_NUMBER"  # reuses an invoice number seen before
    UNUSUAL_AMOUNT = "UNUSUAL_AMOUNT"                    # grand total far outside the normal range
    LINE_ITEM_INCONSISTENCY = "LINE_ITEM_INCONSISTENCY"  # a line's quantity*unit_price != line_total


ALL_FRAUD_TYPES: tuple[FraudType, ...] = tuple(FraudType)


@dataclass
class SyntheticLineItem:
    description: str
    quantity: float | None
    unit_price: float | None
    line_total: float | None


@dataclass
class SyntheticInvoiceRecord:
    """
    Mirrors the attribute names `math_verification.run_math_verification`
    already expects (line_items, subtotal, cgst, sgst, igst, total_tax,
    total_amount) so it can be fed straight into the existing Phase 4A
    verification code without an adapter.
    """
    invoice_number: str
    invoice_date: str
    supplier_name: str
    supplier_gstin: str
    buyer_name: str
    buyer_gstin: str
    subtotal: float | None
    cgst: float | None
    sgst: float | None
    igst: float | None
    total_tax: float | None
    discount: float | None
    total_amount: float | None
    currency: str
    line_items: list[SyntheticLineItem] = field(default_factory=list)

    # --- labeling / provenance metadata (kept separate from ML features) ---
    label: str = "legitimate"  # "legitimate" | "fraud"
    fraud_type: str | None = None
    is_duplicate_of: str | None = None  # invoice_number this record duplicates, if any
    synthetic: bool = True


@dataclass
class GeneratorConfig:
    seed: int = 42
    tax_rate_choices: tuple[float, ...] = (0.05, 0.12, 0.18, 0.28)  # common GST slabs
    min_line_items: int = 1
    max_line_items: int = 5
    min_quantity: int = 1
    max_quantity: int = 20
    min_unit_price: float = 10.0
    max_unit_price: float = 2000.0
    legitimate_discount_probability: float = 0.3
    max_legitimate_discount_ratio: float = 0.15  # up to 15% of subtotal


class SyntheticInvoiceGenerator:
    def __init__(self, config: GeneratorConfig | None = None):
        self.config = config or GeneratorConfig()
        self._rng = __import__("random").Random(self.config.seed)
        self._invoice_counter = 0
        self._generated_invoice_numbers: list[str] = []

    def _next_invoice_number(self) -> str:
        self._invoice_counter += 1
        number = f"SYN-{self.config.seed}-{self._invoice_counter:05d}"
        self._generated_invoice_numbers.append(number)
        return number

    def _random_gstin(self) -> str:
        state_code = self._rng.randint(1, 37)
        letters = "".join(self._rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(5))
        pan_digits = self._rng.randint(1000, 9999)
        entity_letter = self._rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        check_char = self._rng.choice("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        return f"{state_code:02d}{letters}{pan_digits}{entity_letter}Z{check_char}"

    def _generate_consistent_line_items(self) -> list[SyntheticLineItem]:
        n_items = self._rng.randint(self.config.min_line_items, self.config.max_line_items)
        items = []
        for i in range(n_items):
            qty = self._rng.randint(self.config.min_quantity, self.config.max_quantity)
            price = round(self._rng.uniform(self.config.min_unit_price, self.config.max_unit_price), 2)
            line_total = round(qty * price, 2)
            items.append(SyntheticLineItem(f"Synthetic Item {i + 1}", float(qty), price, line_total))
        return items

    def generate_legitimate_invoice(self) -> SyntheticInvoiceRecord:
        line_items = self._generate_consistent_line_items()
        subtotal = round(sum(li.line_total for li in line_items), 2)
        tax_rate = self._rng.choice(self.config.tax_rate_choices)
        total_tax = round(subtotal * tax_rate, 2)

        intra_state = self._rng.random() < 0.6
        if intra_state:
            cgst = round(total_tax / 2, 2)
            sgst = round(total_tax - cgst, 2)  # avoids rounding drift vs total_tax
            igst = 0.0
        else:
            cgst, sgst, igst = 0.0, 0.0, total_tax

        discount = 0.0
        if self._rng.random() < self.config.legitimate_discount_probability:
            discount = round(subtotal * self._rng.uniform(0.01, self.config.max_legitimate_discount_ratio), 2)

        total_amount = round(subtotal + total_tax - discount, 2)

        return SyntheticInvoiceRecord(
            invoice_number=self._next_invoice_number(),
            invoice_date="2026-01-15",
            supplier_name=f"Synthetic Supplier {self._rng.randint(1, 500)}",
            supplier_gstin=self._random_gstin(),
            buyer_name=f"Synthetic Buyer {self._rng.randint(1, 500)}",
            buyer_gstin=self._random_gstin(),
            subtotal=subtotal, cgst=cgst, sgst=sgst, igst=igst,
            total_tax=total_tax, discount=discount, total_amount=total_amount,
            currency="INR", line_items=line_items,
            label="legitimate", fraud_type=None,
        )

    def generate_fraud_invoice(self, fraud_type: FraudType | None = None) -> SyntheticInvoiceRecord:
        chosen_type = fraud_type or self._rng.choice(ALL_FRAUD_TYPES)
        record = self.generate_legitimate_invoice()
        record.label = "fraud"
        record.fraud_type = chosen_type.value

        mutator = getattr(self, f"_mutate_{chosen_type.value.lower()}")
        mutator(record)
        return record

    # --- individual, independent mutation strategies (modular by design) ---

    def _mutate_math_mismatch(self, record: SyntheticInvoiceRecord) -> None:
        inflation = self._rng.uniform(1.3, 2.5)
        record.total_amount = round((record.total_amount or 0) * inflation, 2)

    def _mutate_tax_mismatch(self, record: SyntheticInvoiceRecord) -> None:
        if record.igst:
            record.igst = round(record.igst * self._rng.uniform(0.3, 0.7), 2)
        else:
            record.cgst = round((record.cgst or 0) * self._rng.uniform(1.5, 3.0), 2)

    def _mutate_abnormal_discount(self, record: SyntheticInvoiceRecord) -> None:
        subtotal = record.subtotal or 0
        record.discount = round(subtotal * self._rng.uniform(0.7, 1.3), 2)  # sometimes even exceeds subtotal
        record.total_amount = round(subtotal + (record.total_tax or 0) - record.discount, 2)

    def _mutate_duplicate_invoice_number(self, record: SyntheticInvoiceRecord) -> None:
        if len(self._generated_invoice_numbers) > 1:
            previous = self._rng.choice(self._generated_invoice_numbers[:-1])
            record.is_duplicate_of = previous
            record.invoice_number = previous
        # If no prior invoice exists yet (first record), the duplication
        # marker simply stays unset -- honest rather than fabricated.

    def _mutate_unusual_amount(self, record: SyntheticInvoiceRecord) -> None:
        direction = self._rng.choice(["high", "low"])
        if direction == "high":
            record.total_amount = round((record.total_amount or 1) * self._rng.uniform(20, 100), 2)
        else:
            record.total_amount = round((record.total_amount or 1) * self._rng.uniform(0.001, 0.05), 2)

    def _mutate_line_item_inconsistency(self, record: SyntheticInvoiceRecord) -> None:
        if not record.line_items:
            return
        item = self._rng.choice(record.line_items)
        item.line_total = round((item.line_total or 0) * self._rng.uniform(1.5, 3.0), 2)

    def generate_dataset(self, n_legitimate: int, n_fraud: int,
                          fraud_type_distribution: list[FraudType] | None = None) -> list[SyntheticInvoiceRecord]:
        """
        Deterministic for a fixed (seed, n_legitimate, n_fraud,
        fraud_type_distribution) -- generation always proceeds legitimate
        records first, then fraud records, so ordering is stable.
        """
        records = [self.generate_legitimate_invoice() for _ in range(n_legitimate)]

        for i in range(n_fraud):
            chosen_type = fraud_type_distribution[i % len(fraud_type_distribution)] if fraud_type_distribution else None
            records.append(self.generate_fraud_invoice(chosen_type))

        return records
