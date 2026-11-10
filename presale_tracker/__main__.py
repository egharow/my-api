"""Usage:
    python -m presale_tracker auto     # weekly refresh if due, else daily check (for cron)
    python -m presale_tracker weekly   # charts + news scrape (Firecrawl) + Ticketmaster check
    python -m presale_tracker daily    # Ticketmaster check only (no Firecrawl credits)
"""
from __future__ import annotations

import sys

from .config import NEWS_PAGES, POLLSTAR_CHARTS, Config
from .notify import build_digest, send
from .sources import ARTICLE_PROMPT, ARTICLE_SCHEMA, chart_artists, tour_articles
from .state import State, now_iso
from .ticketmaster import Ticketmaster, sale_windows


def weekly_scrape(cfg: Config, state: State) -> list:
    from .firecrawl_client import Firecrawl, FirecrawlError

    fc = Firecrawl(cfg.firecrawl_key)
    found = []
    for label, url in POLLSTAR_CHARTS.items():  # 1 credit each
        try:
            artists = chart_artists(fc.markdown(url))
            print("%s: %s" % (label, ", ".join(artists) or "(nothing parsed)"))
            state.watch(artists)
        except FirecrawlError as e:
            print("WARN chart %s: %s" % (label, e))
    state.watch(cfg.extra_artists)
    watched = state.watched(cfg.artist_memory_days)

    candidates = {}
    for page in NEWS_PAGES:  # 1 credit each
        domain = page.split("/")[2].replace("www.", "")
        try:
            candidates.update(tour_articles(fc.links(page), watched, domain))
        except FirecrawlError as e:
            print("WARN news %s: %s" % (page, e))

    extracted = 0
    for url, artist in candidates.items():
        if state.seen("seen_articles", url):
            continue
        if extracted >= cfg.max_extractions:
            print("Extraction cap reached; %s left for next week" % url)
            break
        try:
            info = fc.extract(url, ARTICLE_SCHEMA, ARTICLE_PROMPT)  # 5 credits
        except FirecrawlError as e:
            print("WARN article %s: %s" % (url, e))
            continue
        extracted += 1
        state.mark("seen_articles", url)
        found.append({"artist": artist, "url": url, "info": info})
    print("Firecrawl credits used this run: %d" % fc.credits_used)
    return found


def ticketmaster_check(cfg: Config, state: State, watched: list) -> list:
    tm = Ticketmaster(cfg.ticketmaster_key)
    new = []
    for artist in watched:
        ids = state.data["attraction_ids"]
        try:
            if artist not in ids:
                ids[artist] = tm.find_attraction_id(artist)
            if not ids[artist]:
                continue
            windows = sale_windows(tm.events(ids[artist]), artist, cfg.countries, cfg.lookahead_days)
        except Exception as e:  # keep going for other artists
            print("WARN Ticketmaster %s: %s" % (artist, e))
            continue
        for w in windows:
            if not state.seen("alerted", w["key"]):
                new.append(w)
    new.sort(key=lambda x: x["starts"])
    return new


def main(argv: list) -> int:
    mode = argv[1] if len(argv) > 1 else "auto"
    if mode not in ("auto", "weekly", "daily"):
        print(__doc__)
        return 2
    cfg = Config()
    state = State(cfg.state_path)
    weekly = mode == "weekly" or (mode == "auto" and state.weekly_due())
    missing = [k for k, v in (("TICKETMASTER_API_KEY", cfg.ticketmaster_key),
                              ("FIRECRAWL_API_KEY", cfg.firecrawl_key if weekly else "ok")) if not v]
    if missing:
        print("Missing settings: %s (see presale_tracker/README.md)" % ", ".join(missing))
        return 2

    articles = weekly_scrape(cfg, state) if weekly else []
    if weekly:
        state.data["last_weekly"] = now_iso()
    state.watch(cfg.extra_artists)
    watched = state.watched(cfg.artist_memory_days)
    if not watched:
        print("No artists to watch yet - run `weekly` first.")
        state.save()
        return 1
    sales = ticketmaster_check(cfg, state, watched)

    if sales or articles or weekly:
        subject = "Presale alert: %d upcoming sale(s)" % len(sales) if sales else "Weekly presale digest"
        send(cfg, subject, build_digest(sales, articles, watched, weekly))
        for s in sales:
            state.mark("alerted", s["key"])
    else:
        print("No new presales for %d watched artists." % len(watched))
    state.save()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
