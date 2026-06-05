"""Collection, browse, and trade-picker SQL query helpers.

Shared app helpers are injected at runtime by the app facade.
"""

CONDITION_SORT_SQL = "CASE condition WHEN 'NM' THEN 1 WHEN 'LP' THEN 2 WHEN 'MP' THEN 3 WHEN 'HP' THEN 4 WHEN 'DMG' THEN 5 ELSE 99 END"

COLLECTION_SORT_SQL = {
    "name": ("card_name COLLATE NOCASE",),
    "set": ("set_name COLLATE NOCASE", "set_code COLLATE NOCASE", "collector_number COLLATE NOCASE"),
    "game": ("game COLLATE NOCASE",),
    "qty": ("quantity",),
    "trade": ("quantity_for_trade",),
    "quality": (CONDITION_SORT_SQL,),
    "finish": ("finish COLLATE NOCASE",),
    "value": ("COALESCE(CAST(NULLIF(price_usd, '') AS REAL), 0) * quantity",),
    "updated": ("updated_at",),
}

BROWSE_SORT_SQL = {
    "name": ("collection_items.card_name COLLATE NOCASE",),
    "set": ("collection_items.set_name COLLATE NOCASE", "collection_items.set_code COLLATE NOCASE", "collection_items.collector_number COLLATE NOCASE"),
    "game": ("collection_items.game COLLATE NOCASE",),
    "qty": ("collection_items.quantity",),
    "trade": ("collection_items.quantity_for_trade",),
    "quality": ("CASE collection_items.condition WHEN 'NM' THEN 1 WHEN 'LP' THEN 2 WHEN 'MP' THEN 3 WHEN 'HP' THEN 4 WHEN 'DMG' THEN 5 ELSE 99 END",),
    "finish": ("collection_items.finish COLLATE NOCASE",),
    "value": ("COALESCE(CAST(NULLIF(collection_items.price_usd, '') AS REAL), 0) * collection_items.quantity_for_trade",),
    "updated": ("collection_items.updated_at",),
}

GROUP_COLLECTION_SORT_SQL = {
    "name": ("collection_items.card_name COLLATE NOCASE",),
    "set": ("collection_items.set_name COLLATE NOCASE", "collection_items.set_code COLLATE NOCASE", "collection_items.collector_number COLLATE NOCASE"),
    "game": ("collection_items.game COLLATE NOCASE",),
    "qty": ("group_collection_items.quantity",),
    "trade": ("collection_items.quantity_for_trade",),
    "quality": ("CASE collection_items.condition WHEN 'NM' THEN 1 WHEN 'LP' THEN 2 WHEN 'MP' THEN 3 WHEN 'HP' THEN 4 WHEN 'DMG' THEN 5 ELSE 99 END",),
    "finish": ("collection_items.finish COLLATE NOCASE",),
    "value": ("COALESCE(CAST(NULLIF(collection_items.price_usd, '') AS REAL), 0) * group_collection_items.quantity",),
    "updated": ("group_collection_items.updated_at", "collection_items.updated_at"),
}

WANT_SORT_SQL = {
    "name": ("card_name COLLATE NOCASE",),
    "set": ("set_name COLLATE NOCASE", "set_code COLLATE NOCASE", "collector_number COLLATE NOCASE"),
    "game": ("game COLLATE NOCASE",),
    "qty": ("desired_quantity",),
    "quality": ("condition COLLATE NOCASE",),
    "finish": ("finish COLLATE NOCASE",),
    "value": ("COALESCE(CAST(NULLIF(price_usd, '') AS REAL), 0) * desired_quantity",),
    "updated": ("updated_at",),
}

GROUP_WANT_SORT_SQL = {
    "name": ("want_items.card_name COLLATE NOCASE",),
    "set": ("want_items.set_name COLLATE NOCASE", "want_items.set_code COLLATE NOCASE", "want_items.collector_number COLLATE NOCASE"),
    "game": ("want_items.game COLLATE NOCASE",),
    "qty": ("want_items.desired_quantity",),
    "quality": ("want_items.condition COLLATE NOCASE",),
    "finish": ("want_items.finish COLLATE NOCASE",),
    "value": ("COALESCE(CAST(NULLIF(want_items.price_usd, '') AS REAL), 0) * want_items.desired_quantity",),
    "updated": ("group_want_items.updated_at", "want_items.updated_at"),
}

def collection_where(user_id, q="", game="", trade_only=False, **advanced_filters):
    if isinstance(q, dict):
        filters = q
        q = filters.get("q", "")
        game = filters.get("game", "")
        trade_only = filters.get("trade_only", False)
        advanced_filters = filters
    where = ["user_id = ?"]
    params = [user_id]
    if q:
        where.append("(card_name LIKE ? OR type_line LIKE ?)")
        term = f"%{q}%"
        params.extend([term, term])
    if game:
        where.append("game = ?")
        params.append(game)
    if trade_only:
        where.append("quantity_for_trade > 0")
    if advanced_filters.get("set_name"):
        where.append("set_name LIKE ?")
        params.append(f"%{advanced_filters['set_name']}%")
    if advanced_filters.get("set_code"):
        where.append("set_code = ? COLLATE NOCASE")
        params.append(advanced_filters["set_code"])
    if advanced_filters.get("collector_number"):
        where.append("collector_number LIKE ?")
        params.append(f"%{advanced_filters['collector_number']}%")
    if advanced_filters.get("type_line"):
        where.append("type_line LIKE ?")
        params.append(f"%{advanced_filters['type_line']}%")
    if advanced_filters.get("condition"):
        where.append("condition = ?")
        params.append(advanced_filters["condition"])
    if advanced_filters.get("finish"):
        where.append("finish = ?")
        params.append(advanced_filters["finish"])
    if advanced_filters.get("language"):
        where.append("language = ?")
        params.append(advanced_filters["language"])
    if advanced_filters.get("rarity"):
        where.append("rarity = ? COLLATE NOCASE")
        params.append(advanced_filters["rarity"])
    if advanced_filters.get("color_identity"):
        color = advanced_filters["color_identity"]
        if color == "C":
            where.append("color_identity = ''")
        else:
            where.append("(',' || color_identity || ',') LIKE ?")
            params.append(f"%,{color},%")
    if advanced_filters.get("card_data") == "with_scryfall":
        where.append("(scryfall_id != '' OR scryfall_uri != '' OR image_url != '' OR type_line != '' OR oracle_text != '')")
    elif advanced_filters.get("card_data") == "missing_scryfall":
        where.append("scryfall_id = '' AND scryfall_uri = '' AND image_url = '' AND type_line = '' AND oracle_text = ''")
    elif advanced_filters.get("card_data") == "with_image":
        where.append("image_url != ''")
    elif advanced_filters.get("card_data") == "missing_image":
        where.append("image_url = ''")
    if advanced_filters.get("visibility") == "public":
        where.append("is_public = 1")
    elif advanced_filters.get("visibility") == "private":
        where.append("is_public = 0")
    if advanced_filters.get("quantity_min") is not None:
        where.append("quantity >= ?")
        params.append(advanced_filters["quantity_min"])
    if advanced_filters.get("quantity_max") is not None:
        where.append("quantity <= ?")
        params.append(advanced_filters["quantity_max"])
    if advanced_filters.get("trade_min") is not None:
        where.append("quantity_for_trade >= ?")
        params.append(advanced_filters["trade_min"])
    if advanced_filters.get("trade_max") is not None:
        where.append("quantity_for_trade <= ?")
        params.append(advanced_filters["trade_max"])
    return where, params

def browse_where(user_id, q="", game="", quality="", finish="", owner_id=0, **advanced_filters):
    if isinstance(q, dict):
        filters = q
        q = filters.get("q", "")
        game = filters.get("game", "")
        quality = filters.get("quality", "")
        finish = filters.get("finish", "")
        owner_id = filters.get("owner_id", 0)
        advanced_filters = filters
    where = [
        "collection_items.user_id != ?",
        "collection_items.quantity_for_trade > 0",
        "collection_items.is_public = 1",
        "users.is_banned = 0",
    ]
    params = [user_id]
    if q:
        where.append("(collection_items.card_name LIKE ? OR collection_items.type_line LIKE ?)")
        term = f"%{q}%"
        params.extend([term, term])
    if game:
        where.append("collection_items.game = ?")
        params.append(game)
    if quality:
        where.append("collection_items.condition = ?")
        params.append(quality)
    if finish:
        where.append("collection_items.finish = ?")
        params.append(finish)
    if owner_id:
        where.append("collection_items.user_id = ?")
        params.append(owner_id)
    if advanced_filters.get("set_name"):
        where.append("collection_items.set_name LIKE ?")
        params.append(f"%{advanced_filters['set_name']}%")
    if advanced_filters.get("set_code"):
        where.append("collection_items.set_code = ? COLLATE NOCASE")
        params.append(advanced_filters["set_code"])
    if advanced_filters.get("collector_number"):
        where.append("collection_items.collector_number LIKE ?")
        params.append(f"%{advanced_filters['collector_number']}%")
    if advanced_filters.get("type_line"):
        where.append("collection_items.type_line LIKE ?")
        params.append(f"%{advanced_filters['type_line']}%")
    if advanced_filters.get("language"):
        where.append("collection_items.language = ?")
        params.append(advanced_filters["language"])
    if advanced_filters.get("rarity"):
        where.append("collection_items.rarity = ? COLLATE NOCASE")
        params.append(advanced_filters["rarity"])
    if advanced_filters.get("color_identity"):
        color = advanced_filters["color_identity"]
        if color == "C":
            where.append("collection_items.color_identity = ''")
        else:
            where.append("(',' || collection_items.color_identity || ',') LIKE ?")
            params.append(f"%,{color},%")
    if advanced_filters.get("card_data") == "with_scryfall":
        where.append("(collection_items.scryfall_id != '' OR collection_items.scryfall_uri != '' OR collection_items.image_url != '' OR collection_items.type_line != '' OR collection_items.oracle_text != '')")
    elif advanced_filters.get("card_data") == "missing_scryfall":
        where.append("collection_items.scryfall_id = '' AND collection_items.scryfall_uri = '' AND collection_items.image_url = '' AND collection_items.type_line = '' AND collection_items.oracle_text = ''")
    elif advanced_filters.get("card_data") == "with_image":
        where.append("collection_items.image_url != ''")
    elif advanced_filters.get("card_data") == "missing_image":
        where.append("collection_items.image_url = ''")
    if advanced_filters.get("quantity_min") is not None:
        where.append("collection_items.quantity >= ?")
        params.append(advanced_filters["quantity_min"])
    if advanced_filters.get("quantity_max") is not None:
        where.append("collection_items.quantity <= ?")
        params.append(advanced_filters["quantity_max"])
    if advanced_filters.get("trade_min") is not None:
        where.append("collection_items.quantity_for_trade >= ?")
        params.append(advanced_filters["trade_min"])
    if advanced_filters.get("trade_max") is not None:
        where.append("collection_items.quantity_for_trade <= ?")
        params.append(advanced_filters["trade_max"])
    return where, params

def browse_filter_users(user_id):
    return rows(
        """
        SELECT DISTINCT users.id, users.display_name, users.username
        FROM users
        JOIN collection_items ON collection_items.user_id = users.id
        WHERE users.id != ? AND users.is_banned = 0 AND collection_items.quantity_for_trade > 0 AND collection_items.is_public = 1
        ORDER BY users.display_name COLLATE NOCASE
        """,
        (user_id,),
    )

def trade_picker_where(user_id, filters, viewer_id=None):
    where = ["user_id = ?", "quantity_for_trade > 0"]
    params = [user_id]
    if viewer_id is not None and int(viewer_id) != int(user_id):
        where.append("is_public = 1")
    if filters.get("q"):
        where.append("(card_name LIKE ? OR type_line LIKE ?)")
        term = f"%{filters['q']}%"
        params.extend([term, term])
    if filters.get("game"):
        where.append("game = ?")
        params.append(filters["game"])
    if filters.get("condition"):
        where.append("condition = ?")
        params.append(filters["condition"])
    if filters.get("finish"):
        where.append("finish = ?")
        params.append(filters["finish"])
    if filters.get("set_name"):
        where.append("set_name LIKE ?")
        params.append(f"%{filters['set_name']}%")
    if filters.get("set_code"):
        where.append("set_code = ? COLLATE NOCASE")
        params.append(filters["set_code"])
    if filters.get("collector_number"):
        where.append("collector_number LIKE ?")
        params.append(f"%{filters['collector_number']}%")
    if filters.get("type_line"):
        where.append("type_line LIKE ?")
        params.append(f"%{filters['type_line']}%")
    if filters.get("language"):
        where.append("language = ?")
        params.append(filters["language"])
    if filters.get("rarity"):
        where.append("rarity = ? COLLATE NOCASE")
        params.append(filters["rarity"])
    if filters.get("color_identity"):
        color = filters["color_identity"]
        if color == "C":
            where.append("color_identity = ''")
        else:
            where.append("(',' || color_identity || ',') LIKE ?")
            params.append(f"%,{color},%")
    if filters.get("card_data") == "with_scryfall":
        where.append("(scryfall_id != '' OR scryfall_uri != '' OR image_url != '' OR type_line != '' OR oracle_text != '')")
    elif filters.get("card_data") == "missing_scryfall":
        where.append("scryfall_id = '' AND scryfall_uri = '' AND image_url = '' AND type_line = '' AND oracle_text = ''")
    elif filters.get("card_data") == "with_image":
        where.append("image_url != ''")
    elif filters.get("card_data") == "missing_image":
        where.append("image_url = ''")
    if filters.get("quantity_min") is not None:
        where.append("quantity >= ?")
        params.append(filters["quantity_min"])
    if filters.get("quantity_max") is not None:
        where.append("quantity <= ?")
        params.append(filters["quantity_max"])
    if filters.get("trade_min") is not None:
        where.append("quantity_for_trade >= ?")
        params.append(filters["trade_min"])
    if filters.get("trade_max") is not None:
        where.append("quantity_for_trade <= ?")
        params.append(filters["trade_max"])
    return where, params



def collection_count(where, params):
    return row(
        f"SELECT COUNT(*) AS count FROM collection_items WHERE {' AND '.join(where)}",
        params,
    )["count"]


def collection_page_rows(where, params, order_clause, limit, offset):
    return rows(
        f"""
        SELECT *
        FROM collection_items
        WHERE {' AND '.join(where)}
        ORDER BY {order_clause}
        LIMIT ? OFFSET ?
        """,
        [*params, int(limit), int(offset)],
    )


def browse_count(where, params):
    return row(
        f"""
        SELECT COUNT(*) AS count
        FROM collection_items
        JOIN users ON users.id = collection_items.user_id
        WHERE {' AND '.join(where)}
        """,
        params,
    )["count"]


def browse_page_rows(where, params, order_clause, limit, offset):
    return rows(
        f"""
        SELECT
            collection_items.*,
            users.id AS owner_id,
            users.username AS owner_username,
            users.display_name AS owner_name
        FROM collection_items
        JOIN users ON users.id = collection_items.user_id
        WHERE {' AND '.join(where)}
        ORDER BY {order_clause}
        LIMIT ? OFFSET ?
        """,
        [*params, int(limit), int(offset)],
    )


def trade_picker_count(where, params):
    return row(
        f"SELECT COUNT(*) AS count FROM collection_items WHERE {' AND '.join(where)}",
        params,
    )["count"]


def trade_picker_rows(where, params, order_clause, limit, offset):
    return rows(
        f"""
        SELECT *
        FROM collection_items
        WHERE {' AND '.join(where)}
        ORDER BY {order_clause}
        LIMIT ? OFFSET ?
        """,
        [*params, int(limit), int(offset)],
    )

__all__ = [
    'CONDITION_SORT_SQL',
    'COLLECTION_SORT_SQL',
    'BROWSE_SORT_SQL',
    'GROUP_COLLECTION_SORT_SQL',
    'WANT_SORT_SQL',
    'GROUP_WANT_SORT_SQL',
    'collection_where',
    'browse_where',
    'browse_filter_users',
    'trade_picker_where',
    'collection_count',
    'collection_page_rows',
    'browse_count',
    'browse_page_rows',
    'trade_picker_count',
    'trade_picker_rows',
]
