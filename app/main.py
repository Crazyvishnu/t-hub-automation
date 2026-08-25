from __future__ import annotations

import logging
import secrets

from fastapi import Depends, FastAPI, HTTPException, Query, status

from app.config import Settings, get_settings
from app.database import SupabaseEventRepository
from app.notifications import TelegramNotifier
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
    notifier = TelegramNotifier(runtime.telegram_bot_token, runtime.telegram_chat_id, runtime.request_timeout_seconds) if runtime.telegram_configured else None
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

