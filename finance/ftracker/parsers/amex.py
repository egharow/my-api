import re

from .base import (ParseError, ParsedFile, ParsedStatement, ParsedTxn, apply_detail, clean,
                   currency_code, parse_date, to_float)

_DATE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}$")


def read_rows(path) -> list[list]:
    import xlrd
    ws = xlrd.open_workbook(path).sheet_by_index(0)
    return [ws.row_values(i) for i in range(ws.nrows)]


def looks_like(rows: list[list]) -> bool:
    head = " ".join(clean(c) for r in rows[:10] for c in r if c)
    return "אמריקן אקספרס" in head and "מועד חיוב" in head


def parse_rows(rows: list[list]) -> ParsedFile:
    statements: list[ParsedStatement] = []
    holder = clean(rows[1][0]) if len(rows) > 1 else ""
    current: ParsedStatement | None = None

    for r in rows:
        cells = list(r) + [None] * 8
        first = clean(cells[0])
        if "אמריקן אקספרס" in first and clean(cells[1]) == "מועד חיוב":
            m = re.search(r"-\s*(\d{4})", first)
            if not m:
                raise ParseError(f"no card number in {first!r}")
            current = ParsedStatement(
                issuer="amex", account_kind="card", last4=m.group(1),
                product=first.split("-")[0].strip(), billing_date=parse_date(cells[2]),
                period_start=None, period_end=None, stated_total=None, currency="ILS",
                holder_text=holder,
            )
            statements.append(current)
        elif current and clean(cells[1]).startswith("סך חיוב"):
            current.stated_total = to_float(cells[4])
        elif current and _DATE.match(first):
            charge_cur = currency_code(cells[5])
            orig_cur = currency_code(cells[3])
            t = ParsedTxn(
                txn_date=parse_date(first), description=clean(cells[1]),
                amount=-to_float(cells[4]), currency=charge_cur,
                orig_amount=-to_float(cells[2]), orig_currency=orig_cur,
                voucher=clean(cells[6]) or None,
            )
            apply_detail(t, cells[7])
            current.txns.append(t)
    if not statements:
        raise ParseError("not an Amex export")
    for st in statements:
        if st.stated_total is None:
            raise ParseError(f"total missing for card {st.last4}")
    return ParsedFile("amex", "card_export", statements)


def parse(path) -> ParsedFile:
    return parse_rows(read_rows(path))
