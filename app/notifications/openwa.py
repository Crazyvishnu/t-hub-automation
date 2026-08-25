from __future__ import annotations

import logging
import httpx
from datetime import timezone, timedelta

from app.models import Event
from app.notifications.base import Notifier

logger = logging.getLogger(__name__)

# Indian Standard Time — UTC+05:30
_IST = timezone(timedelta(hours=5, minutes=30))


class OpenWANotifier(Notifier):
    def __init__(
        self,
        base_url: str,
        api_key: str,
        session_id: str,
        target_number: str,
        timeout_seconds: float = 25,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.session_id = session_id
        # OpenWA usually expects format 919876543210@c.us
        if not target_number.endswith("@c.us"):
            self.target_number = f"{target_number}@c.us"
        else:
            self.target_number = target_number
        self.timeout_seconds = timeout_seconds

    async def send(self, event: Event) -> None:
        """Send one event notification via WhatsApp."""
        text = self.format_event(event)
        await self._post_message(text)

    async def send_raw(self, message: str) -> None:
        """Send a plain text message (e.g., admin alerts) via WhatsApp."""
        await self._post_message(message)

    async def _post_message(self, text: str) -> None:
        payload = {
            "chatId": self.target_number,
            "text": text
        }
        
        headers = {
            "accept": "application/json",
            "Content-Type": "application/json"
        }
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        
        url = f"{self.base_url}/api/sessions/{self.session_id}/messages/send-text"
        
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            
    @staticmethod
    def format_event(event: Event) -> str:
        # WhatsApp uses *bold* instead of <b>bold</b>
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
            
        lines.extend([
            "",
            "💰 *FREE*",
            ""
        ])
        
        if event.registration_status:
            if event.registration_status.upper() == "OPEN":
                lines.append("🟢 *Registration OPEN*")
            else:
                lines.append(f"ℹ️ Registration: {event.registration_status}")
            lines.append("")

        lines.append("🔗 Register:")
        lines.append(str(event.registration_url) if event.registration_url else str(event.url))
        lines.extend([
            "",
            "⚡ Registration may fill quickly.",
        ])
        return "\n".join(lines)
