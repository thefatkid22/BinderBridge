"""Mana Pool market-price feed, durable cache, and provider-aware resolver."""

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from binderbridge.config import config_bool, config_int, config_str
from binderbridge.pricing import normalize_price_basis, normalize_price_source, normalize_price_usd
from binderbridge.version import DEFAULT_USER_AGENT


MANAPOOL_PRICE_FEED_URL = config_str(
    "MANAPOOL_PRICE_FEED_URL",
    default="https://manapool.com/api/v1/prices/singles",
    section="pricing",
    key="manapool_price_feed_url",
)
MANAPOOL_PRICE_REFRESH_INTERVAL_HOURS = max(
    1,
    config_int(
        "MANAPOOL_PRICE_REFRESH_INTERVAL_HOURS",
        default=24,
        section="pricing",
        key="manapool_price_refresh_interval_hours",
    ),
)
MANAPOOL_PRICE_REFRESH_AUTO = config_bool(
    "MANAPOOL_PRICE_REFRESH_AUTO",
    default=True,
    section="pricing",
    key="manapool_price_refresh_auto",
)
MANAPOOL_PRICE_TIMEOUT_SECONDS = max(
    5,
    config_int(
        "MANAPOOL_PRICE_TIMEOUT_SECONDS",
        default=60,
        section="pricing",
        key="manapool_price_timeout_seconds",
    ),
)

MANAPOOL_PRICE_REFRESH_STATUS_KEY = "manapool_price_refresh_status"
MANAPOOL_PRICE_REFRESH_UPDATED_KEY = "manapool_price_refresh_updated_at"
MANAPOOL_PRICE_REFRESH_ERROR_KEY = "manapool_price_refresh_error"
MANAPOOL_PRICE_SOURCE_UPDATED_KEY = "manapool_price_source_updated_at"


class ManaPoolPriceFeedError(Exception):
    pass


def _feed_text(value, *, required=False, field="value"):
    if value is None:
        if required:
            raise ManaPoolPriceFeedError(f"Mana Pool feed entry is missing {field}.")
        return ""
    if not isinstance(value, str):
        raise ManaPoolPriceFeedError(f"Mana Pool feed field {field} must be text.")
    text = value.strip()
    if required and not text:
        raise ManaPoolPriceFeedError(f"Mana Pool feed entry is missing {field}.")
    return text


def manapool_cents_to_usd(value):
    """Convert positive integer cents from Mana Pool to a normalized USD string."""
    if value is None or isinstance(value, bool):
        return ""
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return ""
    if amount <= 0 or amount != amount.to_integral_value():
        return ""
    return normalize_price_usd(amount / Decimal("100"))


def _feed_quantity(value):
    if value is None or isinstance(value, bool):
        return 0
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, quantity)


def _feed_source_timestamp(payload):
    meta = payload.get("meta") or {}
    if not isinstance(meta, dict):
        raise ManaPoolPriceFeedError("Mana Pool feed meta must be an object.")
    for key in ("updated_at", "generated_at", "timestamp", "as_of"):
        value = meta.get(key)
        if value is not None:
            return _feed_text(value, field=f"meta.{key}")
    return ""


def parse_manapool_price_feed(payload):
    """Validate the response envelope and normalize supported price records."""
    if not isinstance(payload, dict):
        raise ManaPoolPriceFeedError("Mana Pool feed must be a JSON object.")
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise ManaPoolPriceFeedError("Mana Pool feed data must be a non-empty list.")
    source_updated_at = _feed_source_timestamp(payload)
    parsed = []
    for index, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ManaPoolPriceFeedError(f"Mana Pool feed entry {index} must be an object.")
        scryfall_id = _feed_text(
            entry.get("scryfall_id"), required=True, field=f"data[{index}].scryfall_id"
        ).lower()
        set_code = _feed_text(entry.get("set_code"), field=f"data[{index}].set_code").lower()
        collector_number = _feed_text(
            entry.get("number", entry.get("collector_number")),
            field=f"data[{index}].number",
        )
        source_url = _feed_text(entry.get("url"), field=f"data[{index}].url")
        parsed.append(
            {
                "scryfall_id": scryfall_id,
                "set_code": set_code,
                "collector_number": collector_number,
                "regular_price_usd": manapool_cents_to_usd(entry.get("price_market")),
                "foil_price_usd": manapool_cents_to_usd(entry.get("price_market_foil")),
                "source_url": source_url,
                "available_quantity": _feed_quantity(entry.get("available_quantity")),
                "source_updated_at": source_updated_at,
            }
        )
    id_counts = Counter(entry["scryfall_id"] for entry in parsed)
    parsed = [
        entry for entry in parsed if id_counts[entry["scryfall_id"]] == 1
    ]
    if not parsed:
        raise ManaPoolPriceFeedError(
            "Mana Pool feed has no unambiguous Scryfall-ID price records."
        )
    return parsed


def fetch_manapool_price_feed(opener=urlopen):
    request = Request(
        MANAPOOL_PRICE_FEED_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
        },
    )
    try:
        with opener(request, timeout=MANAPOOL_PRICE_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ManaPoolPriceFeedError(f"Mana Pool price request failed: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManaPoolPriceFeedError("Mana Pool returned invalid JSON.") from exc
    return parse_manapool_price_feed(payload)


def replace_manapool_price_cache(entries, fetched_at=None):
    """Replace the snapshot in one transaction so a failure cannot empty the cache."""
    timestamp = fetched_at or now_iso()
    with db() as conn:
        conn.execute("DELETE FROM provider_price_cache WHERE provider = 'manapool'")
        conn.executemany(
            """
            INSERT INTO provider_price_cache
                (provider, scryfall_id, set_code, collector_number, regular_price_usd,
                 foil_price_usd, source_url, available_quantity, source_updated_at, fetched_at)
            VALUES ('manapool', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    entry["scryfall_id"],
                    entry["set_code"],
                    entry["collector_number"],
                    entry["regular_price_usd"],
                    entry["foil_price_usd"],
                    entry["source_url"],
                    entry["available_quantity"],
                    entry["source_updated_at"],
                    timestamp,
                )
                for entry in entries
            ],
        )
    return len(entries)


def parse_manapool_iso_datetime(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def manapool_price_refresh_status():
    cached = row(
        """
        SELECT COUNT(*) AS count, MAX(fetched_at) AS fetched_at,
               MAX(source_updated_at) AS source_updated_at
        FROM provider_price_cache
        WHERE provider = 'manapool'
        """
    )
    return {
        "status": get_setting(MANAPOOL_PRICE_REFRESH_STATUS_KEY, "idle"),
        "updated_at": get_setting(MANAPOOL_PRICE_REFRESH_UPDATED_KEY, ""),
        "source_updated_at": get_setting(
            MANAPOOL_PRICE_SOURCE_UPDATED_KEY,
            row_value(cached, "source_updated_at", "") or "",
        ),
        "fetched_at": row_value(cached, "fetched_at", "") or "",
        "error": get_setting(MANAPOOL_PRICE_REFRESH_ERROR_KEY, ""),
        "cached_prices": int(row_value(cached, "count", 0) or 0),
        "interval_hours": MANAPOOL_PRICE_REFRESH_INTERVAL_HOURS,
        "auto": MANAPOOL_PRICE_REFRESH_AUTO,
    }


def manapool_price_refresh_due(reference_time=None):
    last_run = parse_manapool_iso_datetime(
        get_setting(MANAPOOL_PRICE_REFRESH_UPDATED_KEY, "")
    )
    if not last_run:
        return True
    reference_time = reference_time or datetime.now(timezone.utc)
    return last_run <= reference_time - timedelta(
        hours=MANAPOOL_PRICE_REFRESH_INTERVAL_HOURS
    )


def _cache_match_for_item(conn, item):
    scryfall_id = str(row_value(item, "scryfall_id", "") or "").strip().lower()
    if scryfall_id:
        found = conn.execute(
            """
            SELECT *
            FROM provider_price_cache
            WHERE provider = 'manapool' AND scryfall_id = ?
            """,
            (scryfall_id,),
        ).fetchone()
        if found:
            return found
    set_code = str(row_value(item, "set_code", "") or "").strip()
    collector_number = str(row_value(item, "collector_number", "") or "").strip()
    if not set_code or not collector_number:
        return None
    matches = conn.execute(
        """
        SELECT *
        FROM provider_price_cache
        WHERE provider = 'manapool'
          AND set_code = ? COLLATE NOCASE
          AND collector_number = ? COLLATE NOCASE
        LIMIT 2
        """,
        (set_code, collector_number),
    ).fetchall()
    return matches[0] if len(matches) == 1 else None


def _scryfall_price_for_item(conn, item):
    raw_price = normalize_price_usd(row_value(item, "price_usd", ""))
    raw_source = normalize_price_source(row_value(item, "price_source", ""))
    if raw_price and raw_source in ("", "scryfall"):
        return raw_price
    scryfall_id = str(row_value(item, "scryfall_id", "") or "").strip().lower()
    if scryfall_id:
        found = conn.execute(
            "SELECT price_usd FROM scryfall_bulk_cards WHERE scryfall_id = ?",
            (scryfall_id,),
        ).fetchone()
        price = normalize_price_usd(row_value(found, "price_usd", ""))
        if price:
            return price
    set_code = str(row_value(item, "set_code", "") or "").strip()
    collector_number = str(row_value(item, "collector_number", "") or "").strip()
    if set_code and collector_number:
        matches = conn.execute(
            """
            SELECT price_usd
            FROM scryfall_bulk_cards
            WHERE set_code = ? COLLATE NOCASE
              AND collector_number = ? COLLATE NOCASE
            LIMIT 2
            """,
            (set_code, collector_number),
        ).fetchall()
        if len(matches) == 1:
            return normalize_price_usd(row_value(matches[0], "price_usd", ""))
    return ""


def resolve_card_price(item, requested_basis="scryfall", conn=None):
    """Return (price_usd, actual_source, requested_basis, used_fallback)."""
    basis = normalize_price_basis(requested_basis)

    def resolve(active_conn):
        fallback_price = _scryfall_price_for_item(active_conn, item)
        if basis != "manapool":
            return fallback_price, "scryfall" if fallback_price else "", basis, False
        game = str(row_value(item, "game", "mtg") or "mtg").strip().lower()
        finish = str(row_value(item, "finish", "") or "").strip().lower()
        if game == "mtg" and "etched" not in finish:
            match = _cache_match_for_item(active_conn, item)
            price_field = "foil_price_usd" if finish == "foil" else "regular_price_usd"
            manapool_price = normalize_price_usd(row_value(match, price_field, ""))
            if manapool_price and Decimal(manapool_price) > 0:
                return manapool_price, "manapool", basis, False
        return fallback_price, "scryfall" if fallback_price else "", basis, True

    if conn is not None:
        return resolve(conn)
    with db() as new_conn:
        return resolve(new_conn)


def apply_price_basis(item, requested_basis="scryfall", conn=None):
    priced = dict(item)
    price, source, basis, used_fallback = resolve_card_price(
        item, requested_basis=requested_basis, conn=conn
    )
    priced["display_price_usd"] = price
    priced["display_price_source"] = source
    priced["requested_price_basis"] = basis
    priced["used_price_fallback"] = used_fallback
    return priced


def apply_viewer_price(item, viewer=None, conn=None):
    basis = (
        normalize_price_basis(row_value(viewer, "preferred_price_source", "scryfall"))
        if viewer
        else "scryfall"
    )
    return apply_price_basis(item, basis, conn=conn)


def apply_price_basis_rows(items, requested_basis="scryfall"):
    with db() as conn:
        return [
            apply_price_basis(item, requested_basis, conn=conn)
            for item in items
        ]


def apply_viewer_prices(items, viewer=None):
    basis = (
        normalize_price_basis(
            row_value(viewer, "preferred_price_source", "scryfall")
        )
        if viewer
        else "scryfall"
    )
    return apply_price_basis_rows(items, basis)


def refresh_manapool_price_cache(opener=urlopen):
    """Fetch, validate, atomically install, and observe the selected price basis."""
    set_setting(MANAPOOL_PRICE_REFRESH_STATUS_KEY, "running")
    try:
        entries = fetch_manapool_price_feed(opener=opener)
        timestamp = now_iso()
        count = replace_manapool_price_cache(entries, fetched_at=timestamp)
        changed = 0
        observed = 0
        with db() as conn:
            items = conn.execute(
                """
                SELECT collection_items.*
                FROM collection_items
                JOIN users ON users.id = collection_items.user_id
                WHERE collection_items.game = 'mtg'
                  AND users.is_banned = 0
                  AND users.preferred_price_source = 'manapool'
                """
            ).fetchall()
            for item in items:
                price, actual_source, basis, _fallback = resolve_card_price(
                    item, "manapool", conn=conn
                )
                if not price:
                    continue
                observed += 1
                status = record_price_history_for_item(
                    item["id"],
                    item["user_id"],
                    item,
                    "",
                    price,
                    conn=conn,
                    observed_at=timestamp,
                    price_source=actual_source,
                    price_basis=basis,
                )
                if status == "changed":
                    changed += 1
        source_updated_at = entries[0]["source_updated_at"] if entries else ""
        set_setting(MANAPOOL_PRICE_REFRESH_UPDATED_KEY, timestamp)
        set_setting(MANAPOOL_PRICE_SOURCE_UPDATED_KEY, source_updated_at)
        set_setting(MANAPOOL_PRICE_REFRESH_STATUS_KEY, "idle")
        set_setting(MANAPOOL_PRICE_REFRESH_ERROR_KEY, "")
        return {"cached": count, "observed": observed, "changed": changed}
    except Exception as exc:
        set_setting(MANAPOOL_PRICE_REFRESH_STATUS_KEY, "error")
        set_setting(MANAPOOL_PRICE_REFRESH_ERROR_KEY, str(exc)[:1000])
        raise


def start_manapool_price_refresh():
    enqueue = globals().get("enqueue_background_job")
    if not enqueue:
        return False
    _job_id, created = enqueue(
        "manapool_price_sync",
        unique_key="system:manapool-price-sync",
        max_attempts=10,
    )
    expedite = globals().get("expedite_background_job")
    if expedite:
        expedite("system:manapool-price-sync")
    return created


__all__ = [
    "MANAPOOL_PRICE_FEED_URL",
    "MANAPOOL_PRICE_REFRESH_INTERVAL_HOURS",
    "MANAPOOL_PRICE_REFRESH_AUTO",
    "MANAPOOL_PRICE_TIMEOUT_SECONDS",
    "MANAPOOL_PRICE_REFRESH_STATUS_KEY",
    "MANAPOOL_PRICE_REFRESH_UPDATED_KEY",
    "MANAPOOL_PRICE_REFRESH_ERROR_KEY",
    "MANAPOOL_PRICE_SOURCE_UPDATED_KEY",
    "ManaPoolPriceFeedError",
    "manapool_cents_to_usd",
    "parse_manapool_price_feed",
    "fetch_manapool_price_feed",
    "replace_manapool_price_cache",
    "parse_manapool_iso_datetime",
    "manapool_price_refresh_status",
    "manapool_price_refresh_due",
    "resolve_card_price",
    "apply_price_basis",
    "apply_viewer_price",
    "apply_price_basis_rows",
    "apply_viewer_prices",
    "refresh_manapool_price_cache",
    "start_manapool_price_refresh",
]
