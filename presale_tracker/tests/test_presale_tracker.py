import os
from datetime import datetime, timedelta, timezone

from presale_tracker.sources import chart_artists, slugify, tour_articles
from presale_tracker.ticketmaster import sale_windows

HERE = os.path.dirname(__file__)


def _fixture(name):
    with open(os.path.join(HERE, "fixtures", name)) as f:
        return f.read()


def test_chart_artists_live75_rank_order_without_header_duplicates():
    assert chart_artists(_fixture("live75.md")) == [
        "BTS", "Backstreet Boys", "Tame Impala", "Noah Kahan", "Phoebe Bridgers"]


def test_chart_artists_power_index():
    assert chart_artists(_fixture("api.md")) == ["Bruno Mars", "The Weeknd", "Usher"]


def test_slugify():
    assert slugify("The Weeknd") == "weeknd"
    assert slugify("Angus & Julia Stone") == "angus-and-julia-stone"
    assert slugify("P!NK") == "pink"


def test_tour_articles_matches_artist_and_tour_word():
    links = [
        "https://pitchfork.com/story/queens-of-the-stone-age-plot-2027-us-tour/",
        "https://pitchfork.com/story/phoebe-bridgers-announces-madison-square-garden-ep/",
        "https://pitchfork.com/story/rush-announce-sphere-residency/",
        "https://pitchfork.com/story/brushstrokes-tour/",
        "https://example.com/story/rush-tour/",
    ]
    hits = tour_articles(links, ["Queens of the Stone Age", "Phoebe Bridgers", "Rush"], "pitchfork.com")
    assert hits == {
        "https://pitchfork.com/story/queens-of-the-stone-age-plot-2027-us-tour/": "Queens of the Stone Age",
        "https://pitchfork.com/story/rush-announce-sphere-residency/": "Rush",
    }


def _event(country, presales, public_start):
    return {
        "id": "E1-" + country, "name": "Big Tour", "url": "https://tm/e1",
        "dates": {"start": {"localDate": "2027-05-01"}},
        "sales": {"presales": presales, "public": {"startDateTime": public_start}},
        "_embedded": {"venues": [{"name": "Arena", "city": {"name": "City"}, "country": {"countryCode": country}}]},
    }


def test_sale_windows_filters_country_time_and_horizon():
    now = datetime(2026, 11, 10, tzinfo=timezone.utc)
    iso = lambda d: (now + timedelta(days=d)).strftime("%Y-%m-%dT%H:%M:%SZ")
    presales = [
        {"name": "Artist Presale", "startDateTime": iso(3), "endDateTime": iso(4)},
        {"name": "Ended Presale", "startDateTime": iso(-5), "endDateTime": iso(-1)},
        {"name": "Running Presale", "startDateTime": iso(-1), "endDateTime": iso(1)},
        {"name": "Far Presale", "startDateTime": iso(90), "endDateTime": iso(91)},
    ]
    events = [_event("US", presales, iso(5)), _event("JP", presales, iso(5))]
    out = sale_windows(events, "Band", ["US", "GB"], 30, now=now)
    assert [w["sale"] for w in out] == ["Running Presale", "Artist Presale", "General on-sale"]
    assert all(w["country"] == "US" for w in out)
    assert len({w["key"] for w in out}) == 3
