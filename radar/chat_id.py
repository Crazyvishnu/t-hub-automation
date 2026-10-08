"""Read-only helper; never print bot token or private message contents."""
from getpass import getpass
from .core import HTTP, RadarError


def main():
    token = getpass('Bot token (hidden): ').strip()
    from .core import Telegram
    try:
        Telegram(token, '1')  # Validate token shape; does not make a request.
        result = HTTP().request(f'https://api.telegram.org/bot{token}/getUpdates')
        if result.get('ok') is not True:
            raise RadarError('getUpdates rejected; check bot configuration')
        chats = {}
        for update in result.get('result', []):
            for field in ('message', 'channel_post', 'my_chat_member'):
                chat = update.get(field, {}).get('chat', {})
                if 'id' in chat:
                    chats[chat['id']] = chat.get('type', 'unknown')
        if not chats:
            print('No chats found. Send /start to your bot, then try again.')
        for identifier, kind in chats.items():
            print(f'Chat ID: {identifier} | type: {kind}')
    except RadarError as exc:
        print(f'ERROR: {exc}')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
