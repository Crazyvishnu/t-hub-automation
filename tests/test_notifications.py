"""Tests for the Telegram notifier formatting (no network required)."""
from datetime import datetime, timezone, timedelta

import pytest

from app.models import Event
from app.notifications.telegram import TelegramNotifier

_IST = timezone(timedelta(hours=5, minutes=30))


def _make_event(**kwargs) -> Event:
    defaults = dict(
        event_id="event-1",
        source="T-Hub",
        title="AI Hackathon",
        url="https://tevents.t-hub.co/events/ai",
        event_date=datetime(2026, 8, 28, 10, 0, tzinfo=_IST),
        location="T-Hub, Hyderabad",
        price=0,
        is_free=True,
    )
    defaults.update(kwargs)
    return Event(**defaults)


def test_message_contains_essential_fields() -> None:
    event = _make_event()
    msg = TelegramNotifier.format_event(event)

    assert "NEW FREE EVENT" in msg
    assert "AI Hackathon" in msg
    assert "28 August 2026" in msg
    assert "T-Hub, Hyderabad" in msg
    assert "https://tevents.t-hub.co/events/ai" in msg
    assert "FREE" in msg
    assert "Register" in msg


def test_date_displayed_in_ist() -> None:
    """A UTC event (00:00 UTC = 05:30 IST) should show the IST date/time."""
    utc_midnight = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
    event = _make_event(event_date=utc_midnight)
    msg = TelegramNotifier.format_event(event)

    # 00:00 UTC = 05:30 IST on the same calendar day
    assert "1 September 2026" in msg
    assert "5:30 AM IST" in msg


def test_no_date_section_when_event_date_is_none() -> None:
    event = _make_event(event_date=None)
    msg = TelegramNotifier.format_event(event)

    assert "📅" not in msg
    assert "⏰" not in msg


def test_no_location_section_when_location_is_none() -> None:
    event = _make_event(location=None)
    msg = TelegramNotifier.format_event(event)

    assert "📍" not in msg


def test_message_has_html_bold_markers() -> None:
    event = _make_event()
    msg = TelegramNotifier.format_event(event)
    assert "<b>" in msg
