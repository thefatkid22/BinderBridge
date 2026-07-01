"""Extracted BinderBridge feature code.

The app facade injects shared helpers/constants into this module at import time
so the legacy app.py public API remains compatible during the split.
"""

def normalize_group_type(value):
    group_type = sanitize_text_input(value, max_length=20).strip().lower()
    if group_type not in dict(GROUP_TYPE_OPTIONS):
        raise ValueError("Choose deck, binder, or wishlist.")
    return group_type


def group_type_label(group_type, plural=False):
    label = dict(GROUP_TYPE_OPTIONS).get(group_type, group_type.title())
    return f"{label}s" if plural else label


def create_card_group(
    user_id,
    group_type,
    name,
    description="",
    is_public=True,
    visibility=None,
    default_item_visibility="members",
    show_values=True,
    show_photos=True,
):
    group_type = normalize_group_type(group_type)
    name = sanitize_text_input(name, max_length=80).strip()
    description = sanitize_text_input(description, max_length=1000).strip()
    if not name:
        raise ValueError("Group name is required.")
    visibility = normalize_visibility(
        visibility,
        default=VISIBILITY_MEMBERS if is_public else VISIBILITY_PRIVATE,
    )
    default_item_visibility = normalize_visibility(default_item_visibility)
    timestamp = now_iso()
    return execute(
        """
        INSERT INTO card_groups
            (user_id, group_type, name, description, is_public, visibility, default_item_visibility,
             show_values, show_photos, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            group_type,
            name,
            description,
            visibility_to_public_flag(visibility),
            visibility,
            default_item_visibility,
            1 if show_values else 0,
            1 if show_photos else 0,
            timestamp,
            timestamp,
        ),
    )


def user_group(user_id, group_id):
    return row("SELECT * FROM card_groups WHERE id = ? AND user_id = ?", (group_id, user_id))


def group_summary_rows(user_id):
    return rows(
        """
        SELECT
            card_groups.*,
            COALESCE((SELECT SUM(quantity) FROM group_collection_items WHERE group_id = card_groups.id), 0) AS collection_quantity,
            (SELECT COUNT(*) FROM group_collection_items WHERE group_id = card_groups.id) AS collection_entries,
            (SELECT COUNT(*) FROM group_want_items WHERE group_id = card_groups.id) AS want_entries
        FROM card_groups
        WHERE user_id = ?
        ORDER BY group_type, name COLLATE NOCASE
        """,
        (user_id,),
    )


def _group_item_filter_sql(filters, table_alias, wishlist=False):
    filters = filters or {}
    where = []
    params = []
    if filters.get("q"):
        where.append(f"({table_alias}.card_name LIKE ? OR {table_alias}.type_line LIKE ? OR {table_alias}.set_name LIKE ?)")
        term = f"%{filters['q']}%"
        params.extend([term, term, term])
    if filters.get("game"):
        where.append(f"{table_alias}.game = ?")
        params.append(filters["game"])
    if filters.get("condition"):
        where.append(f"{table_alias}.condition = ?")
        params.append(filters["condition"])
    if filters.get("finish"):
        where.append(f"{table_alias}.finish = ?")
        params.append(filters["finish"])
    if wishlist and filters.get("priority"):
        where.append(f"{table_alias}.priority = ?")
        params.append(filters["priority"])
    return (" AND " + " AND ".join(where) if where else ""), params


def collection_group_item_count(group_id, filters=None):
    filter_sql, params = _group_item_filter_sql(filters, "collection_items")
    return row(
        f"""
        SELECT COUNT(*) AS count
        FROM group_collection_items
        JOIN collection_items ON collection_items.id = group_collection_items.collection_item_id
        WHERE group_collection_items.group_id = ?{filter_sql}
        """,
        [group_id, *params],
    )["count"]


def collection_group_quantity(group_id):
    return row(
        "SELECT COALESCE(SUM(quantity), 0) AS quantity FROM group_collection_items WHERE group_id = ?",
        (group_id,),
    )["quantity"]


def collection_group_items(group_id, order_clause=None, filters=None, limit=None, offset=0):
    order_clause = order_clause or "collection_items.card_name COLLATE NOCASE, collection_items.set_name COLLATE NOCASE"
    filter_sql, params = _group_item_filter_sql(filters, "collection_items")
    limit_sql = " LIMIT ? OFFSET ?" if limit is not None else ""
    if limit is not None:
        params.extend([int(limit), int(offset)])
    return rows(
        f"""
        SELECT
            group_collection_items.id AS group_item_id,
            group_collection_items.quantity AS group_quantity,
            collection_items.*
        FROM group_collection_items
        JOIN collection_items ON collection_items.id = group_collection_items.collection_item_id
        WHERE group_collection_items.group_id = ?{filter_sql}
        ORDER BY {order_clause}
        {limit_sql}
        """,
        [group_id, *params],
    )


def wishlist_group_item_count(group_id, filters=None):
    filter_sql, params = _group_item_filter_sql(filters, "want_items", wishlist=True)
    return row(
        f"""
        SELECT COUNT(*) AS count
        FROM group_want_items
        JOIN want_items ON want_items.id = group_want_items.want_item_id
        WHERE group_want_items.group_id = ?{filter_sql}
        """,
        [group_id, *params],
    )["count"]


def wishlist_group_items(group_id, order_clause=None, filters=None, limit=None, offset=0):
    order_clause = order_clause or "want_items.card_name COLLATE NOCASE, want_items.set_name COLLATE NOCASE"
    filter_sql, params = _group_item_filter_sql(filters, "want_items", wishlist=True)
    limit_sql = " LIMIT ? OFFSET ?" if limit is not None else ""
    if limit is not None:
        params.extend([int(limit), int(offset)])
    return rows(
        f"""
        SELECT
            group_want_items.id AS group_item_id,
            want_items.*
        FROM group_want_items
        JOIN want_items ON want_items.id = group_want_items.want_item_id
        WHERE group_want_items.group_id = ?{filter_sql}
        ORDER BY {order_clause}
        {limit_sql}
        """,
        [group_id, *params],
    )


def add_collection_item_to_group(user_id, group_id, collection_item_id, quantity):
    group = user_group(user_id, group_id)
    if not group or group["group_type"] == "wishlist":
        raise ValueError("Deck and binder groups can use collection cards.")
    item = row("SELECT * FROM collection_items WHERE id = ? AND user_id = ?", (collection_item_id, user_id))
    if not item:
        raise ValueError("Collection card not found.")
    if not str(quantity or "").strip().lstrip("+-").isdigit():
        raise ValueError("Quantity must be a whole number.")
    quantity = clamp_quantity(quantity, 1)
    quantity = min(max(1, quantity), max(1, int(item["quantity"] or 1)))
    timestamp = now_iso()
    execute(
        """
        INSERT INTO group_collection_items (group_id, collection_item_id, quantity, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(group_id, collection_item_id) DO UPDATE SET
            quantity = excluded.quantity,
            updated_at = excluded.updated_at
        """,
        (group_id, collection_item_id, quantity, timestamp, timestamp),
    )


def add_want_item_to_group(user_id, group_id, want_item_id):
    group = user_group(user_id, group_id)
    if not group or group["group_type"] != "wishlist":
        raise ValueError("Wishlist groups can use wanted cards.")
    want = row("SELECT * FROM want_items WHERE id = ? AND user_id = ?", (want_item_id, user_id))
    if not want:
        raise ValueError("Wanted card not found.")
    timestamp = now_iso()
    execute(
        """
        INSERT INTO group_want_items (group_id, want_item_id, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(group_id, want_item_id) DO UPDATE SET
            updated_at = excluded.updated_at
        """,
        (group_id, want_item_id, timestamp, timestamp),
    )


def remove_group_item(user_id, group_id, group_item_id):
    group = user_group(user_id, group_id)
    if not group:
        return 0
    table = "group_want_items" if group["group_type"] == "wishlist" else "group_collection_items"
    with db() as conn:
        cursor = conn.execute(f"DELETE FROM {table} WHERE id = ? AND group_id = ?", (group_item_id, group_id))
        return cursor.rowcount


def _clean_group_item_ids(group_item_ids):
    clean_ids = []
    for value in group_item_ids or []:
        try:
            item_id = int(value)
        except (TypeError, ValueError):
            continue
        if item_id > 0 and item_id not in clean_ids:
            clean_ids.append(item_id)
    return clean_ids


def parse_group_item_quantity(value):
    text = str(value or "").strip()
    if not text.isdigit():
        raise ValueError("Group quantity must be a whole number.")
    quantity = clamp_quantity(text, 1)
    if quantity < 1:
        raise ValueError("Group quantity must be at least 1.")
    return quantity


def _apply_group_collection_quantity_rows(items, quantity):
    timestamp = now_iso()
    updated = 0
    with db() as conn:
        for item in items:
            capped_quantity = min(max(1, int(quantity or 1)), max(1, int(item["owned_quantity"] or 1)))
            cursor = conn.execute(
                "UPDATE group_collection_items SET quantity = ?, updated_at = ? WHERE id = ?",
                (capped_quantity, timestamp, item["group_item_id"]),
            )
            updated += cursor.rowcount
    return updated


def update_group_collection_item_quantities(user_id, group_id, group_item_ids, quantity):
    group = user_group(user_id, group_id)
    if not group or group["group_type"] == "wishlist":
        raise ValueError("Deck and binder groups can update group quantities.")
    clean_ids = _clean_group_item_ids(group_item_ids)
    if not clean_ids:
        return 0
    placeholders = ", ".join("?" for _ in clean_ids)
    quantity = parse_group_item_quantity(quantity)
    items = rows(
        f"""
        SELECT group_collection_items.id AS group_item_id, collection_items.quantity AS owned_quantity
        FROM group_collection_items
        JOIN collection_items ON collection_items.id = group_collection_items.collection_item_id
        WHERE group_collection_items.group_id = ?
          AND collection_items.user_id = ?
          AND group_collection_items.id IN ({placeholders})
        """,
        [group_id, user_id, *clean_ids],
    )
    return _apply_group_collection_quantity_rows(items, quantity)


def update_group_collection_item_quantities_matching(user_id, group_id, filters, quantity):
    group = user_group(user_id, group_id)
    if not group or group["group_type"] == "wishlist":
        raise ValueError("Deck and binder groups can update group quantities.")
    quantity = parse_group_item_quantity(quantity)
    filter_sql, params = _group_item_filter_sql(filters, "collection_items")
    items = rows(
        f"""
        SELECT group_collection_items.id AS group_item_id, collection_items.quantity AS owned_quantity
        FROM group_collection_items
        JOIN collection_items ON collection_items.id = group_collection_items.collection_item_id
        WHERE group_collection_items.group_id = ?
          AND collection_items.user_id = ?{filter_sql}
        """,
        [group_id, user_id, *params],
    )
    return _apply_group_collection_quantity_rows(items, quantity)


def remove_group_items(user_id, group_id, group_item_ids):
    group = user_group(user_id, group_id)
    if not group:
        return 0
    clean_ids = _clean_group_item_ids(group_item_ids)
    if not clean_ids:
        return 0
    table = "group_want_items" if group["group_type"] == "wishlist" else "group_collection_items"
    placeholders = ", ".join("?" for _ in clean_ids)
    with db() as conn:
        cursor = conn.execute(
            f"DELETE FROM {table} WHERE group_id = ? AND id IN ({placeholders})",
            [group_id, *clean_ids],
        )
        return cursor.rowcount


def remove_group_items_matching(user_id, group_id, filters=None):
    group = user_group(user_id, group_id)
    if not group:
        return 0
    if group["group_type"] == "wishlist":
        table = "group_want_items"
        source_table = "want_items"
        source_key = "want_item_id"
        filter_sql, params = _group_item_filter_sql(filters, source_table, wishlist=True)
    else:
        table = "group_collection_items"
        source_table = "collection_items"
        source_key = "collection_item_id"
        filter_sql, params = _group_item_filter_sql(filters, source_table)
    with db() as conn:
        cursor = conn.execute(
            f"""
            DELETE FROM {table}
            WHERE id IN (
                SELECT {table}.id
                FROM {table}
                JOIN {source_table} ON {source_table}.id = {table}.{source_key}
                WHERE {table}.group_id = ?
                  AND {source_table}.user_id = ?{filter_sql}
            )
            """,
            [group_id, user_id, *params],
        )
        return cursor.rowcount


def delete_card_group(user_id, group_id):
    with db() as conn:
        cursor = conn.execute("DELETE FROM card_groups WHERE id = ? AND user_id = ?", (group_id, user_id))
        return cursor.rowcount


def update_card_group(user_id, group_id, name=None, description=None, visibility=None):
    existing = user_group(user_id, group_id)
    if not existing:
        return 0
    name = sanitize_text_input(row_value(existing, "name", "") if name is None else name, max_length=80).strip()
    description = sanitize_text_input(
        row_value(existing, "description", "") if description is None else description,
        max_length=1000,
    ).strip()
    if not name:
        raise ValueError("Group name is required.")
    if visibility is None:
        visibility = row_value(existing, "visibility", VISIBILITY_MEMBERS)
    elif isinstance(visibility, bool):
        visibility = VISIBILITY_MEMBERS if visibility else VISIBILITY_PRIVATE
    visibility = normalize_visibility(visibility)
    with db() as conn:
        cursor = conn.execute(
            """
            UPDATE card_groups
            SET name = ?, description = ?, visibility = ?, is_public = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                name,
                description,
                visibility,
                visibility_to_public_flag(visibility),
                now_iso(),
                group_id,
                user_id,
            ),
        )
        return cursor.rowcount


def update_card_group_visibility(user_id, group_id, visibility):
    if isinstance(visibility, bool):
        visibility = VISIBILITY_MEMBERS if visibility else VISIBILITY_PRIVATE
    visibility = normalize_visibility(visibility)
    with db() as conn:
        cursor = conn.execute(
            "UPDATE card_groups SET visibility = ?, is_public = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (visibility, visibility_to_public_flag(visibility), now_iso(), group_id, user_id),
        )
        return cursor.rowcount


def update_card_group_sharing_defaults(user_id, group_id, visibility, default_item_visibility, show_values, show_photos):
    visibility = normalize_visibility(visibility)
    default_item_visibility = normalize_visibility(default_item_visibility)
    with db() as conn:
        cursor = conn.execute(
            """
            UPDATE card_groups
            SET visibility = ?, is_public = ?, default_item_visibility = ?, show_values = ?, show_photos = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                visibility,
                visibility_to_public_flag(visibility),
                default_item_visibility,
                1 if show_values else 0,
                1 if show_photos else 0,
                now_iso(),
                group_id,
                user_id,
            ),
        )
        return cursor.rowcount


__all__ = [
    "normalize_group_type",
    "group_type_label",
    "create_card_group",
    "user_group",
    "group_summary_rows",
    "collection_group_item_count",
    "collection_group_quantity",
    "collection_group_items",
    "wishlist_group_item_count",
    "wishlist_group_items",
    "add_collection_item_to_group",
    "add_want_item_to_group",
    "remove_group_item",
    "parse_group_item_quantity",
    "update_group_collection_item_quantities",
    "update_group_collection_item_quantities_matching",
    "remove_group_items",
    "remove_group_items_matching",
    "delete_card_group",
    "update_card_group",
    "update_card_group_visibility",
    "update_card_group_sharing_defaults",
]
