from datetime import date

from ftracker import reconcile, summary
from ftracker.parsers import detect, leumi_xls
from .helpers import run_import

PAGE = """<HTML dir="RTL"><head><META http-equiv="Content-Type" content="text/html; charset=UTF-8"></head><body><table>
<tr><td>בנק לאומי - אחזור דפי חשבון</td></tr>
<tr><td>נכון לתאריך: 07/10/2026</td><td>יתרה: &#8206;₪ 3,000.00&#8206;</td></tr>
<tr><td>תאריך</td><td>דף</td><td>אסמכתא</td><td>תיאור</td><td>חובה</td><td>זכות</td><td>יתרה</td><td>מספר חשבון</td></tr>
<tr><td>01/09/26</td><td>4\\2026</td><td>7143</td><td>אנליסט קופ"ג-י</td><td>500.00</td><td></td><td>9,500.00</td><td>933-123456/78</td></tr>
<tr><td>01/09/26</td><td>4\\2026</td><td>7143</td><td>אנליסט קופ"ג-י</td><td>500.00</td><td></td><td>9,000.00</td><td>933-123456/78</td></tr>
<tr><td>29/09/26</td><td>4\\2026</td><td>70432</td><td>קנית ני"ע</td><td>8,000.00</td><td></td><td>1,000.00</td><td>933-123456/78</td></tr>
<tr><td>01/10/26</td><td>4\\2026</td><td>3556</td><td>מ.חינוך-משכו-י</td><td></td><td>2,000.00</td><td>3,000.00</td><td>933-123456/78</td></tr>
</table></body></HTML>"""


def _file(tmp_path):
    p = tmp_path / "אחזור דפי חשבון 08-10-2026.xls"
    p.write_text(PAGE, encoding="utf-8")
    return p


def test_leumi_excel_download_is_read(tmp_path):
    pf = detect.parse_file(_file(tmp_path))
    st = pf.statements[0]
    assert pf.issuer == "leumi" and st.account_kind == "bank" and st.last4 == "5678"
    assert (st.period_start, st.period_end) == (date(2026, 9, 1), date(2026, 10, 1)) and len(st.txns) == 4
    assert [t.amount for t in st.txns] == [2000.0, -8000.0, -500.0, -500.0]          # newest first, debit negative, credit positive
    assert st.txns[1].voucher == "70432" and st.txns[1].balance_after == 1000.0
    assert reconcile.chain_problems(st.txns) == []


def test_other_html_is_not_mistaken_for_a_statement(tmp_path):
    p = tmp_path / "x.xls"
    p.write_text("<html><table><tr><td>hello</td></tr></table></html>", encoding="utf-8")
    try:
        detect.parse_file(p)
    except detect.Unrecognised as exc:
        assert "not a known" in str(exc)
    else:
        raise AssertionError("should not be recognised")


def test_securities_and_analyst_deposits_are_savings_and_count_as_invested(tmp_path, home, conn, monkeypatch):
    f = _file(tmp_path)
    monkeypatch.setattr("ftracker.importer.parse_file", lambda path: detect.parse_file(f), raising=False)
    from ftracker import importer
    home.inbox.mkdir(parents=True, exist_ok=True)
    (home.inbox / f.name).write_bytes(f.read_bytes())
    rep = importer.import_inbox(home, conn, date(2026, 10, 8))
    assert [x.status for x in rep.files] == ["imported"]
    rows = {r["description"]: (r["cat"], r["kind"]) for r in conn.execute(
        "SELECT t.description, c.name AS cat, t.kind FROM transactions t JOIN categories c ON c.id = t.category_id")}
    assert rows['קנית ני"ע'] == ("Transfer to savings", "transfer") and rows['אנליסט קופ"ג-י'] == ("Transfer to savings", "transfer")
    assert summary.invested(conn, "2026-09") == 9000.0                                  # 8,000 of securities and 2 x 500 into the fund
