from datetime import date

from ftracker.parsers import isracard_pdf as ip


def H(text):                       # the PDF stores Hebrew words back to front
    return text[::-1]


def w(text, x1, top, width=20):
    return {"text": text, "x0": x1 - width, "x1": x1, "top": top}


def page():
    ws = [w("*6045*", 560, 6), w(H("פירוט"), 524, 143), w(H("פעולותיך"), 505, 143), w(H("לתאריך:"), 472, 143), w("10/07/26", 442, 143),
          w(H("לכבוד"), 525, 99), w(H("שיר"), 511, 107)]
    # shekel row (charge near x=262, original near x=305, sector near x=356) with an instalment note
    ws += [w("15/06/26", 524, 250), w(H("סופר"), 478, 250), w(H("דוגמה"), 442, 250), w(H("מזון"), 356, 250),
           w("300.00", 305, 250), w("100.00", 262, 250), w(H("תשלום"), 235, 250), w("2", 215, 250), w(H("מתוך"), 205, 250), w("3", 190, 250)]
    # a refund
    ws += [w("16/06/26", 524, 262), w(H("חנות"), 478, 262), w(H("החזרים"), 442, 262), w("-25.00", 305, 262), w("-25.00", 262, 262)]
    ws += [w(H('סה"כ'), 523, 280), w(H("חיוב"), 501, 280), w(H("לתאריך"), 483, 280), w("10/07/26", 453, 280), w("75.00", 262, 280)]
    # foreign rows: Thai baht with a rate, a dollar row, and a shekel row inside the foreign table
    ws += [w("02/07/26", 524, 330), w("SAMUI", 437, 330), w("THB", 377, 330), w("587.00", 361, 330), w("17.68", 332, 330),
           w("03/07/26", 289, 330), w("3.0060", 248, 330), w("0.00", 221, 330), w("53.15", 181, 330)]
    ws += [w("03/07/26", 524, 342), w("AMAZON", 420, 342), w("$", 377, 342), w("10.00", 370, 342),
           w("04/07/26", 289, 342), w("3.0000", 248, 342), w("0.00", 221, 342), w("30.00", 181, 342)]
    ws += [w("04/07/26", 524, 354), w("ONLINE", 421, 354), w("12.00", 377, 354), w("₪", 356, 354), w("12.00", 182, 354)]
    ws += [w("02/07/26", 524, 366), w("SAMUI", 437, 366), w("THB", 377, 366), w("587.00", 361, 366), w("17.68", 332, 366),
           w("03/07/26", 289, 366), w("3.0060", 248, 366), w("0.00", 221, 366), w("53.15", 181, 366)]       # same line twice
    ws += [w(H('סה"כ'), 521, 380), w(H("חיוב"), 500, 380), w(H("לתאריך"), 482, 380), w("10/07/26", 451, 380), w("148.30", 181, 380)]
    return ws


def test_rows_are_read_by_column_and_totals_match():
    assert ip.looks_like([page()])
    st = ip.parse_pages([page()]).statements[0]
    assert (st.last4, st.billing_date, st.stated_total) == ("6045", date(2026, 7, 10), 223.3)
    by = {(t.txn_date.day, t.description): t for t in st.txns}
    shop = by[(15, "סופר דוגמה")]
    assert shop.amount == -100.0 and shop.orig_amount == -300.0 and (shop.instalment_no, shop.instalment_total) == (2, 3)
    assert by[(16, "חנות החזרים")].amount == 25.0                       # a refund is money back
    thai = by[(2, "SAMUI")]
    assert thai.amount == -53.15 and thai.orig_currency == "THB" and thai.orig_amount == -587.0
    assert by[(3, "AMAZON")].orig_currency == "USD" and by[(3, "AMAZON")].amount == -30.0
    assert by[(4, "ONLINE")].orig_currency == "ILS" and by[(4, "ONLINE")].amount == -12.0
    assert abs(sum(-t.amount for t in st.txns) - st.stated_total) < 0.01
    assert "שיר" in st.holder_text


def test_identical_lines_on_one_day_stay_separate():
    st = ip.parse_pages([page()]).statements[0]
    thai = [t for t in st.txns if t.description == "SAMUI" and t.amount == -53.15]
    assert len(thai) == 2 and thai[0].voucher != thai[1].voucher


def test_other_pdfs_are_not_mistaken_for_it():
    assert not ip.looks_like([[w(H("חשבון"), 500, 10), w(H("חובה"), 400, 10)]])
