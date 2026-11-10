# Presale tracker

Emails you when the best-selling touring artists have a presale coming up in the
US or Europe, so you can register and buy early. It only alerts you: signing up
and buying stay manual (automating ticket purchases breaks sellers' terms and,
in the US, the BOTS Act).

## How it works

| When | What | Cost |
|---|---|---|
| Weekly | Firecrawl reads Pollstar's free top 10 of **Live 75** (tickets sold) and **Artist Power Index** and adds them to the watch list (artists stay watched for 28 days) | 2 credits |
| Weekly | Firecrawl reads the Pitchfork and Pollstar news pages for tour articles that name a watched artist | 2 credits |
| Weekly | Firecrawl pulls presale details from up to 5 new matching articles | 5 credits each |
| Daily | Ticketmaster Discovery API checks official presale and on-sale times for watched artists | free |

About 5–30 Firecrawl credits a week, well inside the free plan's 1,000 a month.
Dates taken from articles are AI-extracted, so check them; Ticketmaster times are official.

## Setup (GitHub Actions, free)

1. Get the free keys and keep them to yourself:
   - Firecrawl API key: firecrawl.dev dashboard (free plan, no card)
   - Ticketmaster Discovery API key: developer.ticketmaster.com (free)
   - Email: any SMTP account, e.g. Gmail with an App Password (`smtp.gmail.com`, port 587)
2. In GitHub: **Settings → Secrets and variables → Actions → New repository secret** and add
   `FIRECRAWL_API_KEY`, `TICKETMASTER_API_KEY`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
   `SMTP_PASSWORD`, `ALERT_EMAIL_TO`.
3. Optional **Variables** (same page, Variables tab):
   - `TRACKER_EXTRA_ARTISTS` – comma-separated artists to always watch
   - `TRACKER_COUNTRIES` – country codes, default US + Europe
4. **Actions → Presale tracker → Run workflow** with mode `weekly` to build the first watch list.
   After that it runs by itself every day; the Firecrawl scrape happens once a week.

## Run locally

```bash
pip install requests
export FIRECRAWL_API_KEY=... TICKETMASTER_API_KEY=...
python -m presale_tracker weekly   # first run
python -m presale_tracker daily    # Ticketmaster only, no Firecrawl credits
```

Without SMTP settings the digest is printed and saved to `presale_digest.md`.
Other settings: `TRACKER_LOOKAHEAD_DAYS` (30), `TRACKER_MAX_EXTRACTIONS` (5),
`TRACKER_ARTIST_MEMORY_DAYS` (28).

## Tests

```bash
python -m pytest presale_tracker
```
