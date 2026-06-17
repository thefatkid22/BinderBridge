# BinderBridge v0.2.0-alpha.2

BinderBridge v0.2.0-alpha.2 is a stability, moderation, and deployment release for the self-hostable trading-card collection and trade-management application. It focuses on durable background work, reusable saved filters, registration moderation, API rate limiting, and Docker-based deployment polish.

## Highlights

- Run Scryfall enrichment, price refreshes, imports, backups, notifications, webhooks, and legacy price work through a durable SQLite-backed job runner
- Save reusable filter and sort presets across collection, browse, wishlist, and trade-builder screens
- Protect API and integration endpoints with configurable SQLite-backed rate limits
- Review pending registrations and suspicious signup signals before accounts can sign in
- Deploy with a non-root Docker image, healthcheck, production config template, `.env.example`, and a two-service Compose stack with a dedicated worker
- Use the new deployment guide for first-run setup, reverse proxy, backups, upgrades, worker operations, and AGPL source notes

## Installation

Requires Python 3.10 or newer:

```bash
git clone https://github.com/thefatkid22/BinderBridge.git
cd BinderBridge
git checkout v0.2.0-alpha.2
python app.py
```

Then open `http://127.0.0.1:8000`. The first registered user becomes the owner.

Docker Compose is also supported:

```bash
cp .env.example .env
docker compose up -d --build
```

## Upgrading

1. Create and download a backup from `Admin -> Backup and restore`.
2. Stop BinderBridge.
3. Update the checkout to `v0.2.0-alpha.2`.
4. Start BinderBridge. SQLite migrations run automatically.
5. Confirm the Admin health and database-maintenance pages show no errors.

## Alpha Notes

- This release is intended for evaluation and trusted small-group use.
- Back up the SQLite database before upgrades and periodically test restoration.
- Use an HTTPS reverse proxy and set `BINDERBRIDGE_PUBLIC_BASE_URL` for any internet-facing deployment.
- The Docker Compose deployment runs one web process and one worker process against the same SQLite volume. Keep worker concurrency modest.
- Confirmed large collection imports can take several minutes while the applied rows are written.
- Operators publishing modified versions should set `BINDERBRIDGE_SOURCE_URL` to the corresponding source repository.

## Validation

- Focused registration/admin/auth tests: 68 tests
- Full automated test suite: 229 tests
- Fresh install and upgrade smoke checks through schema version 13
- Published `v0.2.0-alpha.1` database migration from schema version 9 to schema version 13
- Backup creation, restore, and post-restore SQLite integrity checks
- Complete multi-user trade offer, acceptance, completion, card-transfer, and notification workflow
- Confirmed 5,000-row CSV collection import
- Docker Compose build, startup, worker start, `/api/v1/health`, and `/login` smoke checks
- GitHub Actions PR checks for Python 3.10, 3.12, 3.13, and Docker startup smoke test

BinderBridge is licensed under the GNU Affero General Public License v3.0.
