"""Import preview batch tracking and undo support for BinderBridge."""

import json

def add_import_warning(result, message):
    result["warning_count"] += 1
    if len(result["warnings"]) < 12:
        result["warnings"].append(message)

IMPORT_BATCH_COLLECTION_FIELDS = (
    "game",
    "card_name",
    "set_name",
    "set_code",
    "collector_number",
    "finish",
    "condition",
    "condition_notes",
    "language",
    "quantity",
    "quantity_for_trade",
    "scryfall_id",
    "image_url",
    "mana_cost",
    "type_line",
    "oracle_text",
    "rarity",
    "colors",
    "color_identity",
    "scryfall_uri",
    "price_usd",
    "price_source",
    "tcgplayer_product_id",
    "cardmarket_product_id",
    "cardkingdom_sku",
    "price_refreshed_at",
    "price_status",
    "notes",
    "is_public",
    "created_at",
    "updated_at",
)

def record_state(record):
    if not record:
        return ""
    return json.dumps({key: record[key] for key in record.keys()}, ensure_ascii=True, sort_keys=True)

def load_record_state(value):
    try:
        data = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}

def import_batch_summary_json(summary):
    return json.dumps(summary or {}, ensure_ascii=True, sort_keys=True)

def import_batch_payload_json(payload):
    return json.dumps(payload or {}, ensure_ascii=True, sort_keys=True)

def create_import_batch(user_id, import_type, source="", status="preview", summary=None, payload=None, group_id=0):
    timestamp = now_iso()
    return execute(
        """
        INSERT INTO import_batches
            (user_id, group_id, import_type, source, status, summary_json, payload_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            int(group_id or 0),
            sanitize_text_input(import_type, max_length=40).strip(),
            sanitize_text_input(source, max_length=40).strip(),
            sanitize_text_input(status, max_length=40).strip(),
            import_batch_summary_json(summary),
            import_batch_payload_json(payload),
            timestamp,
            timestamp,
        ),
    )

def update_import_batch(batch_id, status=None, summary=None, payload=None, undone=False):
    assignments = ["updated_at = ?"]
    params = [now_iso()]
    if status is not None:
        assignments.append("status = ?")
        params.append(sanitize_text_input(status, max_length=40).strip())
    if summary is not None:
        assignments.append("summary_json = ?")
        params.append(import_batch_summary_json(summary))
    if payload is not None:
        assignments.append("payload_json = ?")
        params.append(import_batch_payload_json(payload))
    if undone:
        assignments.append("undone_at = ?")
        params.append(now_iso())
    params.append(batch_id)
    execute(f"UPDATE import_batches SET {', '.join(assignments)} WHERE id = ?", params)

def import_batch_for_user(user_id, batch_id):
    try:
        batch_id = int(batch_id)
    except (TypeError, ValueError):
        return None
    return row("SELECT * FROM import_batches WHERE id = ? AND user_id = ?", (batch_id, user_id))

def import_batch_payload(batch):
    return load_record_state(row_value(batch, "payload_json", ""))

def import_batch_summary(batch):
    return load_record_state(row_value(batch, "summary_json", ""))

def record_import_batch_item(batch_id, item_type, action, target_table, target_id, previous_state=""):
    if not batch_id:
        return 0
    return execute(
        """
        INSERT INTO import_batch_items
            (batch_id, item_type, action, target_table, target_id, previous_state, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            batch_id,
            sanitize_text_input(item_type, max_length=60).strip(),
            sanitize_text_input(action, max_length=60).strip(),
            sanitize_text_input(target_table, max_length=80).strip(),
            int(target_id or 0),
            previous_state or "",
            now_iso(),
        ),
    )

def recent_import_batches(user_id, import_type=None, group_id=None, limit=6):
    where = ["user_id = ?", "status IN ('applied', 'undone')"]
    params = [user_id]
    if import_type:
        where.append("import_type = ?")
        params.append(import_type)
    if group_id is not None:
        where.append("group_id = ?")
        params.append(int(group_id or 0))
    return rows(
        f"""
        SELECT *
        FROM import_batches
        WHERE {' AND '.join(where)}
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        params + [int(limit or 6)],
    )

def restore_collection_item_state(item_id, user_id, previous_state):
    state = load_record_state(previous_state)
    if not state:
        return 0
    existing = row("SELECT id FROM collection_items WHERE id = ? AND user_id = ?", (item_id, user_id))
    if not existing:
        return 0
    assignments = []
    params = []
    for field in IMPORT_BATCH_COLLECTION_FIELDS:
        if field in state:
            assignments.append(f"{field} = ?")
            params.append(state[field])
    if not assignments:
        return 0
    params.extend([item_id, user_id])
    execute(
        f"UPDATE collection_items SET {', '.join(assignments)} WHERE id = ? AND user_id = ?",
        params,
    )
    return 1

def undo_collection_import_item(user_id, batch, item):
    target_id = int(item["target_id"] or 0)
    if not target_id:
        return 0
    if item["action"] == "inserted":
        existing = row("SELECT id FROM collection_items WHERE id = ? AND user_id = ?", (target_id, user_id))
        if not existing:
            return 0
        execute("DELETE FROM collection_items WHERE id = ? AND user_id = ?", (target_id, user_id))
        return 1
    if item["action"] == "updated":
        restored = restore_collection_item_state(target_id, user_id, item["previous_state"])
        execute(
            """
            DELETE FROM scryfall_enrichment_jobs
            WHERE collection_item_id = ?
                AND user_id = ?
                AND status IN ('pending', 'processing')
                AND created_at >= ?
            """,
            (target_id, user_id, batch["created_at"]),
        )
        return restored
    return 0

def restore_group_collection_item_state(user_id, previous_state):
    state = load_record_state(previous_state)
    if not state:
        return 0
    group_id = int(state.get("group_id") or 0)
    group = user_group(user_id, group_id)
    if not group:
        return 0
    existing = row("SELECT * FROM group_collection_items WHERE id = ? AND group_id = ?", (state.get("id"), group_id))
    if not existing:
        return 0
    execute(
        """
        UPDATE group_collection_items
        SET collection_item_id = ?, quantity = ?, created_at = ?, updated_at = ?
        WHERE id = ? AND group_id = ?
        """,
        (
            int(state.get("collection_item_id") or 0),
            max(1, int(state.get("quantity") or 1)),
            state.get("created_at") or now_iso(),
            state.get("updated_at") or now_iso(),
            int(state.get("id") or 0),
            group_id,
        ),
    )
    return 1

def undo_group_collection_import_item(user_id, item):
    target_id = int(item["target_id"] or 0)
    if not target_id:
        return 0
    found = row(
        """
        SELECT group_collection_items.*
        FROM group_collection_items
        JOIN card_groups ON card_groups.id = group_collection_items.group_id
        WHERE group_collection_items.id = ? AND card_groups.user_id = ?
        """,
        (target_id, user_id),
    )
    if item["action"] == "inserted":
        if not found:
            return 0
        execute("DELETE FROM group_collection_items WHERE id = ?", (target_id,))
        return 1
    if item["action"] == "updated":
        return restore_group_collection_item_state(user_id, item["previous_state"])
    return 0

def undo_import_batch(user_id, batch_id):
    batch = import_batch_for_user(user_id, batch_id)
    if not batch or batch["status"] != "applied":
        raise ValueError("That import cannot be undone.")
    batch_items = rows(
        """
        SELECT *
        FROM import_batch_items
        WHERE batch_id = ?
        ORDER BY id DESC
        """,
        (batch["id"],),
    )
    changed = 0
    for item in batch_items:
        if item["item_type"] == "collection_item":
            changed += undo_collection_import_item(user_id, batch, item)
        elif item["item_type"] == "group_collection_item":
            changed += undo_group_collection_import_item(user_id, item)
    update_import_batch(batch["id"], status="undone", summary={**import_batch_summary(batch), "undone_items": changed}, payload={}, undone=True)
    return {"batch_id": batch["id"], "undone_items": changed, "import_type": batch["import_type"], "group_id": batch["group_id"]}

__all__ = [
    'add_import_warning',
    'IMPORT_BATCH_COLLECTION_FIELDS',
    'record_state',
    'load_record_state',
    'import_batch_summary_json',
    'import_batch_payload_json',
    'create_import_batch',
    'update_import_batch',
    'import_batch_for_user',
    'import_batch_payload',
    'import_batch_summary',
    'record_import_batch_item',
    'recent_import_batches',
    'restore_collection_item_state',
    'undo_collection_import_item',
    'restore_group_collection_item_state',
    'undo_group_collection_import_item',
    'undo_import_batch',
]
