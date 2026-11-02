"""Balances from a screenshot: find the amounts in a picture, match them to your accounts, and save what you approve."""
import hashlib
import json
import re
import sqlite3
from difflib import SequenceMatcher
from pathlib import Path
from statistics import median

from . import balances, fx, ocr
from .config import Home

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
MAX_BYTES = 15 * 1024 * 1024
DONE_KEY = "screens_done"
_MONEY = re.compile(r"^(?P<a>[₪$€])?(?P<neg>-)?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+\.\d{1,2}|\d+)(?P<b>[₪$€])?$")
_COUNT = re.compile(r"^\d+\s*(חשבונות|חשבון|תיקים|תיק|מוצרים|מוצר|כרטיסים|כרטיס|קופות|קופה)$")
_SKIP = ("שער", "שינוי", "מועד", "תאריך", "דמי", "הפקדה", "נזילות", "פוליסה", "צפוי", "יתרה משוערת", "שווי נכסים", "חיובים קרובים", "יתרה כוללת", "%")
_SYMBOL = {"₪": "ILS", "$": "USD", "€": "EUR"}


def dir_of(home: Home) -> Path:
    d = home.root / "screens"
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_name(name: str) -> str | None:
    name = Path(name).name
    if not re.fullmatch(r"[\w\-. ()]{1,120}", name) or Path(name).suffix.lower() not in IMAGE_EXT:
        return None
    return name


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def done(conn: sqlite3.Connection) -> set[str]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (DONE_KEY,)).fetchone()
    return set(json.loads(row[0])) if row else set()


def mark_done(conn: sqlite3.Connection, path: Path) -> None:
    """The picture is dealt with: remember it (so the same file in your folder is not picked up again) and put it away."""
    import shutil
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (DONE_KEY, json.dumps(sorted(done(conn) | {sha(path)})[-500:])))
    conn.commit()
    away = path.parent / "done"
    away.mkdir(exist_ok=True)
    target = away / path.name
    n = 2
    while target.exists():
        target = away / f"{path.stem}-{n}{path.suffix}"
        n += 1
    shutil.move(str(path), str(target))


def save_upload(home: Home, name: str, data: bytes) -> str:
    clean = safe_name(name)
    if not clean:
        raise ValueError(f"{name}: only pictures (.png, .jpg, .jpeg, .webp, .bmp) are accepted")
    if not data or len(data) > MAX_BYTES:
        raise ValueError(f"{name}: empty or too large")
    d = dir_of(home)
    dest, n = d / clean, 2
    while dest.exists():
        dest = d / f"{Path(clean).stem}-{n}{Path(clean).suffix}"
        n += 1
    dest.write_bytes(data)
    return dest.name


def waiting_names(home: Home) -> list[str]:
    """Pictures that came in (from your folder or the Upload page) and have not been checked yet, newest first."""
    d = dir_of(home)
    return [p.name for p in sorted((p for p in d.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT), key=lambda p: p.stat().st_mtime, reverse=True)]


def pick_up(conn: sqlite3.Connection, home: Home, src: Path) -> bool:
    """Copy a picture from your watched folder, unless it was handled before. True when it is new."""
    h = sha(src)
    if h in done(conn) or any(sha(p) == h for p in dir_of(home).iterdir() if p.is_file()):
        return False
    save_upload(home, src.name, src.read_bytes())
    return True


# ---- reading the amounts -------------------------------------------------------------------------------

def _rows(words: list[dict]) -> list[list[dict]]:
    if not words:
        return []
    tall = median(w["h"] for w in words) or 10.0
    out: list[list[dict]] = []
    for w in sorted(words, key=lambda w: w["y"] + w["h"] / 2):
        yc = w["y"] + w["h"] / 2
        if out and abs(yc - sum(x["y"] + x["h"] / 2 for x in out[-1]) / len(out[-1])) <= 0.6 * tall:
            out[-1].append(w)
        else:
            out.append([w])
    return out


def _amount(text: str):
    m = _MONEY.match(text.strip())
    if not m:
        return None
    sym = m.group("a") or m.group("b")
    num = m.group("num")
    if not (sym or "." in num or "," in num):
        return None
    value = float(num.replace(",", "")) * (-1 if m.group("neg") else 1)
    return value, _SYMBOL.get(sym or "₪", "ILS")


def extract(words: list[dict]) -> list[dict]:
    """The amounts on the picture, each with the words beside it and the section it sits under."""
    rows = []
    for r in _rows(words):
        r = sorted(r, key=lambda w: -w["x"])                             # right to left, as Hebrew is read
        money, label = [], []
        for i, w in enumerate(r):
            a = _amount(w["text"])
            if a is None and w["text"] in _SYMBOL and i + 1 < len(r):    # a lone currency sign next to its number
                a2 = _amount(r[i + 1]["text"])
                a = (a2[0], _SYMBOL[w["text"]]) if a2 else None
            if a:
                money.append(a)
            elif w["text"] not in _SYMBOL:
                label.append(w["text"])
        rows.append({"money": money, "label": re.sub(r"\s+", " ", " ".join(label)).strip(), "y": min(w["y"] for w in r)})
    out, section = [], ""
    for i, r in enumerate(rows):
        if _COUNT.match(r["label"]) and i > 0:
            head = out[-1] if out and out[-1]["y"] == rows[i - 1]["y"] else None
            section = rows[i - 1]["label"]
            if head:
                head["total"], head["section"] = True, ""
            continue
        if not r["money"]:
            continue
        skip = any(s in r["label"] for s in _SKIP)
        for value, cur in r["money"][:1]:
            out.append({"amount": value, "currency": cur, "label": r["label"], "section": section, "total": skip or not r["label"], "y": r["y"]})
    return out


def _norm(text: str) -> str:
    return re.sub(r"[^\w ]", "", text).strip().lower()


def suggest(conn: sqlite3.Connection, section: str, label: str) -> int | None:
    """The account this line was matched to before, or the account whose name it resembles."""
    key = _norm(f"{section}|{label}")
    best, who = 0.0, None
    for r in conn.execute("SELECT section, label, account_id FROM screen_aliases"):
        score = SequenceMatcher(None, key, _norm(f"{r['section']}|{r['label']}")).ratio()
        if score > best:
            best, who = score, r["account_id"]
    if best >= 0.82:
        return who
    words = {w for w in _norm(label).split() if len(w) > 2}
    cand = []
    for a in conn.execute("SELECT id, label FROM accounts WHERE active = 1 AND kind != 'card' AND issuer NOT IN ('sheet', 'cash')"):
        names = {w for w in _norm(a["label"]).split() if len(w) > 2}
        if words and names and len(words & names) / len(names) >= 0.6:
            cand.append(a["id"])
    return cand[0] if len(cand) == 1 else None


def remember(conn: sqlite3.Connection, section: str, label: str, account_id: int) -> None:
    conn.execute("INSERT OR REPLACE INTO screen_aliases (section, label, account_id) VALUES (?, ?, ?)", (section, label, account_id))


def to_account_currency(conn: sqlite3.Connection, amount: float, shown: str, account_currency: str, today: str) -> float:
    """The amount in the account's own currency (a dollar figure for a shekel account is converted at the current rate)."""
    if shown == account_currency:
        return amount
    rate = fx.current_rate(conn)
    if not rate:
        return amount
    r = rate["rate"]
    if shown == "USD" and account_currency == "ILS":
        return round(amount * r, 2)
    if shown == "ILS" and account_currency == "USD":
        return round(amount / r, 2)
    return amount


def approve(conn: sqlite3.Connection, rows: list[dict], as_of: str) -> int:
    """rows: account_id, amount, shown currency, and optionally the section and label to remember."""
    saved = 0
    for r in rows:
        acct = conn.execute("SELECT id, currency FROM accounts WHERE id = ?", (r["account_id"],)).fetchone()
        if not acct:
            continue
        amount = to_account_currency(conn, r["amount"], r.get("currency", acct["currency"]), acct["currency"], as_of)
        balances.set_balance_for(conn, acct["id"], amount, as_of)
        if r.get("label"):
            remember(conn, r.get("section", ""), r["label"], acct["id"])
        saved += 1
    conn.commit()
    return saved
