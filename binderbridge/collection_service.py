"""Extracted BinderBridge feature code.

The app facade injects shared helpers/constants into this module at import time
so the legacy app.py public API remains compatible during the split.
"""

def collection_item_values(data):
    defaults = {
        "game": "mtg",
        "card_name": "",
        "set_name": "",
        "set_code": "",
        "collector_number": "",
        "finish": "Regular",
        "condition": "NM",
        "language": "English",
        "quantity": 1,
        "quantity_for_trade": 0,
        "price_usd": "",
        "price_source": "",
        "tcgplayer_product_id": "",
        "cardmarket_product_id": "",
        "cardkingdom_sku": "",
        "price_refreshed_at": "",
        "price_status": "",
        "notes": "",
        "is_public": 1,
    }
    for field in SCRYFALL_COLLECTION_FIELDS:
        defaults[field] = ""
    defaults.update({key: data.get(key, defaults.get(key, "")) for key in defaults})
    defaults["quantity"] = clamp_quantity(defaults["quantity"], 0)
    defaults["quantity_for_trade"] = min(clamp_quantity(defaults["quantity_for_trade"], 0), defaults["quantity"])
    defaults["price_usd"] = normalize_price_usd(defaults["price_usd"])
    defaults["price_source"] = "scryfall" if defaults["price_usd"] else ""
    defaults["is_public"] = 1 if str(defaults.get("is_public", "1")).strip() in ("1", "true", "True", "on", "yes") else 0
    for field in PRICE_PROVIDER_ID_FIELDS.values():
        defaults[field] = str(defaults.get(field, "") or "").strip()[:80]
    return defaults


def update_collection_item(user_id, item_id, data):
    values = collection_item_values(data)
    with db() as conn:
        existing = conn.execute("SELECT * FROM collection_items WHERE id = ? AND user_id = ?", (item_id, user_id)).fetchone()
        if not existing:
            return
        conn.execute(
            """
            UPDATE collection_items
            SET game = ?, card_name = ?, set_name = ?, set_code = ?, collector_number = ?, finish = ?, condition = ?,
                language = ?, quantity = ?, quantity_for_trade = ?, scryfall_id = ?, image_url = ?, mana_cost = ?,
                type_line = ?, oracle_text = ?, rarity = ?, colors = ?, color_identity = ?, scryfall_uri = ?,
                price_usd = ?, price_source = ?, tcgplayer_product_id = ?, cardmarket_product_id = ?,
                cardkingdom_sku = ?, notes = ?, is_public = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                values["game"],
                values["card_name"],
                values["set_name"],
                values["set_code"],
                values["collector_number"],
                values["finish"],
                values["condition"],
                values["language"],
                values["quantity"],
                values["quantity_for_trade"],
                values["scryfall_id"],
                values["image_url"],
                values["mana_cost"],
                values["type_line"],
                values["oracle_text"],
                values["rarity"],
                values["colors"],
                values["color_identity"],
                values["scryfall_uri"],
                values["price_usd"],
                values["price_source"],
                values["tcgplayer_product_id"],
                values["cardmarket_product_id"],
                values["cardkingdom_sku"],
                values["notes"],
                values["is_public"],
                now_iso(),
                item_id,
                user_id,
            ),
        )
        record_price_history_for_item(item_id, user_id, values, row_value(existing, "price_usd", ""), values["price_usd"], conn=conn)
        previous_public_trade_quantity = existing["quantity_for_trade"] if int(row_value(existing, "is_public", 1) or 0) else 0
        notify_watchlist_matches_for_collection_item(item_id, previous_trade_quantity=previous_public_trade_quantity, conn=conn)


def bulk_delete_collection_items(user_id, item_ids):
    clean_ids = []
    for value in item_ids:
        try:
            clean_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not clean_ids:
        return 0
    placeholders = ",".join("?" for _ in clean_ids)
    with db() as conn:
        cursor = conn.execute(
            f"DELETE FROM collection_items WHERE user_id = ? AND id IN ({placeholders})",
            [user_id, *clean_ids],
        )
        return cursor.rowcount


def delete_collection_items_matching(user_id, q="", game="", trade_only=False, **filters):
    where, params = collection_where(user_id, q, game, trade_only, **filters)
    with db() as conn:
        cursor = conn.execute(
            f"DELETE FROM collection_items WHERE {' AND '.join(where)}",
            params,
        )
        return cursor.rowcount


def parse_optional_bulk_quantity(value, field_label):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        number = int(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_label} must be a whole number.") from exc
    if number < 0:
        raise ValueError(f"{field_label} cannot be negative.")
    return min(number, MAX_CARD_QUANTITY)


def parse_bulk_collection_update(form):
    quantity = parse_optional_bulk_quantity(form.get("quantity", [""])[0], "Quantity owned")
    quantity_for_trade = parse_optional_bulk_quantity(form.get("quantity_for_trade", [""])[0], "Quantity for trade")
    visibility_value = str(form.get("is_public", [""])[0] or "").strip()
    if visibility_value == "":
        is_public = None
    elif visibility_value in ("0", "1"):
        is_public = int(visibility_value)
    else:
        raise ValueError("Choose public, private, or no visibility change.")
    if quantity is None and quantity_for_trade is None and is_public is None:
        raise ValueError("Enter a quantity owned, quantity for trade, or visibility value to update.")
    if quantity is not None and quantity_for_trade is not None and quantity_for_trade > quantity:
        raise ValueError("Quantity for trade cannot be higher than quantity owned.")
    return quantity, quantity_for_trade, is_public


def update_collection_items_by_ids(user_id, item_ids, quantity=None, quantity_for_trade=None, is_public=None):
    clean_ids = []
    for value in item_ids:
        try:
            clean_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not clean_ids:
        return 0
    placeholders = ",".join("?" for _ in clean_ids)
    where = f"user_id = ? AND id IN ({placeholders})"
    return update_collection_items_where(where, [user_id, *clean_ids], quantity, quantity_for_trade, is_public)


def update_collection_items_matching(user_id, q="", game="", trade_only=False, quantity=None, quantity_for_trade=None, is_public=None, **filters):
    where, params = collection_where(user_id, q, game, trade_only, **filters)
    return update_collection_items_where(" AND ".join(where), params, quantity, quantity_for_trade, is_public)


def update_collection_items_where(where_sql, params, quantity=None, quantity_for_trade=None, is_public=None):
    with db() as conn:
        candidates = conn.execute(f"SELECT id, quantity, quantity_for_trade, is_public FROM collection_items WHERE {where_sql}", params).fetchall()
        updated = 0
        for item in candidates:
            new_quantity = item["quantity"] if quantity is None else clamp_quantity(quantity, 0)
            new_trade_quantity = item["quantity_for_trade"] if quantity_for_trade is None else clamp_quantity(quantity_for_trade, 0)
            new_trade_quantity = min(new_trade_quantity, new_quantity)
            new_is_public = item["is_public"] if is_public is None else is_public
            previous_public_trade_quantity = item["quantity_for_trade"] if int(item["is_public"] or 0) else 0
            conn.execute(
                """
                UPDATE collection_items
                SET quantity = ?, quantity_for_trade = ?, is_public = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_quantity, new_trade_quantity, new_is_public, now_iso(), item["id"]),
            )
            notify_watchlist_matches_for_collection_item(item["id"], previous_trade_quantity=previous_public_trade_quantity, conn=conn)
            updated += 1
        return updated


def watchlist_browse_url(item):
    query = urlencode({"q": row_value(item, "card_name", ""), "user": row_value(item, "user_id", "")})
    return f"/browse?{query}"


def notify_watchlist_matches_for_collection_item(collection_item_id, previous_trade_quantity=0, conn=None):
    def run(active_conn):
        item = active_conn.execute(
            """
            SELECT collection_items.*, users.display_name AS owner_name, users.is_banned AS owner_banned
            FROM collection_items
            JOIN users ON users.id = collection_items.user_id
            WHERE collection_items.id = ?
            """,
            (collection_item_id,),
        ).fetchone()
        if not item or int(row_value(item, "owner_banned", 0) or 0):
            return 0
        try:
            old_trade_quantity = int(previous_trade_quantity or 0)
        except (TypeError, ValueError):
            old_trade_quantity = 0
        if old_trade_quantity > 0 or int(row_value(item, "quantity_for_trade", 0) or 0) <= 0:
            return 0
        if not int(row_value(item, "is_public", 1) or 0):
            return 0
        matches = active_conn.execute(
            """
            SELECT want_items.*, users.display_name, users.username
            FROM want_items
            JOIN users ON users.id = want_items.user_id
            WHERE want_items.user_id != ?
                AND users.is_banned = 0
                AND users.watchlist_alerts_enabled = 1
                AND want_items.game = ?
                AND (
                    (? != '' AND want_items.scryfall_id = ?)
                    OR (
                        want_items.card_name = ? COLLATE NOCASE
                        AND (want_items.set_code = '' OR ? = '' OR want_items.set_code = ? COLLATE NOCASE)
                        AND (want_items.collector_number = '' OR ? = '' OR want_items.collector_number = ?)
                    )
                )
                AND (COALESCE(want_items.condition, '') = '' OR instr(',' || want_items.condition || ',', ',' || COALESCE(?, '') || ',') > 0)
                AND (COALESCE(want_items.finish, '') = '' OR instr(',' || want_items.finish || ',', ',' || COALESCE(?, '') || ',') > 0)
                AND (COALESCE(want_items.language, '') = '' OR instr(',' || want_items.language || ',', ',' || COALESCE(?, '') || ',') > 0)
            ORDER BY want_items.user_id, want_items.card_name COLLATE NOCASE
            """,
            (
                item["user_id"],
                item["game"],
                row_value(item, "scryfall_id", ""),
                row_value(item, "scryfall_id", ""),
                item["card_name"],
                row_value(item, "set_code", ""),
                row_value(item, "set_code", ""),
                row_value(item, "collector_number", ""),
                row_value(item, "collector_number", ""),
                row_value(item, "condition", ""),
                row_value(item, "finish", ""),
                row_value(item, "language", ""),
            ),
        ).fetchall()
        notified_users = set()
        notification_count = 0
        url = watchlist_browse_url(item)
        for want in matches:
            target_user_id = want["user_id"]
            if target_user_id in notified_users:
                continue
            notified_users.add(target_user_id)
            quantity = int(row_value(item, "quantity_for_trade", 0) or 0)
            owner_name = row_value(item, "owner_name", "Another user")
            title = f"Watchlist match: {item['card_name']}"
            body = (
                f"{owner_name} added {quantity} {item['card_name']} "
                f"card{'s' if quantity != 1 else ''} to their trade list."
            )
            create_notification(
                target_user_id,
                "watchlist_alert",
                title,
                body,
                url,
                conn=active_conn,
            )
            notification_count += 1
        return notification_count

    if conn is not None:
        return run(conn)
    with db() as active_conn:
        return run(active_conn)


def upsert_collection_item(user_id, data, merge=True, return_id=False):
    values = collection_item_values(data)
    existing = None
    if merge:
        existing = row(
            """
            SELECT *
            FROM collection_items
            WHERE user_id = ?
                AND game = ?
                AND card_name = ? COLLATE NOCASE
                AND set_name = ?
                AND set_code = ?
                AND collector_number = ?
                AND finish = ?
                AND condition = ?
                AND language = ?
            """,
            (
                user_id,
                values["game"],
                values["card_name"],
                values["set_name"],
                values["set_code"],
                values["collector_number"],
                values["finish"],
                values["condition"],
                values["language"],
            ),
        )
    if existing:
        new_quantity = min(MAX_CARD_QUANTITY, existing["quantity"] + values["quantity"])
        new_trade_quantity = min(existing["quantity_for_trade"] + values["quantity_for_trade"], new_quantity)
        merged = {field: values[field] or existing[field] for field in SCRYFALL_COLLECTION_FIELDS}
        merged["price_source"] = values["price_source"] or row_value(existing, "price_source", "")
        merged["tcgplayer_product_id"] = values["tcgplayer_product_id"] or row_value(existing, "tcgplayer_product_id", "")
        merged["cardmarket_product_id"] = values["cardmarket_product_id"] or row_value(existing, "cardmarket_product_id", "")
        merged["cardkingdom_sku"] = values["cardkingdom_sku"] or row_value(existing, "cardkingdom_sku", "")
        execute(
            """
            UPDATE collection_items
            SET quantity = ?, quantity_for_trade = ?, notes = ?, scryfall_id = ?, image_url = ?, mana_cost = ?,
                type_line = ?, oracle_text = ?, rarity = ?, colors = ?, color_identity = ?, scryfall_uri = ?,
                price_usd = ?, price_source = ?, tcgplayer_product_id = ?, cardmarket_product_id = ?,
                cardkingdom_sku = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                new_quantity,
                new_trade_quantity,
                values["notes"] or existing["notes"],
                merged["scryfall_id"],
                merged["image_url"],
                merged["mana_cost"],
                merged["type_line"],
                merged["oracle_text"],
                merged["rarity"],
                merged["colors"],
                merged["color_identity"],
                merged["scryfall_uri"],
                merged["price_usd"],
                merged["price_source"],
                merged["tcgplayer_product_id"],
                merged["cardmarket_product_id"],
                merged["cardkingdom_sku"],
                now_iso(),
                existing["id"],
            ),
        )
        history_item = dict(values)
        history_item.update(merged)
        record_price_history_for_item(existing["id"], user_id, history_item, row_value(existing, "price_usd", ""), merged["price_usd"])
        notify_watchlist_matches_for_collection_item(existing["id"], previous_trade_quantity=existing["quantity_for_trade"])
        return ("updated", existing["id"]) if return_id else "updated"

    new_id = execute(
        """
        INSERT INTO collection_items
            (user_id, game, card_name, set_name, set_code, collector_number, finish, condition, language,
             quantity, quantity_for_trade, scryfall_id, image_url, mana_cost, type_line, oracle_text, rarity,
             colors, color_identity, scryfall_uri, price_usd, price_source, tcgplayer_product_id,
             cardmarket_product_id, cardkingdom_sku, notes, is_public, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            values["game"],
            values["card_name"],
            values["set_name"],
            values["set_code"],
            values["collector_number"],
            values["finish"],
            values["condition"],
            values["language"],
            values["quantity"],
            values["quantity_for_trade"],
            values["scryfall_id"],
            values["image_url"],
            values["mana_cost"],
            values["type_line"],
            values["oracle_text"],
            values["rarity"],
            values["colors"],
            values["color_identity"],
            values["scryfall_uri"],
            values["price_usd"],
            values["price_source"],
            values["tcgplayer_product_id"],
            values["cardmarket_product_id"],
            values["cardkingdom_sku"],
            values["notes"],
            values["is_public"],
            now_iso(),
            now_iso(),
        ),
    )
    record_price_history_for_item(new_id, user_id, values, "", values["price_usd"])
    notify_watchlist_matches_for_collection_item(new_id, previous_trade_quantity=0)
    return ("inserted", new_id) if return_id else "inserted"


__all__ = [
    "collection_item_values",
    "update_collection_item",
    "bulk_delete_collection_items",
    "delete_collection_items_matching",
    "parse_optional_bulk_quantity",
    "parse_bulk_collection_update",
    "update_collection_items_by_ids",
    "update_collection_items_matching",
    "update_collection_items_where",
    "watchlist_browse_url",
    "notify_watchlist_matches_for_collection_item",
    "upsert_collection_item",
]
