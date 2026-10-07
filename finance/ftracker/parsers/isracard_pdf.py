"""Isracard card statement as a PDF ("פירוט פעולותיך לתאריך ..."), the file you download from the Isracard app.

The PDF stores Hebrew words back to front and the table columns sit at fixed horizontal positions, so rows are
read by position: the date column on the right, then the merchant, then the amount columns going left.
Two kinds of rows exist: shekel purchases (charge column near x=262) and foreign-currency purchases
(charge column near x=181, with the foreign amount, rate and so on between).
"""
import re
from datetime import date

from .base import ParseError, ParsedFile, ParsedStatement, ParsedTxn, apply_detail, clean, parse_date

_HEBREW = re.compile(r"[א-ת]")
_DATE = re.compile(r"^\d{2}/\d{2}/\d{2}$")
_NUM = re.compile(r"^-?\d{1,3}(?:,\d{3})*\.\d{2}$|^-?\d+\.\d{2}$")
_LAST4 = re.compile(r"^\*(\d{4})\*$")

DATE_X = (500, 535)             # right edge of the transaction date
CHARGE_ILS_X = (250, 275)       # shekel table: amount charged
ORIG_ILS_X = (292, 318)         # shekel table: transaction amount
CHARGE_FX_X = (170, 195)        # foreign table: amount charged in shekels
MERCHANT_MIN_X = 362            # shekel table: words right of this belong to the merchant, the rest is the sector
FX_MERCHANT_MIN_X = 380
DETAIL_MAX_X = 245              # shekel table: free text such as "תשלום 2 מתוך 6" or "הוראת קבע"


def fix_word(text: str) -> str:
    text = clean(text)
    return text[::-1] if _HEBREW.search(text) else text


def read_pages(path) -> list[list[dict]]:
    import pdfplumber
    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            pages.append([{"text": w["text"], "x0": w["x0"], "x1": w["x1"], "top": w["top"]}
                          for w in page.extract_words(x_tolerance=1.5)])
    return pages


def looks_like(pages: list[list[dict]]) -> bool:
    if not pages:
        return False
    words = [fix_word(w["text"]) for w in pages[0]]
    return any(_LAST4.match(w) for w in words) and any("פעולותיך" in w for w in words) and any("לתאריך" in w for w in words)


def _num(text: str) -> float:
    return float(text.replace(",", ""))


def _in(x: float, band: tuple[int, int]) -> bool:
    return band[0] <= x <= band[1]


def _lines(words: list[dict]) -> list[list[dict]]:
    """Words grouped into text lines (by height), each line ordered right to left as Hebrew is read."""
    out: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["top"]), -w["x1"])):
        if out and abs(out[-1][0]["top"] - w["top"]) <= 2.5:
            out[-1].append(w)
        else:
            out.append([w])
    return [sorted(line, key=lambda w: -w["x1"]) for line in out]


def _is_total_line(line: list[dict]) -> bool:
    texts = [fix_word(w["text"]) for w in line]
    return any(t == 'סה"כ' for t in texts[:2]) and any("חיוב" in t for t in texts)


def parse_pages(pages: list[list[dict]]) -> ParsedFile:
    first = pages[0]
    last4 = next((m.group(1) for w in first if (m := _LAST4.match(fix_word(w["text"])))), None)
    if not last4:
        raise ParseError("could not find the card number")
    billing = None
    holder: list[str] = []
    lines0 = _lines(first)
    for i, line in enumerate(lines0):
        texts = [fix_word(w["text"]) for w in line]
        if any("פעולותיך" in t for t in texts) and billing is None:
            for t in texts:
                if _DATE.match(t):
                    billing = parse_date(t)
        if texts and texts[0] == "לכבוד":
            for nxt in lines0[i + 1:i + 3]:
                holder += [fix_word(w["text"]) for w in nxt if w["x1"] > 400]
    if billing is None:
        raise ParseError("could not read the billing date")

    txns: list[ParsedTxn] = []
    totals: list[float] = []
    seen: dict[tuple, int] = {}
    for words in pages:
        lines = _lines(words)
        # a transaction starts at a line whose rightmost word is a date in the date column
        starts = [i for i, l in enumerate(lines) if _DATE.match(fix_word(l[0]["text"])) and _in(l[0]["x1"], DATE_X)]
        stops = [i for i, l in enumerate(lines) if _is_total_line(l)]
        for n, s in enumerate(starts):
            end = min([x for x in starts[n + 1:] + stops if x > s], default=len(lines))
            top = lines[s][0]["top"]
            band = [w for l in lines[s:end] if l[0]["top"] - top <= 14 for w in l]   # the row and a wrapped second line
            t = _row(lines[s][0], band, seen)
            if t:
                txns.append(t)
        for i in stops:
            nums = [fix_word(w["text"]) for w in lines[i] if _NUM.match(fix_word(w["text"]))]
            if nums:
                totals.append(_num(nums[-1]))
    if not totals:
        raise ParseError("statement total row not found")
    st = ParsedStatement(issuer="isracard", account_kind="card", last4=last4, product="Isracard", billing_date=billing,
                         period_start=None, period_end=None, stated_total=round(sum(totals), 2), currency="ILS",
                         txns=txns, holder_text=" ".join(holder))
    return ParsedFile("isracard", "card_export", [st])


def _row(date_word: dict, band: list[dict], seen: dict) -> ParsedTxn | None:
    day = parse_date(fix_word(date_word["text"]))
    rest = sorted(((fix_word(w["text"]), w["x1"]) for w in band if w is not date_word), key=lambda t: -t[1])
    charge_ils = next((_num(t) for t, x in rest if _NUM.match(t) and _in(x, CHARGE_ILS_X)), None)
    charge_fx = next((_num(t) for t, x in rest if _NUM.match(t) and _in(x, CHARGE_FX_X)), None)
    if charge_fx is not None and charge_ils is None:                       # foreign-currency table
        nums = [(t, x) for t, x in rest if _NUM.match(t)]
        orig = next((_num(t) for t, x in nums if 355 <= x <= 380 and x > 340), None)
        if orig is None:
            orig = next((_num(t) for t, x in nums if 320 <= x <= 380), charge_fx)
        label = next((t for t, x in rest if 372 <= x <= 380 and not _NUM.match(t)), "")
        cur = {"$": "USD", "₪": "ILS", "€": "EUR", "£": "GBP"}.get(label, label if re.fullmatch(r"[A-Z]{3}", label or "") else "ILS")
        rate = next((t for t, x in rest if re.fullmatch(r"\d\.\d{4}", t)), None)
        merchant = " ".join(t for t, x in rest if x >= FX_MERCHANT_MIN_X and not _NUM.match(t) and t not in ("$", "₪") and not _DATE.match(t))
        orig_cur = cur if label not in ("", "₪") else "ILS"
        detail = f"{orig_cur} {orig:,.2f}" + (f" at {rate}" if rate and orig_cur != "ILS" else "")
        t = ParsedTxn(txn_date=day, description=merchant, amount=-charge_fx, currency="ILS",
                      orig_amount=-orig, orig_currency=orig_cur)
        apply_detail(t, detail)
    elif charge_ils is not None:                                           # shekel table
        orig = next((_num(t) for t, x in rest if _NUM.match(t) and _in(x, ORIG_ILS_X)), charge_ils)
        merchant = " ".join(t for t, x in rest if x >= MERCHANT_MIN_X and not _NUM.match(t))
        detail = " ".join(t for t, x in rest if x <= DETAIL_MAX_X and not _NUM.match(t))
        t = ParsedTxn(txn_date=day, description=merchant, amount=-charge_ils, currency="ILS",
                      orig_amount=-orig, orig_currency="ILS")
        apply_detail(t, detail)
    else:
        return None
    t.description = clean(t.description) or "(no name)"
    key = (t.txn_date, t.description, round(t.amount, 2))
    seen[key] = seen.get(key, 0) + 1
    t.voucher = f"pdf-{seen[key]}"         # this PDF has no voucher number: the n-th identical line of the day keeps its identity
    return t


def parse(path) -> ParsedFile:
    return parse_pages(read_pages(path))
