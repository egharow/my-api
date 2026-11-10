"""Turning scraped pages into artists and tour-announcement articles."""
from __future__ import annotations

import re

# Chart table rows link to /artists/<slug> (the header graphic uses /Artist/).
_CHART_ROW = re.compile(r"\[([^\]]+)\]\(https?://(?:www\.)?pollstar\.com/artists/[^)]+\)")

TOUR_WORDS = (
    "tour", "tours", "dates", "residency", "shows", "concert", "concerts",
    "stadium", "stadiums", "arena", "arenas", "presale", "onsale", "on-sale", "live",
)

ARTICLE_SCHEMA = {
    "type": "object",
    "properties": {
        "artist": {"type": "string"},
        "tour_name": {"type": "string"},
        "regions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Countries or cities with announced dates",
        },
        "presales": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "e.g. Artist presale, Verified Fan, Amex, Spotify, venue"},
                    "how_to_get_access": {"type": "string", "description": "Signup, code, fan club, card..."},
                    "signup_url": {"type": "string"},
                    "signup_deadline": {"type": "string"},
                    "starts": {"type": "string"},
                },
            },
        },
        "general_onsale": {"type": "string"},
        "ticket_url": {"type": "string"},
        "quote": {"type": "string", "description": "Exact sentence(s) from the article about tickets/presales"},
    },
}

ARTICLE_PROMPT = (
    "Extract ticketing details for the tour announced in this article. Only use facts stated "
    "in the article; leave fields empty if not mentioned. When the article uses relative dates "
    "such as 'this Friday', convert them to an absolute date using the article's publish date "
    "and include the original wording in parentheses. Copy the exact ticket/presale sentence(s) "
    "into 'quote'."
)


def chart_artists(markdown: str) -> list:
    """Artist names in rank order from a Pollstar chart page (free top 10)."""
    seen, out = set(), []
    for name in _CHART_ROW.findall(markdown):
        name = name.strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


def slugify(name: str) -> str:
    s = name.lower().replace("&", " and ").replace("!", "i")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[4:] if s.startswith("the-") else s


def _url_slug(url: str) -> str:
    path = re.sub(r"^https?://[^/]+", "", url).split("?")[0].split("#")[0]
    return path.strip("/").split("/")[-1].lower()


def tour_articles(links: list, artists: list, domain: str) -> dict:
    """Map article URL -> artist for links on `domain` that mention a watched
    artist and a touring word in the URL slug."""
    hits = {}
    for url in links:
        if domain not in url:
            continue
        slug = _url_slug(url)
        if not slug:
            continue
        words = set(slug.split("-"))
        if not words.intersection(TOUR_WORDS):
            continue
        padded = "-" + slug + "-"
        for artist in artists:
            a = slugify(artist)
            if a and ("-" + a + "-") in padded:
                hits.setdefault(url, artist)
                break
    return hits
