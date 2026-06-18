# BinderBridge Deployment Guide

This guide covers the recommended self-hosted Docker Compose setup, first-run checks, upgrades, backups, and operational settings for BinderBridge.

## Recommended Docker Compose Setup

The default Compose stack runs two containers:

- `binderbridge`: the web app on port `8000`
- `binderbridge-worker`: the durable background worker for imports, Scryfall refreshes, automatic backups, email digests, stale-trade reminders, and webhook delivery

Both containers share the same `binderbridge-data` volume mounted at `/data`. The production config template at `deploy/binderbridge.production.ini` is mounted read-only at `/config/binderbridge.ini`, and environment values from `.env` override it.

### 1. Prepare Configuration

```powershell
Copy-Item .env.example .env
```

Edit `.env` and set at least:

```env
BINDERBRIDGE_PUBLIC_BASE_URL=https://cards.example.com
BINDERBRIDGE_SOURCE_URL=https://github.com/thefatkid22/BinderBridge
```

For a local LAN-only test, `http://localhost:8000` is fine. For passkeys, password reset links, invite links, and notification email, set `BINDERBRIDGE_PUBLIC_BASE_URL` to the real HTTPS origin users will open in their browser.

SMTP is optional. If SMTP values are blank, email-only controls stay hidden, while in-app notifications, manual invite links, and admin-assisted password recovery still work.

### 2. Start The Stack

```powershell
docker compose up -d --build
docker compose ps
docker compose logs -f binderbridge binderbridge-worker
```

Open `http://localhost:8000` or the configured public URL. The first registered account becomes the owner.

### 3. First-Run Admin Checklist

After creating the owner account:

- Open `Admin -> First-run setup`
- Set the public URL users will open in their browsers
- Review the small-local-group defaults: invite-only registration, suspicious-signup review, daily automatic backups, and manual invite links when SMTP is not configured
- Configure SMTP if you want email invites, password recovery email, or notification email
- Create a first backup and confirm automatic backup retention
- Run or confirm Scryfall bulk sync before large imports
- Create an invite or import a first collection
- Open `Admin -> Maintenance Health` to confirm the database and background jobs are healthy

## Configuration Files

The Compose stack uses:

- `.env`: local secrets and deployment-specific values, not committed
- `deploy/binderbridge.production.ini`: committed production-oriented defaults
- `/data`: SQLite database, backups, Scryfall cache, uploaded condition photos, dispute evidence, and runtime data

Environment variables override INI values. Prefer `.env` for secrets such as SMTP credentials.

Important settings:

```env
BINDERBRIDGE_PUBLISHED_PORT=8000
BINDERBRIDGE_PUBLIC_BASE_URL=https://cards.example.com
BINDERBRIDGE_SMTP_HOST=
BINDERBRIDGE_SMTP_USERNAME=
BINDERBRIDGE_SMTP_PASSWORD=
BINDERBRIDGE_JOB_RUNNER_MODE=external
```

Use `external` when running the Compose worker service. Use `embedded` only when intentionally running the web container without the separate worker.

## Single-Container Mode

Small local installs can run only the web container with the embedded worker:

```powershell
docker compose up -d --build --scale binderbridge-worker=0
```

Set this in `.env`:

```env
BINDERBRIDGE_JOB_RUNNER_MODE=embedded
```

Two-container mode is still recommended for normal self-hosted use because worker logs and restarts are easier to reason about.

## Reverse Proxy And HTTPS

Put BinderBridge behind a reverse proxy for internet-facing installs. The app listens on plain HTTP inside the container; the proxy should terminate HTTPS.

Minimal Nginx location example:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Set `BINDERBRIDGE_PUBLIC_BASE_URL` to the HTTPS URL served by the proxy. This is especially important for invite links, password recovery, passkeys, and webhook payload URLs.

## Backups And Restore Drills

Use the in-app backup tools first:

- Manual backups: `Admin -> Backup and restore`
- Automatic backups: enabled by default
- Backup retention: configurable from the same admin area
- Integrity checks: available from `Admin -> Maintenance Health`

Backups are stored under the `/data` volume. A good operating rhythm is:

- Keep automatic backups enabled
- Download or copy backups off-host periodically
- Test a restore before opening the app to a real group
- Always create a backup before upgrading

## Upgrades

For source-based Docker installs:

```powershell
docker compose down
git fetch --tags
git checkout main
git pull
docker compose build --pull
docker compose up -d
docker compose logs -f binderbridge binderbridge-worker
```

SQLite migrations run automatically when the app starts. Before upgrading, create a backup and make sure the previous deployment is fully stopped.

After upgrading:

- Open `Admin -> Maintenance Health`
- Confirm schema/migration status
- Confirm the worker is running
- Confirm automatic backup and Scryfall refresh status
- Run a quick login and collection page smoke test

## Background Worker Operations

The worker processes durable jobs stored in SQLite. Jobs are leased, retried with backoff, and visible from `Admin -> Import and job dashboard`.

Useful commands:

```powershell
docker compose logs -f binderbridge-worker
docker compose restart binderbridge-worker
```

Run one worker initially. SQLite coordinates leases, but BinderBridge is still designed around modest concurrency for small and medium self-hosted groups.

## Data Volume Notes

The `binderbridge-data` volume contains:

- `binderbridge.sqlite3`
- SQLite WAL/SHM files when active
- uploaded condition photos
- dispute evidence
- Scryfall bulk-data cache
- generated backups

Do not delete or replace this volume unless you have a verified backup. To inspect the volume from a temporary container:

```powershell
docker compose run --rm binderbridge sh -lc "ls -lah /data"
```

## Troubleshooting

Check container health:

```powershell
docker compose ps
docker compose logs --tail=100 binderbridge
docker compose logs --tail=100 binderbridge-worker
```

Common issues:

- Login or invite links point to localhost: set `BINDERBRIDGE_PUBLIC_BASE_URL`
- Email options are hidden: SMTP host is blank or not loaded
- Imports are queued but not finishing: check the worker container and `Admin -> Import and job dashboard`
- Scryfall lookups are slow after first install: run the Scryfall bulk sync and let the worker populate the local cache
- Passkeys fail on public installs: use HTTPS and a stable public origin

## AGPL Source Notice

BinderBridge is AGPL-3.0 licensed. If you run a modified public deployment, provide users access to the corresponding source and set `BINDERBRIDGE_SOURCE_URL` to that source repository.
