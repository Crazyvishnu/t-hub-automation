import base64
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from radar.core import Event, FileState, GitHubState, HTTP, IST, RadarError, Telegram, deliver, empty_state, parse_date
from radar.sources import calendar_items, collect, parse_calendar, parse_portal, portal


def event(**changes):
    values = dict(source='portal', source_id='1', title='AI & <Robotics>',
                  url='https://tevents.t-hub.co/example', date=datetime(2026, 10, 9, 10, tzinfo=IST))
    values.update(changes)
    return Event(**values)


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = FileState(Path(self.temp.name) / 'state.json')
        self.now = datetime(2026, 10, 8, tzinfo=IST)
        self.sender = Mock()

    def send(self, events, **kwargs):
        return deliver(events, self.store, self.sender, now=self.now, **kwargs)

    def test_success_is_persisted_and_not_resent(self):
        self.assertEqual(self.send([event()]), 1)
        self.assertEqual(self.send([event()]), 0)
        self.sender.send.assert_called_once()

    def test_failed_delivery_is_retried_next_run(self):
        self.sender.send.side_effect = RadarError('failed')
        with self.assertRaises(RadarError):
            self.send([event()])
        self.assertEqual(self.store.load(), empty_state())
        self.sender.send.side_effect = None
        self.assertEqual(self.send([event()]), 1)

    def test_partial_batch_keeps_successful_checkpoint(self):
        self.sender.send.side_effect = [None, RadarError('failed')]
        with self.assertRaises(RadarError):
            self.send([event(), event(source_id='2', title='Other')])
        self.assertEqual(len(self.store.load()['delivered']), 2)

    def test_persistence_failure_stops_batch(self):
        self.store.save = Mock(side_effect=RadarError('state failure'))
        with self.assertRaises(RadarError):
            self.send([event(), event(source_id='2', title='Other')])
        self.sender.send.assert_called_once()

    def test_cross_source_duplicates(self):
        self.assertEqual(self.send([event(), event(source='calendar', source_id='99')]), 1)

    def test_past_events_skipped(self):
        self.assertEqual(self.send([event(date=datetime(2026, 10, 7, tzinfo=IST))]), 0)

    def test_today_retained(self):
        self.assertEqual(self.send([event(date=self.now)]), 1)

    def test_limit_defers_remaining(self):
        items = [event(source_id=str(i), title=f'Event {i}') for i in range(3)]
        self.assertEqual(self.send(items, limit=2), 2)
        self.assertEqual(self.send(items, limit=2), 1)

    def test_unknown_price_not_called_free(self):
        self.assertNotIn('Free (confirmed', event().message())
        self.assertEqual(self.send([event()], free_only=True), 0)
        self.assertEqual(self.send([event(price='free')], free_only=True), 1)

    def test_corrupt_state_fails_closed(self):
        self.store.path.write_text('{}')
        with self.assertRaises(RadarError):
            self.send([event()])
        self.sender.send.assert_not_called()

    def test_plain_text_and_length(self):
        self.assertIn('AI & <Robotics>', event().message())
        self.assertLess(len(event(title='😀' * 5000, location='x' * 5000, url='x' * 5000).message().encode('utf-16-le')) // 2, 4096)

    def test_date_formats(self):
        self.assertEqual(parse_date('2026-10-09T10:00:00+0530'), parse_date('10/09/2026 10:00 AM'))
        with self.assertRaises(RadarError):
            parse_date('bad')

    def test_telegram_validation(self):
        with self.assertRaises(RadarError):
            Telegram('', '12')
        with self.assertRaises(RadarError):
            Telegram('123:abc', 'replace-me')

    @patch('radar.core.time.sleep')
    def test_telegram_no_paid_or_parse_mode(self, sleep):
        http = Mock()
        http.request.return_value = {'ok': True}
        Telegram('123:abc', '-123', http).send(event().message())
        payload = http.request.call_args.kwargs['payload']
        self.assertIs(payload['allow_paid_broadcast'], False)
        self.assertNotIn('parse_mode', payload)

    @patch('radar.core.time.sleep')
    def test_telegram_rejection(self, sleep):
        http = Mock()
        http.request.return_value = {'ok': False}
        with self.assertRaises(RadarError):
            Telegram('123:abc', '123', http).send('test')

    @patch('radar.core.urlopen')
    def test_errors_do_not_expose_token(self, open_url):
        open_url.side_effect = HTTPError('https://api.telegram.org/bot123:secret/sendMessage', 403, 'secret', {}, None)
        with self.assertRaises(RadarError) as cm:
            HTTP().request('https://api.telegram.org/bot123:secret/sendMessage')
        self.assertNotIn('secret', str(cm.exception))

    @patch('radar.core.time.sleep')
    @patch('radar.core.urlopen')
    def test_rate_limit_retries(self, open_url, sleep):
        from io import BytesIO
        response = Mock()
        response.__enter__ = Mock(return_value=BytesIO(b'{"ok":true}'))
        response.__exit__ = Mock(return_value=False)
        open_url.side_effect = [HTTPError('https://example.org', 429, 'limit', {}, BytesIO(b'{"parameters":{"retry_after":2}}')), response]
        self.assertTrue(HTTP().request('https://example.org')['ok'])
        sleep.assert_called_once_with(2)

    def test_remote_state_uses_sha(self):
        http = Mock()
        http.request.side_effect = [{'content': base64.b64encode(json.dumps(empty_state()).encode()).decode(), 'sha': 'old'}, {'content': {'sha': 'new'}}]
        store = GitHubState('owner/repo', 'token', http)
        store.save(store.load())
        self.assertEqual(http.request.call_args.kwargs['payload']['sha'], 'old')
        self.assertEqual(store.sha, 'new')

    def test_missing_file_existing_branch_is_error(self):
        http = Mock()
        http.request.side_effect = [RadarError('HTTP 404: resource not found'), {'object': {'sha': 'abc'}}]
        with self.assertRaises(RadarError):
            GitHubState('owner/repo', 'token', http).load()
        self.assertEqual(http.request.call_count, 2)

    def test_bootstrap_checks_write_access(self):
        http = Mock()
        http.request.side_effect = [RadarError('HTTP 404: resource not found'), RadarError('HTTP 404: resource not found'), {'default_branch': 'main'}, {'object': {'sha': 'abc'}}, {}, {'content': {'sha': 'state'}}]
        state = GitHubState('owner/repo', 'token', http)
        self.assertEqual(state.load(), empty_state())
        self.assertEqual(state.sha, 'state')

    def test_portal_schema_and_empty(self):
        http = Mock()
        http.request.return_value = {'liveEventMetas': []}
        self.assertEqual(portal(http), [])
        http.request.return_value = {}
        with self.assertRaises(RadarError):
            portal(http)

    def test_portal_parse_unknown_price(self):
        item = {'meta': {'event': {'eventId': '1', 'eventKey': 'demo', 'tzStartDate': '2026-10-09T10:00:00+0530'}, 'eventTranslation': [{'langCode': 'en', 'name': 'Demo'}]}}
        self.assertEqual(parse_portal(item).price, 'unknown')
        with self.assertRaises(RadarError):
            parse_portal({})

    def test_calendar_extraction(self):
        items = [{'id': '1', 'title': 'Demo', 'start': '10/09/2026 10:00 AM'}]
        html = 'compMeta: JSON.parse(' + json.dumps(json.dumps({'EVENTS': items})) + ')'
        self.assertEqual(calendar_items(html), items)
        self.assertEqual(parse_calendar(items[0]).price, 'unknown')
        with self.assertRaises(RadarError):
            calendar_items('<html>changed</html>')

    def test_calendar_nested_model_events(self):
        items = [{'id': '1', 'title': 'Demo', 'start': '10/09/2026 10:00 AM'}]
        html = 'compMeta: JSON.parse(' + json.dumps(json.dumps({'MODEL': {'EVENTS': items}})) + ')'
        self.assertEqual(calendar_items(html), items)

    def test_calendar_unknown_payload_rejected(self):
        for body in ({'MODEL': {}}, {'EVENTS': 'invalid'}, {'EVENTS': [None]}):
            html = 'compMeta: JSON.parse(' + json.dumps(json.dumps(body)) + ')'
            with self.subTest(body=body), self.assertRaises(RadarError):
                calendar_items(html)

    def test_calendar_javascript_hex_escapes(self):
        items = [{'id': '1', 'title': 'AI & Robotics', 'start': '10/09/2026 10:00 AM'}]
        literal = json.dumps(json.dumps({'EVENTS': items}))
        literal = literal.replace(r'\"', r'\x22')
        self.assertEqual(calendar_items('compMeta: JSON.parse(' + literal + ')'), items)

    @patch('radar.sources.portal', side_effect=RadarError('schema changed'))
    def test_source_failure_is_visible(self, fetch):
        events, errors = collect(False)
        self.assertEqual(events, [])
        self.assertEqual(errors, ['portal: schema changed'])


if __name__ == '__main__':
    unittest.main()
