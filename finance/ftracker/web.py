"""The local dashboard. Serves on this computer only unless you ask for --lan (then a PIN is required)."""
import hashlib
import hmac
import json
import re
import secrets
import sys
import threading
import time
import webbrowser
from pathlib import Path
from dataclasses import dataclass, field
from datetime import date
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import appmode, balances, categorize, commits, firstrun, fx, goals, importer, sheet_import, sheetsync, starter, summary, uploads, views, views_home, views_setup, views_trends
from . import accounts as accounts_mod
from . import discrepancies as dx
from .config import Home, migrate_legacy
from .db import connect

LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
CSP = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; "
       "form-action 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
UPLOAD_PATHS = {"/upload", "/history/upload"}
TOKEN_RE = re.compile(r"^[0-9a-f]{16}$")


@dataclass
class Response:
    status: int
    body: bytes
    headers: list[tuple[str, str]] = field(default_factory=list)


def _html(status: int, text: str, extra=None) -> Response:
    return Response(status, text.encode("utf-8"), [("Content-Type", "text/html; charset=utf-8")] + (extra or []))


LOADING_PAGE = ("<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=2><title>Finance · loading</title>"
                "<body style='font:16px system-ui;max-width:460px;margin:80px auto;text-align:center'>"
                "<h2>Loading your data…</h2><p>The first start, and the first start after an update, can take about a minute. Then it opens by itself.</p>"
                "<script>fetch('/ping').catch(function(){})</script>")


class App:
    def __init__(self, home: Home, pin: str | None = None, lan: bool = False, today: str | None = None,
                 app_dir: Path | None = None, watchdog: "appmode.Watchdog | None" = None, clock=time.time):
        self.home, self.pin, self.lan, self.fixed_today = home, pin, lan, today
        self.csrf = secrets.token_urlsafe(24)
        self._secret = secrets.token_bytes(32)
        self.app_dir = app_dir or appmode.app_dir()
        self.watchdog, self.clock, self.active = watchdog, clock, 0
        self.shutdown = None                      # set by serve(); closes the server
        self.loading = False                      # True while the starting data is being loaded in the background

    def _json(self, status: int, obj: dict) -> Response:
        return Response(status, json.dumps(obj).encode("utf-8"), [("Content-Type", "application/json")])

    def _today(self) -> str:
        return self.fixed_today or date.today().isoformat()

    def _auth_token(self) -> str:
        return hmac.new(self._secret, (self.pin or "").encode(), hashlib.sha256).hexdigest()

    def handle(self, method: str, raw_path: str, headers: dict, body: bytes = b"") -> Response:
        headers = {k.lower(): v for k, v in headers.items()}
        host = headers.get("host", "").rsplit(":", 1)[0] if not headers.get("host", "").startswith("[") else headers["host"].split("]")[0] + "]"
        if not self.lan and host not in LOCAL_HOSTS:
            return _html(421, "<h1>Wrong host</h1>")         # blocks DNS-rebinding style access
        cookies = SimpleCookie(headers.get("cookie", ""))
        url = urlparse(raw_path)
        path, q = url.path, {k: v[-1] for k, v in parse_qs(url.query).items()}

        if self.pin:
            if path == "/login" and method == "POST":
                form = {k: v[-1] for k, v in parse_qs(body.decode("utf-8")).items()}
                if hmac.compare_digest(form.get("pin", ""), self.pin):
                    return Response(303, b"", [("Location", "/"), ("Set-Cookie", f"auth={self._auth_token()}; HttpOnly; SameSite=Strict; Path=/")])
                return _html(403, self._login_page("Wrong PIN."))
            if not (cookies.get("auth") and hmac.compare_digest(cookies["auth"].value, self._auth_token())):
                return _html(401, self._login_page())

        if path == "/health":      # for the launcher: is the app up, and has a window actually loaded it?
            seen = bool(self.watchdog and self.watchdog.last_ping is not None)
            return self._json(200, {"app": appmode.APP_ID, "ok": True, "page_seen": seen})
        if path == "/ping":
            if self.watchdog:
                self.watchdog.ping(self.clock())
            return self._json(200, {"app": appmode.APP_ID, "ok": True})
        if self.loading:
            return _html(200, LOADING_PAGE)
        if method == "POST" and path in UPLOAD_PATHS:
            return self._upload(path, headers, body)

        self.active += 1
        conn = connect(self.home.db_path)
        try:
            owners = [r[0] for r in conn.execute("SELECT name FROM owners ORDER BY name")]
            who = unquote(cookies["who"].value) if cookies.get("who") else (owners[0] if owners else "")
            flash = unquote(cookies["flash"].value) if cookies.get("flash") else ""
            ctx = views.Ctx(today=self._today(), who=who, csrf=self.csrf, owners=owners,
                            flash=flash.split("|", 1)[-1] if flash else "", flash_kind="err" if flash.startswith("err|") else "ok",
                            inbox_count=sum(1 for p in self.home.inbox.iterdir() if p.is_file()) if self.home.inbox.exists() else 0,
                            data_root=str(self.home.root), starter=starter.find(self.app_dir) is not None, can_shortcut=appmode.WINDOWS)
            if method == "POST":
                return self._post(conn, ctx, path, headers, body)
            if path in ("/", "/balances", "/wealth") and not self.fixed_today:
                try:
                    fx.ensure_fresh(conn, self._today())      # at most every few hours; offline is fine
                except Exception:
                    pass
            res = self._get(conn, ctx, path, q)
            if flash:
                res.headers.append(("Set-Cookie", "flash=; Max-Age=0; Path=/"))
            return res
        finally:
            conn.close()
            self.active -= 1

    def _login_page(self, msg: str = "") -> str:
        return (f"<!doctype html><meta charset=utf-8><title>Finance</title><body style='font:16px system-ui;max-width:320px;margin:60px auto'>"
                f"<h2>Finance</h2><p>{msg}</p><form method=post action=/login><input name=pin type=password placeholder=PIN autofocus> "
                f"<button>Open</button></form>")

    def _get(self, conn, ctx, path, q) -> Response:
        if path == "/":
            return _html(200, views_home.home(conn, ctx))
        if path == "/dashboard":
            return _html(200, views.dashboard(conn, ctx, q))
        if path == "/upload":
            return _html(200, views_home.upload_page(conn, ctx))
        if path in ("/spending", "/trends"):
            return _html(200, views_home.spending_page(conn, ctx, q))
        if path in ("/wealth", "/networth"):
            return _html(200, views_home.wealth_page(conn, ctx, q))
        if path == "/review":
            return _html(200, views.review(conn, ctx, q))
        if path == "/items":
            return _html(200, views.items(conn, ctx, q))
        if path.startswith("/item/") and path[6:].isdigit():
            return _html(200, views.item(conn, ctx, int(path[6:])))
        if path == "/imports":
            return _html(200, views.imports(conn, ctx))
        if path == "/balances":
            return _html(200, views.balances_page(conn, ctx))
        if path == "/goals":
            return _html(200, views.goals_page(conn, ctx))
        if path == "/sheet":
            return _html(200, views.sheet_page(conn, ctx))
        if path == "/setup":
            return _html(200, views_setup.setup_page(conn, ctx))
        if path == "/history":
            return self._history_page(conn, ctx, q.get("t", ""))
        return _html(404, "<h1>Not found</h1>")

    def _check_post(self, headers: dict, token: str) -> Response | None:
        origin = headers.get("origin")
        if origin and urlparse(origin).netloc != headers.get("host"):
            return self._json(403, {"error": "Blocked: request came from another site."})
        if not hmac.compare_digest(token, self.csrf):
            return self._json(403, {"error": "Reload the page and try again."})
        return None

    def _upload(self, path: str, headers: dict, body: bytes) -> Response:
        bad = self._check_post(headers, headers.get("x-csrf", ""))
        if bad:
            return bad
        try:
            files = uploads.parse_multipart(headers.get("content-type", ""), body)
        except uploads.UploadError as exc:
            return self._json(400, {"error": str(exc)})
        if not files:
            return self._json(400, {"error": "No file arrived."})
        if path == "/upload":
            saved, rejected = uploads.save_to_inbox(files, self.home.inbox)
            return self._json(200, {"saved": saved, "rejected": rejected})
        f = files[0]
        if not f.name.lower().endswith(".xlsx"):
            return self._json(400, {"error": "Please choose the Excel (.xlsx) file you downloaded from Google Sheets."})
        tmp = self.home.data / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        for old in tmp.glob("history-*.xlsx"):
            if time.time() - old.stat().st_mtime > 86400:
                old.unlink(missing_ok=True)
        token = secrets.token_hex(8)
        dest = tmp / f"history-{token}.xlsx"
        dest.write_bytes(f.data)
        try:
            sheet_import.parse_workbook(dest)
        except Exception as exc:
            dest.unlink(missing_ok=True)
            return self._json(400, {"error": f"That file could not be read as your budget sheet ({exc})."})
        return self._json(200, {"token": token})

    def _history_page(self, conn, ctx, token: str) -> Response:
        if not token:
            return _html(200, views_setup.history_upload_page(conn, ctx))
        path = self.home.data / "tmp" / f"history-{token}.xlsx"
        if not TOKEN_RE.match(token) or not path.exists():
            return self._redirect("/history", "That preview has expired. Choose the file again.", err=True)
        return _html(200, views_setup.history_preview_page(conn, ctx, sheet_import.parse_workbook(path), token))

    def _redirect(self, to: str, msg: str = "", err: bool = False, cookies=None) -> Response:
        h = [("Location", to)]
        if msg:
            h.append(("Set-Cookie", f"flash={quote(('err|' if err else 'ok|') + msg)}; Path=/; Max-Age=30; SameSite=Strict"))
        h += cookies or []
        return Response(303, b"", h)

    def _post(self, conn, ctx, path, headers, body) -> Response:
        form = {k: v[-1] for k, v in parse_qs(body.decode("utf-8"), keep_blank_values=True).items()}
        multi = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        origin = headers.get("origin")
        if origin and urlparse(origin).netloc != headers.get("host"):
            return _html(403, "<h1>Blocked: request came from another site</h1>")
        if not hmac.compare_digest(form.get("csrf", ""), self.csrf):
            return _html(403, "<h1>Blocked: reload the page and try again</h1>")
        back = urlparse(headers.get("referer", "")).path or ("/setup" if path.startswith("/setup") else "/history" if path.startswith("/history") else "/")
        who = ctx.who or "user"
        try:
            if path == "/who":
                return self._redirect(back, cookies=[("Set-Cookie", f"who={quote(form.get('who', ''))}; Path=/; SameSite=Strict; Max-Age=31536000")])
            if path == "/approve":
                ids = [r[0] for r in conn.execute(
                    """SELECT t.id FROM transactions t JOIN batches b ON b.id = t.batch_id
                       WHERE t.category_status = 'proposed' AND t.description_norm = ? AND b.status != 'committed'""", (form["key"],))]
                n = categorize.approve(conn, ids, form["category"], learn=form.get("learn") == "1", actor=who)
                return self._redirect("/review", f"{n} line(s) set to {form['category']}" + (" and remembered" if form.get("learn") == "1" else ""))
            m = path.split("/")
            if len(m) == 4 and m[1] == "item" and m[3] == "comment":
                item_id = int(m[2])
                dx.add_comment(conn, item_id, who, form.get("body", ""))
                if form.get("status"):
                    dx.set_status(conn, item_id, form["status"], who, form.get("follow_up") or None)
                conn.commit()
                return self._redirect(f"/item/{item_id}", "Comment saved")
            if path == "/import":
                rep = importer.import_inbox(self.home, conn, date.fromisoformat(self._today()))
                if not rep.files:
                    return self._redirect("/upload", "The inbox is empty.", err=True)
                bad = [f for f in rep.files if f.status == "unrecognised"]
                dup = [f for f in rep.files if f.status == "duplicate"]
                msg = f"{len(rep.imported)} file(s) imported"
                if dup:
                    msg += f"; {len(dup)} had already been imported, so nothing was added twice"
                if bad:
                    msg += f"; {len(bad)} not recognised ({', '.join(f.name for f in bad[:3])})"
                return self._redirect("/upload", msg, err=bool(bad))
            if len(m) == 4 and m[1] == "imports" and m[3] == "submit":
                try:
                    commits.submit(conn, self.home, int(m[2]), who, form.get("ack") == "1", self._today())
                except commits.NeedsAcknowledgement as exc:
                    return self._redirect("/imports", f"{len(exc.issues)} thing(s) are still open. Tick the box to submit anyway.", err=True)
                sync = sheetsync.sync_if_linked(conn, self._today())
                bad = bool(sync) and sync.startswith("Google Sheet NOT")
                return self._redirect("/imports", f"Import #{m[2]} submitted." + (f" {sync}." if sync else ""), err=bad)
            if len(m) == 4 and m[1] == "imports" and m[3] == "reopen":
                commits.reopen(conn, int(m[2]), form.get("reason", ""), who)
                return self._redirect("/imports", f"Import #{m[2]} reopened.")
            if path == "/balances":
                saved = 0
                for key, value in form.items():
                    if key.startswith("bal_") and value.strip():
                        amount = float(value.replace(",", "").replace("₪", "").replace("$", "").strip())
                        balances.set_balance_for(conn, int(key[4:]), amount, form.get("as_of") or self._today())
                        saved += 1
                return self._redirect("/balances", f"{saved} balance(s) saved")
            back = form.get("back") if form.get("back") in ("/upload", "/sheet") else "/sheet"
            if path == "/sheet/link":
                sheetsync.link(conn, form.get("url", ""))
                res = sheetsync.push(conn, self._today())
                return self._redirect(back, ("Linked. " if res["ok"] else "Saved, but the first update failed: ") + res["detail"], err=not res["ok"])
            if path == "/sheet/sync":
                res = sheetsync.push(conn, self._today())
                return self._redirect(back, ("Updated: " if res["ok"] else "Not updated: ") + res["detail"], err=not res["ok"])
            if path == "/sheet/notes":
                sheetsync.set_notes(conn, form.get("notes") == "1")
                return self._redirect(back, "Saved")
            if path == "/sheet/unlink":
                sheetsync.unlink(conn)
                return self._redirect(back, "Unlinked")
            if path == "/seed/dismiss":
                firstrun.dismiss_notice(conn)
                return self._redirect("/", "")
            if path == "/quit":
                if self.shutdown:
                    threading.Timer(0.5, self.shutdown).start()
                return _html(200, "<!doctype html><meta charset=utf-8><title>Closed</title><body style='font:16px system-ui;max-width:420px;margin:80px auto'>"
                                  "<h2>Finance Tracker has closed.</h2><p>You can close this window. Open the app again whenever you like.</p>")
            if path == "/history/save":
                token = form.get("t", "")
                src = self.home.data / "tmp" / f"history-{token}.xlsx"
                if not TOKEN_RE.match(token) or not src.exists():
                    return self._redirect("/history", "That preview has expired. Choose the file again.", err=True)
                extra = {form[f"src_{i}"]: form[f"dst_{i}"] for i in range(int(form.get("n_maps") or 0))
                         if form.get(f"dst_{i}")}
                res = sheet_import.save(conn, sheet_import.parse_workbook(src), form.get("primary") or "Ely", form.get("partner") or "Shir", extra)
                src.unlink(missing_ok=True)
                if res["batch_id"] is None:
                    return self._redirect("/imports", "Nothing new: this history was already imported.")
                return self._redirect("/imports", f"Old history saved as draft #{res['batch_id']}: {res['transactions']} lines, "
                                                  f"{res['balances']} balances, {res['rules_learned']} rules learned. Review it, then submit.")
            if path == "/setup/starter":
                found = starter.find(self.app_dir)
                if not found:
                    return self._redirect("/setup", "No starter setup file was found.", err=True)
                done = starter.apply(conn, found)
                return self._redirect("/setup", f"Starter setup applied: {done['owners']} people, {done['accounts']} accounts, {done['rules']} rules.")
            if path == "/setup/owner":
                accounts_mod.add_owner(conn, form["name"].strip())
                return self._redirect("/setup", f"{form['name'].strip()} added")
            if path == "/setup/alias":
                accounts_mod.add_alias(conn, form["owner"], form.get("alias", ""))
                return self._redirect("/setup", "Name saved")
            if path == "/setup/alias/remove":
                accounts_mod.remove_alias(conn, int(form["id"]))
                return self._redirect("/setup", "Name removed")
            if path == "/setup/account-owner":
                accounts_mod.set_owner_by_id(conn, int(form["id"]), form.get("owner") or None)
                return self._redirect("/setup", "Saved")
            if path == "/setup/rule":
                dest = None
                if form.get("destination"):
                    dest = conn.execute("SELECT label FROM accounts WHERE id = ?", (int(form["destination"]),)).fetchone()[0]
                categorize.add_rule_from_form(conn, form["pattern"], form.get("category", ""), form.get("mode", "spend"), dest)
                return self._redirect("/setup", "Rule saved")
            if path == "/setup/category/merge":
                res = categorize.merge_category(conn, form["src"], form["dst"])
                return self._redirect("/setup", f"“{form['src']}” merged into “{form['dst']}”: {res['lines']} lines moved")
            if path == "/setup/category/rename":
                categorize.rename_category(conn, form["old"], form.get("new", ""))
                return self._redirect("/setup", f"Renamed to “{form['new'].strip()}”")
            if path == "/setup/rule/toggle":
                categorize.toggle_rule(conn, int(form["id"]))
                return self._redirect("/setup", "Saved")
            if path == "/setup/rule/delete":
                categorize.delete_rule(conn, int(form["id"]))
                return self._redirect("/setup", "Rule deleted")
            if path == "/setup/shortcut":
                exe = Path(sys.executable).with_name("pythonw.exe")
                target = str(exe if exe.exists() else sys.executable)
                lnk = appmode.create_desktop_shortcut(target, "-m ftracker app", str(self.app_dir))
                return self._redirect("/setup", f"Icon added to your desktop ({Path(lnk).name})")
            if path == "/fx/set":
                fx.set_rate(conn, self._today(), "USD", "ILS", float(form["rate"].replace(",", "")), "manual")
                conn.commit()
                return self._redirect("/balances", f"Dollar rate set to {form['rate']}")
            if path == "/wealth/include":
                shown = [int(x) for x in multi.get("shown", []) if x.isdigit()]
                balances.set_in_wealth(conn, shown, {a for a in shown if form.get(f"inc_{a}") == "1"})
                return self._redirect("/wealth", "Saved. Net worth now counts only the accounts you ticked.")
            if path == "/setting/reimbursed":
                summary.set_include_reimbursed(conn, form.get("on") == "1")
                return self._redirect(form.get("next") if form.get("next") in ("/", "/spending") else "/", "Vituri is now " + ("counted in" if form.get("on") == "1" else "left out of") + " spending")
            if path == "/fx/refresh":
                res = fx.refresh_current(conn, self._today(), force=True)
                if res["status"] == "failed":
                    return self._redirect("/balances", "Could not reach a rate source (offline?). Type today's rate instead.", err=True)
                return self._redirect("/balances", f"Dollar rate: ₪{res['rate']:.3f} ({res['source']}, {res['date']})")
            if path == "/accounts":
                accounts_mod.add_account(conn, form["kind"], "manual", form["label"].strip(), form.get("owner") or None, form.get("currency", "ILS"))
                return self._redirect("/balances", "Account added")
            if path == "/goals":
                goals.add_goal(conn, form["name"].strip(), float(form["amount"].replace(",", "")), form.get("currency", "ILS"),
                               form.get("by") or None, int(form["age"]) if form.get("age") else None, form.get("owner") or None,
                               [int(a) for a in multi.get("account", [])], float(form.get("return") or 0) / 100)
                return self._redirect("/goals", "Goal added")
            if path == "/birth":
                goals.set_birth_date(conn, form["owner"], form["birth"])
                return self._redirect("/goals", "Birth date saved")
        except (ValueError, PermissionError, LookupError, KeyError) as exc:
            conn.rollback()
            return self._redirect(back, str(exc.args[0]) if exc.args else "That did not work.", err=True)
        return _html(404, "<h1>Not found</h1>")


def serve(home: Home, port: int = 8765, lan: bool = False, pin: str | None = None, open_browser: bool = True,
          app_mode: bool = False) -> None:
    try:
        _serve(home, port, lan, pin, open_browser, app_mode)
    except Exception:
        import traceback
        print(traceback.format_exc())            # in app mode this lands in app-log.txt
        if sys.stdout is not sys.__stdout__ and sys.__stderr__:
            traceback.print_exc(file=sys.__stderr__)
        raise


def _serve(home: Home, port: int, lan: bool, pin: str | None, open_browser: bool, app_mode: bool) -> None:
    home.ensure()
    log = None
    if app_mode:
        # Started without a console (pythonw): print would crash, so send output to a log file you can send me.
        log = open(home.root / "app-log.txt", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
        port = appmode.PREFERRED_PORT
        if not appmode._free(port):
            if appmode.is_ours(port):                      # already running: just bring up a window
                appmode.open_window(f"http://localhost:{port}/")
                return
            port = appmode.pick_port(port + 1)
    moved = migrate_legacy(home)
    if moved:
        print(moved)
    conn = connect(home.db_path)
    try:
        need_load = firstrun.needed(conn, appmode.app_dir()) or not firstrun.repair_done(conn)
    finally:
        conn.close()
    if lan and not pin:
        pin = f"{secrets.randbelow(10 ** 6):06d}"
    watchdog = appmode.Watchdog(time.time()) if app_mode else None
    app = App(home, pin, lan, watchdog=watchdog)
    app.loading = need_load

    def load_starting_data():
        c = connect(home.db_path)
        try:
            if firstrun.needed(c, appmode.app_dir()):
                firstrun.run(c, home, appmode.app_dir())
            firstrun.repair(c, appmode.app_dir(), home)
        except Exception:
            import traceback
            print(traceback.format_exc())
        finally:
            c.close()
            app.loading = False
    if need_load:             # the window opens at once and shows a loading page, instead of nothing for a minute
        threading.Thread(target=load_starting_data, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, res: Response):
            self.send_response(res.status)
            for k, v in res.headers:
                self.send_header(k, v)
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(res.body)))
            self.end_headers()
            self.wfile.write(res.body)

        def do_GET(self):
            self._send(app.handle("GET", self.path, dict(self.headers)))

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            limit = uploads.MAX_BYTES + 1_000_000 if self.path in UPLOAD_PATHS else 1_000_000
            self._send(app.handle("POST", self.path, dict(self.headers), self.rfile.read(min(n, limit))))

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("0.0.0.0" if lan else "127.0.0.1", port), Handler)
    app.shutdown = httpd.shutdown
    url = f"http://localhost:{port}/"
    if watchdog:
        def watch():
            while True:
                time.sleep(5)
                if watchdog.should_stop(time.time(), app.active):
                    httpd.shutdown()
                    return
        threading.Thread(target=watch, daemon=True).start()
    if app_mode or sys.stdout:
        print(f"Dashboard running at {url}" + ("" if app_mode else "   (Ctrl+C to stop)"))
    if lan:
        print(f"Open on your home network at http://<this computer's address>:{port}/ with PIN {pin}\n"
              "This is plain HTTP on your own network; only use it at home.")
    if open_browser:
        if app_mode:
            threading.Thread(target=appmode.open_window, args=(url,), daemon=True).start()
        else:
            webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if log:
            log.flush()
