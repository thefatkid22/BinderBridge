"""Display formatting, option lists, suggestions, and collection summary helpers.

The app facade injects shared runtime helpers/constants into this module.
"""

import base64
import binascii
import hashlib
import hmac
import html
import json
import re
import secrets
import smtplib
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from email.message import EmailMessage
from urllib.parse import quote

from binderbridge.config import config_bool, config_float, config_int, config_str
from binderbridge.migrations import (
    CURRENT_SCHEMA_VERSION,
    SCHEMA_MIGRATIONS,
    SCHEMA_VERSION_KEY,
    db_schema_version,
    migrate_hot_path_indexes,
    run_schema_migrations,
    set_db_schema_version,
)

CARD_GAMES = [
    ("mtg", "Magic: The Gathering"),
    ("pokemon", "Pokemon"),
    ("lorcana", "Disney Lorcana"),
    ("other", "Other"),
]

CONDITION_OPTIONS = ["NM", "LP", "MP", "HP", "DMG"]

FINISH_OPTIONS = ["Regular", "Foil", "Etched", "Showcase", "Other"]

LANGUAGE_OPTIONS = ["English", "Japanese", "German", "French", "Spanish", "Italian", "Portuguese", "Korean", "Chinese", "Other"]

RARITY_OPTIONS = ["common", "uncommon", "rare", "mythic", "special", "bonus"]

COLOR_IDENTITY_OPTIONS = [
    ("C", "Colorless"),
    ("W", "White"),
    ("U", "Blue"),
    ("B", "Black"),
    ("R", "Red"),
    ("G", "Green"),
]

CARD_DATA_FILTER_OPTIONS = [
    ("with_scryfall", "Has Scryfall data"),
    ("missing_scryfall", "Missing Scryfall data"),
    ("with_image", "Has image"),
    ("missing_image", "Missing image"),
]

GROUP_TYPE_OPTIONS = [
    ("deck", "Deck"),
    ("binder", "Binder"),
    ("wishlist", "Wishlist"),
]

WANT_PRIORITY_OPTIONS = [
    ("urgent", "Urgent"),
    ("high", "High"),
    ("normal", "Normal"),
    ("low", "Low"),
]

WANT_PRIORITY_LABELS = dict(WANT_PRIORITY_OPTIONS)
WANT_PRIORITY_RANKS = {
    "low": 1,
    "normal": 2,
    "high": 3,
    "urgent": 4,
}

TRADE_STATUS_LABELS = {
    "pending": "Pending",
    "accepted": "Accepted",
    "completed": "Completed",
    "declined": "Declined",
    "cancelled": "Cancelled",
    "countered": "Countered",
}

TRADE_DISPUTE_CATEGORY_OPTIONS = (
    ("shipping", "Shipping or delivery"),
    ("condition", "Card condition"),
    ("missing_cards", "Wrong or missing cards"),
    ("communication", "Communication"),
    ("suspicious", "Suspicious behavior"),
    ("other", "Other issue"),
)

TRADE_DISPUTE_STATUS_OPTIONS = (
    ("open", "Open"),
    ("reviewing", "Reviewing"),
    ("resolved", "Resolved"),
    ("dismissed", "Dismissed"),
)

CSV_SOURCE_OPTIONS = [
    ("auto", "Auto detect"),
    ("manabox", "ManaBox"),
    ("archidekt", "Archidekt"),
    ("generic", "Generic CSV"),
]

DECK_IMPORT_SOURCE_OPTIONS = CSV_SOURCE_OPTIONS + [
    ("decklist", "Deck list text/link"),
]

PAGE_SIZE_OPTIONS = [10, 25, 50, 100]

def row_value(record, key, default=None):
    if record is None:
        return default
    try:
        return record[key]
    except (KeyError, IndexError):
        return default

def money(cents):
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents or 0))
    return f"{sign}${cents // 100}.{cents % 100:02d}"

def price_text_from_cents(cents):
    sign = "-" if cents < 0 else ""
    cents = abs(int(cents or 0))
    return f"{sign}{cents // 100}.{cents % 100:02d}"

def signed_money(cents):
    if cents > 0:
        return f"+{money(cents)}"
    return money(cents)

def signed_price_text(value):
    text = str(value or "").strip()
    if not text:
        return "$0.00"
    if text.startswith("-"):
        return f"-${text[1:]}"
    return f"+${text}"

def e(value):
    return html.escape("" if value is None else str(value), quote=True)

def selected(current, expected):
    return " selected" if current == expected else ""

def checked(value):
    return " checked" if value else ""

def form_public_flag(form, default=1):
    if "is_public" in form:
        value = str(form.get("is_public", [str(default)])[0] or "").strip().lower()
        return 1 if value in ("1", "true", "yes", "on", "public") else 0
    if form.get("_visibility_present", [""])[0] == "1":
        return 0
    return 1 if default else 0

def game_label(value):
    return dict(CARD_GAMES).get(value, value)

def normalize_want_priority(value):
    clean = str(value or "").strip().lower() or "normal"
    if clean not in WANT_PRIORITY_RANKS:
        raise ValueError("Choose a valid wishlist priority.")
    return clean

def want_priority_label(value):
    return WANT_PRIORITY_LABELS.get(str(value or "").strip().lower(), "Normal")

def want_priority_rank(value):
    return WANT_PRIORITY_RANKS.get(str(value or "").strip().lower(), WANT_PRIORITY_RANKS["normal"])

def option_tags(options, current):
    return "".join(f'<option value="{e(value)}"{selected(current, value)}>{e(label)}</option>' for value, label in options)

def simple_option_tags(options, current):
    return "".join(f'<option value="{e(value)}"{selected(current, value)}>{e(value)}</option>' for value in options)

SUGGESTION_COLUMNS = {
    "card_name": "card_name",
    "set_name": "set_name",
    "set_code": "set_code",
    "collector_number": "collector_number",
    "type_line": "type_line",
}

def datalist_options(values):
    seen = set()
    options = []
    for value in values:
        text = str(value or "").strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        options.append(f'<option value="{e(text)}"></option>')
    return "".join(options)

def render_datalist(datalist_id, values):
    return f'<datalist id="{e(datalist_id)}">{datalist_options(values)}</datalist>'

def collection_field_suggestions(user_id, column, limit=80):
    column_name = SUGGESTION_COLUMNS.get(column)
    if not column_name:
        return []
    found = rows(
        f"""
        SELECT DISTINCT {column_name} AS value
        FROM collection_items
        WHERE user_id = ? AND {column_name} != ''
        ORDER BY {column_name} COLLATE NOCASE
        LIMIT ?
        """,
        (user_id, limit),
    )
    return [item["value"] for item in found]

def collection_search_suggestions(user_id, limit=100):
    found = rows(
        """
        SELECT value
        FROM (
            SELECT DISTINCT card_name AS value
            FROM collection_items
            WHERE user_id = ? AND card_name != ''
            UNION
            SELECT DISTINCT type_line AS value
            FROM collection_items
            WHERE user_id = ? AND type_line != ''
        )
        ORDER BY value COLLATE NOCASE
        LIMIT ?
        """,
        (user_id, user_id, limit),
    )
    return [item["value"] for item in found]

def browse_search_suggestions(user_id, limit=100):
    privacy_clause, privacy_params = visibility_sql_for_user_id(
        user_id, "collection_items.visibility", "collection_items.user_id"
    )
    found = rows(
        f"""
        SELECT value
        FROM (
            SELECT DISTINCT collection_items.card_name AS value
            FROM collection_items
            JOIN users ON users.id = collection_items.user_id
            WHERE collection_items.user_id != ?
                AND collection_items.quantity_for_trade > 0
                AND {privacy_clause}
                AND users.is_banned = 0
                AND collection_items.card_name != ''
            UNION
            SELECT DISTINCT collection_items.type_line AS value
            FROM collection_items
            JOIN users ON users.id = collection_items.user_id
            WHERE collection_items.user_id != ?
                AND collection_items.quantity_for_trade > 0
                AND {privacy_clause}
                AND users.is_banned = 0
                AND collection_items.type_line != ''
        )
        ORDER BY value COLLATE NOCASE
        LIMIT ?
        """,
        [user_id, *privacy_params, user_id, *privacy_params, limit],
    )
    return [item["value"] for item in found]

def browse_field_suggestions(user_id, column, limit=80):
    column_name = SUGGESTION_COLUMNS.get(column)
    if not column_name:
        return []
    privacy_clause, privacy_params = visibility_sql_for_user_id(
        user_id, "collection_items.visibility", "collection_items.user_id"
    )
    found = rows(
        f"""
        SELECT DISTINCT collection_items.{column_name} AS value
        FROM collection_items
        JOIN users ON users.id = collection_items.user_id
        WHERE collection_items.user_id != ?
            AND collection_items.quantity_for_trade > 0
            AND {privacy_clause}
            AND users.is_banned = 0
            AND collection_items.{column_name} != ''
        ORDER BY collection_items.{column_name} COLLATE NOCASE
        LIMIT ?
        """,
        [user_id, *privacy_params, limit],
    )
    return [item["value"] for item in found]

def trade_picker_field_suggestions(user_id, column, limit=80, public_only=False):
    column_name = SUGGESTION_COLUMNS.get(column)
    if not column_name:
        return []
    visibility_sql = "AND is_public = 1" if public_only else ""
    found = rows(
        f"""
        SELECT DISTINCT {column_name} AS value
        FROM collection_items
        WHERE user_id = ?
            AND quantity_for_trade > 0
            {visibility_sql}
            AND {column_name} != ''
        ORDER BY {column_name} COLLATE NOCASE
        LIMIT ?
        """,
        (user_id, limit),
    )
    return [item["value"] for item in found]

def trade_picker_search_suggestions(user_id, limit=100, public_only=False):
    visibility_sql = "AND is_public = 1" if public_only else ""
    found = rows(
        f"""
        SELECT value
        FROM (
            SELECT DISTINCT card_name AS value
            FROM collection_items
            WHERE user_id = ? AND quantity_for_trade > 0 {visibility_sql} AND card_name != ''
            UNION
            SELECT DISTINCT type_line AS value
            FROM collection_items
            WHERE user_id = ? AND quantity_for_trade > 0 {visibility_sql} AND type_line != ''
        )
        ORDER BY value COLLATE NOCASE
        LIMIT ?
        """,
        (user_id, user_id, limit),
    )
    return [item["value"] for item in found]

def member_search_suggestions(user_id, limit=100):
    privacy_clause, privacy_params = visibility_sql_for_user_id(
        user_id, "collection_items.visibility", "collection_items.user_id"
    )
    found = rows(
        f"""
        SELECT value
        FROM (
            SELECT DISTINCT display_name AS value
            FROM users
            WHERE id != ? AND is_banned = 0 AND display_name != ''
            UNION
            SELECT DISTINCT username AS value
            FROM users
            WHERE id != ? AND is_banned = 0 AND username != ''
            UNION
            SELECT DISTINCT collection_items.card_name AS value
            FROM users
            JOIN collection_items ON collection_items.user_id = users.id AND collection_items.quantity_for_trade > 0 AND {privacy_clause}
            WHERE users.id != ? AND users.is_banned = 0 AND collection_items.card_name != ''
        )
        ORDER BY value COLLATE NOCASE
        LIMIT ?
        """,
        [user_id, user_id, *privacy_params, user_id, limit],
    )
    return [item["value"] for item in found]

def get_trade_participants(trade):
    return rows(
        "SELECT id, username, display_name FROM users WHERE id IN (?, ?) ORDER BY display_name",
        (trade["proposer_id"], trade["recipient_id"]),
    )

def compact_card_label(item):
    details = []
    if item["set_name"]:
        details.append(item["set_name"])
    if item["collector_number"]:
        details.append(f"#{item['collector_number']}")
    if item["condition"]:
        details.append(item["condition"])
    if item["finish"]:
        details.append(item["finish"])
    suffix = f" ({', '.join(details)})" if details else ""
    return f"{item['card_name']}{suffix}"

def stat_percent(part, total):
    try:
        part_value = float(part or 0)
        total_value = float(total or 0)
    except (TypeError, ValueError):
        return 0.0
    if total_value <= 0:
        return 0.0
    return round((part_value / total_value) * 100, 1)

def stats_color_identity_label(value):
    text = str(value or "").strip()
    if not text:
        return "Colorless or unset"
    labels = dict(COLOR_IDENTITY_OPTIONS)
    parts = [labels.get(part.strip(), part.strip()) for part in text.split(",") if part.strip()]
    return " / ".join(parts) if parts else "Colorless or unset"

def collection_stat_bucket(items, label_func, total_quantity, limit=8):
    buckets = {}
    for item in items:
        label = str(label_func(item) or "").strip() or "Unspecified"
        quantity = max(0, int(row_value(item, "quantity", 0) or 0))
        trade_quantity = max(0, int(row_value(item, "quantity_for_trade", 0) or 0))
        unit_cents = price_to_cents(row_value(item, "price_usd", ""))
        bucket = buckets.setdefault(
            label,
            {"label": label, "entries": 0, "quantity": 0, "trade_quantity": 0, "value_cents": 0},
        )
        bucket["entries"] += 1
        bucket["quantity"] += quantity
        bucket["trade_quantity"] += trade_quantity
        bucket["value_cents"] += quantity * unit_cents
    bucket_rows = sorted(
        buckets.values(),
        key=lambda bucket: (-bucket["quantity"], -bucket["entries"], bucket["label"].lower()),
    )
    for bucket in bucket_rows:
        bucket["percent"] = stat_percent(bucket["quantity"], total_quantity)
    return bucket_rows[:limit] if limit else bucket_rows

def collection_statistics(user_id):
    items = rows(
        """
        SELECT *
        FROM collection_items
        WHERE user_id = ?
        ORDER BY updated_at DESC, id DESC
        """,
        (user_id,),
    )
    group_counts = rows(
        """
        SELECT group_type, COUNT(*) AS count
        FROM card_groups
        WHERE user_id = ?
        GROUP BY group_type
        """,
        (user_id,),
    )
    total_cards = 0
    trade_cards = 0
    public_cards = 0
    private_cards = 0
    public_entries = 0
    private_entries = 0
    public_value_cents = 0
    private_value_cents = 0
    priced_cards = 0
    priced_entries = 0
    scryfall_cards = 0
    scryfall_entries = 0
    image_cards = 0
    image_entries = 0
    total_value_cents = 0
    trade_value_cents = 0
    unique_names = set()
    top_value = []
    for item in items:
        quantity = max(0, int(row_value(item, "quantity", 0) or 0))
        trade_quantity = max(0, int(row_value(item, "quantity_for_trade", 0) or 0))
        total_cards += quantity
        trade_cards += trade_quantity
        unique_names.add(str(row_value(item, "card_name", "") or "").strip().lower())
        normalized_price = normalize_price_usd(row_value(item, "price_usd", ""))
        unit_cents = price_to_cents(normalized_price)
        if row_value(item, "is_public", 1):
            public_entries += 1
            public_cards += quantity
            public_value_cents += unit_cents * quantity
        else:
            private_entries += 1
            private_cards += quantity
            private_value_cents += unit_cents * quantity
        if normalized_price:
            priced_entries += 1
            priced_cards += quantity
            total_value_cents += unit_cents * quantity
            trade_value_cents += unit_cents * trade_quantity
            top_value.append({
                "card_name": row_value(item, "card_name", ""),
                "set_name": row_value(item, "set_name", ""),
                "set_code": row_value(item, "set_code", ""),
                "collector_number": row_value(item, "collector_number", ""),
                "quantity": quantity,
                "unit_cents": unit_cents,
                "total_cents": unit_cents * quantity,
            })
        if row_value(item, "scryfall_id", "") or row_value(item, "type_line", "") or row_value(item, "scryfall_uri", ""):
            scryfall_entries += 1
            scryfall_cards += quantity
        if row_value(item, "image_url", ""):
            image_entries += 1
            image_cards += quantity
    top_value.sort(key=lambda item: (-item["total_cents"], item["card_name"].lower()))
    average_priced_value_cents = int(total_value_cents / priced_cards) if priced_cards else 0
    game_labels = dict(CARD_GAMES)
    group_labels = dict(GROUP_TYPE_OPTIONS)
    return {
        "total_cards": total_cards,
        "trade_cards": trade_cards,
        "unique_entries": len(items),
        "unique_cards": len([name for name in unique_names if name]),
        "public_cards": public_cards,
        "private_cards": private_cards,
        "priced_cards": priced_cards,
        "priced_entries": priced_entries,
        "unpriced_entries": max(0, len(items) - priced_entries),
        "scryfall_cards": scryfall_cards,
        "scryfall_entries": scryfall_entries,
        "image_cards": image_cards,
        "image_entries": image_entries,
        "total_value_cents": total_value_cents,
        "trade_value_cents": trade_value_cents,
        "average_priced_value_cents": average_priced_value_cents,
        "price_coverage_percent": stat_percent(priced_cards, total_cards),
        "scryfall_coverage_percent": stat_percent(scryfall_cards, total_cards),
        "image_coverage_percent": stat_percent(image_cards, total_cards),
        "group_counts": [
            {"label": group_labels.get(item["group_type"], item["group_type"].title()), "count": item["count"]}
            for item in group_counts
        ],
        "top_value": top_value[:8],
        "buckets": {
            "game": collection_stat_bucket(items, lambda item: game_labels.get(row_value(item, "game", ""), row_value(item, "game", "") or "Other"), total_cards),
            "rarity": collection_stat_bucket(items, lambda item: (row_value(item, "rarity", "") or "Unspecified").title(), total_cards),
            "condition": collection_stat_bucket(items, lambda item: row_value(item, "condition", "") or "Unspecified", total_cards),
            "finish": collection_stat_bucket(items, lambda item: row_value(item, "finish", "") or "Unspecified", total_cards),
            "language": collection_stat_bucket(items, lambda item: row_value(item, "language", "") or "Unspecified", total_cards),
            "set": collection_stat_bucket(items, lambda item: row_value(item, "set_name", "") or row_value(item, "set_code", "") or "Unspecified set", total_cards),
            "color_identity": collection_stat_bucket(items, lambda item: stats_color_identity_label(row_value(item, "color_identity", "")), total_cards),
            "visibility": [
                {
                    "label": "Public",
                    "entries": public_entries,
                    "quantity": public_cards,
                    "trade_quantity": 0,
                    "value_cents": public_value_cents,
                    "percent": stat_percent(public_cards, total_cards),
                },
                {
                    "label": "Private",
                    "entries": private_entries,
                    "quantity": private_cards,
                    "trade_quantity": 0,
                    "value_cents": private_value_cents,
                    "percent": stat_percent(private_cards, total_cards),
                },
            ],
        },
    }

def get_collection_summary(user_id):
    stats = row(
        """
        SELECT
            COALESCE(SUM(quantity), 0) AS total_cards,
            COALESCE(SUM(quantity_for_trade), 0) AS trade_cards,
            COUNT(*) AS unique_cards
        FROM collection_items
        WHERE user_id = ?
        """,
        (user_id,),
    )
    wants_count = row("SELECT COUNT(*) AS count FROM want_items WHERE user_id = ?", (user_id,))["count"]
    return {
        "total_cards": stats["total_cards"],
        "trade_cards": stats["trade_cards"],
        "unique_cards": stats["unique_cards"],
        "wants_count": wants_count,
    }

__all__ = [
    'CARD_GAMES',
    'CONDITION_OPTIONS',
    'FINISH_OPTIONS',
    'LANGUAGE_OPTIONS',
    'RARITY_OPTIONS',
    'COLOR_IDENTITY_OPTIONS',
    'CARD_DATA_FILTER_OPTIONS',
    'GROUP_TYPE_OPTIONS',
    'WANT_PRIORITY_OPTIONS',
    'WANT_PRIORITY_LABELS',
    'WANT_PRIORITY_RANKS',
    'TRADE_STATUS_LABELS',
    'TRADE_DISPUTE_CATEGORY_OPTIONS',
    'TRADE_DISPUTE_STATUS_OPTIONS',
    'CSV_SOURCE_OPTIONS',
    'DECK_IMPORT_SOURCE_OPTIONS',
    'PAGE_SIZE_OPTIONS',
    'row_value',
    'money',
    'price_text_from_cents',
    'signed_money',
    'signed_price_text',
    'e',
    'selected',
    'checked',
    'form_public_flag',
    'game_label',
    'normalize_want_priority',
    'want_priority_label',
    'want_priority_rank',
    'option_tags',
    'simple_option_tags',
    'SUGGESTION_COLUMNS',
    'datalist_options',
    'render_datalist',
    'collection_field_suggestions',
    'collection_search_suggestions',
    'browse_search_suggestions',
    'browse_field_suggestions',
    'trade_picker_field_suggestions',
    'trade_picker_search_suggestions',
    'member_search_suggestions',
    'get_trade_participants',
    'compact_card_label',
    'stat_percent',
    'stats_color_identity_label',
    'collection_stat_bucket',
    'collection_statistics',
    'get_collection_summary',
]
