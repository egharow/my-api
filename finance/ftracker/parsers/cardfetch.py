"""Reads what "Fetch cards" brought back from a card company's site (a .json file the app writes into the inbox).

One statement per card and billing date, like a downloaded export. Pending charges (not yet final) are left out:
they come in on a later fetch once they are final.
"""
import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

from .base import CURRENCY, ParseError, ParsedFile, ParsedStatement, ParsedTxn, clean

FORMAT = "ftracker-card-fetch"
MAX_BYTES = 30_000_000
ISSUERS = {"isracard": "isracard", "amex": "amex", "visaCal": "cal", "max": "max"}
NAMES = {"isracard": "Isracard", "amex": "American Express", "visaCal": "Cal", "max": "Max"}


def read(path: Path) -> dict | None:
    if path.suffix.lower() != ".json" or path.stat().st_size > MAX_BYTES:
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("format") == FORMAT else None


def _currency(value, default: str = "ILS") -> str:
    s = clean(value)
    if not s:
        return default
    if s in CURRENCY:
        return CURRENCY[s]
    if re.fullmatch(r"[A-Z]{3}", s):
        return s
    return default


def _day(text) -> date | None:
    try:
        return date.fromisoformat(str(text)[:10]) if text else None
    except ValueError:
        return None


def parse(d: dict) -> ParsedFile:
    company = d.get("company")
    if company not in ISSUERS:
        raise ParseError(f"unknown card company {company!r}")
    issuer = ISSUERS[company]
    statements = []
    for acct in d.get("accounts") or []:
        digits = re.sub(r"\D", "", str(acct.get("accountNumber") or ""))
        last4 = digits[-4:]
        if not last4:
            continue
        groups: dict[date, list[ParsedTxn]] = defaultdict(list)
        for t in acct.get("txns") or []:
            if (t.get("status") or "completed") != "completed" or t.get("chargedAmount") is None:
                continue
            when = _day(t.get("date"))
            billed = _day(t.get("processedDate")) or when
            if not when:
                continue
            charged_cur = _currency(t.get("chargedCurrency"))
            orig_cur = _currency(t.get("originalCurrency"), charged_cur)
            amount = round(float(t["chargedAmount"]), 2)
            orig = t.get("originalAmount")
            inst = t.get("installments") or {}
            txn = ParsedTxn(
                txn_date=when, description=clean(t.get("description")) or "(no description)", amount=amount,
                currency=charged_cur,
                orig_amount=round(float(orig), 2) if orig is not None and orig_cur != charged_cur else None,
                orig_currency=orig_cur if orig is not None and orig_cur != charged_cur else None,
                voucher=clean(t.get("identifier")) or None,
                detail=clean(t.get("memo")).replace("\n", " "),
            )
            if inst.get("total"):
                txn.instalment_no, txn.instalment_total = int(inst.get("number") or 0) or None, int(inst["total"])
                if orig is not None:
                    txn.instalment_full = abs(float(orig))
                if txn.instalment_no:
                    txn.detail = (txn.detail + f" תשלום {txn.instalment_no} מתוך {txn.instalment_total}").strip()
            groups[billed].append(txn)
        for billed, txns in sorted(groups.items()):
            statements.append(ParsedStatement(
                issuer=issuer, account_kind="card", last4=last4, product=NAMES[company], billing_date=billed,
                period_start=None, period_end=None, stated_total=None, currency="ILS", txns=txns))
    if not statements:
        raise ParseError("the fetch brought back no final charges")
    return ParsedFile(issuer=issuer, file_kind="card_fetch", statements=statements)
