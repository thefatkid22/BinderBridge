"""Small test data factories for BinderBridge tests.

The factories return database ids by default because most existing tests use
ids to drive app helpers. Use the matching ``*_row`` helpers when a test needs
the freshly inserted row.
"""

from __future__ import annotations

from itertools import count

import app


_sequence = count(1)


def _next_suffix():
    return next(_sequence)


def _timestamp_pair():
    timestamp = app.now_iso()
    return timestamp, timestamp


def _insert(table, data):
    columns = list(data)
    placeholders = ", ".join("?" for _ in columns)
    column_sql = ", ".join(columns)
    return app.execute(
        f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})",
        tuple(data[column] for column in columns),
    )


def create_user(username=None, password="password123", display_name=None, **overrides):
    suffix = _next_suffix()
    username = username or f"user{suffix}"
    display_name = display_name or username.replace("_", " ").title()
    return app.create_user(username, password, display_name, **overrides)


def user_row(username=None, password="password123", display_name=None, **overrides):
    user_id = create_user(username, password, display_name, **overrides)
    return app.row("SELECT * FROM users WHERE id = ?", (user_id,))


def create_session_user(username=None, password="password123", display_name=None, **overrides):
    user_id = create_user(username, password, display_name, **overrides)
    token, expires_at = app.create_session(user_id)
    return user_id, token, expires_at


def create_collection_item(user_id, card_name="Sol Ring", **overrides):
    created_at, updated_at = _timestamp_pair()
    data = {
        "user_id": user_id,
        "game": "mtg",
        "card_name": card_name,
        "set_name": "",
        "set_code": "",
        "collector_number": "",
        "finish": "Regular",
        "condition": "NM",
        "language": "English",
        "quantity": 1,
        "quantity_for_trade": 0,
        "scryfall_id": "",
        "image_url": "",
        "mana_cost": "",
        "type_line": "",
        "oracle_text": "",
        "rarity": "",
        "colors": "",
        "color_identity": "",
        "scryfall_uri": "",
        "price_usd": "",
        "price_source": "",
        "tcgplayer_product_id": "",
        "cardmarket_product_id": "",
        "cardkingdom_sku": "",
        "price_refreshed_at": "",
        "price_status": "",
        "notes": "",
        "is_public": 1,
        "created_at": created_at,
        "updated_at": updated_at,
    }
    data.update(overrides)
    return _insert("collection_items", data)


def collection_item_row(user_id, card_name="Sol Ring", **overrides):
    item_id = create_collection_item(user_id, card_name, **overrides)
    return app.row("SELECT * FROM collection_items WHERE id = ?", (item_id,))


def create_want_item(user_id, card_name="Sol Ring", **overrides):
    created_at, updated_at = _timestamp_pair()
    data = {
        "user_id": user_id,
        "game": "mtg",
        "card_name": card_name,
        "set_name": "",
        "set_code": "",
        "collector_number": "",
        "desired_quantity": 1,
        "priority": "normal",
        "budget_cap_usd": "",
        "condition": "",
        "finish": "",
        "language": "",
        "scryfall_id": "",
        "image_url": "",
        "mana_cost": "",
        "type_line": "",
        "oracle_text": "",
        "rarity": "",
        "colors": "",
        "color_identity": "",
        "scryfall_uri": "",
        "price_usd": "",
        "price_source": "",
        "preferred_printing_notes": "",
        "notes": "",
        "is_public": 1,
        "created_at": created_at,
        "updated_at": updated_at,
    }
    data.update(overrides)
    return _insert("want_items", data)


def want_item_row(user_id, card_name="Sol Ring", **overrides):
    want_id = create_want_item(user_id, card_name, **overrides)
    return app.row("SELECT * FROM want_items WHERE id = ?", (want_id,))


def create_trade(proposer_id, recipient_id, **overrides):
    created_at, updated_at = _timestamp_pair()
    data = {
        "proposer_id": proposer_id,
        "recipient_id": recipient_id,
        "status": "pending",
        "proposer_note": "",
        "response_note": "",
        "price_source_preference": "",
        "countered_from_trade_id": None,
        "counter_trade_id": None,
        "created_at": created_at,
        "updated_at": updated_at,
    }
    data.update(overrides)
    return _insert("trades", data)


def trade_row(proposer_id, recipient_id, **overrides):
    trade_id = create_trade(proposer_id, recipient_id, **overrides)
    return app.row("SELECT * FROM trades WHERE id = ?", (trade_id,))


def create_trade_item(trade_id, owner_id, card_name="Sol Ring", side="offered", **overrides):
    data = {
        "trade_id": trade_id,
        "owner_id": owner_id,
        "collection_item_id": None,
        "card_name": card_name,
        "set_name": "",
        "quantity": 1,
        "condition": "",
        "finish": "",
        "price_usd": "",
        "price_source": "",
        "side": side,
    }
    data.update(overrides)
    return _insert("trade_items", data)


def trade_item_row(trade_id, owner_id, card_name="Sol Ring", side="offered", **overrides):
    trade_item_id = create_trade_item(trade_id, owner_id, card_name, side, **overrides)
    return app.row("SELECT * FROM trade_items WHERE id = ?", (trade_item_id,))
