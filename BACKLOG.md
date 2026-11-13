# BinderBridge Backlog

This is the canonical planning list for BinderBridge across the web/server project and the native Android client. It records likely work, not release commitments; priorities can change as releases, user feedback, security findings, and dependencies change.

## How To Use This Backlog

- **Now** is the short, ordered queue for release readiness and the next work selection.
- **Next** contains valuable, understood work to consider after the Now queue.
- **Later** contains larger, more speculative, or dependency-heavy improvements.
- Area tags identify the primary surface: **Web**, **Android**, **Cross-platform**, **Operations**, or **Research**.
- Keep only unfinished work here. Record completed behavior in the relevant README, changelog, and release notes, then remove the completed backlog item during the next planning pass.

When choosing the next item, prefer release blockers and security or data-integrity work, followed by changes that unblock both clients, then focused usability improvements. Revisit the ordering after each release candidate.

## Now

- [ ] **[Android · Tests] Expand instrumentation coverage for offline behavior.** Exercise queued writes, Sync retry and discard actions, conflict or blocked states, and cached fallback behavior on an emulator or device.
- [ ] **[Android · Release] Complete physical-device validation for the next release candidate.** Run the Android manual QA checklist against LAN HTTP and production-style HTTPS connections and capture any release-blocking findings.
- [ ] **[Operations · Release] Publish packaged and verifiable release artifacts.** Add versioned container-image publishing and signed or attested server and Android release artifacts to the release workflow.
- [ ] **[Web · Accessibility] Complete a focused accessibility and theme pass.** Add a high-contrast preference, audit compact controls for adequate tap targets, and verify the existing reduced-motion behavior across high-traffic workflows.

## Next

- [ ] **[Cross-platform · Trades] Add accepted-trade fulfillment tracking and safe contact exchange.** Track packed, sent, received, and problem-reported states while giving participants explicit, privacy-preserving control over any address or contact details they share.
- [ ] **[Web · Integrations] Expand webhook operations and API guidance.** Add configurable retry schedules, manual delivery replay, endpoint health summaries, per-endpoint event-history filters, and in-app API documentation with scopes, examples, signing guidance, and sample payloads.
- [ ] **[Web · Operations] Expand background-job administration.** Add schedule controls, per-job run history, and richer progress reporting for long-running imports and pricing-data refreshes.
- [ ] **[Web · Security] Expand passkey administration.** Add operator policy controls, clearer recovery guidance, and admin-visible enrollment status suitable for self-hosted groups.
- [ ] **[Web · UX] Add useful drill-downs from summaries.** Link dashboard metrics, admin health summaries, and collection statistics to the relevant filtered views, and clarify the priority of recent notifications.
- [ ] **[Web · UX] Finish the shared UI consistency and safety pass.** Standardize card-data presentation and page-action hierarchy, separate routine and destructive admin actions, and align compact help, empty-state next actions, save-success feedback, secondary-link hierarchy, spacing, and heading rhythm on long pages.
- [ ] **[Android · Architecture] Continue extracting high-traffic screens into dedicated native components.** Reduce the remaining programmatic single-activity layout while preserving navigation, draft restoration, loading, offline, empty, and error states.
- [ ] **[Android · Imports] Expand Import Center beyond pasted lists.** Add file pickers, CSV profile mapping, richer deck and binder destinations, and server-backed import-batch undo.

## Later

- [ ] **[Cross-platform · Games] Broaden first-class game support.** Add per-game metadata and source adapters together with game-specific condition, finish, pricing, and display rules.
- [ ] **[Cross-platform · Trades] Add reusable trade packages.** Let members name and reuse bundles of cards in offers.
- [ ] **[Web · Imports] Add an import-source profile editor and shareable adapter packs.** Make third-party collection and deck-list adapters easier to maintain when upstream formats or endpoints change.
- [ ] **[Cross-platform · Collaboration] Add shared collection planning.** Support shared binders, shared wishlists, and group-curated trade boxes with clear ownership and privacy rules.
- [ ] **[Operations · Scale] Investigate higher-concurrency storage and import performance.** Define when an optional database/backend becomes necessary, increase safe worker concurrency, and reduce the apply time of very large confirmed imports.
- [ ] **[Android · Trades] Add more prefilled trade-proposal entry points.** Start proposals from individual collection cards and public binder views in addition to mutual matches.
- [ ] **[Android · Notifications] Evaluate user-controlled notification cadence and quiet behavior.** Add controls only where Android scheduling limits can provide predictable behavior.
- [ ] **[Android · Images] Improve card-image handling.** Polish the large-image viewer and add bounded offline thumbnail caching.
- [ ] **[Cross-platform · Notifications] Add true push-style trade alerts.** Integrate Firebase Cloud Messaging with server-side device-token registration, delivery triggers, token revocation, and user notification preferences.
- [ ] **[Research · UX] Validate the remaining conditional UI ideas through live use.** Evaluate a collapsed mobile wishlist add flow, a persistent trade-selection summary, collapsible completed-trade support history, a full value-change history view, alternate trade sorting, and clearer navigation between Browse and member profiles before committing to implementation.

## Recurring Release Work

- Use the [server release checklist](RELEASE_CHECKLIST.md) for every BinderBridge release.
- Run the [Android manual QA checklist](https://github.com/thefatkid22/BinderBridge-Android/blob/main/docs/manual-qa-checklist.md) on a physical device before every Android release candidate.
- Keep the main and Android changelogs and release notes synchronized with the behavior actually shipped.
