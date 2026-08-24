# Hyderabad Event Radar

Cloud-ready monitor for newly published free T-Hub events. Stores all detected events in Supabase, sends exactly one Telegram alert per new free event, and runs every 5 minutes via an external cron trigger. The service is stateless: Render can restart it at any time without losing event history.

## Architecture

```
cron-job.org (every 5 min)
        |
        | GET /run?token=SECRET
        v
  Render Free  (Docker: Python 3.11)
        |
        v
  T-Hub scraper ── HTTP GET ──► Backstage API
        |
        v
  Event normaliser (source-neutral Event model)
        |
        v
  Supabase PostgreSQL (upsert, duplicate prevention)
        |
     New + Free?
        |
        v
  Telegram Bot ── one message per event
```

## What is included

| Component | Details |
|---|---|
| `GET /health` | Returns `{"status": "ok"}` immediately |
| `GET /run?token=…` | Runs one full monitoring cycle; token-protected |
| T-Hub scraper | Uses the public T-Hub Backstage API. Ultra-fast, pure HTTP, no browser needed. |
| Event model | Source-neutral, validates required fields |
| Supabase repo | Upsert with `event_id` unique constraint; atomic notification claim via `FOR UPDATE SKIP LOCKED` |
| Telegram notifier | IST date/time display, HTML formatting, admin alerts |
| Source health monitoring | Tracks `consecutive_failures`; sends Telegram admin alert after 3 failures |
| Docker image | Based on lightweight `python:3.11-slim` |
| Database schema | `events` + `source_status` tables, triggers, RPCs, 90-day cleanup |

## Local setup

### 1. Configure environment

```powershell
Copy-Item .env.example .env
# Edit .env and fill in all required values
```

### 2. Create Supabase database

1. Create a free project at [supabase.com](https://supabase.com).
2. Open **SQL Editor** and paste the contents of [`database/schema.sql`](database/schema.sql).
3. Run it. This creates the `events` and `source_status` tables and all required functions.

### 3. Install and run locally

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### 4. Test the endpoints

```powershell
# Health check
Invoke-RestMethod "http://localhost:8000/health"

# Trigger a monitoring run
$secret = (Get-Content .env | Select-String "RUN_SECRET").ToString().Split("=")[1]
Invoke-RestMethod "http://localhost:8000/run?token=$secret"
```

## Tests

```powershell
py -m pytest -v
```

32 tests covering: scraper API parsing, price/date helpers, notification formatting (IST timezone), and event processor logic (mocked dependencies).

## Deploy to Render

1. Push this repository to GitHub.
2. In Render, create a **Web Service** → **Deploy from Docker**.
3. Set these environment variables in Render:

   | Variable | Value |
   |---|---|
   | `SUPABASE_URL` | `https://your-project.supabase.co` |
   | `SUPABASE_KEY` | Service-role key (not anon key) |
   | `TELEGRAM_BOT_TOKEN` | From [@BotFather](https://t.me/botfather) |
   | `TELEGRAM_CHAT_ID` | Your Telegram chat/channel ID |
   | `RUN_SECRET` | A long random string (e.g. `openssl rand -hex 32`) |

4. Confirm the service is live:

   ```
   https://YOUR-SERVICE.onrender.com/health  →  {"status": "ok"}
   ```

5. At [cron-job.org](https://cron-job.org) create one GET job, every **5 minutes**:

   ```
   https://YOUR-SERVICE.onrender.com/run?token=YOUR_RUN_SECRET
   ```

6. In Supabase **SQL Editor**, schedule the 90-day cleanup (weekly or monthly):

   ```sql
   delete from public.events where first_seen < now() - interval '90 days';
   ```

## Operational notes

- **API detection**: T-Hub uses Ember.js with the Backstage events API. The scraper calls this API directly, returning events instantly without loading a headless browser.
- **No duplicate alerts**: New events are inserted atomically. `FOR UPDATE SKIP LOCKED` prevents overlapping `/run` calls from claiming the same notification.
- **Resilient failures**: A scraper error is logged and reported as `partial_failure`. Existing events are never deleted. The next scheduled run retries automatically.
- **Telegram failure recovery**: If Telegram is unavailable, the notification claim is released. The event stays in the database and will be retried on the next run.
- **Source health alerts**: After 3 consecutive failures for any source, an admin alert is sent to the Telegram chat.
- **Secrets**: Never commit `.env`. Use Render environment variables in production.

## Project structure

```
app/
├── main.py                  FastAPI app (/health, /run)
├── config.py                Settings from environment variables
├── models/event.py          Source-neutral Event model
├── scrapers/
│   ├── base.py              EventScraper ABC
│   └── thub.py              T-Hub scraper (HTTP API)
├── database/supabase.py     Supabase REST client + health monitoring
├── notifications/
│   ├── base.py              Notifier ABC (send + send_raw)
│   └── telegram.py          Telegram notifier (IST timezone)
├── services/event_processor.py  Orchestrates scrape→upsert→notify
└── utils/logger.py          Logging configuration

database/schema.sql          Supabase schema (run once)
tests/                       32 unit tests (no network required)
Dockerfile                   python:3.11-slim image
```
