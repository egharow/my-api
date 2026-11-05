"""Leumi account movements downloaded as "Excel" from the bank's site ("בנק לאומי - אחזור דפי חשבון").

The file is really an HTML table saved with an .xls name. Columns: date, page, reference, description, debit, credit,
balance, account number. Rows come in the order the bank processed them (the running balance adds up in that order),
and the date is a value date, so the rows are kept in file order and the balance is what ties them together.
"""
import html
import re
from datetime import date

from .base import ParseError, ParsedFile, ParsedStatement, ParsedTxn, clean

HEADER = ("תאריך", "דף", "אסמכתא", "תיאור", "חובה", "זכות", "יתרה")


def is_html(path) -> bool:
    with open(path, "rb") as f:
        head = f.read(600).lstrip().lower()
    return head.startswith(b"<html") or head.startswith(b"<!doctype html") or b"<table" in head


def read_rows(path) -> list[list[str]]:
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1255"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", "replace")
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S | re.I):
        cells = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", c))).replace("‎", "").replace("‏", "").strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.S | re.I)]
        if any(cells):
            rows.append(cells)
    return rows


def looks_like(rows: list[list[str]]) -> bool:
    return any(tuple(r[:7]) == HEADER for r in rows[:30])


def _num(text: str) -> float | None:
    s = clean(text).replace("₪", "").replace(",", "").replace(" ", "")
    if not s:
        return None
    neg = s.startswith("-") or s.endswith("-") or (s.startswith("(") and s.endswith(")"))
    s = s.strip("-()")
    try:
        v = float(s)
    except ValueError:
        raise ParseError(f"bad number {text!r}")
    return -v if neg else v


def _day(text: str) -> date:
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})", clean(text))
    if not m:
        raise ParseError(f"bad date {text!r}")
    y = int(m.group(3))
    return date(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1)))


def parse_rows(rows: list[list[str]]) -> ParsedFile:
    head = next((i for i, r in enumerate(rows) if tuple(r[:7]) == HEADER), None)
    if head is None:
        raise ParseError("not a Leumi movements file")
    txns: list[ParsedTxn] = []
    accounts: set[str] = set()
    for r in rows[head + 1:]:
        if len(r) < 7 or not re.fullmatch(r"\d{1,2}/\d{1,2}/\d{2,4}", r[0]):
            continue
        debit, credit = _num(r[4]), _num(r[5])
        if debit is None and credit is None:
            continue
        amount = (credit or 0.0) - (debit or 0.0)
        txns.append(ParsedTxn(txn_date=_day(r[0]), description=clean(r[3]), amount=round(amount, 2), currency="ILS",
                              voucher=clean(r[2]) or None, balance_after=_num(r[6])))
        if len(r) > 7 and r[7]:
            accounts.add(re.sub(r"\D", "", r[7]))
    if not txns:
        raise ParseError("no movements found")
    if len(accounts) != 1:
        raise ParseError("the file does not name exactly one account")
    last4 = accounts.pop()[-4:]
    txns.reverse()                                   # newest first, as the other bank readers give them
    days = [t.txn_date for t in txns]
    st = ParsedStatement(issuer="leumi", account_kind="bank", last4=last4, product="checking", billing_date=None,
                         period_start=min(days), period_end=max(days), stated_total=None, currency="ILS", txns=txns, holder_text="")
    return ParsedFile("leumi", "bank_statement", [st])


def parse(path) -> ParsedFile:
    return parse_rows(read_rows(path))
