"""One Zero (וואן זירו) bank account movements exported as an Excel file."""
import re
from datetime import date, timedelta

from .base import ParseError, ParsedFile, ParsedStatement, ParsedTxn, clean

HEADER = ["תאריך תנועה", "תאריך ערך", "סוג פעולה", "תיאור", "סכום פעולה"]
_HEBREW = re.compile("[א-ת]")
_LATIN = re.compile(r"[A-Za-z0-9_.\-]+")


def read_rows(path) -> list[list]:
    import xlrd
    ws = xlrd.open_workbook(path).sheet_by_index(0)
    return [ws.row_values(i) for i in range(ws.nrows)]


def looks_like(rows: list[list]) -> bool:
    return bool(rows) and [clean(c) for c in rows[0][:5]] == HEADER


def _segment(seg: str) -> str:
    if not _HEBREW.search(seg):
        return seg
    rev = seg[::-1]                                              # Hebrew is stored back to front
    return _LATIN.sub(lambda m: m.group(0)[::-1], rev)           # numbers and Latin words read forwards again


def fix_text(text: str) -> str:
    """The description as it reads: Hebrew stored back to front, and the '/'-separated parts in reverse order."""
    raw = str(text).replace("\r", " ")
    if not raw.lstrip().startswith("\u202d"):                    # only descriptions that start with the override mark are stored back to front
        return clean(raw)
    parts = [_segment(clean(p)) for p in raw.split("/")]
    return "/".join(p for p in reversed(parts) if p).strip()


def _day(value) -> date:
    if isinstance(value, (int, float)):
        return date(1899, 12, 30) + timedelta(days=int(value))
    m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{2,4})", clean(value))
    if not m:
        raise ParseError(f"bad date {value!r}")
    y = int(m.group(3))
    return date(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1)))


def _chain_order(txns: list[ParsedTxn]) -> list[ParsedTxn]:
    """Newest first, and within one day in the order that makes the running balance add up (the file does not say)."""
    from itertools import permutations, groupby
    out: list[ParsedTxn] = []
    for _day_, grp in groupby(txns, key=lambda t: t.txn_date):
        rows = list(grp)
        if len(rows) < 2 or len(rows) > 6 or any(t.balance_after is None for t in rows):
            out += rows
            continue

        def fits(order):
            return sum(1 for a, b in zip(order, order[1:]) if abs(a.balance_after - (b.balance_after + a.amount)) < 0.005)
        out += list(max(permutations(rows), key=fits))
    return out


def parse_rows(rows: list[list]) -> ParsedFile:
    if not looks_like(rows):
        raise ParseError("not a One Zero movements file")
    txns: list[ParsedTxn] = []
    for r in rows[1:]:
        cells = list(r) + [None] * 9
        if cells[0] in (None, ""):
            continue
        amount = float(cells[4])
        if clean(cells[6]) == "חיוב" and amount > 0:
            amount = -amount
        kind = clean(cells[2])
        desc = fix_text(cells[3])
        t = ParsedTxn(txn_date=_day(cells[0]), description=f"{kind} {desc}".strip(), amount=amount, currency="ILS",
                      balance_after=float(cells[7]) if cells[7] not in (None, "") else None, voucher=clean(cells[8]) or None)
        txns.append(t)
    if not txns:
        raise ParseError("no movements found")
    txns = _chain_order(txns)
    days = [t.txn_date for t in txns]
    st = ParsedStatement(issuer="onezero", account_kind="bank", last4="", product="One Zero", billing_date=None,
                         period_start=min(days), period_end=max(days), stated_total=None, currency="ILS", txns=txns, holder_text="")
    return ParsedFile("onezero", "bank_statement", [st])


def parse(path) -> ParsedFile:
    return parse_rows(read_rows(path))
