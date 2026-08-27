"""
OpenWA WhatsApp notifier.

Provider-independent: the host URL (livemy.app, HeavenCloud, or any VPS)
is supplied entirely via OPENWA_URL in the environment.  Business logic
never references a specific hosting provider name.

API contract — rmyndharis/openwa REST API:
  POST  {base_url}/api/sessions/{session_id}/messages/send-text
        Body: { "chatId": "91…@c.us", "text": "…" }
        Header: X-API-Key: {api_key}

  GET   {base_url}/api/sessions/{session_id}
        Returns session object including `status` field.

Session wake-up: if the API returns 400 "not active", we attempt
  POST  {base_url}/api/sessions/start   Body: { "sessionId": "…" }
and then retry once.
"""
from __future__ import annotations

import asyncio
import logging
import httpx
from datetime import timezone, timedelta

from app.models import Event
from app.notifications.base import Notifier

logger = logging.getLogger(__name__)

# Indian Standard Time — UTC+05:30
_IST = timezone(timedelta(hours=5, minutes=30))


class OpenWANotifier(Notifier):
    """Send WhatsApp messages via an OpenWA (rmyndharis/openwa) gateway."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        session_id: str,
        target_number: str,
        timeout_seconds: float = 10,   # short timeout — never block the pipeline
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session_id = session_id
        # OpenWA expects format: 919876543210@c.us
        if not target_number.endswith("@c.us"):
            self.target_number = f"{target_number}@c.us"
        else:
            self.target_number = target_number
        self.timeout_seconds = timeout_seconds

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def send(self, event: Event) -> None:
        """Send one event notification via WhatsApp."""
        await self._post_message(self.format_event(event))

    async def send_raw(self, message: str) -> None:
        """Send a plain admin alert message via WhatsApp."""
        await self._post_message(message)

    async def check_health(self) -> dict:
        """
        Check whether the OpenWA gateway is reachable and the session is active.

        Returns a dict:
            {
              "reachable": bool,
              "session_status": str | None,   # e.g. "CONNECTED", "DISCONNECTED"
              "ready": bool,                   # True only when session is CONNECTED
            }

        Does NOT raise — always returns a dict so callers can branch safely.
        """
        result = {"reachable": False, "session_status": None, "ready": False}
        url = f"{self.base_url}/api/sessions/{self.session_id}"
        headers = self._headers()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    result["reachable"] = True
                    data = response.json()
                    # rmyndharis/openwa returns { "status": "CONNECTED" | "DISCONNECTED" | … }
                    session_status = data.get("status") or data.get("session", {}).get("status")
                    result["session_status"] = session_status
                    result["ready"] = str(session_status).upper() == "CONNECTED"
                else:
                    result["reachable"] = True   # server responded, just not ready
                    logger.warning(
                        "OpenWA health check returned %s: %s",
                        response.status_code, response.text[:200]
                    )
        except Exception as exc:
            logger.warning("OpenWA health check failed: %s", exc)
        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _headers(self) -> dict:
        h = {"accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    async def _post_message(self, text: str) -> None:
        payload = {"chatId": self.target_number, "text": text}
        headers = self._headers()
        send_url = f"{self.base_url}/api/sessions/{self.session_id}/messages/send-text"
        start_url = f"{self.base_url}/api/sessions/start"

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.post(send_url, json=payload, headers=headers)
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                # 400 "not active" means the session is sleeping — try to wake it up once
                if e.response.status_code == 400 and "not active" in e.response.text.lower():
                    logger.info(
                        "OpenWA session '%s' is not active — attempting to start it…",
                        self.session_id,
                    )
                    start_resp = await client.post(
                        start_url,
                        json={"sessionId": self.session_id},
                        headers=headers,
                    )
                    if start_resp.status_code not in (200, 201):
                        logger.error("Failed to start OpenWA session: %s", start_resp.text[:200])
                        raise  # re-raise original to trigger Telegram fallback

                    await asyncio.sleep(2)  # give the worker a moment to authenticate

                    retry = await client.post(send_url, json=payload, headers=headers)
                    retry.raise_for_status()
                else:
                    raise

    # ------------------------------------------------------------------
    # Message formatting (WhatsApp uses *bold*, not <b>)
    # ------------------------------------------------------------------

    @staticmethod
    def format_event(event: Event) -> str:
        lines = ["🚨 *NEW FREE EVENT*", "", f"🎯 *{event.title}*", ""]
        if event.event_date:
            local = (
                event.event_date.astimezone(_IST)
                if event.event_date.tzinfo
                else event.event_date
            )
            lines.append(f"📅 {local.strftime('%d %B %Y')}")
            time_str = local.strftime("%I:%M %p").lstrip("0") or "12:00 AM"
            lines.append(f"⏰ {time_str} IST")
        if event.location:
            lines.append(f"📍 {event.location}")
        if event.description:
            lines.append("")
            lines.append(event.description.strip())
        lines.extend(["", "💰 *FREE*", ""])
        if event.registration_status:
            if event.registration_status.upper() == "OPEN":
                lines.append("🟢 *Registration OPEN*")
            else:
                lines.append(f"ℹ️ Registration: {event.registration_status}")
            lines.append("")
        lines.append("🔗 Register:")
        lines.append(str(event.registration_url) if event.registration_url else str(event.url))
        lines.extend(["", "⚡ Registration may fill quickly."])
        return "\n".join(lines)
