# T-Hub Event Radar

Automated monitor for newly published free T-Hub events.  Scrapes the T-Hub
portal and Zoho Calendar every 5 minutes, stores all events in Supabase (for
duplicate prevention and history), and sends instant alerts to **WhatsApp**
via an OpenWA gateway — with automatic **Telegram** fallback if WhatsApp is
ever unreachable.

---

## Architecture

```text
                  cron-job.org (every 5 min)
                         │  GET /run?token=…
                         ▼
                   Render (FastAPI)
                         │
          ┌──────────────┴──────────────┐
          ▼                             ▼
  T-Hub Events API             T-Hub Zoho Calendar
          │                             │
          └──────────────┬──────────────┘
                         ▼
                   Event Processor
                         │
                         ▼
                Supabase PostgreSQL
                         │
               qualifying free event
                         │
                         ▼
               Notification Manager
                         │
            ┌────────────┴────────────┐
            ▼                         ▼
  HeavenCloud OpenWA             Telegram Bot
   (Baileys engine)               (fallback)
            │                         ▲
            │  success                │
            └────── failure ──────────┘
                         │
                         ▼
                        You
```

**Key properties:**
- Render hosts the Python radar (unchanged).
- HeavenCloud hosts the OpenWA WhatsApp gateway (replacing livemy.app).
- Supabase holds all event data — never on HeavenCloud.
- Circuit breaker: after 3 consecutive WhatsApp failures the manager uses
  Telegram directly until WhatsApp recovers.

---

## Quick-start (local testing)

```powershell
Copy-Item .env.example .env   # fill in your values
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run_local.py            # one full scrape + notify cycle
```

---

## Setup guide

### Step 1 — Supabase database

1. Create a free project at [supabase.com](https://supabase.com).
2. **SQL Editor → New query** → paste `database/schema.sql` → **Run**.
3. Go to **Project Settings → API** and copy:
   - `Project URL` → `SUPABASE_URL`
   - `service_role` secret key → `SUPABASE_KEY`

### Step 2 — Telegram bot (fallback)

1. Open Telegram and message [@BotFather](https://t.me/botfather) → `/newbot`.
2. Copy the token → `TELEGRAM_BOT_TOKEN`.
3. Send your bot a message, then visit
   `https://api.telegram.org/bot<token>/getUpdates` to find your chat ID →
   `TELEGRAM_CHAT_ID`.

### Step 3 — OpenWA on HeavenCloud (WhatsApp primary)

HeavenCloud free WhatsApp tier: ~715 MB RAM, 1 GB SSD, 75% CPU, 24/7.

> **Use a secondary/burner phone number** — never your primary personal number.
> WhatsApp's ToS prohibits bots on personal accounts.

#### 3a. Deploy OpenWA

1. Sign up at [heavencloud.app](https://heavencloud.app) (no credit card).
2. Create a new **WhatsApp** or **Bot** project.
3. If Docker deployment is available, use the `rmyndharis/openwa` image:

   ```yaml
   # docker-compose for reference — adapt to HeavenCloud's UI
   services:
     openwa:
       image: rmyndharis/openwa:latest
       restart: unless-stopped
       ports:
         - "2785:2785"
       environment:
         ENGINE_TYPE: baileys          # lightweight — no Chromium
         SESSION_DATA_PATH: /app/data/sessions
         API_KEY: ${OPENWA_API_KEY}
       volumes:
         - openwa_sessions:/app/data/sessions   # PERSISTENT — survives restarts

   volumes:
     openwa_sessions:
   ```

4. Set environment variables in HeavenCloud:

   | Variable | Value |
   |---|---|
   | `ENGINE_TYPE` | `baileys` |
   | `SESSION_DATA_PATH` | `/app/data/sessions` |
   | `API_KEY` | a long random secret you choose |

5. Mount a persistent volume at `/app/data/sessions`.  Without this, every
   restart requires re-scanning the QR code.

#### 3b. Link WhatsApp

1. Once the container is running, open the HeavenCloud service URL in your browser (the OpenWA dashboard).
2. Go to **Sessions → + New Session**, name it (e.g. `t-hub-bot`).
3. Click the session → **QR Code** → scan with your phone's WhatsApp
   (**Linked Devices → Link a device**).
4. Go to **API Keys** → generate a key.  Save it as `OPENWA_API_KEY`.
5. Copy the Session UUID → `OPENWA_SESSION_ID`.
6. Copy the dashboard base URL → `OPENWA_URL`
   (e.g. `https://your-app.heavencloud.app`).

#### 3c. Verify persistence (required)

1. Send a test message from the dashboard **Message Tester**.
2. **Restart** the HeavenCloud container.
3. Wait ~30 seconds for it to come back up.
4. Send another test message — you should **not** be asked to scan again.
5. If re-scan is required: check that the volume is mounted correctly.

### Step 4 — Configure the Python radar

Edit `.env` with all values collected above:

```ini
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_service_role_key

TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
TELEGRAM_MODE=FALLBACK

RUN_SECRET=a_long_random_string

OPENWA_URL=https://your-app.heavencloud.app
OPENWA_API_KEY=your_openwa_api_key
OPENWA_SESSION_ID=your-session-uuid
WHATSAPP_TARGET_NUMBER=919876543210

# Optional tuning
WHATSAPP_TIMEOUT_SECONDS=10
OPENWA_FAILURE_THRESHOLD=3
```

### Step 5 — Deploy to Render

1. Push the repository to GitHub.
2. In [Render](https://render.com), create a **Web Service → Docker**.
3. Copy all variables from your `.env` into Render's **Environment** tab.
4. Deploy. Verify: `https://YOUR-APP.onrender.com/health` → `{"status":"ok"}`.

### Step 6 — Automate with cron-job.org

1. Create a free account at [cron-job.org](https://cron-job.org).
2. New cron job → **every 5 minutes** → URL:
   ```
   https://YOUR-APP.onrender.com/run?token=YOUR_RUN_SECRET
   ```
3. Done. The radar will now silently monitor T-Hub 24/7.

---

## Self-healing behaviour

| Situation | What happens |
|---|---|
| OpenWA session sleeping | Code sends `/start`, waits 2 s, retries automatically |
| WhatsApp fails once | Fallback to Telegram; event logged as `WHATSAPP:ERROR, TELEGRAM:SUCCESS` |
| WhatsApp fails 3× in a row | Circuit breaker opens — Telegram used directly until WhatsApp recovers |
| Circuit breaker open, WhatsApp heals | Breaker closes automatically on next successful send |
| T-Hub redesigns their website | Scraper raises `REDESIGN_DETECTED`; 🚨 alert sent to WhatsApp + Telegram |
| HeavenCloud restarts container | Session reloads from persistent volume — no re-scan required |

---

## Reconnecting WhatsApp after session loss

If the session is permanently lost (e.g. phone changed, WhatsApp banned the
session, volume was wiped):

1. Open the HeavenCloud dashboard.
2. **Sessions → your session → Unlink / Delete**.
3. Click **+ New Session**, same name.
4. Scan the new QR code.
5. Update `OPENWA_SESSION_ID` in Render environment variables if the UUID changed.

---

## Project structure

```
app/
├── main.py                       FastAPI app (/health, /run)
├── config.py                     Settings from environment variables
├── models/event.py               Source-neutral Event model
├── scrapers/
│   ├── thub.py                   T-Hub Events API scraper
│   └── thub_calendar.py          T-Hub Zoho Calendar scraper
├── database/supabase.py          Supabase REST client
├── notifications/
│   ├── base.py                   Notifier abstract base
│   ├── openwa.py                 WhatsApp via OpenWA (provider-independent)
│   ├── telegram.py               Telegram fallback
│   └── manager.py                Primary→fallback routing + circuit breaker
└── services/event_processor.py   Orchestrates scrape → upsert → notify

database/schema.sql               Run once in Supabase SQL Editor
run_local.py                      Local one-shot test run
Dockerfile                        python:3.11-slim + Playwright (for calendar scraper)
```

---

## Security notes

- Never commit `.env` to Git (`.gitignore` already excludes it).
- Use Render's encrypted environment variables in production.
- The `/run` endpoint requires `RUN_SECRET` — keep it long and random.
- Use a burner phone number for the OpenWA WhatsApp session.
- OpenWA dashboard should be password-protected (set `DASHBOARD_USERNAME` /
  `DASHBOARD_PASSWORD` env vars in HeavenCloud if supported).
