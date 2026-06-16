"""Personal saved searches and reusable filter presets."""

import json
from urllib.parse import parse_qs, urlencode, urlparse


SAVED_SEARCH_MAX_PER_CONTEXT = 25
SAVED_SEARCH_MAX_NAME_LENGTH = 80
SAVED_SEARCH_MAX_VALUE_LENGTH = 500

COLLECTION_SAVED_SEARCH_KEYS = (
    "q", "game", "trade_only", "set_name", "set_code", "collector_number", "type_line",
    "condition", "finish", "language", "rarity", "color_identity", "card_data", "visibility",
    "quantity_min", "quantity_max", "trade_min", "trade_max", "sort", "dir",
)
BROWSE_SAVED_SEARCH_KEYS = (
    "q", "user", "quality", "game", "finish", "set_name", "set_code", "collector_number",
    "type_line", "language", "rarity", "color_identity", "card_data", "quantity_min",
    "quantity_max", "trade_min", "trade_max", "sort", "dir",
)
WANT_SAVED_SEARCH_KEYS = (
    "q", "priority", "matched_only", "game", "visibility", "sort", "dir",
)
TRADE_PICKER_SAVED_SEARCH_SUFFIXES = (
    "q", "game", "condition", "finish", "set_name", "set_code", "collector_number",
    "type_line", "language", "rarity", "color_identity", "card_data", "quantity_min",
    "quantity_max", "trade_min", "trade_max", "sort", "dir",
)

SAVED_SEARCH_CONTEXTS = {
    "collection": {
        "label": "Collection",
        "path": "/collection",
        "keys": COLLECTION_SAVED_SEARCH_KEYS,
        "page_key": "page",
        "preserve_other": False,
    },
    "browse": {
        "label": "Browse",
        "path": "/browse",
        "keys": BROWSE_SAVED_SEARCH_KEYS,
        "page_key": "page",
        "preserve_other": False,
    },
    "wants": {
        "label": "Wishlist",
        "path": "/wants",
        "keys": WANT_SAVED_SEARCH_KEYS,
        "page_key": "page",
        "preserve_other": False,
    },
    "trade_offer": {
        "label": "Offer picker",
        "path": "/trades/new",
        "keys": tuple(f"offer_{key}" for key in TRADE_PICKER_SAVED_SEARCH_SUFFIXES),
        "page_key": "offer_page",
        "preserve_other": True,
    },
    "trade_request": {
        "label": "Request picker",
        "path": "/trades/new",
        "keys": tuple(f"request_{key}" for key in TRADE_PICKER_SAVED_SEARCH_SUFFIXES),
        "page_key": "request_page",
        "preserve_other": True,
    },
}


def saved_search_context_definition(context):
    clean_context = str(context or "").strip().lower()
    definition = SAVED_SEARCH_CONTEXTS.get(clean_context)
    if not definition:
        raise ValueError("Choose a supported saved-search view.")
    return clean_context, definition


def saved_search_clean_name(name):
    clean_name = sanitize_text_input(name, max_length=SAVED_SEARCH_MAX_NAME_LENGTH).strip()
    if not clean_name:
        raise ValueError("Enter a name for this saved search.")
    return clean_name


def saved_search_query_values(context, source):
    _clean_context, definition = saved_search_context_definition(context)
    clean = {}
    source = source or {}
    for key in definition["keys"]:
        values = source.get(key, [])
        if not isinstance(values, (list, tuple)):
            values = [values]
        if not values:
            continue
        value = sanitize_text_input(values[0], max_length=SAVED_SEARCH_MAX_VALUE_LENGTH).strip()
        if value:
            clean[key] = value
    return clean


def saved_search_query_json(context, source):
    return json.dumps(
        saved_search_query_values(context, source),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def saved_search_payload(search):
    try:
        payload = json.loads(str(search["query_json"] or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        return {}
    return saved_search_query_values(search["context"], payload)


def saved_search_rows(user_id, context):
    clean_context, _definition = saved_search_context_definition(context)
    return rows(
        """
        SELECT *
        FROM saved_searches
        WHERE user_id = ? AND context = ?
        ORDER BY name COLLATE NOCASE, id
        """,
        (int(user_id), clean_context),
    )


def save_saved_search(user_id, context, name, source):
    clean_context, _definition = saved_search_context_definition(context)
    clean_name = saved_search_clean_name(name)
    query_values = saved_search_query_values(clean_context, source)
    if not query_values:
        raise ValueError("Apply at least one filter or sort option before saving.")
    timestamp = now_iso()
    with db() as conn:
        existing = conn.execute(
            "SELECT id FROM saved_searches WHERE user_id = ? AND context = ? AND name = ? COLLATE NOCASE",
            (int(user_id), clean_context, clean_name),
        ).fetchone()
        if not existing:
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM saved_searches WHERE user_id = ? AND context = ?",
                (int(user_id), clean_context),
            ).fetchone()["count"]
            if int(count or 0) >= SAVED_SEARCH_MAX_PER_CONTEXT:
                raise ValueError(f"Delete an existing preset before saving more than {SAVED_SEARCH_MAX_PER_CONTEXT} for this view.")
        query_json = json.dumps(query_values, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        conn.execute(
            """
            INSERT INTO saved_searches (user_id, context, name, query_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, context, name)
            DO UPDATE SET query_json = excluded.query_json, updated_at = excluded.updated_at
            """,
            (int(user_id), clean_context, clean_name, query_json, timestamp, timestamp),
        )
        return conn.execute(
            "SELECT * FROM saved_searches WHERE user_id = ? AND context = ? AND name = ? COLLATE NOCASE",
            (int(user_id), clean_context, clean_name),
        ).fetchone()


def delete_saved_search(user_id, saved_search_id):
    with db() as conn:
        search = conn.execute(
            "SELECT * FROM saved_searches WHERE id = ? AND user_id = ?",
            (int(saved_search_id), int(user_id)),
        ).fetchone()
        if not search:
            raise ValueError("That saved search was not found.")
        conn.execute("DELETE FROM saved_searches WHERE id = ? AND user_id = ?", (search["id"], int(user_id)))
        return search


def saved_search_query_mapping(query):
    clean = {}
    for key, values in (query or {}).items():
        values = values if isinstance(values, (list, tuple)) else [values]
        filtered = [
            sanitize_text_input(value, max_length=SAVED_SEARCH_MAX_VALUE_LENGTH).strip()
            for value in values
            if str(value or "").strip()
        ]
        if filtered:
            clean[str(key)] = filtered
    return clean


def saved_search_current_url(context, query, required_params=None):
    _clean_context, definition = saved_search_context_definition(context)
    clean = saved_search_query_mapping(query)
    for key, value in (required_params or {}).items():
        if value not in ("", None, False):
            clean[str(key)] = [str(value)]
    query_string = urlencode(clean, doseq=True)
    return f'{definition["path"]}?{query_string}' if query_string else definition["path"]


def saved_search_apply_url(search, current_query=None, required_params=None):
    _clean_context, definition = saved_search_context_definition(search["context"])
    controlled = set(definition["keys"])
    clean = saved_search_query_mapping(current_query) if definition["preserve_other"] else {}
    clean.pop(definition["page_key"], None)
    for key in controlled:
        clean.pop(key, None)
    for key, value in saved_search_payload(search).items():
        clean[key] = [str(value)]
    for key, value in (required_params or {}).items():
        if value not in ("", None, False):
            clean[str(key)] = [str(value)]
    query_string = urlencode(clean, doseq=True)
    return f'{definition["path"]}?{query_string}' if query_string else definition["path"]


def saved_search_safe_return_to(context, value):
    _clean_context, definition = saved_search_context_definition(context)
    parsed = urlparse(str(value or ""))
    if parsed.scheme or parsed.netloc or parsed.path != definition["path"]:
        return definition["path"]
    query = parse_qs(parsed.query, keep_blank_values=False)
    query_string = urlencode(query, doseq=True)
    return f'{definition["path"]}?{query_string}' if query_string else definition["path"]


__all__ = [
    "SAVED_SEARCH_MAX_PER_CONTEXT",
    "SAVED_SEARCH_MAX_NAME_LENGTH",
    "SAVED_SEARCH_CONTEXTS",
    "saved_search_context_definition",
    "saved_search_clean_name",
    "saved_search_query_values",
    "saved_search_query_json",
    "saved_search_payload",
    "saved_search_rows",
    "save_saved_search",
    "delete_saved_search",
    "saved_search_query_mapping",
    "saved_search_current_url",
    "saved_search_apply_url",
    "saved_search_safe_return_to",
]
