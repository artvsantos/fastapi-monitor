# Website Monitor

[![CI](https://github.com/artvsantos/fastapi-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/artvsantos/fastapi-monitor/actions/workflows/ci.yml)

[Português](README.md) · **English**

An uptime monitoring API built with FastAPI. You create an account and add
the URLs you want to watch, each with its own check interval. A background
worker checks every site, stores the status and response time, and sends a
webhook when a site goes down or comes back. The API shows the history and
the uptime percentage.

The API itself (messages, docs) is in Portuguese; the code is in English.

## Features

- Sign-up and login with JWT.
- Monitors visible only to their owner, checked every 1 minute to 1 day.
- Check history with cursor pagination.
- Stats per period (24h, 7d, 30d): uptime, average response time and
  number of outages.
- HMAC-signed webhook alerts when the status changes, retried if the
  receiver fails.
- History older than 30 days is deleted automatically.
- Interactive docs at `/docs`.

## Design decisions

**SSRF protection.** The server requests URLs that anyone can submit.
Without a filter, someone could register `http://169.254.169.254` (the
cloud credentials) or `http://postgres:5432` and use the server to reach
the internal network. The filter has two layers:

1. On creation, it rejects non-http(s) schemes, credentials in the URL,
   `localhost` and internal IPs, and resolves the hostname to catch public
   domains that point inside the network.
2. On every check, DNS is resolved again **at connection time**: a custom
   httpcore network backend resolves the name, validates the address and
   connects to that exact IP. This closes *DNS rebinding*, where a domain
   returns a public IP at creation and an internal one later. Redirects
   are not followed, and proxy settings from the environment are ignored.

The webhook URL goes through the same filter.

**Postgres as the queue, no Redis.** Each monitor stores its next check
time. The worker claims due monitors with
`SELECT ... FOR UPDATE SKIP LOCKED` and schedules the next check in the
same transaction, so several workers can run without checking the same
site twice. At this scale, Redis would be one more moving part with no
real gain.

**Alerts that are not lost (outbox pattern).** The alert is written in the
same transaction as the check that changed the status; delivery happens
afterwards. If the worker crashes in between, the alert is still queued.
If the webhook fails, it is retried after 1, 2, 4 and 8 minutes. Alerts
for the same monitor go out in order: "back up" waits for "down" to be
delivered. Delivery is at-least-once, so the body carries an `alert_id`
for the receiver to drop duplicates.

**Stats computed in the database.** Counts, average and percentage come
from a single query instead of loading thousands of rows into Python.
Outages are counted with the `LAG()` window function: a failed check whose
previous check was up.

**Cursor pagination.** The history uses `before=<timestamp>` instead of
`?page=2`. A new check doesn't shift items between pages, and the query
uses the `(monitor_id, checked_at)` index on any page, whereas `OFFSET`
would read and discard everything before it.

**Passwords and tokens.** Passwords are hashed with argon2. Logging in
with an unknown email takes as long as a wrong password, so the API does
not reveal who has an account. JWTs require `sub` and `exp`, and only the
expected algorithm is accepted. The API refuses to start against a real
database with the development key.

## Running locally

With Docker, this starts the API, the worker and Postgres:

```bash
docker compose up --build
```

Docs at http://localhost:8000/docs.

Without Docker, with Python 3.13 or newer (uses SQLite):

```bash
python -m venv venv
venv/bin/pip install -r requirements-dev.txt   # Windows: venv\Scripts\pip
venv/bin/alembic upgrade head
RUN_WORKER_IN_API=1 venv/bin/uvicorn app.main:app --reload
```

## Usage example

```bash
API=http://localhost:8000

curl -X POST $API/auth/register -H 'Content-Type: application/json' \
  -d '{"email": "ana@example.com", "password": "a-strong-password"}'

TOKEN=$(curl -s -X POST $API/auth/token \
  -d 'username=ana@example.com&password=a-strong-password' | jq -r .access_token)

curl -X POST $API/monitors -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name": "My site", "url": "https://example.com/",
       "interval_minutes": 5, "webhook_url": "https://my-server.com/alerts"}'

curl $API/monitors/1/stats?period=7d -H "Authorization: Bearer $TOKEN"
```

```json
{
  "period": "7d",
  "since": "2026-10-20T12:00:00Z",
  "checks": 2016,
  "up_checks": 2010,
  "uptime_percent": 99.7,
  "avg_response_ms": 182,
  "incidents": 2
}
```

## Webhook

When a site goes down or comes back, the registered URL receives a `POST`:

```json
{
  "event": "down",
  "alert_id": 42,
  "message": "My site (https://example.com/) caiu: O site respondeu com o código 503.",
  "monitor": {"id": 1, "name": "My site", "url": "https://example.com/"},
  "check": {
    "checked_at": "2026-10-27T12:00:00+00:00",
    "status_code": 503,
    "response_time_ms": 241,
    "error": "O site respondeu com o código 503."
  }
}
```

A 2xx response counts as delivered. Each monitor has a `webhook_secret`,
and the signature is sent in the `X-Monitor-Signature` header. The
timestamp is part of the signed content, so a captured alert can't be
replayed later. To verify it:

```python
import hashlib
import hmac
import time


def is_valid_alert(secret: str, headers: dict, body: bytes) -> bool:
    timestamp = headers['X-Monitor-Timestamp']
    if abs(time.time() - int(timestamp)) > 300:  # older than 5 minutes
        return False
    expected = hmac.new(
        secret.encode(), f'{timestamp}.'.encode() + body, hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(headers['X-Monitor-Signature'],
                               f'sha256={expected}')
```

## Configuration

Everything comes from environment variables (or a `.env` file):

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | local SQLite | Accepts the Postgres URL as Neon shows it. |
| `SECRET_KEY` | development only | Signs the tokens. Required (32+ characters) with Postgres. |
| `RUN_WORKER_IN_API` | `false` | Runs the worker inside the API, for single-process hosting. |
| `WORKER_INTERVAL_SECONDS` | `10` | Pause between worker rounds. |
| `WORKER_CONCURRENCY` | `10` | Checks running at the same time. |
| `CHECK_TIMEOUT` | `10` | Seconds to wait for each site. |
| `CHECK_RETENTION_DAYS` | `30` | How many days of history to keep. |
| `WEBHOOK_MAX_ATTEMPTS` | `5` | Attempts to deliver an alert. |
| `MAX_MONITORS_PER_USER` | `20` | Monitors per account. |

## Tests

```bash
pytest --cov
```

Built with TDD, at 100% coverage (CI fails below that). Tests don't touch
the internet: DNS and websites are faked. In CI, the same suite runs on
both SQLite and Postgres, migrations are applied and rolled back on
Postgres, and `docker compose` is started and checked.

## Deployment

Deployed on [Render](https://render.com) (the API with the worker inside,
from `render.yaml`) and [Neon](https://neon.tech) (Postgres). On Render's
free plan the API sleeps after 15 minutes without visits, and checks pause
until someone opens the page again. To check around the clock, run the
worker as its own process, as in `docker-compose.yml`.

## Stack

Python 3.13 · FastAPI · SQLAlchemy 2 (async) · Alembic · PostgreSQL ·
asyncpg · httpx/httpcore · PyJWT · argon2 · pytest · ruff · Docker ·
GitHub Actions

## Author

Arthur Vieira · [LinkedIn](https://www.linkedin.com/in/arthurvieira-dev)
· [GitHub](https://github.com/artvsantos)
