"""Fetch cards: bring in your credit card lines straight from the card companies' sites, on your computer only.

How it is kept safe:
- The scraper (israeli-bank-scrapers) is installed from a lockfile that ships with the app: every package is pinned to an
  exact version and checked against its hash, and no install scripts run. It is only updated when you press Install.
- It uses the Chrome or Edge you already have, so it downloads no browser of its own.
- Logins come out of the encrypted store (vault.py) one at a time, go to the scraper on stdin, and are never written
  down, logged or put on a command line.
- What comes back is written into the inbox as a .json file of transactions (no login details in it) and imported like
  any statement, so the usual duplicate checks and Review apply.
- It runs only when you press the button, and only from this computer (never from a phone or another device).
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta
from importlib import resources
from pathlib import Path

from . import vault

PACKAGE = "israeli-bank-scrapers"
KIT_FILES = ("package.json", "package-lock.json", "fetch.js")
MARK = "@@FTRACKER-RESULT@@"
MIN_NODE = (22, 22)
STATUS_FILE = "card-fetch-status.json"
FIRST_DAYS, OVERLAP_DAYS = 120, 40
RUN_TIMEOUT, INSTALL_TIMEOUT = 600, 900
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

COMPANIES = {
    "isracard": {"name": "Isracard", "fields": [("id", "ID number (ת.ז.)"), ("card6Digits", "First 6 digits of one of your cards"), ("password", "Password")]},
    "amex": {"name": "American Express", "fields": [("id", "ID number (ת.ז.)"), ("card6Digits", "First 6 digits of one of your cards"), ("password", "Password")]},
    "visaCal": {"name": "Cal", "fields": [("username", "Username"), ("password", "Password")]},
    "max": {"name": "Max", "fields": [("username", "Username"), ("password", "Password")]},
}

ERRORS = {
    "INVALID_PASSWORD": "The site said the login details are wrong. Check them with Change.",
    "CHANGE_PASSWORD": "The site wants a new password. Log in on the site yourself once, change it, then save the new one here.",
    "ACCOUNT_BLOCKED": "The site says the login is blocked. Log in on the site yourself to sort it out.",
    "TIMEOUT": "The site did not answer in time. Try again later, or import a file for this card this month.",
    "TWO_FACTOR_RETRIEVER_MISSING": "The site asked for an SMS code, which a fetch cannot type. Import a file for this card this month.",
}
GENERIC = "The fetch did not work: the site may have changed or blocked the automatic login. Import a file for this card this month."


def check_fields(company: str, fields: dict) -> str | None:
    """A plain reason the login can't be right, or None. Only the shape is checked; nothing is sent anywhere."""
    if company not in COMPANIES:
        return "Pick a card company."
    for key, label in COMPANIES[company]["fields"]:
        if not (fields.get(key) or "").strip():
            return f"Fill in {label}."
    if "id" in fields and not (fields["id"].strip().isdigit() and 5 <= len(fields["id"].strip()) <= 9):
        return "The ID number should be digits only."
    if "card6Digits" in fields and not (fields["card6Digits"].strip().isdigit() and len(fields["card6Digits"].strip()) == 6):
        return "Type exactly the first 6 digits of a card."
    return None


# ---- where things are -------------------------------------------------------------------------------------------

def work_dir() -> Path:
    return vault.base_dir() / "scraper"


def _kit(name: str) -> bytes:
    return resources.files("ftracker").joinpath("card_scraper_kit", name).read_bytes()


def find_node() -> str | None:
    found = shutil.which("node")
    if found:
        return found
    if sys.platform == "win32":
        for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            for sub in (("nodejs", "node.exe"), ("Programs", "nodejs", "node.exe")):
                if base and Path(base, *sub).is_file():
                    return str(Path(base, *sub))
    return None


def node_version(node: str) -> tuple[int, ...] | None:
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW).stdout
        return tuple(int(x) for x in out.strip().lstrip("v").split(".")[:3])
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def find_npm_cli(node: str) -> str | None:
    """npm's own script, run with node (so no .cmd files or shells are involved on Windows)."""
    here = Path(node).resolve().parent
    for cand in (here / "node_modules" / "npm" / "bin" / "npm-cli.js",
                 here.parent / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js"):
        if cand.is_file():
            return str(cand)
    return None


def find_browser() -> str | None:
    override = os.environ.get("FTRACKER_BROWSER")
    if override and Path(override).is_file():
        return override
    if sys.platform == "win32":
        cands = []
        for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                cands += [Path(base, "Google", "Chrome", "Application", "chrome.exe"),
                          Path(base, "Microsoft", "Edge", "Application", "msedge.exe")]
        for c in cands:
            if c.is_file():
                return str(c)
        return None
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        if shutil.which(name):
            return shutil.which(name)
    return None


def installed_version() -> str | None:
    p = work_dir() / "node_modules" / PACKAGE / "package.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError):
        return None


def pinned_version() -> str:
    return json.loads(_kit("package.json"))["dependencies"][PACKAGE]


def _lock_matches() -> bool:
    try:
        return (work_dir() / "package-lock.json").read_bytes() == _kit("package-lock.json")
    except OSError:
        return False


def setup_status() -> dict:
    node = find_node()
    ver = node_version(node) if node else None
    inst = installed_version()
    return {
        "windows": sys.platform == "win32",
        "vault": vault.available(),
        "node": node, "node_version": ".".join(map(str, ver)) if ver else None,
        "node_ok": bool(ver and ver[:2] >= MIN_NODE),
        "npm": bool(node and find_npm_cli(node)),
        "browser": find_browser(),
        "installed": inst, "pinned": pinned_version(),
        "ready": bool(inst == pinned_version() and _lock_matches()),
        "update_waiting": bool(inst and (inst != pinned_version() or not _lock_matches())),
    }


# ---- install ----------------------------------------------------------------------------------------------------

def _copy_kit() -> Path:
    d = work_dir()
    d.mkdir(parents=True, exist_ok=True)
    for name in KIT_FILES:
        (d / name).write_bytes(_kit(name))
    return d


def install() -> tuple[bool, str]:
    """Install exactly the versions in the shipped lockfile (npm ci), with install scripts off and no browser download."""
    node = find_node()
    ver = node_version(node) if node else None
    if not ver or ver[:2] < MIN_NODE:
        return False, "Install Node.js first (the LTS version from nodejs.org), then restart the app and press Install again."
    cli = find_npm_cli(node)
    if not cli:
        return False, "npm was not found next to Node.js. Reinstall Node.js from nodejs.org."
    d = _copy_kit()
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("NPM_CONFIG_", "DEBUG"))}
    env.update({"PUPPETEER_SKIP_DOWNLOAD": "1", "npm_config_ignore_scripts": "true", "npm_config_audit": "false",
                "npm_config_fund": "false", "npm_config_update_notifier": "false"})
    try:
        r = subprocess.run([node, cli, "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--omit=dev"], cwd=d, env=env,
                           capture_output=True, text=True, timeout=INSTALL_TIMEOUT, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return False, "The install took too long. Check the internet connection and press Install again."
    except OSError as exc:
        return False, f"The install could not start ({exc})."
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
        return False, "The install did not finish: " + " / ".join(tail)[:300]
    if installed_version() != pinned_version():
        return False, "The install finished but the expected version is not there. Press Install again."
    return True, f"Installed {PACKAGE} {pinned_version()}, checked against the app's lockfile."


# ---- run --------------------------------------------------------------------------------------------------------

def run_scraper(company: str, credentials: dict, start: date, show_browser: bool = False) -> dict:
    """Run one company. The login goes in on stdin only. Returns the runner's answer (never containing the login)."""
    node = find_node()
    if not node:
        return {"ok": False, "errorType": "SETUP", "errorMessage": "Node.js is not installed"}
    d = work_dir()
    (d / "fetch.js").write_bytes(_kit("fetch.js"))          # the runner always matches this version of the app
    payload = json.dumps({"companyId": company, "credentials": credentials, "startDate": start.isoformat(),
                          "executablePath": find_browser(), "showBrowser": show_browser, "timeoutMs": 90000})
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("DEBUG")}
    try:
        r = subprocess.run([node, str(d / "fetch.js")], input=payload, cwd=d, env=env, capture_output=True, text=True,
                           encoding="utf-8", timeout=RUN_TIMEOUT, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return {"ok": False, "errorType": "TIMEOUT", "errorMessage": ""}
    except OSError as exc:
        return {"ok": False, "errorType": "SETUP", "errorMessage": str(exc)}
    finally:
        payload = None
    out = r.stdout or ""
    if MARK not in out:
        return {"ok": False, "errorType": "CRASH", "errorMessage": (r.stderr or "").strip()[-300:]}
    try:
        return json.loads(out.split(MARK, 1)[1].strip().splitlines()[0])
    except (ValueError, IndexError):
        return {"ok": False, "errorType": "CRASH", "errorMessage": "unreadable answer from the scraper"}


def explain(result: dict) -> str:
    kind = result.get("errorType") or ""
    if kind == "SETUP":
        return "Card fetching is not set up yet (see the steps on this page)."
    return ERRORS.get(kind, GENERIC)


def _status_path() -> Path:
    return vault.base_dir() / STATUS_FILE


def last_status() -> dict:
    try:
        return json.loads(_status_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_status(lid: str, entry: dict) -> None:
    st = last_status()
    st[lid] = entry
    p = _status_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def start_date_for(lid: str, today: date) -> date:
    prev = last_status().get(lid) or {}
    if prev.get("ok") and prev.get("at"):
        try:
            return date.fromisoformat(prev["at"][:10]) - timedelta(days=OVERLAP_DAYS)
        except ValueError:
            pass
    return today - timedelta(days=FIRST_DAYS)


def write_fetch_file(inbox: Path, company: str, person: str, result: dict, when: datetime) -> Path | None:
    """Transactions only. Nothing is written when the site had no final charges in the period."""
    accounts = [{"accountNumber": a.get("accountNumber", ""), "txns": a.get("txns") or []} for a in result.get("accounts") or []]
    if not any(t for a in accounts for t in a["txns"] if (t.get("status") or "completed") == "completed"):
        return None
    inbox.mkdir(parents=True, exist_ok=True)
    safe_person = "".join(ch for ch in person if ch.isalnum()) or "me"
    p = inbox / f"{when:%Y-%m-%d_%H%M%S}_{company}_{safe_person}.cardfetch.json"
    p.write_text(json.dumps({"format": "ftracker-card-fetch", "version": 1, "company": company,
                             "fetched_at": when.isoformat(timespec="seconds"), "accounts": accounts},
                            ensure_ascii=False), encoding="utf-8")
    return p


# ---- the background job -----------------------------------------------------------------------------------------

class Job:
    """One fetch or install at a time, in the background, so the page stays usable and shows progress."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.kind = ""
        self.step = ""
        self.started = self.finished = None
        self.results: list[dict] = []
        self.summary = ""

    def snapshot(self) -> dict:
        with self.lock:
            return {"running": self.running, "kind": self.kind, "step": self.step, "results": list(self.results),
                    "summary": self.summary, "started": self.started, "finished": self.finished}

    def _begin(self, kind: str) -> bool:
        with self.lock:
            if self.running:
                return False
            self.running, self.kind, self.step, self.results, self.summary = True, kind, "starting", [], ""
            self.started, self.finished = time.time(), None
            return True

    def _end(self, summary: str) -> None:
        with self.lock:
            self.running, self.summary, self.step, self.finished = False, summary, "", time.time()

    def start_install(self, hooks=None) -> bool:
        if not self._begin("install"):
            return False

        def work():
            _enter(hooks)
            try:
                self.step = "installing the card scraper (a minute or two)"
                ok, msg = install()
                self.results = [{"name": "Install", "ok": ok, "message": msg}]
                self._end(msg)
            except Exception as exc:                       # the job must always end
                self._end(f"The install stopped: {exc}")
            finally:
                _leave(hooks)
        threading.Thread(target=work, daemon=True).start()
        return True

    def start_fetch(self, home, connect, today: date, pin: str | None, only: list[str] | None = None, hooks=None) -> bool:
        vault.check_pin(pin)                                # a wrong PIN is told at once, not after a minute
        logins = [e for e in vault.list_logins() if not only or e["id"] in only]
        if not logins or not self._begin("fetch"):
            return False

        def work():
            _enter(hooks)
            files: dict[str, str] = {}
            try:
                for e in logins:
                    name = f"{COMPANIES.get(e['company'], {}).get('name', e['company'])} ({e['person']})"
                    self.step = f"fetching {name}"
                    try:
                        creds = vault.load_login(e["id"], pin)
                        res = run_scraper(e["company"], creds, start_date_for(e["id"], today))
                    except vault.VaultError as exc:
                        res = {"ok": False, "errorType": "VAULT", "errorMessage": str(exc)}
                    finally:
                        creds = None
                    now = datetime.now()
                    if res.get("ok"):
                        f = write_fetch_file(home.inbox, e["company"], e["person"], res, now)
                        n = sum(len(a.get("txns") or []) for a in res.get("accounts") or [])
                        row = {"id": e["id"], "name": name, "ok": True, "message": f"{n} line(s) read from the site" if f else "nothing new on the site"}
                        if f:
                            files[f.name] = e["id"]
                    else:
                        msg = ("The saved login could not be opened: " + res.get("errorMessage", "")) if res.get("errorType") == "VAULT" else explain(res)
                        row = {"id": e["id"], "name": name, "ok": False, "message": msg}
                    self.results.append(row)
                    _save_status(e["id"], {"at": now.isoformat(timespec="seconds"), "ok": row["ok"], "message": row["message"]})
                imported = 0
                if files:
                    self.step = "importing what was fetched"
                    from . import importer
                    conn = connect(home.db_path)
                    try:
                        rep = importer.import_inbox(home, conn, today)
                    finally:
                        conn.close()
                    for fr in rep.files:
                        lid = files.get(fr.name)
                        for row in self.results:
                            if row.get("id") == lid:
                                if fr.status == "imported":
                                    row["message"] += f"; {fr.new_txns} new, {fr.skipped_txns} already here"
                                    imported += fr.new_txns
                                elif fr.status == "unrecognised":
                                    row["ok"], row["message"] = False, "Fetched, but the result could not be imported: " + fr.detail
                bad = sum(1 for r in self.results if not r["ok"])
                self._end(f"{imported} new line(s) brought in" + (f"; {bad} card company(ies) did not work" if bad else "")
                          + (". New lines are waiting in Review." if imported else "."))
            except Exception as exc:
                self._end(f"The fetch stopped: {exc}")
            finally:
                _leave(hooks)
        threading.Thread(target=work, daemon=True).start()
        return True


def _enter(hooks):
    if hooks and hooks.get("enter"):
        hooks["enter"]()


def _leave(hooks):
    if hooks and hooks.get("leave"):
        hooks["leave"]()


JOB = Job()
