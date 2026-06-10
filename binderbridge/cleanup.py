"""Duplicate detection and cleanup helpers for BinderBridge."""

import base64
import json
from collections import defaultdict


COLLECTION_DUPLICATE_FIELDS = [
    "game",
    "card_name",
    "set_name",
    "set_code",
    "collector_number",
    "finish",
    "condition",
    "language",
]

WANT_DUPLICATE_FIELDS = [
    "game",
    "card_name",
    "set_name",
    "set_code",
    "collector_number",
    "condition",
    "finish",
    "language",
    "scryfall_id",
]

CONDITION_AUDIT_ALIASES = {
    "near mint": "NM",
    "nm": "NM",
    "lightly played": "LP",
    "light played": "LP",
    "lp": "LP",
    "moderately played": "MP",
    "medium played": "MP",
    "mp": "MP",
    "heavily played": "HP",
    "heavy played": "HP",
    "hp": "HP",
    "damaged": "DMG",
    "damage": "DMG",
    "dmg": "DMG",
    "poor": "DMG",
}

FINISH_AUDIT_ALIASES = {
    "regular": "Regular",
    "normal": "Regular",
    "nonfoil": "Regular",
    "non foil": "Regular",
    "non-foil": "Regular",
    "no": "Regular",
    "false": "Regular",
    "0": "Regular",
    "foil": "Foil",
    "traditional foil": "Foil",
    "yes": "Foil",
    "true": "Foil",
    "1": "Foil",
    "etched": "Etched",
    "etched foil": "Etched",
    "showcase": "Showcase",
    "other": "Other",
}

AUDIT_ISSUE_OPTIONS = [
    ("", "All audit issues"),
    ("missing_condition", "Missing condition"),
    ("missing_finish", "Missing finish"),
    ("invalid_condition", "Unknown condition"),
    ("invalid_finish", "Unknown finish"),
    ("scryfall_finish_mismatch", "Finish not in Scryfall printing"),
    ("normalize_condition", "Can normalize condition"),
    ("normalize_finish", "Can normalize finish"),
    ("trade_needs_review", "Trade cards need review"),
]

AUDIT_ISSUE_LABELS = dict(AUDIT_ISSUE_OPTIONS)

APP_FINISH_TO_SCRYFALL = {
    "Regular": "nonfoil",
    "Foil": "foil",
    "Etched": "etched",
}

SCRYFALL_FINISH_LABELS = {
    "nonfoil": "Regular",
    "foil": "Foil",
    "etched": "Etched",
}


def duplicate_key(parts):
    raw = json.dumps(parts, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def normalized_duplicate_value(item, field):
    value = str(row_value(item, field, "") or "").strip()
    return value.lower()


def item_duplicate_key(item, fields):
    return duplicate_key([normalized_duplicate_value(item, field) for field in fields])


def combine_text_field(items, field, max_length=1000):
    notes = []
    seen = set()
    for item in items:
        note = str(row_value(item, field, "") or "").strip()
        if note and note not in seen:
            notes.append(note)
            seen.add(note)
    return "\n".join(notes)[:max_length]


def combine_notes(items):
    return combine_text_field(items, "notes")


def first_nonblank(items, field):
    for item in items:
        value = row_value(item, field, "")
        if value not in ("", None):
            return value
    return ""


def duplicate_group_label(item):
    parts = [row_value(item, "card_name", "")]
    if row_value(item, "set_code", ""):
        parts.append(f"({row_value(item, 'set_code')})")
    if row_value(item, "collector_number", ""):
        parts.append(f"#{row_value(item, 'collector_number')}")
    return " ".join(str(part) for part in parts if part)


def collection_duplicate_groups(user_id):
    items = rows(
        """
        SELECT *
        FROM collection_items
        WHERE user_id = ?
        ORDER BY card_name COLLATE NOCASE, set_name COLLATE NOCASE, collector_number COLLATE NOCASE, id
        """,
        (user_id,),
    )
    grouped = defaultdict(list)
    for item in items:
        grouped[item_duplicate_key(item, COLLECTION_DUPLICATE_FIELDS)].append(item)
    duplicate_groups = []
    for key, group_items in grouped.items():
        if len(group_items) < 2:
            continue
        total_quantity = sum(int(row_value(item, "quantity", 0) or 0) for item in group_items)
        total_trade = sum(int(row_value(item, "quantity_for_trade", 0) or 0) for item in group_items)
        duplicate_groups.append({
            "key": key,
            "label": duplicate_group_label(group_items[0]),
            "items": group_items,
            "count": len(group_items),
            "quantity": total_quantity,
            "trade_quantity": min(total_trade, total_quantity),
        })
    return duplicate_groups


def want_duplicate_groups(user_id):
    items = rows(
        """
        SELECT *
        FROM want_items
        WHERE user_id = ?
        ORDER BY card_name COLLATE NOCASE, set_name COLLATE NOCASE, collector_number COLLATE NOCASE, id
        """,
        (user_id,),
    )
    grouped = defaultdict(list)
    for item in items:
        grouped[item_duplicate_key(item, WANT_DUPLICATE_FIELDS)].append(item)
    duplicate_groups = []
    for key, group_items in grouped.items():
        if len(group_items) < 2:
            continue
        total_quantity = sum(int(row_value(item, "desired_quantity", 0) or 0) for item in group_items)
        duplicate_groups.append({
            "key": key,
            "label": duplicate_group_label(group_items[0]),
            "items": group_items,
            "count": len(group_items),
            "desired_quantity": total_quantity,
        })
    return duplicate_groups


def duplicate_cleanup_summary(user_id):
    collection_groups = collection_duplicate_groups(user_id)
    want_groups = want_duplicate_groups(user_id)
    return {
        "collection_groups": collection_groups,
        "want_groups": want_groups,
        "collection_duplicate_rows": sum(group["count"] - 1 for group in collection_groups),
        "want_duplicate_rows": sum(group["count"] - 1 for group in want_groups),
    }


def selected_duplicate_groups(all_groups, selected_keys):
    selected = {str(key) for key in selected_keys if str(key).strip()}
    if not selected:
        return []
    return [group for group in all_groups if group["key"] in selected]


def merge_collection_group_rows(conn, keep_id, duplicate_id, max_quantity):
    group_rows = conn.execute(
        "SELECT * FROM group_collection_items WHERE collection_item_id = ?",
        (duplicate_id,),
    ).fetchall()
    for group_row in group_rows:
        existing = conn.execute(
            "SELECT * FROM group_collection_items WHERE group_id = ? AND collection_item_id = ?",
            (group_row["group_id"], keep_id),
        ).fetchone()
        if existing:
            merged_quantity = min(max_quantity, int(existing["quantity"] or 0) + int(group_row["quantity"] or 0))
            conn.execute(
                "UPDATE group_collection_items SET quantity = ?, updated_at = ? WHERE id = ?",
                (merged_quantity, now_iso(), existing["id"]),
            )
            conn.execute("DELETE FROM group_collection_items WHERE id = ?", (group_row["id"],))
        else:
            conn.execute(
                "UPDATE group_collection_items SET collection_item_id = ?, updated_at = ? WHERE id = ?",
                (keep_id, now_iso(), group_row["id"]),
            )


def move_collection_unique_rows(conn, table_name, keep_id, duplicate_id, unique_field):
    rows_to_move = conn.execute(f"SELECT * FROM {table_name} WHERE collection_item_id = ?", (duplicate_id,)).fetchall()
    for item in rows_to_move:
        existing = conn.execute(
            f"SELECT id FROM {table_name} WHERE collection_item_id = ? AND {unique_field} = ?",
            (keep_id, item[unique_field]),
        ).fetchone()
        if existing:
            conn.execute(f"DELETE FROM {table_name} WHERE id = ?", (item["id"],))
        else:
            conn.execute(f"UPDATE {table_name} SET collection_item_id = ? WHERE id = ?", (keep_id, item["id"]))


def move_collection_single_reference(conn, table_name, keep_id, duplicate_id):
    rows_to_move = conn.execute(f"SELECT * FROM {table_name} WHERE collection_item_id = ?", (duplicate_id,)).fetchall()
    keep_existing = conn.execute(f"SELECT id FROM {table_name} WHERE collection_item_id = ?", (keep_id,)).fetchone()
    for item in rows_to_move:
        if keep_existing:
            conn.execute(f"DELETE FROM {table_name} WHERE id = ?", (item["id"],))
        else:
            conn.execute(f"UPDATE {table_name} SET collection_item_id = ? WHERE id = ?", (keep_id, item["id"]))
            keep_existing = item


def merge_collection_duplicate_group(conn, group):
    items = sorted(group["items"], key=lambda item: item["id"])
    keep = items[0]
    duplicates = items[1:]
    keep_id = keep["id"]
    duplicate_ids = [item["id"] for item in duplicates]
    total_quantity = sum(int(row_value(item, "quantity", 0) or 0) for item in items)
    total_trade = min(total_quantity, sum(int(row_value(item, "quantity_for_trade", 0) or 0) for item in items))
    metadata = {field: first_nonblank(items, field) for field in SCRYFALL_COLLECTION_FIELDS}
    legacy_provider_fields = {
        "tcgplayer_product_id": first_nonblank(items, "tcgplayer_product_id"),
        "cardmarket_product_id": first_nonblank(items, "cardmarket_product_id"),
        "cardkingdom_sku": first_nonblank(items, "cardkingdom_sku"),
    }
    price_source = first_nonblank(items, "price_source")
    price_refreshed_at = first_nonblank(items, "price_refreshed_at")
    price_status = first_nonblank(items, "price_status")
    condition_notes = combine_text_field(items, "condition_notes")
    notes = combine_notes(items)
    is_public = 1 if any(int(row_value(item, "is_public", 1) or 0) for item in items) else 0
    timestamp = now_iso()

    conn.execute(
        """
        UPDATE collection_items
        SET quantity = ?, quantity_for_trade = ?, condition_notes = ?, notes = ?, is_public = ?,
            scryfall_id = ?, image_url = ?, mana_cost = ?, type_line = ?, oracle_text = ?,
            rarity = ?, colors = ?, color_identity = ?, scryfall_uri = ?, price_usd = ?,
            price_source = ?, tcgplayer_product_id = ?, cardmarket_product_id = ?,
            cardkingdom_sku = ?, price_refreshed_at = ?, price_status = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            total_quantity,
            total_trade,
            condition_notes,
            notes,
            is_public,
            metadata["scryfall_id"],
            metadata["image_url"],
            metadata["mana_cost"],
            metadata["type_line"],
            metadata["oracle_text"],
            metadata["rarity"],
            metadata["colors"],
            metadata["color_identity"],
            metadata["scryfall_uri"],
            metadata["price_usd"],
            price_source,
            legacy_provider_fields["tcgplayer_product_id"],
            legacy_provider_fields["cardmarket_product_id"],
            legacy_provider_fields["cardkingdom_sku"],
            price_refreshed_at,
            price_status,
            timestamp,
            keep_id,
        ),
    )

    for duplicate_id in duplicate_ids:
        merge_collection_group_rows(conn, keep_id, duplicate_id, total_quantity)
        conn.execute(
            "UPDATE trade_items SET collection_item_id = ? WHERE collection_item_id = ? AND owner_id = ?",
            (keep_id, duplicate_id, keep["user_id"]),
        )
        conn.execute("UPDATE price_history SET collection_item_id = ? WHERE collection_item_id = ?", (keep_id, duplicate_id))
        move_collection_single_reference(conn, "scryfall_enrichment_jobs", keep_id, duplicate_id)
        move_collection_unique_rows(conn, "card_price_sources", keep_id, duplicate_id, "provider")
        move_collection_unique_rows(conn, "price_refresh_jobs", keep_id, duplicate_id, "provider")
        move_collection_unique_rows(conn, "collection_item_photos", keep_id, duplicate_id, "checksum_sha256")
        conn.execute("DELETE FROM collection_items WHERE id = ?", (duplicate_id,))
    return len(duplicate_ids)


def cleanup_collection_duplicates(user_id, selected_keys=None):
    groups = collection_duplicate_groups(user_id)
    selected = selected_duplicate_groups(groups, selected_keys) if selected_keys is not None else groups
    if not selected:
        return {"groups": 0, "merged": 0}
    with db() as conn:
        merged = 0
        for group in selected:
            merged += merge_collection_duplicate_group(conn, group)
    return {"groups": len(selected), "merged": merged}


def merge_want_group_rows(conn, keep_id, duplicate_id):
    group_rows = conn.execute(
        "SELECT * FROM group_want_items WHERE want_item_id = ?",
        (duplicate_id,),
    ).fetchall()
    for group_row in group_rows:
        existing = conn.execute(
            "SELECT * FROM group_want_items WHERE group_id = ? AND want_item_id = ?",
            (group_row["group_id"], keep_id),
        ).fetchone()
        if existing:
            conn.execute("DELETE FROM group_want_items WHERE id = ?", (group_row["id"],))
        else:
            conn.execute(
                "UPDATE group_want_items SET want_item_id = ?, updated_at = ? WHERE id = ?",
                (keep_id, now_iso(), group_row["id"]),
            )


def merge_want_duplicate_group(conn, group):
    items = sorted(group["items"], key=lambda item: item["id"])
    keep = items[0]
    duplicates = items[1:]
    keep_id = keep["id"]
    total_quantity = sum(int(row_value(item, "desired_quantity", 0) or 0) for item in items)
    metadata = {field: first_nonblank(items, field) for field in SCRYFALL_COLLECTION_FIELDS}
    notes = combine_notes(items)
    preferred_printing_notes = combine_text_field(items, "preferred_printing_notes")
    priority = max(items, key=lambda item: want_priority_rank(row_value(item, "priority", "normal")))
    priority = normalize_want_priority(row_value(priority, "priority", "normal"))
    budgets = [
        normalize_price_usd(row_value(item, "budget_cap_usd", ""))
        for item in items
        if normalize_price_usd(row_value(item, "budget_cap_usd", ""))
    ]
    budget_cap_usd = min(budgets, key=price_to_cents) if budgets else ""
    is_public = 1 if any(int(row_value(item, "is_public", 1) or 0) for item in items) else 0

    conn.execute(
        """
        UPDATE want_items
        SET desired_quantity = ?, priority = ?, budget_cap_usd = ?,
            preferred_printing_notes = ?, notes = ?, is_public = ?,
            scryfall_id = ?, image_url = ?, mana_cost = ?, type_line = ?, oracle_text = ?,
            rarity = ?, colors = ?, color_identity = ?, scryfall_uri = ?, price_usd = ?,
            price_source = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            total_quantity,
            priority,
            budget_cap_usd,
            preferred_printing_notes,
            notes,
            is_public,
            metadata["scryfall_id"],
            metadata["image_url"],
            metadata["mana_cost"],
            metadata["type_line"],
            metadata["oracle_text"],
            metadata["rarity"],
            metadata["colors"],
            metadata["color_identity"],
            metadata["scryfall_uri"],
            metadata["price_usd"],
            first_nonblank(items, "price_source"),
            now_iso(),
            keep_id,
        ),
    )
    for duplicate in duplicates:
        merge_want_group_rows(conn, keep_id, duplicate["id"])
        conn.execute("DELETE FROM want_items WHERE id = ?", (duplicate["id"],))
    return len(duplicates)


def cleanup_want_duplicates(user_id, selected_keys=None):
    groups = want_duplicate_groups(user_id)
    selected = selected_duplicate_groups(groups, selected_keys) if selected_keys is not None else groups
    if not selected:
        return {"groups": 0, "merged": 0}
    with db() as conn:
        merged = 0
        for group in selected:
            merged += merge_want_duplicate_group(conn, group)
    return {"groups": len(selected), "merged": merged}


def audit_query_value(query, key, default=""):
    values = query.get(key, [default]) if isinstance(query, dict) else [default]
    if isinstance(values, (list, tuple)):
        return str(values[0] if values else default).strip()
    return str(values or default).strip()


def normalize_audit_text(value):
    return " ".join(str(value or "").strip().replace("_", " ").replace("-", " ").lower().split())


def audit_condition_suggestion(value):
    text = str(value or "").strip()
    if not text:
        return ""
    upper = text.upper()
    if upper in CONDITION_OPTIONS:
        return upper
    return CONDITION_AUDIT_ALIASES.get(normalize_audit_text(text))


def audit_finish_suggestion(value):
    text = str(value or "").strip()
    if not text:
        return ""
    for option in FINISH_OPTIONS:
        if text.lower() == option.lower():
            return option
    return FINISH_AUDIT_ALIASES.get(normalize_audit_text(text))


def parse_scryfall_finishes(value):
    if isinstance(value, (list, tuple)):
        raw_values = value
    else:
        text = str(value or "").strip()
        if not text:
            return []
        try:
            decoded = json.loads(text)
            raw_values = decoded if isinstance(decoded, list) else []
        except (TypeError, ValueError, json.JSONDecodeError):
            raw_values = text.split(",")
    normalized = []
    for raw in raw_values:
        finish = str(raw or "").strip().lower()
        if finish in SCRYFALL_FINISH_LABELS and finish not in normalized:
            normalized.append(finish)
    return normalized


def scryfall_finish_labels(finishes):
    return [SCRYFALL_FINISH_LABELS.get(finish, finish.title()) for finish in finishes]


def split_finish_values_for_scryfall_check(value):
    values = []
    for part in str(value or "").split(","):
        cleaned = part.strip()
        if not cleaned:
            continue
        normalized = audit_finish_suggestion(cleaned) or cleaned
        if normalized not in values:
            values.append(normalized)
    return values


def scryfall_cache_finishes(row_data):
    if not row_data:
        return []
    raw_json = row_value(row_data, "raw_json", "")
    if not raw_json:
        return []
    try:
        decoded = json.loads(raw_json)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(decoded, dict):
        return []
    return parse_scryfall_finishes(decoded.get("finishes") or [])


def scryfall_print_finishes_for_item_conn(conn, item):
    if row_value(item, "game", "mtg") != "mtg":
        return []
    scryfall_id = row_value(item, "scryfall_id", "")
    set_code = row_value(item, "set_code", "")
    collector_number = row_value(item, "collector_number", "")
    found = None
    if scryfall_id:
        found = conn.execute("SELECT finishes FROM scryfall_bulk_cards WHERE scryfall_id = ?", (scryfall_id,)).fetchone()
    if not found and set_code and collector_number:
        found = conn.execute(
            """
            SELECT finishes
            FROM scryfall_bulk_cards
            WHERE set_code = ? COLLATE NOCASE AND collector_number = ? COLLATE NOCASE
            ORDER BY released_at DESC
            LIMIT 1
            """,
            (set_code.upper(), collector_number),
        ).fetchone()
    if found:
        finishes = parse_scryfall_finishes(row_value(found, "finishes", ""))
        if finishes:
            return finishes
    cache_row = None
    if scryfall_id:
        cache_row = conn.execute("SELECT raw_json FROM scryfall_cache WHERE scryfall_id = ? ORDER BY fetched_at DESC LIMIT 1", (scryfall_id,)).fetchone()
    if not cache_row and set_code and collector_number:
        cache_row = conn.execute(
            """
            SELECT raw_json
            FROM scryfall_cache
            WHERE set_code = ? COLLATE NOCASE AND collector_number = ? COLLATE NOCASE
            ORDER BY fetched_at DESC
            LIMIT 1
            """,
            (set_code.upper(), collector_number),
        ).fetchone()
    return scryfall_cache_finishes(cache_row)


def scryfall_print_finishes_for_item(item):
    with db() as conn:
        return scryfall_print_finishes_for_item_conn(conn, item)


def scryfall_finish_check_result(item, finish_values=None):
    result = {
        "checked": False,
        "mismatch": False,
        "available_finishes": [],
        "available_labels": "",
        "selected_finishes": [],
        "mismatched_finishes": [],
        "mismatched_labels": "",
    }
    if row_value(item, "game", "mtg") != "mtg":
        return result
    if not row_value(item, "scryfall_id", "") and not (row_value(item, "set_code", "") and row_value(item, "collector_number", "")):
        return result
    available = scryfall_print_finishes_for_item(item)
    if not available:
        return result
    selected_finishes = split_finish_values_for_scryfall_check(
        row_value(item, "finish", "") if finish_values is None else finish_values
    )
    mismatched = []
    for finish in selected_finishes:
        scryfall_finish = APP_FINISH_TO_SCRYFALL.get(finish)
        if scryfall_finish and scryfall_finish not in available:
            mismatched.append(finish)
    result.update({
        "checked": True,
        "mismatch": bool(mismatched),
        "available_finishes": available,
        "available_labels": ", ".join(scryfall_finish_labels(available)),
        "selected_finishes": selected_finishes,
        "mismatched_finishes": mismatched,
        "mismatched_labels": ", ".join(mismatched),
    })
    return result


def scryfall_finish_check_message(item, finish_values=None):
    check = scryfall_finish_check_result(item, finish_values=finish_values)
    if not check["mismatch"]:
        return ""
    card_name = row_value(item, "card_name", "This card") or "This card"
    selected = check["mismatched_labels"]
    available = check["available_labels"]
    return f"{card_name}: Scryfall lists this printing as available in {available}, but {selected} was selected."


def ensure_scryfall_finish_allowed(item, allow_override=False, finish_values=None):
    message = scryfall_finish_check_message(item, finish_values=finish_values)
    if message and not allow_override:
        raise ValueError(f"{message} Use the Scryfall finish override if this is an intentional odd-case entry.")
    return message


def condition_finish_audit_filter_values(query):
    filters = {
        "q": audit_query_value(query, "q"),
        "game": audit_query_value(query, "game"),
        "set_name": audit_query_value(query, "set_name"),
        "condition": audit_query_value(query, "condition").upper(),
        "finish": audit_query_value(query, "finish"),
        "issue": audit_query_value(query, "issue"),
        "trade_only": audit_query_value(query, "trade_only") == "1",
    }
    if filters["game"] and filters["game"] not in dict(CARD_GAMES):
        filters["game"] = ""
    if filters["condition"] and filters["condition"] not in CONDITION_OPTIONS:
        filters["condition"] = ""
    if filters["finish"] and filters["finish"] not in FINISH_OPTIONS:
        filters["finish"] = ""
    if filters["issue"] and filters["issue"] not in AUDIT_ISSUE_LABELS:
        filters["issue"] = ""
    return filters


def condition_finish_audit_hidden_inputs(filters):
    hidden = []
    for key, value in filters.items():
        if value in ("", None, False):
            continue
        hidden.append(f'<input type="hidden" name="{e(key)}" value="{e("1" if value is True else value)}">')
    return "".join(hidden)


def condition_finish_audit_details(item, conn=None):
    condition_text = str(row_value(item, "condition", "") or "").strip()
    finish_text = str(row_value(item, "finish", "") or "").strip()
    suggested_condition = audit_condition_suggestion(condition_text)
    suggested_finish = audit_finish_suggestion(finish_text)
    canonical_finish = suggested_finish or finish_text
    available_finishes = scryfall_print_finishes_for_item_conn(conn, item) if conn is not None else scryfall_print_finishes_for_item(item)
    condition_missing = condition_text == ""
    finish_missing = finish_text == ""
    condition_invalid = bool(condition_text and suggested_condition is None)
    finish_invalid = bool(finish_text and suggested_finish is None)
    normalize_condition = bool(suggested_condition and suggested_condition != condition_text)
    normalize_finish = bool(suggested_finish and suggested_finish != finish_text)
    expected_scryfall_finish = APP_FINISH_TO_SCRYFALL.get(canonical_finish, "")
    scryfall_finish_mismatch = bool(
        finish_text
        and expected_scryfall_finish
        and available_finishes
        and expected_scryfall_finish not in available_finishes
    )
    trade_needs_review = int(row_value(item, "quantity_for_trade", 0) or 0) > 0 and (
        condition_missing
        or finish_missing
        or condition_invalid
        or finish_invalid
        or normalize_condition
        or normalize_finish
        or scryfall_finish_mismatch
    )
    issues = []
    for key, active in (
        ("missing_condition", condition_missing),
        ("invalid_condition", condition_invalid),
        ("normalize_condition", normalize_condition),
        ("missing_finish", finish_missing),
        ("invalid_finish", finish_invalid),
        ("scryfall_finish_mismatch", scryfall_finish_mismatch),
        ("normalize_finish", normalize_finish),
        ("trade_needs_review", trade_needs_review),
    ):
        if active:
            issues.append(key)
    enriched = dict(item)
    enriched.update({
        "condition_missing": condition_missing,
        "finish_missing": finish_missing,
        "condition_invalid": condition_invalid,
        "finish_invalid": finish_invalid,
        "normalize_condition": normalize_condition,
        "normalize_finish": normalize_finish,
        "scryfall_finish_mismatch": scryfall_finish_mismatch,
        "trade_needs_review": trade_needs_review,
        "suggested_condition": suggested_condition or "",
        "suggested_finish": suggested_finish or "",
        "scryfall_finishes": ",".join(available_finishes),
        "scryfall_finish_labels": ", ".join(scryfall_finish_labels(available_finishes)),
        "issues": issues,
        "issue_labels": [AUDIT_ISSUE_LABELS[key] for key in issues],
    })
    return enriched


def collection_condition_finish_audit_rows(user_id, filters=None):
    filters = filters or {}
    where = ["user_id = ?"]
    params = [user_id]
    if filters.get("q"):
        where.append("(card_name LIKE ? OR type_line LIKE ?)")
        term = f"%{filters['q']}%"
        params.extend([term, term])
    if filters.get("game"):
        where.append("game = ?")
        params.append(filters["game"])
    if filters.get("set_name"):
        where.append("set_name LIKE ?")
        params.append(f"%{filters['set_name']}%")
    if filters.get("condition"):
        where.append("condition = ?")
        params.append(filters["condition"])
    if filters.get("finish"):
        where.append("finish = ?")
        params.append(filters["finish"])
    if filters.get("trade_only"):
        where.append("quantity_for_trade > 0")
    with db() as conn:
        items = conn.execute(
            f"""
            SELECT *
            FROM collection_items
            WHERE {' AND '.join(where)}
            ORDER BY card_name COLLATE NOCASE, set_name COLLATE NOCASE, collector_number COLLATE NOCASE, id
            """,
            params,
        ).fetchall()
        audited = [condition_finish_audit_details(item, conn=conn) for item in items]
    issue = filters.get("issue", "")
    if issue:
        audited = [item for item in audited if issue in item["issues"]]
    return [item for item in audited if item["issues"]]


def condition_finish_audit_summary(user_id):
    summary = {
        "total": 0,
        "missing_condition": 0,
        "missing_finish": 0,
        "invalid_condition": 0,
        "invalid_finish": 0,
        "scryfall_finish_mismatch": 0,
        "normalize_condition": 0,
        "normalize_finish": 0,
        "trade_needs_review": 0,
    }
    for item in collection_condition_finish_audit_rows(user_id, {}):
        summary["total"] += 1
        for issue in item["issues"]:
            summary[issue] += 1
    return summary


def parse_condition_finish_audit_update(form):
    condition = audit_query_value(form, "new_condition").upper()
    finish = audit_query_value(form, "new_finish")
    if condition and condition not in CONDITION_OPTIONS:
        raise ValueError("Choose a valid condition.")
    if finish and finish not in FINISH_OPTIONS:
        raise ValueError("Choose a valid finish.")
    if not condition and not finish:
        raise ValueError("Choose a condition or finish value to apply.")
    return condition or None, finish or None


def clean_collection_item_ids(item_ids):
    clean_ids = []
    seen = set()
    for value in item_ids:
        try:
            item_id = int(value)
        except (TypeError, ValueError):
            continue
        if item_id in seen:
            continue
        clean_ids.append(item_id)
        seen.add(item_id)
    return clean_ids


def update_collection_condition_finish_by_ids(user_id, item_ids, condition=None, finish=None):
    clean_ids = clean_collection_item_ids(item_ids)
    if not clean_ids:
        return 0
    updates = []
    params = []
    if condition is not None:
        updates.append("condition = ?")
        params.append(condition)
    if finish is not None:
        updates.append("finish = ?")
        params.append(finish)
    if not updates:
        return 0
    updates.append("updated_at = ?")
    params.append(now_iso())
    placeholders = ",".join("?" for _ in clean_ids)
    with db() as conn:
        cursor = conn.execute(
            f"""
            UPDATE collection_items
            SET {', '.join(updates)}
            WHERE user_id = ? AND id IN ({placeholders})
            """,
            [*params, user_id, *clean_ids],
        )
        return cursor.rowcount


def update_collection_condition_finish_matching(user_id, filters, condition=None, finish=None):
    item_ids = [item["id"] for item in collection_condition_finish_audit_rows(user_id, filters)]
    return update_collection_condition_finish_by_ids(user_id, item_ids, condition, finish)


def normalize_collection_condition_finish_by_ids(user_id, item_ids):
    clean_ids = clean_collection_item_ids(item_ids)
    if not clean_ids:
        return 0
    placeholders = ",".join("?" for _ in clean_ids)
    updated = 0
    with db() as conn:
        items = conn.execute(
            f"SELECT * FROM collection_items WHERE user_id = ? AND id IN ({placeholders})",
            [user_id, *clean_ids],
        ).fetchall()
        for item in items:
            audited = condition_finish_audit_details(item)
            new_condition = audited["suggested_condition"] if audited["normalize_condition"] else item["condition"]
            new_finish = audited["suggested_finish"] if audited["normalize_finish"] else item["finish"]
            if new_condition == item["condition"] and new_finish == item["finish"]:
                continue
            conn.execute(
                """
                UPDATE collection_items
                SET condition = ?, finish = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (new_condition, new_finish, now_iso(), item["id"], user_id),
            )
            updated += 1
    return updated


def normalize_collection_condition_finish_matching(user_id, filters):
    item_ids = [item["id"] for item in collection_condition_finish_audit_rows(user_id, filters)]
    return normalize_collection_condition_finish_by_ids(user_id, item_ids)


__all__ = [
    "COLLECTION_DUPLICATE_FIELDS",
    "WANT_DUPLICATE_FIELDS",
    "CONDITION_AUDIT_ALIASES",
    "FINISH_AUDIT_ALIASES",
    "AUDIT_ISSUE_OPTIONS",
    "AUDIT_ISSUE_LABELS",
    "APP_FINISH_TO_SCRYFALL",
    "SCRYFALL_FINISH_LABELS",
    "duplicate_key",
    "normalized_duplicate_value",
    "item_duplicate_key",
    "combine_notes",
    "first_nonblank",
    "duplicate_group_label",
    "collection_duplicate_groups",
    "want_duplicate_groups",
    "duplicate_cleanup_summary",
    "selected_duplicate_groups",
    "merge_collection_group_rows",
    "move_collection_unique_rows",
    "move_collection_single_reference",
    "merge_collection_duplicate_group",
    "cleanup_collection_duplicates",
    "merge_want_group_rows",
    "merge_want_duplicate_group",
    "cleanup_want_duplicates",
    "audit_query_value",
    "normalize_audit_text",
    "audit_condition_suggestion",
    "audit_finish_suggestion",
    "parse_scryfall_finishes",
    "scryfall_finish_labels",
    "split_finish_values_for_scryfall_check",
    "scryfall_cache_finishes",
    "scryfall_print_finishes_for_item_conn",
    "scryfall_print_finishes_for_item",
    "scryfall_finish_check_result",
    "scryfall_finish_check_message",
    "ensure_scryfall_finish_allowed",
    "condition_finish_audit_filter_values",
    "condition_finish_audit_hidden_inputs",
    "condition_finish_audit_details",
    "collection_condition_finish_audit_rows",
    "condition_finish_audit_summary",
    "parse_condition_finish_audit_update",
    "clean_collection_item_ids",
    "update_collection_condition_finish_by_ids",
    "update_collection_condition_finish_matching",
    "normalize_collection_condition_finish_by_ids",
    "normalize_collection_condition_finish_matching",
]
