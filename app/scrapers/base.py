from __future__ import annotations

from abc import ABC, abstractmethod

from app.models import Event


class EventScraper(ABC):
    source: str

    @abstractmethod
    async def scrape(self) -> list[Event]:
        """Fetch and normalize events from one source.

        Raises an exception on a source failure. Returning an empty list means a
        successful scrape that genuinely found no events.
        """

