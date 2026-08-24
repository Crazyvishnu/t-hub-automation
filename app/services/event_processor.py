from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field

from app.database import SupabaseEventRepository
from app.models import Event
from app.notifications.base import Notifier
from app.scrapers.base import EventScraper

logger = logging.getLogger(__name__)

# Send an admin Telegram alert after this many consecutive failures for a source.
_FAILURE_ALERT_THRESHOLD = 3


@dataclass
class RunSummary:
    status: str = "success"
    sources_checked: int = 0
    events_found: int = 0
    new_events: int = 0
    notifications_sent: int = 0
    duration_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)

    def payload(self) -> dict:
        return asdict(self)


class EventProcessor:
    def __init__(
        self,
        scrapers: list[EventScraper],
        repository: SupabaseEventRepository,
        notifier: Notifier | None,
    ) -> None:
        self.scrapers = scrapers
        self.repository = repository
        self.notifier = notifier

    async def run(self) -> RunSummary:
        summary = RunSummary()
        start = time.monotonic()

        for scraper in self.scrapers:
            summary.sources_checked += 1
            source_start = time.monotonic()
            try:
                events = await scraper.scrape()
                duration = time.monotonic() - source_start
                summary.events_found += len(events)
                new_count = await self.repository.upsert_events(events)
                summary.new_events += new_count
                logger.info(
                    "Source %s: %d event(s) scraped, %d new, %.1fs",
                    scraper.source, len(events), new_count, duration,
                )
                # Record success in source_status table.
                await self.repository.upsert_source_status(
                    source=scraper.source,
                    status="SUCCESS",
                    events_found=len(events),
                )
            except Exception as exc:
                duration = time.monotonic() - source_start
                logger.exception(
                    "Source %s failed after %.1fs", scraper.source, duration
                )
                summary.status = "partial_failure"
                summary.errors.append(f"{scraper.source}: {exc}")
                await self.repository.upsert_source_status(
                    source=scraper.source,
                    status="ERROR",
                    events_found=0,
                    error=str(exc)[:500],
                )
                # Send admin alert if threshold crossed.
                await self._maybe_alert_admin(scraper.source, str(exc))

        # ------------------------------------------------------------------
        # Notify pending free events
        # ------------------------------------------------------------------
        if self.notifier is None:
            summary.duration_seconds = round(time.monotonic() - start, 2)
            return summary

        try:
            for row in await self.repository.claim_pending_free_events():
                event = Event.model_validate(row)
                try:
                    await self.notifier.send(event)
                    await self.repository.mark_notified(event.event_id)
                    summary.notifications_sent += 1
                    logger.info("Notified event %s (%s)", event.event_id, event.title)
                except Exception as exc:
                    logger.exception(
                        "Notification failed for event %s", event.event_id
                    )
                    await self.repository.release_claim(event.event_id, str(exc))
                    summary.status = "partial_failure"
                    summary.errors.append(f"notification {event.event_id}: {exc}")
        except Exception as exc:
            logger.exception("Unable to claim pending notifications")
            summary.status = "partial_failure"
            summary.errors.append(f"notification queue: {exc}")

        summary.duration_seconds = round(time.monotonic() - start, 2)
        return summary

    async def _maybe_alert_admin(self, source: str, error: str) -> None:
        """Send a Telegram alert when a source has exceeded the failure threshold."""
        if self.notifier is None:
            return
        try:
            failures = await self.repository.get_consecutive_failures(source)
            if failures >= _FAILURE_ALERT_THRESHOLD:
                message = (
                    f"⚠️ <b>SCRAPER ERROR</b>\n\n"
                    f"Source: {source}\n"
                    f"Consecutive failures: {failures}\n"
                    f"Error: {error[:200]}"
                )
                # Use send_raw if available; otherwise send a synthetic event-like call.
                if hasattr(self.notifier, "send_raw"):
                    await self.notifier.send_raw(message)
                else:
                    logger.warning("Admin alert (no send_raw): %s", message)
        except Exception:
            logger.warning("Could not send admin alert for source %s", source)
