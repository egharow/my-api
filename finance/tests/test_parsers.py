from datetime import date

import pytest

from ftracker.parsers import amex, isracard, leumi_pdf
from ftracker.parsers.base import ParseError


def _isracard_rows():
    return [
        ["פירוט עסקאות", None, "אוקטובר 2026", None, None, None, None, None],
        ["‫פלטינה מסטרקארד‬ - 1111", None, None, None, None, None, None, "₪ 130.00"],
        ["על שם דוגמה בדיקה", None, None, None, None, None, None, "לחיוב ב-02.10"],
        ["עסקאות למועד חיוב"],
        ["תאריך רכישה", "שם בית עסק", "סכום עסקה", "מטבע עסקה", "סכום חיוב", "מטבע חיוב", "מס' שובר", "פירוט נוסף"],
        ["28.09.26", "SERVICE ONLINE", 20, "$", 61.55, "₪", "900", "אתר חו\"ל\nהוראת קבע"],
        ["23.09.26", "STORE REFUND", -50, "₪", -50, "₪", "901", ""],
        ["23.08.26", "CAR THING", 1905, "₪", 173.18, "₪", "902", "תשלום 2 מתוך 11"],
        [None, "סה\"כ לחיוב החודש בכרטיס בש\"ח", None, None, 184.73, "₪", None, None],
    ]


def test_isracard_parses_fx_refund_instalment_and_total():
    f = isracard.parse_rows(_isracard_rows())
    st = f.statements[0]
    assert (st.issuer, st.last4, st.billing_date) == ("isracard", "1111", date(2026, 10, 2))
    assert st.stated_total == 184.73
    assert round(-sum(t.amount for t in st.txns), 2) == 184.73
    fx_txn, refund, inst = st.txns
    assert (fx_txn.orig_amount, fx_txn.orig_currency, fx_txn.amount) == (-20.0, "USD", -61.55)
    assert fx_txn.is_recurring
    assert refund.amount == 50.0                      # money back is positive
    assert (inst.instalment_no, inst.instalment_total, inst.instalment_full) == (2, 11, 1905.0)
    assert inst.amount == -173.18                     # only this month's charge is booked


def test_isracard_billing_in_next_year():
    rows = _isracard_rows()
    rows[0][2] = "דצמבר 2026"
    rows[2][7] = "לחיוב ב-02.01"
    assert isracard.parse_rows(rows).statements[0].billing_date == date(2027, 1, 2)


def test_isracard_rejects_other_layouts():
    with pytest.raises(ParseError):
        isracard.parse_rows([["something", "else"]])


def _amex_rows():
    return [
        [""],
        ["שם בעל כרטיס", "", "", "", "", "", "", ""],
        [""],
        ["אמריקן אקספרס זהב - 2222", "מועד חיוב", "02/10/26", "", "", "", "", ""],
        ["עסקאות בארץ"],
        ["תאריך רכישה", "שם בית עסק", "סכום עסקה", "מטבע מקור", "סכום חיוב", "מטבע לחיוב", "מספר שובר", "פירוט נוסף"],
        ["03/09/2026", "SHOP ONE", 100.0, "₪", 100.0, "₪", "a1", ""],
        ["04/09/2026", "SHOP TWO", 6.3, "₪", 5.98, "₪", "a2", "הנחה 0.32 ש\"ח חבר"],
        ["02/10/26", "סך חיוב בש\"ח:", "02/10/26", "", 105.98, "₪", "", ""],
        [""],
        ["אמריקן אקספרס זהב - 3333 *", "מועד חיוב", "02/10/26", "", "", "", "", ""],
        ["תאריך רכישה", "שם בית עסק", "סכום עסקה", "מטבע מקור", "סכום חיוב", "מטבע לחיוב", "מספר שובר", "פירוט נוסף"],
        ["26/12/2024", "BIG PURCHASE", 3600.0, "₪", 100.0, "₪", "b1", "תשלום 22 מתוך 36"],
        ["02/10/26", "סך חיוב בש\"ח:", "02/10/26", "", 100.0, "₪", "", ""],
    ]


def test_amex_two_cards_in_one_file():
    f = amex.parse_rows(_amex_rows())
    assert [s.last4 for s in f.statements] == ["2222", "3333"]
    first, second = f.statements
    assert first.stated_total == 105.98 and len(first.txns) == 2
    assert first.txns[1].amount == -5.98              # the discounted charge, not the list price
    assert second.txns[0].instalment_no == 22 and second.txns[0].instalment_full == 3600.0
    for st in f.statements:                           # total row must not be read as a transaction
        assert round(-sum(t.amount for t in st.txns), 2) == st.stated_total


def _visual(word):
    """Hebrew is stored in visual order in the PDF, i.e. reversed."""
    return word[::-1] if any("֐" <= c <= "׿" for c in word) else word


def _w(text, x0, x1, top):
    return {"text": _visual(text), "x0": x0, "x1": x1, "top": top}


def _leumi_page(extra_description="עמלה"):
    header = [_w("מספר", 195, 230, 100), _w("חשבון:", 160, 195, 100), _w("123-00004567", 81, 150, 100),
              _w("04.10.2026-04.07.2026", 361, 440, 100),
              _w("יתרה", 59, 90, 128), _w("מצטברת", 91, 127, 128),
              _w("חובה", 186, 210, 128), _w("זכות", 293, 315, 128)]
    rows = [
        # a debit whose description contains the word "debit" must not move the column
        [_w("ריבית", 432, 462, 150), _w("חובה", 400, 430, 150), _w("04.10.2026", 468, 520, 150),
         _w("₪900.00", 68, 118, 150), _w("₪4.94", 184, 212, 150)],
        [_w("משכורת", 430, 462, 172), _w("03.10.2026", 468, 520, 172),
         _w("₪905.00", 68, 118, 172), _w("₪1,000.00", 280, 326, 172)],
        [_w(extra_description, 430, 462, 194), _w("02.10.2026", 468, 520, 194),
         _w("₪-95.00", 68, 118, 194), _w("₪1,000.00", 175, 225, 194)],
    ]
    return header + [w for r in rows for w in r]


def test_leumi_reads_columns_and_signs():
    f = leumi_pdf.parse_pages([_leumi_page()])
    st = f.statements[0]
    assert (st.last4, st.period_start, st.period_end) == ("4567", date(2026, 7, 4), date(2026, 10, 4))
    by_desc = {t.description: t for t in st.txns}
    assert by_desc["משכורת"].amount == 1000.0         # credit column
    assert by_desc["עמלה"].amount == -1000.0          # debit column
    assert by_desc["ריבית חובה"].amount == -4.94      # the trap: "debit" inside a description
    assert by_desc["עמלה"].balance_after == -95.0


def test_leumi_detects_by_headers_not_order():
    assert leumi_pdf.looks_like([_leumi_page()])
    assert not leumi_pdf.looks_like([[_w("hello", 1, 2, 3)]])


def test_one_zero_movements_are_decoded_and_ordered_by_balance():
    from datetime import date
    from ftracker.parsers import onezero
    rows = [["תאריך תנועה", "תאריך ערך", "סוג פעולה", "תיאור", "סכום פעולה", "מטבע", "חיוב/זיכוי", "יתרה", "אסמכתא"],
            [46310.0, 46310.0, "העברות", "‭99010330/10-933-000585939/םולשת/אירבג םיקילא/‭ימואל", 5000.0, "ILS", "זיכוי", 6000.0, "r1"],
            [46310.0, 46310.0, "העברות", "זיכוי דמי מנוי\r", 40.0, "ILS", "זיכוי", 1000.0, "r2"],
            [46310.0, 46310.0, "אחר", "דמי מנוי - FounderPlusS", -40.0, "ILS", "חיוב", 960.0, "r3"],
            [46309.0, 46309.0, "חיובים", "‭13009998/6045/‭טקרייד-טרכארשי", -100.0, "ILS", "חיוב", 1000.0, "r4"]]
    st = onezero.parse_rows(rows).statements[0]
    assert st.issuer == "onezero" and st.account_kind == "bank" and len(st.txns) == 4
    assert st.txns[0].description.startswith("העברות לאומי/אליקים גבריא/תשלום")        # read the right way round
    assert "6045" in st.txns[3].description and "ישראכרט" in st.txns[3].description
    assert st.txns[1].description == "אחר דמי מנוי - FounderPlusS" or st.txns[2].description == "אחר דמי מנוי - FounderPlusS"
    for a, b in zip(st.txns, st.txns[1:]):
        assert abs(a.balance_after - (b.balance_after + a.amount)) < 0.01             # the balances chain, also within one day
    assert st.period_end == date(2026, 11, 2) or st.period_end.year == 2026


def test_cal_statement_rows_are_read_and_club_points_are_skipped():
    from datetime import datetime
    from ftracker.parsers import cal
    rows = [["פירוט עסקאות לחשבון לאומי לישראל 933-585939 לכרטיס מאסטרקארד WORLD המסתיים ב-5962"], ["עסקאות לחיוב ב-02/11/2026: 124.00 ₪"],
            ["תאריך\nעסקה", "שם בית עסק", "סכום\nעסקה", "סכום\nחיוב", "סוג\nעסקה", "ענף", "הערות"],
            [datetime(2026, 10, 31), "החזר FLYALL", -47.39, None, "צבירה למועדון", None, None],
            [datetime(2026, 10, 30), "מאפיית לחם משנה", 24, 24, "רגילה", "מזון מהיר", None],
            [datetime(2026, 10, 30), "מאפיית לחם משנה", 24, 24, "רגילה", "מזון מהיר", None],
            [datetime(2026, 10, 25), "סופר פארם", 76, 76, "רגילה", "רפואה ובריאות", None]]
    st = cal.parse_rows(rows).statements[0]
    assert (st.issuer, st.last4, st.stated_total) == ("cal", "5962", 124.0) and len(st.txns) == 3
    assert len({t.voucher for t in st.txns}) == 3                                    # two identical purchases on one day stay two lines
    assert st.txns[0].detail.startswith("ענף: מזון מהיר")
