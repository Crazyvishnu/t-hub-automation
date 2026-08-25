from __future__ import annotations

from datetime import timezone, timedelta

import httpx

from app.models import Event
from app.notifications.base import Notifier

# Indian Standard Time — UTC+05:30
_IST = timezone(timedelta(hours=5, minutes=30))


class TelegramNotifier(Notifier):
    def __init__(self, bot_token: str, chat_id: str, timeout_seconds: float = 25) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout_seconds = timeout_seconds

    async def send(self, event: Event) -> None:
        await self._post_message(self.format_event(event))

    async def send_raw(self, message: str) -> None:
        """Send a plain admin alert message (e.g., repeated failure notice)."""
        await self._post_message(message)

    async def _post_message(self, text: str) -> None:
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        if not body.get("ok"):
            raise RuntimeError(
                f"Telegram rejected message: {body.get('description', 'unknown error')}"
            )

    @staticmethod
    def format_event(event: Event) -> str:
        lines = ["🚨 <b>NEW FREE EVENT</b>", "", f"🎯 <b>{event.title}</b>", ""]
        if event.event_date:
            # Always display in IST so Hyderabad users see local time.
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
        lines.extend([
            "",
            "💰 <b>FREE</b>",
            ""
        ])
        
        if event.registration_status:
            if event.registration_status.upper() == "OPEN":
                lines.append("🟢 <b>Registration OPEN</b>")
            else:
                lines.append(f"ℹ️ Registration: {event.registration_status}")
            lines.append("")

        lines.append("🔗 Register:")
        # Use registration_url if available, else fallback to url
        lines.append(str(event.registration_url) if event.registration_url else str(event.url))
        lines.extend([
            "",
            "⚡ Registration may fill quickly.",
        ])
        return "\n".join(lines)
