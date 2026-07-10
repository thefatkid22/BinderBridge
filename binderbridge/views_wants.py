"""Wishlist, want form, and trade matchmaking views.

This module is wired by binderbridge.views; shared app helpers are injected at runtime.
"""

from binderbridge.want_queries import *
from binderbridge.matchmaking_queries import *


def default_want_item():
    item = {
        "game": "mtg",
        "card_name": "",
        "set_name": "",
        "set_code": "",
        "collector_number": "",
        "desired_quantity": 1,
        "priority": "normal",
        "budget_cap_usd": "",
        "condition": "",
        "finish": "",
        "language": "",
        "preferred_printing_notes": "",
        "notes": "",
        "lookup_on_save": "1",
        "scryfall_finish_override": "",
        "price_source": "",
        "is_public": 1,
        "visibility": VISIBILITY_MEMBERS,
    }
    for field in SCRYFALL_COLLECTION_FIELDS:
        item[field] = ""
    return item


def split_preference_values(value):
    values = []
    for part in str(value or "").split(","):
        cleaned = part.strip()
        if cleaned and cleaned not in values:
            values.append(cleaned)
    return values


def normalize_want_preference_values(values, options, normalizer):
    if not isinstance(values, (list, tuple)):
        values = [values]
    normalized = []
    for raw_value in values:
        for fragment in sanitize_text_input(raw_value, max_length=200).split(","):
            cleaned = fragment.strip()
            if not cleaned:
                continue
            choice = normalizer(cleaned)
            if choice in options and choice not in normalized:
                normalized.append(choice)
    ordered = [option for option in options if option in normalized]
    return ",".join(ordered)


def prepare_want_draft(draft=None):
    item = dict(default_want_item())
    item.update(dict(draft or {}))
    for field in SCRYFALL_COLLECTION_FIELDS:
        item.setdefault(field, "")
    item.setdefault("condition", "")
    item.setdefault("finish", "")
    item.setdefault("language", "")
    item.setdefault("priority", "normal")
    item.setdefault("budget_cap_usd", "")
    item.setdefault("preferred_printing_notes", "")
    item.setdefault("price_source", "scryfall" if item.get("price_usd") and (item.get("scryfall_id") or item.get("scryfall_uri")) else "")
    item.setdefault("lookup_on_save", "1")
    return item


def validate_want_form(form):
    card_name = sanitize_text_input(form.get("card_name", [""])[0], max_length=160).strip()
    game = form.get("game", ["mtg"])[0].strip() or "mtg"
    set_name = sanitize_text_input(form.get("set_name", [""])[0], max_length=120).strip()
    set_code = sanitize_text_input(form.get("set_code", [""])[0], max_length=20).strip().upper()
    collector_number = sanitize_text_input(form.get("collector_number", [""])[0], max_length=40).strip()
    priority = normalize_want_priority(form.get("priority", ["normal"])[0])
    raw_budget_cap = sanitize_text_input(form.get("budget_cap_usd", [""])[0], max_length=24).strip()
    budget_cap_usd = normalize_price_usd(raw_budget_cap)
    if raw_budget_cap and not budget_cap_usd:
        raise ValueError("Budget cap must be a valid non-negative dollar amount.")
    preferred_printing_notes = sanitize_text_input(form.get("preferred_printing_notes", [""])[0], max_length=1000).strip()
    notes = sanitize_text_input(form.get("notes", [""])[0], max_length=1000).strip()
    desired_quantity = max(1, clamp_quantity(form.get("desired_quantity", ["1"])[0], 1))
    if not card_name:
        raise ValueError("Card name is required.")
    if game not in dict(CARD_GAMES):
        game = "other"
    data = {
        "game": game,
        "card_name": card_name,
        "set_name": set_name,
        "set_code": set_code,
        "collector_number": collector_number,
        "desired_quantity": desired_quantity,
        "priority": priority,
        "budget_cap_usd": budget_cap_usd,
        "condition": normalize_want_preference_values(form.get("condition", []), CONDITION_OPTIONS, normalize_condition),
        "finish": normalize_want_preference_values(form.get("finish", []), FINISH_OPTIONS, normalize_finish),
        "language": normalize_want_preference_values(form.get("language", []), LANGUAGE_OPTIONS, normalize_language),
        "preferred_printing_notes": preferred_printing_notes,
        "notes": notes,
        "visibility": form_visibility(form),
        "lookup_on_save": "1" if form.get("lookup_on_save", [""])[0] == "1" else "",
        "scryfall_finish_override": "1" if form.get("scryfall_finish_override", [""])[0] == "1" else "",
        "selected_scryfall_id": sanitize_text_input(form.get("selected_scryfall_id", [""])[0], max_length=80).strip(),
    }
    data["is_public"] = visibility_to_public_flag(data["visibility"])
    for field in SCRYFALL_COLLECTION_FIELDS:
        data[field] = sanitize_text_input(form.get(field, [""])[0], max_length=5000).strip()
    data["price_usd"] = normalize_price_usd(data.get("price_usd", ""))
    data["price_source"] = "scryfall" if data["price_usd"] else ""
    return data


def insert_want_item(user_id, data):
    return execute(
        """
        INSERT INTO want_items
            (user_id, game, card_name, set_name, set_code, collector_number, desired_quantity,
             priority, budget_cap_usd, condition, finish, language,
             scryfall_id, image_url, mana_cost, type_line, oracle_text, rarity, colors, color_identity,
             scryfall_uri, price_usd, price_source, preferred_printing_notes, notes, is_public, visibility, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            data.get("game", "mtg"),
            data.get("card_name", ""),
            data.get("set_name", ""),
            data.get("set_code", ""),
            data.get("collector_number", ""),
            data.get("desired_quantity", 1),
            normalize_want_priority(data.get("priority", "normal")),
            normalize_price_usd(data.get("budget_cap_usd", "")),
            data.get("condition", ""),
            data.get("finish", ""),
            data.get("language", ""),
            data.get("scryfall_id", ""),
            data.get("image_url", ""),
            data.get("mana_cost", ""),
            data.get("type_line", ""),
            data.get("oracle_text", ""),
            data.get("rarity", ""),
            data.get("colors", ""),
            data.get("color_identity", ""),
            data.get("scryfall_uri", ""),
            normalize_price_usd(data.get("price_usd", "")),
            data.get("price_source", ""),
            data.get("preferred_printing_notes", ""),
            data.get("notes", ""),
            1 if data.get("is_public", 1) else 0,
            normalize_visibility(data.get("visibility", ""), default=VISIBILITY_MEMBERS if data.get("is_public", 1) else VISIBILITY_PRIVATE),
            now_iso(),
            now_iso(),
        ),
    )


def update_want_item(user_id, want_id, data):
    with db() as conn:
        cursor = conn.execute(
            """
            UPDATE want_items
            SET game = ?, card_name = ?, set_name = ?, set_code = ?, collector_number = ?,
                desired_quantity = ?, priority = ?, budget_cap_usd = ?, condition = ?, finish = ?, language = ?,
                scryfall_id = ?, image_url = ?, mana_cost = ?, type_line = ?, oracle_text = ?,
                rarity = ?, colors = ?, color_identity = ?, scryfall_uri = ?, price_usd = ?,
                price_source = ?, preferred_printing_notes = ?, notes = ?, is_public = ?, visibility = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                data.get("game", "mtg"),
                data.get("card_name", ""),
                data.get("set_name", ""),
                data.get("set_code", ""),
                data.get("collector_number", ""),
                data.get("desired_quantity", 1),
                normalize_want_priority(data.get("priority", "normal")),
                normalize_price_usd(data.get("budget_cap_usd", "")),
                data.get("condition", ""),
                data.get("finish", ""),
                data.get("language", ""),
                data.get("scryfall_id", ""),
                data.get("image_url", ""),
                data.get("mana_cost", ""),
                data.get("type_line", ""),
                data.get("oracle_text", ""),
                data.get("rarity", ""),
                data.get("colors", ""),
                data.get("color_identity", ""),
                data.get("scryfall_uri", ""),
                normalize_price_usd(data.get("price_usd", "")),
                data.get("price_source", ""),
                data.get("preferred_printing_notes", ""),
                data.get("notes", ""),
                1 if data.get("is_public", 1) else 0,
                normalize_visibility(data.get("visibility", ""), default=VISIBILITY_MEMBERS if data.get("is_public", 1) else VISIBILITY_PRIVATE),
                now_iso(),
                want_id,
                user_id,
            ),
        )
        return cursor.rowcount


def insert_selected_want_items(user_id, data, selected_scryfall_ids):
    clean_ids = []
    seen = set()
    for value in selected_scryfall_ids:
        selected_id = str(value or "").strip()
        if not selected_id or selected_id in seen:
            continue
        clean_ids.append(selected_id)
        seen.add(selected_id)
    if not clean_ids:
        raise ValueError("Choose at least one printing or variant to add.")
    inserted = 0
    for selected_id in clean_ids:
        enriched = apply_scryfall_data(data, selected_scryfall_card_data(selected_id))
        enriched["lookup_on_save"] = "1"
        ensure_scryfall_finish_allowed(
            enriched,
            allow_override=data.get("scryfall_finish_override") == "1",
            finish_values=enriched.get("finish", ""),
        )
        insert_want_item(user_id, enriched)
        inserted += 1
    return inserted




def match_entry_quantity(row_data):
    try:
        desired = int(row_value(row_data, "desired_quantity", 1) or 1)
    except (TypeError, ValueError):
        desired = 1
    try:
        available = int(row_value(row_data, "quantity_for_trade", 0) or 0)
    except (TypeError, ValueError):
        available = 0
    return max(0, min(desired, available))


def trade_match_entry_from_row(row_data):
    quantity = match_entry_quantity(row_data)
    entry = {
        "collection_item_id": row_data["collection_item_id"],
        "want_id": row_data["want_id"],
        "card_name": row_data["card_name"],
        "set_name": row_value(row_data, "set_name", ""),
        "set_code": row_value(row_data, "set_code", ""),
        "collector_number": row_value(row_data, "collector_number", ""),
        "condition": row_value(row_data, "condition", ""),
        "finish": row_value(row_data, "finish", ""),
        "language": row_value(row_data, "language", ""),
        "price_usd": normalize_price_usd(row_value(row_data, "price_usd", "")),
        "image_url": row_value(row_data, "image_url", ""),
        "type_line": row_value(row_data, "type_line", ""),
        "priority": row_value(row_data, "want_priority", "normal"),
        "budget_cap_usd": normalize_price_usd(row_value(row_data, "want_budget_cap_usd", "")),
        "preferred_printing_notes": row_value(row_data, "want_preferred_printing_notes", ""),
        "quantity": quantity,
    }
    entry["value_cents"] = price_to_cents(entry["price_usd"]) * quantity
    entry["unpriced"] = 0 if entry["price_usd"] else quantity
    entry["priority_rank"] = want_priority_rank(entry["priority"])
    entry["within_budget"] = bool(
        entry["budget_cap_usd"]
        and entry["price_usd"]
        and price_to_cents(entry["price_usd"]) <= price_to_cents(entry["budget_cap_usd"])
    )
    return entry


def add_trade_match_entry(match, side, row_data):
    quantity = match_entry_quantity(row_data)
    if quantity <= 0:
        return
    collection_item_id = row_data["collection_item_id"]
    entries = match[f"{side}_entry_map"]
    current = entries.get(collection_item_id)
    if current and current["quantity"] >= quantity:
        return
    entries[collection_item_id] = trade_match_entry_from_row(row_data)


def sorted_trade_match_entries(entries):
    return sorted(
        entries,
        key=lambda item: (
            -int(item["priority_rank"] or 0),
            0 if item["within_budget"] else 1,
            -int(item["value_cents"] or 0),
            item["card_name"].lower(),
            item["set_name"].lower(),
        ),
    )


def finalize_trade_match(match):
    they_have = sorted_trade_match_entries(match.pop("they_have_entry_map").values())
    they_want = sorted_trade_match_entries(match.pop("they_want_entry_map").values())
    match["they_have_cards"] = they_have
    match["they_want_cards"] = they_want
    match["they_have_count"] = sum(item["quantity"] for item in they_have)
    match["they_want_count"] = sum(item["quantity"] for item in they_want)
    match["they_have_value_cents"] = sum(item["value_cents"] for item in they_have)
    match["they_want_value_cents"] = sum(item["value_cents"] for item in they_want)
    match["unpriced_count"] = sum(item["unpriced"] for item in they_have) + sum(item["unpriced"] for item in they_want)
    match["highest_priority_rank"] = max((item["priority_rank"] for item in they_have + they_want), default=0)
    match["within_budget_count"] = sum(item["quantity"] for item in they_have + they_want if item["within_budget"])
    offered = [(item, item["quantity"]) for item in they_want]
    requested = [(item, item["quantity"]) for item in they_have]
    match["balance"] = trade_balance_details(offered, requested)
    match["mutual_quantity"] = min(match["they_have_count"], match["they_want_count"])
    return match




def trade_matchmaking_results(user_id):
    matches = {}
    they_have_rows, they_want_rows = trade_matchmaking_candidate_rows(user_id)
    for row_data in they_have_rows:
        member_id = row_data["member_id"]
        match = matches.setdefault(
            member_id,
            {
                "member_id": member_id,
                "username": row_data["username"],
                "display_name": row_data["display_name"],
                "they_have_entry_map": {},
                "they_want_entry_map": {},
            },
        )
        add_trade_match_entry(match, "they_have", row_data)
    for row_data in they_want_rows:
        member_id = row_data["member_id"]
        match = matches.setdefault(
            member_id,
            {
                "member_id": member_id,
                "username": row_data["username"],
                "display_name": row_data["display_name"],
                "they_have_entry_map": {},
                "they_want_entry_map": {},
            },
        )
        add_trade_match_entry(match, "they_want", row_data)
    finalized = [
        finalize_trade_match(match)
        for match in matches.values()
        if match["they_have_entry_map"] and match["they_want_entry_map"]
    ]
    return sorted(
        finalized,
        key=lambda item: (
            -item["highest_priority_rank"],
            -item["within_budget_count"],
            -item["mutual_quantity"],
            abs(item["balance"]["difference"]),
            -item["they_have_value_cents"] - item["they_want_value_cents"],
            item["display_name"].lower(),
        ),
    )


def trade_matchmaking_prefill_url(match, max_items=8):
    query = {"recipient_id": [str(match["member_id"])]}
    for entry in match["they_want_cards"][:max_items]:
        query[f"offer_{entry['collection_item_id']}"] = [str(entry["quantity"])]
    for entry in match["they_have_cards"][:max_items]:
        query[f"request_{entry['collection_item_id']}"] = [str(entry["quantity"])]
    return f"/trades/new?{urlencode(query, doseq=True)}"


def render_trade_match_card_list(title, entries, empty_text):
    if not entries:
        return f"""
        <div class="match-card-list">
            <strong>{e(title)}</strong>
            <p class="muted compact">{e(empty_text)}</p>
        </div>
        """
    items = "".join(
        f"""
        <li>
            <span>{e(entry["quantity"])}x</span>
            <div>
                <strong>{e(entry["card_name"])}</strong>
                <span>{e(entry["set_name"] or "Any set")} - {e(entry["condition"] or "Condition n/a")} - {e(entry["finish"] or "Finish n/a")}</span>
                <span>{e(want_priority_label(entry["priority"]))} priority{f' - within ${e(entry["budget_cap_usd"])} budget' if entry["within_budget"] else ''}</span>
                {f'<span>{e(entry["preferred_printing_notes"])}</span>' if entry["preferred_printing_notes"] else ''}
            </div>
            <span>{e(money(entry["value_cents"]))}</span>
        </li>
        """
        for entry in entries[:4]
    )
    more = len(entries) - 4
    more_text = f'<p class="muted compact">+{more} more matched card{"s" if more != 1 else ""}</p>' if more > 0 else ""
    return f"""
    <div class="match-card-list">
        <strong>{e(title)}</strong>
        <ul>{items}</ul>
        {more_text}
    </div>
    """


def render_trade_match_card(match):
    balance = match["balance"]
    prefill_url = trade_matchmaking_prefill_url(match)
    unpriced = f'<span class="pill">{e(match["unpriced_count"])} unpriced</span>' if match["unpriced_count"] else ""
    return f"""
    <article class="trade-match-card">
        <div class="trade-match-head">
            <div>
                <h2>{e(match["display_name"])}</h2>
                <span class="subtle">@{e(match["username"])}</span>
            </div>
            <div class="match-score">
                <strong>{e(match["mutual_quantity"])}</strong>
                <span>mutual cards</span>
            </div>
        </div>
        <div class="match-metrics">
            <div><strong>{e(match["they_have_count"])}</strong><span>cards you want</span></div>
            <div><strong>{e(money(match["they_have_value_cents"]))}</strong><span>request value</span></div>
            <div><strong>{e(match["they_want_count"])}</strong><span>cards they want</span></div>
            <div><strong>{e(money(match["they_want_value_cents"]))}</strong><span>offer value</span></div>
        </div>
        <div class="trade-match-balance {e(balance["tone"])}">
            <strong>{e(balance["label"])}</strong>
            <span>{e(balance["detail"])}</span>
            {unpriced}
        </div>
        <div class="trade-match-lists">
            {render_trade_match_card_list("They have cards you want", match["they_have_cards"], "No matched trade cards.")}
            {render_trade_match_card_list("They want cards you have", match["they_want_cards"], "No matched wants.")}
        </div>
        <div class="inline-actions">
            <a class="button primary" href="{e(prefill_url)}">Start matched trade</a>
            <a class="button secondary" href="/members/{match["member_id"]}">Open binder</a>
        </div>
    </article>
    """


def render_trade_matchmaking(user, query=None, notice=None, status="info"):
    query = query or {}
    matches = trade_matchmaking_results(user["id"])
    total_count = len(matches)
    page, per_page, page_count, offset = pagination_state(query, total_count, default_per_page=10)
    page_matches = matches[offset:offset + per_page]
    match_cards = "".join(render_trade_match_card(match) for match in page_matches)
    if not match_cards:
        match_cards = """
        <div class="empty-state">
            No mutual trade matches yet. Add wanted cards and mark collection cards for trade to improve matchmaking.
        </div>
        """
    pagination = render_pagination("/trades/matches", query, total_count, page, per_page, page_count)
    content = f"""
    {render_trades_subnav("matches")}
    <section class="section-heading">
        <div>
            <p class="eyebrow">Trades</p>
            <h1>Trade matchmaking</h1>
            <p class="lead">Find users who have cards from your wishlist and publicly want cards you have available for trade.</p>
        </div>
        <div class="actions">
            <a class="button secondary" href="/trades">Back to trades</a>
            <a class="button secondary" href="/browse">Browse all cards</a>
        </div>
    </section>
    <section class="matchmaking-grid">{match_cards}</section>
    {pagination}
    """
    return render_layout(user, "Trade matchmaking", content, active="trades", notice=notice, status=status)


def want_list_filter_chip_specs():
    return (
        {"key": "q", "label": "Search"},
        {"key": "priority", "label": "Priority", "formatter": want_priority_label},
        {"key": "game", "label": "Game", "formatter": game_label},
        {"key": "visibility", "label": "Visibility", "formatter": lambda value: VISIBILITY_LABELS.get(value, value.title())},
        {"key": "matched_only", "label": "Trade matches only", "standalone": True},
    )


def render_preference_summary(want):
    preferences = []
    condition_values = split_preference_values(row_value(want, "condition", ""))
    finish_values = split_preference_values(row_value(want, "finish", ""))
    language_values = split_preference_values(row_value(want, "language", ""))
    if condition_values:
        preferences.append(f"Condition: {', '.join(condition_values)}")
    if finish_values:
        preferences.append(f"Finish: {', '.join(finish_values)}")
    if language_values:
        preferences.append(f"Language: {', '.join(language_values)}")
    return (
        f'<p class="want-preferences">Prefers {e("; ".join(preferences))}</p>'
        if preferences
        else '<p class="want-preferences muted compact">No condition, finish, or language preference</p>'
    )


def preference_checkbox_group(name, label, options, selected_values):
    selected_values = set(split_preference_values(selected_values))
    select_all_checked = len(selected_values) == len(options)
    checkboxes = "".join(
        f"""
        <label class="checkbox-line preference-option">
            <input type="checkbox" name="{e(name)}" value="{e(option)}"{checked(option in selected_values)} data-preference-option>
            {e(option)}
        </label>
        """
        for option in options
    )
    return f"""
    <fieldset class="preference-checks" data-preference-group>
        <legend>{e(label)}</legend>
        <label class="checkbox-line preference-select-all">
            <input type="checkbox"{checked(select_all_checked)} data-preference-select-all>
            Select all
        </label>
        <div class="preference-option-grid">
            {checkboxes}
        </div>
    </fieldset>
    """


def render_preference_select_all_script():
    return """
    <script>
        (function () {
            document.querySelectorAll("[data-preference-select-all]").forEach(function (toggle) {
                var group = toggle.closest("[data-preference-group]");
                if (!group) return;
                var options = Array.prototype.slice.call(group.querySelectorAll("[data-preference-option]"));
                function syncToggle() {
                    toggle.checked = options.length > 0 && options.every(function (option) { return option.checked; });
                    toggle.indeterminate = options.some(function (option) { return option.checked; }) && !toggle.checked;
                }
                toggle.addEventListener("change", function () {
                    options.forEach(function (option) { option.checked = toggle.checked; });
                });
                options.forEach(function (option) {
                    option.addEventListener("change", syncToggle);
                });
                syncToggle();
            });
        })();
    </script>
    """


def render_want_form(
    draft,
    action,
    title,
    submit_label,
    scryfall_results=None,
    scryfall_picker_intent="use_scryfall",
    scryfall_picker_label="Use selected want",
    scryfall_picker_title="Scryfall matches",
    scryfall_picker_multiple=False,
    cancel_href="",
    form_classes="panel form-grid compact-form",
):
    draft = prepare_want_draft(draft)
    cancel_link = f'<a class="button ghost" href="{e(cancel_href)}">Cancel edit</a>' if cancel_href else ""
    hidden_scryfall_fields = "".join(
        f'<input type="hidden" name="{field}" value="{e(draft[field])}">'
        for field in SCRYFALL_COLLECTION_FIELDS
    ) + f'<input type="hidden" name="price_source" value="{e(draft["price_source"])}">'
    if scryfall_picker_multiple and draft.get("selected_scryfall_id"):
        hidden_scryfall_fields += f'<input type="hidden" name="selected_scryfall_id" value="{e(draft["selected_scryfall_id"])}">'
    preview = render_scryfall_preview(draft)
    result_options = render_scryfall_result_picker(
        scryfall_results,
        button_label=scryfall_picker_label,
        intent=scryfall_picker_intent,
        title=scryfall_picker_title,
        multiple=scryfall_picker_multiple,
    )
    condition_checks = preference_checkbox_group("condition", "Desired condition", CONDITION_OPTIONS, draft["condition"])
    finish_checks = preference_checkbox_group("finish", "Desired finish", FINISH_OPTIONS, draft["finish"])
    language_checks = preference_checkbox_group("language", "Desired language", LANGUAGE_OPTIONS, draft["language"])
    return f"""
    <form class="{e(form_classes)}" method="post" action="{e(action)}">
        {hidden_scryfall_fields}
        <div class="span-2 panel-heading">
            <h2>{e(title)}</h2>
            {cancel_link}
        </div>
        <label>Card name
            <input required name="card_name" maxlength="160" value="{e(draft["card_name"])}">
        </label>
        <label>Game
            <select name="game">{option_tags(CARD_GAMES, draft["game"])}</select>
        </label>
        <label>Set
            <input name="set_name" maxlength="120" value="{e(draft["set_name"])}">
        </label>
        <label>Set code
            <input name="set_code" maxlength="20" value="{e(draft["set_code"])}">
        </label>
        <label>Collector number
            <input name="collector_number" maxlength="40" value="{e(draft["collector_number"])}">
        </label>
        <label>Desired qty
            <input required type="number" min="1" name="desired_quantity" value="{e(draft["desired_quantity"])}">
        </label>
        <label>Priority
            <select name="priority">{option_tags(WANT_PRIORITY_OPTIONS, draft["priority"])}</select>
        </label>
        <label>Per-copy budget cap
            <input name="budget_cap_usd" inputmode="decimal" maxlength="24" placeholder="Optional" value="{e(draft["budget_cap_usd"])}">
        </label>
        {condition_checks}
        {finish_checks}
        {language_checks}
        <label class="span-2">Preferred-printing notes
            <textarea name="preferred_printing_notes" rows="3" maxlength="1000" placeholder="Preferred art, border, frame, promo, or other printing details">{e(draft["preferred_printing_notes"])}</textarea>
        </label>
        <label class="span-2">Notes
            <textarea name="notes" rows="3" maxlength="1000">{e(draft["notes"])}</textarea>
        </label>
        {visibility_checkbox(draft)}
        {preview}
        {result_options}
        <div class="lookup-controls span-2">
            <label class="checkbox-line">
                <input type="checkbox" name="lookup_on_save" value="1"{checked(draft["lookup_on_save"] == "1")}>
                Scryfall lookup on save
            </label>
            <label class="checkbox-line">
                <input type="checkbox" name="scryfall_finish_override" value="1"{checked(draft["scryfall_finish_override"] == "1")}>
                Override Scryfall finish check
            </label>
            <button class="button secondary" name="intent" value="lookup" type="submit">Search Scryfall</button>
        </div>
        <div class="form-actions span-2">
            <button class="button primary" name="intent" value="save" type="submit">{e(submit_label)}</button>
        </div>
    </form>
    """


def render_want_share_link_row(want, link):
    revoked = bool(row_value(link, "revoked_at", ""))
    expired = bool(row_value(link, "expires_at", "") and row_value(link, "expires_at", "") <= now_iso())
    state = "Revoked" if revoked else "Expired" if expired else "Active"
    state_class = "declined" if revoked or expired else "accepted"
    revoke_form = ""
    if not revoked:
        revoke_form = f"""
        <form method="post" action="/wants/{want["id"]}/share-links/{link["id"]}/revoke">
                <button class="button danger small" type="submit" data-confirm="Revoke this private wanted-card link?">Revoke</button>
        </form>
        """
    expires = row_value(link, "expires_at", "")
    return f"""
    <li>
        <div>
            <strong>{e(link["label"])}</strong>
            <span>Token ending {e(link["token_hint"])} - {e("expires " + expires[:10] if expires else "no expiration")}</span>
            <small>{e("Value shown" if link["show_values"] else "Value hidden")}{e(" - last opened " + link["last_accessed_at"][:10] if link["last_accessed_at"] else "")}</small>
        </div>
        <div class="actions"><span class="status {state_class}">{e(state)}</span>{revoke_form}</div>
    </li>
    """


def render_want_share_panel(user_id, want, share_result=None):
    links = want_share_link_rows(user_id, want["id"])
    link_rows = "".join(render_want_share_link_row(want, link) for link in links)
    link_rows = link_rows or '<li class="muted compact">No private wanted-card links yet.</li>'
    result_panel = ""
    if share_result:
        result_panel = f"""
        <div class="notice success span-2">
            <strong>Copy this private link now</strong>
            <input readonly value="{e(share_result["url"])}" onclick="this.select()">
        </div>
        """
    if record_visibility(want) == VISIBILITY_LINK:
        create_form = f"""
        <form class="form-grid compact-form" method="post" action="/wants/{want["id"]}/share-links">
            <div class="span-2 panel-heading">
                <div><h2>Private wanted-card links</h2><p class="muted compact">Share only this wanted card without exposing the rest of your wishlist.</p></div>
            </div>
            <label>Label<input name="label" maxlength="80" placeholder="Local trade request"></label>
            <label>Expires<select name="expires_days"><option value="7">In 7 days</option><option value="30" selected>In 30 days</option><option value="90">In 90 days</option><option value="0">Never</option></select></label>
            <label class="checkbox-line span-2"><input type="checkbox" name="show_values" value="1"> Show Scryfall value</label>
            {result_panel}
            <div class="form-actions span-2"><button class="button primary" type="submit">Create private link</button></div>
        </form>
        """
    else:
        create_form = """
        <div class="panel-heading">
            <div>
                <h2>Private wanted-card links</h2>
                <p class="muted compact">Choose Share-link only above and save this wanted card before creating a private link.</p>
            </div>
        </div>
        """
    return f"""
    <section class="want-share-panel" id="want-share-links-{want["id"]}">
        {create_form}
        <ul class="stack-list compact-stack">{link_rows}</ul>
    </section>
    """


def shared_want_from_link(link):
    return {key[5:]: value for key, value in link.items() if key.startswith("want_")}


def render_shared_want_card(link, token):
    want = shared_want_from_link(link)
    show_values = bool(link["show_values"])
    image = (
        f'<img class="want-image" src="{e(want["image_url"])}" alt="" referrerpolicy="no-referrer">'
        if want.get("image_url")
        else '<span class="want-image placeholder"></span>'
    )
    metadata = [game_label(want.get("game", "other"))]
    if want.get("set_name"):
        metadata.append(want["set_name"])
    if want.get("set_code"):
        metadata.append(want["set_code"])
    if want.get("collector_number"):
        metadata.append(f"#{want['collector_number']}")
    priority = normalize_want_priority(want.get("priority", "normal"))
    budget_cap = normalize_price_usd(want.get("budget_cap_usd", ""))
    preferred_printing = want.get("preferred_printing_notes", "")
    notes = want.get("notes", "")
    scryfall_link = (
        f'<a href="{e(want["scryfall_uri"])}" target="_blank" rel="noreferrer">Open on Scryfall</a>'
        if want.get("scryfall_uri")
        else ""
    )
    content = f"""
    <section class="section-heading shared-want-heading">
        <div><p class="eyebrow">Wanted by {e(link["owner_name"])}</p><h1>{e(want["card_name"])}</h1></div>
        <span class="pill">Private wanted-card link</span>
    </section>
    <article class="panel want-card shared-want-card">
        {image}
        <div class="want-main">
            <div class="want-title-row">
                <div><h2>{e(want["card_name"])}</h2><p>{e(" - ".join(metadata))}</p></div>
                <div class="want-card-badges">
                    <span class="pill want-priority priority-{e(priority)}">{e(want_priority_label(priority))}</span>
                    <span class="want-qty">Want {e(want.get("desired_quantity", 1))}</span>
                </div>
            </div>
            <p class="want-type">{e(want.get("type_line") or "Any printing")}</p>
            {render_preference_summary(want)}
            {f'<p class="want-printing-note"><strong>Preferred printing:</strong> {e(preferred_printing)}</p>' if preferred_printing else ""}
            {f'<p class="muted compact">{e(notes)}</p>' if notes else ""}
            <div class="want-links">{scryfall_link}</div>
        </div>
        <div class="want-side">
            <span class="pill">{e("Budget up to $" + budget_cap + " each" if budget_cap else "No budget cap")}</span>
            {price_pill(want) if show_values else ""}
            <span class="muted compact">{e("Scryfall value shown" if show_values else "Scryfall value hidden")}</span>
        </div>
    </article>
    """
    return render_layout(None, want["card_name"], content)


def render_want_card(
    user,
    want,
    edit_draft=None,
    scryfall_results=None,
    scryfall_picker_intent="use_scryfall",
    scryfall_picker_label="Use selected want",
    scryfall_picker_title="Scryfall matches",
    scryfall_picker_multiple=False,
    share_result=None,
    bulk_select=False,
    bulk_form_id="",
):
    if edit_draft is not None:
        draft = prepare_want_draft(edit_draft)
        image_source = draft if draft.get("image_url") else want
        image = f'<img class="want-image" src="{e(row_value(image_source, "image_url", ""))}" alt="">' if row_value(image_source, "image_url", "") else '<span class="want-image placeholder"></span>'
        edit_form = render_want_form(
            draft,
            f"/wants/{want['id']}/edit",
            "Edit wanted card",
            "Save want",
            scryfall_results=scryfall_results,
            scryfall_picker_intent=scryfall_picker_intent,
            scryfall_picker_label=scryfall_picker_label,
            scryfall_picker_title=scryfall_picker_title,
            scryfall_picker_multiple=scryfall_picker_multiple,
            cancel_href="/wants",
            form_classes="want-edit-form form-grid compact-form",
        )
        share_panel = render_want_share_panel(user["id"], want, share_result=share_result)
        return f"""
        <article class="want-card editing">
            {image}
            <div class="want-edit-body">
                {edit_form}
                {share_panel}
            </div>
        </article>
        """

    availability = want_trade_matches(user["id"], want)
    image = f'<img class="want-image" src="{e(want["image_url"])}" alt="">' if want["image_url"] else '<span class="want-image placeholder"></span>'
    bulk_control = (
        f"""
        <label class="bulk-card-select">
            <input type="checkbox" name="want_id" value="{e(want["id"])}" form="{e(bulk_form_id)}" aria-label="Select {e(want["card_name"])}">
            <span>Select</span>
        </label>
        """
        if bulk_select and bulk_form_id
        else ""
    )
    metadata = []
    metadata.append(game_label(want["game"]))
    if want["set_name"]:
        metadata.append(want["set_name"])
    if want["set_code"]:
        metadata.append(want["set_code"])
    if want["collector_number"]:
        metadata.append(f"#{want['collector_number']}")
    if want["rarity"]:
        metadata.append(want["rarity"].title())
    preference_html = render_preference_summary(want)
    priority = row_value(want, "priority", "normal")
    budget_cap = normalize_price_usd(row_value(want, "budget_cap_usd", ""))
    budget_badge = f'<span class="pill want-budget">Up to ${e(budget_cap)} each</span>' if budget_cap else ""
    preferred_printing = row_value(want, "preferred_printing_notes", "")
    preferred_printing_html = f'<p class="want-printing-note"><strong>Preferred printing:</strong> {e(preferred_printing)}</p>' if preferred_printing else ""
    trade_html = ""
    if availability["total_quantity"]:
        if budget_cap:
            if availability["within_budget_quantity"]:
                budget_match_summary = f'<span class="muted compact">{e(availability["within_budget_quantity"])} currently fit the ${e(budget_cap)} per-copy budget.</span>'
            else:
                budget_match_summary = f'<span class="muted compact">Current priced matches exceed the ${e(budget_cap)} per-copy budget.</span>'
        else:
            budget_match_summary = ""
        member_links = "".join(
            f"""
            <a class="match-chip" href="/members/{match["owner_id"]}">
                {e(match["display_name"])} <span>{e(match["total_quantity"])}</span>
                {f'<small>{e(match["within_budget_quantity"])} in budget</small>' if budget_cap and match["within_budget_quantity"] else ''}
            </a>
            """
            for match in availability["matches"]
        )
        more_count = availability["user_count"] - len(availability["matches"])
        more_text = f'<span class="muted compact">+{more_count} more</span>' if more_count > 0 else ""
        trade_html = f"""
        <div class="want-availability available">
            <span class="status accepted">Available for trade</span>
            <strong>{e(availability["total_quantity"])} copies from {e(availability["user_count"])} users</strong>
            {budget_match_summary}
            <div class="match-chip-row">{member_links}{more_text}</div>
        </div>
        """
    else:
        trade_html = """
        <div class="want-availability">
            <span class="pill">No current matches</span>
            <span class="muted compact">No active user has this marked for trade yet.</span>
        </div>
        """
    scryfall_link = f'<a href="{e(want["scryfall_uri"])}" target="_blank" rel="noreferrer">Scryfall</a>' if want["scryfall_uri"] else ""
    notes = f'<p class="muted compact">{e(want["notes"])}</p>' if want["notes"] else ""
    share_action = (
        f'<a class="button secondary small" href="/wants/{want["id"]}/edit#want-share-links-{want["id"]}">Manage link</a>'
        if record_visibility(want) == VISIBILITY_LINK
        else ""
    )
    return f"""
    <article class="want-card">
        <div class="want-media">
            {bulk_control}
            {image}
        </div>
        <div class="want-main">
            <div class="want-title-row">
                <div>
                    <h2>{e(want["card_name"])}</h2>
                    <p>{e(" - ".join(metadata))}</p>
                </div>
                <div class="want-card-badges">
                    {visibility_badge(want)}
                    <span class="pill want-priority priority-{e(priority)}">{e(want_priority_label(priority))}</span>
                    {budget_badge}
                    <span class="want-qty">Want {e(want["desired_quantity"])}</span>
                </div>
            </div>
            <p class="want-type">{e(want["type_line"] or "Any printing")}</p>
            {preference_html}
            {preferred_printing_html}
            {notes}
            <div class="want-links">{scryfall_link}</div>
        </div>
        <div class="want-side">
            {trade_html}
            <div class="inline-actions">
                {share_action}
                <a class="button secondary small" href="/wants/{want["id"]}/edit">Edit</a>
                <form method="post" action="/wants/{want["id"]}/delete" data-confirm="Delete this wanted card?">
                    <button class="button danger small" type="submit">Delete</button>
                </form>
            </div>
        </div>
    </article>
    """


def render_wants(
    user,
    draft=None,
    scryfall_results=None,
    notice=None,
    status="info",
    scryfall_picker_intent="use_scryfall",
    scryfall_picker_label="Use selected want",
    scryfall_picker_title="Scryfall matches",
    scryfall_picker_multiple=False,
    edit_want_id=0,
    query=None,
    share_result=None,
):
    query = query or {}
    draft = prepare_want_draft(draft)
    current_sort, current_dir = sort_state(query, WANT_SORT_OPTIONS, default="priority")
    order_clause = sort_order_clause(
        query,
        WANT_SORT_OPTIONS,
        WANT_SORT_SQL,
        default="priority",
        fallback=("card_name COLLATE NOCASE", "set_name COLLATE NOCASE", "collector_number COLLATE NOCASE"),
    )
    filters = want_list_filter_values(query)
    total_count = want_count_for_user(user["id"], filters)
    page, per_page, page_count, offset = pagination_state(query, total_count)
    if edit_want_id:
        wants = want_rows_for_user(user["id"], order_clause)
        pagination = ""
    else:
        wants = want_page_rows(user["id"], filters, order_clause, per_page, offset)
        pagination = render_pagination("/wants", query, total_count, page, per_page, page_count)
    bulk_form_id = "wants-bulk-form"
    want_cards = "".join(
        render_want_card(
            user,
            want,
            edit_draft=draft if edit_want_id and want["id"] == int(edit_want_id) else None,
            scryfall_results=scryfall_results if edit_want_id and want["id"] == int(edit_want_id) else None,
            scryfall_picker_intent=scryfall_picker_intent,
            scryfall_picker_label=scryfall_picker_label,
            scryfall_picker_title=scryfall_picker_title,
            scryfall_picker_multiple=scryfall_picker_multiple,
            share_result=share_result if edit_want_id and want["id"] == int(edit_want_id) else None,
            bulk_select=not edit_want_id,
            bulk_form_id=bulk_form_id,
        )
        for want in wants
    )
    want_filters_active = any(value not in ("", None, False) for value in filters.values())
    if wants:
        redirect_to = page_url("/wants", query, page, per_page)
        hidden_filters = collection_hidden_filter_inputs(filters)
        wishlist_groups = [group for group in group_summary_rows(user["id"]) if group["group_type"] == "wishlist"]
        wishlist_group_options = "".join(
            f'<option value="{e(group["id"])}">Wishlist: {e(group["name"])}</option>'
            for group in wishlist_groups
        )
        wishlist_group_disabled = "" if wishlist_group_options else " disabled"
        wishlist_group_empty = "Choose group" if wishlist_group_options else "Create a wishlist group first"
        priority_bulk_options = "".join(
            f'<option value="{e(value)}">{e(label)}</option>'
            for value, label in WANT_PRIORITY_OPTIONS
        )
        visibility_bulk_options = "".join(
            f'<option value="{e(value)}">{e(label)}</option>'
            for value, label in VISIBILITY_OPTIONS
        )
        bulk_controls = f"""
        <form id="{bulk_form_id}" class="bulk-selection-form want-bulk-form" method="post" action="/wants/bulk-update" data-bulk-selection-form data-bulk-selection-name="want_id">
            <input type="hidden" name="redirect_to" value="{e(redirect_to)}">
            {hidden_filters}
            <details class="bulk-tools-disclosure" data-bulk-tools>
            <summary>
                <span>Bulk actions</span>
                <span class="bulk-selection-status" data-bulk-selection-status aria-live="polite">Select cards below or open for all matching</span>
            </summary>
            <div class="bulk-action-bar">
            <div class="bulk-action-intro">
                <strong>Bulk edit wishlist</strong>
                <span class="muted compact">Select wanted cards below, then update shared fields or move cards into a wishlist group.</span>
                <label class="select-all-control inline-select-all">
                    <input type="checkbox" onclick="document.querySelectorAll('input[form={bulk_form_id}][name=want_id]').forEach((box) => box.checked = this.checked)">
                    <span>Select page</span>
                </label>
            </div>
            <div class="bulk-update-workflow">
                <div class="bulk-update-controls">
                    <label>Desired qty
                        <input type="number" min="1" name="desired_quantity" placeholder="No change">
                    </label>
                    <label>Priority
                        <select name="priority">
                            <option value="">No change</option>
                            {priority_bulk_options}
                        </select>
                    </label>
                    <label>Visibility
                        <select name="visibility">
                            <option value="">No change</option>
                            {visibility_bulk_options}
                        </select>
                    </label>
                </div>
                <div class="actions bulk-update-actions">
                    <button class="button secondary small" type="submit" formaction="/wants/bulk-update">Update selected</button>
                    <button class="button secondary small" type="submit" formaction="/wants/update-all" data-confirm="Update all {total_count} wanted cards matching the current filters?">Update all matching</button>
                </div>
                <div class="bulk-group-workflow">
                    <label>Add to group
                        <select name="group_id"{wishlist_group_disabled}>
                            <option value="">{e(wishlist_group_empty)}</option>
                            {wishlist_group_options}
                        </select>
                    </label>
                    <div class="actions bulk-update-actions">
                        <button class="button secondary small" type="submit" formaction="/wants/bulk-group"{wishlist_group_disabled}>Add selected</button>
                        <button class="button secondary small" type="submit" formaction="/wants/group-all" data-confirm="Add all {total_count} wanted cards matching the current filters to this wishlist group?"{wishlist_group_disabled}>Add all matching</button>
                    </div>
                </div>
            </div>
            <details class="bulk-danger-zone">
                <summary>Remove wants</summary>
                <p class="muted compact">Deletion permanently removes wanted-card records and their group links.</p>
                <div class="actions">
                    <button class="button danger small" type="submit" formaction="/wants/bulk-delete" data-confirm="Delete selected wanted cards?">Delete selected</button>
                    <button class="button danger small" type="submit" formaction="/wants/delete-all" data-confirm="Delete all {total_count} wanted cards matching the current filters? This cannot be undone.">Delete all matching</button>
                </div>
            </details>
            </div>
            </details>
        </form>
        """
        want_list = f'{bulk_controls}<div class="want-list">{want_cards}</div>'
    elif want_filters_active:
        want_list = render_empty_action_state(
            "No wanted cards match these filters.",
            "Reset filters to return to your full wishlist.",
            actions=(("/wants", "Reset filters", "secondary"),),
        )
    else:
        want_list = render_empty_action_state(
            "No wanted cards yet.",
            "Add a wanted card below to start powering trade matches and alerts.",
            actions=(("#add-want", "Add a want", "secondary"),),
        )
    add_panel = ""
    if not edit_want_id:
        active_add_draft = any(
            draft.get(key)
            for key in (
                "card_name",
                "set_name",
                "set_code",
                "collector_number",
                "scryfall_id",
                "selected_scryfall_id",
            )
        )
        add_panel_open = " open" if (scryfall_results or active_add_draft or (not wants and not want_filters_active)) else ""
        add_form = render_want_form(
            draft,
            "/wants/new",
            "Add wanted card",
            "Add want",
            scryfall_results=scryfall_results,
            scryfall_picker_intent=scryfall_picker_intent,
            scryfall_picker_label=scryfall_picker_label,
            scryfall_picker_title=scryfall_picker_title,
            scryfall_picker_multiple=scryfall_picker_multiple,
            form_classes="form-grid compact-form embedded-form want-add-form",
        )
        add_panel = f"""
        <details class="panel want-add-panel" id="add-want"{add_panel_open}>
            <summary>
                <span>Add wanted card</span>
                <small class="muted">Search Scryfall or enter card details</small>
            </summary>
            <div class="want-add-panel-body">{add_form}</div>
        </details>
        """
    priority_options = "".join(
        f'<option value="{e(value)}"{selected(filters["priority"], value)}>{e(label)}</option>'
        for value, label in WANT_PRIORITY_OPTIONS
    )
    visibility_options = "".join(
        f'<option value="{e(value)}"{selected(filters["visibility"], value)}>{e(label)}</option>'
        for value, label in VISIBILITY_OPTIONS
    )
    active_filters = render_active_filter_chips("/wants", query, filters, want_list_filter_chip_specs())
    want_datalist = render_datalist("want-search-suggestions", want_search_suggestions(user["id"]))
    add_href = "/wants#add-want" if edit_want_id else "#add-want"
    wishlist_scryfall_count = int(want_scryfall_enhancement_audit_summary(user["id"]).get("missing", 0) or 0)
    duplicate_counts = duplicate_cleanup_count_summary(user["id"])
    content = f"""
    {render_wishlist_subnav("wants")}
    <section class="section-heading">
        <div>
            <p class="eyebrow">Wishlist</p>
            <h1>Cards you want</h1>
            <p class="lead">Track priorities and printing preferences, then focus on wants that other members can fill.</p>
        </div>
        <div class="actions">
            <a class="button primary" href="{e(add_href)}">Add wanted card</a>
            <details class="header-action-menu">
                <summary class="button secondary">More actions</summary>
                <div class="header-action-menu-panel">
                    {render_counted_action(audit_section_path(AUDIT_SECTION_WISHLIST_SCRYFALL), "Audit wishlist", wishlist_scryfall_count, "missing Scryfall", "missing Scryfall")}
                    {render_counted_action("/cleanup", "Duplicate cleanup", duplicate_counts["want_duplicate_groups"], "group")}
                    <a class="button secondary" href="/wants/export">Export CSV</a>
                </div>
            </details>
        </div>
    </section>
    <form class="filter-bar wants-filter-bar" method="get" action="/wants">
        <details class="responsive-filter-panel" data-responsive-filter data-filter-active="{'true' if want_filters_active else 'false'}" open>
        <summary><span>Search and filters</span><span class="muted compact">Prioritize and refine wants</span></summary>
        <div class="filter-primary-row">
            <label class="search-field">Search
                <input name="q" value="{e(filters["q"])}" placeholder="Card name, type, or set" list="want-search-suggestions">
            </label>
            <label>Priority
                <select name="priority"><option value="">All priorities</option>{priority_options}</select>
            </label>
            <label class="checkbox-line">
                <input type="checkbox" name="matched_only" value="1"{checked(filters["matched_only"])}>
                Trade matches only
            </label>
            <div class="actions filter-actions">
                <button class="button secondary" type="submit">Filter</button>
                <a class="button ghost" href="/wants">Reset</a>
            </div>
        </div>
        <details class="advanced-filter"{" open" if filters["game"] or filters["visibility"] else ""}>
            <summary><span>More filters and sorting</span><span class="advanced-filter-count">Game, visibility, order</span></summary>
            <div class="advanced-filter-grid">
                <label>Game
                    <select name="game"><option value="">All games</option>{option_tags(CARD_GAMES, filters["game"])}</select>
                </label>
                <label>Visibility
                    <select name="visibility"><option value="">All visibility</option>{visibility_options}</select>
                </label>
                {render_sort_controls(WANT_SORT_OPTIONS, current_sort, current_dir)}
            </div>
        </details>
        </details>
    </form>
    {render_saved_search_controls(user["id"], "wants", query)}
    {active_filters}
    <section class="panel flush wants-panel" id="tracked-wants">
            <div class="panel-heading padded">
                <h2>Tracked wants</h2>
                <span class="muted">{e(total_count)} matching card{"s" if total_count != 1 else ""}</span>
            </div>
            {want_list}
    </section>
    {pagination}
    {add_panel}
    {want_datalist}
    {render_preference_select_all_script()}
    """
    return render_layout(user, "Wishlist", content, active="wants", notice=notice, status=status)


__all__ = ['default_want_item', 'validate_want_form', 'insert_want_item', 'update_want_item', 'insert_selected_want_items', 'want_trade_matches', 'render_want_share_link_row', 'render_want_share_panel', 'shared_want_from_link', 'render_shared_want_card', 'render_want_card', 'render_wants', 'match_entry_quantity', 'trade_match_entry_from_row', 'add_trade_match_entry', 'sorted_trade_match_entries', 'finalize_trade_match', 'trade_matchmaking_candidate_rows', 'trade_matchmaking_results', 'trade_matchmaking_prefill_url', 'render_trade_match_card_list', 'render_trade_match_card', 'render_trade_matchmaking']
