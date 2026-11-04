"""Server mode: the app on an always-on machine, reached over a private HTTPS link (Tailscale), with a sign-in for each person.

Passwords are stored as salted scrypt hashes in the settings table. A signed-in browser holds a signed cookie that
expires after 30 days. Too many wrong passwords lock that name and that address out for 15 minutes.
"""
import base64
import hashlib
import hmac
import json
import secrets
import sqlite3
import time

USERS_KEY = "server_users"
SECRET_KEY = "server_secret"
SESSION_SECONDS = 30 * 24 * 3600
MIN_PASSWORD = 10
LOCK_AFTER = 5
LOCK_SECONDS = 15 * 60
N, R, P = 2 ** 14, 8, 1


def _get(conn: sqlite3.Connection, key: str):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def _set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=N, r=R, p=P, dklen=32)
    return f"scrypt${N}${R}${P}${salt.hex()}${h.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """Always does the same work, so a wrong name takes as long as a wrong password."""
    if not stored:
        stored = hash_password("not a real password", b"\0" * 16)
        ok = False
    else:
        ok = True
    try:
        _, n, r, p, salt, want = stored.split("$")
        got = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return ok and hmac.compare_digest(got.hex(), want)
    except (ValueError, TypeError):
        return False


def users(conn: sqlite3.Connection) -> dict[str, str]:
    raw = _get(conn, USERS_KEY)
    return json.loads(raw) if raw else {}


def add_user(conn: sqlite3.Connection, name: str, password: str) -> None:
    name = name.strip()
    if not name or any(c in name for c in "|;,\"'<>\n"):
        raise ValueError("Use a plain name, for example Ely.")
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"The password needs at least {MIN_PASSWORD} characters.")
    u = users(conn)
    u[name] = hash_password(password)
    _set(conn, USERS_KEY, json.dumps(u))
    conn.commit()


def remove_user(conn: sqlite3.Connection, name: str) -> bool:
    u = users(conn)
    found = u.pop(name, None) is not None
    _set(conn, USERS_KEY, json.dumps(u))
    conn.commit()
    return found


def secret(conn: sqlite3.Connection) -> bytes:
    s = _get(conn, SECRET_KEY)
    if not s:
        s = secrets.token_hex(32)
        _set(conn, SECRET_KEY, s)
        conn.commit()
    return bytes.fromhex(s)


def make_session(key: bytes, user: str, now: float) -> str:
    body = base64.urlsafe_b64encode(f"{user}|{int(now + SESSION_SECONDS)}".encode("utf-8")).decode().rstrip("=")
    sig = hmac.new(key, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def read_session(key: bytes, token: str | None, now: float) -> str | None:
    if not token or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(hmac.new(key, body.encode(), hashlib.sha256).hexdigest(), sig):
        return None
    try:
        user, exp = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode("utf-8").split("|")
        return user if float(exp) > now else None
    except (ValueError, UnicodeDecodeError):
        return None


class Lockout:
    """Counts wrong passwords per name and per address."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.fails: dict[str, list[float]] = {}

    def _recent(self, key: str) -> list[float]:
        cutoff = self.clock() - LOCK_SECONDS
        v = [t for t in self.fails.get(key, []) if t > cutoff]
        self.fails[key] = v
        return v

    def locked(self, *keys: str) -> bool:
        return any(len(self._recent(k)) >= LOCK_AFTER for k in keys)

    def fail(self, *keys: str) -> None:
        for k in keys:
            self._recent(k).append(self.clock())

    def clear(self, *keys: str) -> None:
        for k in keys:
            self.fails.pop(k, None)


def login_page(msg: str = "") -> str:
    from html import escape
    return ("<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Sign in · Finance</title>"
            "<style>body{margin:0;background:#efefee;color:#111;font:16px system-ui,sans-serif}main{max-width:340px;margin:12vh auto;padding:0 16px}"
            "h1{font-size:22px;margin:0 0 4px}p{color:#6b6b6e;margin:0 0 16px}form{display:flex;flex-direction:column;gap:10px;background:#fff;padding:20px;border-top:2px solid #111}"
            "input,button{height:44px;font:inherit;padding:0 12px;border:1px solid #c9c9c6;background:#fff}button{background:#111;color:#fff;border-color:#111;font-weight:600}"
            ".err{color:#b3261e;margin:0 0 12px}@media(prefers-color-scheme:dark){body{background:#121212;color:#ededed}form{background:#1b1b1c;border-color:#ededed}"
            "input{background:#1b1b1c;color:#ededed;border-color:#38383b}button{background:#ededed;color:#111}}</style>"
            "<main><h1>Household ledger</h1><p>Sign in to continue.</p>"
            + (f"<p class=err role=alert>{escape(msg)}</p>" if msg else "")
            + "<form method=post action=/login><input name=user placeholder=Name autocomplete=username autocapitalize=words required autofocus>"
              "<input name=password type=password placeholder=Password autocomplete=current-password required><button>Sign in</button></form></main>")
