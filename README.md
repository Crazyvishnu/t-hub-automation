# T-Hub Radar — free Telegram automation

A fresh Telegram-only replacement for the previous Render / Supabase / WhatsApp stack.
Checks T-Hub's event portal and calendar, then sends unseen upcoming events to your Telegram chat.

## Cost: ₹0 with the configuration in this repository

| Resource | Purpose | Free condition |
|---|---|---|
| Public GitHub repository | Code and small delivery-state file | Keep this repository public |
| GitHub Actions standard Ubuntu runner | Scheduled checks every 15 minutes | Workflows refuse to run in private repositories; no larger runners |
| Telegram Bot API | Personal/group notifications | Ordinary messages only; paid broadcasts explicitly disabled |
| T-Hub public pages | Event discovery | No subscription or API key |

No Render, Supabase, paid domain, VPS, AI API, external cron account, cache, or uploaded Actions artifact is required. Your computer does not have to stay on. Pricing was checked on October 8, 2026; providers can change policies.

References: [GitHub free runner usage](https://docs.github.com/en/billing/concepts/product-billing/github-actions), [Telegram free messaging](https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this).

## Setup — do these in order

1. **Review and merge this rebuild into the default branch (`main`).** Scheduled workflows only run from the default branch. Before merging, disable auto-deploy for the OLD Render service and disable its cron-job.org trigger: this rebuild deliberately removes the old Docker web server. Do not run the old and new monitors together unless you want duplicate alerts.
2. **Create or reuse a Telegram bot.** In Telegram, message the verified `@BotFather`, use `/newbot`, and copy the bot token privately. Open your new bot and press **Start** (or send `/start`). Bots cannot initiate a private conversation before you do this.
3. **Find your numeric chat ID.** Use the local helper below. If using a group, add the bot and send a command mentioning it first. Do not publish your token in an issue, screenshot, URL, or repository.
4. **Add two repository secrets:** GitHub → Settings → Secrets and variables → Actions → New repository secret:
   - `TELEGRAM_BOT_TOKEN`: token from BotFather.
   - `TELEGRAM_CHAT_ID`: numeric chat ID (group IDs may be negative).
5. **Check Actions permissions:** Settings → Actions → General. Allow this repository's workflows and give `GITHUB_TOKEN` read/write contents permission. No personal access token is needed. Organization policy can override this; the state write must succeed.
6. **Test Telegram:** Actions → **T-Hub Radar** → Run workflow → branch `main` → mode **test**. Expect one confirmation message. This does not run scrapers or alter event history.
7. **Test sources:** Run workflow again in **dry-run** mode. It sends nothing and changes no state. Both sources should report parsed counts. Zero counts are possible; errors are not treated as successful empty results.
8. **Start monitoring:** Run once in **run** mode. It sends up to 10 unseen upcoming events, saving each delivery. Further runs drain the remaining events. The schedule then checks at minutes 7, 22, 37, and 52 of every hour (UTC; same 15-minute cadence in India).

The schedule begins after merge, so add secrets promptly or initial runs will fail clearly until setup is complete. A green `test` confirms Telegram; a green `dry-run` confirms scraping; a green `run` with zero deliveries just means there were no eligible unseen events.

## Safe local helper: discover chat IDs

From the repository directory, with Python 3.11+ installed:

```text
python -m radar.chat_id
```

The token prompt is hidden. The helper calls only `getUpdates`; it does not send messages, remove a webhook, or acknowledge updates. If the bot already has a webhook, use that bot's existing integration to obtain the chat ID or create a separate bot. Update history can contain private messages, so the helper prints only candidate chat IDs and types.

## How it works

- Portal source: public Backstage `eventsMeta` API, with pagination.
- Calendar source: Playwright reads the Zoho embed and the next three months.
- Dates are displayed in **IST**. Events before today's IST date are ignored.
- Source IDs and normalized title/date hashes prevent repeat and cross-source alerts.
- State is a small `state.json` file on the separate **radar-state** branch, created automatically. It contains hashes and timestamps, not bot credentials, chat IDs, or message contents.
- A successful Telegram API response is required before marking an event delivered. Failed delivery stops the batch and is retried on a later run.
- Each accepted message is checkpointed immediately through GitHub's Contents API. A state-write failure stops further sends.
- HTTP rate limits and transient server errors have bounded retries. Errors redact credentials and make the workflow red.
- Concurrent monitor workflows are serialized. Do not run another local sender against the same chat at the same time.

### Ticket price and access

Free infrastructure does not mean every T-Hub event is free. The listing sources do not reliably expose pricing. Alerts therefore say **“Not published — check registration page”** instead of making up a free price. Calendar listings can include restricted events; check access and registration on the linked calendar. `--free-only` excludes unknown prices; because current adapters do not confirm ticket pricing, this option can produce no alerts. Price verification is not implemented.

### Reliability boundaries

This is tested code, not a promise of zero errors. T-Hub can change its undocumented API or page structure. GitHub scheduled jobs can be delayed or dropped during high load and schedules in public repositories can be disabled after 60 days without repository activity. Check the Actions tab periodically and re-enable a disabled schedule. See [GitHub schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

Delivery is **at least once**, not exactly once: a network timeout after Telegram accepts a message, a process crash between send and checkpoint, or a failed state save can cause a repeat on the next run. Never delete `radar-state` to fix an unrelated error—it would resend events. Matching title/day across sources may merge distinct same-name events on the same day.

Enable GitHub's workflow-failure notifications in your GitHub notification settings. Telegram cannot report its own broken credentials to the same chat. The monitor does not send a heartbeat every 15 minutes.

## Troubleshooting

| Symptom | Action |
|---|---|
| Invalid/missing token or chat ID | Correct the two Actions secrets; run `test` |
| HTTP 401 | Replace invalid bot token; check which operation failed |
| HTTP 403 | Start/unblock bot or check GitHub contents write permission |
| Telegram test succeeds, no events arrive | Inspect dry-run source counts; previous deliveries are intentionally skipped |
| Calendar schema/browser error | Workflow fails visibly; portal results can still deliver in run mode; update calendar adapter |
| Portal liveEventMetas error | Upstream schema changed; update portal adapter |
| Invalid state / missing state.json | Restore the last valid state file on radar-state; do not reset history |
| State conflict (409) | Stop other writers; next serialized run reloads state |
| No scheduled executions | Ensure workflow is on main, Actions enabled, public repo, schedule not disabled |

## Local development

No secrets are needed for tests or dry-run. Test mode and run mode send real messages only when explicitly selected.

```text
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
python -m unittest discover -s tests -v
python -m radar --mode dry-run
python -m radar --mode dry-run --portal-only
```

For a local sender, set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in your process environment and use `python -m radar --mode run --state-file state.json`. `.env` files are intentionally not loaded automatically. Keep local history separate and disable the hosted sender while testing.

## Validation of this rebuild

24 offline tests cover delivery checkpointing, partial batches, retry eligibility, state corruption, state branch bootstrap, rate limits, secret redaction, plain-text message size, parsing, source changes, duplicate suppression, and IST dates. Live Telegram delivery requires your two secrets and the explicit `test` step. Live source access must pass `dry-run` from GitHub Actions before treating the monitor as operational.
