"""Collection, browse, import, cleanup, and statistics views.

This module is wired by binderbridge.views; shared app helpers are injected at runtime.
"""

from binderbridge.collection_queries import *
from binderbridge.components import *


def query_value(query, key):
    return query.get(key, [""])[0].strip()


def query_nonnegative_int(query, key):
    value = query_value(query, key)
    if value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def collection_filters(query):
    filters = collection_filter_values(query)
    return filters["q"], filters["game"], filters["trade_only"]


def collection_filter_values(query):
    filters = {
        "q": query_value(query, "q"),
        "game": query_value(query, "game"),
        "trade_only": query_value(query, "trade_only") == "1",
        "set_name": query_value(query, "set_name"),
        "set_code": query_value(query, "set_code").upper(),
        "collector_number": query_value(query, "collector_number"),
        "type_line": query_value(query, "type_line"),
        "condition": query_value(query, "condition").upper(),
        "finish": query_value(query, "finish"),
        "language": query_value(query, "language"),
        "rarity": query_value(query, "rarity").lower(),
        "color_identity": query_value(query, "color_identity").upper(),
        "card_data": query_value(query, "card_data"),
        "visibility": query_value(query, "visibility"),
        "quantity_min": query_nonnegative_int(query, "quantity_min"),
        "quantity_max": query_nonnegative_int(query, "quantity_max"),
        "trade_min": query_nonnegative_int(query, "trade_min"),
        "trade_max": query_nonnegative_int(query, "trade_max"),
    }
    if filters["game"] and filters["game"] not in dict(CARD_GAMES):
        filters["game"] = ""
    if filters["condition"] and filters["condition"] not in CONDITION_OPTIONS:
        filters["condition"] = ""
    if filters["finish"] and filters["finish"] not in FINISH_OPTIONS:
        filters["finish"] = ""
    if filters["language"] and filters["language"] not in LANGUAGE_OPTIONS:
        filters["language"] = ""
    if filters["rarity"] and filters["rarity"] not in RARITY_OPTIONS:
        filters["rarity"] = ""
    if filters["color_identity"] and filters["color_identity"] not in dict(COLOR_IDENTITY_OPTIONS):
        filters["color_identity"] = ""
    if filters["card_data"] and filters["card_data"] not in dict(CARD_DATA_FILTER_OPTIONS):
        filters["card_data"] = ""
    if filters["visibility"] not in ("", "public", "private"):
        filters["visibility"] = ""
    return filters


def collection_has_advanced_filters(filters):
    return any(
        filters.get(key) not in ("", None, False)
        for key in (
            "set_name",
            "set_code",
            "collector_number",
            "type_line",
            "condition",
            "finish",
            "language",
            "rarity",
            "color_identity",
            "card_data",
            "visibility",
            "quantity_min",
            "quantity_max",
            "trade_min",
            "trade_max",
        )
    )


def collection_hidden_filter_inputs(filters):
    inputs = []
    for key, value in filters.items():
        if value in ("", None, False):
            continue
        inputs.append(f'<input type="hidden" name="{e(key)}" value="{e("1" if value is True else value)}">')
    return "".join(inputs)


CARD_SORT_OPTIONS = (
    ("name", "Name"),
    ("set", "Set"),
    ("game", "Game"),
    ("qty", "Qty"),
    ("trade", "Trade"),
    ("quality", "Quality"),
    ("finish", "Finish"),
    ("value", "Value"),
    ("updated", "Updated"),
)

WANT_SORT_OPTIONS = (
    ("priority", "Priority"),
    ("budget", "Budget cap"),
    ("name", "Name"),
    ("set", "Set"),
    ("game", "Game"),
    ("qty", "Wanted qty"),
    ("quality", "Quality"),
    ("finish", "Finish"),
    ("value", "Value"),
    ("updated", "Updated"),
)


def collection_filter_chip_specs():
    return (
        {"key": "q", "label": "Search"},
        {"key": "game", "label": "Game", "formatter": game_label},
        {"key": "trade_only", "label": "For trade only", "standalone": True},
        {"key": "set_name", "label": "Set"},
        {"key": "set_code", "label": "Set code"},
        {"key": "collector_number", "label": "Collector #"},
        {"key": "type_line", "label": "Type"},
        {"key": "condition", "label": "Condition"},
        {"key": "finish", "label": "Finish"},
        {"key": "language", "label": "Language"},
        {"key": "rarity", "label": "Rarity", "formatter": title_value_label},
        {"key": "color_identity", "label": "Color", "formatter": lambda value: option_value_label(COLOR_IDENTITY_OPTIONS, value)},
        {"key": "card_data", "label": "Card data", "formatter": lambda value: option_value_label(CARD_DATA_FILTER_OPTIONS, value)},
        {"key": "visibility", "label": "Visibility", "formatter": title_value_label},
        {"key": "quantity_min", "label": "Qty", "formatter": min_filter_label},
        {"key": "quantity_max", "label": "Qty", "formatter": max_filter_label},
        {"key": "trade_min", "label": "Trade", "formatter": min_filter_label},
        {"key": "trade_max", "label": "Trade", "formatter": max_filter_label},
    )


def browse_filter_chip_specs(owner_labels):
    return (
        {"key": "q", "label": "Search"},
        {"key": "owner_id", "query_key": "user", "label": "User", "formatter": lambda value: owner_labels.get(int(value), f"User #{value}")},
        {"key": "quality", "label": "Quality"},
        {"key": "game", "label": "Game", "formatter": game_label},
        {"key": "finish", "label": "Finish"},
        {"key": "set_name", "label": "Set"},
        {"key": "set_code", "label": "Set code"},
        {"key": "collector_number", "label": "Collector #"},
        {"key": "type_line", "label": "Type"},
        {"key": "language", "label": "Language"},
        {"key": "rarity", "label": "Rarity", "formatter": title_value_label},
        {"key": "color_identity", "label": "Color", "formatter": lambda value: option_value_label(COLOR_IDENTITY_OPTIONS, value)},
        {"key": "card_data", "label": "Card data", "formatter": lambda value: option_value_label(CARD_DATA_FILTER_OPTIONS, value)},
        {"key": "quantity_min", "label": "Owned qty", "formatter": min_filter_label},
        {"key": "quantity_max", "label": "Owned qty", "formatter": max_filter_label},
        {"key": "trade_min", "label": "Available", "formatter": min_filter_label},
        {"key": "trade_max", "label": "Available", "formatter": max_filter_label},
    )


def trade_picker_filter_chip_specs(prefix):
    return (
        {"key": "q", "query_key": f"{prefix}_q", "label": "Search"},
        {"key": "game", "query_key": f"{prefix}_game", "label": "Game", "formatter": game_label},
        {"key": "condition", "query_key": f"{prefix}_condition", "label": "Condition"},
        {"key": "finish", "query_key": f"{prefix}_finish", "label": "Finish"},
        {"key": "set_name", "query_key": f"{prefix}_set_name", "label": "Set"},
        {"key": "set_code", "query_key": f"{prefix}_set_code", "label": "Set code"},
        {"key": "collector_number", "query_key": f"{prefix}_collector_number", "label": "Collector #"},
        {"key": "type_line", "query_key": f"{prefix}_type_line", "label": "Type"},
        {"key": "language", "query_key": f"{prefix}_language", "label": "Language"},
        {"key": "rarity", "query_key": f"{prefix}_rarity", "label": "Rarity", "formatter": title_value_label},
        {"key": "color_identity", "query_key": f"{prefix}_color_identity", "label": "Color", "formatter": lambda value: option_value_label(COLOR_IDENTITY_OPTIONS, value)},
        {"key": "card_data", "query_key": f"{prefix}_card_data", "label": "Card data", "formatter": lambda value: option_value_label(CARD_DATA_FILTER_OPTIONS, value)},
        {"key": "quantity_min", "query_key": f"{prefix}_quantity_min", "label": "Owned qty", "formatter": min_filter_label},
        {"key": "quantity_max", "query_key": f"{prefix}_quantity_max", "label": "Owned qty", "formatter": max_filter_label},
        {"key": "trade_min", "query_key": f"{prefix}_trade_min", "label": "Available", "formatter": min_filter_label},
        {"key": "trade_max", "query_key": f"{prefix}_trade_max", "label": "Available", "formatter": max_filter_label},
    )

def render_cleanup_group_items(group, quantity_label):
    return "".join(
        f"""
        <li>
            <strong>Row #{e(item["id"])} - {e(row_value(item, quantity_label, 0))}</strong>
            <span>{e(row_value(item, "set_name", "") or "Any set")} - {e(row_value(item, "condition", "") or "Any condition")} - {e(row_value(item, "finish", "") or "Any finish")}</span>
            {f'<span>{e(row_value(item, "notes", ""))}</span>' if row_value(item, "notes", "") else ""}
        </li>
        """
        for item in group["items"]
    )


def render_duplicate_cleanup_panel(title, groups, action, empty_text, quantity_key, total_label):
    if not groups:
        body = f'<div class="empty-state compact-empty">{e(empty_text)}</div>'
    else:
        group_cards = "".join(
            f"""
            <article class="duplicate-card">
                <label class="checkbox-line">
                    <input type="checkbox" name="group_key" value="{e(group["key"])}" checked>
                    <strong>{e(group["label"])}</strong>
                </label>
                <div class="duplicate-card-meta">
                    <span>{e(group["count"])} rows</span>
                    <span>{e(group[quantity_key])} {e(total_label)}</span>
                </div>
                <ul class="stack-list compact-stack">{render_cleanup_group_items(group, quantity_key if quantity_key == "desired_quantity" else "quantity")}</ul>
            </article>
            """
            for group in groups
        )
        body = f"""
        <form class="duplicate-cleanup-form" method="post" action="{e(action)}">
            <div class="duplicate-grid">{group_cards}</div>
            <div class="form-actions">
                <button class="button primary" type="submit" onclick="return confirm('Merge the selected duplicate groups?')">Merge selected</button>
            </div>
        </form>
        """
    return f"""
    <section class="panel cleanup-panel">
        <div class="panel-heading">
            <h2>{e(title)}</h2>
            <span class="pill">{e(len(groups))} group{'s' if len(groups) != 1 else ''}</span>
        </div>
        {body}
    </section>
    """


def render_cleanup(user, notice=None, status="info"):
    summary = duplicate_cleanup_summary(user["id"])
    content = f"""
    <section class="section-heading">
        <div>
            <p class="eyebrow">Data cleanup</p>
            <h1>Duplicate cleanup</h1>
            <p class="lead">Merge exact duplicate collection and wishlist rows while preserving group links, trade references, notes, and price history.</p>
        </div>
        <div class="actions">
            <a class="button secondary" href="/cleanup/audit">Condition &amp; finish audit</a>
            <a class="button secondary" href="/collection">My cards</a>
            <a class="button secondary" href="/wants">Wishlist</a>
        </div>
    </section>
    <section class="metric-grid compact-metrics">
        <article class="metric"><span>{e(len(summary["collection_groups"]))}</span><p>collection duplicate groups</p></article>
        <article class="metric"><span>{e(summary["collection_duplicate_rows"])}</span><p>extra collection rows</p></article>
        <article class="metric"><span>{e(len(summary["want_groups"]))}</span><p>wishlist duplicate groups</p></article>
        <article class="metric"><span>{e(summary["want_duplicate_rows"])}</span><p>extra wanted rows</p></article>
    </section>
    <section class="content-grid cleanup-grid">
        {render_duplicate_cleanup_panel("Collection duplicates", summary["collection_groups"], "/cleanup/collection", "No exact collection duplicates found.", "quantity", "owned")}
        {render_duplicate_cleanup_panel("Wanted-card duplicates", summary["want_groups"], "/cleanup/wants", "No exact wanted-card duplicates found.", "desired_quantity", "wanted")}
    </section>
    """
    return render_layout(user, "Duplicate cleanup", content, active="account", notice=notice, status=status)


def render_audit_issue_badges(item):
    if not item["issues"]:
        return '<span class="pill">Clean</span>'
    badges = []
    for issue in item["issues"]:
        tone = "danger" if issue in ("missing_condition", "missing_finish", "invalid_condition", "invalid_finish", "scryfall_finish_mismatch") else "warning"
        badges.append(f'<span class="audit-badge {tone}">{e(AUDIT_ISSUE_LABELS[issue])}</span>')
    return "".join(badges)


def render_audit_value(current_value, suggested_value, can_normalize, empty_text):
    current = str(current_value or "").strip()
    if not current:
        return f'<span class="audit-current missing">{e(empty_text)}</span>'
    suggestion = (
        f'<span class="audit-suggestion">Normalize to {e(suggested_value)}</span>'
        if can_normalize and suggested_value
        else ""
    )
    return f'<span class="audit-current">{e(current)}</span>{suggestion}'


def render_condition_finish_audit_row(item):
    set_bits = [row_value(item, "set_name", "")]
    if row_value(item, "set_code", ""):
        set_bits.append(f'({row_value(item, "set_code")})')
    if row_value(item, "collector_number", ""):
        set_bits.append(f'#{row_value(item, "collector_number")}')
    set_text = " ".join(str(bit) for bit in set_bits if bit) or "Any set"
    finish_cell = render_audit_value(item["finish"], item["suggested_finish"], item["normalize_finish"], "Missing finish")
    if item["scryfall_finish_labels"]:
        finish_cell += f'<span class="audit-suggestion">Available finishes: {e(item["scryfall_finish_labels"])}</span>'
    return f"""
    <tr>
        <td class="select-col"><input type="checkbox" name="item_id" value="{e(item["id"])}"></td>
        <td>
            <strong>{e(item["card_name"])}</strong>
            <span class="subtle">{e(row_value(item, "type_line", "") or row_value(item, "game", ""))}</span>
        </td>
        <td>{e(set_text)}</td>
        <td>{render_audit_value(item["condition"], item["suggested_condition"], item["normalize_condition"], "Missing condition")}</td>
        <td>{finish_cell}</td>
        <td><span class="pill">{e(item["quantity"])}</span></td>
        <td><span class="pill">{e(item["quantity_for_trade"])}</span></td>
        <td><div class="audit-badge-list">{render_audit_issue_badges(item)}</div></td>
        <td class="actions-cell"><a class="button ghost small" href="/collection/{item["id"]}/edit">Edit</a></td>
    </tr>
    """


def render_condition_finish_audit(user, query, notice=None, status="info"):
    filters = condition_finish_audit_filter_values(query)
    audited_rows = collection_condition_finish_audit_rows(user["id"], filters)
    total_count = len(audited_rows)
    page, per_page, page_count, offset = pagination_state(query, total_count)
    page_items = audited_rows[offset:offset + per_page]
    summary = condition_finish_audit_summary(user["id"])
    pagination = render_pagination("/cleanup/audit", query, total_count, page, per_page, page_count)
    redirect_to = page_url("/cleanup/audit", query, page, per_page)
    hidden_filters = condition_finish_audit_hidden_inputs(filters)
    row_html = "".join(render_condition_finish_audit_row(item) for item in page_items)
    issue_options = option_tags(AUDIT_ISSUE_OPTIONS, filters["issue"])
    condition_options = simple_option_tags(CONDITION_OPTIONS, filters["condition"])
    finish_options = simple_option_tags(FINISH_OPTIONS, filters["finish"])
    new_condition_options = simple_option_tags(CONDITION_OPTIONS, "")
    new_finish_options = simple_option_tags(FINISH_OPTIONS, "")
    collection_datalists = "".join(
        [
            render_datalist("audit-search-suggestions", collection_search_suggestions(user["id"])),
            render_datalist("audit-set-name-suggestions", collection_field_suggestions(user["id"], "set_name")),
        ]
    )
    table = f"""
    <form method="post" action="/cleanup/audit/update">
        <input type="hidden" name="redirect_to" value="{e(redirect_to)}">
        {hidden_filters}
        <div class="bulk-action-bar audit-action-bar">
            <div>
                <span class="muted compact">Select rows that need condition or finish cleanup.</span>
                <span class="subtle">Normalize only changes recognized imported labels; manual updates apply the selected values.</span>
            </div>
            <div class="bulk-update-controls">
                <label>Set condition
                    <select name="new_condition">
                        <option value="">No change</option>
                        {new_condition_options}
                    </select>
                </label>
                <label>Set finish
                    <select name="new_finish">
                        <option value="">No change</option>
                        {new_finish_options}
                    </select>
                </label>
                <button class="button secondary small" type="submit">Apply selected</button>
                <button class="button secondary small" type="submit" formaction="/cleanup/audit/update-all" onclick="return confirm('Update all {total_count} cards matching this audit view?')">Apply all matching</button>
            </div>
            <div class="actions">
                <button class="button secondary small" type="submit" formaction="/cleanup/audit/normalize">Normalize selected</button>
                <button class="button secondary small" type="submit" formaction="/cleanup/audit/normalize-all" onclick="return confirm('Normalize all recognized labels matching this audit view?')">Normalize all matching</button>
            </div>
        </div>
        <div class="table-wrap">
            <table>
                <thead>
                    <tr>
                        <th class="select-col">
                            <label class="select-all-control">
                                <input type="checkbox" onclick="this.form.querySelectorAll('input[name=item_id]').forEach((box) => box.checked = this.checked)">
                                <span>All</span>
                            </label>
                        </th>
                        <th>Card</th>
                        <th>Set</th>
                        <th>Condition</th>
                        <th>Finish</th>
                        <th>Qty</th>
                        <th>Trade</th>
                        <th>Issue</th>
                        <th></th>
                    </tr>
                </thead>
                <tbody>{row_html}</tbody>
            </table>
        </div>
    </form>
    """ if page_items else '<div class="empty-state">No collection cards need condition or finish cleanup for this view.</div>'
    advanced_active = bool(filters["set_name"] or filters["condition"] or filters["finish"])
    content = f"""
    {render_cards_subnav("collection")}
    <section class="section-heading">
        <div>
            <p class="eyebrow">Collection hygiene</p>
            <h1>Condition &amp; finish audit</h1>
            <p class="lead">Find collection cards with missing, unknown, or import-style condition and finish values before they show up in trades.</p>
        </div>
        <div class="actions">
            <a class="button secondary" href="/cleanup">Duplicate cleanup</a>
            <a class="button secondary" href="/collection">Collection</a>
        </div>
    </section>
    <section class="metric-grid compact-metrics">
        <article class="metric"><span>{e(summary["total"])}</span><p>cards need audit</p></article>
        <article class="metric"><span>{e(summary["missing_condition"])}</span><p>missing condition</p></article>
        <article class="metric"><span>{e(summary["missing_finish"])}</span><p>missing finish</p></article>
        <article class="metric"><span>{e(summary["scryfall_finish_mismatch"])}</span><p>finish mismatch</p></article>
        <article class="metric"><span>{e(summary["trade_needs_review"])}</span><p>trade cards to review</p></article>
    </section>
    <form class="filter-bar collection-filter-bar audit-filter-bar" method="get" action="/cleanup/audit">
        <div class="filter-primary-row">
            <label class="search-field">Search
                <input name="q" value="{e(filters["q"])}" placeholder="Card name or type" list="audit-search-suggestions">
            </label>
            <label>Issue
                <select name="issue">{issue_options}</select>
            </label>
            <label>Game
                <select name="game">
                    <option value="">All games</option>
                    {option_tags(CARD_GAMES, filters["game"])}
                </select>
            </label>
            <label class="checkbox-line">
                <input type="checkbox" name="trade_only" value="1"{checked(filters["trade_only"])}>
                For trade only
            </label>
            <div class="actions filter-actions">
                <button class="button secondary" type="submit">Filter</button>
                <a class="button ghost" href="/cleanup/audit">Reset</a>
            </div>
        </div>
        <details class="advanced-filter"{" open" if advanced_active else ""}>
            <summary>
                <span>More filters</span>
                <span class="advanced-filter-count">Set, condition, finish</span>
            </summary>
            <div class="advanced-filter-grid">
                <label>Set name
                    <input name="set_name" value="{e(filters["set_name"])}" placeholder="Modern Horizons 3" list="audit-set-name-suggestions">
                </label>
                <label>Current condition
                    <select name="condition">
                        <option value="">Any condition</option>
                        {condition_options}
                    </select>
                </label>
                <label>Current finish
                    <select name="finish">
                        <option value="">Any finish</option>
                        {finish_options}
                    </select>
                </label>
            </div>
        </details>
        {collection_datalists}
    </form>
    <section class="panel flush">{table}</section>
    {pagination}
    """
    return render_layout(user, "Condition & finish audit", content, active="cards", notice=notice, status=status)


def render_collection(user, query, notice=None, status="info"):
    filters = collection_filter_values(query)
    q = filters["q"]
    game = filters["game"]
    trade_only = filters["trade_only"]
    advanced_active = collection_has_advanced_filters(filters)
    advanced_count = sum(
        1
        for key, value in filters.items()
        if key not in ("q", "game", "trade_only") and value not in ("", None, False)
    )
    where, params = collection_where(user["id"], filters)
    current_sort, current_dir = sort_state(query, CARD_SORT_OPTIONS)
    order_clause = sort_order_clause(
        query,
        CARD_SORT_OPTIONS,
        COLLECTION_SORT_SQL,
        fallback=("card_name COLLATE NOCASE", "set_name COLLATE NOCASE", "collector_number COLLATE NOCASE"),
    )
    total_count = collection_count(where, params)
    page, per_page, page_count, offset = pagination_state(query, total_count)
    items = collection_page_rows(where, params, order_clause, per_page, offset)
    item_rows = "".join(render_collection_row(item, owner_view=True, bulk_select=True) for item in items)
    pagination = render_pagination("/collection", query, total_count, page, per_page, page_count)
    redirect_to = current_collection_url("/collection", query, page, per_page)
    hidden_filters = collection_hidden_filter_inputs(filters)
    condition_options = simple_option_tags(CONDITION_OPTIONS, filters["condition"])
    finish_options = simple_option_tags(FINISH_OPTIONS, filters["finish"])
    language_options = simple_option_tags(LANGUAGE_OPTIONS, filters["language"])
    rarity_options = "".join(
        f'<option value="{rarity}"{selected(filters["rarity"], rarity)}>{e(rarity.title())}</option>'
        for rarity in RARITY_OPTIONS
    )
    color_options = option_tags(COLOR_IDENTITY_OPTIONS, filters["color_identity"])
    card_data_options = option_tags(CARD_DATA_FILTER_OPTIONS, filters["card_data"])
    advanced_summary = f"{advanced_count} active" if advanced_count else "More options"
    collection_datalists = "".join(
        [
            render_datalist("collection-search-suggestions", collection_search_suggestions(user["id"])),
            render_datalist("collection-set-name-suggestions", collection_field_suggestions(user["id"], "set_name")),
            render_datalist("collection-set-code-suggestions", collection_field_suggestions(user["id"], "set_code")),
            render_datalist("collection-collector-number-suggestions", collection_field_suggestions(user["id"], "collector_number")),
            render_datalist("collection-type-line-suggestions", collection_field_suggestions(user["id"], "type_line")),
        ]
    )
    table = f"""
    <form method="post" action="/collection/bulk-update">
        <input type="hidden" name="redirect_to" value="{e(redirect_to)}">
        <div class="bulk-action-bar">
            <div>
                <span class="muted compact">Select rows to update or remove them from your collection.</span>
                <span class="subtle">Blank quantity fields keep the current value. Trade quantity is capped at owned quantity.</span>
            </div>
            <div class="bulk-update-controls">
                <label>Qty owned
                    <input type="number" min="0" name="quantity" placeholder="No change">
                </label>
                <label>Trade qty
                    <input type="number" min="0" name="quantity_for_trade" placeholder="No change">
                </label>
                <label>Visibility
                    <select name="is_public">
                        <option value="">No change</option>
                        <option value="1">Public</option>
                        <option value="0">Private</option>
                    </select>
                </label>
                <button class="button secondary small" type="submit" formaction="/collection/bulk-update">Update selected</button>
                <button class="button secondary small" type="submit" formaction="/collection/update-all" onclick="return confirm('Update all {total_count} cards matching the current filters?')">Update all</button>
            </div>
            <div class="actions">
                <button class="button danger small" type="submit" formaction="/collection/bulk-delete" onclick="return confirm('Delete selected cards from your collection?')">Delete selected</button>
                <button class="button danger small" type="submit" formaction="/collection/delete-all" onclick="return confirm('Delete all {total_count} cards matching the current filters? This cannot be undone.')">Delete all</button>
            </div>
        </div>
        {hidden_filters}
        <div class="table-wrap">
            <table class="responsive-card-table collection-table">
                <thead>
                    <tr>
                        <th class="select-col">
                            <label class="select-all-control">
                                <input type="checkbox" onclick="this.form.querySelectorAll('input[name=item_id]').forEach((box) => box.checked = this.checked)">
                                <span>All</span>
                            </label>
                        </th>
                        <th>Card</th>
                        <th>Game</th>
                        <th>Set</th>
                        <th>Code</th>
                        <th>Qty</th>
                        <th>Trade</th>
                        <th>Details</th>
                        <th></th>
                    </tr>
                </thead>
                <tbody>{item_rows}</tbody>
            </table>
        </div>
    </form>
    """ if items else '<div class="empty-state">No cards match this view.</div>'
    export_url = page_url("/collection/export", query, 1)
    active_filter_chips = render_active_filter_chips(
        "/collection",
        query,
        filters,
        collection_filter_chip_specs(),
        class_name="collection-active-filters",
    )
    content = f"""
    {render_cards_subnav("collection")}
    <section class="section-heading">
        <div>
            <p class="eyebrow">My Cards</p>
            <h1>Your cards</h1>
        </div>
        <div class="actions">
            <a class="button secondary" href="/cleanup/audit">Audit condition/finish</a>
            <a class="button secondary" href="/cleanup">Cleanup duplicates</a>
            <a class="button secondary" href="{e(export_url)}">Export CSV</a>
            <a class="button primary" href="/collection/new">Add card</a>
        </div>
    </section>
    <form class="filter-bar collection-filter-bar" method="get" action="/collection">
        <div class="filter-primary-row">
            <label class="search-field">Search
                <input name="q" value="{e(q)}" placeholder="Card name or type" list="collection-search-suggestions">
            </label>
            <label>Game
                <select name="game">
                    <option value="">All games</option>
                    {option_tags(CARD_GAMES, game)}
                </select>
            </label>
            <label class="checkbox-line">
                <input type="checkbox" name="trade_only" value="1"{checked(trade_only)}>
                For trade only
            </label>
            {render_sort_controls(CARD_SORT_OPTIONS, current_sort, current_dir)}
            <div class="actions filter-actions">
                <button class="button secondary" type="submit">Filter</button>
                <a class="button ghost" href="/collection">Reset</a>
            </div>
        </div>
        <details class="advanced-filter"{" open" if advanced_active else ""}>
            <summary>
                <span>Advanced filters</span>
                <span class="advanced-filter-count">{e(advanced_summary)}</span>
            </summary>
            <div class="advanced-filter-grid">
                <label>Set name
                    <input name="set_name" value="{e(filters["set_name"])}" placeholder="Modern Horizons 3" list="collection-set-name-suggestions">
                </label>
                <label>Set code
                    <input name="set_code" value="{e(filters["set_code"])}" placeholder="MH3" list="collection-set-code-suggestions">
                </label>
                <label>Collector #
                    <input name="collector_number" value="{e(filters["collector_number"])}" placeholder="123" list="collection-collector-number-suggestions">
                </label>
                <label>Type line
                    <input name="type_line" value="{e(filters["type_line"])}" placeholder="Creature, artifact, planeswalker" list="collection-type-line-suggestions">
                </label>
                <label>Condition
                    <select name="condition">
                        <option value="">Any condition</option>
                        {condition_options}
                    </select>
                </label>
                <label>Finish
                    <select name="finish">
                        <option value="">Any finish</option>
                        {finish_options}
                    </select>
                </label>
                <label>Language
                    <select name="language">
                        <option value="">Any language</option>
                        {language_options}
                    </select>
                </label>
                <label>Rarity
                    <select name="rarity">
                        <option value="">Any rarity</option>
                        {rarity_options}
                    </select>
                </label>
                <label>Color identity
                    <select name="color_identity">
                        <option value="">Any color identity</option>
                        {color_options}
                    </select>
                </label>
                <label>Card data
                    <select name="card_data">
                        <option value="">Any card data</option>
                        {card_data_options}
                    </select>
                </label>
                <label>Visibility
                    <select name="visibility">
                        <option value="">Any visibility</option>
                        <option value="public"{selected(filters["visibility"], "public")}>Public</option>
                        <option value="private"{selected(filters["visibility"], "private")}>Private</option>
                    </select>
                </label>
                <label>Qty min
                    <input type="number" min="0" name="quantity_min" value="{e(filters["quantity_min"])}">
                </label>
                <label>Qty max
                    <input type="number" min="0" name="quantity_max" value="{e(filters["quantity_max"])}">
                </label>
                <label>Trade min
                    <input type="number" min="0" name="trade_min" value="{e(filters["trade_min"])}">
                </label>
                <label>Trade max
                    <input type="number" min="0" name="trade_max" value="{e(filters["trade_max"])}">
                </label>
            </div>
        </details>
        {collection_datalists}
    </form>
    {active_filter_chips}
    <section class="panel flush">{table}</section>
    {pagination}
    """
    return render_layout(user, "Collection", content, active="cards", notice=notice, status=status)


def stat_percent_text(value):
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return "0%"
    return f"{int(number)}%" if number.is_integer() else f"{number:.1f}%"


def render_stat_breakdown(title, bucket_rows, empty_text="No collection data yet."):
    if not bucket_rows:
        body = f'<div class="empty-state compact-empty">{e(empty_text)}</div>'
    else:
        body = "".join(
            f"""
            <li class="stat-breakdown-row">
                <div class="stat-breakdown-topline">
                    <strong>{e(item["label"])}</strong>
                    <span>{e(item["quantity"])} cards</span>
                </div>
                <div class="stat-breakdown-bar" aria-hidden="true">
                    <span style="width: {e(min(100, max(0, float(item.get("percent", 0) or 0))))}%"></span>
                </div>
                <div class="stat-breakdown-meta">
                    <span>{e(stat_percent_text(item.get("percent", 0)))} of collection</span>
                    <span>{e(item.get("entries", 0))} entries</span>
                    {f'<span>{e(money(item.get("value_cents", 0)))}</span>' if item.get("value_cents") else ""}
                </div>
            </li>
            """
            for item in bucket_rows
        )
        body = f'<ul class="stat-breakdown-list">{body}</ul>'
    return f"""
    <article class="panel stat-panel">
        <div class="panel-heading">
            <h2>{e(title)}</h2>
        </div>
        {body}
    </article>
    """


def render_stat_coverage_row(label, value, total, percent):
    return f"""
    <li class="stat-breakdown-row">
        <div class="stat-breakdown-topline">
            <strong>{e(label)}</strong>
            <span>{e(value)} / {e(total)} cards</span>
        </div>
        <div class="stat-breakdown-bar" aria-hidden="true">
            <span style="width: {e(min(100, max(0, float(percent or 0))))}%"></span>
        </div>
        <div class="stat-breakdown-meta">
            <span>{e(stat_percent_text(percent))}</span>
        </div>
    </li>
    """


def render_collection_top_value(top_value):
    if not top_value:
        return '<div class="empty-state compact-empty">No priced cards yet.</div>'
    rows_html = "".join(
        f"""
        <li class="stat-value-row">
            <div>
                <strong>{e(item["card_name"])}</strong>
                <span>{e(item["set_name"] or "Any set")} {e("(" + item["set_code"] + ")" if item["set_code"] else "")} {e("#" + item["collector_number"] if item["collector_number"] else "")}</span>
            </div>
            <div>
                <strong>{e(money(item["total_cents"]))}</strong>
                <span>{e(item["quantity"])} x {e(money(item["unit_cents"]))}</span>
            </div>
        </li>
        """
        for item in top_value
    )
    return f'<ul class="stat-value-list">{rows_html}</ul>'


def render_group_count_summary(group_counts):
    if not group_counts:
        return '<div class="empty-state compact-empty">No groups yet.</div>'
    return "".join(
        f'<span class="stat-chip"><strong>{e(item["count"])}</strong>{e(item["label"])}</span>'
        for item in group_counts
    )


def render_collection_statistics(user, notice=None, status="info"):
    stats = collection_statistics(user["id"])
    content = f"""
    {render_cards_subnav("stats")}
    <section class="section-heading">
        <div>
            <p class="eyebrow">My Cards</p>
            <h1>Collection stats</h1>
        </div>
        <div class="actions">
            <a class="button secondary" href="/collection">Collection</a>
            <a class="button secondary" href="/collection/export">Export CSV</a>
            <a class="button primary" href="/collection/new">Add card</a>
        </div>
    </section>
    <section class="metric-grid compact-metrics">
        <article class="metric"><span>{e(stats["total_cards"])}</span><p>Total cards</p></article>
        <article class="metric"><span>{e(stats["unique_cards"])}</span><p>Unique cards</p></article>
        <article class="metric"><span>{e(money(stats["total_value_cents"]))}</span><p>Collection value</p></article>
        <article class="metric"><span>{e(money(stats["trade_value_cents"]))}</span><p>Trade value</p></article>
        <article class="metric"><span>{e(stat_percent_text(stats["price_coverage_percent"]))}</span><p>Price coverage</p></article>
        <article class="metric"><span>{e(stats["trade_cards"])}</span><p>For trade</p></article>
    </section>
    <section class="content-grid stats-overview-grid">
        <article class="panel stat-panel">
            <div class="panel-heading">
                <h2>Value summary</h2>
            </div>
            <div class="stat-summary-grid">
                <span><strong>{e(money(stats["total_value_cents"]))}</strong>Total value</span>
                <span><strong>{e(money(stats["trade_value_cents"]))}</strong>Available trade value</span>
                <span><strong>{e(money(stats["average_priced_value_cents"]))}</strong>Average priced card</span>
                <span><strong>{e(stats["priced_entries"])}</strong>Priced entries</span>
            </div>
        </article>
        <article class="panel stat-panel">
            <div class="panel-heading">
                <h2>Data coverage</h2>
            </div>
            <ul class="stat-breakdown-list">
                {render_stat_coverage_row("Priced", stats["priced_cards"], stats["total_cards"], stats["price_coverage_percent"])}
                {render_stat_coverage_row("Scryfall data", stats["scryfall_cards"], stats["total_cards"], stats["scryfall_coverage_percent"])}
                {render_stat_coverage_row("Images", stats["image_cards"], stats["total_cards"], stats["image_coverage_percent"])}
            </ul>
        </article>
    </section>
    <section class="content-grid stats-grid">
        {render_stat_breakdown("Games", stats["buckets"]["game"])}
        {render_stat_breakdown("Rarity mix", stats["buckets"]["rarity"])}
        {render_stat_breakdown("Condition mix", stats["buckets"]["condition"])}
        {render_stat_breakdown("Finish mix", stats["buckets"]["finish"])}
        {render_stat_breakdown("Language coverage", stats["buckets"]["language"])}
        {render_stat_breakdown("Top sets", stats["buckets"]["set"])}
        {render_stat_breakdown("Color identity", stats["buckets"]["color_identity"])}
        {render_stat_breakdown("Visibility", stats["buckets"]["visibility"])}
        <article class="panel stat-panel">
            <div class="panel-heading">
                <h2>Most valuable entries</h2>
            </div>
            {render_collection_top_value(stats["top_value"])}
        </article>
        <article class="panel stat-panel">
            <div class="panel-heading">
                <h2>Groups</h2>
            </div>
            <div class="stat-chip-list">{render_group_count_summary(stats["group_counts"])}</div>
        </article>
    </section>
    """
    return render_layout(user, "Collection stats", content, active="cards", notice=notice, status=status)


def browse_filters(query):
    filters = browse_filter_values(query)
    return filters["q"], filters["game"], filters["quality"], filters["finish"], filters["owner_id"]


def browse_filter_values(query):
    filters = {
        "q": query_value(query, "q"),
        "game": query_value(query, "game"),
        "quality": query_value(query, "quality").upper(),
        "finish": query_value(query, "finish"),
        "owner_id": query_int(query, "user", 0),
        "set_name": query_value(query, "set_name"),
        "set_code": query_value(query, "set_code").upper(),
        "collector_number": query_value(query, "collector_number"),
        "type_line": query_value(query, "type_line"),
        "language": query_value(query, "language"),
        "rarity": query_value(query, "rarity").lower(),
        "color_identity": query_value(query, "color_identity").upper(),
        "card_data": query_value(query, "card_data"),
        "quantity_min": query_nonnegative_int(query, "quantity_min"),
        "quantity_max": query_nonnegative_int(query, "quantity_max"),
        "trade_min": query_nonnegative_int(query, "trade_min"),
        "trade_max": query_nonnegative_int(query, "trade_max"),
    }
    if filters["game"] and filters["game"] not in dict(CARD_GAMES):
        filters["game"] = ""
    if filters["quality"] and filters["quality"] not in CONDITION_OPTIONS:
        filters["quality"] = ""
    if filters["finish"] and filters["finish"] not in FINISH_OPTIONS:
        filters["finish"] = ""
    if filters["language"] and filters["language"] not in LANGUAGE_OPTIONS:
        filters["language"] = ""
    if filters["rarity"] and filters["rarity"] not in RARITY_OPTIONS:
        filters["rarity"] = ""
    if filters["color_identity"] and filters["color_identity"] not in dict(COLOR_IDENTITY_OPTIONS):
        filters["color_identity"] = ""
    if filters["card_data"] and filters["card_data"] not in dict(CARD_DATA_FILTER_OPTIONS):
        filters["card_data"] = ""
    return filters


def browse_has_advanced_filters(filters):
    return any(
        filters.get(key) not in ("", None, False)
        for key in (
            "set_name",
            "set_code",
            "collector_number",
            "type_line",
            "language",
            "rarity",
            "color_identity",
            "card_data",
            "quantity_min",
            "quantity_max",
            "trade_min",
            "trade_max",
        )
    )

def trade_picker_filter_values(query, prefix):
    filters = {
        "q": query_value(query, f"{prefix}_q"),
        "game": query_value(query, f"{prefix}_game"),
        "condition": query_value(query, f"{prefix}_condition").upper(),
        "finish": query_value(query, f"{prefix}_finish"),
        "set_name": query_value(query, f"{prefix}_set_name"),
        "set_code": query_value(query, f"{prefix}_set_code").upper(),
        "collector_number": query_value(query, f"{prefix}_collector_number"),
        "type_line": query_value(query, f"{prefix}_type_line"),
        "language": query_value(query, f"{prefix}_language"),
        "rarity": query_value(query, f"{prefix}_rarity").lower(),
        "color_identity": query_value(query, f"{prefix}_color_identity").upper(),
        "card_data": query_value(query, f"{prefix}_card_data"),
        "quantity_min": query_nonnegative_int(query, f"{prefix}_quantity_min"),
        "quantity_max": query_nonnegative_int(query, f"{prefix}_quantity_max"),
        "trade_min": query_nonnegative_int(query, f"{prefix}_trade_min"),
        "trade_max": query_nonnegative_int(query, f"{prefix}_trade_max"),
    }
    if filters["game"] and filters["game"] not in dict(CARD_GAMES):
        filters["game"] = ""
    if filters["condition"] and filters["condition"] not in CONDITION_OPTIONS:
        filters["condition"] = ""
    if filters["finish"] and filters["finish"] not in FINISH_OPTIONS:
        filters["finish"] = ""
    if filters["language"] and filters["language"] not in LANGUAGE_OPTIONS:
        filters["language"] = ""
    if filters["rarity"] and filters["rarity"] not in RARITY_OPTIONS:
        filters["rarity"] = ""
    if filters["color_identity"] and filters["color_identity"] not in dict(COLOR_IDENTITY_OPTIONS):
        filters["color_identity"] = ""
    if filters["card_data"] and filters["card_data"] not in dict(CARD_DATA_FILTER_OPTIONS):
        filters["card_data"] = ""
    return filters


def trade_picker_has_advanced_filters(filters):
    return any(
        filters.get(key) not in ("", None, False)
        for key in (
            "set_name",
            "set_code",
            "collector_number",
            "type_line",
            "language",
            "rarity",
            "color_identity",
            "card_data",
            "quantity_min",
            "quantity_max",
            "trade_min",
            "trade_max",
        )
    )
def trade_picker_datalists(prefix, owner_id, public_only=False):
    datalist_prefix = f"trade-{prefix}"
    return "".join(
        [
            render_datalist(f"{datalist_prefix}-search-suggestions", trade_picker_search_suggestions(owner_id, public_only=public_only)),
            render_datalist(f"{datalist_prefix}-set-name-suggestions", trade_picker_field_suggestions(owner_id, "set_name", public_only=public_only)),
            render_datalist(f"{datalist_prefix}-set-code-suggestions", trade_picker_field_suggestions(owner_id, "set_code", public_only=public_only)),
            render_datalist(f"{datalist_prefix}-collector-number-suggestions", trade_picker_field_suggestions(owner_id, "collector_number", public_only=public_only)),
            render_datalist(f"{datalist_prefix}-type-line-suggestions", trade_picker_field_suggestions(owner_id, "type_line", public_only=public_only)),
        ]
    )
def render_browse(user, query, notice=None, status="info"):
    filters = browse_filter_values(query)
    q = filters["q"]
    game = filters["game"]
    quality = filters["quality"]
    finish = filters["finish"]
    owner_id = filters["owner_id"]
    advanced_active = browse_has_advanced_filters(filters)
    advanced_count = sum(
        1
        for key, value in filters.items()
        if key not in ("q", "game", "quality", "finish", "owner_id") and value not in ("", None, False)
    )
    where, params = browse_where(user["id"], filters)
    current_sort, current_dir = sort_state(query, CARD_SORT_OPTIONS)
    order_clause = sort_order_clause(
        query,
        CARD_SORT_OPTIONS,
        BROWSE_SORT_SQL,
        fallback=("collection_items.card_name COLLATE NOCASE", "users.display_name COLLATE NOCASE", "collection_items.set_name COLLATE NOCASE"),
    )
    total_count = browse_count(where, params)
    page, per_page, page_count, offset = pagination_state(query, total_count)
    items = browse_page_rows(where, params, order_clause, per_page, offset)
    filter_users = browse_filter_users(user["id"])
    owner_labels = {
        owner["id"]: f'{owner["display_name"]} (@{owner["username"]})'
        for owner in filter_users
    }
    owner_options = "".join(
        f'<option value="{owner["id"]}"{selected(str(owner_id), str(owner["id"]))}>{e(owner["display_name"])} (@{e(owner["username"])})</option>'
        for owner in filter_users
    )
    rows_html = "".join(render_browse_row(item) for item in items)
    language_options = simple_option_tags(LANGUAGE_OPTIONS, filters["language"])
    rarity_options = "".join(
        f'<option value="{rarity}"{selected(filters["rarity"], rarity)}>{e(rarity.title())}</option>'
        for rarity in RARITY_OPTIONS
    )
    color_options = option_tags(COLOR_IDENTITY_OPTIONS, filters["color_identity"])
    card_data_options = option_tags(CARD_DATA_FILTER_OPTIONS, filters["card_data"])
    advanced_summary = f"{advanced_count} active" if advanced_count else "More options"
    browse_datalists = "".join(
        [
            render_datalist("browse-search-suggestions", browse_search_suggestions(user["id"])),
            render_datalist("browse-set-name-suggestions", browse_field_suggestions(user["id"], "set_name")),
            render_datalist("browse-set-code-suggestions", browse_field_suggestions(user["id"], "set_code")),
            render_datalist("browse-collector-number-suggestions", browse_field_suggestions(user["id"], "collector_number")),
            render_datalist("browse-type-line-suggestions", browse_field_suggestions(user["id"], "type_line")),
        ]
    )
    table = f"""
    <div class="table-wrap">
        <table class="responsive-card-table browse-table">
            <thead>
                <tr>
                    <th>Card</th>
                    <th>User</th>
                    <th>Game</th>
                    <th>Set</th>
                    <th>Code</th>
                    <th>Available</th>
                    <th>Quality</th>
                    <th></th>
                </tr>
            </thead>
            <tbody>{rows_html}</tbody>
        </table>
    </div>
    """ if items else '<div class="empty-state">No trade cards match those filters.</div>'
    pagination = render_pagination("/browse", query, total_count, page, per_page, page_count)
    active_filter_chips = render_active_filter_chips(
        "/browse",
        query,
        filters,
        browse_filter_chip_specs(owner_labels),
        class_name="browse-active-filters",
    )
    content = f"""
    <section class="section-heading">
        <div>
            <p class="eyebrow">Browse</p>
            <h1>Available trade cards</h1>
        </div>
    </section>
    <form class="filter-bar browse-filter-bar" method="get" action="/browse">
        <div class="filter-primary-row">
            <label class="search-field">Card name
                <input name="q" value="{e(q)}" placeholder="Card name or type" list="browse-search-suggestions">
            </label>
            <label>User
                <select name="user">
                    <option value="">All users</option>
                    {owner_options}
                </select>
            </label>
            <label>Quality
                <select name="quality">
                    <option value="">All qualities</option>
                    {simple_option_tags(CONDITION_OPTIONS, quality)}
                </select>
            </label>
            <label>Game
                <select name="game">
                    <option value="">All games</option>
                    {option_tags(CARD_GAMES, game)}
                </select>
            </label>
            <label>Finish
                <select name="finish">
                    <option value="">All finishes</option>
                    {simple_option_tags(FINISH_OPTIONS, finish)}
                </select>
            </label>
            {render_sort_controls(CARD_SORT_OPTIONS, current_sort, current_dir)}
            <div class="actions filter-actions">
                <button class="button secondary" type="submit">Filter</button>
                <a class="button ghost" href="/browse">Reset</a>
            </div>
        </div>
        <details class="advanced-filter"{" open" if advanced_active else ""}>
            <summary>
                <span>Advanced filters</span>
                <span class="advanced-filter-count">{e(advanced_summary)}</span>
            </summary>
            <div class="advanced-filter-grid">
                <label>Set name
                    <input name="set_name" value="{e(filters["set_name"])}" placeholder="Dominaria Remastered" list="browse-set-name-suggestions">
                </label>
                <label>Set code
                    <input name="set_code" value="{e(filters["set_code"])}" placeholder="DMR" list="browse-set-code-suggestions">
                </label>
                <label>Collector #
                    <input name="collector_number" value="{e(filters["collector_number"])}" placeholder="123" list="browse-collector-number-suggestions">
                </label>
                <label>Type line
                    <input name="type_line" value="{e(filters["type_line"])}" placeholder="Creature, artifact, planeswalker" list="browse-type-line-suggestions">
                </label>
                <label>Language
                    <select name="language">
                        <option value="">Any language</option>
                        {language_options}
                    </select>
                </label>
                <label>Rarity
                    <select name="rarity">
                        <option value="">Any rarity</option>
                        {rarity_options}
                    </select>
                </label>
                <label>Color identity
                    <select name="color_identity">
                        <option value="">Any color identity</option>
                        {color_options}
                    </select>
                </label>
                <label>Card data
                    <select name="card_data">
                        <option value="">Any card data</option>
                        {card_data_options}
                    </select>
                </label>
                <label>Owned qty min
                    <input type="number" min="0" name="quantity_min" value="{e(filters["quantity_min"])}">
                </label>
                <label>Owned qty max
                    <input type="number" min="0" name="quantity_max" value="{e(filters["quantity_max"])}">
                </label>
                <label>Available min
                    <input type="number" min="0" name="trade_min" value="{e(filters["trade_min"])}">
                </label>
                <label>Available max
                    <input type="number" min="0" name="trade_max" value="{e(filters["trade_max"])}">
                </label>
            </div>
        </details>
        {browse_datalists}
    </form>
    {active_filter_chips}
    <section class="panel flush">{table}</section>
    {pagination}
    <script>
        (function () {{
            document.querySelectorAll("[data-photo-dialog]").forEach(function (button) {{
                button.addEventListener("click", function () {{
                    var dialog = document.getElementById(button.dataset.photoDialog);
                    if (dialog && typeof dialog.showModal === "function") dialog.showModal();
                }});
            }});
            document.querySelectorAll(".condition-photo-dialog").forEach(function (dialog) {{
                dialog.addEventListener("click", function (event) {{
                    if (event.target === dialog) dialog.close();
                }});
            }});
        }})();
    </script>
    """
    return render_layout(user, "Browse", content, active="browse", notice=notice, status=status)


def render_browse_row(item):
    thumb = f'<img class="card-thumb" src="{e(item["image_url"])}" alt="">' if item["image_url"] else '<span class="card-thumb placeholder"></span>'
    type_line = f'<span class="subtle">{e(item["type_line"])}</span>' if item["type_line"] else f'<span class="subtle">{e(item["collector_number"])}</span>'
    price = price_pill(item)
    scryfall_link = f'<a class="subtle" href="{e(item["scryfall_uri"])}" target="_blank" rel="noreferrer">Scryfall</a>' if item["scryfall_uri"] else ""
    photo_preview = render_browse_photo_preview(item)
    trade_form = f"""
    <form class="inline-trade-form" method="get" action="/trades/new">
        <input type="hidden" name="recipient_id" value="{e(item["owner_id"])}">
        <label class="mini-input trade-request-quantity">Qty
            <input type="number" min="1" max="{e(item["quantity_for_trade"])}" name="request_{e(item["id"])}" value="1" aria-label="Quantity to request for {e(item["card_name"])}">
        </label>
        <button class="button primary small" type="submit">Propose trade</button>
    </form>
    """
    return f"""
    <tr>
        <td data-label="Card">
            <div class="card-cell">
                {thumb}
                <div><strong>{e(item["card_name"])}</strong>{type_line}{scryfall_link}</div>
            </div>
        </td>
        <td data-label="User">
            <a href="/members/{item["owner_id"]}"><strong>{e(item["owner_name"])}</strong></a>
            <span class="subtle">@{e(item["owner_username"])}</span>
        </td>
        <td data-label="Game">{e(game_label(item["game"]))}</td>
        <td data-label="Set">{e(item["set_name"] or "-")}</td>
        <td data-label="Code">{e(item["set_code"] or "-")}</td>
        <td data-label="Available">{e(item["quantity_for_trade"])}</td>
        <td data-label="Quality"><span class="pill">{e(item["condition"])}</span> <span class="pill">{e(item["finish"])}</span>{price} {visibility_badge(item)} {photo_preview}<span class="subtle">{e(item["language"])}</span>{f'<span class="subtle condition-detail">{e(row_value(item, "condition_notes", ""))}</span>' if row_value(item, "condition_notes", "") else ''}</td>
        <td class="table-actions" data-label="Trade">
            {trade_form}
        </td>
    </tr>
    """


def render_browse_photo_preview(item):
    photo_count = int(row_value(item, "photo_count", 0) or 0)
    if not photo_count:
        return ""
    dialog_id = f'browse-photo-dialog-{int(item["id"])}'
    title_id = f'{dialog_id}-title'
    photo_label = f'{photo_count} photo{"s" if photo_count != 1 else ""}'
    condition_notes = row_value(item, "condition_notes", "")
    return f"""
    <button class="photo-preview-trigger" type="button" data-photo-dialog="{dialog_id}" aria-haspopup="dialog" aria-controls="{dialog_id}">
        View {e(photo_label)}
    </button>
    <dialog class="condition-photo-dialog" id="{dialog_id}" aria-labelledby="{title_id}">
        <div class="condition-photo-dialog-header">
            <div>
                <p class="eyebrow">Condition preview</p>
                <h2 id="{title_id}">{e(item["card_name"])}</h2>
                <p class="muted compact">{e(item["owner_name"])} - {e(item["condition"])} - {e(item["finish"])}</p>
            </div>
            <form method="dialog">
                <button class="button ghost small condition-photo-dialog-close" type="submit" aria-label="Close condition photo preview">Close</button>
            </form>
        </div>
        {f'<p class="condition-detail"><strong>Condition details:</strong> {e(condition_notes)}</p>' if condition_notes else ''}
        {render_collection_photo_gallery(item["id"])}
    </dialog>
    """


def render_collection_row(
    item,
    owner_view=False,
    trade_select=False,
    input_name="items",
    bulk_select=False,
    form_id=None,
    selected_quantity=0,
):
    action = ""
    select_cell = ""
    if owner_view:
        select_cell = f'<td class="select-col" data-label=""><input type="checkbox" name="item_id" value="{e(item["id"])}" aria-label="Select {e(item["card_name"])}"></td>' if bulk_select else ""
        action = f"""
            <td class="table-actions" data-label="Actions">
                <a class="button ghost small" href="/collection/{item["id"]}/edit">Edit</a>
            </td>
        """
    elif trade_select:
        form_attr = f' form="{e(form_id)}"' if form_id else ""
        metadata = f'{game_label(item["game"])} - {item["set_name"] or "Any set"}'
        if item["set_code"]:
            metadata += f' ({item["set_code"]})'
        if item["condition"] or item["finish"]:
            metadata += f' - {item["condition"]} {item["finish"]}'.strip()
        price = trade_item_price_usd(item)
        source = trade_item_price_source(item)
        action = f"""
            <td class="table-actions" data-label="Pick">
                <label class="mini-input">Qty
                    <input type="number" min="0" max="{e(item["quantity_for_trade"])}" name="{input_name}_{item["id"]}" value="{e(selected_quantity)}"{form_attr} data-trade-pick data-side="{e(input_name)}" data-card-name="{e(item["card_name"])}" data-card-meta="{e(metadata)}" data-card-price="{e(price)}" data-price-source="{e(source)}">
                </label>
            </td>
        """
    else:
        action = '<td data-label=""></td>'
    thumb = f'<img class="card-thumb" src="{e(item["image_url"])}" alt="">' if item["image_url"] else '<span class="card-thumb placeholder"></span>'
    type_line = f'<span class="subtle">{e(item["type_line"])}</span>' if item["type_line"] else f'<span class="subtle">{e(item["collector_number"])}</span>'
    price = price_pill(item)
    scryfall_link = f'<a class="subtle" href="{e(item["scryfall_uri"])}" target="_blank" rel="noreferrer">Scryfall</a>' if item["scryfall_uri"] else ""
    return f"""
    <tr>
        {select_cell}
        <td data-label="Card">
            <div class="card-cell">
                {thumb}
                <div><strong>{e(item["card_name"])}</strong>{type_line}{scryfall_link}</div>
            </div>
        </td>
        <td data-label="Game">{e(game_label(item["game"]))}</td>
        <td data-label="Set">{e(item["set_name"] or "-")}</td>
        <td data-label="Code">{e(item["set_code"] or "-")}</td>
        <td data-label="Qty">{e(item["quantity"])}</td>
        <td data-label="Trade">{e(item["quantity_for_trade"])}</td>
        <td data-label="Details"><span class="pill">{e(item["condition"])}</span> <span class="pill">{e(item["finish"])}</span>{price} {visibility_badge(item)} {f'<span class="pill">{e(row_value(item, "photo_count", 0))} photo{"s" if int(row_value(item, "photo_count", 0) or 0) != 1 else ""}</span>' if int(row_value(item, "photo_count", 0) or 0) else ''}<span class="subtle">{e(item["language"])}</span>{f'<span class="subtle condition-detail">{e(row_value(item, "condition_notes", ""))}</span>' if row_value(item, "condition_notes", "") else ''}</td>
        {action}
    </tr>
    """


def render_price_history_panel(user_id, item_id):
    history = price_history_rows(item_id, user_id)
    if history:
        rows_html = "".join(
            f"""
            <tr>
                <td>{e(entry["observed_at"][:16].replace("T", " "))}</td>
                <td>${e(entry["price_usd"])}</td>
                <td>{e("$" + entry["previous_price_usd"] if entry["previous_price_usd"] else "-")}</td>
                <td>{e(signed_price_text(entry["change_amount"]) if entry["change_amount"] else "-")}</td>
                <td>{e((entry["change_percent"] + "%") if entry["change_percent"] else "-")}</td>
            </tr>
            """
            for entry in history
        )
        history_body = f"""
        <div class="table-wrap compact-table-wrap">
            <table class="price-history-table">
                <thead>
                    <tr><th>Observed</th><th>Price</th><th>Previous</th><th>Change</th><th>%</th></tr>
                </thead>
                <tbody>{rows_html}</tbody>
            </table>
        </div>
        """
    else:
        history_body = '<div class="empty-state compact-empty">No Scryfall price observations yet.</div>'
    return f"""
    <section class="panel price-history-panel">
        <div class="panel-heading">
            <h2>Price history</h2>
            <span class="pill">Scryfall</span>
        </div>
        {history_body}
    </section>
    """


def card_photo_size_label(size):
    size = max(0, int(size or 0))
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} bytes"


def render_collection_photo_gallery(collection_item_id, compact=False, editable=False):
    photos = collection_item_photo_rows(collection_item_id)
    if not photos:
        return "" if compact else '<p class="muted compact">No condition photos attached.</p>'
    gallery_class = "card-photo-gallery compact-gallery" if compact else "card-photo-gallery"
    items = "".join(
        f"""
        <article class="card-photo">
            <a href="/collection/photos/{photo["id"]}" target="_blank" rel="noreferrer">
                <img src="/collection/photos/{photo["id"]}" alt="{e(photo["caption"] or photo["original_filename"])}">
            </a>
            <div>
                {f'<strong>{e(photo["caption"])}</strong>' if photo["caption"] else ''}
                <span>{e(card_photo_size_label(photo["file_size"]))}</span>
            </div>
            {f'<form method="post" action="/collection/{collection_item_id}/photos/{photo["id"]}/delete" data-confirm="Delete this card photo?"><button class="button danger small" type="submit">Delete</button></form>' if editable else ''}
        </article>
        """
        for photo in photos
    )
    return f'<div class="{gallery_class}">{items}</div>'


def render_collection_photo_panel(item):
    item_id = item["id"]
    return f"""
    <section class="panel card-photo-panel">
        <div class="panel-heading">
            <div>
                <h2>Condition photos</h2>
                <p class="muted compact">Photos are visible wherever this public card appears and are snapshotted into new trade offers.</p>
            </div>
            <span class="pill">{e(collection_item_photo_count(item_id))}/{e(CARD_PHOTO_MAX_COUNT)}</span>
        </div>
        {render_collection_photo_gallery(item_id, editable=True)}
        <form class="card-photo-upload" method="post" action="/collection/{item_id}/photos" enctype="multipart/form-data">
            <label>Photo
                <input required type="file" name="card_photo" accept="image/png,image/jpeg,image/gif,image/webp">
            </label>
            <label>Caption
                <input name="caption" maxlength="300" placeholder="Front, back, corner wear, crease...">
            </label>
            <button class="button secondary" type="submit">Add photo</button>
        </form>
    </section>
    """


def render_collection_form(
    user,
    item=None,
    notice=None,
    status="info",
    scryfall_results=None,
    scryfall_picker_intent="use_scryfall",
    scryfall_picker_label="Use selected card",
    scryfall_picker_title="Scryfall matches",
):
    try:
        is_edit = item is not None and item["id"] is not None
    except (KeyError, TypeError):
        is_edit = False
    title = "Edit card" if is_edit else "Add card"
    action = f"/collection/{item['id']}/edit" if is_edit else "/collection/new"
    if item is None:
        item = {
            "game": "mtg",
            "card_name": "",
            "set_name": "",
            "set_code": "",
            "collector_number": "",
            "finish": "Regular",
            "condition": "NM",
            "condition_notes": "",
            "language": "English",
            "quantity": 1,
            "quantity_for_trade": 0,
            "price_usd": "",
            "price_source": "",
            "tcgplayer_product_id": "",
            "cardmarket_product_id": "",
            "cardkingdom_sku": "",
            "notes": "",
            "is_public": 1,
            "scryfall_finish_override": "",
        }
    else:
        item = dict(item)
    for field in SCRYFALL_COLLECTION_FIELDS:
        item.setdefault(field, "")
    for field in PRICE_PROVIDER_ID_FIELDS.values():
        item.setdefault(field, "")
    item.setdefault("price_source", "scryfall" if item.get("price_usd") and (item.get("scryfall_id") or item.get("scryfall_uri")) else "")
    item.setdefault("condition_notes", "")
    item.setdefault("lookup_on_save", "1")
    item.setdefault("scryfall_finish_override", "")
    item.setdefault("is_public", 1)
    hidden_scryfall_fields = "".join(
        f'<input type="hidden" name="{field}" value="{e(item[field])}">'
        for field in SCRYFALL_COLLECTION_FIELDS
    )
    preview = render_scryfall_preview(item)
    result_options = render_scryfall_result_picker(
        scryfall_results,
        button_label=scryfall_picker_label,
        intent=scryfall_picker_intent,
        title=scryfall_picker_title,
    )
    price_history_panel = render_price_history_panel(user["id"], item["id"]) if is_edit else ""
    photo_panel = render_collection_photo_panel(item) if is_edit else ""
    content = f"""
    {render_cards_subnav("collection")}
    <section class="section-heading">
        <div>
            <p class="eyebrow">My Cards</p>
            <h1>{title}</h1>
        </div>
        <a class="button secondary" href="/collection">Back</a>
    </section>
    <form class="panel form-grid" method="post" action="{action}">
        {hidden_scryfall_fields}
        <label>Card name
            <input required name="card_name" value="{e(item["card_name"])}" maxlength="160" autofocus>
        </label>
        <label>Game
            <select name="game">{option_tags(CARD_GAMES, item["game"])}</select>
        </label>
        <label>Set
            <input name="set_name" value="{e(item["set_name"])}" maxlength="120">
        </label>
        <label>Set code
            <input name="set_code" value="{e(item["set_code"])}" maxlength="20">
        </label>
        <label>Collector number
            <input name="collector_number" value="{e(item["collector_number"])}" maxlength="40">
        </label>
            <label>Finish
            <select name="finish">
                {simple_option_tags(FINISH_OPTIONS, item["finish"])}
            </select>
            </label>
        <label>Condition
            <select name="condition">{simple_option_tags(CONDITION_OPTIONS, item["condition"])}</select>
        </label>
        <label class="span-2">Condition details
            <textarea name="condition_notes" rows="3" maxlength="1000" placeholder="Describe whitening, scratches, bends, signatures, altered art, or other notable details">{e(item["condition_notes"])}</textarea>
        </label>
        <label>Language
            <select name="language">{simple_option_tags(LANGUAGE_OPTIONS, item["language"])}</select>
        </label>
        <label>Quantity owned
            <input required type="number" min="0" name="quantity" value="{e(item["quantity"])}">
        </label>
        <label>Quantity for trade
            <input required type="number" min="0" name="quantity_for_trade" value="{e(item["quantity_for_trade"])}">
        </label>
        <label class="span-2">Notes
            <textarea name="notes" rows="4" maxlength="1000">{e(item["notes"])}</textarea>
        </label>
        {visibility_checkbox(item)}
        {preview}
        {result_options}
        <div class="lookup-controls span-2">
            <label class="checkbox-line">
                <input type="checkbox" name="lookup_on_save" value="1"{checked(item["lookup_on_save"] == "1")}>
                Scryfall lookup on save
            </label>
            <label class="checkbox-line">
                <input type="checkbox" name="scryfall_finish_override" value="1"{checked(item["scryfall_finish_override"] == "1")}>
                Override Scryfall finish check
            </label>
            <button class="button secondary" name="intent" value="lookup" type="submit">Search Scryfall</button>
        </div>
        <div class="form-actions span-2">
            <button class="button primary" name="intent" value="save" type="submit">Save card</button>
        </div>
    </form>
    {photo_panel}
    {price_history_panel}
    """
    return render_layout(user, title, content, active="cards", notice=notice, status=status)


def render_csv_import_mapping_preset_options(user, import_target="collection", selected_id=0):
    try:
        selected_id = int(selected_id or 0)
    except (TypeError, ValueError):
        selected_id = 0
    options = ['<option value="0">Built-in column matching</option>']
    for preset in csv_import_preset_rows_for_user(user["id"], import_target):
        selected = " selected" if int(preset["id"]) == selected_id else ""
        options.append(f'<option value="{preset["id"]}"{selected}>{e(csv_import_preset_display_name(preset))}</option>')
    return "".join(options)


def render_csv_import_mapping_fields():
    placeholders = {
        "name": "Card, Title, Card Name",
        "quantity": "Qty, Count, Owned",
        "trade": "Trade Qty, For Trade, Available",
        "game": "Game, TCG",
        "set_name": "Set, Expansion",
        "set_code": "Set Code, Edition Code",
        "collector_number": "Collector #, Number",
        "finish": "Foil, Finish, Printing",
        "condition": "Condition, Quality",
        "condition_notes": "Condition Notes, Condition Details",
        "language": "Language, Lang",
        "scryfall_id": "Scryfall ID",
        "tcgplayer_product_id": "TCGplayer ID",
        "cardmarket_product_id": "Cardmarket ID",
        "cardkingdom_sku": "Card Kingdom SKU",
        "notes": "Notes",
        "section": "Section, Board, Category",
    }
    return "".join(
        f"""
        <label>{e(label)}
            <input name="map_{e(key)}" placeholder="{e(placeholders.get(key, label))}">
        </label>
        """
        for key, label in CSV_IMPORT_MAPPING_FIELDS
    )


def render_csv_import_mapping_preset_row(user, preset):
    mapping = csv_import_mapping_from_json(preset["mapping_json"])
    mapped_fields = ", ".join(label for key, label in CSV_IMPORT_MAPPING_FIELDS if key in mapping)
    owner_or_scope = "Shared" if row_value(preset, "is_shared", 0) else "Personal"
    can_delete = int(preset["user_id"]) == int(user["id"]) or bool(user["is_admin"])
    delete_form = ""
    if can_delete:
        delete_form = f"""
        <form method="post" action="/import/presets/{preset["id"]}/delete">
            <button class="button danger small" type="submit" onclick="return confirm('Delete this mapping preset?')">Delete</button>
        </form>
        """
    return f"""
    <li class="csv-preset-row">
        <div>
            <strong>{e(preset["name"])}</strong>
            <span class="muted">{e(owner_or_scope)} {e("deck" if preset["import_target"] == "deck" else "collection")} preset</span>
            <small>{e(mapped_fields or "No mapped fields")}</small>
        </div>
        {delete_form}
    </li>
    """


def render_csv_import_mapping_presets(user):
    presets = [
        *csv_import_preset_rows_for_user(user["id"], "collection"),
        *csv_import_preset_rows_for_user(user["id"], "deck"),
    ]
    preset_rows = "".join(render_csv_import_mapping_preset_row(user, preset) for preset in presets)
    if not preset_rows:
        preset_rows = '<li class="empty-list">No saved mapping presets yet.</li>'
    shared_checkbox = ""
    if user["is_admin"]:
        shared_checkbox = """
        <label class="checkbox-line">
            <input type="checkbox" name="is_shared" value="1">
            Share with all users
        </label>
        """
    return f"""
    <section class="panel csv-mapping-presets">
        <div class="panel-heading">
            <div>
                <h2>CSV mapping presets</h2>
                <p class="muted compact">Save header mappings for collection apps, deck builders, or custom spreadsheets. Separate multiple possible headers with commas.</p>
            </div>
        </div>
        <form class="form-grid csv-mapping-form" method="post" action="/import/presets">
            <label>Preset name
                <input required name="name" maxlength="80" placeholder="Local store export">
            </label>
            <label>Use for
                <select name="import_target">
                    <option value="collection">Collection imports</option>
                    <option value="deck">Deck CSV imports</option>
                </select>
            </label>
            {shared_checkbox}
            <div class="csv-mapping-grid span-2">
                {render_csv_import_mapping_fields()}
            </div>
            <div class="form-actions span-2">
                <button class="button primary" type="submit">Save preset</button>
            </div>
        </form>
        <div class="panel-heading with-gap">
            <h2>Saved presets</h2>
        </div>
        <ul class="stack-list csv-preset-list">{preset_rows}</ul>
    </section>
    """


def render_import(user, result=None, preview=None, notice=None, status="info"):
    bulk_status = scryfall_bulk_status()
    queue_stats = scryfall_enrichment_stats()
    price_refresh_status = scryfall_price_refresh_status()
    result_html = ""
    if result:
        warning_block = render_import_warning_block(result)
        undo_button = ""
        if result.get("batch_id"):
            undo_button = f"""
            <form method="post" action="/imports/{result["batch_id"]}/undo">
                <input type="hidden" name="redirect_to" value="/import">
                <button class="button danger small" type="submit" onclick="return confirm('Undo this import batch? Imported collection changes from the batch will be reverted.')">Undo import</button>
            </form>
            """
        result_html = f"""
        <section class="panel import-result">
            <div class="panel-heading">
                <h2>Import result</h2>
                <div class="inline-actions">
                    {undo_button}
                    <a href="/collection">Open collection</a>
                </div>
            </div>
            <div class="metric-grid compact-metrics">
                <article class="metric"><span>{e(result["inserted"])}</span><p>Inserted</p></article>
                <article class="metric"><span>{e(result["updated"])}</span><p>Updated</p></article>
                <article class="metric"><span>{e(result["enriched"])}</span><p>Enriched</p></article>
                <article class="metric"><span>{e(result["queued"])}</span><p>Queued</p></article>
                <article class="metric"><span>{e(result.get("skipped", 0))}</span><p>Skipped</p></article>
            </div>
            {warning_block}
        </section>
        """
    preview_html = render_collection_import_preview(preview)
    recent_batch_rows = render_import_batch_list(recent_import_batches(user["id"], "collection_csv"), "/import")
    bulk_updated = bulk_status["updated_at"][:10] if bulk_status["updated_at"] else "Not synced"
    price_refresh_updated = price_refresh_status["updated_at"][:16].replace("T", " ") if price_refresh_status["updated_at"] else "Not run yet"
    auto_price_detail = (
        f"{price_refresh_status['status'].title()}, every {price_refresh_status['interval_hours']} hours"
        if price_refresh_status["auto"]
        else "Disabled"
    )
    queue_total = queue_stats["pending"] + queue_stats["processing"]
    queue_detail = f'{queue_total} queued, {queue_stats["done"]} done, {queue_stats["failed"] + queue_stats["not_found"]} need review'
    bulk_error = f'<p class="notice error compact">{e(bulk_status["error"])}</p>' if bulk_status["error"] else ""
    price_refresh_error = f'<p class="notice error compact">{e(price_refresh_status["error"])}</p>' if price_refresh_status["error"] else ""
    content = f"""
    {render_cards_subnav("import")}
    <section class="section-heading">
        <div>
            <p class="eyebrow">My Cards</p>
            <h1>Bring in cards</h1>
        </div>
        <a class="button secondary" href="/collection">Collection</a>
    </section>
    <section class="content-grid import-grid">
        <form class="panel form-grid" method="post" action="/import" enctype="multipart/form-data">
            <label>Source
                <select name="source">{option_tags(CSV_SOURCE_OPTIONS, "auto")}</select>
            </label>
            <label>Default game
                <select name="game">{option_tags(CARD_GAMES, "mtg")}</select>
            </label>
            <label class="span-2">Mapping preset
                <select name="mapping_preset_id">{render_csv_import_mapping_preset_options(user, "collection")}</select>
            </label>
            <label class="span-2">CSV file
                <input required type="file" name="csv_file" accept=".csv,text/csv">
            </label>
            <label>Default trade qty
                <input type="number" min="0" name="default_trade_quantity" value="0">
            </label>
            <div class="toggle-stack">
                <label class="checkbox-line">
                    <input type="checkbox" name="enrich_scryfall" value="1"{checked(True)}>
                    Scryfall lookup
                </label>
                <label class="checkbox-line">
                    <input type="checkbox" name="merge_duplicates" value="1"{checked(True)}>
                    Merge duplicates
                </label>
                <label class="checkbox-line">
                    <input type="checkbox" name="scryfall_finish_override" value="1">
                    Allow Scryfall finish mismatches
                </label>
            </div>
            <div class="form-actions span-2">
                <button class="button primary" name="intent" value="preview" type="submit">Preview CSV</button>
                <button class="button secondary" name="intent" value="import_now" type="submit" onclick="return confirm('Import without preview?')">Import now</button>
            </div>
        </form>
        <article class="panel import-notes">
            <div class="panel-heading">
                <h2>Scryfall local data</h2>
                <span class="status {"pending" if bulk_status["status"] == "running" else "accepted" if bulk_status["card_count"] else "declined"}">{e(bulk_status["status"].title())}</span>
            </div>
            <div class="import-status-list">
                <p><strong>{e(bulk_status["card_count"])}</strong> cached cards</p>
                <p>Updated: {e(bulk_updated)}</p>
                <p>Background enrichment: {e(queue_detail)}</p>
                <p>Auto prices: {e(auto_price_detail)}</p>
                <p>Price refresh: {e(price_refresh_updated)}</p>
            </div>
            {bulk_error}
            {price_refresh_error}
            <p class="muted compact">Scryfall card data and prices update automatically in the background.</p>
            <div class="panel-heading with-gap">
                <h2>Columns</h2>
            </div>
            <div class="column-list">
                <span>Name</span>
                <span>Quantity</span>
                <span>Set</span>
                <span>Set code</span>
                <span>Collector number</span>
                <span>Foil</span>
                <span>Condition</span>
                <span>Language</span>
                <span>Scryfall ID</span>
            </div>
        </article>
    </section>
    {render_csv_import_mapping_presets(user)}
    {preview_html}
    {result_html}
    <section class="panel import-history">
        <div class="panel-heading">
            <h2>Recent collection imports</h2>
        </div>
        <ul class="stack-list compact-stack">{recent_batch_rows}</ul>
    </section>
    """
    return render_layout(user, "Import", content, active="cards", notice=notice, status=status)


__all__ = ['query_value', 'query_nonnegative_int', 'collection_filters', 'collection_filter_values', 'collection_has_advanced_filters', 'collection_hidden_filter_inputs', 'CARD_SORT_OPTIONS', 'WANT_SORT_OPTIONS', 'GROUP_COLLECTION_SORT_SQL', 'GROUP_WANT_SORT_SQL', 'sort_state', 'sort_order_clause', 'render_sort_controls', 'render_sort_bar', 'collection_where', 'query_int', 'pagination_state', 'page_url', 'current_collection_url', 'render_pagination', 'pagination_hidden_inputs', 'render_cleanup_group_items', 'render_duplicate_cleanup_panel', 'render_cleanup', 'render_audit_issue_badges', 'render_audit_value', 'render_condition_finish_audit_row', 'render_condition_finish_audit', 'render_collection', 'stat_percent_text', 'render_stat_breakdown', 'render_collection_top_value', 'render_group_count_summary', 'render_collection_statistics', 'browse_filters', 'browse_filter_values', 'browse_has_advanced_filters', 'browse_where', 'browse_filter_users', 'TRADE_PICKER_FILTER_KEYS', 'trade_picker_filter_values', 'trade_picker_has_advanced_filters', 'trade_picker_where', 'trade_picker_pagination_state', 'trade_picker_url', 'trade_picker_preserved_inputs', 'trade_picker_datalists', 'render_trade_picker_pagination', 'render_browse', 'render_browse_row', 'render_browse_photo_preview', 'render_collection_row', 'render_price_history_panel', 'card_photo_size_label', 'render_collection_photo_gallery', 'render_collection_photo_panel', 'render_collection_form', 'render_import']
