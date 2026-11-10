from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _age_days(iso: str) -> float:
    try:
        then = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return 1e9
    return (datetime.now(timezone.utc) - then).total_seconds() / 86400


class State:
    """Small JSON file: watched artists, Ticketmaster ids, seen articles, sent alerts."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.data = {"artists": {}, "attraction_ids": {}, "seen_articles": {}, "alerted": {}, "last_weekly": None}
        if os.path.exists(path):
            with open(path) as f:
                self.data.update(json.load(f))

    def save(self) -> None:
        for bucket in ("seen_articles", "alerted"):
            self.data[bucket] = {k: v for k, v in self.data[bucket].items() if _age_days(v) < 180}
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def weekly_due(self) -> bool:
        return self.data.get("last_weekly") is None or _age_days(self.data["last_weekly"]) >= 6.5

    def watch(self, artists: list) -> None:
        stamp = now_iso()
        for a in artists:
            self.data["artists"][a] = stamp

    def watched(self, memory_days: int) -> list:
        return sorted(a for a, seen in self.data["artists"].items() if _age_days(seen) <= memory_days)

    def seen(self, bucket: str, key: str) -> bool:
        return key in self.data[bucket]

    def mark(self, bucket: str, key: str) -> None:
        self.data[bucket][key] = now_iso()
