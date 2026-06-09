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
    return bool(int(row_value(record, "is_public", 1) or 0))


def visibility_label(record):
    return "Public" if record_is_public(record) else "Private"


def visibility_badge(record):
    css_class = "accepted" if record_is_public(record) else "pending"
    return f'<span class="status {css_class}">{e(visibility_label(record))}</span>'


def visibility_checkbox(record, name="is_public"):
    return f"""
    <label class="span-2">Visibility
        <select name="{e(name)}">
            <option value="1"{selected("1" if record_is_public(record) else "0", "1")}>Public</option>
            <option value="0"{selected("1" if record_is_public(record) else "0", "0")}>Private</option>
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
                <button class="button danger small" type="submit" onclick="return confirm('Delete this group? Cards stay in your collection and wants.')">Delete</button>
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
        cards = "".join(render_group_card(group) for group in type_groups) or f'<div class="empty-state compact-empty">No {e(label.lower())} groups yet.</div>'
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
        <form class="panel form-grid compact-form" method="post" action="/groups">
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
            <label class="span-2">Visibility
                <select name="is_public">
                    <option value="1" selected>Public</option>
                    <option value="0">Private</option>
                </select>
            </label>
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


def render_group_collection_items(group, items, sort_bar=""):
    if items:
        rendered = "".join(
            f"""
            <li class="group-item">
                <div class="card-cell">
                    {f'<img class="card-thumb" src="{e(item["image_url"])}" alt="">' if item["image_url"] else '<span class="card-thumb placeholder"></span>'}
                    <div>
                        <strong>{e(item["group_quantity"])} x {e(item["card_name"])}</strong>
                        <span>{e(item["set_name"] or "Any set")} - {e(item["condition"])} - {e(item["finish"])}</span>
                        <span class="subtle">{e(item["quantity"])} owned{price_pill(item)}</span>
                    </div>
                </div>
                <form method="post" action="/groups/{group["id"]}/items/{item["group_item_id"]}/delete">
                    <button class="button ghost small" type="submit">Remove</button>
                </form>
            </li>
            """
            for item in items
        )
    else:
        rendered = '<li class="empty-state compact-empty">No cards added yet.</li>'
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
        else '<div class="empty-state compact-empty">Add cards to your collection before filling this group.</div>'
    )
    return f"""
    <section class="panel">
        <div class="panel-heading">
            <h2>{e(group_type_label(group["group_type"]))} cards</h2>
            <span class="pill">{e(sum(item["group_quantity"] for item in items))} total</span>
        </div>
        {sort_bar}
        <ul class="group-item-list">{rendered}</ul>
        {add_form}
    </section>
    """


def render_group_want_items(group, wants, sort_bar=""):
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
                <div>
                    <strong>{e(want["card_name"])}</strong>
                    <span>{e(want["set_name"] or "Any set")} - want {e(want["desired_quantity"])}</span>
                    <span class="subtle">{e(want["type_line"] or "Any printing")}{price_pill(want)}</span>
                    <span class="subtle">{e(want_priority_label(row_value(want, "priority", "normal")))} priority{e(budget_text)} - {e(preference_text)}</span>
                    {printing_note}
                </div>
                <form method="post" action="/groups/{group["id"]}/items/{want["group_item_id"]}/delete">
                    <button class="button ghost small" type="submit">Remove</button>
                </form>
            </li>
            """)
        rendered = "".join(rows)
    else:
        rendered = '<li class="empty-state compact-empty">No wanted cards added yet.</li>'
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
        else '<div class="empty-state compact-empty">Add cards to your want list before filling this wishlist.</div>'
    )
    return f"""
    <section class="panel">
        <div class="panel-heading">
            <h2>Wishlist cards</h2>
            <span class="pill">{e(len(wants))}</span>
        </div>
        {sort_bar}
        <ul class="group-item-list">{rendered}</ul>
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
            <td><span class="pill">{e(item.get("action", ""))}</span></td>
            <td>
                <strong>{e(item.get("card_name", ""))}</strong>
                <span class="subtle">{e(item.get("set_name", "") or "Any set")} {e("(" + item.get("set_code", "") + ")" if item.get("set_code") else "")}</span>
            </td>
            <td>{e(item.get("quantity", 0))}</td>
            <td>{e(item.get("quantity_for_trade", 0))}</td>
            <td>{e(item.get("condition", ""))} {e(item.get("finish", ""))}</td>
            <td>{e(item.get("note", ""))}</td>
        </tr>
        """
        for item in preview_rows
    )
    return f"""
    <div class="table-wrap import-preview-table">
        <table>
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
                <button class="button danger small" type="submit" onclick="return confirm('Undo this import batch? Imported rows/group links from the batch will be reverted.')">Undo</button>
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


def render_group_detail(user, group_id, notice=None, status="info", import_result=None, import_review=None, query=None):
    group = user_group(user["id"], group_id)
    if not group:
        return None
    query = query or {}
    if group["group_type"] == "wishlist":
        current_sort, current_dir = sort_state(query, WANT_SORT_OPTIONS)
        order_clause = sort_order_clause(
            query,
            WANT_SORT_OPTIONS,
            GROUP_WANT_SORT_SQL,
            fallback=("want_items.card_name COLLATE NOCASE",),
        )
        sort_bar = render_sort_bar(f"/groups/{group_id}", query, WANT_SORT_OPTIONS, current_sort, current_dir)
        items_html = render_group_want_items(group, wishlist_group_items(group_id, order_clause), sort_bar)
        subnav = render_wishlist_subnav("groups")
        layout_active = "wants"
        back_label = "Wishlist groups"
    else:
        current_sort, current_dir = sort_state(query, CARD_SORT_OPTIONS)
        order_clause = sort_order_clause(
            query,
            CARD_SORT_OPTIONS,
            GROUP_COLLECTION_SORT_SQL,
            fallback=("collection_items.card_name COLLATE NOCASE",),
        )
        sort_bar = render_sort_bar(f"/groups/{group_id}", query, CARD_SORT_OPTIONS, current_sort, current_dir)
        items_html = render_group_collection_items(group, collection_group_items(group_id, order_clause), sort_bar)
        subnav = render_cards_subnav("groups")
        layout_active = "cards"
        back_label = "Decks & Binders"
    deck_import_html = render_deck_import_panel(user, group, import_result, import_review) if group["group_type"] == "deck" else ""
    description = f'<p class="lead">{e(group["description"])}</p>' if group["description"] else ""
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
            <a class="button secondary" href="/groups/{group["id"]}/export">Export CSV</a>
            <form method="post" action="/groups/{group["id"]}/visibility">
                <input type="hidden" name="is_public" value="{e('0' if record_is_public(group) else '1')}">
                <button class="button secondary" type="submit">Make {e('private' if record_is_public(group) else 'public')}</button>
            </form>
            <form method="post" action="/groups/{group["id"]}/delete">
                <button class="button danger" type="submit" onclick="return confirm('Delete this group? Cards stay in your collection and wants.')">Delete group</button>
            </form>
        </div>
    </section>
    {items_html}
    {deck_import_html}
    """
    return render_layout(user, group["name"], content, active=layout_active, notice=notice, status=status)


def public_member_group_rows(member_id):
    return rows(
        """
        SELECT
            card_groups.*,
            COALESCE((
                SELECT SUM(group_collection_items.quantity)
                FROM group_collection_items
                JOIN collection_items ON collection_items.id = group_collection_items.collection_item_id
                WHERE group_collection_items.group_id = card_groups.id AND collection_items.is_public = 1
            ), 0) AS collection_quantity,
            (
                SELECT COUNT(*)
                FROM group_collection_items
                JOIN collection_items ON collection_items.id = group_collection_items.collection_item_id
                WHERE group_collection_items.group_id = card_groups.id AND collection_items.is_public = 1
            ) AS collection_entries,
            (
                SELECT COUNT(*)
                FROM group_want_items
                JOIN want_items ON want_items.id = group_want_items.want_item_id
                WHERE group_want_items.group_id = card_groups.id AND want_items.is_public = 1
            ) AS want_entries
        FROM card_groups
        WHERE card_groups.user_id = ? AND card_groups.is_public = 1
        ORDER BY card_groups.group_type, card_groups.name COLLATE NOCASE
        """,
        (member_id,),
    )


def render_public_group_collection_items(items):
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
                    <span class="subtle">{e(game_label(item["game"]))}{price_pill(item)}</span>
                </div>
            </div>
        </li>
        """
        for item in items
    )
    return f'<ul class="group-item-list">{rendered}</ul>'


def render_public_group_want_items(wants):
    if not wants:
        return '<div class="empty-state">No public wanted cards in this group.</div>'
    rendered = "".join(
        f"""
        <li class="group-item">
            <div>
                <strong>{e(want["card_name"])}</strong>
                <span>{e(want["set_name"] or "Any set")} - want {e(want["desired_quantity"])}</span>
                <span class="subtle">{e(want["type_line"] or "Any printing")}{price_pill(want)}</span>
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
    group = row(
        "SELECT * FROM card_groups WHERE id = ? AND user_id = ? AND is_public = 1",
        (group_id, member_id),
    )
    if not group:
        return None
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
            WHERE group_want_items.group_id = ? AND want_items.is_public = 1
            ORDER BY {order_clause}
            """,
            (group_id,),
        )
        items_html = render_public_group_want_items(items)
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
            WHERE group_collection_items.group_id = ? AND collection_items.is_public = 1
            ORDER BY {order_clause}
            """,
            (group_id,),
        )
        items_html = render_public_group_collection_items(items)
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


__all__ = ['group_count_label', 'record_is_public', 'visibility_label', 'visibility_badge', 'visibility_checkbox', 'render_group_card', 'normalize_group_view', 'render_groups', 'collection_item_option_tags', 'want_item_option_tags', 'render_group_collection_items', 'render_group_want_items', 'render_group_import_result', 'render_import_warning_block', 'render_import_preview_rows', 'render_import_batch_list', 'render_collection_import_preview', 'render_deck_import_preview', 'render_deck_missing_wishlist_prompt', 'render_deck_import_review', 'render_deck_import_panel', 'render_group_detail', 'public_member_group_rows', 'render_public_group_collection_items', 'render_public_group_want_items', 'render_public_group_detail']
