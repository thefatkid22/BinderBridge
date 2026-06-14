# BinderBridge UI Inventory

Date: June 13, 2026

## Scope

This inventory maps BinderBridge's current user-facing interface and identifies the best targets for a focused UI polish pass. It is based on the route map, rendered view modules, shared components, responsive CSS, and public-route smoke checks.

Current interface scale:

- 83 dispatched routes
- 135 view renderers across 9 view modules
- 14 rendered tables, all using the mobile table-to-card treatment where horizontal width requires it
- Shared CSS includes responsive workspaces, table-to-card layouts, and accessibility foundations
- Shared accessible confirmation dialog used by high-risk actions

## Overall Assessment

BinderBridge already has a solid visual foundation. The dark-first theme, light-mode toggle, panel system, filter chips, pagination, empty states, responsive grids, and mobile table-to-card pattern are established and generally consistent.

The main UI risk is feature density. New capabilities have accumulated inside a few large pages, making them harder to scan and learn even though the individual controls work. The next pass should focus on information architecture and progressive disclosure before changing the overall visual style.

There are no P0 blocking UI issues in the current inventory.

## Navigation Map

### Primary Navigation

| Area | Main destination | Current condition |
| --- | --- | --- |
| Dashboard | `/` | Clear and focused |
| My Cards | `/collection` | Strong sub-navigation and mature controls |
| Wishlist | `/wants` | Searchable, filterable, paginated workspace with an in-page add flow |
| Browse | `/browse` | Strong filtering and trade entry flow |
| Trades | `/trades` | Filterable offer inbox with shared navigation into supporting workflows |
| Account | `/account` | Overloaded single-page control panel |
| Admin / Staff | `/admin` | Useful hub, but overloaded for owners and admins |

### Secondary Navigation

- My Cards has a useful sub-navigation for Collection, Stats, Decks & Binders, and Import.
- Wishlist has a useful sub-navigation for Wanted Cards and Wishlist Groups.
- Account has local section navigation across profile, notifications, security, integrations, and data.
- Admin has local section navigation and focused operational dashboards for health, jobs, collection health, and database maintenance.
- Trades now has shared navigation between offers, matches, browse, and trade updates.

## Page Inventory

### Strong Foundations

These pages need only a light consistency and accessibility pass:

| Page | Strengths | Light polish opportunities |
| --- | --- | --- |
| Dashboard | Clear metrics, useful next actions, recent activity | Make metric cards clickable and clarify notification priority |
| Collection | Advanced filters, chips, sorting, pagination, bulk actions, responsive cards, and shared confirmations | Continue refining only from live-use feedback |
| Browse | Good filtering, typeahead, pagination, inline quantities, photo preview | Clarify the relationship between Browse and member profiles |
| Collection Stats | Clear grouped summaries | Add drill-down links from statistics to filtered collection views |
| Login and Recovery | Focused forms, clear recovery behavior, passkey option | Tighten secondary-link hierarchy |
| Public Profile | Useful trade availability, reputation, wants, groups, and local section navigation | Continue refining only from live-use feedback |

### Needs Focused Restructuring

| Page | Current issue | Recommended treatment |
| --- | --- | --- |
| Account | Profile and notification settings share one very large form; password, 2FA, passkeys, API/webhooks, and export all stack below it | Add Account sub-navigation or tabs: Profile, Notifications, Security, Integrations, Data |
| Admin landing | Combines onboarding, policy settings, integration policy, registration, invites, backups, activity, disputes, and user controls | Turn it into an admin overview with status summaries; move settings into dedicated pages |
| Wishlist | The add form and tracked wants still share one long page on small screens | Consider collapsing the add form behind a compact mobile action |
| Trade builder | The card pickers remain information-dense even with the new staged hierarchy | Consider a persistent compact selected-card summary on large screens after more live-use feedback |
| Trade detail | Supporting issue, feedback, and comment sections still make completed trades long | Consider collapsing historical/supporting sections |
| Group detail | Cards, sharing, import, and group settings now have explicit work areas plus filtering, sorting, pagination, and bulk removal | Continue refining only from live-use feedback |
| Notifications | Category/read/search filtering and pagination are implemented; value changes remain a supporting panel | Consider a dedicated full value-change history page |
| Trades list | Participant/status filtering and pagination are implemented; explicit sorting is not yet exposed | Add sorting only if users need alternatives to action-first/recent ordering |

## Responsive Inventory

### Existing Strengths

- Primary navigation remains usable through horizontal overflow and responsive stacking.
- Shared grids collapse at sensible breakpoints.
- Collection, Browse, Trades, Admin Logs, Collection Health, and Migration History use the mobile table-to-card treatment.
- Forms and action groups generally become full width on small screens.
- Wants cards and condition-photo dialogs have dedicated mobile layouts.

### Remaining Wide Tables

All identified operational tables now use the shared mobile table-to-card treatment when their width would otherwise require horizontal scrolling.

### Mobile Navigation Concern

The primary navigation collapses into a compact, keyboard-accessible mobile menu while preserving active-page and unread-trade indicators.

## Consistency Inventory

### Working Well

- Shared page headings and action groups are widely used.
- Primary, secondary, ghost, and danger button roles are established.
- Filter bars, advanced filters, removable chips, sorting, and pagination have reusable components.
- Empty states exist across almost every major workflow.
- Status pills and severity colors are consistently used.
- Cards and panels keep a restrained, operational visual style.

### Drift to Address

- Destructive actions still sit directly beside routine actions on a few admin surfaces.
- Some pages use sub-navigation while other similarly complex areas do not.
- Page-level actions can become crowded with four or more equally prominent buttons.
- Long forms use section headings but do not provide a local contents menu or saved-state feedback.
- Browse cards, public-profile cards, collection rows, and trade-picker rows present similar card data through different structures.

## Accessibility Inventory

### Existing Strengths

- Forms generally use visible labels.
- Native fieldsets and legends are used for grouped preferences.
- Filter chips have removal labels.
- The trade builder uses an `aria-live` summary.
- Photo previews use native dialogs with labelled headings.
- Color is usually paired with text labels and status wording.

### Recommended Improvements

1. Review tap targets for small inline links and compact controls.
2. Add a high-contrast preference alongside the existing reduced-motion support.

## Priority Findings

### P1: Highest User Impact

1. Continue refining the staged trade builder after live-use feedback.
2. Add a dedicated full value-change history view if price alerts become a frequent workflow.

### P2: Consistency and Safety

1. Standardize card-data presentation across Collection, Browse, public profiles, and trade pickers.
2. Reduce page-header action clutter by keeping one primary action visible and moving secondary actions into a menu where appropriate.
3. Separate routine and destructive actions on remaining admin surfaces.

### P3: Accessibility and Finish

1. Add high-contrast support.
2. Make dashboard metrics and health summaries useful as drill-down links.
3. Standardize compact help text, empty-state next actions, and save-success feedback.

## Recommended Polish Sequence

### Phase 1: Structure

- Continue refining long supporting sections from live-use feedback.

### Phase 2: High-Traffic Workflows

- Consider a dedicated full value-change history page.

### Phase 3: Mobile and Accessibility

- Add high-contrast and larger-control preferences.

### Phase 4: Visual Consistency

- Standardize card rows and page actions.
- Tighten spacing and heading rhythm on long pages.
- Add dashboard drill-down links and more helpful empty-state actions.

## Suggested First Implementation Slice

Start with Account and Admin information architecture. They are the most congested pages, affect nearly every advanced feature, and can be improved without changing collection or trade behavior. After that, tackle the trade builder because it is the most consequential user workflow.

### Implementation Status

Completed June 13, 2026:

- Account now has local navigation and separate Profile, Notifications, Security, Integrations, and Data work areas.
- Profile and notification settings remain one secure submission so unchecked preferences are not accidentally reset.
- Admin now opens with a focused operations launcher and separates Policies, Access, Operations, and Users.
- The main admin user-management table now uses the shared mobile table-to-card layout.
- Trades now has shared navigation for Offers, Matches, Browse Cards, and Trade Updates.
- The trades list now has status/direction/member filtering, removable chips, action-first ordering, summary metrics, and pagination.
- The trade builder now presents a three-step flow with local navigation and clearer Offer / Request sections.
- Trade details now place status, notes, warnings, and response actions before card and support history.
- Wishlist now has search, priority/game/visibility/trade-match filters, removable chips, sorting, typeahead, and pagination.
- Notifications now has category navigation, search/read-state filters, removable chips, pagination, and a distinct value-change support area.
- Group Detail now separates Cards, Sharing, Import, and Group Settings with local navigation and summary metrics.
- Collection bulk editing now keeps routine updates visible while placing permanent deletion inside a clearly marked danger area.
- Group contents now have database-backed search, filtering, sorting, pagination, current-page selection, and bulk removal.
- High-risk actions now use a shared accessible confirmation dialog instead of native browser prompts.
- Primary navigation now collapses into a compact mobile menu with keyboard dismissal.
