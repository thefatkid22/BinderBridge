# BinderBridge v0.2.0-alpha.4

BinderBridge v0.2.0-alpha.4 expands the self-hosted backend and web app for the native Android client while delivering broader collection, deck, binder, trade, cleanup, and responsive-interface improvements.

## Highlights

- Serve a web-aligned Android home dashboard with collection metrics, pending trades, recently tradeable cards, recent notifications, and cache-friendly responses
- Expand scoped mobile APIs for collection details and batch imports, wanted cards, groups, trade proposals and conversations, notifications, and trade alerts
- Create and edit decks and binders, manage grouped quantities, move or copy cards between groups, and import directly into a target group
- Keep counteroffers in the existing trade conversation with richer comments, decisions, and participant-safe responses
- Audit collection and wishlist rows against Scryfall data, surface hygiene counts, and run targeted cleanup actions
- Improve bulk collection, wishlist, and group workflows with clearer completion feedback and preserved navigation state
- Polish responsive collection, group, trade, wishlist, dashboard, admin, and application-shell layouts
- Seed a rich disposable demo environment and run expanded Playwright coverage across desktop and narrow mobile viewports

## Installation

Requires Python 3.10 or newer:

```bash
git clone https://github.com/thefatkid22/BinderBridge.git
cd BinderBridge
git checkout v0.2.0-alpha.4
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
2. Stop BinderBridge and its background worker.
3. Update the checkout to `v0.2.0-alpha.4`.
4. Rebuild and restart BinderBridge. SQLite migrations run automatically.
5. Open `Admin -> Maintenance Health` and confirm the database, worker, backups, and scheduled jobs are healthy.
6. Revoke any temporary mobile API tokens used for upgrade testing.

## Android Compatibility

- BinderBridge Android `0.2.0-rc.2` uses the expanded APIs in this release, including `GET /api/v1/dashboard`.
- Use least-privilege API tokens and HTTPS for production Android connections.
- The Android local-release variant may use LAN HTTP for trusted development networks.

## Alpha Notes

- This release is intended for evaluation and trusted small-group use.
- Back up the SQLite database before upgrades and periodically test restoration.
- Use an HTTPS reverse proxy and set `BINDERBRIDGE_PUBLIC_BASE_URL` for any internet-facing deployment.
- SMTP remains optional; manual invite and password-recovery links are available for local groups.
- Operators publishing modified versions should set `BINDERBRIDGE_SOURCE_URL` to the corresponding source repository.

## Validation

- Full Python compilation and automated unit/integration suite: 266 tests
- Fresh-install, schema-upgrade, backup/restore, SQLite integrity, complete trade lifecycle, and 5,000-row import release smoke coverage
- Published `v0.2.0-alpha.3` database upgrade smoke with preserved collection data
- Playwright browser smoke across first-run setup, import, collection, wishlist, trade, notification, desktop, and narrow-mobile workflows
- Docker image build, startup, public health endpoint, Android dashboard authentication, and external worker checks
- GitHub Actions checks across Python 3.10, 3.12, and 3.13 plus Docker and browser smoke jobs

BinderBridge is licensed under the GNU Affero General Public License v3.0.
