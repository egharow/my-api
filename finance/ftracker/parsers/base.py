import re
from dataclasses import dataclass, field
from datetime import date

# Bidirectional marks and other invisible characters that statements sprinkle around text.
_INVISIBLE = re.compile("[‎‏‪-‮⁦-⁩﻿]")

CURRENCY = {"₪": "ILS", "ש\"ח": "ILS", "$": "USD", "€": "EUR", "£": "GBP",
            "ILS": "ILS", "USD": "USD", "EUR": "EUR", "GBP": "GBP"}


class ParseError(Exception):
    pass


def clean(text) -> str:
    if text is None:
        return ""
    return _INVISIBLE.sub("", str(text)).strip()


def currency_code(symbol) -> str:
    s = clean(symbol)
    if s not in CURRENCY:
        raise ParseError(f"unknown currency {s!r}")
    return CURRENCY[s]


def to_float(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    s = clean(value).replace(",", "").replace("₪", "").replace("$", "").strip()
    if not s:
        raise ParseError("empty amount")
    return float(s)


def parse_date(text) -> date:
    """dd.mm.yy, dd.mm.yyyy, dd/mm/yy or dd/mm/yyyy."""
    s = clean(text)
    m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{2}|\d{4})", s)
    if not m:
        raise ParseError(f"bad date {s!r}")
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000
    return date(y, mo, d)


@dataclass
class ParsedTxn:
    txn_date: date
    description: str
    amount: float              # signed cash flow, charge currency (out is negative)
    currency: str
    orig_amount: float | None = None
    orig_currency: str | None = None
    voucher: str | None = None
    detail: str = ""
    balance_after: float | None = None
    is_recurring: bool = False
    instalment_no: int | None = None
    instalment_total: int | None = None
    instalment_full: float | None = None


@dataclass
class ParsedStatement:
    issuer: str                # isracard | amex | leumi
    account_kind: str          # card | bank
    last4: str
    product: str
    billing_date: date | None
    period_start: date | None
    period_end: date | None
    stated_total: float | None
    currency: str
    txns: list[ParsedTxn] = field(default_factory=list)
    holder_text: str = ""      # used once to match an owner alias, never stored


@dataclass
class ParsedFile:
    issuer: str
    file_kind: str             # card_export | bank_statement
    statements: list[ParsedStatement]

    @property
    def period(self) -> tuple[date | None, date | None]:
        starts = [s.period_start or s.billing_date for s in self.statements]
        ends = [s.period_end or s.billing_date for s in self.statements]
        starts = [d for d in starts if d]
        ends = [d for d in ends if d]
        return (min(starts) if starts else None, max(ends) if ends else None)


_INSTALMENT = re.compile(r"תשלום\s+(\d+)\s+מתוך\s+(\d+)")


def apply_detail(txn: ParsedTxn, detail: str) -> None:
    """Pull instalment and standing-order markers out of the free-text detail column."""
    txn.detail = clean(detail).replace("\n", " ")
    m = _INSTALMENT.search(txn.detail)
    if m:
        txn.instalment_no, txn.instalment_total = int(m.group(1)), int(m.group(2))
        if txn.orig_amount is not None:
            txn.instalment_full = abs(txn.orig_amount)
    if "הוראת קבע" in txn.detail:
        txn.is_recurring = True
