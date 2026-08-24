from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class Event(BaseModel):
    """Source-neutral representation of an event."""

    model_config = ConfigDict(str_strip_whitespace=True)

    event_id: str = Field(min_length=1, max_length=255)
    source: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=500)
    url: HttpUrl
    event_date: datetime | None = None
    location: str | None = None
    price: Decimal | None = None
    is_free: bool = False
    description: str | None = None

    @field_validator("event_date", mode="before")
    @classmethod
    def blank_date_is_none(cls, value: object) -> object:
        return None if value in ("", None) else value

    def database_payload(self) -> dict[str, object]:
        """Columns accepted by the events upsert endpoint."""
        return {
            "event_id": self.event_id,
            "source": self.source,
            "title": self.title,
            "url": str(self.url),
            "event_date": self.event_date.isoformat() if self.event_date else None,
            "location": self.location,
            "price": float(self.price) if self.price is not None else None,
            "is_free": self.is_free,
            "description": self.description,
        }

