# BinderBridge v0.2.0-alpha.1

BinderBridge v0.2.0-alpha.1 is a stabilization and usability release for the self-hostable trading-card collection and trade-management application. It focuses on secure account recovery, clearer responsive workflows, and stronger release validation.

## Highlights

- Recover user accounts through email when SMTP is configured or administrator-issued manual links when it is not
- Use reorganized Account and Admin work areas with clearer local navigation
- Navigate high-traffic collection, wishlist, trade, notification, group, and public-profile workflows more comfortably on mobile
- Search, filter, sort, paginate, select, and bulk-remove links from deck, binder, and wishlist groups
- Use shared accessible confirmations for high-risk actions and compact keyboard-accessible mobile navigation
- Continue using the complete multi-user collection, import, trade, privacy, notification, security, and administration feature set from the first alpha

## Installation

Requires Python 3.10 or newer:

```bash
git clone https://github.com/thefatkid22/BinderBridge.git
cd BinderBridge
git checkout v0.2.0-alpha.1
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
3. Update the checkout to `v0.2.0-alpha.1`.
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

- Full automated test suite with 207 tests
- Fresh direct-Python runtime copy and database initialization
- Existing database migration from schema version 7 to schema version 9
- Published `v0.1.0-alpha.1` database migration from schema version 8 to schema version 9
- Backup creation, restore, and post-restore SQLite integrity checks
- Complete multi-user trade offer, acceptance, completion, card-transfer, and notification workflow
- Confirmed 5,000-row CSV collection import
- Docker image build and startup smoke test through GitHub Actions

BinderBridge is licensed under the GNU Affero General Public License v3.0.
