from __future__ import annotations

import os

# Europe + US by default. Override with TRACKER_COUNTRIES="US,GB,DE".
EUROPE = (
    "GB IE FR DE NL BE LU AT CH IT ES PT DK SE NO FI IS PL CZ SK HU "
    "SI HR RO BG GR EE LV LT MT CY RS"
).split()
DEFAULT_COUNTRIES = ["US"] + EUROPE

POLLSTAR_CHARTS = {
    "Live 75 (tickets sold)": "https://pollstar.com/charts/5284/live-75/chart",
    "Artist Power Index": "https://pollstar.com/charts/5290/artist-power-index/chart",
}
NEWS_PAGES = [
    "https://pitchfork.com/news/",
    "https://www.pollstar.com/",
]


def _list(name: str, default: list) -> list:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return list(default)
    return [x.strip() for x in raw.split(",") if x.strip()]


class Config:
    def __init__(self) -> None:
        self.firecrawl_key = os.environ.get("FIRECRAWL_API_KEY", "")
        self.ticketmaster_key = os.environ.get("TICKETMASTER_API_KEY", "")
        self.countries = [c.upper() for c in _list("TRACKER_COUNTRIES", DEFAULT_COUNTRIES)]
        self.extra_artists = _list("TRACKER_EXTRA_ARTISTS", [])
        self.state_path = os.environ.get("TRACKER_STATE", "presale_tracker_state.json")
        self.digest_path = os.environ.get("TRACKER_DIGEST", "presale_digest.md")
        # Only alert on presales/on-sales starting within this many days.
        self.lookahead_days = int(os.environ.get("TRACKER_LOOKAHEAD_DAYS", "30"))
        # Keep watching an artist this long after it drops off the charts.
        self.artist_memory_days = int(os.environ.get("TRACKER_ARTIST_MEMORY_DAYS", "28"))
        # Hard cap on 5-credit article extractions per weekly run.
        self.max_extractions = int(os.environ.get("TRACKER_MAX_EXTRACTIONS", "5"))
        # Email (optional). Without it the digest is printed and saved to a file.
        self.smtp_host = os.environ.get("SMTP_HOST", "")
        self.smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        self.smtp_user = os.environ.get("SMTP_USER", "")
        self.smtp_password = os.environ.get("SMTP_PASSWORD", "")
        self.email_to = os.environ.get("ALERT_EMAIL_TO", "")
        self.email_from = os.environ.get("ALERT_EMAIL_FROM", "") or self.smtp_user
