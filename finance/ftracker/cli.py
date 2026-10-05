import argparse
import sys
from datetime import date

from . import accounts, balances, categorize, commits, expected, export, fx, goals, importer, reconcile, sheet_import, sheetsync, summary
from . import discrepancies as dx
from .config import resolve_home
from .db import connect


def _money(x) -> str:
    return f"{x:,.2f}"


def _open(args):
    home = resolve_home(args.home)
    home.ensure()
    conn = connect(home.db_path)
    if args.cmd != "fx":                     # keep the dollar rate fresh; offline is fine
        try:
            fx.ensure_fresh(conn, args.today or date.today().isoformat())
        except Exception:
            pass
    return home, conn


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
    elif args.action == "birth":
        goals.set_birth_date(conn, args.name, args.date)
        print(f"Birth date saved for {args.name}")
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


def cmd_goal(args):
    home, conn = _open(args)
    today = args.today or date.today().isoformat()
    if args.action == "add":
        gid = goals.add_goal(conn, args.name, args.amount, args.currency, args.by, args.age, args.owner,
                             args.account, args.return_rate)
        print(f"Goal #{gid} saved")
        return
    for p in goals.all_progress(conn, today) if args.action == "list" else [goals.progress(conn, args.goal, today, args.extra)]:
        pct = f"{p['percent'] * 100:.0f}%" if p["percent"] is not None else "-"
        print(f"#{p['id']} {p['name']}: {pct} of {_money(p['target'])} {p['currency']}  [{p['status']}]")
        if p["current"] is not None:
            print(f"    now {_money(p['current'])}; growing {_money(p['growth_per_month'] or 0)}/month lately")
        if p["target_date"]:
            print(f"    target {p['target_date']}: needs {_money(p['required_per_month'])}/month"
                  + (f", short by {_money(p['gap_per_month'])}/month" if p["gap_per_month"] else ""))
        if p["projected_date"]:
            print(f"    at this pace: reached about {p['projected_date'][:7]}")
        if p.get("note"):
            print(f"    {p['note']}")


def cmd_export(args):
    home, conn = _open(args)
    from pathlib import Path
    out = Path(args.out) if args.out else home.root / "exports" / f"finance-summary-{date.today():%Y-%m-%d}.xlsx"
    out.parent.mkdir(parents=True, exist_ok=True)
    res = export.export_workbook(conn, out, args.today or date.today().isoformat(), args.notes)
    print(f"Saved {out}\n  {res['months']} submitted month(s); {res['drafts_excluded']} draft import(s) left out"
          + ("; notes included" if args.notes else "; notes not included (add --notes to share them)"))
    print("Upload it to Google Drive and open it as a Google Sheet, or File > Import into the shared sheet.")


def cmd_serve(args):
    from . import web
    home = resolve_home(args.home)
    web.serve(home, args.port, args.lan, args.pin, not args.no_browser)


def cmd_first_run(args):
    from . import appmode, firstrun
    home, conn = _open(args)
    out = firstrun.run(conn, home, appmode.app_dir())
    print("\n".join(out["steps"]) or "Nothing to load.")


def cmd_app(args):
    from . import web
    web.serve(resolve_home(args.home), app_mode=True)


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
    cur = fx.current_rate(conn)
    if cur:
        print(f"Dollar assets valued at {cur['rate']:.4f} ₪/$ ({cur['source']}, {cur['date']})")


def cmd_fx(args):
    home, conn = _open(args)
    today = args.today or date.today().isoformat()
    if args.action == "set":
        day = today if args.date == "today" else args.date
        fx.set_rate(conn, day, args.base, args.quote, args.rate, "manual")
        conn.commit()
        print(f"Rate saved: 1 {args.base} = {args.rate} {args.quote} on {day}")
    elif args.action == "refresh":
        res = fx.refresh_current(conn, today, force=True)
        if res["status"] == "failed":
            print("Could not fetch a rate (offline or blocked). Enter one with: finance fx set today USD ILS RATE")
            print(f"  {res['detail']}")
        else:
            print(f"USD/ILS {res['rate']} on {res['date']} ({res['source']})")
    elif args.action == "mode":
        if args.date:
            fx.set_mode(conn, args.date)
        print(f"Dollar assets are valued at: {fx.mode(conn)} rate")
    elif args.action == "fetch":
        n = fx.fetch_boi(conn, date.fromisoformat(args.start), date.fromisoformat(args.end))
        print(f"{n} rate(s) downloaded")
    else:
        cur = fx.current_rate(conn)
        print(f"Mode: {fx.mode(conn)}. " + (f"Current rate: {cur['rate']:.4f} on {cur['date']} ({cur['source']})" if cur else "No rate yet."))
        for r in conn.execute("SELECT * FROM fx_rates ORDER BY rate_date DESC LIMIT 10"):
            print(f"  {r['rate_date']} {r['base']}/{r['quote']} {r['rate']} ({r['source']})")


def cmd_sheet(args):
    home, conn = _open(args)
    today = args.today or date.today().isoformat()
    if args.action == "setup":
        out = home.root / "exports" / "sheet-link-script.gs"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(sheetsync.script_text(conn, args.reset), encoding="utf-8")
        print(f"Script saved to {out}\n"
              "1. Open your Google Sheet, then Extensions > Apps Script.\n"
              "2. Delete what is there, paste the whole script, Save.\n"
              "3. Deploy > New deployment > Web app. Execute as: Me. Who has access: Anyone. Deploy and allow it.\n"
              "4. Copy the Web app address and run: finance sheet link ADDRESS\n"
              "(or paste it on the Sheet page of the dashboard)")
    elif args.action == "link":
        if not args.value:
            sys.exit("Usage: finance sheet link ADDRESS")
        sheetsync.link(conn, args.value)
        res = sheetsync.push(conn, today)
        print(("Linked. " if res["ok"] else "Saved the address, but the first update failed: ") + res["detail"])
    elif args.action == "sync":
        res = sheetsync.push(conn, today)
        print(("Google Sheet updated: " if res["ok"] else "Google Sheet NOT updated: ") + res["detail"])
    elif args.action == "notes":
        sheetsync.set_notes(conn, args.value == "on")
        print(f"Comments {'will' if args.value == 'on' else 'will not'} be sent to the Sheet")
    elif args.action == "unlink":
        sheetsync.unlink(conn)
        print("Unlinked")
    else:
        st = sheetsync.status(conn)
        print("Linked" if st["linked"] else "Not linked. Run: finance sheet setup")
        if st["linked"]:
            print(f"Last update: {st['last_sync'] or 'never'}; {st['last_result'] or ''}; comments {'included' if st['notes'] else 'not included'}")


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
    msg = sheetsync.sync_if_linked(conn, today)
    if msg:
        print(msg)


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
    s = sub.add_parser("owner"); s.add_argument("action", choices=["add", "list", "birth"]); s.add_argument("name", nargs="?"); s.add_argument("date", nargs="?")
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
    s = sub.add_parser("goal"); s.add_argument("action", choices=["add", "list", "progress"])
    s.add_argument("--name"); s.add_argument("--amount", type=float); s.add_argument("--currency", default="ILS")
    s.add_argument("--by", help="target date YYYY-MM-DD"); s.add_argument("--age", type=int)
    s.add_argument("--owner"); s.add_argument("--account", action="append"); s.add_argument("--return-rate", type=float, default=0.0)
    s.add_argument("--goal", type=int); s.add_argument("--extra", type=float, default=0.0)
    s.set_defaults(fn=cmd_goal)
    s = sub.add_parser("export"); s.add_argument("--out"); s.add_argument("--notes", action="store_true")
    s.set_defaults(fn=cmd_export)
    s = sub.add_parser("serve"); s.add_argument("--port", type=int, default=8765)
    s.add_argument("--lan", action="store_true", help="let other devices on your home network open it (PIN required)")
    s.add_argument("--pin"); s.add_argument("--no-browser", action="store_true"); s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("first-run", help="load the starter setup and seed data next to the app"); s.set_defaults(fn=cmd_first_run)
    s = sub.add_parser("app", help="run as an app window (what the desktop icon does)"); s.set_defaults(fn=cmd_app)
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
    s = sub.add_parser("fx"); s.add_argument("action", choices=["set", "refresh", "mode", "fetch", "list"])
    s.add_argument("date", nargs="?"); s.add_argument("base", nargs="?"); s.add_argument("quote", nargs="?")
    s.add_argument("rate", nargs="?", type=float); s.add_argument("--start"); s.add_argument("--end")
    s.set_defaults(fn=cmd_fx)
    s = sub.add_parser("sheet"); s.add_argument("action", choices=["setup", "link", "sync", "notes", "unlink", "status"])
    s.add_argument("value", nargs="?"); s.add_argument("--reset", action="store_true", help="make a new secret"); s.set_defaults(fn=cmd_sheet)
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
