import argparse
import os
import sys
from datetime import datetime

from .core import FileState, GitHubState, IST, RadarError, Telegram, deliver, notify_health, resend, notify_source_health
from .sources import collect


def main():
    parser = argparse.ArgumentParser(description='T-Hub Telegram Radar')
    parser.add_argument('--mode', choices=['dry-run', 'test', 'run', 'resend'], default='dry-run')
    parser.add_argument('--portal-only', action='store_true')
    parser.add_argument('--free-only', action='store_true', help='Skip events without confirmed free pricing')
    parser.add_argument('--state-file', help='Local state; do not run alongside GitHub schedule')
    args = parser.parse_args()
    try:
        sender = None
        if args.mode != 'dry-run':
            sender = Telegram(os.environ.get('TELEGRAM_BOT_TOKEN', '').strip(),
                              os.environ.get('TELEGRAM_CHAT_ID', '').strip())
        if args.mode == 'test':
            sender.send('T-Hub Radar test: Telegram delivery works. Event monitoring is a separate step.')
            print('Telegram test accepted successfully.')
            return 0
        events, errors = collect(not args.portal_only)
        for error in errors:
            print('ERROR: ' + error, file=sys.stderr)
        if args.mode == 'dry-run':
            print(f'Dry run: {len(events)} parsed events; no messages sent or state changed.')
            upcoming = [e for e in events if e.date.astimezone(IST).date() >= datetime.now(IST).date()]
            print(f'Upcoming events: {len(upcoming)}')
            for event in upcoming[:5]:
                print(event.message() + '\n---')
        else:
            store = FileState(args.state_file) if args.state_file else GitHubState(
                os.environ.get('GITHUB_REPOSITORY', ''), os.environ.get('GITHUB_TOKEN', ''))
            # Report scraping failures even if another source still works.
            notify_source_health(store, sender, errors)
            if args.mode == 'run':
                # Warn on failed sources and recoveries without repeating each run.
                notify_health(store, sender, errors)
                count = deliver(events, store, sender, free_only=args.free_only)
                print(f'Telegram deliveries accepted and saved: {count}')
            else:
                count = resend(events, store, sender)
                print(f'Manual event replay sent: {count}; history unchanged')
        # A partially working source must not hide a broken source.
        return 1 if errors else 0
    except RadarError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    except Exception as exc:
        print(f'ERROR: unexpected {type(exc).__name__}; inspect tests/configuration', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
