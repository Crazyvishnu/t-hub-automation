"""Tests for EventProcessor with mocked dependencies (no network required)."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.models import Event
from app.services.event_processor import EventProcessor, RunSummary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_event(event_id: str = "ev-1", title: str = "Free Event") -> Event:
    return Event(
        event_id=event_id,
        source="T-Hub",
        title=title,
        url=f"https://tevents.t-hub.co/events/{event_id}",
        is_free=True,
        price=0,
    )


def _paid_event(event_id: str = "ev-paid") -> Event:
    return Event(
        event_id=event_id,
        source="T-Hub",
        title="Paid Event",
        url=f"https://tevents.t-hub.co/events/{event_id}",
        is_free=False,
        price=500,
    )


def _mock_scraper(events: list[Event], source: str = "T-Hub"):
    scraper = MagicMock()
    scraper.source = source
    scraper.scrape = AsyncMock(return_value=events)
    return scraper


def _mock_repository(new_count: int = 0, pending_rows: list[dict] | None = None, consecutive_failures: int = 0):
    repo = MagicMock()
    repo.upsert_events = AsyncMock(return_value=new_count)
    repo.claim_pending_free_events = AsyncMock(return_value=pending_rows or [])
    repo.mark_notified = AsyncMock()
    repo.release_claim = AsyncMock()
    repo.upsert_source_status = AsyncMock()
    repo.get_consecutive_failures = AsyncMock(return_value=consecutive_failures)
    repo.get_existing_fingerprints = AsyncMock(return_value={})
    return repo


def _mock_notifier():
    notifier = MagicMock()
    notifier.send = AsyncMock()
    notifier.send_raw = AsyncMock()
    return notifier


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_counts_events_and_new() -> None:
    events = [_free_event("a"), _paid_event("b")]
    scraper = _mock_scraper(events)
    repo = _mock_repository(new_count=1)

    processor = EventProcessor(scrapers=[scraper], repository=repo, notifier=None)
    summary = await processor.run()

    assert summary.sources_checked == 1
    assert summary.events_found == 2
    assert summary.new_events == 1
    assert summary.status == "success"


@pytest.mark.asyncio
async def test_run_sends_telegram_for_free_event() -> None:
    free_row = {
        "event_id": "ev-1", "source": "T-Hub", "title": "Free Event",
        "url": "https://tevents.t-hub.co/events/ev-1", "is_free": True,
        "price": 0, "event_date": None, "location": None, "description": None,
    }
    scraper = _mock_scraper([])
    repo = _mock_repository(pending_rows=[free_row])
    notifier = _mock_notifier()

    processor = EventProcessor(scrapers=[scraper], repository=repo, notifier=notifier)
    summary = await processor.run()

    notifier.send.assert_awaited_once()
    repo.mark_notified.assert_awaited_once_with("ev-1")
    assert summary.notifications_sent == 1


@pytest.mark.asyncio
async def test_run_marks_partial_failure_on_scraper_error() -> None:
    scraper = MagicMock()
    scraper.source = "T-Hub"
    scraper.scrape = AsyncMock(side_effect=RuntimeError("Timeout"))
    repo = _mock_repository()

    processor = EventProcessor(scrapers=[scraper], repository=repo, notifier=None)
    summary = await processor.run()

    assert summary.status == "partial_failure"
    assert any("Timeout" in e for e in summary.errors)


@pytest.mark.asyncio
async def test_run_keeps_event_on_telegram_failure() -> None:
    """If Telegram fails, event stays in DB and claim is released."""
    free_row = {
        "event_id": "ev-2", "source": "T-Hub", "title": "Free Event",
        "url": "https://tevents.t-hub.co/events/ev-2", "is_free": True,
        "price": 0, "event_date": None, "location": None, "description": None,
    }
    scraper = _mock_scraper([])
    repo = _mock_repository(pending_rows=[free_row])
    notifier = _mock_notifier()
    notifier.send = AsyncMock(side_effect=RuntimeError("Telegram unavailable"))

    processor = EventProcessor(scrapers=[scraper], repository=repo, notifier=notifier)
    summary = await processor.run()

    repo.mark_notified.assert_not_awaited()
    repo.release_claim.assert_awaited_once()
    assert summary.notifications_sent == 0
    assert summary.status == "partial_failure"


@pytest.mark.asyncio
async def test_run_records_source_status_success() -> None:
    scraper = _mock_scraper([_free_event()])
    repo = _mock_repository(new_count=1)

    processor = EventProcessor(scrapers=[scraper], repository=repo, notifier=None)
    await processor.run()

    repo.upsert_source_status.assert_awaited_once_with(
        source="T-Hub", status="SUCCESS", events_found=1
    )


@pytest.mark.asyncio
async def test_run_records_source_status_error() -> None:
    scraper = MagicMock()
    scraper.source = "T-Hub"
    scraper.scrape = AsyncMock(side_effect=RuntimeError("parse error"))
    repo = _mock_repository()

    processor = EventProcessor(scrapers=[scraper], repository=repo, notifier=None)
    await processor.run()

    call_kwargs = repo.upsert_source_status.call_args.kwargs
    assert call_kwargs["status"] == "ERROR"
    assert call_kwargs["source"] == "T-Hub"


@pytest.mark.asyncio
async def test_run_no_notifier_skips_notification_phase() -> None:
    scraper = _mock_scraper([_free_event()])
    repo = _mock_repository(new_count=1)

    processor = EventProcessor(scrapers=[scraper], repository=repo, notifier=None)
    summary = await processor.run()

    repo.claim_pending_free_events.assert_not_awaited()
    assert summary.notifications_sent == 0


@pytest.mark.asyncio
async def test_run_duration_is_positive() -> None:
    scraper = _mock_scraper([])
    repo = _mock_repository()
    processor = EventProcessor(scrapers=[scraper], repository=repo, notifier=None)
    summary = await processor.run()
    assert summary.duration_seconds >= 0
