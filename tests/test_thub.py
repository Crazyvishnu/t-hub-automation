"""Tests for the T-Hub scraper — API-based implementation."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.scrapers.thub import THubScraper

SCRAPER = THubScraper("https://tevents.t-hub.co/events")

_IST = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------------------
# Sample API response data (matches real T-Hub Backstage format)
# ---------------------------------------------------------------------------

def _make_meta(
    event_id="413000035639185",
    meta_id="413000035643055",
    event_key="AIHackathon2026",
    title="AI Hackathon 2026",
    tz_start="2026-08-28T14:00:00+0530",
    tz_end="2026-08-28T19:00:00+0530",
    category="TECHNOLOGY",
    venue_name="T-Hub",
    city="Hyderabad",
    state="Telangana",
) -> dict:
    return {
        "id": meta_id,
        "meta": {
            "event": {
                "eventId": event_id,
                "eventKey": event_key,
                "category": category,
                "tzStartDate": tz_start,
                "tzEndDate": tz_end,
                "startDate": "2026-08-28T08:30:00.000Z",
                "isOnlineEvent": False,
                "timezone": "Asia/Calcutta",
                "eventType": 2,
                "portalId": "672982250",
                "domainName": "tevents.t-hub.co",
            },
            "eventTranslation": [
                {"name": title, "summary": "", "langCode": "en"}
            ],
            "venueTranslation": [
                {"name": venue_name, "city": city, "state": state,
                 "country": "India", "langCode": "en",
                 "formattedVenue": f"{city}, {state}, 500032"}
            ],
        },
    }


# ---------------------------------------------------------------------------
# _parse_meta
# ---------------------------------------------------------------------------

def test_parse_meta_extracts_all_fields() -> None:
    item = _make_meta()
    event = SCRAPER._parse_meta(item)

    assert event is not None
    assert event.event_id == "thub_413000035639185"
    assert event.title == "AI Hackathon 2026"
    assert str(event.url) == "https://tevents.t-hub.co/events/AIHackathon2026"
    assert event.source == "T-Hub"
    assert event.location == "T-Hub, Hyderabad, Telangana"


def test_parse_meta_date_ist_offset() -> None:
    item = _make_meta(tz_start="2026-08-28T14:00:00+0530")
    event = SCRAPER._parse_meta(item)

    assert event is not None
    assert event.event_date is not None
    assert event.event_date.hour == 14
    assert event.event_date.minute == 0
    # Offset should be +05:30
    assert event.event_date.utcoffset().seconds // 3600 == 5


def test_parse_meta_price_is_none_when_unknown() -> None:
    """Per spec: do not assume free when pricing data is unavailable."""
    item = _make_meta()
    event = SCRAPER._parse_meta(item)

    assert event is not None
    assert event.is_free is False
    assert event.price is None


def test_parse_meta_returns_none_without_event_key() -> None:
    item = _make_meta()
    item["meta"]["event"].pop("eventKey")
    assert SCRAPER._parse_meta(item) is None


def test_parse_meta_returns_none_without_title() -> None:
    item = _make_meta()
    item["meta"]["eventTranslation"] = []
    assert SCRAPER._parse_meta(item) is None


def test_parse_meta_category_mapped_to_description() -> None:
    item = _make_meta(category="WORKSHOP")
    event = SCRAPER._parse_meta(item)
    assert event is not None
    assert event.description == "Workshop"


def test_parse_meta_unknown_category_titlecased() -> None:
    item = _make_meta(category="CUSTOM_CATEGORY")
    event = SCRAPER._parse_meta(item)
    assert event is not None
    assert event.description == "Custom Category"


# ---------------------------------------------------------------------------
# _parse_date helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected_hour", [
    ("2026-08-28T14:00:00+0530", 14),
    ("2026-08-28T08:30:00.000Z", 8),
    ("2026-08-28T14:00:00+05:30", 14),
])
def test_parse_date_formats(value: str, expected_hour: int) -> None:
    dt = THubScraper._parse_date(value)
    assert dt is not None
    assert dt.hour == expected_hour


def test_parse_date_returns_none_for_none() -> None:
    assert THubScraper._parse_date(None) is None


def test_parse_date_returns_none_for_garbage() -> None:
    assert THubScraper._parse_date("not-a-date") is None


# ---------------------------------------------------------------------------
# _build_location helper
# ---------------------------------------------------------------------------

def test_build_location_combines_name_city_state() -> None:
    venues = [{"name": "T-Hub", "city": "Hyderabad", "state": "Telangana"}]
    assert THubScraper._build_location(venues) == "T-Hub, Hyderabad, Telangana"


def test_build_location_returns_none_for_empty() -> None:
    assert THubScraper._build_location([]) is None


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def test_deduplicates_identical_event_ids() -> None:
    items = [_make_meta(event_id="same", meta_id="m1"), _make_meta(event_id="same", meta_id="m2")]
    events = [SCRAPER._parse_meta(i) for i in items]
    deduplicated = SCRAPER._deduplicate([e for e in events if e])
    assert len(deduplicated) == 1


# ---------------------------------------------------------------------------
# scrape() — mocked HTTP
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scrape_returns_events_from_api() -> None:
    mock_response = {
        "liveEventMetas": [_make_meta(), _make_meta(event_id="999", meta_id="m999", event_key="OtherEvent", title="Other")]
    }

    import httpx
    from unittest.mock import MagicMock

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=mock_response)

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        events = await SCRAPER.scrape()

    assert len(events) == 2
    assert any(e.title == "AI Hackathon 2026" for e in events)


@pytest.mark.asyncio
async def test_scrape_stops_on_empty_response() -> None:
    """Pagination stops immediately when liveEventMetas is empty."""
    import httpx
    from unittest.mock import MagicMock

    call_count = 0

    async def fake_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={"liveEventMetas": []})
        return mock_resp

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = fake_get
        mock_client_cls.return_value = mock_client

        events = await SCRAPER.scrape()

    assert events == []
    assert call_count == 1  # stops immediately on first empty page


@pytest.mark.asyncio
async def test_scrape_stops_when_results_less_than_page_size() -> None:
    """If page returns fewer items than PAGE_SIZE, it's the last page — no next request."""
    item = _make_meta()
    # 1 item < PAGE_SIZE=15, so scraper should not request page 2

    import httpx
    from unittest.mock import MagicMock

    call_count = 0

    async def fake_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={"liveEventMetas": [item]})
        return mock_resp

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = fake_get
        mock_client_cls.return_value = mock_client

        events = await SCRAPER.scrape()

    assert len(events) == 1
    assert call_count == 1  # partial page → no more pages requested
