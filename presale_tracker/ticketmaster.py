"""Ticketmaster Discovery API: official presale and on-sale windows (free key)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

BASE = "https://app.ticketmaster.com/discovery/v2"


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class Ticketmaster:
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise RuntimeError("TICKETMASTER_API_KEY is not set")
        self.api_key = api_key

    def _get(self, path: str, params: dict) -> dict:
        import requests

        params = dict(params, apikey=self.api_key)
        for attempt in range(3):
            resp = requests.get(BASE + path, params=params, timeout=30)
            if resp.status_code == 429 and attempt < 2:  # 5 requests/second limit
                time.sleep(2 * (attempt + 1))
                continue
            break
        resp.raise_for_status()
        time.sleep(0.25)
        return resp.json()

    def find_attraction_id(self, artist: str):
        data = self._get("/attractions.json", {"keyword": artist, "classificationName": "music", "size": 10})
        items = (data.get("_embedded") or {}).get("attractions") or []
        exact = [a for a in items if (a.get("name") or "").strip().lower() == artist.strip().lower()]
        pick = exact[0] if exact else None
        return pick.get("id") if pick else None

    def events(self, attraction_id: str) -> list:
        data = self._get("/events.json", {"attractionId": attraction_id, "size": 200, "sort": "date,asc"})
        return (data.get("_embedded") or {}).get("events") or []


def sale_windows(events: list, artist: str, countries: list, lookahead_days: int, now=None) -> list:
    """Presales (and public on-sales) that haven't ended and start within the window."""
    now = now or datetime.now(timezone.utc)
    horizon = now + timedelta(days=lookahead_days)
    allowed = set(countries)
    out = []
    for ev in events:
        venues = (ev.get("_embedded") or {}).get("venues") or [{}]
        venue = venues[0]
        country = ((venue.get("country") or {}).get("countryCode") or "").upper()
        if allowed and country not in allowed:
            continue
        base = {
            "artist": artist,
            "event_id": ev.get("id"),
            "event": ev.get("name"),
            "url": ev.get("url"),
            "date": ((ev.get("dates") or {}).get("start") or {}).get("localDate"),
            "venue": venue.get("name"),
            "city": (venue.get("city") or {}).get("name"),
            "country": country,
        }
        sales = ev.get("sales") or {}
        windows = []
        for p in sales.get("presales") or []:
            windows.append((p.get("name") or "Presale", p.get("startDateTime"), p.get("endDateTime"), p.get("url")))
        public = sales.get("public") or {}
        if public.get("startDateTime") and not public.get("startTBD"):
            windows.append(("General on-sale", public.get("startDateTime"), public.get("endDateTime"), None))
        for name, start_s, end_s, url in windows:
            start, end = parse_time(start_s), parse_time(end_s)
            if start is None:
                continue
            if end is not None and end < now:
                continue
            if end is None and start < now:
                continue
            if start > horizon:
                continue
            item = dict(base, sale=name, starts=start, ends=end, sale_url=url)
            item["key"] = "%s|%s|%s" % (base["event_id"], name, start_s)
            out.append(item)
    out.sort(key=lambda x: x["starts"])
    return out
