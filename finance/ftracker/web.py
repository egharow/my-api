"""The local dashboard. Serves on this computer only unless you ask for --lan (then a PIN is required)."""
import hashlib
import hmac
import secrets
import webbrowser
from dataclasses import dataclass, field
from datetime import date
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import balances, categorize, commits, fx, goals, importer, sheetsync, views
from . import accounts as accounts_mod
from . import discrepancies as dx
from .config import Home
from .db import connect

LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
CSP = ("default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; "
       "form-action 'self'; base-uri 'none'; frame-ancestors 'none'")


@dataclass
class Response:
    status: int
    body: bytes
    headers: list[tuple[str, str]] = field(default_factory=list)


def _html(status: int, text: str, extra=None) -> Response:
    return Response(status, text.encode("utf-8"), [("Content-Type", "text/html; charset=utf-8")] + (extra or []))


class App:
    def __init__(self, home: Home, pin: str | None = None, lan: bool = False, today: str | None = None):
        self.home, self.pin, self.lan, self.fixed_today = home, pin, lan, today
        self.csrf = secrets.token_urlsafe(24)
        self._secret = secrets.token_bytes(32)

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

        conn = connect(self.home.db_path)
        try:
            owners = [r[0] for r in conn.execute("SELECT name FROM owners ORDER BY name")]
            who = unquote(cookies["who"].value) if cookies.get("who") else (owners[0] if owners else "")
            flash = unquote(cookies["flash"].value) if cookies.get("flash") else ""
            ctx = views.Ctx(today=self._today(), who=who, csrf=self.csrf, owners=owners,
                            flash=flash.split("|", 1)[-1] if flash else "", flash_kind="err" if flash.startswith("err|") else "ok",
                            inbox_count=sum(1 for p in self.home.inbox.iterdir() if p.is_file()) if self.home.inbox.exists() else 0)
            if method == "POST":
                return self._post(conn, ctx, path, headers, body)
            if path in ("/", "/balances") and not self.fixed_today:
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

    def _login_page(self, msg: str = "") -> str:
        return (f"<!doctype html><meta charset=utf-8><title>Finance</title><body style='font:16px system-ui;max-width:320px;margin:60px auto'>"
                f"<h2>Finance</h2><p>{msg}</p><form method=post action=/login><input name=pin type=password placeholder=PIN autofocus> "
                f"<button>Open</button></form>")

    def _get(self, conn, ctx, path, q) -> Response:
        if path == "/":
            return _html(200, views.dashboard(conn, ctx, q))
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
        return _html(404, "<h1>Not found</h1>")

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
        back = urlparse(headers.get("referer", "/")).path or "/"
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
                    return self._redirect("/imports", "The inbox is empty.", err=True)
                bad = [f for f in rep.files if f.status == "unrecognised"]
                msg = f"{len(rep.imported)} file(s) imported" + (f"; {len(bad)} not recognised (see inbox/_unrecognised)" if bad else "")
                return self._redirect("/imports", msg, err=bool(bad))
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
            if path == "/sheet/link":
                sheetsync.link(conn, form.get("url", ""))
                res = sheetsync.push(conn, self._today())
                return self._redirect("/sheet", ("Linked. " if res["ok"] else "Saved, but the first update failed: ") + res["detail"], err=not res["ok"])
            if path == "/sheet/sync":
                res = sheetsync.push(conn, self._today())
                return self._redirect("/sheet", ("Updated: " if res["ok"] else "Not updated: ") + res["detail"], err=not res["ok"])
            if path == "/sheet/notes":
                sheetsync.set_notes(conn, form.get("notes") == "1")
                return self._redirect("/sheet", "Saved")
            if path == "/sheet/unlink":
                sheetsync.unlink(conn)
                return self._redirect("/sheet", "Unlinked")
            if path == "/fx/set":
                fx.set_rate(conn, self._today(), "USD", "ILS", float(form["rate"].replace(",", "")), "manual")
                conn.commit()
                return self._redirect("/balances", f"Dollar rate set to {form['rate']}")
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


def serve(home: Home, port: int = 8765, lan: bool = False, pin: str | None = None, open_browser: bool = True) -> None:
    home.ensure()
    connect(home.db_path).close()
    if lan and not pin:
        pin = f"{secrets.randbelow(10 ** 6):06d}"
    app = App(home, pin, lan)

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
            self._send(app.handle("POST", self.path, dict(self.headers), self.rfile.read(min(n, 1_000_000))))

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("0.0.0.0" if lan else "127.0.0.1", port), Handler)
    url = f"http://localhost:{port}/"
    print(f"Dashboard running at {url}   (Ctrl+C to stop)")
    if lan:
        print(f"Open on your home network at http://<this computer's address>:{port}/ with PIN {pin}\n"
              "This is plain HTTP on your own network; only use it at home.")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
