"""Extracted BinderBridge feature code.

The app facade injects shared helpers/constants into this module at import time
so the legacy app.py public API remains compatible during the split.
"""

import hashlib
from pathlib import Path

CARD_PHOTO_MAX_BYTES = 5 * 1024 * 1024
CARD_PHOTO_MAX_COUNT = 6
CARD_PHOTO_ALLOWED_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
}
CARD_PHOTO_EXTENSION_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

def collection_item_values(data):
    requested_visibility = data.get("visibility", "")
    defaults = {
        "game": "mtg",
        "card_name": "",
        "set_name": "",
        "set_code": "",
        "collector_number": "",
        "finish": "Regular",
        "condition": "NM",
        "condition_notes": "",
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
        "visibility": VISIBILITY_MEMBERS,
    }
    for field in SCRYFALL_COLLECTION_FIELDS:
        defaults[field] = ""
    defaults.update({key: data.get(key, defaults.get(key, "")) for key in defaults})
    defaults["quantity"] = clamp_quantity(defaults["quantity"], 0)
    defaults["quantity_for_trade"] = min(clamp_quantity(defaults["quantity_for_trade"], 0), defaults["quantity"])
    defaults["price_usd"] = normalize_price_usd(defaults["price_usd"])
    defaults["price_source"] = "scryfall" if defaults["price_usd"] else ""
    defaults["condition_notes"] = sanitize_text_input(defaults.get("condition_notes", ""), max_length=1000).strip()
    visibility_default = VISIBILITY_MEMBERS if str(defaults.get("is_public", "1")).strip() in ("1", "true", "True", "on", "yes") else VISIBILITY_PRIVATE
    defaults["visibility"] = normalize_visibility(requested_visibility, default=visibility_default)
    defaults["is_public"] = visibility_to_public_flag(defaults["visibility"])
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
                condition_notes = ?, language = ?, quantity = ?, quantity_for_trade = ?, scryfall_id = ?, image_url = ?, mana_cost = ?,
                type_line = ?, oracle_text = ?, rarity = ?, colors = ?, color_identity = ?, scryfall_uri = ?,
                price_usd = ?, price_source = ?, tcgplayer_product_id = ?, cardmarket_product_id = ?,
                cardkingdom_sku = ?, notes = ?, is_public = ?, visibility = ?, updated_at = ?
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
                values["condition_notes"],
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
                values["visibility"],
                now_iso(),
                item_id,
                user_id,
            ),
        )
        record_price_history_for_item(item_id, user_id, values, row_value(existing, "price_usd", ""), values["price_usd"], conn=conn)
        previous_visibility = record_visibility(existing)
        previous_public_trade_quantity = (
            existing["quantity_for_trade"]
            if previous_visibility not in (VISIBILITY_PRIVATE, VISIBILITY_LINK)
            else 0
        )
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


def card_photo_type_matches(content_type, content):
    if content_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if content_type == "image/gif":
        return content.startswith((b"GIF87a", b"GIF89a"))
    if content_type == "image/webp":
        return len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP"
    return False


def normalize_card_photo_upload(upload, caption=""):
    if not upload:
        return None
    content = upload.get("content") or b""
    if isinstance(content, str):
        content = content.encode("utf-8")
    if not content:
        return None
    if len(content) > CARD_PHOTO_MAX_BYTES:
        raise ValueError("Card photos must be 5 MB or smaller.")
    filename = safe_download_filename(upload.get("filename") or "card-photo", default="card-photo")
    extension_type = CARD_PHOTO_EXTENSION_TYPES.get(Path(filename).suffix.lower())
    content_type = sanitize_text_input(upload.get("content_type") or "", max_length=120).strip().lower().split(";", 1)[0]
    if content_type in ("", "application/octet-stream") and extension_type:
        content_type = extension_type
    if content_type not in CARD_PHOTO_ALLOWED_TYPES and extension_type in CARD_PHOTO_ALLOWED_TYPES:
        content_type = extension_type
    if content_type not in CARD_PHOTO_ALLOWED_TYPES:
        raise ValueError("Card photos must be PNG, JPG, GIF, or WebP images.")
    if not card_photo_type_matches(content_type, content):
        raise ValueError("Card photo contents do not match an allowed image type.")
    return {
        "original_filename": filename,
        "content_type": content_type,
        "file_size": len(content),
        "checksum_sha256": hashlib.sha256(content).hexdigest(),
        "caption": sanitize_text_input(caption, max_length=300).strip(),
        "content": content,
    }


def collection_item_photo_rows(collection_item_id):
    return rows(
        """
        SELECT id, collection_item_id, original_filename, content_type, file_size,
            checksum_sha256, caption, created_at
        FROM collection_item_photos
        WHERE collection_item_id = ?
        ORDER BY created_at, id
        """,
        (collection_item_id,),
    )


def collection_item_photo_count(collection_item_id):
    found = row("SELECT COUNT(*) AS count FROM collection_item_photos WHERE collection_item_id = ?", (collection_item_id,))
    return int(found["count"] or 0) if found else 0


def add_collection_item_photo(user_id, collection_item_id, upload, caption=""):
    photo = normalize_card_photo_upload(upload, caption)
    if not photo:
        raise ValueError("Choose a card photo before uploading.")
    with db() as conn:
        item = conn.execute(
            "SELECT id FROM collection_items WHERE id = ? AND user_id = ?",
            (collection_item_id, user_id),
        ).fetchone()
        if not item:
            raise ValueError("Collection card not found.")
        count = conn.execute(
            "SELECT COUNT(*) AS count FROM collection_item_photos WHERE collection_item_id = ?",
            (collection_item_id,),
        ).fetchone()["count"]
        if int(count or 0) >= CARD_PHOTO_MAX_COUNT:
            raise ValueError(f"Each collection card can have up to {CARD_PHOTO_MAX_COUNT} photos.")
        duplicate = conn.execute(
            "SELECT id FROM collection_item_photos WHERE collection_item_id = ? AND checksum_sha256 = ?",
            (collection_item_id, photo["checksum_sha256"]),
        ).fetchone()
        if duplicate:
            raise ValueError("That photo is already attached to this card.")
        cursor = conn.execute(
            """
            INSERT INTO collection_item_photos
                (collection_item_id, original_filename, content_type, file_size, checksum_sha256, caption, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                collection_item_id,
                photo["original_filename"],
                photo["content_type"],
                photo["file_size"],
                photo["checksum_sha256"],
                photo["caption"],
                photo["content"],
                now_iso(),
            ),
        )
        conn.execute("UPDATE collection_items SET updated_at = ? WHERE id = ?", (now_iso(), collection_item_id))
        return cursor.lastrowid


def delete_collection_item_photo(user_id, collection_item_id, photo_id):
    with db() as conn:
        cursor = conn.execute(
            """
            DELETE FROM collection_item_photos
            WHERE id = ? AND collection_item_id = ?
                AND EXISTS (
                    SELECT 1 FROM collection_items
                    WHERE collection_items.id = collection_item_photos.collection_item_id
                        AND collection_items.user_id = ?
                )
            """,
            (photo_id, collection_item_id, user_id),
        )
        if cursor.rowcount:
            conn.execute("UPDATE collection_items SET updated_at = ? WHERE id = ?", (now_iso(), collection_item_id))
        return cursor.rowcount


def collection_item_photo_for_user(photo_id, user_id):
    photo = row(
        """
        SELECT collection_item_photos.*, collection_items.user_id AS owner_id,
            collection_items.visibility, collection_items.is_public
        FROM collection_item_photos
        JOIN collection_items ON collection_items.id = collection_item_photos.collection_item_id
        JOIN users ON users.id = collection_items.user_id
        WHERE collection_item_photos.id = ? AND users.is_banned = 0
        """,
        (photo_id,),
    )
    viewer = row("SELECT * FROM users WHERE id = ?", (user_id,))
    return photo if photo and can_view_record(viewer, photo["owner_id"], photo) else None


def copy_collection_item_photos_to_trade_item_conn(conn, collection_item_id, trade_item_id):
    conn.execute(
        """
        INSERT INTO trade_item_photos
            (trade_item_id, original_filename, content_type, file_size, checksum_sha256, caption, content, created_at)
        SELECT ?, original_filename, content_type, file_size, checksum_sha256, caption, content, ?
        FROM collection_item_photos
        WHERE collection_item_id = ?
        ORDER BY created_at, id
        """,
        (trade_item_id, now_iso(), collection_item_id),
    )


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
    visibility_value = str(form.get("visibility", form.get("is_public", [""]))[0] or "").strip()
    if visibility_value == "":
        is_public = None
    elif visibility_value in ("0", "1"):
        is_public = int(visibility_value)
    elif visibility_value in VISIBILITY_LABELS:
        is_public = normalize_visibility(visibility_value)
    else:
        raise ValueError("Choose a valid visibility level or no visibility change.")
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
        candidates = conn.execute(f"SELECT id, quantity, quantity_for_trade, is_public, visibility FROM collection_items WHERE {where_sql}", params).fetchall()
        updated = 0
        for item in candidates:
            new_quantity = item["quantity"] if quantity is None else clamp_quantity(quantity, 0)
            new_trade_quantity = item["quantity_for_trade"] if quantity_for_trade is None else clamp_quantity(quantity_for_trade, 0)
            new_trade_quantity = min(new_trade_quantity, new_quantity)
            if is_public is None:
                new_visibility = item["visibility"]
            elif str(is_public) in ("0", "1"):
                new_visibility = VISIBILITY_MEMBERS if int(is_public) else VISIBILITY_PRIVATE
            else:
                new_visibility = normalize_visibility(is_public)
            new_is_public = visibility_to_public_flag(new_visibility)
            previous_visibility = record_visibility(item)
            previous_public_trade_quantity = (
                item["quantity_for_trade"]
                if previous_visibility not in (VISIBILITY_PRIVATE, VISIBILITY_LINK)
                else 0
            )
            conn.execute(
                """
                UPDATE collection_items
                SET quantity = ?, quantity_for_trade = ?, is_public = ?, visibility = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_quantity, new_trade_quantity, new_is_public, new_visibility, now_iso(), item["id"]),
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
        if record_visibility(item) in (VISIBILITY_PRIVATE, VISIBILITY_LINK):
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
            target_user = active_conn.execute("SELECT * FROM users WHERE id = ?", (target_user_id,)).fetchone()
            if not can_view_record(target_user, item["user_id"], item):
                continue
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
            SET quantity = ?, quantity_for_trade = ?, condition_notes = ?, notes = ?, scryfall_id = ?, image_url = ?, mana_cost = ?,
                type_line = ?, oracle_text = ?, rarity = ?, colors = ?, color_identity = ?, scryfall_uri = ?,
                price_usd = ?, price_source = ?, tcgplayer_product_id = ?, cardmarket_product_id = ?,
                cardkingdom_sku = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                new_quantity,
                new_trade_quantity,
                values["condition_notes"] or row_value(existing, "condition_notes", ""),
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
            (user_id, game, card_name, set_name, set_code, collector_number, finish, condition, condition_notes, language,
             quantity, quantity_for_trade, scryfall_id, image_url, mana_cost, type_line, oracle_text, rarity,
             colors, color_identity, scryfall_uri, price_usd, price_source, tcgplayer_product_id,
             cardmarket_product_id, cardkingdom_sku, notes, is_public, visibility, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            values["condition_notes"],
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
            values["visibility"],
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
    "CARD_PHOTO_MAX_BYTES",
    "CARD_PHOTO_MAX_COUNT",
    "CARD_PHOTO_ALLOWED_TYPES",
    "CARD_PHOTO_EXTENSION_TYPES",
    "card_photo_type_matches",
    "normalize_card_photo_upload",
    "collection_item_photo_rows",
    "collection_item_photo_count",
    "add_collection_item_photo",
    "delete_collection_item_photo",
    "collection_item_photo_for_user",
    "copy_collection_item_photos_to_trade_item_conn",
    "parse_optional_bulk_quantity",
    "parse_bulk_collection_update",
    "update_collection_items_by_ids",
    "update_collection_items_matching",
    "update_collection_items_where",
    "watchlist_browse_url",
    "notify_watchlist_matches_for_collection_item",
    "upsert_collection_item",
]
