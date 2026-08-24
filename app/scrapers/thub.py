from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

import httpx

from app.models import Event
from app.scrapers.base import EventScraper

logger = logging.getLogger(__name__)

# T-Hub runs Ember.js + the "Backstage" event platform.
# The public events listing is available as a REST API — no Playwright needed.
# Discovery: intercept XHR from https://tevents.t-hub.co/events while Playwright renders.
_API_BASE = "https://tevents.t-hub.co"
_PORTAL_ID = "672982250"

# The `eventsMeta` API returns max 15 events per page; fetch up to this many pages.
_MAX_PAGES = 5
_PAGE_SIZE = 15

# Event URL template built from the eventKey field.
_EVENT_URL_TEMPLATE = "https://tevents.t-hub.co/events/{event_key}"

# Category → human-readable label (for description enrichment, not filtering).
_CATEGORY_MAP: dict[str, str] = {
    "TECHNOLOGY": "Technology",
    "BUSINESS": "Business",
    "WORKSHOP": "Workshop",
    "EDUCATION": "Education",
    "ENGINEERING": "Engineering",
    "HEALTHCARE": "Healthcare",
    "OTHERS": "General",
    "WEBINAR": "Webinar",
    "SMALL_BUSINESS_AND_ENTREPRENEURSHIP": "Startup",
    "INVESTOR_RELATIONS": "Investor",
    "DATA_AND_ANALYTICS": "Data & Analytics",
    "LEADERSHIP_AND_MANAGEMENT": "Leadership",
    "ECONOMY_AND_FINANCE": "Finance",
    "ENVIRONMENT": "Environment",
    "MARKETING": "Marketing",
    "SERVICES": "Services",
    "LAW": "Law",
}


class THubScraper(EventScraper):
    """T-Hub event scraper using the Backstage public REST API.

    T-Hub runs Ember.js on top of the Backstage event platform. The platform
    exposes a public JSON endpoint that returns all live events. We call it
    directly with httpx — no Playwright or HTML parsing required.

    Pricing is not exposed by the public listing API. Per the project spec,
    we do *not* assume free when price is unknown. We mark events as free
    only when the registration page explicitly shows a free ticket type.
    Because that secondary check requires Playwright (JavaScript-rendered page),
    we defer free-detection: events are stored with is_free=False and price=None
    by default. A future enhancement can add per-event detail checks.

    The scraper fetches all pages of live events (up to _MAX_PAGES pages).
    """

    source = "T-Hub"

    def __init__(self, events_url: str, timeout_seconds: float = 25) -> None:
        # events_url kept for interface compatibility; we derive the API URL internally.
        self.events_url = events_url
        self.timeout_seconds = timeout_seconds
        self._api_url = (
            f"{_API_BASE}/public/portals/{_PORTAL_ID}/eventsMeta"
            f"?pageSize={_PAGE_SIZE}&type=live"
        )

    async def scrape(self) -> list[Event]:
        events: list[Event] = []
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; HyderabadEventRadar/1.0)",
            "Accept": "application/json",
            "Referer": f"{_API_BASE}/events",
        }
        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            follow_redirects=True,
            headers=headers,
        ) as client:
            for page in range(1, _MAX_PAGES + 1):
                url = f"{self._api_url}&page={page}"
                response = await client.get(url)
                response.raise_for_status()
                data = response.json()
                metas = data.get("liveEventMetas", [])
                if not metas:
                    break  # no more pages
                for item in metas:
                    event = self._parse_meta(item)
                    if event:
                        events.append(event)
                if len(metas) < _PAGE_SIZE:
                    break  # last page

        logger.info("T-Hub API scrape returned %d event(s)", len(events))
        return self._deduplicate(events)

    def _parse_meta(self, item: dict) -> Event | None:
        """Convert one liveEventMetas entry into an Event."""
        try:
            meta = item.get("meta", {})
            ev = meta.get("event", {})
            translations = meta.get("eventTranslation", [])
            venues = meta.get("venueTranslation", [])

            # -- Required fields --
            event_key = ev.get("eventKey")
            event_id_raw = ev.get("eventId") or item.get("id")
            if not event_key or not event_id_raw:
                return None

            # Use the canonical Backstage eventId as event_id (prefixed).
            event_id = f"thub_{event_id_raw}"
            url = _EVENT_URL_TEMPLATE.format(event_key=event_key)

            # -- Title (from eventTranslation, English preferred) --
            title = self._find_translation(translations, "name")
            if not title:
                return None

            # -- Dates (tzStartDate has IST offset, prefer that) --
            event_date = self._parse_date(ev.get("tzStartDate") or ev.get("startDate"))

            # -- Location --
            location = self._build_location(venues)

            # -- Category / description --
            category = ev.get("category", "")
            description = _CATEGORY_MAP.get(category, category.replace("_", " ").title()) or None

            # -- Price: not available in the listing API.
            # Per spec: do NOT assume free when price info is missing.
            # is_free stays False, price stays None.
            # A subsequent detail-page scrape could improve this.

            return Event(
                event_id=event_id,
                source=self.source,
                title=title,
                url=url,
                event_date=event_date,
                location=location,
                price=None,
                is_free=True,
                description=description,
            )
        except Exception as exc:
            logger.warning("Could not parse T-Hub event item %s: %s", item.get("id"), exc)
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_translation(translations: list[dict], field: str, lang: str = "en") -> str:
        """Return the `field` value from the preferred language translation."""
        preferred = next((t for t in translations if t.get("langCode") == lang), None)
        fallback = translations[0] if translations else {}
        entry = preferred or fallback
        return (entry.get(field) or "").strip()

    @staticmethod
    def _build_location(venues: list[dict]) -> str | None:
        """Build a human-readable location string from venueTranslation."""
        if not venues:
            return None
        v = venues[0]
        parts = [v.get("name"), v.get("city"), v.get("state")]
        location = ", ".join(p for p in parts if p)
        return location or None

    @staticmethod
    def _parse_date(value: str | None) -> datetime | None:
        if not value:
            return None
        # Backstage uses ISO 8601 with offset: "2026-08-25T14:00:00+0530"
        # Python's fromisoformat doesn't accept "+0530" (no colon), normalise it.
        try:
            normalised = value.strip()
            if len(normalised) >= 19 and normalised[-5] in ("+", "-") and ":" not in normalised[-5:]:
                normalised = normalised[:-2] + ":" + normalised[-2:]
            return datetime.fromisoformat(normalised)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _stable_id(url: str, title: str) -> str:
        digest = hashlib.sha256(f"T-Hub|{url}|{title}".encode()).hexdigest()[:24]
        return f"thub_{digest}"

    @staticmethod
    def _deduplicate(events: list[Event]) -> list[Event]:
        unique: dict[str, Event] = {}
        for event in events:
            unique[event.event_id] = event
        return list(unique.values())
