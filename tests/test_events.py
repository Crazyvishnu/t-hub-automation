from datetime import datetime

from app.models import Event


def test_event_database_payload_is_source_neutral() -> None:
    event = Event(
        event_id="thub_123", source="T-Hub", title="Builder Meetup",
        url="https://tevents.t-hub.co/events/meetup", event_date=datetime(2026, 8, 28, 10, 0),
        price=0, is_free=True,
    )
    payload = event.database_payload()

    assert payload["event_id"] == "thub_123"
    assert payload["price"] == 0.0
    assert payload["is_free"] is True

