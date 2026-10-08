# T-Hub Radar — Telegram event alerts

A Python automation that checks the public **T-Hub events portal** and **T-Hub events calendar**, then sends upcoming events to your own Telegram bot. Runs on GitHub Actions every 15 minutes without requiring a server or an always-on laptop.

**Fork or clone this project and use your own Telegram credentials.** Do not reuse anyone else's bot token or chat ID.

## Features

- Scrapes the T-Hub portal and its embedded Zoho calendar (current month plus three months).
- Sends event title, date/time in India Standard Time (IST), location, and event details link.
- Prefers published direct HTTPS event URLs; falls back to the general T-Hub calendar when no direct link is supplied. A link is not a guarantee of public registration.
- Suppresses repeat notifications using hashes saved in `state.json` on a separate `radar-state` branch.
- Sends Telegram alerts for scraping failures, recovery, and unexpected zero-event results, plus a daily healthy status message.
- Supports `dry-run`, `test`, `run`, and manually triggered `resend` modes.
- Keeps event history when using `resend` (up to 10 upcoming events per invocation).
- Uses GitHub Actions; no external database, Render deployment, or paid service is required for a public repository using standard hosted runners.

## Prerequisites

- Your own GitHub account and a **public** repository. This workflow deliberately skips private repositories to avoid unintended billing.
- Your own Telegram account and bot created with [@BotFather](https://t.me/BotFather).
- A numeric Telegram chat ID.
- Python 3.11+ for optional local use.

## Installation — fork/clone and configure your own bot

1. **Fork** this repository on GitHub (recommended if you want automatic GitHub Actions), or clone it locally:

   ```bash
   git clone https://github.com/Crazyvishnu/t-hub-automation.git
   cd t-hub-automation
   ```

   For a fresh independent repository, create your own GitHub repository and push the code there. Keep it **public** for the supplied workflow.

2. In Telegram, open [@BotFather](https://t.me/BotFather), send `/newbot`, and follow the instructions. Save the **bot token** privately. Never commit or share it. Open your new bot's chat and send `/start`.

3. Find your **chat ID**. You can use the safe helper after cloning:

   ```bash
   python -m radar.chat_id
   ```

   Enter your token only into the local hidden prompt. Send a message to the bot first. The helper reads Telegram `getUpdates` and prints candidate numeric chat IDs; it does not send Telegram messages. For a group, add the bot, send a message/command, and use the group's negative numeric chat ID. If the bot uses a webhook, use a separate bot or obtain its chat ID through that integration.

4. In **your** GitHub repository, open **Settings → Secrets and variables → Actions → New repository secret**. Create exactly these two secrets:

   | Secret name | Your value |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | Token from your BotFather bot |
   | `TELEGRAM_CHAT_ID` | Your numeric private chat ID or group ID |

   **Do not** put the actual values into source files, `README.md`, issues, workflow YAML, or screenshots.

5. Open **Settings → Actions → General**. Enable Actions and ensure the workflow's `GITHUB_TOKEN` can write repository contents (needed to save delivery history on `radar-state`). The workflow already requests `contents: write`; repository/organization settings must allow it. No personal access token is required.

6. In the **Actions** tab, enable the **T-Hub Radar** workflow if prompted. Select **Run workflow**, choose your default branch (`main`), then select:
   - **`test`**: sends one Telegram connectivity message, without scraping or modifying history.
   - **`dry-run`**: scrapes sources and prints results in the Actions log; sends no Telegram messages and does not change history.
   - **`run`**: sends unseen upcoming events, saves history, and sends health/failure notifications.
   - **`resend`**: manually sends up to 10 upcoming events again to inspect event links, without resetting delivery history.

7. After a successful `test` and `dry-run`, run `run` once. Future scheduled runs automatically use `run` at **07, 22, 37, and 52 minutes past every hour (UTC)**, approximately every 15 minutes. You do not need to keep your device on.

## How event history works

The monitor creates a separate `radar-state` branch with a `state.json` checkpoint file. This is **not** a SQL database. It contains event hashes, delivery timestamps, and health status; no bot credentials or chat ID. The default `main` branch is not used for mutable delivery history.

An event is marked delivered only after Telegram accepts its message. A crash between Telegram acceptance and the checkpoint can still cause a duplicate. Changing an event URL does not automatically resend an already delivered event. Use manual `resend` to check links; **do not delete delivery history** as a normal testing method.

## Scraping and health alerts

- The portal is accessed through its public event metadata endpoint, with bounded pagination.
- The calendar requires Chromium/Playwright to inspect the embedded calendar and navigate the next three months.
- A parsing/schema failure, browser timeout, or unexpected zero-event result is reported in GitHub Actions and can trigger a Telegram warning during `run`.
- Identical failure conditions are not repeatedly messaged. A recovery notification is sent after errors clear. One healthy-status message is sent per IST day.
- Zero events can legitimately occur; the warning is conservative and asks you to verify the source.
- The bot cannot notify you if GitHub Actions never starts, if credentials are broken, or if Telegram is unavailable. Configure GitHub failure notifications and optionally an independent uptime monitor.

## Local development and tests

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m playwright install chromium
python -m unittest discover -s tests -v
python -m radar --mode dry-run
```

For local message delivery, set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as **environment variables**, then run:

```bash
python -m radar --mode run --state-file local-state.json
```

Do not run a local sender concurrently with the hosted sender: their state files differ and duplicate messages may occur. Keep local state files and tokens out of Git. The repository does not load `.env` files automatically.

## Troubleshooting

| Problem | Check |
|---|---|
| Telegram `test` fails | Bot token, numeric chat ID, `/start`, group permissions |
| `dry-run` succeeds but `run` sends zero events | No unseen upcoming events, or history already contains them |
| Website HTML/API changed | Read the failing source in Actions logs; update `radar/sources.py` |
| Calendar event has only the calendar homepage URL | The upstream event data did not provide a usable direct HTTPS URL |
| State save fails | Confirm `contents: write` permission and existence/integrity of `radar-state/state.json` |
| Scheduled runs stop | Check Actions enablement, default branch, repository visibility, and GitHub's scheduled-workflow inactivity policy |
| Too many Telegram messages | Use `run` rather than `resend`; do not reset `radar-state` |

## Limitations and safety

- This is an independent community automation, **not an official T-Hub project**.
- Public listings may omit pricing or registration permissions. The bot does not claim events are free.
- Events may be cancelled or rescheduled without a follow-up alert; existing-event change detection is not implemented.
- GitHub Actions schedules can be delayed/skipped; public-repository scheduled workflows can be disabled after prolonged repository inactivity.
- A maximum of 10 unseen events are delivered per `run`; later runs handle the rest.
- Browser scraping depends on third-party HTML/API structures and may need maintenance.
- No external monitoring can be guaranteed by a bot whose own workflow has stopped.

See [GitHub Actions schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule) and [Telegram Bot FAQ](https://core.telegram.org/bots/faq).

## Contributing

Open an issue describing the source URL, observed behavior, and sanitized logs. Never include bot tokens, private chats, GitHub secrets, or personal credentials. Submit pull requests with tests for changes to parsing or message delivery.

## License

No license file is currently included. Forking on GitHub is subject to GitHub's applicable terms; for broader third-party reuse or redistribution, the repository owner should add an explicit open-source license.
