"""Dollar rate. Only your assets need it: expenses are always in shekels because the card charges
the shekel amount. By default every dollar balance is valued at the current rate, so net worth over
time shows how your assets moved, not how the exchange rate did. A 'historical' mode (rates by
date) is available if you ever want it."""
import csv
import io
import json
import sqlite3
import urllib.request
from datetime import date, datetime, timedelta

# Lower number wins when several sources have a rate for the same day.
_SOURCE_RANK = {"manual": 0, "boi": 1, "web": 2}
REFRESH_EVERY_HOURS = 6
MAX_AGE_DAYS = 1

BOI_URL = ("https://edge.boi.gov.il/FusionEdgeServer/sdmx/v2/data/dataflow/BOI.STATISTICS/EXR/1.0"
           "?c%5BDATA_TYPE%5D=OF00&c%5BBASE_CURRENCY%5D={base}&startperiod={start}&endperiod={end}&format=csv")
# Tried in order. These are public, key-free sources; the first that answers wins.
WEB_SOURCES = [
    ("https://api.frankfurter.dev/v1/latest?base=USD&symbols=ILS", "frankfurter"),
    ("https://open.er-api.com/v6/latest/USD", "er-api"),
]


def set_rate(conn: sqlite3.Connection, day: str, base: str, quote: str, rate: float, source: str = "manual") -> None:
    if rate <= 0:
        raise ValueError("the rate must be above zero")
    existing = conn.execute("SELECT source FROM fx_rates WHERE rate_date=? AND base=? AND quote=?",
                            (day, base, quote)).fetchone()
    if existing and _SOURCE_RANK.get(existing["source"], 9) < _SOURCE_RANK[source]:
        return  # keep the better source
    conn.execute("INSERT OR REPLACE INTO fx_rates (rate_date, base, quote, rate, source) VALUES (?,?,?,?,?)",
                 (day, base, quote, rate, source))


def rate_on(conn: sqlite3.Connection, day: str, base: str, quote: str) -> float | None:
    """Rate on or before `day`, using the inverse if only the reverse pair is stored."""
    if base == quote:
        return 1.0
    row = conn.execute(
        """SELECT rate FROM fx_rates WHERE base=? AND quote=? AND rate_date<=?
           ORDER BY rate_date DESC, CASE source WHEN 'manual' THEN 0 WHEN 'boi' THEN 1 ELSE 2 END
           LIMIT 1""", (base, quote, day)).fetchone()
    if row:
        return row["rate"]
    row = conn.execute(
        """SELECT rate FROM fx_rates WHERE base=? AND quote=? AND rate_date<=?
           ORDER BY rate_date DESC LIMIT 1""", (quote, base, day)).fetchone()
    return 1.0 / row["rate"] if row else None


def current_rate(conn: sqlite3.Connection, base: str = "USD", quote: str = "ILS") -> dict | None:
    """The newest rate we hold, whatever its date, with where it came from."""
    row = conn.execute(
        """SELECT rate_date, rate, source FROM fx_rates WHERE base=? AND quote=?
           ORDER BY rate_date DESC, CASE source WHEN 'manual' THEN 0 WHEN 'boi' THEN 1 ELSE 2 END LIMIT 1""",
        (base, quote)).fetchone()
    if row:
        return {"rate": row["rate"], "date": row["rate_date"], "source": row["source"]}
    row = conn.execute("SELECT rate_date, rate, source FROM fx_rates WHERE base=? AND quote=? ORDER BY rate_date DESC LIMIT 1",
                       (quote, base)).fetchone()
    return {"rate": 1.0 / row["rate"], "date": row["rate_date"], "source": row["source"]} if row else None


def mode(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = 'fx_mode'").fetchone()
    return row[0] if row else "current"


def set_mode(conn: sqlite3.Connection, value: str) -> None:
    if value not in ("current", "historical"):
        raise ValueError("mode must be 'current' or 'historical'")
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('fx_mode', ?)", (value,))
    conn.commit()


NO_RATE = ("No dollar rate yet. Enter today's rate on the Balances page, or run: finance fx set today USD ILS 3.6 "
           "(or finance fx refresh)")


def convert_asset(conn: sqlite3.Connection, amount: float, src: str, dst: str, as_of: str | None = None) -> float:
    """Value an asset balance. Current mode uses today's rate for every date."""
    if src == dst:
        return amount
    if mode(conn) == "historical" and as_of:
        rate = rate_on(conn, as_of, src, dst)
        if rate is None:
            raise LookupError(f"no {src}/{dst} rate on or before {as_of}; add one with `finance fx set`")
        return amount * rate
    cur = current_rate(conn, src, dst)
    if cur is None:
        raise LookupError(NO_RATE)
    return amount * cur["rate"]


def convert(conn: sqlite3.Connection, amount: float, src: str, dst: str, day: str) -> float:
    """Historical conversion by date (used by historical mode and explicit lookups)."""
    rate = rate_on(conn, day, src, dst)
    if rate is None:
        raise LookupError(f"no {src}/{dst} rate on or before {day}; add one with `finance fx set`")
    return amount * rate


# --- fetching ---------------------------------------------------------------------------------

def _get(url: str, opener, timeout: int = 4) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "finance-tracker/1.0"})
    with opener(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8-sig")


def parse_web_rate(text: str) -> tuple[str, float]:
    """Understands the two public JSON layouts. Returns (iso date, ILS per USD)."""
    data = json.loads(text)
    if "rates" in data and "ILS" in data["rates"]:
        day = data.get("date")
        if not day and data.get("time_last_update_utc"):
            day = datetime.strptime(data["time_last_update_utc"][5:16], "%d %b %Y").date().isoformat()
        rate = float(data["rates"]["ILS"])
        if rate > 0:
            return (day or date.today().isoformat()), rate
    raise ValueError("unexpected rate response")


def refresh_current(conn: sqlite3.Connection, today: str, opener=urllib.request.urlopen, force: bool = False) -> dict:
    """Fetch today's USD/ILS rate if the one we hold is stale. Never raises: offline is fine."""
    have = current_rate(conn)
    if not force and have and have["date"] >= (date.fromisoformat(today) - timedelta(days=MAX_AGE_DAYS)).isoformat():
        return {"status": "fresh", **have}
    errors = []
    for url, name in WEB_SOURCES:
        try:
            day, rate = parse_web_rate(_get(url, opener))
            set_rate(conn, min(day, today), "USD", "ILS", rate, "web")
            conn.commit()
            return {"status": "fetched", "rate": rate, "date": min(day, today), "source": name}
        except Exception as exc:       # offline, blocked, or a changed response: try the next source
            errors.append(f"{name}: {exc}")
    return {"status": "failed", "detail": "; ".join(errors), **(have or {})}


def ensure_fresh(conn: sqlite3.Connection, today: str, opener=urllib.request.urlopen) -> dict | None:
    """Refresh at most every few hours, so opening the dashboard never waits on the network twice."""
    row = conn.execute("SELECT value FROM settings WHERE key = 'fx_last_attempt'").fetchone()
    if row and datetime.now() - datetime.fromisoformat(row[0]) < timedelta(hours=REFRESH_EVERY_HOURS):
        return None
    result = refresh_current(conn, today, opener)
    if result["status"] != "fresh":
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('fx_last_attempt', ?)",
                     (datetime.now().isoformat(timespec="seconds"),))
        conn.commit()
    return result


def fetch_boi(conn: sqlite3.Connection, start: date, end: date, base: str = "USD",
              opener=urllib.request.urlopen) -> int:
    """Bank of Israel representative rates for a date range, for historical mode.

    NOTE: not exercised against the live service; if the layout differs, use `finance fx set`.
    """
    url = BOI_URL.format(base=base, start=start.isoformat(), end=end.isoformat())
    text = _get(url, opener, timeout=30)
    count = 0
    for row in csv.DictReader(io.StringIO(text)):
        day = row.get("TIME_PERIOD") or ""
        value = row.get("OBS_VALUE") or ""
        if day and value:
            set_rate(conn, day[:10], base, "ILS", float(value), "boi")
            count += 1
    conn.commit()
    return count
