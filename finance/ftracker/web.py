"""The local dashboard. Serves on this computer only unless you ask for --lan (then a PIN is required)."""
import hashlib
import hmac
import json
import sqlite3
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

from . import appmode, balances, categorize, budgets, closing, cloudbackup, commits, datafixes, firstrun, fx, goals, importer, sheet_import, sheetsync, phone_cloud, starter, summary, watchfolder, uploads, views, views_home, views_budget, views_close, views_overview, views_setup, views_trends
from . import accounts as accounts_mod
from . import discrepancies as dx
from .config import Home, migrate_legacy
from .db import connect

PHONE_PAGES = {"/", "/spending", "/trends", "/wealth", "/networth", "/health", "/ping"}
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
                 app_dir: Path | None = None, watchdog: "appmode.Watchdog | None" = None, clock=time.time,
                 phone_pin: str | None = None, port: int = 8765):
        self.home, self.pin, self.lan, self.fixed_today = home, pin, lan, today
        self.phone = bool(phone_pin)              # phone mode: this computer is unrestricted, other devices need the PIN and may only look
        if self.phone:
            self.pin, self.lan = phone_pin, True
        self.port = port
        self._fails: dict[str, tuple[int, float]] = {}
        self._push_timer: threading.Timer | None = None
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
        client = headers.get("x-client-ip", "")
        remote = bool(client) and client not in ("127.0.0.1", "::1")
        if (not self.lan or (self.phone and not remote)) and host not in LOCAL_HOSTS:
            return _html(421, "<h1>Wrong host</h1>")         # blocks DNS-rebinding style access
        cookies = SimpleCookie(headers.get("cookie", ""))
        url = urlparse(raw_path)
        path, q = url.path, {k: v[-1] for k, v in parse_qs(url.query).items()}

        if self.phone and not remote:
            pass                                                          # this computer: no PIN
        elif self.pin:
            if path == "/login" and method == "POST":
                count, until = self._fails.get(client, (0, 0.0))
                if count >= 5 and self.clock() < until:
                    return _html(429, self._login_page("Too many wrong PINs. Wait a minute."))
                form = {k: v[-1] for k, v in parse_qs(body.decode("utf-8")).items()}
                if hmac.compare_digest(form.get("pin", ""), self.pin):
                    self._fails.pop(client, None)
                    return Response(303, b"", [("Location", "/"), ("Set-Cookie", f"auth={self._auth_token()}; HttpOnly; SameSite=Strict; Path=/")])
                self._fails[client] = (count + 1, self.clock() + 60)
                return _html(403, self._login_page("Wrong PIN."))
            if not (cookies.get("auth") and hmac.compare_digest(cookies["auth"].value, self._auth_token())):
                return _html(401, self._login_page())
        if remote and self.phone and not (method == "GET" and path in PHONE_PAGES):
            return _html(403, "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                              "<body style='font:16px system-ui;max-width:420px;margin:60px auto;padding:0 16px'><h2>View only</h2>"
                              "<p>From a phone you can look at the Overview and Wealth. Changes are made on the computer.</p><p><a href='/'>Back to the overview</a></p>")

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
                            data_root=str(self.home.root), starter=starter.find(self.app_dir) is not None, can_shortcut=appmode.WINDOWS,
                            readonly=remote and self.phone, closed=frozenset(closing.closed_months(conn)))
            ph = conn.execute("SELECT value FROM settings WHERE key = 'phone_pin'").fetchone()
            if ph and conn.execute("SELECT value FROM settings WHERE key = 'phone_access'").fetchone():
                ip = appmode.lan_address()
                ctx.phone_pin = ph[0]
                ctx.phone_url = f"http://{ip}:{self.port}/" if ip else ""
                ts = appmode.tailscale_address()
                ctx.phone_anywhere = f"http://{ts}:{self.port}/" if ts else ""
            if method == "POST":
                res = self._post(conn, ctx, path, headers, body)
                if res.status in (200, 303) and not path.startswith("/phone/cloud"):
                    self._schedule_push()
                return res
            if path in ("/", "/balances", "/wealth") and not self.fixed_today:
                try:
                    fx.ensure_fresh(conn, self._today())      # at most every few hours; offline is fine
                except Exception:
                    pass
            if path in ("/", "/upload"):
                try:
                    note = watchfolder.scan(conn, self.home, date.fromisoformat(self._today()))
                except Exception:
                    import traceback
                    print(traceback.format_exc())
                    note = ""
                if note and not ctx.flash:
                    ctx.flash, ctx.flash_kind = note, "ok"
                try:
                    cloudbackup.maybe_run(conn, self.home)
                except Exception:
                    pass
            res = self._get(conn, ctx, path, q)
            if flash:
                res.headers.append(("Set-Cookie", "flash=; Max-Age=0; Path=/"))
            return res
        finally:
            conn.close()
            self.active -= 1

    def _schedule_push(self) -> None:
        """After you change something, refresh the phone page a little later (one send for a burst of changes)."""
        if self.fixed_today:
            return
        if self._push_timer:
            self._push_timer.cancel()
        self._push_timer = threading.Timer(20.0, self.push_phone_now)
        self._push_timer.daemon = True
        self._push_timer.start()

    def push_phone_now(self) -> None:
        try:
            c = connect(self.home.db_path)
            try:
                phone_cloud.maybe_push(c, self._today())
            finally:
                c.close()
        except Exception:
            import traceback
            print(traceback.format_exc())

    def _login_page(self, msg: str = "") -> str:
        return (f"<!doctype html><meta charset=utf-8><title>Finance</title><body style='font:16px system-ui;max-width:320px;margin:60px auto'>"
                f"<h2>Finance</h2><p>{msg}</p><form method=post action=/login><input name=pin type=password placeholder=PIN autofocus> "
                f"<button>Open</button></form>")

    def _get(self, conn, ctx, path, q) -> Response:
        if path == "/":
            return _html(200, views_overview.overview_page(conn, ctx, q))
        if path == "/dashboard":
            return _html(200, views.dashboard(conn, ctx, q))
        if path == "/upload":
            return _html(200, views_home.upload_page(conn, ctx))
        if path in ("/spending", "/trends"):
            return _html(200, views_overview.overview_page(conn, ctx, q))
        if path in ("/wealth", "/networth"):
            return _html(200, views_home.wealth_page(conn, ctx, q))
        if path == "/budgets":
            return _html(200, views_budget.budgets_page(conn, ctx))
        if path == "/close":
            return _html(200, views_close.close_page(conn, ctx, q))
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

    @staticmethod
    def _auto_pass(conn) -> int:
        """Re-suggest the waiting lines now that you approved more; returns how many became approved by themselves."""
        def n():
            return conn.execute("SELECT COUNT(*) FROM transactions WHERE category_status = 'approved' AND proposal_basis LIKE 'approved by itself%'").fetchone()[0]
        before = n()
        categorize.resuggest(conn)
        conn.commit()
        return n() - before

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
            if path == "/approve/bulk":
                picks = []
                for raw in multi.get("sel", []):
                    try:
                        key, cat = json.loads(raw)
                        picks.append((str(key), str(cat)))
                    except (ValueError, TypeError):
                        continue
                if not picks:
                    return self._redirect("/review", "Tick at least one merchant first.", err=True)
                chosen = form.get("category", "")
                learn = form.get("learn") == "1"
                done = skipped = 0
                for key, cat in picks:
                    target = chosen or cat
                    if target == "Uncategorised":
                        skipped += 1
                        continue
                    ids = [r[0] for r in conn.execute(
                        """SELECT t.id FROM transactions t JOIN batches b ON b.id = t.batch_id JOIN categories c ON c.id = t.category_id
                           WHERE t.category_status = 'proposed' AND t.description_norm = ? AND c.name = ? AND b.status != 'committed'""", (key, cat))]
                    if ids:
                        done += categorize.approve(conn, ids, target, learn=learn, actor=who)
                auto = self._auto_pass(conn)
                msg = f"{done} line(s) approved from {len(picks) - skipped} merchant(s)" + (" and remembered" if learn else "")
                if skipped:
                    msg += f". {skipped} had no suggestion: pick a category for them"
                if auto:
                    msg += f". {auto} more approved by themselves (merchants you have approved 3+ times)"
                return self._redirect("/review", msg, err=bool(skipped and not done))
            if path == "/approve/suggested":
                groups = conn.execute(
                    """SELECT t.description_norm AS key, c.name AS cat FROM transactions t JOIN categories c ON c.id = t.category_id
                       JOIN batches b ON b.id = t.batch_id
                       WHERE t.category_status = 'proposed' AND b.status != 'committed' AND c.name != 'Uncategorised'
                       GROUP BY t.description_norm, c.name""").fetchall()
                done = 0
                for g in groups:
                    ids = [r[0] for r in conn.execute(
                        """SELECT t.id FROM transactions t JOIN batches b ON b.id = t.batch_id JOIN categories c ON c.id = t.category_id
                           WHERE t.category_status = 'proposed' AND t.description_norm = ? AND c.name = ? AND b.status != 'committed'""", (g["key"], g["cat"]))]
                    if ids:
                        done += categorize.approve(conn, ids, g["cat"], learn=False, actor=who)
                left = conn.execute("SELECT COUNT(DISTINCT description_norm) FROM transactions t JOIN batches b ON b.id = t.batch_id "
                                    "WHERE t.category_status = 'proposed' AND b.status != 'committed'").fetchone()[0]
                return self._redirect("/review", f"{done} line(s) approved as suggested. {left} merchant(s) still need a category from you.")
            if path == "/approve":
                ids = [r[0] for r in conn.execute(
                    """SELECT t.id FROM transactions t JOIN batches b ON b.id = t.batch_id
                       WHERE t.category_status = 'proposed' AND t.description_norm = ? AND b.status != 'committed'""", (form["key"],))]
                n = categorize.approve(conn, ids, form["category"], learn=form.get("learn") == "1", actor=who)
                auto = self._auto_pass(conn)
                return self._redirect("/review", f"{n} line(s) set to {form['category']}" + (" and remembered" if form.get("learn") == "1" else "")
                                      + (f". {auto} more approved by themselves" if auto else ""))
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
                as_of = form.get("as_of") or self._today()
                kept = 0
                for p_ in balances.pending(conn, as_of):
                    key = f"bal_{p_['account']['id']}"
                    if form.get(key, "").strip() or not p_["latest"]:
                        continue
                    if form.get(f"same_{p_['account']['id']}") == "1" or (form.get("same_all") == "1" and not p_["folded"] and not p_["current"]):
                        balances.set_balance_for(conn, p_["account"]["id"], p_["latest"]["amount"], as_of)
                        kept += 1
                return self._redirect("/balances", f"{saved} balance(s) saved" + (f", {kept} kept as they were" if kept else ""))
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
                self.push_phone_now()                     # leave the phone page up to date before closing
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
            if path == "/phone/cloud/on":
                phone_cloud.turn_on(conn)
                res = sheetsync.push(conn, self._today()) if sheetsync.status(conn)["linked"] else {"ok": False, "detail": "The Google Sheet is not linked yet."}
                return self._redirect("/setup", ("Phone page is on and the first numbers were sent." if res["ok"] else "Turned on, but sending failed: " + res["detail"]), err=not res["ok"])
            if path == "/phone/cloud/send":
                res = sheetsync.push(conn, self._today())
                if res["ok"]:
                    sheetsync._set_setting(conn, "cloud_hash", phone_cloud._digest(phone_cloud.payload_extras(conn, self._today())))
                    conn.commit()
                return self._redirect("/setup", "Sent to your phone page." if res["ok"] else "Not sent: " + res["detail"], err=not res["ok"])
            if path == "/phone/cloud/off":
                phone_cloud.turn_off(conn)
                return self._redirect("/setup", "The phone page is turned off. It still holds the last numbers until you delete the Sheet's hidden _phone tab.")
            if path == "/phone/on":
                pin = f"{secrets.randbelow(10 ** 6):06d}"
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('phone_access', '1')")
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('phone_pin', ?)", (pin,))
                conn.commit()
                return self._redirect("/setup", "Phone access is set. Close the app with Quit and open it again to start it.")
            if path == "/phone/off":
                conn.execute("DELETE FROM settings WHERE key IN ('phone_access', 'phone_pin')")
                conn.commit()
                return self._redirect("/setup", "Phone access is off. Close the app with Quit and open it again.")
            if path == "/accounts/arrange":
                ids = [int(x) for x in form.get("order", "").split(",") if x.isdigit()]
                balances.set_arrangement(conn, ids, int(form.get("above", "0") or 0))
                return Response(200, b"ok", [("Content-Type", "text/plain")])
            if path in ("/accounts/move", "/accounts/rename", "/accounts/close", "/accounts/delete"):
                try:
                    aid = int(form["id"])
                    if path == "/accounts/move":
                        accounts_mod.move_account(conn, aid, "up" if form.get("dir") == "up" else "down")
                        return self._redirect("/balances", "")
                    if path == "/accounts/rename":
                        accounts_mod.rename_account(conn, aid, form.get("label", ""))
                        return self._redirect("/balances", "Renamed")
                    if path == "/accounts/close":
                        date.fromisoformat(form.get("on", ""))
                        accounts_mod.close_account(conn, aid, form["on"])
                        return self._redirect("/balances", "Closed. It stays in your history before that date.")
                    accounts_mod.delete_account(conn, aid)
                    return self._redirect("/balances", "Deleted")
                except (ValueError, KeyError) as exc:
                    return self._redirect("/balances", str(exc), err=True)
            if path == "/setup/card-payer":
                try:
                    accounts_mod.set_card_payer(conn, int(form["card"]), form.get("payer", ""))
                except (ValueError, KeyError) as exc:
                    return self._redirect("/setup", str(exc), err=True)
                return self._redirect("/setup", "Saved")
            if path == "/watch/set":
                try:
                    chosen = watchfolder.set_folder(conn, self.home, form.get("folder", ""))
                except ValueError as exc:
                    return self._redirect("/setup", str(exc), err=True)
                note = watchfolder.scan(conn, self.home, date.fromisoformat(self._today()), force=True)
                return self._redirect("/upload", f"Watching {chosen}. " + (note or "Nothing new to import yet."))
            if path == "/watch/check":
                note = watchfolder.scan(conn, self.home, date.fromisoformat(self._today()), force=True)
                return self._redirect("/upload", note or "Nothing new in the folder.")
            if path == "/budgets/save":
                amounts: dict[str, float] = {}
                for cat, amt in zip(multi.get("cat", []), multi.get("amt", [])):
                    amt = amt.replace(",", "").replace("₪", "").strip()
                    if not amt:
                        continue
                    try:
                        amounts[cat] = float(amt)
                    except ValueError:
                        return self._redirect("/budgets", f"“{amt}” is not a number.", err=True)
                n = budgets.save(conn, amounts)
                return self._redirect("/", f"{n} budget(s) saved")
            if path == "/budgets/fill":
                months = summary.months_available(conn)
                done = [m for m in months if m < self._today()[:7]]
                usual = budgets.usual(conn, (done or months)[-6:])
                n = budgets.save(conn, {c: -(-v // 50) * 50 for c, v in usual.items() if c != "Uncategorised" and v >= 50})
                return self._redirect("/budgets", f"Filled in {n} budget(s) from your usual spending. Change any of them and save.")
            if path == "/backup/set":
                try:
                    chosen = cloudbackup.set_folder(conn, self.home, form.get("folder", ""))
                except ValueError as exc:
                    return self._redirect("/setup", str(exc), err=True)
                note = cloudbackup.run(conn, self.home, "first")
                return self._redirect("/setup", note or f"Backing up to {chosen}", err="try again" in note)
            if path == "/backup/now":
                note = cloudbackup.run(conn, self.home, "manual")
                return self._redirect("/setup", note or "Choose a backup folder first.", err=not note or "try again" in note)
            if path == "/backup/clear":
                cloudbackup.clear(conn)
                return self._redirect("/setup", "No longer backing up to a folder.")
            if path == "/backup/restore":
                if form.get("sure") != "1" or "|" not in form.get("pick", ""):
                    return self._redirect("/setup", "Tick the box to confirm, then choose a backup.", err=True)
                where, name = form["pick"].split("|", 1)
                try:
                    msg = cloudbackup.restore(conn, self.home, where, name)
                except (ValueError, sqlite3.Error, OSError) as exc:
                    return self._redirect("/setup", f"Could not restore: {exc}", err=True)
                return self._redirect("/", msg)
            if path == "/watch/clear":
                watchfolder.clear(conn)
                return self._redirect("/setup", "No longer watching a folder.")
            if path == "/wealth/include":
                shown = [int(x) for x in multi.get("shown", []) if x.isdigit()]
                balances.set_in_wealth(conn, shown, {a for a in shown if form.get(f"inc_{a}") == "1"})
                return self._redirect("/wealth", "Saved. Net worth now counts only the accounts you ticked.")
            if path in ("/line/category", "/line/leaveout") and form.get("id", "").isdigit() and not form.get("ok_closed"):
                row = conn.execute("SELECT budget_month FROM transactions WHERE id = ?", (int(form["id"]),)).fetchone()
                if row and closing.is_closed(conn, row[0]):
                    return self._redirect("/", f"{row[0]} is closed. Reopen it first, or tick “this month is closed” on the payment.", err=True)
            if path == "/line/category":
                back = form.get("back", "/")
                if not back.startswith("/") or back.startswith("//") or "\n" in back or "://" in back:
                    back = "/"
                try:
                    categorize.approve(conn, [int(form["id"])], form["category"], learn=form.get("learn") == "1", actor=who)
                except (ValueError, KeyError, PermissionError) as exc:
                    return self._redirect(back, str(exc), err=True)
                return self._redirect(back, f"Moved to {form['category']}")
            if path == "/setting/reimbursed":
                summary.set_include_reimbursed(conn, form.get("on") == "1")
                return self._redirect(form.get("next") if form.get("next") in ("/", "/spending") else "/", "Vituri is now " + ("counted in" if form.get("on") == "1" else "left out of") + " spending")
            if path == "/close/do":
                m = form.get("m", "")
                if len(m) != 7:
                    return self._redirect("/", "Pick a month.", err=True)
                closing.set_closed(conn, m, form.get("reopen") != "1")
                return self._redirect("/" if form.get("reopen") != "1" else f"/close?m={m}",
                                      f"{m} is closed" if form.get("reopen") != "1" else f"{m} is open again")
            if path == "/line/leaveout":
                back = form.get("back", "/")
                if not back.startswith("/") or back.startswith("//") or "\n" in back or "://" in back:
                    back = "/"
                try:
                    summary.set_left_out(conn, int(form["id"]), form.get("on") == "1")
                except (ValueError, KeyError):
                    return self._redirect(back, "Pick a payment.", err=True)
                return self._redirect(back, "Left out of the totals" if form.get("on") == "1" else "Counted again")
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
        need_load = firstrun.needed(conn, appmode.app_dir()) or not firstrun.repair_done(conn) or bool(datafixes.pending(conn, appmode.app_dir()))
        phone_on = conn.execute("SELECT value FROM settings WHERE key = 'phone_access'").fetchone()
        phone_pin = (conn.execute("SELECT value FROM settings WHERE key = 'phone_pin'").fetchone() or [None])[0] if phone_on else None
    finally:
        conn.close()
    if lan and not pin:
        pin = f"{secrets.randbelow(10 ** 6):06d}"
    watchdog = appmode.Watchdog(time.time()) if app_mode else None
    app = App(home, pin if not phone_pin else None, lan or bool(phone_pin), watchdog=watchdog, phone_pin=phone_pin, port=port)
    app.loading = need_load

    def load_starting_data():
        c = connect(home.db_path)
        try:
            if firstrun.needed(c, appmode.app_dir()):
                firstrun.run(c, home, appmode.app_dir())
            firstrun.repair(c, appmode.app_dir(), home)
            datafixes.apply(c, home, appmode.app_dir())
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

        def _headers(self):
            h = dict(self.headers)
            h["X-Client-IP"] = self.client_address[0]            # set here, never taken from the request
            return h

        def do_GET(self):
            self._send(app.handle("GET", self.path, self._headers()))

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            limit = uploads.MAX_BYTES + 1_000_000 if self.path in UPLOAD_PATHS else 1_000_000
            self._send(app.handle("POST", self.path, self._headers(), self.rfile.read(min(n, limit))))

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("0.0.0.0" if (lan or phone_pin) else "127.0.0.1", port), Handler)
    app.shutdown = httpd.shutdown
    url = f"http://localhost:{port}/"
    if watchdog:
        def watch():
            while True:
                time.sleep(5)
                if watchdog.should_stop(time.time(), app.active):
                    app.push_phone_now()
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
