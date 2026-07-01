"""Deck, binder, wishlist group, and public group views.

This module is wired by binderbridge.views; shared app helpers are injected at runtime.
"""

def group_count_label(group):
    if group["group_type"] == "wishlist":
        count = group["want_entries"]
        return f"{count} wanted card{'s' if count != 1 else ''}"
    entries = group["collection_entries"]
    quantity = group["collection_quantity"]
    return f"{quantity} card{'s' if quantity != 1 else ''} across {entries} entr{'ies' if entries != 1 else 'y'}"


def record_is_public(record):
    return record_visibility(record) == VISIBILITY_MEMBERS


def visibility_badge(record):
    visibility = record_visibility(record)
    css_class = "accepted" if visibility == VISIBILITY_MEMBERS else "pending" if visibility == VISIBILITY_TRUSTED else "declined" if visibility == VISIBILITY_PRIVATE else ""
    return f'<span class="status {css_class}">{e(visibility_label(record))}</span>'


def visibility_checkbox(record, name="visibility"):
    current = record_visibility(record)
    options = "".join(
        f'<option value="{e(value)}"{selected(current, value)}>{e(label)}</option>'
        for value, label in VISIBILITY_OPTIONS
    )
    return f"""
    <label class="span-2">Visibility
        <select name="{e(name)}">
            {options}
        </select>
    </label>
    """


def render_group_card(group):
    description = f'<p class="muted compact">{e(group["description"])}</p>' if group["description"] else ""
    return f"""
    <article class="group-card">
        <div>
            <span class="pill">{e(group_type_label(group["group_type"]))}</span>
            {visibility_badge(group)}
            <h2><a href="/groups/{group["id"]}">{e(group["name"])}</a></h2>
            <span class="subtle">{e(group_count_label(group))}</span>
            {description}
        </div>
        <div class="actions">
            <a class="button secondary small" href="/groups/{group["id"]}">Open</a>
            <form method="post" action="/groups/{group["id"]}/delete">
                <button class="button danger small" type="submit" data-confirm="Delete this group? Cards stay in your collection and wants.">Delete</button>
            </form>
        </div>
    </article>
    """


def normalize_group_view(value):
    return "wishlist" if str(value or "").strip().lower() == "wishlist" else "cards"


def render_groups(user, notice=None, status="info", view="cards"):
    view = normalize_group_view(view)
    groups = group_summary_rows(user["id"])
    if view == "wishlist":
        group_options = [("wishlist", "Wishlist")]
        default_group_type = "wishlist"
        title = "Wishlist groups"
        eyebrow = "Wishlist"
        heading = "Organize wanted cards"
        subnav = render_wishlist_subnav("groups")
        create_type_control = '<input type="hidden" name="group_type" value="wishlist">'
        create_copy = "Priority upgrades, Commander needs, Budget targets"
        layout_active = "wants"
    else:
        group_options = [("deck", "Deck"), ("binder", "Binder")]
        default_group_type = "deck"
        title = "Decks & Binders"
        eyebrow = "My Cards"
        heading = "Decks and binders"
        subnav = render_cards_subnav("groups")
        create_type_control = f"""
            <label>Type
                <select name="group_type">{option_tags(group_options, default_group_type)}</select>
            </label>
        """
        create_copy = "Commander deck, Trade binder, Cube box"
        layout_active = "cards"
    group_sections = []
    for group_type, label in group_options:
        type_groups = [group for group in groups if group["group_type"] == group_type]
        cards = "".join(render_group_card(group) for group in type_groups) or render_empty_action_state(
            f"No {label.lower()} groups yet.",
            "Create one with the form on this page when you are ready to organize cards.",
            actions=(("#create-group", "Create group", "secondary"),),
        )
        group_sections.append(
            f"""
            <section class="panel group-section">
                <div class="panel-heading">
                    <h2>{e(label)}s</h2>
                    <span class="pill">{e(len(type_groups))}</span>
                </div>
                <div class="group-list">{cards}</div>
            </section>
            """
        )
    content = f"""
    {subnav}
    <section class="section-heading">
        <div>
            <p class="eyebrow">{e(eyebrow)}</p>
            <h1>{e(heading)}</h1>
        </div>
    </section>
    <section class="content-grid group-layout">
        <form class="panel form-grid compact-form" id="create-group" method="post" action="/groups">
            <input type="hidden" name="group_view" value="{e(view)}">
            <div class="span-2 panel-heading">
                <h2>Create group</h2>
            </div>
            {create_type_control}
            <label>Name
                <input required name="name" maxlength="80" placeholder="{e(create_copy)}">
            </label>
            <label class="span-2">Notes
                <textarea name="description" rows="4" maxlength="1000"></textarea>
            </label>
            {visibility_checkbox({"visibility": VISIBILITY_MEMBERS})}
            <div class="form-actions span-2">
                <button class="button primary" type="submit">Create group</button>
            </div>
        </form>
        <div class="group-section-stack">
            {''.join(group_sections)}
        </div>
    </section>
    """
    return render_layout(user, title, content, active=layout_active, notice=notice, status=status)


def collection_item_option_tags(user_id):
    items = rows(
        """
        SELECT *
        FROM collection_items
        WHERE user_id = ?
        ORDER BY card_name COLLATE NOCASE, set_name COLLATE NOCASE
        """,
        (user_id,),
    )
    return "".join(
        f'<option value="{item["id"]}">{e(compact_card_label(item))} - {e(item["quantity"])} owned</option>'
        for item in items
    )


def want_item_option_tags(user_id):
    wants = rows(
        """
        SELECT *
        FROM want_items
        WHERE user_id = ?
        ORDER BY card_name COLLATE NOCASE, set_name COLLATE NOCASE
        """,
        (user_id,),
    )
    return "".join(
        f'<option value="{want["id"]}">{e(want["card_name"])} - want {e(want["desired_quantity"])}{e(" - " + want["set_name"] if want["set_name"] else "")}</option>'
        for want in wants
    )


def group_item_filter_values(query, wishlist=False):
    filters = {
        "q": query_value(query, "q"),
        "game": query_value(query, "game"),
        "condition": query_value(query, "condition"),
        "finish": query_value(query, "finish"),
        "priority": query_value(query, "priority") if wishlist else "",
    }
    if filters["game"] and filters["game"] not in dict(CARD_GAMES):
        filters["game"] = ""
    if filters["condition"] and filters["condition"] not in CONDITION_OPTIONS:
        filters["condition"] = ""
    if filters["finish"] and filters["finish"] not in FINISH_OPTIONS:
        filters["finish"] = ""
    if filters["priority"] and filters["priority"] not in WANT_PRIORITY_LABELS:
        filters["priority"] = ""
    return filters


def group_item_hidden_filter_inputs(filters):
    inputs = []
    for key in ("q", "game", "condition", "finish", "priority"):
        value = (filters or {}).get(key)
        if value in ("", None, False):
            continue
        inputs.append(f'<input type="hidden" name="{e(key)}" value="{e(value)}">')
    return "".join(inputs)


def group_item_filter_chip_specs(wishlist=False):
    specs = [
        {"key": "q", "label": "Search"},
        {"key": "game", "label": "Game", "formatter": game_label},
        {"key": "condition", "label": "Condition"},
        {"key": "finish", "label": "Finish"},
    ]
    if wishlist:
        specs.append({"key": "priority", "label": "Priority", "formatter": want_priority_label})
    return tuple(specs)


def render_group_item_controls(group, query, filters, current_sort, current_dir):
    wishlist = group["group_type"] == "wishlist"
    priority_control = (
        f'<label>Priority<select name="priority"><option value="">All priorities</option>{option_tags(WANT_PRIORITY_OPTIONS, filters["priority"])}</select></label>'
        if wishlist
        else ""
    )
    suggestion_values = want_search_suggestions(group["user_id"]) if wishlist else collection_search_suggestions(group["user_id"])
    path = f'/groups/{group["id"]}'
    return f"""
    <form class="filter-bar group-item-filter-bar" method="get" action="{path}">
        <div class="filter-primary-row">
            <label class="search-field">Search group
                <input name="q" value="{e(filters["q"])}" placeholder="Card name, type, or set" list="group-item-search-suggestions">
            </label>
            <label>Game<select name="game"><option value="">All games</option>{option_tags(CARD_GAMES, filters["game"])}</select></label>
            <label>Condition<select name="condition"><option value="">All conditions</option>{simple_option_tags(CONDITION_OPTIONS, filters["condition"])}</select></label>
            <label>Finish<select name="finish"><option value="">All finishes</option>{simple_option_tags(FINISH_OPTIONS, filters["finish"])}</select></label>
            {priority_control}
        </div>
        <div class="group-filter-sort-row">
            {render_sort_controls(WANT_SORT_OPTIONS if wishlist else CARD_SORT_OPTIONS, current_sort, current_dir)}
            <div class="actions filter-actions">
                <button class="button primary" type="submit">Apply</button>
                <a class="button ghost" href="{path}#group-cards">Clear</a>
            </div>
        </div>
        {render_datalist("group-item-search-suggestions", suggestion_values)}
    </form>
    {render_active_filter_chips(path, query, filters, group_item_filter_chip_specs(wishlist), class_name="group-filter-chips")}
    """


def render_group_collection_items(group, items, controls="", pagination="", total_count=0, redirect_to="", filters=None):
    bulk_disabled = "" if items else " disabled"
    matching_disabled = "" if int(total_count or 0) else " disabled"
    hidden_filters = group_item_hidden_filter_inputs(filters)
    if items:
        rendered = "".join(
            f"""
            <li class="group-item">
                <label class="group-item-select"><input type="checkbox" name="group_item_id" value="{e(item["group_item_id"])}"><span class="sr-only">Select {e(item["card_name"])}</span></label>
                <div class="card-cell">
                    {f'<img class="card-thumb" src="{e(item["image_url"])}" alt="">' if item["image_url"] else '<span class="card-thumb placeholder"></span>'}
                    <div>
                        <strong>{e(item["group_quantity"])} x {e(item["card_name"])}</strong>
                        <span>{e(item["set_name"] or "Any set")} - {e(item["condition"])} - {e(item["finish"])}</span>
                        {f'<span class="subtle condition-detail">{e(row_value(item, "condition_notes", ""))}</span>' if row_value(item, "condition_notes", "") else ''}
                        {render_collection_photo_gallery(item["id"], compact=True)}
                        <span class="subtle">{e(item["quantity"])} owned{price_pill(item)}</span>
                    </div>
                </div>
                <button class="button ghost small" type="submit" formaction="/groups/{group["id"]}/items/{item["group_item_id"]}/delete">Remove</button>
            </li>
            """
            for item in items
        )
    else:
        rendered = render_empty_action_state(
            "No cards added yet.",
            "Use the add form below to place collection cards in this group.",
            tag="li",
        )
    options = collection_item_option_tags(group["user_id"])
    add_form = (
        f"""
        <form class="group-add-form" method="post" action="/groups/{group["id"]}/add">
            <label>Collection card
                <select name="collection_item_id">{options}</select>
            </label>
            <label>Quantity
                <input name="quantity" type="number" min="1" value="1">
            </label>
            <button class="button primary" type="submit">Add card</button>
        </form>
        """
        if options
        else render_empty_action_state(
            "Add cards to your collection before filling this group.",
            actions=(("/collection/new", "Add card", "secondary"), ("/import", "Import cards", "ghost")),
        )
    )
    return f"""
    <section class="panel">
        <div class="panel-heading">
            <h2>{e(group_type_label(group["group_type"]))} cards</h2>
            <span class="pill">{e(total_count)} matching</span>
        </div>
        {controls}
        <form method="post" action="/groups/{group["id"]}/items/bulk-delete">
            <input type="hidden" name="redirect_to" value="{e(redirect_to)}">
            {hidden_filters}
            <div class="group-bulk-bar">
                <div class="group-bulk-controls">
                    <label class="select-all-control"><input type="checkbox"{bulk_disabled} onclick="this.form.querySelectorAll('input[name=group_item_id]').forEach((box) => box.checked = this.checked)"><span>Select page</span></label>
                    <label class="group-bulk-quantity">Group qty
                        <input type="number" min="1" name="group_quantity" placeholder="Quantity">
                    </label>
                </div>
                <div class="actions group-bulk-actions">
                    <button class="button secondary small" type="submit" formaction="/groups/{group["id"]}/items/bulk-update"{bulk_disabled}>Update selected</button>
                    <button class="button secondary small" type="submit" formaction="/groups/{group["id"]}/items/update-all"{matching_disabled} data-confirm="Update all {e(total_count)} cards matching the current filters?">Update all matching</button>
                    <button class="button danger small" type="submit" formaction="/groups/{group["id"]}/items/bulk-delete"{bulk_disabled} data-confirm="Remove selected cards from this group? Source collection cards will remain.">Remove selected</button>
                    <button class="button danger small" type="submit" formaction="/groups/{group["id"]}/items/delete-all"{matching_disabled} data-confirm="Remove all {e(total_count)} cards matching the current filters from this group? Source collection cards will remain.">Remove all matching</button>
                </div>
            </div>
            <ul class="group-item-list selectable-group-list">{rendered}</ul>
        </form>
        {pagination}
        {add_form}
    </section>
    """


def render_group_want_items(group, wants, controls="", pagination="", total_count=0, redirect_to="", filters=None):
    bulk_disabled = "" if wants else " disabled"
    matching_disabled = "" if int(total_count or 0) else " disabled"
    hidden_filters = group_item_hidden_filter_inputs(filters)
    if wants:
        rows = []
        for want in wants:
            preferences = []
            if row_value(want, "condition", ""):
                preferences.append(f"{row_value(want, 'condition')} condition")
            if row_value(want, "finish", ""):
                preferences.append(row_value(want, "finish"))
            if row_value(want, "language", ""):
                preferences.append(row_value(want, "language"))
            preference_text = f"Prefers {', '.join(preferences)}" if preferences else "Any condition, finish, or language"
            budget_cap = normalize_price_usd(row_value(want, "budget_cap_usd", ""))
            budget_text = f" - up to ${budget_cap} each" if budget_cap else ""
            printing_note = f'<span class="subtle"><strong>Preferred printing:</strong> {e(row_value(want, "preferred_printing_notes", ""))}</span>' if row_value(want, "preferred_printing_notes", "") else ""
            rows.append(f"""
            <li class="group-item">
                <label class="group-item-select"><input type="checkbox" name="group_item_id" value="{e(want["group_item_id"])}"><span class="sr-only">Select {e(want["card_name"])}</span></label>
                <div>
                    <strong>{e(want["card_name"])}</strong>
                    <span>{e(want["set_name"] or "Any set")} - want {e(want["desired_quantity"])}</span>
                    <span class="subtle">{e(want["type_line"] or "Any printing")}{price_pill(want)}</span>
                    <span class="subtle">{e(want_priority_label(row_value(want, "priority", "normal")))} priority{e(budget_text)} - {e(preference_text)}</span>
                    {printing_note}
                </div>
                <button class="button ghost small" type="submit" formaction="/groups/{group["id"]}/items/{want["group_item_id"]}/delete">Remove</button>
            </li>
            """)
        rendered = "".join(rows)
    else:
        rendered = render_empty_action_state(
            "No wanted cards added yet.",
            "Use the add form below to place wishlist cards in this group.",
            tag="li",
        )
    options = want_item_option_tags(group["user_id"])
    add_form = (
        f"""
        <form class="group-add-form" method="post" action="/groups/{group["id"]}/add">
            <label>Wanted card
                <select name="want_item_id">{options}</select>
            </label>
            <button class="button primary" type="submit">Add want</button>
        </form>
        """
        if options
        else render_empty_action_state(
            "Add cards to your want list before filling this wishlist.",
            actions=(("/wants#add-want", "Add wanted card", "secondary"),),
        )
    )
    return f"""
    <section class="panel">
        <div class="panel-heading">
            <h2>Wishlist cards</h2>
            <span class="pill">{e(total_count)} matching</span>
        </div>
        {controls}
        <form method="post" action="/groups/{group["id"]}/items/bulk-delete">
            <input type="hidden" name="redirect_to" value="{e(redirect_to)}">
            {hidden_filters}
            <div class="group-bulk-bar">
                <div class="group-bulk-controls">
                    <label class="select-all-control"><input type="checkbox"{bulk_disabled} onclick="this.form.querySelectorAll('input[name=group_item_id]').forEach((box) => box.checked = this.checked)"><span>Select page</span></label>
                </div>
                <div class="actions group-bulk-actions">
                    <button class="button danger small" type="submit" formaction="/groups/{group["id"]}/items/bulk-delete"{bulk_disabled} data-confirm="Remove selected wanted cards from this group? Your source wishlist will remain.">Remove selected</button>
                    <button class="button danger small" type="submit" formaction="/groups/{group["id"]}/items/delete-all"{matching_disabled} data-confirm="Remove all {e(total_count)} wanted cards matching the current filters from this group? Your source wishlist will remain.">Remove all matching</button>
                </div>
            </div>
            <ul class="group-item-list selectable-group-list">{rendered}</ul>
        </form>
        {pagination}
        {add_form}
    </section>
    """


def render_group_import_result(result):
    if not result:
        return ""
    warnings = "".join(f"<li>{e(warning)}</li>" for warning in result["warnings"])
    hidden_warnings = result["warning_count"] - len(result["warnings"])
    if hidden_warnings > 0:
        warnings += f"<li>{hidden_warnings} more warnings not shown.</li>"
    warning_block = f"""
    <div class="import-warnings">
        <h3>Warnings</h3>
        <ul>{warnings}</ul>
    </div>
    """ if warnings else ""
    matched = int(result.get("matched", result.get("grouped", 0)) or 0)
    missing = int(result.get("missing", 0) or 0)
    return f"""
    <div class="deck-import-result">
        <div class="metric-grid compact-metrics">
            <article class="metric"><span>{e(result["grouped"])}</span><p>Grouped</p></article>
            <article class="metric"><span>{e(matched)}</span><p>Owned matched</p></article>
            <article class="metric"><span>{e(missing)}</span><p>Missing</p></article>
            <article class="metric"><span>{e(result["enriched"])}</span><p>Enriched</p></article>
            <article class="metric"><span>{e(result["queued"])}</span><p>Queued</p></article>
        </div>
        {warning_block}
    </div>
    """


def render_import_warning_block(result):
    warnings = "".join(f"<li>{e(warning)}</li>" for warning in result.get("warnings", []))
    hidden_warnings = int(result.get("warning_count", 0) or 0) - len(result.get("warnings", []))
    if hidden_warnings > 0:
        warnings += f"<li>{hidden_warnings} more warnings not shown.</li>"
    return f"""
    <div class="import-warnings">
        <h3>Warnings</h3>
        <ul>{warnings}</ul>
    </div>
    """ if warnings else ""


def render_import_preview_rows(preview):
    preview_rows = preview.get("rows", []) if preview else []
    if not preview_rows:
        return '<div class="empty-state compact-empty">No sample rows to show.</div>'
    rows_html = "".join(
        f"""
        <tr>
            <td data-label="Action"><span class="pill">{e(item.get("action", ""))}</span></td>
            <td data-label="Card">
                <strong>{e(item.get("card_name", ""))}</strong>
                <span class="subtle">{e(item.get("set_name", "") or "Any set")} {e("(" + item.get("set_code", "") + ")" if item.get("set_code") else "")}</span>
            </td>
            <td data-label="Qty">{e(item.get("quantity", 0))}</td>
            <td data-label="Trade">{e(item.get("quantity_for_trade", 0))}</td>
            <td data-label="Quality">{e(item.get("condition", ""))} {e(item.get("finish", ""))}</td>
            <td data-label="Note">{e(item.get("note", ""))}</td>
        </tr>
        """
        for item in preview_rows
    )
    return f"""
    <div class="table-wrap import-preview-table">
        <table class="responsive-card-table import-preview-card-table">
            <thead>
                <tr>
                    <th>Action</th>
                    <th>Card</th>
                    <th>Qty</th>
                    <th>Trade</th>
                    <th>Quality</th>
                    <th>Note</th>
                </tr>
            </thead>
            <tbody>{rows_html}</tbody>
        </table>
    </div>
    """


def render_import_batch_list(batches, redirect_to):
    if not batches:
        return '<li class="empty-state compact-empty">No recent imports yet.</li>'
    rendered = []
    for batch in batches:
        summary = import_batch_summary(batch)
        inserted = int(summary.get("inserted", 0) or 0)
        updated = int(summary.get("updated", 0) or 0)
        grouped = int(summary.get("grouped", 0) or 0)
        changed = inserted + updated + grouped
        status_class = "accepted" if batch["status"] == "applied" else "declined" if batch["status"] == "undone" else "pending"
        undo_form = ""
        if batch["status"] == "applied":
            undo_form = f"""
            <form method="post" action="/imports/{batch["id"]}/undo">
                <input type="hidden" name="redirect_to" value="{e(redirect_to)}">
                <button class="button danger small" type="submit" data-confirm="Undo this import batch? Imported rows/group links from the batch will be reverted.">Undo</button>
            </form>
            """
        rendered.append(
            f"""
            <li class="import-batch-row">
                <div>
                    <strong>Batch #{e(batch["id"])} - {e(batch["source"] or batch["import_type"])}</strong>
                    <span class="subtle">{e(batch["created_at"][:16].replace("T", " "))} - {e(changed)} change{'s' if changed != 1 else ''}</span>
                </div>
                <div class="inline-actions">
                    <span class="status {status_class}">{e(batch["status"].title())}</span>
                    {undo_form}
                </div>
            </li>
            """
        )
    return "".join(rendered)


def render_collection_import_preview(preview):
    if not preview:
        return ""
    warning_block = render_import_warning_block(preview)
    return f"""
    <section class="panel import-preview">
        <div class="panel-heading">
            <h2>Import preview</h2>
            <span class="pill">Batch #{e(preview["batch_id"])}</span>
        </div>
        <div class="metric-grid compact-metrics">
            <article class="metric"><span>{e(preview["inserted"])}</span><p>Will insert</p></article>
            <article class="metric"><span>{e(preview["updated"])}</span><p>Will update</p></article>
            <article class="metric"><span>{e(preview["enriched"])}</span><p>Enriched</p></article>
            <article class="metric"><span>{e(preview["queued"])}</span><p>Will queue</p></article>
            <article class="metric"><span>{e(preview.get("skipped", 0))}</span><p>Would skip</p></article>
        </div>
        {render_import_preview_rows(preview)}
        {warning_block}
        <form class="form-actions" method="post" action="/import">
            <input type="hidden" name="intent" value="commit_preview">
            <input type="hidden" name="batch_id" value="{e(preview["batch_id"])}">
            <button class="button primary" type="submit">Import these rows</button>
            <a class="button secondary" href="/import">Cancel</a>
        </form>
    </section>
    """


def render_deck_import_preview(group, preview):
    if not preview:
        return ""
    warning_block = render_import_warning_block(preview)
    return f"""
    <section class="deck-import-review import-preview">
        <div class="panel-heading">
            <div>
                <h3>Preview deck import</h3>
                <p class="muted compact">Review the owned-card matches before adding them to this deck group.</p>
            </div>
            <span class="pill">Batch #{e(preview["batch_id"])}</span>
        </div>
        <div class="metric-grid compact-metrics">
            <article class="metric"><span>{e(preview["grouped"])}</span><p>Will group</p></article>
            <article class="metric"><span>{e(preview["matched"])}</span><p>Owned matched</p></article>
            <article class="metric"><span>{e(preview["missing"])}</span><p>Missing</p></article>
            <article class="metric"><span>{e(preview["enriched"])}</span><p>Enriched</p></article>
        </div>
        {render_import_preview_rows(preview)}
        {warning_block}
        <form class="form-actions" method="post" action="/groups/{group["id"]}/import">
            <input type="hidden" name="intent" value="commit_preview">
            <input type="hidden" name="batch_id" value="{e(preview["batch_id"])}">
            <button class="button primary" type="submit">Import deck cards</button>
            <a class="button secondary" href="/groups/{group["id"]}">Cancel</a>
        </form>
    </section>
    """


def render_deck_missing_wishlist_prompt(group, result):
    if not result or not result.get("missing_items"):
        return ""
    missing_items = result["missing_items"]
    payload = encode_deck_missing_wants_payload(missing_items)
    wishlist_groups = rows(
        """
        SELECT *
        FROM card_groups
        WHERE user_id = ? AND group_type = 'wishlist'
        ORDER BY name COLLATE NOCASE
        """,
        (group["user_id"],),
    )
    group_options = '<option value="0">Create a new wishlist group</option>' + "".join(
        f'<option value="{wishlist["id"]}">{e(wishlist["name"])}</option>'
        for wishlist in wishlist_groups
    )
    missing_rows = "".join(
        f"""
        <label class="missing-card-choice">
            <input type="checkbox" name="missing_key" value="{e(item["key"])}" checked>
            <span>
                <strong>{e(item["quantity"])} x {e(item["card_name"])}</strong>
                <small>{e(item["set_name"] or "Any set")} {e("(" + item["set_code"] + ")" if item["set_code"] else "")}</small>
            </span>
        </label>
        """
        for item in missing_items
    )
    return f"""
    <section class="deck-missing-panel">
        <div class="panel-heading">
            <div>
                <h3>Add missing cards to wishlist</h3>
                <p class="muted compact">Your collection is short {e(result["missing"])} card{'s' if result["missing"] != 1 else ''} for this deck. Add them to a grouped wishlist so you can track or trade for them later.</p>
            </div>
            <span class="pill">{e(result["missing_entries"])} entries</span>
        </div>
        <form class="deck-missing-form" method="post" action="/groups/{group["id"]}/missing-wants">
            <input type="hidden" name="payload" value="{e(payload)}">
            <div class="missing-card-list">{missing_rows}</div>
            <div class="form-grid compact-form embedded-form">
                <label>Wishlist group
                    <select name="wishlist_group_id">{group_options}</select>
                </label>
                <label>New group name
                    <input name="new_group_name" maxlength="80" value="{e(group["name"] + " missing cards")}">
                </label>
                <label class="checkbox-line span-2">
                    <input type="checkbox" name="wishlist_is_public" value="1" checked>
                    Make new wishlist group public
                </label>
            </div>
            <div class="form-actions">
                <button class="button primary" type="submit">Add selected wants</button>
            </div>
        </form>
    </section>
    """


def render_deck_import_review(group, review):
    if not review:
        return ""
    sections = review.get("sections", {})
    counts = deck_import_section_counts(sections)
    main_count = counts.get(DECK_IMPORT_MAIN_SECTION, 0)
    review_sections = deck_import_review_sections(sections)
    checkboxes = []
    for section, label, default_checked, quantity, entries in review_sections:
        detail = f"{quantity} card{'s' if quantity != 1 else ''} across {entries} entr{'ies' if entries != 1 else 'y'}"
        checkboxes.append(
            f"""
            <label class="deck-section-choice">
                <input type="checkbox" name="include_section" value="{e(section)}"{checked(default_checked)}>
                <span>
                    <strong>{e(label)}</strong>
                    <small>{e(detail)}</small>
                </span>
            </label>
            """
        )
    warnings = "".join(f"<li>{e(warning)}</li>" for warning in review.get("warnings", []))
    warning_block = f"""
    <div class="import-warnings compact-review-warnings">
        <h3>Warnings</h3>
        <ul>{warnings}</ul>
    </div>
    """ if warnings else ""
    return f"""
    <section class="deck-import-review">
        <div class="panel-heading">
            <div>
                <h3>Review detected sections</h3>
                <p class="muted compact">Main deck cards will be imported. Choose whether to include the extra sections found in this deck list.</p>
            </div>
            <span class="pill">{e(main_count)} main</span>
        </div>
        <form class="deck-review-form" method="post" action="/groups/{group["id"]}/import">
            <input type="hidden" name="intent" value="confirm_import">
            <input type="hidden" name="payload" value="{e(encode_deck_import_payload(review))}">
            <div class="deck-section-choice-list">
                {''.join(checkboxes)}
            </div>
            {warning_block}
            <div class="form-actions">
                <button class="button primary" type="submit">Import selected sections</button>
                <a class="button secondary" href="/groups/{group["id"]}">Cancel</a>
            </div>
        </form>
    </section>
    """


def render_deck_import_panel(user, group, result=None, review=None):
    preview_html = render_deck_import_preview(group, review) if review and review.get("preview") else ""
    review_html = "" if preview_html else render_deck_import_review(group, review)
    recent_batches = recent_import_batches(group["user_id"], "deck_group", group_id=group["id"])
    recent_batch_rows = render_import_batch_list(recent_batches, f"/groups/{group['id']}")
    return f"""
    <section class="panel deck-import-panel">
        <div class="panel-heading">
            <h2>Bulk import deck</h2>
            <span class="pill">CSV, text, or link</span>
        </div>
        {review_html}
        {preview_html}
        <form class="form-grid deck-import-form" method="post" action="/groups/{group["id"]}/import" enctype="multipart/form-data">
            <label>Source
                <select name="source">{option_tags(DECK_IMPORT_SOURCE_OPTIONS, "decklist")}</select>
                <small>Choose Auto detect for CSV exports, or the matching site profile when available.</small>
            </label>
            <label>CSV mapping preset
                <select name="mapping_preset_id">{render_csv_import_mapping_preset_options(user, "deck")}</select>
            </label>
            <label>CSV or deck-list file
                <input type="file" name="deck_file" accept=".csv,.txt,.dec,text/csv,text/plain">
            </label>
            <label class="span-2">Deck-list URL
                <input name="deck_url" type="url" placeholder="https://archidekt.com/decks/...">
            </label>
            <label class="span-2">Paste deck list
                <textarea name="deck_text" rows="7" placeholder="1 Sol Ring&#10;4 Counterspell (DMR) 45"></textarea>
            </label>
            <div class="toggle-stack">
                <label class="checkbox-line">
                    <input type="checkbox" name="enrich_scryfall" value="1"{checked(True)}>
                    Scryfall lookup
                </label>
                <label class="checkbox-line">
                    <input type="checkbox" name="merge_duplicates" value="1"{checked(True)}>
                    Merge duplicate deck rows
                </label>
            </div>
            <div class="form-actions">
                <button class="button primary" type="submit">Import deck</button>
            </div>
        </form>
        {render_group_import_result(result)}
        {render_deck_missing_wishlist_prompt(group, result)}
        <div class="panel-heading with-gap">
            <h2>Recent deck imports</h2>
        </div>
        <ul class="stack-list compact-stack">{recent_batch_rows}</ul>
    </section>
    """


def render_group_share_link_row(group, link):
    revoked = bool(row_value(link, "revoked_at", ""))
    expired = bool(row_value(link, "expires_at", "") and row_value(link, "expires_at", "") <= now_iso())
    state = "Revoked" if revoked else "Expired" if expired else "Active"
    state_class = "declined" if revoked or expired else "accepted"
    revoke_form = ""
    if not revoked:
        revoke_form = f"""
        <form method="post" action="/groups/{group["id"]}/share-links/{link["id"]}/revoke">
            <button class="button danger small" type="submit" data-confirm="Revoke this private share link?">Revoke</button>
        </form>
        """
    expires = row_value(link, "expires_at", "")
    return f"""
    <li>
        <div>
            <strong>{e(link["label"])}</strong>
            <span>Token ending {e(link["token_hint"])} - {e("expires " + expires[:10] if expires else "no expiration")}</span>
            <small>{e("Values shown" if link["show_values"] else "Values hidden")} - {e("photos shown" if link["show_photos"] else "photos hidden")}{e(" - last opened " + link["last_accessed_at"][:10] if link["last_accessed_at"] else "")}</small>
        </div>
        <div class="actions"><span class="status {state_class}">{e(state)}</span>{revoke_form}</div>
    </li>
    """


def render_group_privacy_panel(group, share_result=None):
    share_rows = "".join(render_group_share_link_row(group, link) for link in group_share_link_rows(group["user_id"], group["id"]))
    share_rows = share_rows or '<li class="muted compact">No private share links yet.</li>'
    share_result_panel = ""
    if share_result:
        share_result_panel = f"""
        <div class="notice success span-2">
            <strong>Copy this private link now</strong>
            <input readonly value="{e(share_result["url"])}" onclick="this.select()">
        </div>
        """
    visibility_options = "".join(
        f'<option value="{e(value)}"{selected(record_visibility(group), value)}>{e(label)}</option>'
        for value, label in VISIBILITY_OPTIONS
    )
    default_options = "".join(
        f'<option value="{e(value)}"{selected(row_value(group, "default_item_visibility", VISIBILITY_MEMBERS), value)}>{e(label)}</option>'
        for value, label in VISIBILITY_OPTIONS
    )
    return f"""
    <section class="content-grid group-sharing-grid">
        <form class="panel form-grid compact-form" method="post" action="/groups/{group["id"]}/sharing">
            <div class="span-2 panel-heading"><h2>Sharing defaults</h2><span class="pill">{e(visibility_label(group))}</span></div>
            <label>Group audience<select name="visibility">{visibility_options}</select></label>
            <label>New item default<select name="default_item_visibility">{default_options}</select></label>
            <label class="checkbox-line span-2"><input type="checkbox" name="show_values" value="1"{checked(row_value(group, "show_values", 1))}> Show card values when this group is viewed</label>
            <label class="checkbox-line span-2"><input type="checkbox" name="show_photos" value="1"{checked(row_value(group, "show_photos", 1))}> Show condition photos when this group is viewed</label>
            <p class="muted compact span-2">Defaults apply to future items and imports. Existing card visibility is never changed automatically.</p>
            <div class="form-actions span-2"><button class="button secondary" type="submit">Save sharing defaults</button></div>
        </form>
        <article class="panel">
            <form class="form-grid compact-form" method="post" action="/groups/{group["id"]}/share-links">
                <div class="span-2 panel-heading"><h2>Private share links</h2></div>
                <label>Label<input name="label" maxlength="80" placeholder="Friday meetup"></label>
                <label>Expires<select name="expires_days"><option value="7">In 7 days</option><option value="30" selected>In 30 days</option><option value="90">In 90 days</option><option value="0">Never</option></select></label>
                <label class="checkbox-line"><input type="checkbox" name="show_values" value="1"> Show values</label>
                <label class="checkbox-line"><input type="checkbox" name="show_photos" value="1" checked> Show photos</label>
                {share_result_panel}
                <div class="form-actions span-2"><button class="button primary" type="submit">Create private link</button></div>
            </form>
            <ul class="stack-list compact-stack">{share_rows}</ul>
        </article>
    </section>
    """


def render_group_detail(user, group_id, notice=None, status="info", import_result=None, import_review=None, query=None, share_result=None, active_section=""):
    group = user_group(user["id"], group_id)
    if not group:
        return None
    query = query or {}
    if group["group_type"] == "wishlist":
        filters = group_item_filter_values(query, wishlist=True)
        current_sort, current_dir = sort_state(query, WANT_SORT_OPTIONS)
        order_clause = sort_order_clause(
            query,
            WANT_SORT_OPTIONS,
            GROUP_WANT_SORT_SQL,
            fallback=("want_items.card_name COLLATE NOCASE",),
        )
        filtered_count = wishlist_group_item_count(group_id, filters)
        page, per_page, page_count, offset = pagination_state(query, filtered_count)
        group_items = wishlist_group_items(group_id, order_clause, filters, per_page, offset)
        controls = render_group_item_controls(group, query, filters, current_sort, current_dir)
        pagination = render_pagination(f"/groups/{group_id}", query, filtered_count, page, per_page, page_count)
        redirect_to = page_url(f"/groups/{group_id}", query, page, per_page) + "#group-cards"
        items_html = render_group_want_items(group, group_items, controls, pagination, filtered_count, redirect_to, filters)
        group_item_count = wishlist_group_item_count(group_id)
        group_item_label = "wanted cards"
        subnav = render_wishlist_subnav("groups")
        layout_active = "wants"
        back_label = "Wishlist groups"
    else:
        filters = group_item_filter_values(query)
        current_sort, current_dir = sort_state(query, CARD_SORT_OPTIONS)
        order_clause = sort_order_clause(
            query,
            CARD_SORT_OPTIONS,
            GROUP_COLLECTION_SORT_SQL,
            fallback=("collection_items.card_name COLLATE NOCASE",),
        )
        filtered_count = collection_group_item_count(group_id, filters)
        page, per_page, page_count, offset = pagination_state(query, filtered_count)
        group_items = collection_group_items(group_id, order_clause, filters, per_page, offset)
        controls = render_group_item_controls(group, query, filters, current_sort, current_dir)
        pagination = render_pagination(f"/groups/{group_id}", query, filtered_count, page, per_page, page_count)
        redirect_to = page_url(f"/groups/{group_id}", query, page, per_page) + "#group-cards"
        items_html = render_group_collection_items(group, group_items, controls, pagination, filtered_count, redirect_to, filters)
        group_item_count = collection_group_quantity(group_id)
        group_item_label = "cards in group"
        subnav = render_cards_subnav("groups")
        layout_active = "cards"
        back_label = "Decks & Binders"
    deck_import_html = render_deck_import_panel(user, group, import_result, import_review) if group["group_type"] == "deck" else ""
    description = f'<p class="lead">{e(group["description"])}</p>' if group["description"] else ""
    workspace_items = [
        ("#group-cards", "Cards", f"Manage {group_type_label(group['group_type']).lower()} contents"),
        ("#group-sharing", "Sharing", "Audience, defaults, and private links"),
    ]
    if group["group_type"] == "deck":
        workspace_items.append(("#group-import", "Import", "Add a deck list and review recent imports"))
    workspace_items.append(("#group-danger", "Group settings", "Export or remove this group"))
    active_attr = workspace_active_attr(active_section, [href.lstrip("#") for href, _text, _detail in workspace_items])
    content = f"""
    {subnav}
    <section class="section-heading">
        <div>
            <p class="eyebrow">{e(group_type_label(group["group_type"]))}</p>
            <h1>{e(group["name"])}</h1>
            {description}
            {visibility_badge(group)}
        </div>
        <div class="actions">
            <a class="button secondary" href="{e(group_listing_url(group))}">{e(back_label)}</a>
            <a class="button primary" href="#group-cards">Manage cards</a>
        </div>
    </section>
    <section class="metric-grid compact-metrics group-detail-metrics">
        <article class="metric"><span>{e(group_item_count)}</span><p>{e(group_item_label)}</p></article>
        <article class="metric"><span>{e(visibility_label(group))}</span><p>group audience</p></article>
        <article class="metric"><span>{e("Shown" if row_value(group, "show_values", 1) else "Hidden")}</span><p>card values</p></article>
        <article class="metric"><span>{e("Shown" if row_value(group, "show_photos", 1) else "Hidden")}</span><p>condition photos</p></article>
    </section>
    <section class="workspace-layout tabbed-workspace group-detail-workspace" data-workspace-tabs{active_attr}>
        {render_workspace_nav(workspace_items, label="Group workspace", compact=True, vertical=True)}
        <div class="workspace-pane-stack">
    <section class="workspace-section group-workspace-section" id="group-cards">
        <div class="workspace-section-heading">
            <div><p class="eyebrow">Contents</p><h2>Manage cards</h2><p class="muted compact">Sort the group, add cards, or remove cards without changing the source collection or wishlist.</p></div>
        </div>
        {items_html}
    </section>
    <section class="workspace-section group-workspace-section" id="group-sharing">
        <div class="workspace-section-heading">
            <div><p class="eyebrow">Sharing</p><h2>Audience and private links</h2><p class="muted compact">Control who can view the group and what details appear.</p></div>
        </div>
        {render_group_privacy_panel(group, share_result=share_result)}
    </section>
    {f'<section class="workspace-section group-workspace-section" id="group-import"><div class="workspace-section-heading"><div><p class="eyebrow">Import</p><h2>Bulk import deck</h2><p class="muted compact">Bring in a deck list, review detected sections, and undo recent imports when needed.</p></div></div>{deck_import_html}</section>' if deck_import_html else ''}
    <section class="workspace-section group-workspace-section" id="group-danger">
        <div class="workspace-section-heading">
            <div><p class="eyebrow">Group settings</p><h2>Export or remove group</h2><p class="muted compact">Export a portable copy or remove this organizational group. Source cards and wants remain untouched.</p></div>
        </div>
        <article class="panel group-danger-panel">
            <div>
                <strong>Group data</strong>
                <span class="muted compact">Exporting is safe. Deleting removes this group and its organization only.</span>
            </div>
            <div class="actions">
                <a class="button secondary" href="/groups/{group["id"]}/export">Export CSV</a>
                <form method="post" action="/groups/{group["id"]}/delete">
                    <button class="button danger" type="submit" data-confirm="Delete this group? Cards stay in your collection and wants.">Delete group</button>
                </form>
            </div>
        </article>
    </section>
        </div>
    </section>
    """
    return render_layout(user, group["name"], content, active=layout_active, notice=notice, status=status)


def render_shared_photo_gallery(collection_item_id, token):
    photos = collection_item_photo_rows(collection_item_id)
    if not photos:
        return ""
    return '<div class="condition-photo-gallery compact">' + "".join(
        f'<a href="/share/{e(token)}/photos/{photo["id"]}" target="_blank" rel="noreferrer"><img src="/share/{e(token)}/photos/{photo["id"]}" alt="{e(photo["caption"] or photo["original_filename"])}"></a>'
        for photo in photos
    ) + "</div>"


def render_shared_group(link, token):
    group_id = link["target_id"]
    show_values = bool(link["show_values"])
    show_photos = bool(link["show_photos"])
    if link["group_type"] == "wishlist":
        items = rows(
            """
            SELECT want_items.*
            FROM group_want_items
            JOIN want_items ON want_items.id = group_want_items.want_item_id
            WHERE group_want_items.group_id = ?
            ORDER BY want_items.card_name COLLATE NOCASE, want_items.set_name COLLATE NOCASE
            """,
            (group_id,),
        )
        body = "".join(
            f"""
            <li class="group-item"><div><strong>{e(item["card_name"])}</strong>
            <span>{e(item["set_name"] or "Any set")} - want {e(item["desired_quantity"])}</span>
            {price_pill(item) if show_values else ""}</div></li>
            """
            for item in items
        )
    else:
        items = rows(
            """
            SELECT group_collection_items.quantity AS group_quantity, collection_items.*
            FROM group_collection_items
            JOIN collection_items ON collection_items.id = group_collection_items.collection_item_id
            WHERE group_collection_items.group_id = ?
            ORDER BY collection_items.card_name COLLATE NOCASE, collection_items.set_name COLLATE NOCASE
            """,
            (group_id,),
        )
        body = "".join(
            f"""
            <li class="group-item"><div class="card-cell">
                {f'<img class="card-thumb" src="{e(item["image_url"])}" alt="" referrerpolicy="no-referrer">' if item["image_url"] else '<span class="card-thumb placeholder"></span>'}
                <div><strong>{e(item["group_quantity"])} x {e(item["card_name"])}</strong>
                <span>{e(item["set_name"] or "Any set")} - {e(item["condition"])} - {e(item["finish"])}</span>
                {price_pill(item) if show_values else ""}
                {render_shared_photo_gallery(item["id"], token) if show_photos else ""}</div>
            </div></li>
            """
            for item in items
        )
    items_html = f'<ul class="group-item-list">{body}</ul>' if body else '<div class="empty-state">This shared group has no cards.</div>'
    description = f'<p class="lead">{e(link["group_description"])}</p>' if link["group_description"] else ""
    content = f"""
    <section class="section-heading">
        <div><p class="eyebrow">Shared by {e(link["owner_name"])}</p><h1>{e(link["group_name"])}</h1>{description}</div>
        <span class="pill">Private link</span>
    </section>
    <section class="panel">
        <div class="panel-heading"><h2>{e(group_type_label(link["group_type"]))} cards</h2><span class="muted">{e("Values shown" if show_values else "Values hidden")} - {e("photos shown" if show_photos else "photos hidden")}</span></div>
        {items_html}
    </section>
    """
    return render_layout(None, link["group_name"], content)


def public_member_group_rows(member_id, viewer=None):
    group_clause, group_params = visibility_sql_for_user(viewer, "visibility", "user_id")
    collection_clause, collection_params = visibility_sql_for_user(viewer, "collection_items.visibility", "collection_items.user_id")
    want_clause, want_params = visibility_sql_for_user(viewer, "want_items.visibility", "want_items.user_id")
    visible_groups = rows(
        f"SELECT * FROM card_groups WHERE user_id = ? AND {group_clause} ORDER BY group_type, name COLLATE NOCASE",
        [member_id, *group_params],
    )
    result = []
    for group in visible_groups:
        data = dict(group)
        collection = row(
            f"""
            SELECT COALESCE(SUM(group_collection_items.quantity), 0) AS quantity, COUNT(*) AS entries
            FROM group_collection_items
            JOIN collection_items ON collection_items.id = group_collection_items.collection_item_id
            WHERE group_collection_items.group_id = ? AND {collection_clause}
            """,
            [group["id"], *collection_params],
        )
        wants = row(
            f"""
            SELECT COUNT(*) AS entries
            FROM group_want_items
            JOIN want_items ON want_items.id = group_want_items.want_item_id
            WHERE group_want_items.group_id = ? AND {want_clause}
            """,
            [group["id"], *want_params],
        )
        data["collection_quantity"] = int(collection["quantity"] or 0)
        data["collection_entries"] = int(collection["entries"] or 0)
        data["want_entries"] = int(wants["entries"] or 0)
        result.append(data)
    return result


def render_public_group_collection_items(user, member, group, items):
    if not items:
        return '<div class="empty-state">No public cards in this group.</div>'
    rendered = "".join(
        f"""
        <li class="group-item">
            <div class="card-cell">
                {f'<img class="card-thumb" src="{e(item["image_url"])}" alt="">' if item["image_url"] else '<span class="card-thumb placeholder"></span>'}
                <div>
                    <strong>{e(item["group_quantity"])} x {e(item["card_name"])}</strong>
                    <span>{e(item["set_name"] or "Any set")} - {e(item["condition"])} - {e(item["finish"])}</span>
                    {f'<span class="subtle condition-detail">{e(row_value(item, "condition_notes", ""))}</span>' if row_value(item, "condition_notes", "") else ''}
                    {render_collection_photo_gallery(item["id"], compact=True) if row_value(group, "show_photos", 1) else ""}
                    <span class="subtle">{e(game_label(item["game"]))}{visible_price_pill(user, member, item, group=group)}</span>
                </div>
            </div>
        </li>
        """
        for item in items
    )
    return f'<ul class="group-item-list">{rendered}</ul>'


def render_public_group_want_items(user, member, group, wants):
    if not wants:
        return '<div class="empty-state">No public wanted cards in this group.</div>'
    rendered = "".join(
        f"""
        <li class="group-item">
            <div>
                <strong>{e(want["card_name"])}</strong>
                <span>{e(want["set_name"] or "Any set")} - want {e(want["desired_quantity"])}</span>
                <span class="subtle">{e(want["type_line"] or "Any printing")}{visible_price_pill(user, member, want, group=group)}</span>
                <span class="subtle">{e(want_priority_label(row_value(want, "priority", "normal")))} priority{f' - up to ${e(normalize_price_usd(row_value(want, "budget_cap_usd", "")))} each' if normalize_price_usd(row_value(want, "budget_cap_usd", "")) else ''}</span>
                {f'<span class="subtle"><strong>Preferred printing:</strong> {e(row_value(want, "preferred_printing_notes", ""))}</span>' if row_value(want, "preferred_printing_notes", "") else ''}
            </div>
        </li>
        """
        for want in wants
    )
    return f'<ul class="group-item-list">{rendered}</ul>'


def render_public_group_detail(user, member_id, group_id, query=None, notice=None, status="info"):
    member = row("SELECT * FROM users WHERE id = ?", (member_id,))
    if not member or member["id"] == user["id"] or member["is_banned"]:
        return None
    query = query or {}
    group = row("SELECT * FROM card_groups WHERE id = ? AND user_id = ?", (group_id, member_id))
    if not group or not can_view_record(user, member_id, group):
        return None
    collection_clause, collection_params = visibility_sql_for_user(user, "collection_items.visibility", "collection_items.user_id")
    want_clause, want_params = visibility_sql_for_user(user, "want_items.visibility", "want_items.user_id")
    if group["group_type"] == "wishlist":
        current_sort, current_dir = sort_state(query, WANT_SORT_OPTIONS)
        order_clause = sort_order_clause(
            query,
            WANT_SORT_OPTIONS,
            GROUP_WANT_SORT_SQL,
            fallback=("want_items.card_name COLLATE NOCASE",),
        )
        items = rows(
            f"""
            SELECT group_want_items.id AS group_item_id, want_items.*
            FROM group_want_items
            JOIN want_items ON want_items.id = group_want_items.want_item_id
            WHERE group_want_items.group_id = ? AND {want_clause}
            ORDER BY {order_clause}
            """,
            [group_id, *want_params],
        )
        items_html = render_public_group_want_items(user, member, group, items)
        sort_options = WANT_SORT_OPTIONS
    else:
        current_sort, current_dir = sort_state(query, CARD_SORT_OPTIONS)
        order_clause = sort_order_clause(
            query,
            CARD_SORT_OPTIONS,
            GROUP_COLLECTION_SORT_SQL,
            fallback=("collection_items.card_name COLLATE NOCASE",),
        )
        items = rows(
            f"""
            SELECT
                group_collection_items.id AS group_item_id,
                group_collection_items.quantity AS group_quantity,
                collection_items.*
            FROM group_collection_items
            JOIN collection_items ON collection_items.id = group_collection_items.collection_item_id
            WHERE group_collection_items.group_id = ? AND {collection_clause}
            ORDER BY {order_clause}
            """,
            [group_id, *collection_params],
        )
        items_html = render_public_group_collection_items(user, member, group, items)
        sort_options = CARD_SORT_OPTIONS
    sort_bar = render_sort_bar(f"/members/{member_id}/groups/{group_id}", query, sort_options, current_sort, current_dir)
    description = f'<p class="lead">{e(group["description"])}</p>' if group["description"] else ""
    content = f"""
    <section class="section-heading">
        <div>
            <p class="eyebrow">{e(member["display_name"])} {e(group_type_label(group["group_type"]))}</p>
            <h1>{e(group["name"])}</h1>
            {description}
        </div>
        <a class="button secondary" href="/members/{member["id"]}">Back to member</a>
    </section>
    <section class="panel">
        <div class="panel-heading">
            <h2>{e(group_type_label(group["group_type"]))} cards</h2>
            {visibility_badge(group)}
        </div>
        {sort_bar}
        {items_html}
    </section>
    """
    return render_layout(user, group["name"], content, active="browse", notice=notice, status=status)


__all__ = ['group_count_label', 'record_is_public', 'visibility_label', 'visibility_badge', 'visibility_checkbox', 'render_group_card', 'normalize_group_view', 'render_groups', 'collection_item_option_tags', 'want_item_option_tags', 'group_item_filter_values', 'group_item_hidden_filter_inputs', 'render_group_collection_items', 'render_group_want_items', 'render_group_import_result', 'render_import_warning_block', 'render_import_preview_rows', 'render_import_batch_list', 'render_collection_import_preview', 'render_deck_import_preview', 'render_deck_missing_wishlist_prompt', 'render_deck_import_review', 'render_deck_import_panel', 'render_group_detail', 'public_member_group_rows', 'render_public_group_collection_items', 'render_public_group_want_items', 'render_public_group_detail']
__all__.extend(['render_group_share_link_row', 'render_group_privacy_panel', 'render_shared_photo_gallery', 'render_shared_group'])
