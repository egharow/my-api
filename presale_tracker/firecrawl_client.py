"""Minimal Firecrawl v2 /scrape client.

Credit costs (free plan = 1,000 credits/month): a plain markdown or links
scrape is 1 credit; adding the JSON extraction format costs 4 more (5 total).
"""
from __future__ import annotations

import time

API_URL = "https://api.firecrawl.dev/v2/scrape"


class FirecrawlError(RuntimeError):
    pass


class Firecrawl:
    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise FirecrawlError("FIRECRAWL_API_KEY is not set")
        self.api_key = api_key
        self.credits_used = 0

    def scrape(self, url: str, formats: list, only_main_content: bool = True) -> dict:
        import requests  # imported lazily so unit tests need no network deps

        body = {"url": url, "formats": formats, "onlyMainContent": only_main_content}
        headers = {"Authorization": "Bearer " + self.api_key}
        for attempt in range(3):
            resp = requests.post(API_URL, json=body, headers=headers, timeout=120)
            if resp.status_code == 429 and attempt < 2:  # free plan: 10 scrapes/min
                time.sleep(10 * (attempt + 1))
                continue
            break
        if resp.status_code == 402:
            raise FirecrawlError("Out of Firecrawl credits for this month")
        if resp.status_code >= 400:
            raise FirecrawlError("Firecrawl %s for %s: %s" % (resp.status_code, url, resp.text[:300]))
        data = resp.json().get("data") or {}
        self.credits_used += int((data.get("metadata") or {}).get("creditsUsed") or 0)
        return data

    def markdown(self, url: str) -> str:
        return self.scrape(url, ["markdown"]).get("markdown") or ""

    def links(self, url: str) -> list:
        return self.scrape(url, ["links"]).get("links") or []

    def extract(self, url: str, schema: dict, prompt: str) -> dict:
        data = self.scrape(url, [{"type": "json", "schema": schema, "prompt": prompt}])
        out = dict(data.get("json") or {})
        meta = data.get("metadata") or {}
        out["_published"] = meta.get("publishedTime") or meta.get("article:published_time")
        out["_title"] = meta.get("title")
        return out
