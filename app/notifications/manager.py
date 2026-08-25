from __future__ import annotations

import logging
from typing import TYPE_CHECKING
import os

from app.models import Event
from app.notifications.base import Notifier

if TYPE_CHECKING:
    from app.database.supabase import SupabaseEventRepository

logger = logging.getLogger(__name__)


class NotificationManager(Notifier):
    """
    Manages multiple notification channels (e.g. WhatsApp and Telegram).
    Implements a fallback mechanism and logs notification attempts to Supabase.
    """

    def __init__(
        self,
        repository: SupabaseEventRepository,
        primary: Notifier | None = None,
        fallback: Notifier | None = None,
        mode: str = "FALLBACK"
    ) -> None:
        self.repository = repository
        self.primary = primary
        self.fallback = fallback
        # "FALLBACK" means only try fallback if primary fails. 
        # "ALWAYS" means try fallback regardless of primary's success.
        self.mode = mode.upper()

    async def send(self, event: Event) -> None:
        """Attempt to send notification using primary, then fallback if needed."""
        primary_success = False

        if self.primary:
            try:
                await self.primary.send(event)
                primary_success = True
                await self.repository.log_notification(
                    event_id=event.event_id,
                    channel="WHATSAPP",
                    status="SUCCESS",
                )
                logger.info(f"Successfully sent WhatsApp notification for {event.event_id}")
            except Exception as e:
                logger.error(f"WhatsApp notification failed for {event.event_id}: {e}")
                await self.repository.log_notification(
                    event_id=event.event_id,
                    channel="WHATSAPP",
                    status="ERROR",
                    error_message=str(e)[:500],
                )

        if self.fallback:
            if not primary_success or self.mode == "ALWAYS":
                try:
                    await self.fallback.send(event)
                    await self.repository.log_notification(
                        event_id=event.event_id,
                        channel="TELEGRAM",
                        status="SUCCESS",
                    )
                    logger.info(f"Successfully sent Telegram notification for {event.event_id}")
                except Exception as e:
                    logger.error(f"Telegram notification failed for {event.event_id}: {e}")
                    await self.repository.log_notification(
                        event_id=event.event_id,
                        channel="TELEGRAM",
                        status="ERROR",
                        error_message=str(e)[:500],
                    )
                    # If both failed, we raise an exception to ensure it isn't marked as successfully sent
                    if not primary_success:
                        raise RuntimeError("All notification channels failed.")
        elif not primary_success:
            # If no fallback is configured and primary failed
            raise RuntimeError("Primary notification failed and no fallback configured.")

    async def send_raw(self, message: str) -> None:
        """Send a plain text message to all configured channels (e.g. for critical admin alerts)."""
        if self.primary:
            try:
                await self.primary.send_raw(message)
            except Exception as e:
                logger.error(f"Failed to send raw message via primary: {e}")
                
        if self.fallback:
            try:
                await self.fallback.send_raw(message)
            except Exception as e:
                logger.error(f"Failed to send raw message via fallback: {e}")
