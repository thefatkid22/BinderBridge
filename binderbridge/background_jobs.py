"""Legacy external price-refresh job queues and workers.

The app facade injects shared helpers into this module so existing app.py
imports keep working while queue logic lives in one focused place.
"""

import json
import threading
import time
from datetime import datetime, timedelta, timezone

from binderbridge.pricing import (
    PRICE_PROVIDER_ID_FIELDS,
    PRICE_PROVIDER_KEYS,
    PRICE_REFRESH_AUTO,
    PRICE_REFRESH_BATCH_SIZE,
    PRICE_REFRESH_DELAY_SECONDS,
    PRICE_REFRESH_INTERVAL_HOURS,
)


class PriceRefreshError(Exception):
    pass


class PriceRefreshRateLimitError(PriceRefreshError):
    def __init__(self, message, retry_after=300):
        super().__init__(message)
        self.retry_after = retry_after


_price_refresh_worker_lock = threading.Lock()
_price_refresh_worker_started = False


def price_provider_label(provider):
    return price_source_label(provider)


def price_provider_ready(provider):
    return False


def price_provider_statuses():
    return {}


def selected_price_providers(provider):
    provider = str(provider or "all").strip().lower()
    if provider == "all":
        return list(PRICE_PROVIDER_KEYS)
    return [provider] if provider in PRICE_PROVIDER_KEYS else []


def item_external_id(item, provider):
    field = PRICE_PROVIDER_ID_FIELDS.get(provider, "")
    return str(row_value(item, field, "") or "").strip()


def item_has_refresh_key(item, provider):
    return False


def parse_iso_datetime(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def item_price_is_stale(item, stale_hours):
    refreshed_at = parse_iso_datetime(row_value(item, "price_refreshed_at", ""))
    if not refreshed_at:
        return True
    return refreshed_at <= datetime.now(timezone.utc) - timedelta(hours=stale_hours)


def provider_price_is_stale(conn, collection_item_id, provider, stale_hours):
    cached = conn.execute(
        """
        SELECT fetched_at
        FROM card_price_sources
        WHERE collection_item_id = ? AND provider = ?
        """,
        (collection_item_id, provider),
    ).fetchone()
    if not cached:
        return True
    fetched_at = parse_iso_datetime(cached["fetched_at"])
    if not fetched_at:
        return True
    return fetched_at <= datetime.now(timezone.utc) - timedelta(hours=stale_hours)


def schedule_price_refresh_jobs(user_id, provider="all", force=True, stale_hours=PRICE_REFRESH_INTERVAL_HOURS):
    providers = selected_price_providers(provider)
    if not providers:
        return 0
    items = rows("SELECT * FROM collection_items WHERE user_id = ?", (user_id,))
    timestamp = now_iso()
    queued = 0
    with db() as conn:
        for item in items:
            for provider_key in providers:
                if not price_provider_ready(provider_key) or not item_has_refresh_key(item, provider_key):
                    continue
                if not force and not provider_price_is_stale(conn, item["id"], provider_key, stale_hours):
                    continue
                conn.execute(
                    """
                    INSERT INTO price_refresh_jobs
                        (collection_item_id, user_id, provider, status, attempts, last_error, available_at, created_at, updated_at)
                    VALUES (?, ?, ?, 'pending', 0, '', '', ?, ?)
                    ON CONFLICT(collection_item_id, provider) DO UPDATE SET
                        status = 'pending',
                        attempts = 0,
                        last_error = '',
                        available_at = '',
                        updated_at = excluded.updated_at
                    """,
                    (item["id"], user_id, provider_key, timestamp, timestamp),
                )
                queued += 1
    return queued


def schedule_price_refresh_jobs_for_users(user_ids, provider="all", force=False, stale_hours=PRICE_REFRESH_INTERVAL_HOURS):
    queued = 0
    seen = set()
    for user_id in user_ids:
        try:
            clean_id = int(user_id)
        except (TypeError, ValueError):
            continue
        if clean_id in seen:
            continue
        seen.add(clean_id)
        queued += schedule_price_refresh_jobs(clean_id, provider, force=force, stale_hours=stale_hours)
    return queued


def apply_cached_provider_prices(user_ids, provider):
    provider = normalize_price_basis(provider)
    if provider not in PRICE_PROVIDER_KEYS:
        return 0
    clean_ids = []
    for user_id in user_ids:
        try:
            clean_ids.append(int(user_id))
        except (TypeError, ValueError):
            continue
    clean_ids = list(dict.fromkeys(clean_ids))
    if not clean_ids:
        return 0
    placeholders = ",".join("?" for _ in clean_ids)
    timestamp = now_iso()
    with db() as conn:
        cursor = conn.execute(
            f"""
            UPDATE collection_items
            SET price_usd = (
                    SELECT card_price_sources.price_usd
                    FROM card_price_sources
                    WHERE card_price_sources.collection_item_id = collection_items.id
                        AND card_price_sources.provider = ?
                    LIMIT 1
                ),
                price_source = ?,
                price_refreshed_at = (
                    SELECT card_price_sources.fetched_at
                    FROM card_price_sources
                    WHERE card_price_sources.collection_item_id = collection_items.id
                        AND card_price_sources.provider = ?
                    LIMIT 1
                ),
                price_status = '',
                updated_at = ?
            WHERE user_id IN ({placeholders})
                AND EXISTS (
                    SELECT 1
                    FROM card_price_sources
                    WHERE card_price_sources.collection_item_id = collection_items.id
                        AND card_price_sources.provider = ?
                        AND card_price_sources.price_usd != ''
                )
            """,
            [provider, provider, provider, timestamp, *clean_ids, provider],
        )
        return cursor.rowcount


def prepare_price_basis_for_users(user_ids, price_basis, force=False):
    provider = normalize_price_basis(price_basis)
    if provider not in PRICE_PROVIDER_KEYS:
        return {"provider": provider, "applied": 0, "queued": 0, "configured": True}
    applied = apply_cached_provider_prices(user_ids, provider)
    queued = 0
    configured = price_provider_ready(provider)
    if configured:
        queued = schedule_price_refresh_jobs_for_users(user_ids, provider=provider, force=force)
    return {"provider": provider, "applied": applied, "queued": queued, "configured": configured}


def price_basis_update_notice(result):
    provider = result.get("provider", "")
    if provider not in PRICE_PROVIDER_KEYS:
        return ""
    label = price_provider_label(provider)
    if not result.get("configured", True):
        return f"{label} is not configured yet. Cached prices will be used if they already exist."
    applied = int(result.get("applied", 0) or 0)
    queued = int(result.get("queued", 0) or 0)
    if queued:
        return f"Applied {applied} cached {label} prices and queued {queued} batched refresh jobs."
    if applied:
        return f"Applied {applied} cached {label} prices."
    return f"No cached {label} prices were ready yet. Add provider IDs or run a refresh."


def schedule_due_price_refresh_jobs():
    total = 0
    for user in rows("SELECT id FROM users WHERE is_banned = 0"):
        total += schedule_price_refresh_jobs(user["id"], "all", force=False, stale_hours=PRICE_REFRESH_INTERVAL_HOURS)
    return total


def claim_price_refresh_jobs(batch_size=PRICE_REFRESH_BATCH_SIZE):
    if not PRICE_PROVIDER_KEYS:
        return "", []
    active_provider_placeholders = ",".join("?" for _ in PRICE_PROVIDER_KEYS)
    with db() as conn:
        provider_row = conn.execute(
            f"""
            SELECT provider
            FROM price_refresh_jobs
            WHERE provider IN ({active_provider_placeholders})
                AND status = 'pending'
                AND (available_at = '' OR available_at <= ?)
            GROUP BY provider
            ORDER BY MIN(created_at)
            LIMIT 1
            """,
            (*PRICE_PROVIDER_KEYS, now_iso()),
        ).fetchone()
        if not provider_row:
            return "", []
        provider = provider_row["provider"]
        jobs = conn.execute(
            """
            SELECT
                price_refresh_jobs.*,
                collection_items.card_name,
                collection_items.set_name,
                collection_items.set_code,
                collection_items.collector_number,
                collection_items.finish,
                collection_items.condition,
                collection_items.language,
                collection_items.tcgplayer_product_id,
                collection_items.cardmarket_product_id,
                collection_items.cardkingdom_sku
            FROM price_refresh_jobs
            JOIN collection_items ON collection_items.id = price_refresh_jobs.collection_item_id
            WHERE price_refresh_jobs.provider = ?
                AND price_refresh_jobs.status = 'pending'
                AND (price_refresh_jobs.available_at = '' OR price_refresh_jobs.available_at <= ?)
            ORDER BY price_refresh_jobs.created_at
            LIMIT ?
            """,
            (provider, now_iso(), batch_size),
        ).fetchall()
        if not jobs:
            return "", []
        job_ids = [job["id"] for job in jobs]
        placeholders = ",".join("?" for _ in job_ids)
        conn.execute(
            f"UPDATE price_refresh_jobs SET status = 'processing', updated_at = ? WHERE id IN ({placeholders})",
            [now_iso(), *job_ids],
        )
        return provider, jobs


def mark_price_job(conn, job, status, error="", attempts=None, retry_after=0):
    next_attempts = job["attempts"] if attempts is None else attempts
    conn.execute(
        """
        UPDATE price_refresh_jobs
        SET status = ?, attempts = ?, last_error = ?, available_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, next_attempts, error[:1000], future_iso(retry_after) if retry_after else "", now_iso(), job["id"]),
    )


def record_price_refresh_success(conn, job, result):
    price = normalize_price_usd(result.get("price_usd", ""))
    if not price:
        mark_price_job(conn, job, "not_found", "No usable price was returned.")
        return
    provider = job["provider"]
    external_id = str(result.get("external_id") or item_external_id(job, provider) or "").strip()
    timestamp = now_iso()
    raw_json = result.get("raw_json", {})
    if not isinstance(raw_json, str):
        raw_json = json.dumps(raw_json)
    conn.execute(
        """
        INSERT INTO card_price_sources
            (collection_item_id, provider, external_id, price_usd, price_label, raw_json, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(collection_item_id, provider) DO UPDATE SET
            external_id = excluded.external_id,
            price_usd = excluded.price_usd,
            price_label = excluded.price_label,
            raw_json = excluded.raw_json,
            fetched_at = excluded.fetched_at
        """,
        (
            job["collection_item_id"],
            provider,
            external_id,
            price,
            str(result.get("price_label") or price_provider_label(provider)),
            raw_json,
            timestamp,
        ),
    )
    extra_updates = []
    params = [price, provider, timestamp, "", timestamp]
    id_field = PRICE_PROVIDER_ID_FIELDS.get(provider)
    if id_field and external_id:
        extra_updates.append(f"{id_field} = ?")
        params.append(external_id)
    params.append(job["collection_item_id"])
    extra_sql = f", {', '.join(extra_updates)}" if extra_updates else ""
    conn.execute(
        f"""
        UPDATE collection_items
        SET price_usd = ?, price_source = ?, price_refreshed_at = ?, price_status = ?, updated_at = ?{extra_sql}
        WHERE id = ?
        """,
        params,
    )
    mark_price_job(conn, job, "done", "")


def fetch_price_batch_for_provider(provider, jobs):
    raise PriceRefreshError("External price refresh has been removed. Scryfall is the only pricing source.")


def process_price_refresh_once():
    provider, jobs = claim_price_refresh_jobs()
    if not jobs:
        return False
    try:
        results = fetch_price_batch_for_provider(provider, jobs)
    except PriceRefreshRateLimitError as exc:
        with db() as conn:
            for job in jobs:
                mark_price_job(conn, job, "pending", str(exc), attempts=job["attempts"], retry_after=exc.retry_after)
        return True
    except PriceRefreshError as exc:
        with db() as conn:
            for job in jobs:
                attempts = job["attempts"] + 1
                status = "failed" if attempts >= 3 else "pending"
                mark_price_job(conn, job, status, str(exc), attempts=attempts, retry_after=attempts * 300 if status == "pending" else 0)
        return True
    with db() as conn:
        for job in jobs:
            result = results.get(job["collection_item_id"])
            if result:
                record_price_refresh_success(conn, job, result)
            else:
                attempts = job["attempts"] + 1
                status = "not_found" if attempts >= 2 else "pending"
                mark_price_job(conn, job, status, f"{price_provider_label(provider)} did not return a price.", attempts=attempts, retry_after=attempts * 300 if status == "pending" else 0)
    if PRICE_REFRESH_DELAY_SECONDS:
        time.sleep(PRICE_REFRESH_DELAY_SECONDS)
    return True


def price_refresh_worker_loop():
    last_auto_schedule = 0.0
    while True:
        if PRICE_REFRESH_AUTO and time.monotonic() - last_auto_schedule > 600:
            schedule_due_price_refresh_jobs()
            last_auto_schedule = time.monotonic()
        processed = process_price_refresh_once()
        time.sleep(0.25 if processed else 5)


def start_price_refresh_worker():
    global _price_refresh_worker_started
    with _price_refresh_worker_lock:
        if _price_refresh_worker_started:
            return False
        execute("UPDATE price_refresh_jobs SET status = 'pending', updated_at = ? WHERE status = 'processing'", (now_iso(),))
        thread = threading.Thread(target=price_refresh_worker_loop, name="price-refresh", daemon=True)
        thread.start()
        _price_refresh_worker_started = True
        return True


def price_refresh_stats():
    found = rows("SELECT status, COUNT(*) AS count FROM price_refresh_jobs GROUP BY status")
    stats = {"pending": 0, "processing": 0, "done": 0, "not_found": 0, "failed": 0}
    for item in found:
        stats[item["status"]] = item["count"]
    return stats
__all__ = [
    "PriceRefreshError",
    "PriceRefreshRateLimitError",
    "price_provider_label",
    "price_provider_ready",
    "price_provider_statuses",
    "selected_price_providers",
    "item_external_id",
    "item_has_refresh_key",
    "parse_iso_datetime",
    "item_price_is_stale",
    "provider_price_is_stale",
    "schedule_price_refresh_jobs",
    "schedule_price_refresh_jobs_for_users",
    "apply_cached_provider_prices",
    "prepare_price_basis_for_users",
    "price_basis_update_notice",
    "schedule_due_price_refresh_jobs",
    "claim_price_refresh_jobs",
    "mark_price_job",
    "record_price_refresh_success",
    "fetch_price_batch_for_provider",
    "process_price_refresh_once",
    "price_refresh_worker_loop",
    "start_price_refresh_worker",
    "price_refresh_stats",
]
