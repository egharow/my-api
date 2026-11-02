"""Cal (כאל) card statements exported as Excel: one sheet with the card in its first line and the charge date in the second."""
import re
from datetime import date, datetime

from .base import ParseError, ParsedFile, ParsedStatement, ParsedTxn, clean, parse_date, to_float


def read_rows(path) -> list[list]:
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[wb.sheetnames[0]]
    return [list(r) for r in ws.iter_rows(values_only=True)]


def looks_like(rows: list[list]) -> bool:
    head = " ".join(clean(c) for r in rows[:4] for c in r if c)
    return "פירוט עסקאות לחשבון" in head and "המסתיים ב" in head and "עסקאות לחיוב ב" in head


def _date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return parse_date(value)


def parse_rows(rows: list[list]) -> ParsedFile:
    title = clean(rows[0][0]) if rows and rows[0] else ""
    m = re.search(r"לכרטיס\s+(.+?)\s+המסתיים ב-?\s*(\d{4})", title)
    if not m:
        raise ParseError("no card number in the first line")
    product, last4 = m.group(1).strip(), m.group(2)
    billing = stated = None
    for r in rows[:5]:
        text = " ".join(clean(c) for c in r if c)
        b = re.search(r"עסקאות לחיוב ב-?\s*(\d{1,2}/\d{1,2}/\d{4})\s*:\s*([\d,]+\.?\d*)", text)
        if b:
            billing, stated = parse_date(b.group(1)), to_float(b.group(2))
    if billing is None:
        raise ParseError("could not read the charge date and total")
    header = next((i for i, r in enumerate(rows) if r and clean(r[0]).replace("\n", " ").startswith("תאריך עסקה")), None)
    if header is None:
        raise ParseError("transaction table not found")
    txns: list[ParsedTxn] = []
    seen: dict[tuple, int] = {}
    for r in rows[header + 1:]:
        cells = list(r) + [None] * 7
        if not isinstance(cells[0], (datetime, date)) and not re.fullmatch(r"\d{1,2}[./]\d{1,2}[./]\d{2,4}", clean(cells[0])):
            continue
        if cells[3] in (None, ""):                              # club points and similar: nothing was charged
            continue
        charge, orig = to_float(cells[3]), to_float(cells[2])
        day, name = _date(cells[0]), clean(cells[1])
        key = (day, name, round(charge, 2))
        seen[key] = seen.get(key, 0) + 1
        t = ParsedTxn(txn_date=day, description=name, amount=-charge, currency="ILS", orig_amount=-orig, orig_currency="ILS",
                      voucher=f"cal-{day.isoformat()}-{len(txns) + 1:03d}-{seen[key]}")
        sector = clean(cells[5])
        notes = clean(cells[6])
        t.detail = " · ".join(x for x in (f"ענף: {sector}" if sector else "", notes, clean(cells[4]) if clean(cells[4]) not in ("", "רגילה") else "") if x)
        mi = re.search(r"תשלום\s*(\d+)\s*מתוך\s*(\d+)", notes)
        if mi:
            t.instalment_no, t.instalment_total = int(mi.group(1)), int(mi.group(2))
        txns.append(t)
    if not txns:
        raise ParseError("no transactions found")
    st = ParsedStatement(issuer="cal", account_kind="card", last4=last4, product=product, billing_date=billing, period_start=None, period_end=None,
                         stated_total=stated, currency="ILS", txns=txns, holder_text="")
    return ParsedFile("cal", "card_export", [st])


def parse(path) -> ParsedFile:
    return parse_rows(read_rows(path))
