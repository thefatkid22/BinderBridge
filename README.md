# BinderBridge

BinderBridge is a self-hostable trading card collection app for small communities. The current app is focused on Magic: The Gathering while keeping each card entry tagged by game so Pokemon, Lorcana, and other TCG support can be expanded later.

Current release: **v0.2.0-alpha.1**

License: **GNU AGPL-3.0**

## Features

- Username and password accounts
- TOTP two-factor authentication with one-time recovery codes
- Passkey/WebAuthn login as an optional passwordless sign-in method
- CSRF protection for authenticated browser form actions
- Basic in-memory rate limiting for sign-in, registration, API auth/write actions, Scryfall lookups, and integration management
- Dark interface enabled by default with a persistent light-mode toggle
- Account control panel for username, email, profile, and password changes
- Owner, admin, moderator, organizer, member, and read-only roles with protected hierarchy and capability-based staff tools
- Staff control panels for user moderation, disputes, invites, role management, trusted status, and security resets
- Admin activity logs for moderation, invite, registration, fairness, and backup actions
- Integration audit logs for API token, webhook, failed API-auth, and API write activity
- Admin-managed registration invites with optional invite-only registration mode and manual-link fallback
- SQLite storage with no external service dependency
- Versioned SQLite schema migrations with hot-path indexes for collection, browse, wishlist, trade, and Scryfall lookup pages
- Admin database maintenance tools with guarded ANALYZE/VACUUM actions, index-planner visibility, storage-growth snapshots, and migration history
- Admin backup and restore tools with scheduled automatic backups, retention settings, and pre-restore safety snapshots
- Admin maintenance health dashboard for database size, backup status, Scryfall refresh status, queued jobs, email configuration, failed notifications, setup warnings, and needs-attention grouping
- Admin collection health dashboard for duplicate rows, missing Scryfall data, invalid finishes, stale prices, per-user issue concentration, and public/private coverage
- Admin data retention controls for pruning read notifications, audit logs, completed webhook delivery records, and resolved dispute evidence
- Admin onboarding checklist for SMTP, registration settings, backups, Scryfall sync, first invites, and first collection import
- Admin import/job dashboard for CSV imports, Scryfall enrichment, Scryfall price refresh status, failed jobs, retries, and import undo
- Admin maintenance health actions for retrying recoverable jobs, replaying failed notification emails, checking backup integrity, and surfacing setup warnings
- Personal collection tracking
- Collection statistics for value totals, rarity mix, condition, finish, language, visibility, and card-data coverage
- Sortable collection, browse, wishlist, deck/binder group, and trade-building card lists by name, set, game, quantity, trade quantity, condition, finish, value, and update time
- Searchable, filterable, paginated deck/binder/wishlist group contents with current-page selection and bulk group-link removal
- Responsive collection, browse, group, trade, and admin layouts with mobile-friendly card views, compact mobile navigation, and shared accessible confirmation dialogs
- Active removable filter chips for collection, browse, and trade-picker results
- Tradeable quantity per card
- Per-card condition details and photo galleries that are preserved in trade offers
- Bulk collection quantity and trade-quantity updates
- Wishlist with optional Scryfall lookup
- Wishlist entries with desired condition, finish, language, set, exact printing preferences, priority levels, per-copy budget caps, and preferred-printing notes
- Deck, binder, and wishlist groups for organizing collection and want entries
- Granular privacy controls for collection cards, wanted cards, decks, binders, and wishlist groups: all-members, trusted-members, share-link-only, or private
- Hidden collection values, per-group value/photo sharing defaults, and expiring/revocable private collection-card, wanted-card, and group share links stored as hashes
- Public member profile pages with visible trade cards, wanted cards, binders, reputation, and propose-trade actions
- Deck group bulk imports from CSV, pasted deck lists, text files, and public deck-list URLs
- Deck imports match against owned collection cards and can turn missing cards into grouped wishlist wants
- Browse page for available trade cards with card, user, quality, game, and finish filters
- Trade proposals, accept, decline, cancel, and completion flow
- Trade comments and linked counter-offers
- Trade matchmaking that highlights users with mutual want/trade overlap and preloads a balanced trade draft
- Trade-builder recommendations for wishlist matches and Scryfall value-balance helpers
- Trade issue reporting with evidence attachments, admin review queue, resolution notes, repeat-issue trend reporting, participant notifications, and audit logging
- Per-user reputation summaries and feedback after completed trades
- Notification center for trade offers, comments, counters, and status changes
- Granular notification preferences for trade offers, comments, counteroffers, price alerts, import completion, admin notices, and optional email delivery
- Immediate, daily-digest, or weekly-digest email delivery with per-user quiet hours and timezone settings
- Configurable stale pending-trade reminders with clearer unread trade indicators
- Scoped API bearer tokens for self-hosted integrations
- Signed webhook endpoints for notifications, trade events, imports, price updates, and backup failures
- Watchlist alerts when another user lists a card from your wishlist for trade
- Trusted-user one-directional trades with confirmation warnings
- Stored USD prices from Scryfall for trade value comparisons
- Scryfall price history with value-change alerts
- Local Scryfall bulk-data price refresh for collection prices
- Automatic daily Scryfall price refresh with per-user alert thresholds
- Per-trade Scryfall price snapshots
- Trade value totals and balance indicators during trade building, review, and detail views
- Admin-configurable trade fairness warnings and optional blocking thresholds for value gaps
- CSV imports for ManaBox, Archidekt, and generic collection exports
- Import previews and undoable import batches for collection CSV and deck group imports
- CSV exports for collection views, wanted cards, and individual groups
- Full account JSON export for self-service data portability
- Duplicate detection and cleanup tools for collection and wanted-card rows
- Condition and finish audit tools for collection hygiene
- Optional Scryfall enrichment for MTG card metadata using local bulk data and a background lookup queue
- Optional demo data for quick evaluation

## Run Locally

Requires Python 3.10 or newer.

```powershell
git clone https://github.com/thefatkid22/BinderBridge.git
cd BinderBridge
git checkout v0.2.0-alpha.1
python app.py
```

Open `http://127.0.0.1:8000`.

Data is stored in `data/binderbridge.sqlite3` by default. Set `BINDERBRIDGE_DATA` to choose another directory.

The first registered user becomes the site owner. For upgrades, create a backup first, stop BinderBridge, update the checkout, and restart it. SQLite migrations run automatically on startup.

## CSV Import

Open `My Cards -> Import` in the app and upload a CSV export. Built-in source profiles support ManaBox, Archidekt, Deckbox, Moxfield, Dragon Shield, and Delver Lens exports. Auto detect selects a profile from the file headers, while `Generic CSV` handles common headers including:

- `Name`
- `Quantity`
- `Set`, `Set name`, or `Edition`
- `Set code`
- `Collector number`
- `Foil` or `Finish`
- `Condition`
- `Language`
- `Scryfall ID`

Uploads default to a preview step that shows how many rows will be inserted, updated, queued, or skipped before anything is written. Applied import batches can be undone from the import history, which removes newly imported rows and restores rows that were merged during the batch.

Users can save custom CSV mapping presets from the import page when a source uses unusual column names. Presets can target collection imports or deck CSV imports, override the selected built-in source profile, and admins can share presets with every user on the site.

When `Scryfall lookup` is enabled, MTG imports are enriched with canonical card names, set data, type line, oracle text, image URL, Scryfall URL, rarity, colors, and current USD price when available. Imports first use the local Scryfall bulk-data cache and existing SQLite lookup cache so large CSV files do not make one live request per row. Rows that cannot be matched locally are imported immediately and queued for background Scryfall enrichment after the preview is confirmed. CSV price columns are ignored; BinderBridge uses Scryfall as the only pricing source.

BinderBridge refreshes Scryfall's `default_cards` bulk file automatically as part of the background price update process and stores the fields needed for collection enrichment locally.

Manual add/edit and want-list entry also support Scryfall search. Enter a partial card name, choose `Search Scryfall`, pick the card, then choose the exact printing or variant before saving.

## Exports

Collection pages include an `Export CSV` action that respects the current filters. Wanted cards and each deck, binder, or wishlist group can also be exported as CSV.

Users can download a full account JSON export from `Account -> Account export`. It includes profile settings, collection cards, wants, groups and their items, trades, notifications, and price history, while excluding password hashes, sessions, and admin-private fields.

## Duplicate Cleanup

Open `Account -> Open cleanup tools`, or use the cleanup shortcut on collection and wishlist pages, to review exact duplicate rows. BinderBridge can merge selected duplicate collection cards or wanted-card entries while preserving quantities, notes, public visibility, group links, trade references, queued Scryfall jobs, and price history.

`My Cards -> Audit condition/finish` opens a collection hygiene queue for cards with missing, unknown, or normalizable condition and finish values. Users can filter by issue type, game, card search, set, trade status, current condition, and current finish, then bulk update selected rows or normalize recognized import labels such as `Near Mint`, `nonfoil`, and lowercase condition values. For MTG cards with an exact Scryfall printing match, the audit also checks whether the selected finish exists for that printing using cached Scryfall bulk data and shows the available finishes for each audited row.

Collection add/edit, wanted-card add/edit, and CSV imports also use the same Scryfall-backed finish check when an exact printing is known. If a real-world oddity needs to be entered anyway, the forms include an override checkbox; collection imports skip mismatched rows unless `Allow Scryfall finish mismatches` is selected.

## Deck Group Import

Open a deck group from `My Cards -> Decks & Binders` and use `Bulk import deck`. Deck imports can use the collection-app CSV profiles plus Deckstats, TappedOut, and AetherHub deck CSV profiles, or common plain-text deck-list formats such as:

```text
1 Sol Ring
4 Counterspell (DMR) 45
1 Arcane Signet [C20] #252
```

Public deck-list URLs are supported on a best-effort basis. BinderBridge tries known public JSON endpoints for Moxfield and Archidekt, text-export candidates for TappedOut and Deckstats, then falls back to plain text, CSV, or readable HTML deck-list content. If a site blocks automated URL export or changes its private endpoint, export the deck as plain text or CSV and upload or paste it into the deck group.

Deck imports compare the parsed list against cards already in your collection and show a preview before adding owned cards to the group. Applied deck import batches can be undone from the deck import history. Owned copies are added to the deck group up to the quantity you have, and any shortage is shown as a missing-card prompt. From that prompt you can add selected missing cards to an existing wishlist group or create a new grouped wishlist for the deck.

## Pricing

BinderBridge uses Scryfall as the only pricing source. Manual prices, CSV price columns, and external marketplace feeds are not selectable in the UI. Existing non-Scryfall price source labels are normalized to Scryfall during database migration, and sent trades snapshot the Scryfall price shown at proposal time so later collection updates do not rewrite old trade value comparisons.

BinderBridge refreshes Scryfall-backed collection prices automatically every 24 hours while the app is running. It uses Scryfall bulk data first, then refreshes each user's stored collection prices from the local cache. Price observations are stored per collection entry, and users receive a notification when the automatic refresh has completed for their collection.

When a CSV import needs queued Scryfall enrichment, BinderBridge processes those lookups in the background and sends one notification when the queued lookup work completes.

Users can control price-change alerts from `Account -> Price alerts`. Alerts can be turned off or limited to changes at or above a chosen percentage threshold.

## Backup And Restore

Admins can download a `.zip` backup from `Admin -> Backup and restore`. The archive contains the SQLite database plus metadata.

Automatic backups are enabled by default while the app is running. Admins can change the interval, pause the schedule, run an immediate automatic backup, and set retention limits from `Admin -> Backup and restore`. Retention cleanup only removes automatic backup archives with the `binderbridge-auto` prefix, leaving manual downloads and pre-restore safety backups untouched.

By default, BinderBridge creates an automatic backup every 24 hours and keeps the newest 14 automatic backups for up to 30 days. If an automatic backup fails, admins receive an in-app notification.

Restoring accepts BinderBridge backup zips or raw SQLite database files. BinderBridge verifies the uploaded database, creates a pre-restore safety backup under `data/backups`, restores with SQLite's backup API, and then runs migrations so older backups can catch up to the current schema.

## Data Retention

Admins can review and run data retention cleanup from `Admin -> Maintenance Health`. Separate retention periods control read notifications, admin audit logs, terminal webhook delivery records, and evidence attached to resolved or dismissed disputes. Set any period to `0` to keep that data forever.

Cleanup is manual and shows the number of currently eligible records before it runs. Unread notifications, pending webhook deliveries, and evidence attached to open disputes are always protected.

## Registration Invites

Admins can create registration invites from `Admin -> Registration invites`. Invite tokens are stored hashed in the database, expire automatically, and can be revoked before they are accepted.

Registration remains open by default so a fresh self-hosted install can create its first admin account. After that, admins can enable `Require an invite link for new accounts` from `Admin -> Registration`.

If SMTP is configured, BinderBridge sends the invite email automatically. Without SMTP, the admin panel shows a copyable invite link.

## Password Recovery

Users can request password recovery from the sign-in page using their username or account email. BinderBridge always returns a generic response so the recovery form cannot be used to discover which accounts exist.

When SMTP is configured and the account has an email address, BinderBridge sends a hashed, single-use password reset link. Without SMTP, or when email delivery fails, the request appears in the admin user panel so an administrator can issue a manually shared reset link.

Administrators do not choose or learn a user's replacement password. Issuing an administrator-assisted reset requires the administrator's current password, signs out the affected user, and creates a one-time link. Completing any password reset signs out active sessions while leaving two-factor authentication enabled. Reset links expire after 60 minutes by default.

## Notification Preferences

Users can configure in-app notification categories from `Account -> Notification preferences`, including trade offers, comments, counteroffers, trade status, price alerts, watchlist alerts, import completion, and admin notices.

If SMTP is configured, email delivery is opt-in per user and can be toggled separately for trade offers, comments, counteroffers, trade status, price alerts, import completion, and admin notices. Users can choose immediate delivery, a daily digest, or a weekly digest, set the digest time and timezone, and defer email during quiet hours. BinderBridge only attempts email delivery for unread in-app notifications. Delivery status is recorded on each notification, and SMTP failures do not block the action that created the notification. If SMTP is not configured, email notification options are hidden and in-app notifications continue to work normally.

Users can also choose how many days a pending trade offer may wait before BinderBridge creates a reminder. Set this value to `0` to disable stale-trade reminders. The reminder worker creates at most one reminder per trade update, while unread trade badges in navigation and trade lists make action-required offers easier to spot.

## API And Webhooks

Users can create scoped API bearer tokens from `Account -> API access`. Tokens are shown once, stored hashed, and can be revoked from the account page. Read tokens can list account data, while write tokens can create, update, or delete supported records.

Initial API endpoints:

- `GET /api/v1/health`
- `GET /api/v1/me`
- `GET /api/v1/collection`
- `POST /api/v1/collection`
- `GET /api/v1/collection/{id}`
- `PATCH /api/v1/collection/{id}`
- `DELETE /api/v1/collection/{id}`
- `GET /api/v1/wants`
- `POST /api/v1/wants`
- `GET /api/v1/trades`
- `GET /api/v1/notifications`

Send API tokens with `Authorization: Bearer bbapi_...`.

Webhook endpoints are also managed from `Account -> API access`. BinderBridge sends JSON `POST` requests with `X-BinderBridge-Event`, `X-BinderBridge-Delivery`, and `X-BinderBridge-Signature` headers. The signature is `sha256=` plus an HMAC-SHA256 of the raw JSON payload using the webhook signing secret. Deliveries are queued in SQLite and processed by a small background worker so trade and notification actions are not blocked by remote webhook downtime.

## Test

```powershell
python -m unittest discover -s tests
```

## Project Layout

`app.py` remains the public entrypoint and HTTP router so existing scripts can still run `python app.py` or `import app`. Feature code is split into focused modules under `binderbridge/`:

- `accounts.py`: account profile, password, admin, invites, registration mode, and trusted-user controls
- `groups.py`: deck, binder, and wishlist group management
- `collection_service.py`: collection CRUD, bulk updates, and watchlist alerts
- `scryfall_client.py`: Scryfall API access, local bulk-data cache, bulk-data synchronization, card lookup, and card metadata enrichment helpers
- `scryfall_jobs.py`: Scryfall enrichment workers, bulk-sync orchestration, and automatic Scryfall price refresh
- `background_jobs.py`: legacy batched external price-refresh queue compatibility
- `views.py`: compatibility facade for feature-specific page renderers
- `components.py`: shared HTML controls such as sort bars, active filter chips, pagination, and trade-picker paging
- `trade_service.py`: trade validation, comments, counters, notifications, and completion logic
- `collection_queries.py`, `want_queries.py`, `matchmaking_queries.py`, `trade_queries.py`: SQL-heavy list, detail, picker, availability, and matchmaking queries
- `import_mapping.py`, `import_batches.py`, `collection_imports.py`, `deck_import_service.py`: CSV mapping, preview batches, undo support, and collection/deck import orchestration
- `maintenance.py`: admin backup, restore, retention, database maintenance, storage history, and index visibility helpers
- `exports.py`: collection, group, wishlist, and account export helpers
- `cleanup.py`: duplicate detection, duplicate merge, and collection hygiene audit helpers
- `collection_health.py`: site-wide collection quality, price freshness, Scryfall coverage, and privacy coverage metrics
- `import_profiles.py`: built-in collection/deck CSV source profiles, mappings, and header-based auto detection
- `api.py`: API tokens, JSON API endpoints, webhooks, and webhook delivery helpers
- `account_routes.py`, `collection_routes.py`, `group_routes.py`, `trade_routes.py`, `admin_routes.py`: feature-specific HTTP route handlers
- `db.py`: SQLite connection helpers, schema bootstrapping, migrations, and settings
- `security.py`: password hashing, sessions, TOTP two-factor, and passkey/WebAuthn helpers
- `notifications.py`: in-app notifications, optional email delivery, and import completion notices
- `pricing.py`: Scryfall pricing normalization, price history, and value-change alerts
- `formatting.py`: shared option lists, labels, money formatting, HTML escaping, suggestions, and collection stats
- `ui_helpers.py`: compatibility facade that re-exports the helper modules while older imports continue to work

The facade wires these modules back into the `app` namespace for compatibility while the codebase continues to move toward smaller modules. Tests are grouped into feature-oriented suites under `tests/`, with shared setup and helpers in `tests/base.py`.

## Database Maintenance and Migrations

Admins with maintenance access can open `Admin -> Database maintenance` to:

- Run `ANALYZE` and `PRAGMA optimize` to refresh SQLite query-planner statistics
- Run a guarded `VACUUM` after `quick_check` to rebuild the database and reclaim reusable pages
- Record storage snapshots and review database, WAL, and shared-memory growth over time
- Inspect application indexes, indexed columns, planner statistics, storage footprint when SQLite `dbstat` is available, and representative query plans that currently choose each index
- Review the current schema version and recorded migration history

SQLite does not expose cumulative per-index usage counters. BinderBridge therefore labels index usage as representative planner evidence rather than claiming to measure every query executed by the application.

Create a backup before running `VACUUM` on an important installation. `VACUUM` needs temporary free disk space and can temporarily block writes. `ANALYZE` is the routine, lower-risk maintenance action.

Schema migrations run automatically during startup. Migration history introduced in version 8 backfills earlier applied versions with the time history tracking was enabled.

| Version | Migration |
| --- | --- |
| 1 | Hot-path indexes for collection, browse, wishlist, trades, and Scryfall lookups |
| 2 | Trade dispute evidence storage and moderation trend indexes |
| 3 | Saved CSV import mapping presets |
| 4 | User roles and hierarchy |
| 5 | Granular privacy controls and group share links |
| 6 | Private collection-card share links |
| 7 | Private wanted-card share links |
| 8 | Database maintenance history, storage snapshots, and recorded migration history |
| 9 | Secure password recovery requests and one-time reset tokens |

## Demo Data

Start with sample users and cards:

```powershell
$env:BINDERBRIDGE_DEMO = "1"
python app.py
```

Demo accounts:

- `alice` / `password123`
- `bob` / `password123`

Demo data is only inserted when the database has no users.

## Configuration

BinderBridge can be configured with an INI file. Copy `binderbridge.example.ini` to `binderbridge.ini` in the project root, or set `BINDERBRIDGE_CONFIG` to an alternate path.

Environment variables still override config-file values, which is useful for Docker, systemd, or hosted deployments.

Common config keys:

```ini
[server]
host = 127.0.0.1
port = 8000
public_base_url = https://cards.example.com

[app]
data_dir = ./data
demo = false
source_url = https://github.com/thefatkid22/BinderBridge

[scryfall]
user_agent = BinderBridge/0.2.0-alpha.1 self-hosted collection manager
delay_seconds = 0.12
search_limit = 24
bulk_type = default_cards

[smtp]
host = smtp.example.com
port = 587
username = user@example.com
password = change-me
from_address = BinderBridge <noreply@example.com>
tls = true
ssl = false

[notifications]
worker_interval_seconds = 60

[registration]
invite_expiry_days = 14

[backups]
auto_enabled = true
interval_hours = 24
retention_count = 14
retention_days = 30

[retention]
notification_days = 90
admin_log_days = 365
webhook_days = 90

[webhooks]
worker_enabled = true
timeout_seconds = 5
delivery_interval_seconds = 30
delivery_batch_size = 20
```

Supported environment variables:

- `BINDERBRIDGE_HOST` or `HOST`: bind address, default `127.0.0.1`
- `BINDERBRIDGE_PORT` or `PORT`: port, default `8000`
- `BINDERBRIDGE_CONFIG`: path to an INI config file
- `BINDERBRIDGE_DATA`: database directory, default `./data`
- `BINDERBRIDGE_DEMO`: seed sample data when set to `1`, `true`, or `yes`
- `BINDERBRIDGE_SOURCE_URL`: source repository shown in the app footer; modified public deployments should point this to their corresponding source
- `SCRYFALL_USER_AGENT`: custom User-Agent header for Scryfall requests
- `SCRYFALL_DELAY_SECONDS`: delay between live Scryfall requests, default `0.12`
- `SCRYFALL_SEARCH_LIMIT`: Scryfall search result limit, default `24`
- `SCRYFALL_BULK_TYPE`: Scryfall bulk-data type, default `default_cards`
- `SCRYFALL_PRICE_REFRESH_AUTO`: set to `0`, `false`, `no`, or `off` to disable automatic price refresh
- `SCRYFALL_PRICE_REFRESH_INTERVAL_HOURS`: automatic Scryfall price refresh interval, default `24`
- `BINDERBRIDGE_PUBLIC_BASE_URL`: public URL used in invite links, for example `https://cards.example.com`
- `BINDERBRIDGE_SMTP_HOST`: SMTP host for invite and notification email delivery
- `BINDERBRIDGE_SMTP_PORT`: SMTP port, default `587`
- `BINDERBRIDGE_SMTP_USERNAME`: SMTP username
- `BINDERBRIDGE_SMTP_PASSWORD`: SMTP password
- `BINDERBRIDGE_SMTP_FROM`: email sender, defaults to the SMTP username or `noreply@localhost`
- `BINDERBRIDGE_SMTP_TLS`: use STARTTLS, default enabled unless SMTP SSL is enabled
- `BINDERBRIDGE_SMTP_SSL`: use SMTP over SSL, default disabled
- `BINDERBRIDGE_PASSWORD_RESET_EXPIRY_MINUTES`: one-time password reset link lifetime, default `60`
- `BINDERBRIDGE_NOTIFICATION_WORKER_INTERVAL_SECONDS`: interval for scheduled email and stale-trade reminder processing, default `60`
- `BINDERBRIDGE_REGISTRATION_INVITE_EXPIRY_DAYS`: invite expiration window, default `14`
- `BINDERBRIDGE_BACKUP_AUTO_ENABLED`: set to `0`, `false`, `no`, or `off` to pause automatic backups by default
- `BINDERBRIDGE_BACKUP_INTERVAL_HOURS`: automatic backup interval, default `24`
- `BINDERBRIDGE_BACKUP_RETENTION_COUNT`: number of automatic backup archives to keep, default `14`
- `BINDERBRIDGE_BACKUP_RETENTION_DAYS`: maximum automatic backup age in days, default `30`; set to `0` to disable age-based cleanup
- `BINDERBRIDGE_NOTIFICATION_RETENTION_DAYS`: default age for read-notification cleanup, default `90`; set to `0` to keep forever
- `BINDERBRIDGE_ADMIN_LOG_RETENTION_DAYS`: default age for admin audit-log cleanup, default `365`; set to `0` to keep forever
- `BINDERBRIDGE_WEBHOOK_RETENTION_DAYS`: default age for completed webhook delivery cleanup, default `90`; set to `0` to keep forever
- `BINDERBRIDGE_SQLITE_BUSY_TIMEOUT_MS`: how long SQLite waits for another write to finish before reporting a lock, default `30000`
- `BINDERBRIDGE_API_PAGE_SIZE_MAX`: maximum API page size, default `250`
- `BINDERBRIDGE_WEBHOOK_WORKER_ENABLED`: set to `0`, `false`, `no`, or `off` to disable the webhook delivery worker
- `BINDERBRIDGE_WEBHOOK_TIMEOUT_SECONDS`: outbound webhook request timeout, default `5`
- `BINDERBRIDGE_WEBHOOK_DELIVERY_INTERVAL_SECONDS`: webhook worker polling interval, default `30`
- `BINDERBRIDGE_WEBHOOK_DELIVERY_BATCH_SIZE`: webhook deliveries processed per worker pass, default `20`
- `DECK_IMPORT_MAX_BYTES`: maximum fetched/uploaded deck import size, default `1500000`
- `BINDERBRIDGE_MAX_UPLOAD_BYTES`: maximum upload size, default `10485760`
- `BINDERBRIDGE_MAX_CSV_ROWS`: maximum CSV import rows, default `25000`

For LAN use, run with `HOST=0.0.0.0` and put the app behind a reverse proxy with HTTPS.

## Docker

Build and run:

```powershell
docker compose up --build
```

The compose file stores SQLite data in a named volume.

## Release Validation

Run the full automated suite and release-critical smoke checks:

```powershell
python -m unittest discover -s tests
python scripts/release_smoke.py
python scripts/release_upgrade_smoke.py v0.1.0-alpha.1
```

The smoke checks use temporary data directories to verify fresh initialization, schema upgrades, backup/restore, a complete multi-user trade lifecycle, SQLite integrity, and a large CSV import. The published-release upgrade check creates a database with the specified release tag and migrates it using the current checkout. GitHub Actions also builds and starts the Docker image. See [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) for the complete release process.

## Known Alpha Limitations

- This alpha is intended primarily for trusted small groups and evaluation.
- Background jobs and in-memory rate limits run inside the web process and assume one BinderBridge app instance.
- Internet-facing installs need an HTTPS reverse proxy, persistent operational monitoring, and regular restore drills.
- Deck-list URL imports are best effort because third-party sites can change or block export endpoints.
- Magic: The Gathering has the deepest metadata and pricing support; other games currently use generic card records.
- Confirmed large collection imports can take several minutes while the applied rows are written.
- Alpha upgrades should always begin with a verified backup.

## Roadmap Ideas

Product features:

- Broader game support with per-game card metadata, source adapters, condition labels, finish labels, and pricing/display rules
- Saved searches and reusable filter presets for collection, wishlist, browse, and trade-building views
- Trade packages for bundling named groups of cards into reusable offers
- Import source profile editor and community-shareable adapter packs
- Collection/deck collaboration tools such as shared binders, shared wishlists, and group-curated trade boxes
- Trade fulfillment checklist for accepted trades, such as packed, sent, received, and problem reported
- Address or contact exchange controls for accepted trades with privacy safeguards

Imports and integrations:

- Advanced webhook controls including retry schedules, manual delivery replay, endpoint health summaries, and per-endpoint event history filters
- In-app API documentation with endpoint examples, scope explanations, webhook signing examples, and sample payloads

Security, operations, and maintenance:

- Passkey policy controls, recovery guidance, and admin-facing enrollment visibility for self-hosted groups
- Background job runner abstraction with scheduling controls for imports, Scryfall refreshes, backups, webhooks, and email delivery
- Deployment hardening guide with HTTPS reverse proxy, SMTP setup, backup restore drills, Docker volume notes, scheduled job expectations, and recommended production settings
- Theme and accessibility polish such as high-contrast mode, reduced motion, and larger tap targets

## Security Notes

Passwords are hashed with PBKDF2-HMAC-SHA256 and sessions use HttpOnly cookies. Authenticated browser forms include CSRF tokens, and sensitive routes use simple in-memory rate limits. Password reset tokens are stored as hashes, expire, and work once. Users can enable TOTP two-factor authentication from the Account page using an authenticator app, and BinderBridge generates one-time recovery codes for account recovery. Users can also register passkeys for passwordless login; passkeys work on localhost for development, but self-hosted deployments should use HTTPS and set `BINDERBRIDGE_PUBLIC_BASE_URL` to the public site origin. API tokens are stored as hashes and webhooks are signed with per-endpoint secrets. This self-hosted build is suitable for trusted small groups, but a public internet deployment should add HTTPS, persistent/distributed rate limiting, regular restore drills, and stricter production hardening.

Profile changes require the current password. Password changes keep the current session active and sign out other active sessions.

The first registered user is made an admin automatically. If an existing database has users but no admin yet, startup promotes the earliest user to admin. Admins can ban users, unban users, issue secure password recovery links, reset two-factor authentication, manage admin access, manage trusted trade status, set the completed-trade threshold for earning trust, and save private moderation notes.

Successful admin actions are recorded in the admin activity log. The log covers account moderation, role changes, trusted-status overrides, two-factor resets, invite and registration settings, trade fairness settings, trade issue reviews, and backup or restore actions.

## License

BinderBridge is licensed under the [GNU Affero General Public License v3.0](LICENSE). Operators running modified public versions should provide users access to the corresponding source and set `BINDERBRIDGE_SOURCE_URL` to that source repository.
