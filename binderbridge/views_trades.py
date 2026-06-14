"""Trade list, builder, review, detail, and feedback views.

This module is wired by binderbridge.views; shared app helpers are injected at runtime.
"""

from binderbridge.trade_queries import *


def trade_list_filter_chip_specs():
    direction_labels = {
        "incoming": "Incoming",
        "outgoing": "Outgoing",
        "needs_action": "Needs my response",
    }
    return (
        {"key": "q", "label": "Search"},
        {"key": "status", "label": "Status", "formatter": lambda value: TRADE_STATUS_LABELS.get(value, value.title())},
        {"key": "direction", "label": "View", "formatter": lambda value: direction_labels.get(value, value.title())},
    )


def render_trades(user, query=None, notice=None, status="info"):
    query = query or {}
    filters = trade_list_filter_values(query)
    total_count = trade_count_for_user(user["id"], filters)
    page, per_page, page_count, offset = pagination_state(query, total_count)
    trades = trade_page_rows(user["id"], filters, per_page, offset)
    needs_action_count = trade_count_for_user(user["id"], {"direction": "needs_action"})
    unread_trade_count = unread_trade_notification_count(user["id"])
    table = render_trade_table(user, trades) if trades else '<div class="empty-state">No trades match these filters.</div>'
    status_options = "".join(
        f'<option value="{e(value)}"{selected(filters["status"], value)}>{e(label)}</option>'
        for value, label in TRADE_STATUS_LABELS.items()
    )
    direction_options = "".join(
        f'<option value="{e(value)}"{selected(filters["direction"], value)}>{e(label)}</option>'
        for value, label in (
            ("incoming", "Incoming"),
            ("outgoing", "Outgoing"),
            ("needs_action", "Needs my response"),
        )
    )
    active_filters = render_active_filter_chips("/trades", query, filters, trade_list_filter_chip_specs())
    pagination = render_pagination("/trades", query, total_count, page, per_page, page_count)
    content = f"""
    {render_trades_subnav("offers")}
    <section class="section-heading">
        <div>
            <p class="eyebrow">Trades</p>
            <h1>Trade offers</h1>
            <p class="lead">Review active offers, follow conversations, and find past trades without digging through one long list.</p>
        </div>
        <div class="actions">
            {f'<a class="button secondary trade-inbox-button" href="/notifications">{e(unread_trade_count)} unread trade update{"s" if unread_trade_count != 1 else ""}</a>' if unread_trade_count else ''}
            <a class="button primary" href="/trades/matches">Find matches</a>
            <a class="button secondary" href="/browse">Browse cards</a>
        </div>
    </section>
    <section class="metric-grid compact-metrics trade-list-metrics">
        <article class="metric"><span>{e(total_count)}</span><p>matching offers</p></article>
        <article class="metric"><span>{e(needs_action_count)}</span><p>need your response</p></article>
        <article class="metric"><span>{e(unread_trade_count)}</span><p>unread updates</p></article>
    </section>
    <form class="filter-bar trade-list-filter-bar" method="get" action="/trades">
        <div class="filter-primary-row">
            <label class="search-field">Search
                <input name="q" value="{e(filters["q"])}" placeholder="Trade number or member">
            </label>
            <label>Status
                <select name="status"><option value="">All statuses</option>{status_options}</select>
            </label>
            <label>View
                <select name="direction"><option value="">All trades</option>{direction_options}</select>
            </label>
            <div class="actions filter-actions">
                <button class="button secondary" type="submit">Filter</button>
                <a class="button ghost" href="/trades">Reset</a>
            </div>
        </div>
    </form>
    {active_filters}
    <section class="panel flush">{table}</section>
    {pagination}
    """
    return render_layout(user, "Trades", content, active="trades", notice=notice, status=status)


def render_trade_table(user, trades, compact=False):
    body = "".join(
        f"""
        <tr class="{'trade-needs-attention' if trade['status'] == 'pending' and trade['recipient_id'] == user['id'] else ''}">
            <td data-label="Trade">
                <a href="/trades/{trade["id"]}">Trade #{trade["id"]}</a>
                {f'<span class="pill unread-trade-pill">{e(row_value(trade, "unread_trade_notifications", 0))} unread</span>' if int(row_value(trade, "unread_trade_notifications", 0) or 0) else ''}
            </td>
            <td data-label="From">{e(trade["proposer_name"])}</td>
            <td data-label="To">{e(trade["recipient_name"])}</td>
            <td data-label="Status"><span class="status {e(trade["status"])}">{e(TRADE_STATUS_LABELS.get(trade["status"], trade["status"]))}</span>{'<span class="subtle action-needed-label">Your response needed</span>' if trade['status'] == 'pending' and trade['recipient_id'] == user['id'] else ''}</td>
            <td data-label="Updated">{e(trade["updated_at"][:10])}</td>
        </tr>
        """
        for trade in trades
    )
    return f"""
    <div class="table-wrap">
        <table class="responsive-card-table trades-table">
            <thead>
                <tr><th>Trade</th><th>From</th><th>To</th><th>Status</th><th>Updated</th></tr>
            </thead>
            <tbody>{body}</tbody>
        </table>
    </div>
    """


def trade_picker_query_inputs(query):
    hidden = []
    allowed_prefixes = ("offer_", "request_")
    for key, values in query.items():
        if key == "recipient_id":
            continue
        if not key.startswith(allowed_prefixes):
            continue
        suffix = key.split("_", 1)[1]
        if suffix not in TRADE_PICKER_FILTER_KEYS and suffix not in ("page", "per_page"):
            continue
        for value in values:
            if value != "":
                hidden.append(f'<input type="hidden" name="{e(key)}" value="{e(value)}">')
    return "".join(hidden)


def trade_quantity_map(form, prefix):
    quantities = {}
    for key, values in form.items():
        if not key.startswith(f"{prefix}_"):
            continue
        suffix = key.split("_", 1)[1]
        if not suffix.isdigit():
            continue
        try:
            item_id = int(suffix)
            quantity = int(values[0])
        except (TypeError, ValueError):
            continue
        if quantity > 0:
            quantities[item_id] = quantity
    return quantities


def trade_selected_quantities_from_form(form):
    return {
        "offer": trade_quantity_map(form, "offer"),
        "request": trade_quantity_map(form, "request"),
    }


def trade_price_basis_for(user, data=None):
    return "scryfall"


def price_for_item_basis(item, price_basis):
    price = normalize_price_usd(row_value(item, "price_usd", ""))
    return price, "scryfall" if price else ""


def apply_trade_price_basis(item, price_basis):
    priced = dict(item)
    price, source = price_for_item_basis(item, price_basis)
    priced["display_price_usd"] = price
    priced["display_price_source"] = source
    return priced


def trade_selected_items(owner_id, selected_quantities, price_basis="", viewer_id=None):
    if not selected_quantities:
        return []
    item_ids = list(selected_quantities.keys())
    found = trade_selected_item_rows(owner_id, item_ids, viewer_id=viewer_id)
    selected = []
    for item in found:
        quantity = min(selected_quantities.get(item["id"], 0), item["quantity_for_trade"])
        if quantity > 0:
            selected.append((apply_trade_price_basis(item, price_basis), quantity))
    return selected


def trade_item_price_usd(item):
    return normalize_price_usd(
        row_value(item, "display_price_usd", row_value(item, "price_usd", ""))
    )


def trade_item_price_source(item):
    return "scryfall" if trade_item_price_usd(item) else ""


def trade_entry_value_cents(item, quantity):
    return price_to_cents(trade_item_price_usd(item)) * int(quantity or 0)


def trade_entries_value_cents(entries):
    return sum(trade_entry_value_cents(item, quantity) for item, quantity in entries)


def trade_entries_unpriced_count(entries):
    return sum(int(quantity or 0) for item, quantity in entries if not trade_item_price_usd(item))


def trade_value_gap_percent(difference_cents, max_value_cents):
    if not max_value_cents:
        return ""
    percent = (Decimal(abs(int(difference_cents or 0))) / Decimal(max_value_cents) * Decimal("100")).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    return format(percent.normalize(), "f") if percent else "0"


def trade_balance_details(offered, requested):
    offer_value = trade_entries_value_cents(offered)
    request_value = trade_entries_value_cents(requested)
    difference = request_value - offer_value
    total = max(offer_value, request_value)
    gap_percent = trade_value_gap_percent(difference, total)
    gap_text = f" ({gap_percent}%)" if gap_percent else ""
    unpriced = trade_entries_unpriced_count(offered) + trade_entries_unpriced_count(requested)
    if offer_value == 0 and request_value == 0:
        label = "No price data yet"
        detail = "Add Scryfall data to compare trade value."
        tone = "muted"
    elif abs(difference) <= max(100, round(total * 0.05)):
        label = "Value looks balanced"
        detail = f"Difference {money(abs(difference))}{gap_text}"
        tone = "balanced"
    elif difference > 0:
        label = f"Request side is {money(difference)} higher"
        detail = f"The cards being requested currently price higher than the cards offered by {gap_percent}%."
        tone = "request"
    else:
        label = f"Offer side is {money(abs(difference))} higher"
        detail = f"The cards being offered currently price higher than the cards requested by {gap_percent}%."
        tone = "offer"
    if unpriced:
        detail += f" {unpriced} unpriced card{'s' if unpriced != 1 else ''} counted as $0.00."
    return {
        "offer_value": offer_value,
        "request_value": request_value,
        "difference": difference,
        "difference_percent": gap_percent,
        "unpriced": unpriced,
        "label": label,
        "detail": detail,
        "tone": tone,
    }


def trade_fairness_assessment(offered, requested):
    balance = trade_balance_details(offered, requested)
    settings = trade_fairness_settings()
    if not balance["difference_percent"]:
        return {
            "severity": "unknown",
            "requires_acknowledgement": False,
            "message": "Scryfall price data is not available yet for this trade.",
            "balance": balance,
            "settings": settings,
        }
    percent = Decimal(str(balance["difference_percent"]))
    warn_percent = Decimal(settings["warn_percent"])
    block_percent = Decimal(settings["block_percent"])
    if settings["block_enabled"] and percent >= block_percent:
        severity = "blocked"
        message = (
            f"This trade is {balance['difference_percent']}% apart by Scryfall value, "
            f"which meets the admin block threshold of {settings['block_percent']}%."
        )
    elif settings["warn_enabled"] and percent >= warn_percent:
        severity = "warning"
        message = (
            f"This trade is {balance['difference_percent']}% apart by Scryfall value, "
            f"which meets the admin warning threshold of {settings['warn_percent']}%."
        )
    else:
        severity = "ok"
        message = "This trade is within the configured fairness threshold."
    return {
        "severity": severity,
        "requires_acknowledgement": severity == "warning",
        "message": message,
        "balance": balance,
        "settings": settings,
    }


def render_trade_fairness_notice(offered, requested, include_acknowledgement=False):
    assessment = trade_fairness_assessment(offered, requested)
    if assessment["severity"] not in ("warning", "blocked"):
        return ""
    title = "Trade fairness block" if assessment["severity"] == "blocked" else "Trade fairness warning"
    extra = ""
    if assessment["severity"] == "blocked":
        extra = "<p>This trade cannot be accepted or sent until the values are closer or an admin raises the block threshold.</p>"
    elif include_acknowledgement:
        extra = """
        <label class="checkbox-line trade-fairness-ack">
            <input type="checkbox" name="fairness_ack" value="1">
            I understand the Scryfall value difference and want to continue.
        </label>
        """
    return f"""
    <div class="trade-warning fairness-{e(assessment["severity"])}" role="alert">
        <strong>{e(title)}</strong>
        <p>{e(assessment["message"])}</p>
        {extra}
    </div>
    """


def validate_trade_fairness_for_send(offered, requested, acknowledged=False):
    assessment = trade_fairness_assessment(offered, requested)
    if assessment["severity"] == "blocked":
        raise ValueError(assessment["message"])
    if assessment["requires_acknowledgement"] and not acknowledged:
        raise ValueError("Acknowledge the trade fairness warning before continuing.")


def validate_trade_fairness_for_creation(offered, requested):
    assessment = trade_fairness_assessment(offered, requested)
    if assessment["severity"] == "blocked":
        raise ValueError(assessment["message"])


def render_trade_value_panel(offered, requested, offer_label="Offer value", request_label="Request value"):
    balance = trade_balance_details(offered, requested)
    return f"""
    <section class="trade-value-panel {e(balance["tone"])}">
        <div>
            <span>{e(offer_label)}</span>
            <strong data-trade-value="offer">{e(money(balance["offer_value"]))}</strong>
        </div>
        <div>
            <span>{e(request_label)}</span>
            <strong data-trade-value="request">{e(money(balance["request_value"]))}</strong>
        </div>
        <div class="trade-balance-message">
            <strong data-trade-balance-label>{e(balance["label"])}</strong>
            <span data-trade-balance-detail>{e(balance["detail"])}</span>
        </div>
    </section>
    """


def render_trade_selected_hidden_inputs(owner_id, selected_quantities, input_name, visible_ids, price_basis="", viewer_id=None):
    hidden_items = trade_selected_items(
        owner_id,
        {item_id: quantity for item_id, quantity in selected_quantities.items() if item_id not in visible_ids},
        price_basis,
        viewer_id,
    )
    hidden = []
    for item, quantity in hidden_items:
        metadata = f'{game_label(item["game"])} - {item["set_name"] or "Any set"}'
        if item["set_code"]:
            metadata += f' ({item["set_code"]})'
        if item["condition"] or item["finish"]:
            metadata += f' - {item["condition"]} {item["finish"]}'.strip()
        price = trade_item_price_usd(item)
        source = trade_item_price_source(item)
        hidden.append(
            f'<input type="hidden" form="trade-submit-form" name="{e(input_name)}_{e(item["id"])}" value="{e(quantity)}" data-trade-pick data-side="{e(input_name)}" data-card-name="{e(item["card_name"])}" data-card-meta="{e(metadata)}" data-card-price="{e(price)}" data-price-source="{e(source)}">'
        )
    return "".join(hidden)


def render_trade_selection_list(selected_items, empty_text):
    if not selected_items:
        return f'<li class="muted compact">{e(empty_text)}</li>'
    items = []
    for item, quantity in selected_items:
        unit_price = trade_item_price_usd(item)
        total = trade_entry_value_cents(item, quantity)
        price_line = ""
        if unit_price:
            source = price_source_label(trade_item_price_source(item))
            price_line = f'<span class="trade-item-price">{e(money(total))} value - {e(source)}</span>'
        condition_notes = row_value(item, "condition_notes", "")
        photo_gallery = render_collection_photo_gallery(item["id"], compact=True)
        items.append(
            f"""
        <li>
            <strong>{e(quantity)} x {e(item["card_name"])}</strong>
            <span>{e(item["set_name"] or "Any set")} - {e(item["condition"] or "Condition n/a")} - {e(item["finish"] or "Finish n/a")}</span>
            {f'<span class="condition-detail"><strong>Condition details:</strong> {e(condition_notes)}</span>' if condition_notes else ''}
            {photo_gallery}
            {price_line}
        </li>
        """
        )
    return "".join(items)


def render_trade_live_summary(selected_offer, selected_request):
    offer_count = sum(quantity for _, quantity in selected_offer)
    request_count = sum(quantity for _, quantity in selected_request)
    value_panel = render_trade_value_panel(selected_offer, selected_request, "You offer", "You request")
    return f"""
    <section class="panel trade-live-summary" aria-live="polite">
        <div class="panel-heading">
            <h2>Selected for trade</h2>
            <span class="muted"><span data-trade-total>{e(offer_count + request_count)}</span> cards</span>
        </div>
        {value_panel}
        <div class="trade-summary-grid">
            <article>
                <h3>You offer <span data-trade-count="offer">{e(offer_count)}</span></h3>
                <ul class="trade-item-list" data-trade-summary="offer">{render_trade_selection_list(selected_offer, "No offered cards selected yet.")}</ul>
            </article>
            <article>
                <h3>You request <span data-trade-count="request">{e(request_count)}</span></h3>
                <ul class="trade-item-list" data-trade-summary="request">{render_trade_selection_list(selected_request, "No requested cards selected yet.")}</ul>
            </article>
        </div>
    </section>
    """


def render_counter_context_panel(source_trade):
    if not source_trade:
        return ""
    return f"""
    <section class="trade-link-panel">
        <strong>Counter offer for <a href="/trades/{source_trade["id"]}">Trade #{source_trade["id"]}</a></strong>
        <p>This will send a new offer to {e(source_trade["proposer_name"])} and mark the original trade as countered after you send it.</p>
    </section>
    """


def render_trade_review(user, recipient_id, form, offered, requested, notice=None, status="info"):
    recipient = row("SELECT * FROM users WHERE id = ?", (recipient_id,))
    if not recipient or recipient["id"] == user["id"]:
        return None
    proposer_note = form.get("proposer_note", [""])[0].strip()
    price_basis = trade_price_basis_for(user, form)
    counter_trade_id = parse_counter_trade_id(form)
    counter_source = counter_source_trade_for(user["id"], recipient_id, counter_trade_id)
    counter_hidden = ""
    if counter_source:
        counter_hidden = f'<input type="hidden" name="counter_trade_id" value="{e(counter_source["id"])}">'
    hidden_quantities = []
    for prefix in ("offer", "request"):
        for item_id, quantity in trade_quantity_map(form, prefix).items():
            hidden_quantities.append(f'<input type="hidden" name="{e(prefix)}_{e(item_id)}" value="{e(quantity)}">')
    query_inputs = trade_picker_query_inputs(form)
    one_way_warning = ""
    if bool(offered) != bool(requested):
        one_way_warning = """
        <div class="trade-warning" role="alert">
            <strong>One-directional trade</strong>
            <p>This trade only has cards on one side. Review it carefully before sending.</p>
        </div>
        """
    fairness = trade_fairness_assessment(offered, requested)
    fairness_notice = render_trade_fairness_notice(offered, requested, include_acknowledgement=True)
    send_disabled = " disabled" if fairness["severity"] == "blocked" else ""
    content = f"""
    {render_trades_subnav("offers")}
    <section class="section-heading">
        <div>
            <p class="eyebrow">Review trade</p>
            <h1>Confirm with {e(recipient["display_name"])}</h1>
        </div>
        <a class="button secondary" href="/trades/new?recipient_id={recipient["id"]}">Start over</a>
    </section>
    <section class="trade-detail-grid">
        <article class="panel">
            <h2>You offer</h2>
            <ul class="trade-item-list">{render_trade_selection_list(offered, "No offered cards selected.")}</ul>
        </article>
        <article class="panel">
            <h2>You request</h2>
            <ul class="trade-item-list">{render_trade_selection_list(requested, "No requested cards selected.")}</ul>
        </article>
    </section>
    <section class="trade-link-panel">
        <strong>Price basis: {e(price_basis_label(price_basis))}</strong>
    </section>
    {render_trade_value_panel(offered, requested, "You offer", "You request")}
    {render_counter_context_panel(counter_source)}
    <form class="panel response-box" method="post" action="/trades/new">
        <input type="hidden" name="recipient_id" value="{e(recipient["id"])}">
        {counter_hidden}
        {query_inputs}
        <input type="hidden" name="price_source_preference" value="{e(price_basis)}">
        {''.join(hidden_quantities)}
        <label>Message
            <textarea name="proposer_note" rows="4" maxlength="1200">{e(proposer_note)}</textarea>
        </label>
        {one_way_warning}
        {fairness_notice}
        <div class="actions">
            <button class="button secondary" name="intent" value="edit" type="submit">Edit trade</button>
            <button class="button primary" name="intent" value="send" type="submit"{send_disabled}>Send trade</button>
        </div>
    </form>
    """
    return render_layout(user, "Review trade", content, active="trades", notice=notice, status=status)


def render_trade_picker_section(title, owner_id, recipient_id, query, prefix, input_name, empty_message, selected_quantities=None, subtitle="", price_basis="", viewer_id=None):
    selected_quantities = selected_quantities or {}
    viewer_id = owner_id if viewer_id is None else viewer_id
    filters = trade_picker_filter_values(query, prefix)
    advanced_active = trade_picker_has_advanced_filters(filters)
    advanced_count = sum(
        1
        for key, value in filters.items()
        if key not in ("q", "game", "condition", "finish") and value not in ("", None, False)
    )
    current_sort, current_dir = sort_state(query, CARD_SORT_OPTIONS, prefix=prefix)
    order_clause = sort_order_clause(
        query,
        CARD_SORT_OPTIONS,
        COLLECTION_SORT_SQL,
        prefix=prefix,
        fallback=("card_name COLLATE NOCASE", "set_name COLLATE NOCASE", "collector_number COLLATE NOCASE"),
    )
    where, params = trade_picker_where(owner_id, filters, viewer_id=viewer_id)
    total_count = trade_picker_count(where, params)
    page, per_page, page_count, offset = trade_picker_pagination_state(query, prefix, total_count)
    items = trade_picker_rows(where, params, order_clause, per_page, offset)
    items = [apply_trade_price_basis(item, price_basis) for item in items]
    visible_ids = {item["id"] for item in items}
    item_rows = "".join(
        render_collection_row(
            item,
            trade_select=True,
            input_name=input_name,
            form_id="trade-submit-form",
            selected_quantity=min(selected_quantities.get(item["id"], 0), item["quantity_for_trade"]),
        )
        for item in items
    )
    hidden_selected = render_trade_selected_hidden_inputs(owner_id, selected_quantities, input_name, visible_ids, price_basis, viewer_id=viewer_id)
    table = trade_picker_table(item_rows) if items else f'<div class="empty-state">{e(empty_message)}</div>'
    pagination = render_trade_picker_pagination(recipient_id, query, prefix, total_count, page, per_page, page_count)
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
    datalist_prefix = f"trade-{prefix}"
    reset_url = trade_picker_url(recipient_id, query, reset_prefix=prefix)
    subtitle_html = f'<p class="muted compact">{e(subtitle)}</p>' if subtitle else ""
    active_filter_chips = render_active_filter_chips(
        "/trades/new",
        query,
        filters,
        trade_picker_filter_chip_specs(prefix),
        page_key=f"{prefix}_page",
        required_params={"recipient_id": recipient_id},
        class_name="trade-picker-active-filters",
    )
    return f"""
    <section class="panel flush trade-picker-panel">
        <div class="panel-heading padded trade-picker-heading">
            <h2>{e(title)}</h2>
            {subtitle_html}
        </div>
        <form class="filter-bar trade-filter-bar" method="get" action="/trades/new">
            {trade_picker_preserved_inputs(recipient_id, query, prefix)}
            <div class="filter-primary-row">
                <label class="search-field">Search
                    <input name="{e(prefix)}_q" value="{e(filters["q"])}" placeholder="Card name or type" list="{datalist_prefix}-search-suggestions">
                </label>
                <label>Game
                    <select name="{e(prefix)}_game">
                        <option value="">All games</option>
                        {option_tags(CARD_GAMES, filters["game"])}
                    </select>
                </label>
                <label>Condition
                    <select name="{e(prefix)}_condition">
                        <option value="">All conditions</option>
                        {condition_options}
                    </select>
                </label>
                <label>Finish
                    <select name="{e(prefix)}_finish">
                        <option value="">All finishes</option>
                        {finish_options}
                    </select>
                </label>
                {render_sort_controls(CARD_SORT_OPTIONS, current_sort, current_dir, prefix=prefix)}
                <div class="actions filter-actions">
                    <button class="button secondary" type="submit">Filter</button>
                    <a class="button ghost" href="{e(reset_url)}">Reset</a>
                </div>
            </div>
            <details class="advanced-filter"{" open" if advanced_active else ""}>
                <summary>
                    <span>Advanced filters</span>
                    <span class="advanced-filter-count">{e(advanced_summary)}</span>
                </summary>
                <div class="advanced-filter-grid">
                    <label>Set name
                        <input name="{e(prefix)}_set_name" value="{e(filters["set_name"])}" placeholder="Dominaria Remastered" list="{datalist_prefix}-set-name-suggestions">
                    </label>
                    <label>Set code
                        <input name="{e(prefix)}_set_code" value="{e(filters["set_code"])}" placeholder="DMR" list="{datalist_prefix}-set-code-suggestions">
                    </label>
                    <label>Collector #
                        <input name="{e(prefix)}_collector_number" value="{e(filters["collector_number"])}" placeholder="123" list="{datalist_prefix}-collector-number-suggestions">
                    </label>
                    <label>Type line
                        <input name="{e(prefix)}_type_line" value="{e(filters["type_line"])}" placeholder="Creature, artifact, planeswalker" list="{datalist_prefix}-type-line-suggestions">
                    </label>
                    <label>Language
                        <select name="{e(prefix)}_language">
                            <option value="">Any language</option>
                            {language_options}
                        </select>
                    </label>
                    <label>Rarity
                        <select name="{e(prefix)}_rarity">
                            <option value="">Any rarity</option>
                            {rarity_options}
                        </select>
                    </label>
                    <label>Color identity
                        <select name="{e(prefix)}_color_identity">
                            <option value="">Any color identity</option>
                            {color_options}
                        </select>
                    </label>
                    <label>Card data
                        <select name="{e(prefix)}_card_data">
                            <option value="">Any card data</option>
                            {card_data_options}
                        </select>
                    </label>
                    <label>Owned qty min
                        <input type="number" min="0" name="{e(prefix)}_quantity_min" value="{e(filters["quantity_min"])}">
                    </label>
                    <label>Owned qty max
                        <input type="number" min="0" name="{e(prefix)}_quantity_max" value="{e(filters["quantity_max"])}">
                    </label>
                    <label>Available min
                        <input type="number" min="0" name="{e(prefix)}_trade_min" value="{e(filters["trade_min"])}">
                    </label>
                    <label>Available max
                        <input type="number" min="0" name="{e(prefix)}_trade_max" value="{e(filters["trade_max"])}">
                    </label>
                </div>
            </details>
            {trade_picker_datalists(prefix, owner_id, public_only=int(viewer_id) != int(owner_id))}
        </form>
        {active_filter_chips}
        {hidden_selected}
        {table}
        {pagination}
    </section>
    """


def trade_recommendation_meta(item):
    bits = [game_label(row_value(item, "game", ""))]
    set_name = row_value(item, "set_name", "")
    set_code = row_value(item, "set_code", "")
    collector_number = row_value(item, "collector_number", "")
    if set_name:
        bits.append(set_name)
    if set_code:
        bits.append(f"({set_code})")
    if collector_number:
        bits.append(f"#{collector_number}")
    condition = row_value(item, "condition", "")
    finish = row_value(item, "finish", "")
    if condition or finish:
        bits.append(" ".join(part for part in (condition, finish) if part))
    return " - ".join(str(bit) for bit in bits if bit)


def render_trade_recommendation_card(recommendation):
    item = recommendation["item"]
    side = recommendation["side"]
    quantity = int(recommendation["quantity"] or 1)
    max_quantity = max(quantity, int(row_value(item, "quantity_for_trade", quantity) or quantity))
    price = trade_item_price_usd(item)
    source = trade_item_price_source(item)
    value_line = f'<span>{e(money(recommendation["value_cents"]))} value</span>' if recommendation["value_cents"] else '<span>Unpriced</span>'
    scryfall_link = f'<a href="{e(row_value(item, "scryfall_uri", ""))}" target="_blank" rel="noreferrer">Scryfall</a>' if row_value(item, "scryfall_uri", "") else ""
    return f"""
    <article class="trade-recommendation-card">
        <div>
            <strong>{e(row_value(item, "card_name", "Card"))}</strong>
            <span class="subtle">{e(trade_recommendation_meta(item))}</span>
            <span class="subtle">{e(recommendation["reason"])}</span>
        </div>
        <div class="trade-recommendation-meta">
            <span class="pill">Add {e(quantity)}</span>
            {value_line}
            {scryfall_link}
        </div>
        <button
            class="button secondary small trade-recommendation-add"
            type="button"
            data-recommend-side="{e(side)}"
            data-recommend-id="{e(item["id"])}"
            data-recommend-quantity="{e(quantity)}"
            data-recommend-max="{e(max_quantity)}"
            data-card-name="{e(row_value(item, "card_name", "Card"))}"
            data-card-meta="{e(trade_recommendation_meta(item))}"
            data-card-price="{e(price)}"
            data-price-source="{e(source)}"
        >Add</button>
    </article>
    """


def render_trade_recommendation_group(title, recommendations, empty_text):
    if not recommendations:
        body = f'<p class="muted compact">{e(empty_text)}</p>'
    else:
        body = '<div class="trade-recommendation-list">' + "".join(render_trade_recommendation_card(item) for item in recommendations) + "</div>"
    return f"""
    <article class="trade-recommendation-group">
        <div class="panel-heading compact-heading">
            <h3>{e(title)}</h3>
            <span class="muted">{e(len(recommendations))}</span>
        </div>
        {body}
    </article>
    """


def render_trade_recommendations_panel(recommendations):
    balance = recommendations.get("balance") or {}
    balance_cards = balance.get("cards") or []
    gap_cents = int(balance.get("gap_cents") or 0)
    if gap_cents > 0:
        balance_title = f"Balance helpers for your offer"
        balance_empty = "Add offer cards to get closer to the requested value."
    elif gap_cents < 0:
        balance_title = "Balance helpers for your request"
        balance_empty = "Add requested cards to get closer to the offered value."
    else:
        balance_title = "Value balance helpers"
        balance_empty = "Current selected values are already close enough for now."
    if not recommendations.get("offer_wishlist") and not recommendations.get("request_wishlist") and not balance_cards:
        return ""
    return f"""
    <section class="panel trade-recommendations-panel">
        <div class="panel-heading">
            <div>
                <h2>Trade recommendations</h2>
                <p class="muted compact">Quick suggestions based on wishlists and current Scryfall value balance.</p>
            </div>
            <span class="pill">Suggestions</span>
        </div>
        <div class="trade-recommendation-grid">
            {render_trade_recommendation_group("They want from you", recommendations.get("offer_wishlist", []), "No matching wishlist cards from your trade binder yet.")}
            {render_trade_recommendation_group("You want from them", recommendations.get("request_wishlist", []), "No matching wishlist cards from their public trade binder yet.")}
            {render_trade_recommendation_group(balance_title, balance_cards, balance_empty)}
        </div>
        <div data-recommendation-hidden></div>
    </section>
    """


def render_new_trade(user, recipient_id, query=None, selected_quantities=None, proposer_note="", notice=None, status="info"):
    query = query or {}
    selected_quantities = selected_quantities or trade_selected_quantities_from_form(query)
    selected_quantities.setdefault("offer", {})
    selected_quantities.setdefault("request", {})
    recipient = row("SELECT * FROM users WHERE id = ?", (recipient_id,))
    if not recipient or recipient["id"] == user["id"]:
        return None
    price_basis = trade_price_basis_for(user, query)
    counter_trade_id = parse_counter_trade_id(query)
    counter_source = counter_source_trade_for(user["id"], recipient_id, counter_trade_id)
    counter_hidden = ""
    if counter_source:
        counter_hidden = f'<input type="hidden" name="counter_trade_id" value="{e(counter_source["id"])}">'
    offer_title = "You offer"
    request_title = "You request"
    offer_subtitle = ""
    request_subtitle = ""
    if counter_source:
        offer_title = "You offer from your collection"
        request_title = f'You request from {recipient["display_name"]}'
        offer_subtitle = "Available from your collection."
        request_subtitle = f'Available from {recipient["display_name"]}.'
    mine_picker = render_trade_picker_section(
        offer_title,
        user["id"],
        recipient_id,
        query,
        "offer",
        "offer",
        "No cards match your offer filters.",
        selected_quantities["offer"],
        offer_subtitle,
        price_basis,
        viewer_id=user["id"],
    )
    theirs_picker = render_trade_picker_section(
        request_title,
        recipient_id,
        recipient_id,
        query,
        "request",
        "request",
        f'No available cards from {recipient["display_name"]} match the request filters.',
        selected_quantities["request"],
        request_subtitle,
        price_basis,
        viewer_id=user["id"],
    )
    selected_offer = trade_selected_items(user["id"], selected_quantities["offer"], price_basis, viewer_id=user["id"])
    selected_request = trade_selected_items(recipient_id, selected_quantities["request"], price_basis, viewer_id=user["id"])
    selection_summary = render_trade_live_summary(selected_offer, selected_request)
    recommendations = trade_recommendations_for_pair(
        user["id"],
        recipient_id,
        selected_offer,
        selected_request,
        selected_quantities=selected_quantities,
        price_basis=price_basis,
    )
    recommendation_panel = render_trade_recommendations_panel(recommendations)
    one_way_policy = one_way_trade_policy()
    if user_can_propose_one_way_trade(user):
        if one_way_policy == "anyone":
            trust_message = "One-directional trades are allowed by site policy."
        elif one_way_policy == "admins":
            trust_message = "Admin one-directional trades enabled."
        else:
            trust_message = "Trusted one-directional trades enabled."
        trust_class = "trusted"
    elif one_way_policy == "disabled":
        trust_message = "One-directional trades are disabled. Select cards on both sides to propose a trade."
        trust_class = "disabled"
    elif one_way_policy == "admins":
        trust_message = "One-directional trades require admin status. Select cards on both sides to propose a trade."
        trust_class = "standard"
    else:
        trust_message = "One-directional trades require trusted status. Select cards on both sides to propose a trade."
        trust_class = "standard"
    content = f"""
    {render_trades_subnav("offers")}
    <section class="section-heading">
        <div>
            <p class="eyebrow">{"Counter offer" if counter_source else "New trade"}</p>
            <h1>Offer to {e(recipient["display_name"])}</h1>
        </div>
        <a class="button secondary" href="/members/{recipient["id"]}">Back to binder</a>
    </section>
    <div class="trade-builder-steps" aria-label="Trade builder steps">
        <span><strong>1</strong>Select cards</span>
        <span><strong>2</strong>Add a message</span>
        <span><strong>3</strong>Review and send</span>
    </div>
    {render_workspace_nav([
        ("#trade-selected", "Selected cards", "See the live offer and request"),
        ("#trade-recommendations", "Recommendations", "Use wishlist and value suggestions"),
        ("#trade-offer", "Your offer", "Choose cards from your collection"),
        ("#trade-request", "Your request", f"Choose cards from {recipient['display_name']}"),
        ("#trade-message", "Message", "Add context before review"),
    ], label="Trade builder")}
    <form id="trade-submit-form" method="post" action="/trades/new">
        <input type="hidden" name="recipient_id" value="{recipient["id"]}">
        <input type="hidden" name="price_source_preference" value="scryfall">
        {counter_hidden}
        {trade_picker_query_inputs(query)}
    </form>
    <div class="trade-builder">
        {render_counter_context_panel(counter_source)}
        <div class="trade-trust-note {trust_class}">{e(trust_message)}</div>
        <div id="trade-selected">{selection_summary}</div>
        <div id="trade-recommendations">{recommendation_panel}</div>
        <div id="trade-offer">{mine_picker}</div>
        <div id="trade-request">{theirs_picker}</div>
        <label class="panel note-panel" id="trade-message">Message
            <textarea form="trade-submit-form" name="proposer_note" rows="4" maxlength="1200" placeholder="Optional note">{e(proposer_note)}</textarea>
        </label>
        <div class="form-actions">
            <button class="button primary" form="trade-submit-form" name="intent" value="review" type="submit">Review trade</button>
        </div>
    </div>
    <script>
        (function () {{
            var root = document.querySelector(".trade-builder");
            if (!root) return;
            function itemMarkup(input) {{
                var qty = parseInt(input.value || "0", 10);
                if (!qty || qty < 1) return "";
                var name = input.dataset.cardName || "Card";
                var meta = input.dataset.cardMeta || "";
                var unitCents = priceCents(input);
                var priceLine = "";
                if (unitCents) {{
                    priceLine = '<span class="trade-item-price">' + formatMoney(unitCents * qty) + " value - " + escapeHtml(priceSource(input)) + "</span>";
                }}
                return "<li><strong>" + qty + " x " + escapeHtml(name) + "</strong><span>" + escapeHtml(meta) + "</span>" + priceLine + "</li>";
            }}
            function escapeHtml(value) {{
                return String(value).replace(/[&<>"']/g, function (char) {{
                    return ({{"&": "&amp;", "<": "&lt;", ">": "&gt;", "\\"": "&quot;", "'": "&#39;"}})[char];
                }});
            }}
            function priceCents(input) {{
                var value = parseFloat(input.dataset.cardPrice || "0");
                if (!isFinite(value) || value <= 0) return 0;
                return Math.round(value * 100);
            }}
            function priceSource(input) {{
                var source = input.dataset.priceSource || "";
                var labels = {{
                    scryfall: "Scryfall"
                }};
                return labels[source] || "Unknown";
            }}
            function formatMoney(cents) {{
                cents = Math.round(cents || 0);
                var sign = cents < 0 ? "-" : "";
                cents = Math.abs(cents);
                return sign + "$" + Math.floor(cents / 100) + "." + String(cents % 100).padStart(2, "0");
            }}
            function sideValue(side) {{
                var value = 0;
                var unpriced = 0;
                root.querySelectorAll('[data-trade-pick][data-side="' + side + '"]').forEach(function (input) {{
                    var qty = parseInt(input.value || "0", 10);
                    if (qty && qty > 0) {{
                        var unitCents = priceCents(input);
                        value += unitCents * qty;
                        if (!unitCents) unpriced += qty;
                    }}
                }});
                return {{ value: value, unpriced: unpriced }};
            }}
            function updateSide(side) {{
                var list = root.querySelector('[data-trade-summary="' + side + '"]');
                var countNode = root.querySelector('[data-trade-count="' + side + '"]');
                if (!list || !countNode) return 0;
                var total = 0;
                var html = "";
                root.querySelectorAll('[data-trade-pick][data-side="' + side + '"]').forEach(function (input) {{
                    var qty = parseInt(input.value || "0", 10);
                    if (qty && qty > 0) {{
                        total += qty;
                        html += itemMarkup(input);
                    }}
                }});
                if (!html) {{
                    html = '<li class="muted compact">' + (side === "offer" ? "No offered cards selected yet." : "No requested cards selected yet.") + "</li>";
                }}
                list.innerHTML = html;
                countNode.textContent = total;
                return total;
            }}
            function updateSummary() {{
                var offerCount = updateSide("offer");
                var requestCount = updateSide("request");
                var total = offerCount + requestCount;
                var totalNode = root.querySelector("[data-trade-total]");
                if (totalNode) totalNode.textContent = total;
                var offerValue = sideValue("offer");
                var requestValue = sideValue("request");
                var offerValueNode = root.querySelector('[data-trade-value="offer"]');
                var requestValueNode = root.querySelector('[data-trade-value="request"]');
                if (offerValueNode) offerValueNode.textContent = formatMoney(offerValue.value);
                if (requestValueNode) requestValueNode.textContent = formatMoney(requestValue.value);
                var labelNode = root.querySelector("[data-trade-balance-label]");
                var detailNode = root.querySelector("[data-trade-balance-detail]");
                var diff = requestValue.value - offerValue.value;
                var maxValue = Math.max(offerValue.value, requestValue.value);
                var threshold = Math.max(100, Math.round(maxValue * 0.05));
                var unpriced = offerValue.unpriced + requestValue.unpriced;
                var label = "No price data yet";
                var detail = "Add Scryfall data to compare trade value.";
                if (offerValue.value || requestValue.value) {{
                    if (Math.abs(diff) <= threshold) {{
                        label = "Value looks balanced";
                        detail = "Difference " + formatMoney(Math.abs(diff));
                    }} else if (diff > 0) {{
                        label = "Request side is " + formatMoney(diff) + " higher";
                        detail = "The cards being requested currently price higher than the cards offered.";
                    }} else {{
                        label = "Offer side is " + formatMoney(Math.abs(diff)) + " higher";
                        detail = "The cards being offered currently price higher than the cards requested.";
                    }}
                }}
                if (unpriced) {{
                    detail += " " + unpriced + " unpriced card" + (unpriced === 1 ? "" : "s") + " counted as $0.00.";
                }}
                if (labelNode) labelNode.textContent = label;
                if (detailNode) detailNode.textContent = detail;
            }}
            function selectedParams() {{
                var params = [];
                root.querySelectorAll("[data-trade-pick]").forEach(function (input) {{
                    var qty = parseInt(input.value || "0", 10);
                    if (qty && qty > 0) {{
                        params.push([input.name, String(qty)]);
                    }}
                }});
                return params;
            }}
            function addSelectionInputs(form) {{
                form.querySelectorAll("[data-synced-trade-pick]").forEach(function (input) {{
                    input.remove();
                }});
                selectedParams().forEach(function (pair) {{
                    var input = document.createElement("input");
                    input.type = "hidden";
                    input.name = pair[0];
                    input.value = pair[1];
                    input.dataset.syncedTradePick = "1";
                    form.appendChild(input);
                }});
            }}
            function withSelections(href) {{
                var url = new URL(href, window.location.origin);
                Array.from(url.searchParams.keys()).forEach(function (key) {{
                    if (/^(offer|request)_\\d+$/.test(key)) {{
                        url.searchParams.delete(key);
                    }}
                }});
                selectedParams().forEach(function (pair) {{
                    url.searchParams.set(pair[0], pair[1]);
                }});
                return url.pathname + url.search;
            }}
            function tradePickByName(name) {{
                var found = null;
                root.querySelectorAll("[data-trade-pick]").forEach(function (input) {{
                    if (!found && input.name === name) found = input;
                }});
                return found;
            }}
            function applyRecommendation(button) {{
                var side = button.dataset.recommendSide || "";
                var id = button.dataset.recommendId || "";
                if (!side || !id) return;
                var name = side + "_" + id;
                var input = tradePickByName(name);
                if (!input) {{
                    var container = root.querySelector("[data-recommendation-hidden]");
                    if (!container) return;
                    input = document.createElement("input");
                    input.type = "hidden";
                    input.name = name;
                    input.setAttribute("form", "trade-submit-form");
                    input.dataset.tradePick = "1";
                    input.dataset.side = side;
                    input.dataset.cardName = button.dataset.cardName || "Card";
                    input.dataset.cardMeta = button.dataset.cardMeta || "";
                    input.dataset.cardPrice = button.dataset.cardPrice || "";
                    input.dataset.priceSource = button.dataset.priceSource || "";
                    container.appendChild(input);
                }}
                var addQty = parseInt(button.dataset.recommendQuantity || "1", 10);
                var maxQty = parseInt(button.dataset.recommendMax || "1", 10);
                var current = parseInt(input.value || "0", 10);
                if (!isFinite(addQty) || addQty < 1) addQty = 1;
                if (!isFinite(maxQty) || maxQty < 1) maxQty = addQty;
                if (!isFinite(current) || current < 0) current = 0;
                input.value = Math.min(maxQty, current + addQty);
                updateSummary();
            }}
            root.querySelectorAll("form.trade-filter-bar").forEach(function (form) {{
                form.addEventListener("submit", function () {{
                    addSelectionInputs(form);
                }});
            }});
            root.addEventListener("click", function (event) {{
                var recommendationButton = event.target.closest("[data-recommend-side]");
                if (recommendationButton) {{
                    event.preventDefault();
                    applyRecommendation(recommendationButton);
                    return;
                }}
                var link = event.target.closest('a[href^="/trades/new"]');
                if (!link) return;
                link.href = withSelections(link.getAttribute("href"));
            }});
            root.querySelectorAll("[data-trade-pick]").forEach(function (input) {{
                input.addEventListener("input", updateSummary);
                input.addEventListener("change", updateSummary);
            }});
            updateSummary();
        }})();
    </script>
    """
    return render_layout(user, "New trade", content, active="trades", notice=notice, status=status)


def trade_picker_table(body):
    return f"""
    <div class="table-wrap">
        <table class="responsive-card-table trade-picker-table">
            <thead><tr><th>Card</th><th>Game</th><th>Set</th><th>Code</th><th>Owned</th><th>Trade</th><th>Details</th><th>Pick</th></tr></thead>
            <tbody>{body}</tbody>
        </table>
    </div>
    """


def render_trade_links(user, trade):
    links = []
    parent = linked_trade_for_user(row_value(trade, "countered_from_trade_id"), user["id"])
    counter = linked_trade_for_user(row_value(trade, "counter_trade_id"), user["id"])
    if parent:
        links.append(
            f"""
            <div>
                <strong>Counter offer to <a href="/trades/{parent["id"]}">Trade #{parent["id"]}</a></strong>
                <span>{e(parent["proposer_name"])} with {e(parent["recipient_name"])}</span>
            </div>
            """
        )
    if counter:
        links.append(
            f"""
            <div>
                <strong>Countered by <a href="/trades/{counter["id"]}">Trade #{counter["id"]}</a></strong>
                <span>{e(counter["proposer_name"])} with {e(counter["recipient_name"])}</span>
            </div>
            """
        )
    if not links:
        return ""
    return f'<section class="trade-link-panel trade-link-list">{"".join(links)}</section>'


def render_trade_comments(trade, comments):
    if comments:
        comment_items = "".join(
            f"""
            <li>
                <div>
                    <strong>{e(comment["display_name"])}</strong>
                    <span>{e(comment["created_at"][:16].replace("T", " "))}</span>
                </div>
                <p>{e(comment["body"])}</p>
            </li>
            """
            for comment in comments
        )
    else:
        comment_items = '<li class="muted compact">No comments yet.</li>'
    return f"""
    <section class="panel trade-comments">
        <div class="panel-heading">
            <h2>Comments</h2>
            <span class="muted">{e(len(comments))}</span>
        </div>
        <ol class="comment-list">{comment_items}</ol>
        <form class="comment-form" method="post" action="/trades/{trade["id"]}/comments">
            <label>Add comment
                <textarea name="body" rows="3" maxlength="2000" placeholder="Add shipping details, questions, or trade notes"></textarea>
            </label>
            <div class="actions">
                <button class="button primary" type="submit">Post comment</button>
            </div>
        </form>
    </section>
    """


def render_trade_feedback(user, trade, feedback_rows):
    if trade["status"] != "completed":
        return ""
    existing = None
    for feedback in feedback_rows:
        if feedback["reviewer_id"] == user["id"]:
            existing = feedback
            break
    reviewee_name = trade["recipient_name"] if trade["proposer_id"] == user["id"] else trade["proposer_name"]
    rating_value = int(existing["rating"]) if existing else 5
    rating_options = "".join(
        f'<option value="{value}" {"selected" if rating_value == value else ""}>{value}/5 - {label}</option>'
        for value, label in (
            (5, "Excellent"),
            (4, "Good"),
            (3, "Okay"),
            (2, "Rough"),
            (1, "Poor"),
        )
    )
    feedback_items = "".join(
        f"""
        <li>
            <div>
                <strong>{e(feedback["reviewer_name"])} rated {e(feedback["reviewee_name"])} {e(feedback_rating_label(feedback["rating"]))}</strong>
                <span>{e(feedback["updated_at"][:16].replace("T", " "))}</span>
            </div>
            {f'<p>{e(feedback["body"])}</p>' if feedback["body"] else '<p class="muted compact">No written comment.</p>'}
        </li>
        """
        for feedback in feedback_rows
    ) or '<li class="muted compact">No feedback has been left for this trade yet.</li>'
    button_label = "Update feedback" if existing else "Save feedback"
    form_title = "Update your feedback" if existing else f"Leave feedback for {reviewee_name}"
    return f"""
    <section class="panel trade-feedback">
        <div class="panel-heading">
            <h2>Trade feedback</h2>
            <span class="muted">{e(len(feedback_rows))}/2 submitted</span>
        </div>
        <ul class="feedback-list">{feedback_items}</ul>
        <form class="feedback-form" method="post" action="/trades/{trade["id"]}/feedback">
            <div class="panel-heading compact-heading">
                <h3>{e(form_title)}</h3>
            </div>
            <label>Rating
                <select name="rating" required>{rating_options}</select>
            </label>
            <label>Comment
                <textarea name="body" rows="3" maxlength="1200" placeholder="Share how the trade went">{e(existing["body"] if existing else "")}</textarea>
            </label>
            <div class="actions">
                <button class="button primary" type="submit">{e(button_label)}</button>
            </div>
        </form>
    </section>
    """


def evidence_size_label(size):
    try:
        size = max(0, int(size or 0))
    except (TypeError, ValueError):
        size = 0
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} bytes"


def render_trade_dispute_evidence_list(dispute):
    evidence_rows = trade_dispute_evidence_rows(dispute["id"])
    if not evidence_rows:
        return '<p class="muted compact">No evidence attachments yet.</p>'
    items = "".join(
        f"""
        <li>
            <a href="/trades/{dispute["trade_id"]}/disputes/{dispute["id"]}/evidence/{evidence["id"]}">{e(evidence["original_filename"])}</a>
            <span class="subtle">{e(evidence_size_label(evidence["file_size"]))} - uploaded by {e(trade_dispute_user_label(evidence, "uploader"))} - {e(evidence["created_at"][:16].replace("T", " "))}</span>
            {f'<span class="subtle">{e(row_value(evidence, "note", ""))}</span>' if row_value(evidence, "note", "") else ""}
        </li>
        """
        for evidence in evidence_rows
    )
    return f'<ul class="stack-list compact-stack evidence-list">{items}</ul>'


def render_trade_dispute_evidence_form(trade_id, dispute_id):
    return f"""
    <form class="dispute-evidence-form" method="post" action="/trades/{trade_id}/disputes/{dispute_id}/evidence" enctype="multipart/form-data">
        <label>Evidence file
            <input type="file" name="evidence_file" accept="image/png,image/jpeg,image/gif,image/webp,application/pdf,text/plain">
        </label>
        <label>Evidence note
            <input name="evidence_note" maxlength="500" placeholder="Optional context for this attachment">
        </label>
        <button class="button secondary small" type="submit">Attach evidence</button>
    </form>
    """


def render_trade_disputes(user, trade, dispute_rows):
    category_options = option_tags(TRADE_DISPUTE_CATEGORY_OPTIONS, "other")
    if dispute_rows:
        dispute_items = "".join(
            f"""
            <li class="dispute-item">
                <div>
                    <strong>{e(trade_dispute_category_label(dispute["category"]))}</strong>
                    <span class="status {e(trade_dispute_status_class(dispute["status"]))}">{e(trade_dispute_status_label(dispute["status"]))}</span>
                    <span class="subtle">Reported by {e(trade_dispute_user_label(dispute, "reporter"))} - {e(dispute["created_at"][:16].replace("T", " "))}</span>
                </div>
                <p>{e(dispute["body"])}</p>
                <div class="evidence-block">
                    <strong>Evidence</strong>
                    {render_trade_dispute_evidence_list(dispute)}
                    {render_trade_dispute_evidence_form(trade["id"], dispute["id"])}
                </div>
                {f'<p class="muted compact"><strong>Admin response:</strong> {e(row_value(dispute, "admin_note", ""))}</p>' if row_value(dispute, "admin_note", "") else ''}
            </li>
            """
            for dispute in dispute_rows
        )
    else:
        dispute_items = '<li class="empty-state compact-empty">No issues have been reported for this trade.</li>'
    return f"""
    <section class="panel trade-disputes">
        <div class="panel-heading">
            <h2>Trade issues</h2>
            <span class="muted">{e(len(dispute_rows))} reported</span>
        </div>
        <ul class="stack-list dispute-list">{dispute_items}</ul>
        <form class="form-grid compact-form embedded-form" method="post" action="/trades/{trade["id"]}/disputes" enctype="multipart/form-data">
            <div class="span-2 panel-heading compact-heading">
                <h3>Report an issue</h3>
            </div>
            <label>Issue type
                <select name="category">{category_options}</select>
            </label>
            <label class="span-2">What happened?
                <textarea name="body" rows="4" maxlength="2000" placeholder="Describe the issue for the admins"></textarea>
            </label>
            <label>Evidence file
                <input type="file" name="evidence_file" accept="image/png,image/jpeg,image/gif,image/webp,application/pdf,text/plain">
            </label>
            <label>Evidence note
                <input name="evidence_note" maxlength="500" placeholder="Optional context for the attachment">
            </label>
            <div class="form-actions span-2">
                <button class="button secondary" type="submit">Send issue report</button>
            </div>
        </form>
    </section>
    """


def render_trade_detail(user, trade_id, notice=None, status="info"):
    trade = trade_detail_for_user(trade_id, user["id"])
    if not trade:
        return None
    offered = trade_item_rows(trade_id, "offered")
    requested = trade_item_rows(trade_id, "requested")
    comments = trade_comment_rows(trade_id)
    feedback = trade_feedback_rows(trade_id)
    disputes = trade_dispute_rows(trade_id)
    offered_html = render_trade_items(offered)
    requested_html = render_trade_items(requested)
    trade_links = render_trade_links(user, trade)
    comments_html = render_trade_comments(trade, comments)
    feedback_html = render_trade_feedback(user, trade, feedback)
    disputes_html = render_trade_disputes(user, trade, disputes)
    trade_entries_offered = [(item, item["quantity"]) for item in offered]
    trade_entries_requested = [(item, item["quantity"]) for item in requested]
    fairness = trade_fairness_assessment(trade_entries_offered, trade_entries_requested)
    fairness_notice = render_trade_fairness_notice(
        trade_entries_offered,
        trade_entries_requested,
        include_acknowledgement=trade["status"] == "pending" and trade["recipient_id"] == user["id"],
    )
    accept_disabled = " disabled" if fairness["severity"] == "blocked" else ""
    one_way_warning = ""
    if trade["status"] == "pending" and trade["recipient_id"] == user["id"] and bool(offered) != bool(requested):
        if offered:
            warning_text = f'{trade["proposer_name"]} is offering cards without requesting any cards from you.'
        else:
            warning_text = f'{trade["proposer_name"]} is requesting cards without offering any cards in return.'
        one_way_warning = f"""
        <div class="trade-warning" role="alert">
            <strong>One-directional trade</strong>
            <p>{e(warning_text)} Review this carefully before accepting.</p>
        </div>
        """
    actions = ""
    if trade["status"] == "pending" and trade["recipient_id"] == user["id"]:
        actions = f"""
        <form class="response-box" method="post" action="/trades/{trade["id"]}/respond">
            {fairness_notice}
            <label>Response note
                <textarea name="response_note" rows="3" maxlength="1200"></textarea>
            </label>
            <div class="actions">
                <button class="button primary" name="decision" value="accepted" type="submit"{accept_disabled}>Accept</button>
                <button class="button danger" name="decision" value="declined" type="submit">Decline</button>
                <a class="button secondary" href="/trades/{trade["id"]}/counter">Counter offer</a>
            </div>
        </form>
        """
    elif trade["status"] == "pending" and trade["proposer_id"] == user["id"]:
        actions = f"""
        <form method="post" action="/trades/{trade["id"]}/cancel">
            <button class="button danger" type="submit">Cancel trade</button>
        </form>
        """
    elif trade["status"] == "accepted":
        actions = f"""
        <form method="post" action="/trades/{trade["id"]}/complete">
            <button class="button primary" type="submit">Mark complete</button>
        </form>
        """
    content = f"""
    {render_trades_subnav("offers")}
    <section class="section-heading">
        <div>
            <p class="eyebrow">Trade #{trade["id"]}</p>
            <h1>{e(trade["proposer_name"])} with {e(trade["recipient_name"])}</h1>
        </div>
        <span class="status {e(trade["status"])}">{e(TRADE_STATUS_LABELS.get(trade["status"], trade["status"]))}</span>
    </section>
    {render_workspace_nav([
        ("#trade-response", "Status and response", "Notes, warnings, and available actions"),
        ("#trade-cards", "Cards", "Review both sides of the trade"),
        ("#trade-issues", "Issues", "Report or review a problem"),
        ("#trade-feedback", "Feedback", "Leave feedback after completion"),
        ("#trade-comments", "Comments", "Continue the trade conversation"),
    ], label="Trade details")}
    <section id="trade-response" class="panel trade-response-panel">
        <div class="panel-heading">
            <div>
                <p class="eyebrow">Current status</p>
                <h2>{e(TRADE_STATUS_LABELS.get(trade["status"], trade["status"]))}</h2>
            </div>
            <span class="status {e(trade["status"])}">{e(TRADE_STATUS_LABELS.get(trade["status"], trade["status"]))}</span>
        </div>
        {trade_links}
        <div class="trade-link-panel compact-trade-link-panel">
            <strong>Price basis: {e(price_basis_label(row_value(trade, "price_source_preference", "")))}</strong>
        </div>
        <div class="note-log">
            <p><strong>Offer note:</strong> {e(trade["proposer_note"] or "No note.")}</p>
            <p><strong>Response note:</strong> {e(trade["response_note"] or "No response yet.")}</p>
        </div>
        {fairness_notice if not (trade["status"] == "pending" and trade["recipient_id"] == user["id"]) else ""}
        {one_way_warning}
        {actions}
    </section>
    <section class="trade-detail-grid" id="trade-cards">
        <article class="panel">
            <h2>{e(trade["proposer_name"])} offers</h2>
            {offered_html}
        </article>
        <article class="panel">
            <h2>{e(trade["proposer_name"])} requests</h2>
            {requested_html}
        </article>
    </section>
    {render_trade_value_panel(trade_entries_offered, trade_entries_requested, "Offer value", "Request value")}
    <div id="trade-issues">{disputes_html}</div>
    <div id="trade-feedback">{feedback_html}</div>
    <div id="trade-comments">{comments_html}</div>
    """
    return render_layout(user, f"Trade #{trade_id}", content, active="trades", notice=notice, status=status)


def render_trade_item_photo_gallery(trade_id, trade_item_id):
    photos = trade_item_photo_rows(trade_item_id)
    if not photos:
        return ""
    rendered = "".join(
        f"""
        <a class="trade-card-photo" href="/trades/{trade_id}/photos/{photo["id"]}" target="_blank" rel="noreferrer">
            <img src="/trades/{trade_id}/photos/{photo["id"]}" alt="{e(photo["caption"] or photo["original_filename"])}">
            {f'<span>{e(photo["caption"])}</span>' if photo["caption"] else ''}
        </a>
        """
        for photo in photos
    )
    return f'<div class="card-photo-gallery compact-gallery">{rendered}</div>'


def render_trade_items(items):
    if not items:
        return '<div class="empty-state compact-empty">No cards selected.</div>'
    rendered = []
    for item in items:
        unit_price = trade_item_price_usd(item)
        price_line = ""
        if unit_price:
            price_line = f'<span class="trade-item-price">{e(money(price_to_cents(unit_price) * item["quantity"]))} value - {e(price_source_label(trade_item_price_source(item)))}</span>'
        condition_notes = row_value(item, "condition_notes", "")
        photo_gallery = render_trade_item_photo_gallery(item["trade_id"], item["id"])
        rendered.append(
            f"""
        <li>
            <strong>{e(item["quantity"])} x {e(item["card_name"])}</strong>
            <span>{e(item["set_name"] or "Any set")} - {e(item["condition"] or "Condition n/a")} - {e(item["finish"] or "Finish n/a")}</span>
            {f'<span class="condition-detail"><strong>Condition details:</strong> {e(condition_notes)}</span>' if condition_notes else ''}
            {photo_gallery}
            {price_line}
        </li>
        """
        )
    return "<ul class=\"trade-item-list\">" + "".join(rendered) + "</ul>"


__all__ = ['render_trades', 'render_trade_table', 'trade_picker_query_inputs', 'trade_quantity_map', 'trade_selected_quantities_from_form', 'trade_price_basis_for', 'price_for_item_basis', 'apply_trade_price_basis', 'trade_selected_items', 'trade_item_price_usd', 'trade_item_price_source', 'trade_entry_value_cents', 'trade_entries_value_cents', 'trade_entries_unpriced_count', 'trade_value_gap_percent', 'trade_balance_details', 'trade_fairness_assessment', 'render_trade_fairness_notice', 'validate_trade_fairness_for_send', 'validate_trade_fairness_for_creation', 'render_trade_value_panel', 'render_trade_selected_hidden_inputs', 'render_trade_selection_list', 'render_trade_live_summary', 'render_counter_context_panel', 'render_trade_review', 'render_trade_picker_section', 'render_new_trade', 'trade_picker_table', 'render_trade_links', 'render_trade_comments', 'render_trade_feedback', 'render_trade_disputes', 'render_trade_detail', 'render_trade_item_photo_gallery', 'render_trade_items']
