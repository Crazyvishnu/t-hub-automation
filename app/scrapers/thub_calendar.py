from __future__ import annotations

import json
import logging
import re
import hashlib
from datetime import datetime, timezone, timedelta

from app.models import Event
from app.scrapers.base import EventScraper

logger = logging.getLogger(__name__)

_CALENDAR_URL = "https://www.t-hub.co/events-calendar"
_IST = timezone(timedelta(hours=5, minutes=30))


class THubCalendarScraper(EventScraper):
    """Zoho Creator calendar scraper.

    The T-Hub main calendar is embedded as a Zoho Creator iframe. All events
    for upcoming months are pre-loaded inside the iframe's HTML as a JSON blob
    in a `compMeta : JSON.parse("...")` script variable. We load the page with
    Playwright, wait for the iframe's container div to appear (which signals
    the Zoho app has injected its data), then read the raw HTML to extract
    the compMeta.
    """

    source = "T-Hub-Calendar"

    def __init__(self, events_url: str = _CALENDAR_URL, timeout_seconds: float = 45) -> None:
        self.events_url = events_url
        self.timeout_seconds = timeout_seconds

    async def scrape(self) -> list[Event]:
        from playwright.async_api import async_playwright

        events: list[Event] = []
        logger.info("Navigating to T-Hub Calendar iframe...")

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-dev-shm-usage", "--no-sandbox", "--disable-setuid-sandbox", "--disable-gpu"],
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                viewport={"width": 1280, "height": 720},
            )
            page = await context.new_page()
            try:
                await page.goto(
                    self.events_url,
                    wait_until="domcontentloaded",
                    timeout=self.timeout_seconds * 1000,
                )

                # Find the Zoho iframe and get its content frame
                try:
                    iframe_element = await page.wait_for_selector("iframe.form-iframe", timeout=15000)
                except Exception as e:
                    raise RuntimeError(f"REDESIGN_DETECTED: Zoho calendar iframe not found ({e})")
                    
                if not iframe_element:
                    raise RuntimeError("REDESIGN_DETECTED: Zoho calendar iframe not found")

                frame = await iframe_element.content_frame()
                if not frame:
                    raise RuntimeError("REDESIGN_DETECTED: Could not access iframe content frame")

                # Wait for the Zoho calendar container div
                try:
                    await frame.wait_for_selector(
                        ".zc-calendar-cont",
                        state="attached",
                        timeout=20000,
                    )
                except Exception:
                    logger.warning("Calendar container not found within timeout, trying anyway...")

                # Set up a listener for the AJAX responses
                ajax_responses = []
                async def handle_response(response):
                    if "report-embed-json" in response.url:
                        try:
                            data = await response.json()
                            month_events = data.get("MODEL", {}).get("EVENTS", [])
                            ajax_responses.extend(month_events)
                        except Exception as e:
                            logger.error(f"Error parsing AJAX response: {e}")
                
                frame.page.on("response", handle_response)

                # Collect events from the first month (already in the DOM)
                html = await frame.content()
                events_from_html = self._extract_events_from_html(html)
                logger.info(f"Month 0: extracted {len(events_from_html)} events")

                # Navigate to the next 3 months
                for month_offset in range(1, 4):
                    try:
                        next_btn = await frame.query_selector("[title='Next Month']")
                        if not next_btn:
                            logger.warning(f"Next Month button not in DOM at offset {month_offset}")
                            break
                        
                        await next_btn.click(force=True)
                        await frame.wait_for_timeout(4000)  # Wait for AJAX + render
                        logger.info(f"Navigated to month {month_offset}")
                        
                    except Exception as nav_exc:
                        logger.warning(f"Could not navigate to next month at offset {month_offset}: {nav_exc}")
                        break
                
                import httpx
                async with httpx.AsyncClient() as client:
                    # Parse all collected events (compMeta + AJAX)
                    # wait, before we used events.extend(self._extract_events_from_html(html))
                    # which we now modified to return list of dicts. So we store it in a variable.
                    # Wait, in the current file, line 94 is `events.extend(self._extract_events_from_html(html))`.
                    # I will fix line 94 to use a new variable later.
                    
                    all_raw_events = events_from_html + ajax_responses
                    
                    # Remove duplicates based on ID before fetching details
                    seen_ids = set()
                    unique_raw_events = []
                    for item in all_raw_events:
                        zoho_id = item.get("id")
                        if zoho_id and zoho_id not in seen_ids:
                            seen_ids.add(zoho_id)
                            unique_raw_events.append(item)
                    
                    parsed_count = 0
                    for raw_event in unique_raw_events:
                        zoho_id = raw_event.get("id")
                        
                        # Fetch details
                        details = await self._fetch_event_details(client, zoho_id)
                        if details.get("accessibility", "").lower() == "closed door":
                            logger.info(f"Skipping closed door event: {raw_event.get('title')}")
                            continue
                            
                        parsed = self._parse_zoho_event(raw_event, details)
                        if parsed:
                            events.append(parsed)
                            parsed_count += 1
                    logger.info(f"Extracted and parsed {parsed_count} valid events total")
            except Exception as exc:
                logger.error(f"Error scraping {self.source}: {exc!r}")
            finally:
                await page.close()
                await context.close()
                await browser.close()

        logger.info(f"{self.source}: {len(events)} events before deduplication")
        return self._deduplicate(events)

    async def _fetch_event_details(self, client: httpx.AsyncClient, zoho_id: str) -> dict:
        url = f"https://creatorapp.zohopublic.com/thubcat/event-manager/summary-embed/Events_Calendar1/{zoho_id}?privatelink=MeMHuvkHuADwezM5Ftfyx466XMwBuwy3fE2U9w0d1r98JTCxg5HHhWUJMphOPjad46vd1W0812uOvf2Ufy3VnQ3s1d1fWWQAyxbK&parentViewType=2"
        try:
            r = await client.get(url, timeout=15.0)
            data = r.json()
            items = data.get("MODEL", {}).get("DATAJSONARRAY", [])
            if items:
                fields = items[0]
                accessibility = ""
                reg_link = ""
                poc_email = fields.get("zc_POC_Email_unformatted", "")
                
                for k, v in fields.items():
                    if isinstance(v, dict):
                        if v.get("name") == "Event_Accesibility":
                            accessibility = v.get("value", "")
                        elif v.get("name") == "Registration_Link":
                            reg_link = v.get("value", "")
                            
                # Check for direct anchor tags in the value for Registration_Link
                if reg_link and "<a" in reg_link:
                    import re
                    match = re.search(r'href\s*=\s*[\'"]?([^\'" >]+)', reg_link)
                    if match:
                        reg_link = match.group(1)
                
                # Note: Registration_Link in json might be in a different format, just taking string value if not anchor
                return {"accessibility": accessibility, "registration_link": reg_link, "poc_email": poc_email}
        except Exception as e:
            logger.warning(f"Failed to fetch details for zoho_id {zoho_id}: {e}")
        return {}

    def _extract_events_from_html(self, html: str) -> list[dict]:
        """Extract raw event dicts from the compMeta JSON blob embedded in the iframe HTML."""
        events = []
        # The compMeta key uses optional whitespace/tabs before the colon.
        match = re.search(r'compMeta\s*:\s*JSON\.parse\("(.*?)"\)', html)
        if not match:
            raise RuntimeError("REDESIGN_DETECTED: compMeta pattern not found in calendar HTML (Calendar software changed?)")

        escaped_json = match.group(1)
        try:
            import ast
            json_str = ast.literal_eval('"' + escaped_json + '"')
            data = json.loads(json_str)
            zoho_events = data.get("EVENTS", [])
            logger.info(f"compMeta parsed: {len(zoho_events)} raw events found")

            for item in zoho_events:
                events.append(item)
        except Exception as exc:
            logger.error(f"Failed to parse compMeta JSON: {exc!r}")

        return events

    def _parse_zoho_event(self, item: dict, details: dict) -> Event | None:
        try:
            zoho_id = item.get("id")
            title = (item.get("title") or "").strip()
            start_str = item.get("start", "")
            description = item.get("description")
            
            poc_email = details.get("poc_email")
            if poc_email:
                if description:
                    description = f"{description}\n\nPOC Email: {poc_email}"
                else:
                    description = f"POC Email: {poc_email}"

            if not zoho_id or not title or not start_str:
                return None

            event_id = f"thubcal_{zoho_id}"

            event_date = None
            for fmt in ("%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M"):
                try:
                    event_date = datetime.strptime(start_str, fmt).replace(tzinfo=_IST)
                    break
                except ValueError:
                    continue

            date_part = event_date.date().isoformat() if event_date else ""
            fingerprint = hashlib.md5(f"{title.lower()}|{date_part}".encode()).hexdigest()

            # Skip past events — only alert on today and future
            if event_date and event_date.date() < datetime.now(_IST).date():
                return None

            # Use registration link if available, otherwise calendar URL
            reg_link = details.get("registration_link", "")
            if reg_link and reg_link.startswith("http"):
                url = reg_link
            else:
                url = self.events_url

            return Event(
                event_id=event_id,
                source=self.source,
                title=title,
                url=url,
                event_date=event_date,
                location="T-Hub Hyderabad",
                price=None,
                is_free=True,   # per project spec: treat all T-Hub events as free
                description=description,
                event_fingerprint=fingerprint,
                registration_status="UNKNOWN",
            )
        except Exception as exc:
            logger.warning(f"Failed parsing zoho item {item.get('id')}: {exc!r}")
            return None

    @staticmethod
    def _deduplicate(events: list[Event]) -> list[Event]:
        unique: dict[str, Event] = {}
        for event in events:
            unique[event.event_id] = event
        return list(unique.values())
