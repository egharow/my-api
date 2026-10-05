"""Leumi account statement (PDF, right-to-left).

pdfplumber returns Hebrew in visual order, so each Hebrew word is reversed and the
words of a row are read from the right edge to the left. The debit and credit
amounts are told apart by which header column they sit under.
"""
import re
from datetime import date

from .base import ParseError, ParsedFile, ParsedStatement, ParsedTxn, clean, parse_date

_HEBREW = re.compile("[֐-׿]")
_AMOUNT = re.compile(r"^-?₪?-?[\d,]+\.\d\d$")
_DATE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
_ROW_TOLERANCE = 3


def fix_word(text: str) -> str:
    text = clean(text)
    return text[::-1] if _HEBREW.search(text) else text


def read_pages(path) -> list[list[dict]]:
    import pdfplumber
    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            pages.append([
                {"text": w["text"], "x0": w["x0"], "x1": w["x1"], "top": w["top"]}
                for w in page.extract_words()
            ])
    return pages


def _rows(words: list[dict]) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["top"]), -w["x1"])):
        if rows and abs(rows[-1][0]["top"] - w["top"]) <= _ROW_TOLERANCE:
            rows[-1].append(w)
        else:
            rows.append([w])
    return rows


def _amount(text: str) -> float:
    return float(text.replace("₪", "").replace(",", ""))


def looks_like(pages: list[list[dict]]) -> bool:
    """Header words appear in the order the PDF stores them, not reading order, so test for presence."""
    if not pages:
        return False
    words = [fix_word(w["text"]) for w in pages[0]]
    has = lambda needle: any(needle in w for w in words)
    return has("חשבון") and has("מספר") and has("חובה") and has("זכות")


def parse_pages(pages: list[list[dict]]) -> ParsedFile:
    last4 = None
    period = None
    txns: list[ParsedTxn] = []
    holder_words: list[str] = []

    for words in pages:
        rows = _rows(words)
        header_centres: dict[str, float] = {}
        for row in rows:
            fixed = [(fix_word(w["text"]), w) for w in row]
            line = " ".join(t for t, _ in fixed)
            compact = line.replace(" ", "")
            if "מספרחשבון" in compact:
                m = re.search(r"(\d{3}-\d{5,9})", line)
                if m:
                    last4 = m.group(1)[-4:]
                m = re.findall(r"\d{2}\.\d{2}\.\d{4}", line)
                if len(m) >= 2:
                    ds = sorted(parse_date(x) for x in m[:2])
                    period = (ds[0], ds[1])
            if "שםחשבון" in compact:
                holder_words = [t for t, _ in fixed]
            # Only the real header row sets the column positions. A description such as
            # "ריבית חובה" (debit interest) contains the word "debit" and must not.
            if any(t.startswith("יתרה") for t, _ in fixed):
                for text, w in fixed:
                    if text in ("חובה", "זכות") or text.startswith("יתרה"):
                        key = "debit" if text == "חובה" else "credit" if text == "זכות" else "balance"
                        header_centres[key] = (w["x0"] + w["x1"]) / 2
            dates = [(t, w) for t, w in fixed if _DATE.match(t)]
            amounts = [(t, w) for t, w in fixed if _AMOUNT.match(t)]
            if not dates or len(amounts) < 2:
                continue
            if len(header_centres) < 3:
                raise ParseError("amount column headers not found")
            amounts.sort(key=lambda tw: tw[1]["x0"])
            balance_t = amounts[0][0]
            others = amounts[1:]
            if len(others) != 1:
                raise ParseError(f"unexpected amount columns in row: {line}")
            amt_t, amt_w = others[0]
            centre = (amt_w["x0"] + amt_w["x1"]) / 2
            is_credit = (abs(centre - header_centres["credit"])
                         < abs(centre - header_centres["debit"]))
            value = _amount(amt_t)
            date_w = dates[0][1]
            desc_words = [(t, w) for t, w in fixed
                          if w["x1"] <= date_w["x0"] + 1 and not _AMOUNT.match(t)
                          and not _DATE.match(t) and w is not date_w]
            desc_words.sort(key=lambda tw: -tw[1]["x1"])
            description = " ".join(t for t, _ in desc_words)
            txns.append(ParsedTxn(
                txn_date=parse_date(dates[0][0]), description=description,
                amount=value if is_credit else -value, currency="ILS",
                balance_after=_amount(balance_t),
            ))
    if not (last4 and period and txns):
        raise ParseError("not a Leumi account statement")
    st = ParsedStatement(
        issuer="leumi", account_kind="bank", last4=last4, product="checking",
        billing_date=None, period_start=period[0], period_end=period[1],
        stated_total=None, currency="ILS", txns=txns, holder_text=" ".join(holder_words),
    )
    return ParsedFile("leumi", "bank_statement", [st])


def parse(path) -> ParsedFile:
    return parse_pages(read_pages(path))
