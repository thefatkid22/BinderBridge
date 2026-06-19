# Changelog

All notable BinderBridge changes will be documented in this file.

The project uses semantic versioning while releases are published. During the alpha period, schema and configuration changes may still require extra care when upgrading.

## [Unreleased]

### Added

- Playwright browser smoke test covering first-run setup, CSV import preview/apply, collection and wishlist flows, browse-to-trade proposal, and recipient notifications
- Rich demo data seeding for local evaluation, including sample users, collections, wants, groups, trades, feedback, disputes, notifications, price history, condition photos, and a reusable seed script

## [0.2.0-alpha.3] - 2026-06-18

### Added

- First-run admin setup wizard for public URL, registration policy, email readiness, backups, Scryfall sync, first invites, and first collection import

### Changed

- Admin onboarding now includes public URL and registration-policy checks, with saved public URLs feeding invite and password-recovery link generation when no config value is set
- Polished the first-run setup experience with small-local-group recommendations, a setup-complete admin banner, direct configuration documentation links, and copyable manual invite links

## [0.2.0-alpha.2] - 2026-06-17

### Added

- Durable SQLite-backed background job runner with leases, retries, recurring schedules, progress, cancellation, admin controls, and an optional standalone worker process
- Personal saved filter and sort presets for collection, browse, wishlist, and both sides of the trade builder
- SQLite-backed API and integration rate limits for failed auth, health checks, authenticated reads, writes, Scryfall lookups, and token/webhook management
- Registration moderation with optional approval queues, privacy-safe ban-evasion signals, pending-account review, and stronger ban cleanup for sessions, API tokens, webhooks, and unused invites
- Docker deployment polish with a non-root image, healthcheck, two-service Compose stack, production config template, `.env.example`, and deployment guide

### Changed

- Routed Scryfall enrichment and refreshes, automatic backups, notification email delivery, webhooks, and legacy price work through the durable runner
- Made normal notification and webhook request paths queue work instead of blocking on outbound delivery
- Made rate limits configurable through the INI file or environment variables, with persistent storage enabled by default
- Documented first-run setup, reverse-proxy/HTTPS expectations, backup and restore drills, Docker volume notes, worker operations, upgrades, and AGPL source requirements

## [0.2.0-alpha.1] - 2026-06-15

### Added

- Secure password recovery with SMTP delivery when configured and administrator-issued manual recovery links otherwise
- Searchable, filterable, sortable, paginated group contents with current-page selection and bulk group-link removal
- Shared accessible confirmation dialogs and compact keyboard-accessible mobile navigation
- UI inventory documenting page-level strengths, remaining polish opportunities, and accessibility priorities
- Release smoke coverage for a complete multi-user trade lifecycle plus SQLite quick and foreign-key integrity checks

### Changed

- Reorganized Account into Profile, Notifications, Security, Integrations, and Data work areas
- Reorganized Admin into focused overview, policy, access, operations, and user-management work areas
- Refined the dashboard, collection, wishlist, trades, public profiles, notifications, and group pages for clearer responsive layouts
- Improved mobile list and card presentation across high-traffic and administrative workflows
- Strengthened administrator-assisted password recovery while preserving self-hosted operation without SMTP

### Validation

- Full automated suite: 207 tests
- Fresh installation and schema-version verification
- Schema upgrade from version 7 to version 9 with data preservation
- Published `v0.1.0-alpha.1` schema 8 database upgrade to schema 9 with data preservation
- Backup creation, restore, and post-restore SQLite integrity verification
- Complete two-user trade offer, acceptance, completion, ownership-transfer, and notification workflow
- Confirmed 5,000-row CSV collection import
- Docker image build and startup smoke test through GitHub Actions

## [0.1.0-alpha.1] - 2026-06-12

### Added

- Multi-user card collections, wanted cards, decks, binders, and wishlist groups
- Trade proposals, comments, counteroffers, value balancing, fairness policies, matchmaking, disputes, feedback, and reputation
- Scryfall-backed card lookup, enrichment, pricing, price history, alerts, and finish validation
- CSV import profiles, custom mapping presets, previews, undo, large-import background enrichment, and account/group exports
- Granular privacy controls, private share links, public profiles, card-condition photos, roles, passkeys, TOTP 2FA, API tokens, and webhooks
- Notification preferences, optional SMTP delivery, digests, quiet hours, stale-trade reminders, and watchlist alerts
- Admin health, job, collection-health, database-maintenance, audit-log, backup/restore, retention, invite, and moderation tools
- Responsive dark-by-default interface with mobile list layouts, filtering, sorting, pagination, and active filter chips
- Versioned SQLite migrations and automated backups

### Changed

- Split feature routes, views, query modules, Scryfall jobs, import services, and shared helpers into focused modules
- Split the integration test suite into feature-oriented files

### Known Limitations

- Alpha release intended primarily for trusted small groups
- Background workers and rate limits run in the web process and are not coordinated across multiple app instances
- Public internet deployments require an HTTPS reverse proxy and additional operational hardening
- Deck-list URL imports are best effort because third-party sites may change or block export endpoints
- Broader non-MTG metadata and pricing support is not yet implemented
- Confirmed large collection imports can take several minutes while the applied rows are written

[Unreleased]: https://github.com/thefatkid22/BinderBridge/compare/v0.2.0-alpha.3...HEAD
[0.2.0-alpha.3]: https://github.com/thefatkid22/BinderBridge/compare/v0.2.0-alpha.2...v0.2.0-alpha.3
[0.2.0-alpha.2]: https://github.com/thefatkid22/BinderBridge/compare/v0.2.0-alpha.1...v0.2.0-alpha.2
[0.2.0-alpha.1]: https://github.com/thefatkid22/BinderBridge/compare/v0.1.0-alpha.1...v0.2.0-alpha.1
[0.1.0-alpha.1]: https://github.com/thefatkid22/BinderBridge/releases/tag/v0.1.0-alpha.1
