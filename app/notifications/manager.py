"""
Notification Manager — routes an event through primary (WhatsApp) then
optionally falls back to secondary (Telegram).

Circuit breaker (req #15):
  After `failure_threshold` consecutive WhatsApp failures the manager
  bypasses WhatsApp and goes straight to Telegram.  It tests WhatsApp again
  every `circuit_test_interval` calls so the channel recovers automatically
  once the gateway is healthy again.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.models import Event
from app.notifications.base import Notifier

if TYPE_CHECKING:
    from app.database.supabase import SupabaseEventRepository

logger = logging.getLogger(__name__)


class NotificationManager(Notifier):
    """
    Manages multiple notification channels.
    Primary = WhatsApp (OpenWA).  Fallback = Telegram.

    Notification contract (req #11, #12, #13):
      - WhatsApp SUCCESS  → log WHATSAPP:SUCCESS,  skip Telegram
      - WhatsApp FAIL     → log WHATSAPP:ERROR, try Telegram
        - Telegram SUCCESS  → log TELEGRAM:SUCCESS
        - Telegram FAIL     → log TELEGRAM:ERROR, raise (event stays unclaimed for retry)
      - Both fail         → raise RuntimeError so the event is NOT marked notified
    """

    def __init__(
        self,
        repository: "SupabaseEventRepository",
        primary: Notifier | None = None,
        fallback: Notifier | None = None,
        mode: str = "FALLBACK",
        failure_threshold: int = 3,
    ) -> None:
        self.repository = repository
        self.primary = primary
        self.fallback = fallback
        # "FALLBACK" = use fallback only when primary fails
        # "ALWAYS"   = always send to fallback regardless of primary result
        self.mode = mode.upper()

        # --- circuit breaker state ---
        self._failure_threshold = failure_threshold
        self._consecutive_failures = 0          # WhatsApp failure counter
        self._circuit_open = False              # True = WhatsApp bypassed
        self._calls_since_circuit_open = 0      # calls since breaker tripped
        self._circuit_test_interval = 5         # attempt WhatsApp every N calls when open

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send(self, event: Event) -> None:
        """Attempt primary → fallback.  Raises only when ALL channels fail."""
        primary_success = False

        if self.primary and self._should_try_primary():
            try:
                await self.primary.send(event)
                primary_success = True
                self._record_primary_success()
                await self.repository.log_notification(
                    event_id=event.event_id,
                    channel="WHATSAPP",
                    status="SUCCESS",
                )
                logger.info("WhatsApp notification sent for %s", event.event_id)
            except Exception as exc:
                self._record_primary_failure()
                logger.error("WhatsApp notification failed for %s: %s", event.event_id, exc)
                await self.repository.log_notification(
                    event_id=event.event_id,
                    channel="WHATSAPP",
                    status="ERROR",
                    error_message=str(exc)[:500],
                )
        elif self.primary and self._circuit_open:
            logger.warning(
                "Circuit breaker OPEN — skipping WhatsApp for %s (will retry in %d calls)",
                event.event_id,
                self._circuit_test_interval - self._calls_since_circuit_open,
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
                    logger.info("Telegram notification sent for %s", event.event_id)
                except Exception as exc:
                    logger.error("Telegram notification failed for %s: %s", event.event_id, exc)
                    await self.repository.log_notification(
                        event_id=event.event_id,
                        channel="TELEGRAM",
                        status="ERROR",
                        error_message=str(exc)[:500],
                    )
                    if not primary_success:
                        raise RuntimeError("All notification channels failed.") from exc
        elif not primary_success:
            raise RuntimeError("Primary notification failed and no fallback configured.")

    async def send_raw(self, message: str) -> None:
        """Send an admin alert to all configured channels (e.g. redesign notice)."""
        if self.primary and self._should_try_primary():
            try:
                await self.primary.send_raw(message)
                self._record_primary_success()
            except Exception as exc:
                self._record_primary_failure()
                logger.error("Failed to send raw message via primary: %s", exc)

        if self.fallback:
            try:
                await self.fallback.send_raw(message)
            except Exception as exc:
                logger.error("Failed to send raw message via fallback: %s", exc)

    # ------------------------------------------------------------------
    # Circuit breaker helpers
    # ------------------------------------------------------------------

    def _should_try_primary(self) -> bool:
        """Return True if we should attempt the primary channel this call."""
        if not self._circuit_open:
            return True
        # Circuit is open: increment counter and try once per interval
        self._calls_since_circuit_open += 1
        if self._calls_since_circuit_open >= self._circuit_test_interval:
            self._calls_since_circuit_open = 0
            logger.info("Circuit breaker: probing WhatsApp…")
            return True
        return False

    def _record_primary_success(self) -> None:
        self._consecutive_failures = 0
        if self._circuit_open:
            logger.info("Circuit breaker CLOSED — WhatsApp recovered.")
        self._circuit_open = False
        self._calls_since_circuit_open = 0

    def _record_primary_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._failure_threshold and not self._circuit_open:
            self._circuit_open = True
            self._calls_since_circuit_open = 0
            logger.warning(
                "Circuit breaker OPEN after %d consecutive WhatsApp failures.",
                self._consecutive_failures,
            )
