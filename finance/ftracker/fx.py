"""Exchange rates. Official Bank of Israel rates where available, the rate a card charged
otherwise, and manual entries. Rates are looked up by date so history is not distorted by today's rate."""
import csv
import io
import sqlite3
import urllib.parse
import urllib.request
from datetime import date, timedelta

# Lower number = preferred when several sources have a rate for the same day.
_SOURCE_RANK = {"manual": 0, "boi": 1, "card": 2}

BOI_URL = ("https://edge.boi.gov.il/FusionEdgeServer/sdmx/v2/data/dataflow/BOI.STATISTICS/EXR/1.0"
           "?c%5BDATA_TYPE%5D=OF00&c%5BBASE_CURRENCY%5D={base}&startperiod={start}&endperiod={end}&format=csv")


def set_rate(conn: sqlite3.Connection, day: str, base: str, quote: str, rate: float,
             source: str = "manual") -> None:
    existing = conn.execute("SELECT source FROM fx_rates WHERE rate_date=? AND base=? AND quote=?",
                            (day, base, quote)).fetchone()
    if existing and _SOURCE_RANK[existing["source"]] < _SOURCE_RANK[source]:
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


def convert(conn: sqlite3.Connection, amount: float, src: str, dst: str, day: str) -> float:
    rate = rate_on(conn, day, src, dst)
    if rate is None:
        raise LookupError(f"no {src}/{dst} rate on or before {day}; add one with `finance fx set`")
    return amount * rate


def record_card_rates(conn: sqlite3.Connection) -> int:
    """Remember the rate each foreign-currency card charge implied."""
    rows = conn.execute(
        """SELECT txn_date, orig_currency, currency, amount, orig_amount FROM transactions
           WHERE orig_currency IS NOT NULL AND orig_currency != currency AND orig_amount != 0""").fetchall()
    for r in rows:
        set_rate(conn, r["txn_date"], r["orig_currency"], r["currency"],
                 round(r["amount"] / r["orig_amount"], 6), "card")
    return len(rows)


def fetch_boi(conn: sqlite3.Connection, start: date, end: date, base: str = "USD") -> int:
    """Download Bank of Israel representative rates (base -> ILS) for a date range.

    NOTE: not exercised against the live service yet; if the response layout differs,
    use `finance fx set` and report it.
    """
    url = BOI_URL.format(base=base, start=start.isoformat(), end=end.isoformat())
    with urllib.request.urlopen(url, timeout=30) as resp:
        text = resp.read().decode("utf-8-sig")
    count = 0
    for row in csv.DictReader(io.StringIO(text)):
        day = row.get("TIME_PERIOD") or row.get("TIME_PERIOD ") or ""
        value = row.get("OBS_VALUE") or ""
        if day and value:
            set_rate(conn, day[:10], base, "ILS", float(value), "boi")
            count += 1
    conn.commit()
    return count


def missing_days(conn: sqlite3.Connection, start: date, end: date, base: str = "USD") -> list[str]:
    have = {r[0] for r in conn.execute(
        "SELECT rate_date FROM fx_rates WHERE base=? AND quote='ILS'", (base,))}
    out, d = [], start
    while d <= end:
        if d.isoformat() not in have and d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out
