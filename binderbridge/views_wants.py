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
        "condition": "",
        "finish": "",
        "language": "",
        "notes": "",
        "lookup_on_save": "1",
        "scryfall_finish_override": "",
        "price_source": "",
        "is_public": 1,
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
    item.setdefault("price_source", "scryfall" if item.get("price_usd") and (item.get("scryfall_id") or item.get("scryfall_uri")) else "")
    item.setdefault("lookup_on_save", "1")
    return item


def validate_want_form(form):
    card_name = sanitize_text_input(form.get("card_name", [""])[0], max_length=160).strip()
    game = form.get("game", ["mtg"])[0].strip() or "mtg"
    set_name = sanitize_text_input(form.get("set_name", [""])[0], max_length=120).strip()
    set_code = sanitize_text_input(form.get("set_code", [""])[0], max_length=20).strip().upper()
    collector_number = sanitize_text_input(form.get("collector_number", [""])[0], max_length=40).strip()
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
        "condition": normalize_want_preference_values(form.get("condition", []), CONDITION_OPTIONS, normalize_condition),
        "finish": normalize_want_preference_values(form.get("finish", []), FINISH_OPTIONS, normalize_finish),
        "language": normalize_want_preference_values(form.get("language", []), LANGUAGE_OPTIONS, normalize_language),
        "notes": notes,
        "is_public": form_public_flag(form),
        "lookup_on_save": "1" if form.get("lookup_on_save", [""])[0] == "1" else "",
        "scryfall_finish_override": "1" if form.get("scryfall_finish_override", [""])[0] == "1" else "",
        "selected_scryfall_id": sanitize_text_input(form.get("selected_scryfall_id", [""])[0], max_length=80).strip(),
    }
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
             condition, finish, language,
             scryfall_id, image_url, mana_cost, type_line, oracle_text, rarity, colors, color_identity,
             scryfall_uri, price_usd, price_source, notes, is_public, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            data.get("game", "mtg"),
            data.get("card_name", ""),
            data.get("set_name", ""),
            data.get("set_code", ""),
            data.get("collector_number", ""),
            data.get("desired_quantity", 1),
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
            data.get("notes", ""),
            1 if data.get("is_public", 1) else 0,
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
                desired_quantity = ?, condition = ?, finish = ?, language = ?,
                scryfall_id = ?, image_url = ?, mana_cost = ?, type_line = ?, oracle_text = ?,
                rarity = ?, colors = ?, color_identity = ?, scryfall_uri = ?, price_usd = ?,
                price_source = ?, notes = ?, is_public = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                data.get("game", "mtg"),
                data.get("card_name", ""),
                data.get("set_name", ""),
                data.get("set_code", ""),
                data.get("collector_number", ""),
                data.get("desired_quantity", 1),
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
                data.get("notes", ""),
                1 if data.get("is_public", 1) else 0,
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
        "quantity": quantity,
    }
    entry["value_cents"] = price_to_cents(entry["price_usd"]) * quantity
    entry["unpriced"] = 0 if entry["price_usd"] else quantity
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
        key=lambda item: (-int(item["value_cents"] or 0), item["card_name"].lower(), item["set_name"].lower()),
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
        {condition_checks}
        {finish_checks}
        {language_checks}
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


def render_want_card(
    user,
    want,
    edit_draft=None,
    scryfall_results=None,
    scryfall_picker_intent="use_scryfall",
    scryfall_picker_label="Use selected want",
    scryfall_picker_title="Scryfall matches",
    scryfall_picker_multiple=False,
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
        return f"""
        <article class="want-card editing">
            {image}
            <div class="want-edit-body">
                {edit_form}
            </div>
        </article>
        """

    availability = want_trade_matches(user["id"], want)
    image = f'<img class="want-image" src="{e(want["image_url"])}" alt="">' if want["image_url"] else '<span class="want-image placeholder"></span>'
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
    trade_html = ""
    if availability["total_quantity"]:
        member_links = "".join(
            f"""
            <a class="match-chip" href="/members/{match["owner_id"]}">
                {e(match["display_name"])} <span>{e(match["total_quantity"])}</span>
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
    return f"""
    <article class="want-card">
        {image}
        <div class="want-main">
            <div class="want-title-row">
                <div>
                    <h2>{e(want["card_name"])}</h2>
                    <p>{e(" - ".join(metadata))}</p>
                </div>
                <div class="want-card-badges">
                    {visibility_badge(want)}
                    <span class="want-qty">Want {e(want["desired_quantity"])}</span>
                </div>
            </div>
            <p class="want-type">{e(want["type_line"] or "Any printing")}</p>
            {preference_html}
            {notes}
            <div class="want-links">{scryfall_link}</div>
        </div>
        <div class="want-side">
            {trade_html}
            <div class="inline-actions">
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
):
    query = query or {}
    draft = prepare_want_draft(draft)
    current_sort, current_dir = sort_state(query, WANT_SORT_OPTIONS)
    order_clause = sort_order_clause(
        query,
        WANT_SORT_OPTIONS,
        WANT_SORT_SQL,
        fallback=("card_name COLLATE NOCASE", "set_name COLLATE NOCASE", "collector_number COLLATE NOCASE"),
    )
    wants = want_rows_for_user(user["id"], order_clause)
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
        )
        for want in wants
    )
    want_list = f'<div class="want-list">{want_cards}</div>' if wants else '<div class="empty-state">No wanted cards yet.</div>'
    add_form = render_want_form(
        default_want_item() if edit_want_id else draft,
        "/wants/new",
        "Add wanted card",
        "Add want",
        scryfall_results=None if edit_want_id else scryfall_results,
        scryfall_picker_intent=scryfall_picker_intent,
        scryfall_picker_label=scryfall_picker_label,
        scryfall_picker_title=scryfall_picker_title,
        scryfall_picker_multiple=scryfall_picker_multiple if not edit_want_id else False,
    )
    content = f"""
    {render_wishlist_subnav("wants")}
    <section class="section-heading">
        <div>
            <p class="eyebrow">Wishlist</p>
            <h1>Cards you want</h1>
        </div>
        <div class="actions">
            <a class="button secondary" href="/cleanup">Cleanup duplicates</a>
            <a class="button secondary" href="/wants/export">Export CSV</a>
        </div>
    </section>
    {render_sort_bar("/wants", query, WANT_SORT_OPTIONS, current_sort, current_dir)}
    <section class="content-grid wants-grid">
        {add_form}
        <article class="panel wants-panel">
            <div class="panel-heading">
                <h2>Tracked wants</h2>
                <span class="muted">{e(len(wants))} cards</span>
            </div>
            {want_list}
        </article>
    </section>
    {render_preference_select_all_script()}
    """
    return render_layout(user, "Wishlist", content, active="wants", notice=notice, status=status)


__all__ = ['default_want_item', 'validate_want_form', 'insert_want_item', 'update_want_item', 'insert_selected_want_items', 'want_trade_matches', 'render_want_card', 'render_wants', 'match_entry_quantity', 'trade_match_entry_from_row', 'add_trade_match_entry', 'sorted_trade_match_entries', 'finalize_trade_match', 'trade_matchmaking_candidate_rows', 'trade_matchmaking_results', 'trade_matchmaking_prefill_url', 'render_trade_match_card_list', 'render_trade_match_card', 'render_trade_matchmaking']
