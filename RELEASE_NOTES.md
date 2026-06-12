# BinderBridge v0.1.0-alpha.1

BinderBridge's first alpha release is a self-hostable trading-card collection and trade-management application built for trusted local groups, with room to grow into larger communities.

## Highlights

- Manage multi-user collections, wanted cards, decks, binders, and wishlist groups
- Browse available cards and build, comment on, counter, balance, and complete trades
- Match users with mutually useful trade opportunities
- Import collection and deck data with previews, reusable mappings, Scryfall enrichment, and undo
- Use Scryfall-backed lookup, pricing, price history, alerts, and finish validation
- Control visibility with member, trusted-user, share-link-only, and private options
- Use TOTP 2FA, passkeys, roles, API tokens, signed webhooks, and optional SMTP notifications
- Operate the site with backups, restore tools, health dashboards, database maintenance, retention controls, logs, and moderation workflows

## Installation

Requires Python 3.10 or newer:

```bash
git clone https://github.com/thefatkid22/BinderBridge.git
cd BinderBridge
git checkout v0.1.0-alpha.1
python app.py
```

Then open `http://127.0.0.1:8000`. The first registered user becomes the owner.

Docker Compose is also supported:

```bash
docker compose up --build
```

## Upgrading

1. Create and download a backup from `Admin -> Backup and restore`.
2. Stop BinderBridge.
3. Update the checkout to `v0.1.0-alpha.1`.
4. Start BinderBridge. SQLite migrations run automatically.
5. Confirm the Admin health and database-maintenance pages show no errors.

## Alpha Notes

- This release is intended for evaluation and trusted small-group use.
- Back up the SQLite database before upgrades and periodically test restoration.
- Use an HTTPS reverse proxy and set `BINDERBRIDGE_PUBLIC_BASE_URL` for any internet-facing deployment.
- Background workers and in-memory rate limits assume a single running BinderBridge app instance.
- Confirmed large collection imports can take several minutes while the applied rows are written.
- Operators publishing modified versions should set `BINDERBRIDGE_SOURCE_URL` to the corresponding source repository.

## Validation

- Full automated test suite with 200 tests
- Fresh direct-Python runtime copy and database initialization
- Existing database migration from schema version 7 to the current schema
- Backup creation and restore
- Confirmed 5,000-row CSV collection import
- Docker image build and startup smoke test through GitHub Actions

BinderBridge is licensed under the GNU Affero General Public License v3.0.
