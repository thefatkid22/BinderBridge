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


def create_card_group(user_id, group_type, name, description="", is_public=True):
    group_type = normalize_group_type(group_type)
    name = sanitize_text_input(name, max_length=80).strip()
    description = sanitize_text_input(description, max_length=1000).strip()
    if not name:
        raise ValueError("Group name is required.")
    timestamp = now_iso()
    return execute(
        """
        INSERT INTO card_groups (user_id, group_type, name, description, is_public, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (user_id, group_type, name, description, 1 if is_public else 0, timestamp, timestamp),
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


def collection_group_items(group_id, order_clause=None):
    order_clause = order_clause or "collection_items.card_name COLLATE NOCASE, collection_items.set_name COLLATE NOCASE"
    return rows(
        f"""
        SELECT
            group_collection_items.id AS group_item_id,
            group_collection_items.quantity AS group_quantity,
            collection_items.*
        FROM group_collection_items
        JOIN collection_items ON collection_items.id = group_collection_items.collection_item_id
        WHERE group_collection_items.group_id = ?
        ORDER BY {order_clause}
        """,
        (group_id,),
    )


def wishlist_group_items(group_id, order_clause=None):
    order_clause = order_clause or "want_items.card_name COLLATE NOCASE, want_items.set_name COLLATE NOCASE"
    return rows(
        f"""
        SELECT
            group_want_items.id AS group_item_id,
            want_items.*
        FROM group_want_items
        JOIN want_items ON want_items.id = group_want_items.want_item_id
        WHERE group_want_items.group_id = ?
        ORDER BY {order_clause}
        """,
        (group_id,),
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


def delete_card_group(user_id, group_id):
    with db() as conn:
        cursor = conn.execute("DELETE FROM card_groups WHERE id = ? AND user_id = ?", (group_id, user_id))
        return cursor.rowcount


def update_card_group_visibility(user_id, group_id, is_public):
    with db() as conn:
        cursor = conn.execute(
            "UPDATE card_groups SET is_public = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (1 if is_public else 0, now_iso(), group_id, user_id),
        )
        return cursor.rowcount


__all__ = [
    "normalize_group_type",
    "group_type_label",
    "create_card_group",
    "user_group",
    "group_summary_rows",
    "collection_group_items",
    "wishlist_group_items",
    "add_collection_item_to_group",
    "add_want_item_to_group",
    "remove_group_item",
    "delete_card_group",
    "update_card_group_visibility",
]
