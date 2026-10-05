import argparse
import sys
from datetime import date

from . import accounts, balances, categorize, commits, expected, fx, importer, reconcile, sheet_import, summary
from . import discrepancies as dx
from .config import resolve_home
from .db import connect


def _money(x) -> str:
    return f"{x:,.2f}"


def _open(args):
    home = resolve_home(args.home)
    home.ensure()
    return home, connect(home.db_path)


def cmd_init(args):
    home, conn = _open(args)
    for name in args.owner or []:
        accounts.add_owner(conn, name)
    print(f"Ready: {home.root}")
    print("Drop statements into inbox/, then run: finance import")


def cmd_owner(args):
    home, conn = _open(args)
    if args.action == "add":
        accounts.add_owner(conn, args.name, args.alias)
        print(f"Owner {args.name} saved")
    else:
        for r in conn.execute("SELECT o.name, GROUP_CONCAT(a.alias, ', ') AS aliases FROM owners o "
                              "LEFT JOIN owner_aliases a ON a.owner_id = o.id GROUP BY o.id"):
            print(f"{r['name']}  (matches: {r['aliases'] or '-'})")


def cmd_account(args):
    home, conn = _open(args)
    if args.action == "add":
        accounts.add_account(conn, args.kind, args.issuer, args.label, args.owner, args.currency,
                             args.last4, args.pays_from)
        print(f"Account {args.label} added")
    elif args.action == "set-owner":
        accounts.set_owner(conn, args.ref, args.owner)
        print("Owner set")
    elif args.action == "pays-from":
        accounts.set_pays_from(conn, args.ref, args.bank)
        print("Payer set")
    elif args.action == "destination":
        accounts.set_counterparty(conn, int(args.ref), args.bank)
        print("Destination set")
    elif args.action == "destination-rule":
        res = accounts.set_destination_rule(conn, args.ref, args.bank)
        print(f"Rule saved. {res['updated']} transfer(s) updated; "
              f"{res['left_in_submitted_imports']} in submitted imports left as they were.")
    else:
        for r in conn.execute("SELECT a.id, a.label, a.kind, a.currency, o.name AS owner, a.pays_from_account_id "
                              "FROM accounts a LEFT JOIN owners o ON o.id = a.owner_id ORDER BY a.kind, a.label"):
            print(f"{r['id']:>3}  {r['label']:<28} {r['kind']:<12} {r['currency']}  owner: {r['owner'] or '-'}")


def _print_status(conn, today, batch_id=None):
    print("\nExpected files")
    items = expected.expected_files(conn, today)
    if not items:
        print("  everything you should have is in")
    for e in items:
        mark = {"missing": "✗", "due": "!", "stale": "~"}.get(e["status"], "·")
        print(f"  {mark} {e['status']:<8} {e['source']} {e['period']}  {e['detail']}")
    fees = reconcile.fee_status(conn, today)
    if fees:
        print("\nBank fee refunds")
        for f in fees:
            tail = f"refunded {f['refund_date']}" if f["state"] == "refunded" else f"expected by {f['due']}"
            print(f"  {f['state']:<9} ₪{_money(f['amount'])} on {f['date']}  {tail}")
    open_items = dx.list_items(conn, ("open",))
    print(f"\nNeeds attention: {len(open_items)}")
    for d in open_items:
        print(f"  #{d['id']:<4} [{d['severity']}] {d['title']}  ({d['comment_count']} comments)")
    n = conn.execute("SELECT COUNT(*) FROM transactions WHERE category_status = 'proposed'").fetchone()[0]
    print(f"\nWaiting for category approval: {n}")


def cmd_import(args):
    home, conn = _open(args)
    today = date.fromisoformat(args.today) if args.today else date.today()
    report = importer.import_inbox(home, conn, today)
    if not report.files:
        print("inbox/ is empty. Drop statements in there first.")
        return
    for f in report.files:
        extra = f" → {f.archived_to}" if f.archived_to else ""
        counts = f" ({f.new_txns} new, {f.skipped_txns} already known)" if f.status == "imported" else ""
        print(f"{f.status:<12} {f.name}{counts}{extra}  {f.detail if f.status != 'imported' else ''}")
    if report.batch_id:
        print(f"\nDraft import #{report.batch_id} created. Review it, then run: finance submit {report.batch_id}")
    _print_status(conn, today.isoformat(), report.batch_id)


def cmd_status(args):
    home, conn = _open(args)
    today = args.today or date.today().isoformat()
    reconcile.track_fee_refunds(conn, today)
    conn.commit()
    _print_status(conn, today)


def cmd_review(args):
    home, conn = _open(args)
    rows = conn.execute(
        """SELECT t.description_norm AS key, MIN(t.description) AS description, COUNT(*) AS n,
                  ROUND(SUM(-t.amount), 2) AS total, c.name AS proposed, MIN(t.proposal_basis) AS basis,
                  GROUP_CONCAT(t.id) AS ids
           FROM transactions t JOIN categories c ON c.id = t.category_id
           WHERE t.category_status = 'proposed' GROUP BY t.description_norm, c.name
           ORDER BY ABS(SUM(t.amount)) DESC""").fetchall()
    if not rows:
        print("Nothing waiting for approval.")
    for r in rows[: args.limit]:
        print(f"{r['description']}  ×{r['n']}  ₪{_money(r['total'])}\n    proposed: {r['proposed']}  ({r['basis']})\n    ids: {r['ids']}")


def cmd_approve(args):
    home, conn = _open(args)
    ids = [int(i) for i in (args.ids or "").split(",") if i]
    if args.merchant:
        ids += [r[0] for r in conn.execute(
            "SELECT id FROM transactions WHERE category_status = 'proposed' AND description_norm LIKE ?",
            (f"%{args.merchant.upper()}%",))]
    if not ids:
        sys.exit("No transactions selected (use --ids or --merchant).")
    if args.learn and args.merchant:
        hits = categorize.preview_rule(conn, args.merchant)
        print(f"This rule would match {len(hits)} transaction(s) in total.")
    n = categorize.approve(conn, ids, args.category, learn=args.learn, actor=args.author)
    print(f"{n} transaction(s) set to {args.category}")


def cmd_rule(args):
    home, conn = _open(args)
    if args.action == "add":
        if not (args.pattern and args.category):
            sys.exit("Usage: finance rule add PATTERN CATEGORY [--kind income] [--direction in]")
        categorize.add_user_rule(conn, args.pattern, args.category, args.kind, args.direction)
        conn.commit()
        print("Rule saved")
    else:
        for r in conn.execute("""SELECT r.id, r.source, r.pattern, c.name AS category, r.enabled FROM rules r
                                 JOIN categories c ON c.id = r.category_id
                                 WHERE r.source != 'builtin' OR ? ORDER BY r.source, r.pattern""", (int(args.all),)):
            print(f"#{r['id']:<4} {r['source']:<8} {r['pattern']:<30} -> {r['category']}{'' if r['enabled'] else '  (off)'}")


def cmd_sheet_import(args):
    home, conn = _open(args)
    extra = dict(m.split("=", 1) for m in args.map or [])
    links = dict(m.split("=", 1) for m in args.link or [])
    data = sheet_import.parse_workbook(args.file, args.high_level_year)
    print(sheet_import.preview(data, extra))
    if not args.save:
        print("\nPreview only. Nothing was saved. Run again with --save to write it as a draft import.")
        return
    res = sheet_import.save(conn, data, args.primary_owner, args.partner_owner, extra, links)
    if res["batch_id"] is None:
        print("\nNothing new to import; everything in this file is already saved.")
        return
    print(f"\nSaved as draft import #{res['batch_id']}: {res['transactions']} lines "
          f"({res['already_there']} already there), {res['accounts_created']} accounts, "
          f"{res['balances']} balances, {res['rules_learned']} rules learned from your categories, "
          f"{res['transfers_reclassified']} Bit/transfer lines treated as transfers.")
    print(f"Review it with `finance review`, then `finance submit {res['batch_id']}`.")


def cmd_items(args):
    home, conn = _open(args)
    statuses = ("open", "explained", "resolved", "acknowledged") if args.all else ("open", "explained")
    for d in dx.list_items(conn, statuses):
        print(f"#{d['id']:<4} {d['status']:<12} [{d['severity']}] {d['title']}")
        if args.threads:
            for c in dx.thread(conn, d["id"]):
                print(f"        {c['created_at']} {c['author']}: {c['body']}")


def cmd_comment(args):
    home, conn = _open(args)
    dx.add_comment(conn, args.item, args.author, args.text)
    if args.status:
        dx.set_status(conn, args.item, args.status, args.author, args.follow_up)
    conn.commit()
    print("Comment saved")


def cmd_note(args):
    home, conn = _open(args)
    item = dx.open_manual(conn, args.txn, args.text, args.author)
    conn.commit()
    print(f"Note saved on item #{item}")


def cmd_balance(args):
    home, conn = _open(args)
    if args.action == "set":
        balances.set_balance(conn, args.ref, args.amount, args.as_of, args.currency, note=args.note)
        print("Balance saved")
    else:
        for r in balances.latest(conn):
            print(f"{r['label']:<28} {r['kind']:<12} {_money(r['amount']):>14} {r['currency']}  as of {r['as_of']}")


def cmd_networth(args):
    home, conn = _open(args)
    nw = balances.net_worth(conn, args.as_of, args.currency, args.owner)
    for l in nw["lines"]:
        print(f"{l['account']:<28} {_money(l['native']):>14} {l['currency']}  = {_money(l['value']):>14} {nw['currency']}")
    print(f"{'TOTAL':<28} {'':>14}      = {_money(nw['total']):>14} {nw['currency']}  (as of {nw['as_of']})")


def cmd_fx(args):
    home, conn = _open(args)
    if args.action == "set":
        fx.set_rate(conn, args.date, args.base, args.quote, args.rate, "manual")
        conn.commit()
        print("Rate saved")
    elif args.action == "fetch":
        n = fx.fetch_boi(conn, date.fromisoformat(args.start), date.fromisoformat(args.end))
        print(f"{n} rate(s) downloaded")
    elif args.action == "needed":
        rows = conn.execute("""SELECT DISTINCT b.as_of FROM balances b WHERE b.currency != 'ILS' ORDER BY b.as_of""").fetchall()
        need = [r[0] for r in rows if fx.rate_on(conn, r[0], "USD", "ILS") is None]
        if not need:
            print("Every dollar balance already has a rate on or before its date.")
        for d in need:
            print(f"finance fx set {d} USD ILS RATE")
    else:
        for r in conn.execute("SELECT * FROM fx_rates ORDER BY rate_date DESC LIMIT 20"):
            print(f"{r['rate_date']} {r['base']}/{r['quote']} {r['rate']} ({r['source']})")


def cmd_submit(args):
    home, conn = _open(args)
    today = args.today or date.today().isoformat()
    try:
        cid = commits.submit(conn, home, args.batch, args.author, args.ack, today)
    except commits.NeedsAcknowledgement as exc:
        print("Still open at submit time:")
        for i in exc.issues:
            print(f"  - {i}")
        sys.exit("Run again with --ack to submit anyway; these are recorded in the commit.")
    row = conn.execute("SELECT committed_at, version FROM commits WHERE id = ?", (cid,)).fetchone()
    print(f"Submitted import #{args.batch} (version {row['version']}) at {row['committed_at']}")


def cmd_reopen(args):
    home, conn = _open(args)
    commits.reopen(conn, args.batch, args.reason, args.author)
    print(f"Import #{args.batch} reopened; submit again when you are done")


def cmd_history(args):
    home, conn = _open(args)
    for c in commits.history(conn):
        why = f"  reason: {c['reason']}" if c["reason"] else ""
        print(f"import #{c['batch_id']} v{c['version']}  submitted {c['committed_at']}{why}")


def cmd_summary(args):
    home, conn = _open(args)
    ov = summary.month_overview(conn, args.month)
    print(f"{args.month}: income ₪{_money(ov['income'])}  spending ₪{_money(ov['spending'])}  net ₪{_money(ov['saved'])}")
    for r in summary.spending_by_category(conn, args.month, args.owner):
        print(f"  {r['category']:<22} {r['subcategory']:<22} ₪{_money(r['spent']):>12}  ({r['n']})")


def build_parser():
    p = argparse.ArgumentParser(prog="finance", description="Household finance tracker")
    p.add_argument("--home", help="folder holding inbox/, archive/, data/ (default ~/Finance or $FINANCE_HOME)")
    p.add_argument("--today", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init"); s.add_argument("--owner", action="append"); s.set_defaults(fn=cmd_init)
    s = sub.add_parser("owner"); s.add_argument("action", choices=["add", "list"]); s.add_argument("name", nargs="?")
    s.add_argument("--alias", action="append"); s.set_defaults(fn=cmd_owner)
    s = sub.add_parser("account")
    s.add_argument("action", choices=["add", "list", "set-owner", "pays-from", "destination", "destination-rule"])
    s.add_argument("ref", nargs="?"); s.add_argument("--owner"); s.add_argument("--bank")
    s.add_argument("--kind", default="savings"); s.add_argument("--issuer", default="manual")
    s.add_argument("--label"); s.add_argument("--currency", default="ILS"); s.add_argument("--last4")
    s.add_argument("--pays-from"); s.set_defaults(fn=cmd_account)
    s = sub.add_parser("import"); s.set_defaults(fn=cmd_import)
    s = sub.add_parser("status"); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("review"); s.add_argument("--limit", type=int, default=40); s.set_defaults(fn=cmd_review)
    s = sub.add_parser("approve"); s.add_argument("--ids"); s.add_argument("--merchant")
    s.add_argument("--category", required=True); s.add_argument("--learn", action="store_true")
    s.add_argument("--author", default="user"); s.set_defaults(fn=cmd_approve)
    s = sub.add_parser("rule"); s.add_argument("action", choices=["add", "list"])
    s.add_argument("pattern", nargs="?"); s.add_argument("category", nargs="?")
    s.add_argument("--kind"); s.add_argument("--direction", choices=["in", "out"])
    s.add_argument("--all", action="store_true"); s.set_defaults(fn=cmd_rule)
    s = sub.add_parser("sheet-import"); s.add_argument("file")
    s.add_argument("--save", action="store_true"); s.add_argument("--map", action="append", metavar="SHEET=APP")
    s.add_argument("--link", action="append", metavar="SHEET_ACCOUNT=ACCOUNT")
    s.add_argument("--primary-owner", default="Ely"); s.add_argument("--partner-owner", default="Shir")
    s.add_argument("--high-level-year", type=int, default=2026); s.set_defaults(fn=cmd_sheet_import)
    s = sub.add_parser("items"); s.add_argument("--all", action="store_true")
    s.add_argument("--threads", action="store_true"); s.set_defaults(fn=cmd_items)
    s = sub.add_parser("comment"); s.add_argument("item", type=int); s.add_argument("text")
    s.add_argument("--author", default="user"); s.add_argument("--status", choices=list(dx.STATUSES))
    s.add_argument("--follow-up"); s.set_defaults(fn=cmd_comment)
    s = sub.add_parser("note"); s.add_argument("txn", type=int); s.add_argument("text")
    s.add_argument("--author", default="user"); s.set_defaults(fn=cmd_note)
    s = sub.add_parser("balance"); s.add_argument("action", choices=["set", "list"])
    s.add_argument("ref", nargs="?"); s.add_argument("amount", nargs="?", type=float)
    s.add_argument("--as-of"); s.add_argument("--currency"); s.add_argument("--note"); s.set_defaults(fn=cmd_balance)
    s = sub.add_parser("networth"); s.add_argument("--as-of"); s.add_argument("--currency", default="ILS")
    s.add_argument("--owner"); s.set_defaults(fn=cmd_networth)
    s = sub.add_parser("fx"); s.add_argument("action", choices=["set", "fetch", "list", "needed"])
    s.add_argument("date", nargs="?"); s.add_argument("base", nargs="?"); s.add_argument("quote", nargs="?")
    s.add_argument("rate", nargs="?", type=float); s.add_argument("--start"); s.add_argument("--end")
    s.set_defaults(fn=cmd_fx)
    s = sub.add_parser("submit"); s.add_argument("batch", type=int); s.add_argument("--ack", action="store_true")
    s.add_argument("--author", default="user"); s.set_defaults(fn=cmd_submit)
    s = sub.add_parser("reopen"); s.add_argument("batch", type=int); s.add_argument("--reason", required=True)
    s.add_argument("--author", default="user"); s.set_defaults(fn=cmd_reopen)
    s = sub.add_parser("history"); s.set_defaults(fn=cmd_history)
    s = sub.add_parser("summary"); s.add_argument("month"); s.add_argument("--owner"); s.set_defaults(fn=cmd_summary)
    return p


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except (ValueError, PermissionError, LookupError) as exc:
        sys.exit(f"Error: {exc}")


if __name__ == "__main__":
    main()
