import logging
import asyncio
from typing import Optional
from playwright.async_api import async_playwright, Browser, Playwright, BrowserContext

logger = logging.getLogger(__name__)

class BrowserManager:
    """Manages a singleton Playwright browser instance."""
    
    def __init__(self) -> None:
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._lock = asyncio.Lock()

    async def get_browser(self) -> Browser:
        async with self._lock:
            if self._browser is None:
                logger.info("Starting Playwright browser...")
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-gpu",
                    ]
                )
            return self._browser

    async def new_context(self) -> BrowserContext:
        browser = await self.get_browser()
        return await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720}
        )

    async def close(self) -> None:
        async with self._lock:
            if self._browser:
                logger.info("Closing Playwright browser...")
                await self._browser.close()
                self._browser = None
            if self._playwright:
                await self._playwright.stop()
                self._playwright = None

# Global singleton
browser_manager = BrowserManager()
