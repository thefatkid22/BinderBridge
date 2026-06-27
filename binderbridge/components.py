"""Reusable HTML components shared by feature views."""

from urllib.parse import urlencode

from binderbridge.formatting import PAGE_SIZE_OPTIONS, e, selected


TRADE_PICKER_FILTER_KEYS = (
    "q",
    "game",
    "condition",
    "finish",
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
    "sort",
    "dir",
)


def query_int(query, key, default):
    try:
        return int(query.get(key, [str(default)])[0])
    except (TypeError, ValueError):
        return default


def sort_state(query, options, default="name", prefix=""):
    keys = {key for key, _ in options}
    sort_name = f"{prefix}_sort" if prefix else "sort"
    dir_name = f"{prefix}_dir" if prefix else "dir"
    sort_key = query.get(sort_name, [""])[0].strip()
    if sort_key not in keys:
        sort_key = default
    direction = query.get(dir_name, [""])[0].strip().lower()
    if direction not in ("asc", "desc"):
        direction = "asc"
    return sort_key, direction


def sort_order_clause(query, options, sort_sql, default="name", prefix="", fallback=()):
    sort_key, direction = sort_state(query, options, default=default, prefix=prefix)
    sql_direction = "DESC" if direction == "desc" else "ASC"
    expressions = sort_sql.get(sort_key, sort_sql[default])
    parts = [f"{expression} {sql_direction}" for expression in expressions]
    for expression in fallback:
        parts.append(f"{expression} ASC")
    return ", ".join(parts)


def sort_option_tags(options, current):
    return "".join(f'<option value="{e(value)}"{selected(current, value)}>{e(label)}</option>' for value, label in options)


def direction_option_tags(current):
    return "".join(
        [
            f'<option value="asc"{selected(current, "asc")}>Ascending</option>',
            f'<option value="desc"{selected(current, "desc")}>Descending</option>',
        ]
    )


def render_sort_controls(options, current_sort, current_dir, prefix=""):
    sort_name = f"{prefix}_sort" if prefix else "sort"
    dir_name = f"{prefix}_dir" if prefix else "dir"
    return f"""
        <label class="sort-field">Sort by
            <select name="{e(sort_name)}">{sort_option_tags(options, current_sort)}</select>
        </label>
        <label class="sort-field">Direction
            <select name="{e(dir_name)}">{direction_option_tags(current_dir)}</select>
        </label>
    """


def sort_form_hidden_inputs(query, exclude=("sort", "dir", "page")):
    hidden = []
    for key, values in query.items():
        if key in exclude:
            continue
        for value in values:
            if value != "":
                hidden.append(f'<input type="hidden" name="{e(key)}" value="{e(value)}">')
    return "".join(hidden)


def render_sort_bar(path, query, options, current_sort, current_dir):
    return f"""
    <form class="filter-bar sort-bar" method="get" action="{e(path)}">
        {sort_form_hidden_inputs(query)}
        {render_sort_controls(options, current_sort, current_dir)}
        <div class="actions filter-actions">
            <button class="button secondary" type="submit">Sort</button>
        </div>
    </form>
    """


def render_empty_action_state(title, body="", actions=(), tag="div", compact=True, class_name=""):
    tag = tag if tag in ("div", "li", "td") else "div"
    compact_class = " compact-empty" if compact else ""
    extra_class = f" {e(class_name)}" if class_name else ""
    body_html = f'<p>{e(body)}</p>' if body else ""
    action_links = []
    for action in actions or ():
        href, label, *rest = action
        variant = rest[0] if rest else "secondary"
        action_links.append(f'<a class="button {e(variant or "secondary")} small" href="{e(href)}">{e(label)}</a>')
    action_html = "".join(action_links)
    action_block = f'<div class="actions">{action_html}</div>' if action_html else ""
    return f"""
    <{tag} class="empty-state empty-action-state{compact_class}{extra_class}">
        <div class="empty-action-copy">
            <h3>{e(title)}</h3>
            {body_html}
        </div>
        {action_block}
    </{tag}>
    """


def filter_value_is_active(value):
    return value not in ("", None, False)


def option_value_label(options, value):
    return dict(options).get(str(value), str(value))


def title_value_label(value):
    return str(value).title()


def min_filter_label(value):
    return f">= {value}"


def max_filter_label(value):
    return f"<= {value}"


def filter_chip_url(path, query, remove_keys, page_key="page", required_params=None):
    clean = {}
    for key, value in (required_params or {}).items():
        if filter_value_is_active(value):
            clean[key] = [str(value)]
    remove = set(remove_keys)
    if page_key:
        remove.add(page_key)
    for key, values in query.items():
        if key in remove:
            continue
        values = values if isinstance(values, (list, tuple)) else [values]
        filtered = [str(value) for value in values if value != ""]
        if filtered:
            clean[key] = filtered
    query_string = urlencode(clean, doseq=True)
    return f"{path}?{query_string}" if query_string else path


def filter_chip_text(spec, value):
    if spec.get("standalone"):
        return spec["label"]
    formatter = spec.get("formatter", str)
    return f'{spec["label"]}: {formatter(value)}'


def render_active_filter_chips(path, query, filters, specs, page_key="page", required_params=None, class_name=""):
    chips = []
    active_keys = []
    for spec in specs:
        value = filters.get(spec["key"])
        if not filter_value_is_active(value):
            continue
        query_key = spec.get("query_key", spec["key"])
        active_keys.append(query_key)
        text = filter_chip_text(spec, value)
        remove_url = filter_chip_url(path, query, (query_key,), page_key=page_key, required_params=required_params)
        chips.append(
            f"""
            <a class="filter-chip" data-filter-key="{e(query_key)}" href="{e(remove_url)}" aria-label="Remove {e(text)} filter">
                <span>{e(text)}</span>
                <span class="filter-chip-remove" aria-hidden="true">&times;</span>
            </a>
            """
        )
    if not chips:
        return ""
    clear_url = filter_chip_url(path, query, active_keys, page_key=page_key, required_params=required_params)
    extra_class = f" {e(class_name)}" if class_name else ""
    return f"""
    <div class="active-filter-bar{extra_class}" aria-label="Active filters">
        <span class="active-filter-label">Active filters</span>
        <div class="active-filter-chips">
            {''.join(chips)}
            <a class="filter-chip clear-filter-chip" href="{e(clear_url)}">Clear filters</a>
        </div>
    </div>
    """


def render_saved_search_controls(user_id, context, query, required_params=None, class_name=""):
    searches = saved_search_rows(user_id, context)
    current_values = saved_search_query_values(context, query)
    return_to = saved_search_current_url(context, query, required_params=required_params)
    hidden_inputs = "".join(
        f'<input type="hidden" name="{e(key)}" value="{e(value)}">'
        for key, value in current_values.items()
    )
    saved_items = []
    for search in searches:
        apply_url = saved_search_apply_url(search, current_query=query, required_params=required_params)
        setting_count = len(saved_search_payload(search))
        saved_items.append(
            f"""
            <li class="saved-search-item">
                <a class="button secondary small saved-search-apply" href="{e(apply_url)}">
                    <span>{e(search["name"])}</span>
                    <small>{e(setting_count)} setting{"s" if setting_count != 1 else ""}</small>
                </a>
                <form method="post" action="/saved-searches/{e(search["id"])}/delete" data-confirm="Delete this saved search?">
                    <input type="hidden" name="context" value="{e(context)}">
                    <input type="hidden" name="return_to" value="{e(return_to)}">
                    <button class="button ghost small" type="submit" aria-label="Delete saved search {e(search["name"])}">Remove</button>
                </form>
            </li>
            """
        )
    saved_list = (
        f'<ul class="saved-search-list">{"".join(saved_items)}</ul>'
        if saved_items
        else '<p class="muted compact">No presets saved for this view yet.</p>'
    )
    disabled = "" if current_values else " disabled"
    helper = (
        "Saving this name again updates the preset."
        if current_values
        else "Apply a filter or sort option before saving a preset."
    )
    extra_class = f" {e(class_name)}" if class_name else ""
    return f"""
    <details class="saved-search-panel{extra_class}">
        <summary>
            <span>Saved filters</span>
            <span class="advanced-filter-count">{e(len(searches))} saved</span>
        </summary>
        <div class="saved-search-body">
            {saved_list}
            <form class="saved-search-form" method="post" action="/saved-searches">
                <input type="hidden" name="context" value="{e(context)}">
                <input type="hidden" name="return_to" value="{e(return_to)}">
                {hidden_inputs}
                <label>Preset name
                    <input name="name" maxlength="80" required placeholder="My favorite view">
                </label>
                <button class="button primary small" type="submit"{disabled}>Save current filters</button>
                <span class="muted compact">{e(helper)}</span>
            </form>
        </div>
    </details>
    """


def pagination_state(query, total_count, default_per_page=25):
    requested_per_page = query_int(query, "per_page", default_per_page)
    per_page = requested_per_page if requested_per_page in PAGE_SIZE_OPTIONS else default_per_page
    page_count = max(1, (total_count + per_page - 1) // per_page)
    page = min(max(1, query_int(query, "page", 1)), page_count)
    offset = (page - 1) * per_page
    return page, per_page, page_count, offset


def page_url(path, query, page, per_page=None):
    clean = {}
    for key, values in query.items():
        if key in ("page", "per_page"):
            continue
        filtered = [value for value in values if value != ""]
        if filtered:
            clean[key] = filtered
    clean["page"] = [str(page)]
    if per_page:
        clean["per_page"] = [str(per_page)]
    query_string = urlencode(clean, doseq=True)
    return f"{path}?{query_string}" if query_string else path


def current_collection_url(path, query, page, per_page):
    return page_url(path, query, page, per_page)


def render_pagination(path, query, total_count, page, per_page, page_count):
    if total_count == 0:
        return ""
    first_item = (page - 1) * per_page + 1
    last_item = min(total_count, page * per_page)
    page_links = []
    start = max(1, page - 2)
    end = min(page_count, page + 2)
    for number in range(start, end + 1):
        if number == page:
            page_links.append(f'<span class="page-link active">{number}</span>')
        else:
            page_links.append(f'<a class="page-link" href="{e(page_url(path, query, number, per_page))}">{number}</a>')
    prev_link = (
        f'<a class="button secondary small" href="{e(page_url(path, query, page - 1, per_page))}">Previous</a>'
        if page > 1
        else '<span class="button secondary small disabled">Previous</span>'
    )
    next_link = (
        f'<a class="button secondary small" href="{e(page_url(path, query, page + 1, per_page))}">Next</a>'
        if page < page_count
        else '<span class="button secondary small disabled">Next</span>'
    )
    per_page_options = "".join(
        f'<option value="{size}"{selected(str(per_page), str(size))}>{size}</option>'
        for size in PAGE_SIZE_OPTIONS
    )
    return f"""
    <div class="pagination-bar">
        <p class="muted compact">Showing {first_item}-{last_item} of {total_count}</p>
        <nav class="pagination-links" aria-label="Pagination">
            {prev_link}
            {''.join(page_links)}
            {next_link}
        </nav>
        <form class="per-page-form" method="get" action="{e(path)}">
            {pagination_hidden_inputs(query)}
            <label>Per page
                <select name="per_page" onchange="this.form.submit()">{per_page_options}</select>
            </label>
        </form>
    </div>
    """


def pagination_hidden_inputs(query):
    hidden = []
    for key, values in query.items():
        if key in ("page", "per_page"):
            continue
        for value in values:
            if value != "":
                hidden.append(f'<input type="hidden" name="{e(key)}" value="{e(value)}">')
    return "".join(hidden)


def trade_picker_pagination_state(query, prefix, total_count):
    requested_per_page = query_int(query, f"{prefix}_per_page", 10)
    per_page = requested_per_page if requested_per_page in PAGE_SIZE_OPTIONS else 10
    page_count = max(1, (total_count + per_page - 1) // per_page)
    page = min(max(1, query_int(query, f"{prefix}_page", 1)), page_count)
    offset = (page - 1) * per_page
    return page, per_page, page_count, offset


def trade_picker_url(recipient_id, query, updates=None, reset_prefix=None):
    clean = {"recipient_id": [str(recipient_id)]}
    for key, values in query.items():
        if key == "recipient_id":
            continue
        if reset_prefix and key.startswith(f"{reset_prefix}_"):
            suffix = key.split("_", 1)[1]
            if suffix in TRADE_PICKER_FILTER_KEYS or suffix in ("page", "per_page"):
                continue
        filtered = [value for value in values if value != ""]
        if filtered:
            clean[key] = filtered
    for key, value in (updates or {}).items():
        if value in ("", None, False):
            clean.pop(key, None)
        else:
            clean[key] = [str(value)]
    query_string = urlencode(clean, doseq=True)
    return f"/trades/new?{query_string}"


def trade_picker_preserved_inputs(recipient_id, query, active_prefix):
    hidden = [f'<input type="hidden" name="recipient_id" value="{e(recipient_id)}">']
    for key, values in query.items():
        if key == "recipient_id":
            continue
        if key.startswith(f"{active_prefix}_"):
            suffix = key.split("_", 1)[1]
            if suffix in TRADE_PICKER_FILTER_KEYS or suffix == "page":
                continue
        for value in values:
            if value != "":
                hidden.append(f'<input type="hidden" name="{e(key)}" value="{e(value)}">')
    return "".join(hidden)


def render_trade_picker_pagination(recipient_id, query, prefix, total_count, page, per_page, page_count):
    if total_count == 0:
        return ""
    first_item = (page - 1) * per_page + 1
    last_item = min(total_count, page * per_page)
    page_links = []
    start = max(1, page - 2)
    end = min(page_count, page + 2)
    for number in range(start, end + 1):
        if number == page:
            page_links.append(f'<span class="page-link active">{number}</span>')
        else:
            page_links.append(
                f'<a class="page-link" href="{e(trade_picker_url(recipient_id, query, {f"{prefix}_page": number, f"{prefix}_per_page": per_page}))}">{number}</a>'
            )
    prev_link = (
        f'<a class="button secondary small" href="{e(trade_picker_url(recipient_id, query, {f"{prefix}_page": page - 1, f"{prefix}_per_page": per_page}))}">Previous</a>'
        if page > 1
        else '<span class="button secondary small disabled">Previous</span>'
    )
    next_link = (
        f'<a class="button secondary small" href="{e(trade_picker_url(recipient_id, query, {f"{prefix}_page": page + 1, f"{prefix}_per_page": per_page}))}">Next</a>'
        if page < page_count
        else '<span class="button secondary small disabled">Next</span>'
    )
    per_page_options = "".join(
        f'<option value="{size}"{selected(str(per_page), str(size))}>{size}</option>'
        for size in PAGE_SIZE_OPTIONS
    )
    return f"""
    <div class="pagination-bar trade-picker-pagination">
        <p class="muted compact">Showing {first_item}-{last_item} of {total_count}</p>
        <nav class="pagination-links" aria-label="Pagination">
            {prev_link}
            {''.join(page_links)}
            {next_link}
        </nav>
        <form class="per-page-form" method="get" action="/trades/new">
            {trade_picker_preserved_inputs(recipient_id, query, prefix)}
            <input type="hidden" name="{e(prefix)}_page" value="1">
            <label>Per page
                <select name="{e(prefix)}_per_page" onchange="this.form.submit()">{per_page_options}</select>
            </label>
        </form>
    </div>
    """


__all__ = [
    "TRADE_PICKER_FILTER_KEYS",
    "query_int",
    "sort_state",
    "sort_order_clause",
    "sort_option_tags",
    "direction_option_tags",
    "render_sort_controls",
    "sort_form_hidden_inputs",
    "render_sort_bar",
    "render_empty_action_state",
    "filter_value_is_active",
    "option_value_label",
    "title_value_label",
    "min_filter_label",
    "max_filter_label",
    "filter_chip_url",
    "filter_chip_text",
    "render_active_filter_chips",
    "render_saved_search_controls",
    "pagination_state",
    "page_url",
    "current_collection_url",
    "render_pagination",
    "pagination_hidden_inputs",
    "trade_picker_pagination_state",
    "trade_picker_url",
    "trade_picker_preserved_inputs",
    "render_trade_picker_pagination",
]
