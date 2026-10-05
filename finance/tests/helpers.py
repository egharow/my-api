"""Builders for synthetic statements. All data here is invented; real statements must never be committed."""
from datetime import date

from ftracker import importer
from ftracker.parsers.base import ParsedFile, ParsedStatement, ParsedTxn


def card_txn(day, desc, charge, voucher, **kw):
    return ParsedTxn(txn_date=day, description=desc, amount=-charge, currency="ILS",
                     orig_amount=-charge, orig_currency="ILS", voucher=voucher, **kw)


def card_file(issuer="isracard", last4="1111", billing=date(2026, 10, 2), txns=None, holder="", total=None):
    txns = txns if txns is not None else [
        card_txn(date(2026, 9, 3), "SUPER EXAMPLE", 100.0, "v1"),
        card_txn(date(2026, 9, 5), "CAFE EXAMPLE", 50.0, "v2"),
    ]
    st = ParsedStatement(issuer=issuer, account_kind="card", last4=last4, product="test", billing_date=billing,
                         period_start=None, period_end=None,
                         stated_total=total if total is not None else round(-sum(t.amount for t in txns), 2),
                         currency="ILS", txns=txns, holder_text=holder)
    return ParsedFile(issuer, "card_export", [st])


def bank_file(rows, start=date(2026, 7, 4), end=date(2026, 10, 4), last4="9999", opening=10000.0):
    """rows: (date, description, signed amount), oldest first. Balances are computed."""
    bal, txns = opening, []
    for d, desc, amt in rows:
        bal = round(bal + amt, 2)
        txns.append(ParsedTxn(txn_date=d, description=desc, amount=amt, currency="ILS", balance_after=bal))
    st = ParsedStatement(issuer="leumi", account_kind="bank", last4=last4, product="checking", billing_date=None,
                         period_start=start, period_end=end, stated_total=None, currency="ILS", txns=txns)
    return ParsedFile("leumi", "bank_statement", [st])


def run_import(home, conn, monkeypatch, files, today=date(2026, 10, 5)):
    """files: list of (filename, ParsedFile). Writes dummy bytes and stubs the parser."""
    by_name = {}
    for i, (name, parsed) in enumerate(files):
        (home.inbox / name).write_bytes(f"{name}-{i}-{len(by_name)}".encode())
        by_name[name] = parsed
    monkeypatch.setattr(importer, "parse_file", lambda path: by_name[path.name])
    return importer.import_inbox(home, conn, today)
