# naukri-bot-public

Public multi-user Telegram bot that delivers filtered Naukri job alerts.

## Architecture

- **Web service** (Render free tier) — runs the Telegram webhook handler. Sleeps after 15 min idle; wakes on inbound updates.
- **Background worker** (Render free tier) — always-on. Runs APScheduler with three jobs:
  - Scrape every 6h → `jobs_pool`
  - Deliver hourly → per-user filtered batches to Telegram
  - Prune `sent_jobs` nightly
- **Postgres** (Render free 90-day) — single source of state.

## Local development

```bash
# 1. Postgres
docker run -d --name naukri-pg -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16
docker exec -it naukri-pg psql -U postgres -c "CREATE DATABASE naukri_bot;"

# 2. Env
cp .env.example .env
# fill in BOT_TOKEN

# 3. Install
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/python -m playwright install --with-deps chromium

# 4. Migrate
.venv/Scripts/alembic upgrade head

# 5. Run bot (polling mode)
.venv/Scripts/python -m app.bot.app

# 6. Run scheduler in another terminal
.venv/Scripts/python -m app.scheduler.worker
```

## Commands

| Command | Behavior |
|---|---|
| `/start` | Subscribe with defaults |
| `/set_keywords python,ml,pytorch` | Required keywords (OR) |
| `/set_exclude senior,manager,php` | Disqualifying keywords |
| `/set_locations bangalore,remote` | Location filter (substring) |
| `/set_experience 0` | Years of experience |
| `/set_frequency daily\|twice_daily\|weekly` | Delivery cadence |
| `/pause`, `/resume`, `/unsubscribe` | Lifecycle |
| `/status` | Show current settings |
| `/help` | Full reference |
