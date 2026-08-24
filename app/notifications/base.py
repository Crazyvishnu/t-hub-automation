from __future__ import annotations

from abc import ABC, abstractmethod

from app.models import Event


class Notifier(ABC):
    @abstractmethod
    async def send(self, event: Event) -> None:
        """Send one event notification or raise an error."""

    async def send_raw(self, message: str) -> None:
        """Send a plain text message (e.g., admin alerts). Optional override."""
