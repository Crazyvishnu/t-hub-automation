from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

IST = timezone(timedelta(hours=5, minutes=30))


class RadarError(Exception):
    """Safe diagnostic, never containing credentials or request URLs."""


class HTTP:
    def request(self, url, *, payload=None, headers=None, method=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = Request(url, data=data, headers={
            'User-Agent': 'THubRadar/2.0', 'Accept': 'application/json',
            'Content-Type': 'application/json', **(headers or {})}, method=method)
        for attempt in range(3):
            try:
                with urlopen(req, timeout=25) as response:
                    return json.load(response)
            except HTTPError as exc:
                # Do not log the URL: Telegram puts the bot token in its path.
                if (exc.code == 429 or exc.code >= 500) and attempt < 2:
                    delay = 2 ** (attempt + 1)
                    try:
                        body = json.loads(exc.read())
                        delay = int(body.get('parameters', {}).get('retry_after', delay))
                    except (ValueError, TypeError):
                        pass
                    time.sleep(min(max(delay, 1), 60))
                    continue
                hint = {400: 'bad request or invalid chat ID', 401: 'invalid credentials',
                        403: 'permission denied; start/unblock the bot',
                        404: 'resource not found', 409: 'state conflict; stop overlapping runs',
                        429: 'rate limited'}.get(exc.code, 'upstream request failed')
                raise RadarError(f'HTTP {exc.code}: {hint}') from None
            except (URLError, TimeoutError, OSError):
                if attempt < 2:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise RadarError('Network timeout or connection failure') from None
            except (ValueError, UnicodeError):
                raise RadarError('Upstream returned invalid JSON') from None


@dataclass(frozen=True)
class Event:
    source: str
    source_id: str
    title: str
    url: str
    date: datetime
    location: str = 'T-Hub, Hyderabad'
    price: str = 'unknown'

    @property
    def keys(self):
        identity = hashlib.sha256(f'{self.source}:{self.source_id}'.encode()).hexdigest()
        name = re.sub(r'\W+', ' ', self.title.casefold()).strip()
        same_event = hashlib.sha256(f'{name}|{self.date.astimezone(IST).date()}'.encode()).hexdigest()
        return [identity, same_event]

    def message(self):
        price = {'free': 'Free (confirmed by source)', 'paid': 'Paid'}.get(
            self.price, 'Not published — check registration page')
        # Plain text avoids HTML/Markdown parsing failures. Bound each field.
        return (f'NEW T-HUB EVENT\n\n{self.title[:400]}\n\n'
                f'When: {self.date.astimezone(IST):%d %b %Y, %I:%M %p} IST\n'
                f'Where: {self.location[:300]}\nTicket price: {price}\n\n'
                f'Register / details:\n{self.url[:1200]}\n\nSource: {self.source}')


def parse_date(value):
    if not isinstance(value, str) or not value.strip():
        raise RadarError('Source event has no usable date')
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        for fmt in ('%m/%d/%Y %I:%M %p', '%m/%d/%Y %H:%M'):
            try:
                date = datetime.strptime(value, fmt)
                break
            except ValueError:
                continue
        else:
            raise RadarError('Source event date format changed') from None
    return date if date.tzinfo else date.replace(tzinfo=IST)


class Telegram:
    def __init__(self, token, chat, http=None):
        if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token or ''):
            raise RadarError('Set a valid TELEGRAM_BOT_TOKEN repository secret')
        if not re.fullmatch(r'-?\d+', chat or ''):
            raise RadarError('Set TELEGRAM_CHAT_ID to the numeric chat ID')
        self.token, self.chat, self.http = token, chat, http or HTTP()

    def send(self, text):
        result = self.http.request(f'https://api.telegram.org/bot{self.token}/sendMessage',
            payload={'chat_id': self.chat, 'text': text,
                     'link_preview_options': {'is_disabled': True},
                     'allow_paid_broadcast': False})
        if not isinstance(result, dict) or result.get('ok') is not True:
            raise RadarError('Telegram rejected the message; check bot and chat settings')
        time.sleep(3.1)  # Also respects the 20 messages/minute group limit.


def empty_state():
    return {'version': 1, 'delivered': {}}


def validate_state(value):
    if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('delivered'), dict):
        raise RadarError('Invalid state; refusing to reset delivery history')
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in value['delivered'].items()):
        raise RadarError('Invalid delivery history')
    return value


class FileState:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        if not self.path.exists():
            return empty_state()
        try:
            return validate_state(json.loads(self.path.read_text()))
        except (ValueError, OSError):
            raise RadarError('Cannot read state; refusing to reset delivery history') from None

    def save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(state, sort_keys=True, indent=2) + '\n')
        temp.replace(self.path)


class GitHubState:
    """Save after each accepted message; SHA checks prevent silent lost writes."""
    def __init__(self, repo, token, http=None):
        if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo or '') or not token:
            raise RadarError('Missing GITHUB_REPOSITORY or GITHUB_TOKEN')
        self.http = http or HTTP()
        self.base = f'https://api.github.com/repos/{repo}'
        self.headers = {'Authorization': f'Bearer {token}', 'X-GitHub-Api-Version': '2022-11-28'}
        self.sha = None

    def api(self, path, **kwargs):
        return self.http.request(self.base + path, headers=self.headers, **kwargs)

    def load(self):
        try:
            data = self.api('/contents/state.json?ref=radar-state')
        except RadarError as exc:
            if not str(exc).startswith('HTTP 404:'):
                raise
            # Bootstrap only if the entire state branch is absent. Missing file
            # on an existing branch is an error, not permission to resend all alerts.
            try:
                self.api('/git/ref/heads/radar-state')
            except RadarError as branch_error:
                if not str(branch_error).startswith('HTTP 404:'):
                    raise
                repo = self.api('')
                ref = self.api('/git/ref/heads/' + repo['default_branch'])
                self.api('/git/refs', payload={'ref': 'refs/heads/radar-state',
                                              'sha': ref['object']['sha']})
                state = empty_state()
                self.save(state)  # Check write permission before any notification.
                return state
            raise RadarError('radar-state exists but state.json is missing; restore it') from None
        try:
            state = validate_state(json.loads(base64.b64decode(data['content'])))
            self.sha = data['sha']
            return state
        except (KeyError, ValueError, TypeError):
            raise RadarError('Invalid remote state; refusing to reset history') from None

    def save(self, state):
        payload = {'message': 'Save Telegram delivery checkpoint', 'branch': 'radar-state',
                   'content': base64.b64encode((json.dumps(state, sort_keys=True) + '\n').encode()).decode()}
        if self.sha:
            payload['sha'] = self.sha
        result = self.api('/contents/state.json', payload=payload, method='PUT')
        self.sha = result['content']['sha']


def deliver(events, store, sender, *, now=None, free_only=False, limit=10):
    now = now or datetime.now(IST)
    state = store.load()
    count = 0
    for event in sorted(events, key=lambda e: e.date):
        if event.date.astimezone(IST).date() < now.astimezone(IST).date():
            continue
        if free_only and event.price != 'free':
            continue
        if any(key in state['delivered'] for key in event.keys):
            continue
        if count >= limit:
            break
        sender.send(event.message())
        for key in event.keys:
            state['delivered'][key] = now.isoformat()
        # Stop immediately if persistence fails. Never knowingly send more
        # messages while delivery history cannot be stored.
        store.save(state)
        count += 1
    return count
