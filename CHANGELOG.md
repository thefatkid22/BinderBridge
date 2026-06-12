# Changelog

All notable BinderBridge changes will be documented in this file.

The project uses semantic versioning while releases are published. During the alpha period, schema and configuration changes may still require extra care when upgrading.

## [Unreleased]

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

[Unreleased]: https://github.com/thefatkid22/BinderBridge/compare/v0.1.0-alpha.1...HEAD
[0.1.0-alpha.1]: https://github.com/thefatkid22/BinderBridge/releases/tag/v0.1.0-alpha.1
