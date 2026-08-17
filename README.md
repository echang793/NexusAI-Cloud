# NexusAI Cloud

Multi-tenant personal finance dashboard — friends sign up with their own
email/password, add their own accounts (any institution, any account type,
fully free text) and holdings, and get CoastFIRE/FIRE projections,
contribution-limit tracking, rebalance drift, tax-loss harvesting scans,
dividend income, and a rule-based (no AI) portfolio advisor. No AI chat in
this version — everything is deterministic math over your own data.

This is a separate deploy from any personal single-user instance of
NexusAI — no data, files, or history are shared between them.

## Local development

Requires a Postgres instance (Docker is easiest):

```bash
docker run -d --name nexusai-pg -e POSTGRES_PASSWORD=devpass -e POSTGRES_DB=nexusai -p 5432:5432 postgres:16-alpine
```

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in DATABASE_URL and SECRET_KEY
.venv/bin/python3 server.py
```

Open http://localhost:5000, sign up, and start adding accounts.

## Deploying to Railway

Railway is **not free** — the Hobby plan is a ~$5/mo minimum (usage-based
beyond a one-time trial credit), plus the Postgres add-on's own usage cost.

1. Create a new Railway project, add this repo as a service (Nixpacks
   build is auto-detected via `railway.json`/`Procfile`).
2. Add a **Postgres** database to the same project — Railway auto-injects
   `DATABASE_URL` into the web service's environment.
3. Set the `SECRET_KEY` env var manually (any random 32+ byte string —
   `python3 -c "import secrets; print(secrets.token_hex(32))"`).
4. Optionally set `FINNHUB_API_KEY` for richer news (app works fine
   without it — falls back to yfinance-only).
5. Deploy. Tables are created automatically on first boot (`db.init_db()`
   runs `CREATE TABLE IF NOT EXISTS` — no separate migration step needed
   at this schema-stable, friends-scale size).

## What's different from a single-user NexusAI instance

- **Auth**: real accounts (Flask-Login + hashed passwords), not open access.
- **Storage**: Postgres, not flat JSON files — required since Railway's
  filesystem doesn't persist across deploys/restarts.
- **Accounts/holdings**: fully generic. Every friend creates their own
  accounts (name, institution, type — all free text) and holdings
  (ticker/shares/cost, tagged to one of their own accounts). No hardcoded
  institution list or account-type enum anywhere.
- **No AI advisor chat** — the Advisor tab's target-allocation plan,
  action items, and risk flags are all rule-based (deterministic math from
  your risk profile + holdings), not an LLM call. Keeps this deploy free
  of any shared API key cost/abuse surface.
- **CSV import** expects generic columns: `ticker, shares, avg_cost,
  account_name` — works with any brokerage's export reformatted to that
  shape, not just specific brokerages.

## Known limitations (same as the underlying math, not a Cloud-specific gap)

- No purchase-date/transaction history — tax-loss harvesting and the gains
  export can't split short vs. long-term or check wash sales.
- Net-worth-by-age percentile context is a rough estimate from public
  survey data, not personalized or precise.
- In-memory market-data caches (price/period) are per-process and not
  persisted — a cold restart re-fetches from yfinance, which is fine at
  this scale but means a brief "loading" state right after a deploy.
