"""Pricing normalization, preferences, price history, and value-change alert helpers.

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

SCRYFALL_BULK_TYPE = config_str("SCRYFALL_BULK_TYPE", default="default_cards", section="scryfall", key="bulk_type")

SCRYFALL_BULK_STATUS_KEY = "scryfall_bulk_status"

SCRYFALL_BULK_UPDATED_KEY = "scryfall_bulk_updated_at"

SCRYFALL_BULK_ERROR_KEY = "scryfall_bulk_error"

SCRYFALL_PRICE_REFRESH_STATUS_KEY = "scryfall_price_refresh_status"

SCRYFALL_PRICE_REFRESH_UPDATED_KEY = "scryfall_price_refresh_updated_at"

SCRYFALL_PRICE_REFRESH_ERROR_KEY = "scryfall_price_refresh_error"

PRICE_REFRESH_BATCH_SIZE = max(1, config_int("PRICE_REFRESH_BATCH_SIZE", default=25, section="pricing", key="price_refresh_batch_size"))

PRICE_REFRESH_DELAY_SECONDS = max(0.0, config_float("PRICE_REFRESH_DELAY_SECONDS", default=0.25, section="pricing", key="price_refresh_delay_seconds"))

PRICE_REFRESH_INTERVAL_HOURS = max(1, config_int("PRICE_REFRESH_INTERVAL_HOURS", default=24, section="pricing", key="price_refresh_interval_hours"))

PRICE_REFRESH_AUTO = config_bool("PRICE_REFRESH_AUTO", default=False, section="pricing", key="price_refresh_auto")

SCRYFALL_PRICE_REFRESH_INTERVAL_HOURS = max(1, config_int("SCRYFALL_PRICE_REFRESH_INTERVAL_HOURS", default=24, section="pricing", key="scryfall_price_refresh_interval_hours"))

SCRYFALL_PRICE_REFRESH_AUTO = config_bool("SCRYFALL_PRICE_REFRESH_AUTO", default=True, section="pricing", key="scryfall_price_refresh_auto")

PRICE_SOURCE_OPTIONS = [
    ("scryfall", "Scryfall"),
]

PRICE_PROVIDER_KEYS = ()

PRICE_PROVIDER_OPTIONS = []

PRICE_BASIS_OPTIONS = [
    ("scryfall", "Scryfall"),
]

PRICE_PROVIDER_ID_FIELDS = {}

def normalize_price_usd(value):
    text = str(value or "").strip().replace("$", "").replace("€", "").replace("£", "")
    text = text.replace("USD", "").replace("usd", "").replace("EUR", "").replace("eur", "").strip()
    if "," in text and "." not in text:
        parts = text.split(",")
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            text = ".".join(parts)
        else:
            text = text.replace(",", "")
    else:
        text = text.replace(",", "")
    if not text:
        return ""
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        return ""
    if amount < 0:
        return ""
    return str(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def price_to_cents(value):
    normalized = normalize_price_usd(value)
    if not normalized:
        return 0
    return int((Decimal(normalized) * 100).to_integral_value(rounding=ROUND_HALF_UP))

def normalize_price_source(value):
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = normalize_header(text)
    return "scryfall" if normalized else ""

def price_source_label(value):
    return "Scryfall" if str(value or "").strip() else "Unknown"

def normalize_price_basis(value):
    return "scryfall"

def price_basis_label(value):
    return "Scryfall"

def user_price_preference(user):
    return normalize_price_basis(row_value(user, "preferred_price_source", ""))

def price_pill(item):
    price = normalize_price_usd(row_value(item, "display_price_usd", row_value(item, "price_usd", "")))
    if not price:
        return ""
    source = row_value(item, "display_price_source", row_value(item, "price_source", "")) or ("scryfall" if row_value(item, "scryfall_id", "") or row_value(item, "scryfall_uri", "") else "")
    source_label = price_source_label(source)
    title = f' title="Price source: {e(source_label)}"' if source_label else ""
    return f' <span class="pill price-pill"{title}>${e(price)}<span>{e(source_label)}</span></span>'

def price_change_percent(previous_cents, delta_cents):
    if not previous_cents:
        return ""
    percent = (Decimal(delta_cents) / Decimal(previous_cents) * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return str(percent)

def normalize_price_alert_threshold(value):
    text = str(value or "").strip()
    if not text:
        return "0"
    try:
        percent = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Price alert threshold must be a number.") from exc
    if percent < 0:
        raise ValueError("Price alert threshold cannot be negative.")
    if percent > Decimal("10000"):
        raise ValueError("Price alert threshold is too high.")
    normalized = percent.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(normalized.normalize(), "f") if normalized else "0"

def user_price_alert_settings(user_id, conn=None):
    query = "SELECT price_alerts_enabled, price_alert_threshold_percent FROM users WHERE id = ?"
    found = conn.execute(query, (user_id,)).fetchone() if conn is not None else row(query, (user_id,))
    if not found:
        return True, Decimal("0")
    enabled = bool(int(row_value(found, "price_alerts_enabled", 1) or 0))
    try:
        threshold = Decimal(str(row_value(found, "price_alert_threshold_percent", "0") or "0"))
    except (InvalidOperation, ValueError):
        threshold = Decimal("0")
    return enabled, max(Decimal("0"), threshold)

def should_send_price_alert(user_id, change_percent, conn=None):
    enabled, threshold = user_price_alert_settings(user_id, conn=conn)
    if not enabled:
        return False
    if threshold <= 0:
        return True
    if not change_percent:
        return True
    try:
        actual = abs(Decimal(str(change_percent)))
    except (InvalidOperation, ValueError):
        return True
    return actual >= threshold

def price_history_rows(collection_item_id, user_id, limit=8):
    return rows(
        """
        SELECT *
        FROM price_history
        WHERE collection_item_id = ? AND user_id = ?
        ORDER BY observed_at DESC, id DESC
        LIMIT ?
        """,
        (collection_item_id, user_id, int(limit)),
    )

def price_history_summary(user_id, limit=6):
    return rows(
        """
        SELECT *
        FROM price_history
        WHERE user_id = ? AND previous_price_usd != '' AND price_usd != previous_price_usd
        ORDER BY observed_at DESC, id DESC
        LIMIT ?
        """,
        (user_id, int(limit)),
    )

def card_price_history_label(card_name, set_name="", collector_number=""):
    details = []
    if set_name:
        details.append(set_name)
    if collector_number:
        details.append(f"#{collector_number}")
    suffix = f" ({', '.join(details)})" if details else ""
    return f"{card_name}{suffix}"

def record_price_history_for_item(collection_item_id, user_id, item, previous_price_usd, price_usd, conn=None, observed_at=None):
    current_price = normalize_price_usd(price_usd)
    if not current_price:
        return "missing"
    previous_price = normalize_price_usd(previous_price_usd)
    item_id = int(collection_item_id or row_value(item, "id", 0) or 0)
    timestamp = observed_at or now_iso()

    def write_history(active_conn):
        latest = active_conn.execute(
            """
            SELECT price_usd
            FROM price_history
            WHERE collection_item_id = ?
            ORDER BY observed_at DESC, id DESC
            LIMIT 1
            """,
            (item_id,),
        ).fetchone()
        latest_price = normalize_price_usd(row_value(latest, "price_usd", ""))
        comparison_price = previous_price or latest_price
        current_cents = price_to_cents(current_price)
        comparison_cents = price_to_cents(comparison_price)
        if latest_price and price_to_cents(latest_price) == current_cents:
            return "same"

        changed = bool(comparison_price) and comparison_cents != current_cents
        delta_cents = current_cents - comparison_cents if changed else 0
        change_amount = price_text_from_cents(delta_cents) if changed else ""
        change_percent = price_change_percent(comparison_cents, delta_cents) if changed else ""
        active_conn.execute(
            """
            INSERT INTO price_history
                (collection_item_id, user_id, card_name, set_name, set_code, collector_number,
                 price_usd, price_source, previous_price_usd, change_amount, change_percent, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'scryfall', ?, ?, ?, ?)
            """,
            (
                item_id or None,
                user_id,
                row_value(item, "card_name", ""),
                row_value(item, "set_name", ""),
                row_value(item, "set_code", ""),
                row_value(item, "collector_number", ""),
                current_price,
                comparison_price if changed else "",
                change_amount,
                change_percent,
                timestamp,
            ),
        )
        if changed and should_send_price_alert(user_id, change_percent, conn=active_conn):
            label = card_price_history_label(
                row_value(item, "card_name", "Card"),
                row_value(item, "set_name", ""),
                row_value(item, "collector_number", ""),
            )
            direction = "increased" if delta_cents > 0 else "decreased"
            percent_text = f" ({change_percent}%)" if change_percent else ""
            create_notification(
                user_id,
                "price_alert",
                f"Price {direction}: {label}",
                f"Scryfall moved from ${comparison_price} to ${current_price}: {signed_money(delta_cents)}{percent_text}.",
                f"/collection/{item_id}/edit" if item_id else "/collection",
                None,
                conn=active_conn,
            )
        return "changed" if changed else "baseline"

    if conn is not None:
        return write_history(conn)
    with db() as new_conn:
        return write_history(new_conn)

__all__ = [
    'SCRYFALL_BULK_TYPE',
    'SCRYFALL_BULK_STATUS_KEY',
    'SCRYFALL_BULK_UPDATED_KEY',
    'SCRYFALL_BULK_ERROR_KEY',
    'SCRYFALL_PRICE_REFRESH_STATUS_KEY',
    'SCRYFALL_PRICE_REFRESH_UPDATED_KEY',
    'SCRYFALL_PRICE_REFRESH_ERROR_KEY',
    'PRICE_REFRESH_BATCH_SIZE',
    'PRICE_REFRESH_DELAY_SECONDS',
    'PRICE_REFRESH_INTERVAL_HOURS',
    'PRICE_REFRESH_AUTO',
    'SCRYFALL_PRICE_REFRESH_INTERVAL_HOURS',
    'SCRYFALL_PRICE_REFRESH_AUTO',
    'PRICE_SOURCE_OPTIONS',
    'PRICE_PROVIDER_KEYS',
    'PRICE_PROVIDER_OPTIONS',
    'PRICE_BASIS_OPTIONS',
    'PRICE_PROVIDER_ID_FIELDS',
    'normalize_price_usd',
    'price_to_cents',
    'normalize_price_source',
    'price_source_label',
    'normalize_price_basis',
    'price_basis_label',
    'user_price_preference',
    'price_pill',
    'price_change_percent',
    'normalize_price_alert_threshold',
    'user_price_alert_settings',
    'should_send_price_alert',
    'price_history_rows',
    'price_history_summary',
    'card_price_history_label',
    'record_price_history_for_item',
]
