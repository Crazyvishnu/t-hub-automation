"""Run both scrapers once locally and print results."""
import asyncio
import logging
import os
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s"
)

from app.config import get_settings
from app.database import SupabaseEventRepository
from app.notifications.telegram import TelegramNotifier
from app.notifications.openwa import OpenWANotifier
from app.notifications.manager import NotificationManager
from app.scrapers.thub import THubScraper
from app.scrapers.thub_calendar import THubCalendarScraper
from app.services.event_processor import EventProcessor

async def main():
    settings = get_settings()

    repository = SupabaseEventRepository(
        settings.supabase_url, settings.supabase_key, settings.request_timeout_seconds
    )
    
    telegram_notifier = TelegramNotifier(
        settings.telegram_bot_token,
        settings.telegram_chat_id,
        settings.request_timeout_seconds,
    ) if settings.telegram_configured else None

    whatsapp_notifier = OpenWANotifier(
        base_url=settings.openwa_url,
        api_key=settings.openwa_api_key,
        session_id=settings.openwa_session_id,
        target_number=settings.whatsapp_target_number,
        timeout_seconds=settings.whatsapp_timeout_seconds,
    ) if settings.openwa_configured else None

    notifier = NotificationManager(
        repository=repository,
        primary=whatsapp_notifier,
        fallback=telegram_notifier,
        mode=settings.telegram_mode,
        failure_threshold=settings.openwa_failure_threshold,
    ) if (whatsapp_notifier or telegram_notifier) else None

    processor = EventProcessor(
        scrapers=[
            THubScraper(settings.thub_events_url, settings.request_timeout_seconds),
            THubCalendarScraper(timeout_seconds=settings.request_timeout_seconds),
        ],
        repository=repository,
        notifier=notifier,
    )

    print("\n" + "="*60)
    print("  T-Hub Event Radar — Local Run")
    print("="*60 + "\n")

    summary = await processor.run()

    print("\n" + "="*60)
    print(f"  Status             : {summary.status}")
    print(f"  Sources checked    : {summary.sources_checked}")
    print(f"  Events found       : {summary.events_found}")
    print(f"  New events         : {summary.new_events}")
    print(f"  Notifications sent : {summary.notifications_sent}")
    print(f"  Duration           : {summary.duration_seconds}s")
    if summary.errors:
        print(f"  Errors:")
        for err in summary.errors:
            print(f"    - {err[:120]}")
    print("="*60 + "\n")

asyncio.run(main())
