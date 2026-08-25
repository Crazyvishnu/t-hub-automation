from __future__ import annotations

import logging
import secrets

from fastapi import Depends, FastAPI, HTTPException, Query, status

from app.config import Settings, get_settings
from app.database import SupabaseEventRepository
from app.notifications.telegram import TelegramNotifier
from app.notifications.openwa import OpenWANotifier
from app.notifications.manager import NotificationManager
from app.services import EventProcessor
from app.scrapers import THubScraper, THubCalendarScraper
from app.utils.logger import configure_logging

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)
app = FastAPI(title="Hyderabad Event Radar", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/run")
async def run_once(token: str = Query(default=""), runtime: Settings = Depends(get_settings)) -> dict:
    if not runtime.run_secret or not secrets.compare_digest(token, runtime.run_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid run token")
    if not runtime.database_configured:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Supabase is not configured")

    repository = SupabaseEventRepository(runtime.supabase_url, runtime.supabase_key, runtime.request_timeout_seconds)
    
    telegram_notifier = TelegramNotifier(
        runtime.telegram_bot_token, 
        runtime.telegram_chat_id, 
        runtime.request_timeout_seconds
    ) if runtime.telegram_configured else None
    
    whatsapp_notifier = OpenWANotifier(
        base_url=runtime.openwa_url,
        api_key=runtime.openwa_api_key,
        session_id=runtime.openwa_session_id,
        target_number=runtime.whatsapp_target_number,
        timeout_seconds=runtime.request_timeout_seconds,
    ) if runtime.openwa_configured else None

    notifier = NotificationManager(
        repository=repository,
        primary=whatsapp_notifier,
        fallback=telegram_notifier,
        mode=runtime.telegram_mode
    ) if (whatsapp_notifier or telegram_notifier) else None

    processor = EventProcessor(
        scrapers=[
            THubScraper(runtime.thub_events_url, runtime.request_timeout_seconds),
            THubCalendarScraper(timeout_seconds=runtime.request_timeout_seconds)
        ],
        repository=repository,
        notifier=notifier,
    )
    summary = await processor.run()
    logger.info("Monitoring run completed: %s", summary.payload())
    return summary.payload()

