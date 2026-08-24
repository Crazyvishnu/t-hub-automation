from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timezone

import httpx

from app.models import Event

logger = logging.getLogger(__name__)


class SupabaseEventRepository:
    """Thin Supabase REST client. No state is kept inside the container."""

    def __init__(self, url: str, key: str, timeout_seconds: float = 25) -> None:
        self.base_url = url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    async def upsert_events(self, events: Sequence[Event]) -> int:
        """Upsert events by event_id. Returns the count of newly inserted rows."""
        if not events:
            return 0
        payload = [event.database_payload() for event in events]
        headers = {
            **self.headers,
            "Prefer": "resolution=merge-duplicates,return=representation",
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/rest/v1/events?on_conflict=event_id",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            rows = response.json()

        # A row is NEW when first_seen == updated_at:
        # - At INSERT: both columns are set by now() in the same statement → equal.
        # - At UPDATE: the set_event_timestamps trigger bumps updated_at → not equal.
        # This is the only reliable signal because created_at == first_seen for ALL rows.
        new_count = 0
        for row in rows:
            fs = row.get("first_seen")
            ua = row.get("updated_at")
            if fs and ua:
                try:
                    fs_dt = datetime.fromisoformat(fs.replace("Z", "+00:00"))
                    ua_dt = datetime.fromisoformat(ua.replace("Z", "+00:00"))
                    if abs((fs_dt - ua_dt).total_seconds()) < 1:  # same instant → new insert
                        new_count += 1
                except Exception:
                    pass
        return new_count

    async def claim_pending_free_events(self, limit: int = 25) -> list[dict]:
        """Atomically claim pending free events via the SQL function."""
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/rest/v1/rpc/claim_pending_free_events",
                headers=self.headers,
                json={"claim_limit": limit},
            )
            response.raise_for_status()
            return response.json()

    async def mark_notified(self, event_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        params = {"event_id": f"eq.{event_id}"}
        payload = {"notified": True, "notified_at": now, "notification_claimed_at": None}
        headers = {**self.headers, "Prefer": "return=minimal"}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.patch(
                f"{self.base_url}/rest/v1/events",
                params=params,
                headers=headers,
                json=payload,
            )
            response.raise_for_status()

    async def release_claim(self, event_id: str, error: str) -> None:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/rest/v1/rpc/release_notification_claim",
                headers=self.headers,
                json={"target_event_id": event_id, "failure_message": error[:1000]},
            )
            response.raise_for_status()

    # ------------------------------------------------------------------
    # Source health monitoring  (plan §20)
    # ------------------------------------------------------------------

    async def upsert_source_status(
        self,
        source: str,
        status: str,
        events_found: int,
        error: str | None = None,
    ) -> None:
        """Persist a source run result via the upsert_source_status RPC."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}/rest/v1/rpc/upsert_source_status",
                    headers=self.headers,
                    json={
                        "p_source": source,
                        "p_status": status,
                        "p_events_found": events_found,
                        "p_error": error,
                    },
                )
                response.raise_for_status()
        except Exception:
            logger.warning("Could not update source_status for %s (non-fatal)", source)

    async def get_consecutive_failures(self, source: str) -> int:
        """Return the current consecutive failure count for a source."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/source_status",
                    headers=self.headers,
                    params={"source": f"eq.{source}", "select": "consecutive_failures"},
                )
                response.raise_for_status()
                rows = response.json()
                if rows:
                    return int(rows[0].get("consecutive_failures", 0))
        except Exception:
            logger.warning("Could not read consecutive_failures for %s", source)
        return 0
