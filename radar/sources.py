"""Public T-Hub sources. Schema changes fail visibly instead of looking empty."""
import asyncio
import json
import re
from urllib.parse import quote

from .core import Event, HTTP, RadarError, parse_date

PORTAL = 'https://tevents.t-hub.co'
CALENDAR = 'https://www.t-hub.co/events-calendar'


def parse_portal(item):
    try:
        meta = item['meta']
        raw = meta['event']
        translations = meta['eventTranslation']
        translation = next((v for v in translations if v.get('langCode') == 'en'), translations[0])
        title = translation['name'].strip()
        source_id = str(raw.get('eventId') or item['id'])
        key = raw['eventKey']
        if not title or not key:
            raise ValueError()
        venues = meta.get('venueTranslation') or []
        location = ', '.join(str(venues[0][k]) for k in ('name', 'city', 'state')
                             if venues[0].get(k)) if venues else 'See event page'
        return Event('T-Hub portal', source_id, title,
                     f'{PORTAL}/{quote(str(key), safe="")}',
                     parse_date(raw.get('tzStartDate') or raw.get('startDate')),
                     location, 'unknown')
    except (KeyError, IndexError, TypeError, AttributeError, ValueError):
        raise RadarError('T-Hub portal event schema changed') from None


def portal(http=None):
    http = http or HTTP()
    events = []
    for page in range(1, 21):
        data = http.request(f'{PORTAL}/public/portals/672982250/eventsMeta?pageSize=15&type=live&page={page}')
        if not isinstance(data, dict) or not isinstance(data.get('liveEventMetas'), list):
            raise RadarError('T-Hub portal: liveEventMetas missing or invalid')
        batch = data['liveEventMetas']
        events.extend(parse_portal(item) for item in batch)
        if len(batch) < 15:
            return events
    raise RadarError('T-Hub pagination exceeded safety limit; inspect source')


def calendar_items(html):
    match = re.search(r'compMeta\s*:\s*JSON\.parse\(("(?:\\.|[^"\\])*")\)', html, re.S)
    if not match:
        raise RadarError('T-Hub calendar embed changed: compMeta missing')
    try:
        body = json.loads(json.loads(match.group(1)))
        events = body['EVENTS']
        if not isinstance(events, list):
            raise ValueError()
        return events
    except (ValueError, KeyError, TypeError):
        raise RadarError('T-Hub calendar payload changed') from None


def parse_calendar(item):
    try:
        title = item['title'].strip()
        identifier = str(item['id'])
        if not title or not identifier:
            raise ValueError()
        # Calendar entries do not establish price or public registration.
        return Event('T-Hub calendar (check access on event page)', identifier,
                     title, CALENDAR, parse_date(item['start']))
    except (KeyError, TypeError, AttributeError, ValueError):
        raise RadarError('T-Hub calendar event schema changed') from None


async def calendar():
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RadarError('Install requirements.txt and run: python -m playwright install chromium') from None
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto(CALENDAR, wait_until='domcontentloaded', timeout=45000)
            iframe = await page.wait_for_selector('iframe.form-iframe', timeout=20000)
            frame = await iframe.content_frame()
            if frame is None:
                raise RadarError('T-Hub calendar iframe unavailable')
            await frame.wait_for_selector('.zc-calendar-cont', state='attached', timeout=30000)
            items = calendar_items(await frame.content())
            for _ in range(3):
                button = frame.locator('[title="Next Month"]')
                if await button.count() == 0:
                    raise RadarError('Calendar next-month control changed')
                async with page.expect_response(lambda r: 'report-embed-json' in r.url, timeout=20000) as pending:
                    await button.first.click(force=True)
                response = await pending.value
                if not response.ok:
                    raise RadarError('Calendar month request failed')
                body = await response.json()
                batch = body.get('MODEL', {}).get('EVENTS')
                if not isinstance(batch, list):
                    raise RadarError('Calendar month response schema changed')
                items.extend(batch)
            unique = {str(item['id']): item for item in items}
            return [parse_calendar(item) for item in unique.values()]
        except RadarError:
            raise
        except Exception:
            # Browser errors can contain long URLs and page contents. Keep
            # diagnostics useful without copying upstream content into logs.
            raise RadarError('Calendar browser failed or timed out; inspect the public calendar structure') from None
        finally:
            await browser.close()


def collect(include_calendar=True):
    events, errors = [], []
    for name, fetch in [('portal', portal)] + ([('calendar', lambda: asyncio.run(calendar()))] if include_calendar else []):
        try:
            result = fetch()
            print(f'{name}: {len(result)} events parsed')
            events.extend(result)
        except RadarError as exc:
            errors.append(f'{name}: {exc}')
    return events, errors
