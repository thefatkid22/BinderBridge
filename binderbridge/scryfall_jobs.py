"""Scryfall enrichment, bulk-sync, and automatic price-refresh workers.

The app facade injects shared helpers into this module so existing app.py
exports stay compatible while Scryfall-specific job logic lives in one place.
"""

import threading
import time
from datetime import datetime, timedelta, timezone

from binderbridge.pricing import (
    SCRYFALL_BULK_ERROR_KEY,
    SCRYFALL_BULK_STATUS_KEY,
    SCRYFALL_PRICE_REFRESH_AUTO,
    SCRYFALL_PRICE_REFRESH_ERROR_KEY,
    SCRYFALL_PRICE_REFRESH_INTERVAL_HOURS,
    SCRYFALL_PRICE_REFRESH_STATUS_KEY,
    SCRYFALL_PRICE_REFRESH_UPDATED_KEY,
)


_scryfall_worker_lock = threading.Lock()
_scryfall_worker_started = False
_scryfall_price_refresh_worker_lock = threading.Lock()
_scryfall_price_refresh_worker_started = False


def parse_scryfall_iso_datetime(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def refresh_user_scryfall_prices(user_id):
    result = {"checked": 0, "priced": 0, "changed": 0, "missing": 0}
    timestamp = now_iso()
    with db() as conn:
        items = conn.execute(
            """
            SELECT *
            FROM collection_items
            WHERE user_id = ? AND game = 'mtg'
            ORDER BY card_name COLLATE NOCASE
            """,
            (user_id,),
        ).fetchall()
        for item in items:
            result["checked"] += 1
            card_data = local_scryfall_card_from_conn(conn, item)
            price = normalize_price_usd(card_data.get("price_usd", "") if card_data else "")
            if not price:
                result["missing"] += 1
                continue
            result["priced"] += 1
            status = record_price_history_for_item(
                item["id"],
                user_id,
                item,
                row_value(item, "price_usd", ""),
                price,
                conn=conn,
                observed_at=timestamp,
            )
            if status == "changed":
                result["changed"] += 1
            conn.execute(
                """
                UPDATE collection_items
                SET price_usd = ?,
                    price_source = 'scryfall',
                    price_refreshed_at = ?,
                    price_status = '',
                    updated_at = ?
                WHERE id = ?
                """,
                (price, timestamp, timestamp, item["id"]),
            )
    return result


def scryfall_price_refresh_status():
    return {
        "status": get_setting(SCRYFALL_PRICE_REFRESH_STATUS_KEY, "idle"),
        "updated_at": get_setting(SCRYFALL_PRICE_REFRESH_UPDATED_KEY, ""),
        "error": get_setting(SCRYFALL_PRICE_REFRESH_ERROR_KEY, ""),
        "interval_hours": SCRYFALL_PRICE_REFRESH_INTERVAL_HOURS,
        "auto": SCRYFALL_PRICE_REFRESH_AUTO,
    }


def scryfall_price_refresh_due(reference_time=None):
    last_run = parse_scryfall_iso_datetime(get_setting(SCRYFALL_PRICE_REFRESH_UPDATED_KEY, ""))
    if not last_run:
        return True
    reference_time = reference_time or datetime.now(timezone.utc)
    return last_run <= reference_time - timedelta(hours=SCRYFALL_PRICE_REFRESH_INTERVAL_HOURS)


def refresh_all_scryfall_prices(sync_bulk=True, notify_users=True):
    set_setting(SCRYFALL_PRICE_REFRESH_STATUS_KEY, "running")
    result = {"users": 0, "checked": 0, "priced": 0, "changed": 0, "missing": 0, "bulk_synced": 0}
    try:
        mtg_count = row("SELECT COUNT(*) AS count FROM collection_items WHERE game = 'mtg'")["count"]
        if not mtg_count:
            set_setting(SCRYFALL_PRICE_REFRESH_STATUS_KEY, "idle")
            set_setting(SCRYFALL_PRICE_REFRESH_ERROR_KEY, "")
            return result
        if sync_bulk and get_setting(SCRYFALL_BULK_STATUS_KEY, "idle") != "running":
            result["bulk_synced"] = run_scryfall_bulk_sync()
        for user in rows("SELECT id FROM users WHERE is_banned = 0"):
            user_result = refresh_user_scryfall_prices(user["id"])
            result["users"] += 1
            for key in ("checked", "priced", "changed", "missing"):
                result[key] += user_result[key]
            if notify_users and user_result["checked"]:
                body = (
                    f"Checked {user_result['checked']} MTG collection entr"
                    f"{'y' if user_result['checked'] == 1 else 'ies'} and refreshed "
                    f"{user_result['priced']} Scryfall price{'s' if user_result['priced'] != 1 else ''}."
                )
                if user_result["changed"]:
                    body += f" {user_result['changed']} price{'s' if user_result['changed'] != 1 else ''} changed."
                if user_result["missing"]:
                    body += f" {user_result['missing']} entr{'y' if user_result['missing'] == 1 else 'ies'} did not have a local Scryfall price yet."
                create_notification(
                    user["id"],
                    "price_refresh",
                    "Scryfall prices updated",
                    body,
                    "/collection",
                )
        timestamp = now_iso()
        set_setting(SCRYFALL_PRICE_REFRESH_UPDATED_KEY, timestamp)
        set_setting(SCRYFALL_PRICE_REFRESH_STATUS_KEY, "idle")
        set_setting(SCRYFALL_PRICE_REFRESH_ERROR_KEY, "")
        return result
    except Exception as exc:
        set_setting(SCRYFALL_PRICE_REFRESH_STATUS_KEY, "error")
        set_setting(SCRYFALL_PRICE_REFRESH_ERROR_KEY, str(exc)[:1000])
        raise


def scryfall_price_refresh_worker_loop():
    while True:
        if SCRYFALL_PRICE_REFRESH_AUTO and scryfall_price_refresh_due():
            try:
                refresh_all_scryfall_prices(sync_bulk=True)
            except Exception:
                pass
        time.sleep(300)


def start_scryfall_price_refresh_worker():
    enqueue = globals().get("enqueue_background_job")
    if enqueue:
        _job_id, created = enqueue(
            "scryfall_price_refresh",
            unique_key="system:scryfall-price-refresh",
            max_attempts=10,
        )
        expedite = globals().get("expedite_background_job")
        if expedite:
            expedite("system:scryfall-price-refresh")
        return created
    return False


def run_scryfall_bulk_sync():
    try:
        set_setting(SCRYFALL_BULK_STATUS_KEY, "running")
        count = sync_scryfall_bulk_data()
        set_setting(SCRYFALL_BULK_STATUS_KEY, "idle")
        set_setting(SCRYFALL_BULK_ERROR_KEY, "")
        return count
    except Exception as exc:
        set_setting(SCRYFALL_BULK_STATUS_KEY, "error")
        set_setting(SCRYFALL_BULK_ERROR_KEY, str(exc)[:1000])
        return 0


def start_scryfall_bulk_sync():
    enqueue = globals().get("enqueue_background_job")
    if enqueue:
        _job_id, created = enqueue(
            "scryfall_bulk_sync",
            unique_key="system:scryfall-bulk-sync",
            priority=10,
            max_attempts=3,
        )
        if created:
            set_setting(SCRYFALL_BULK_STATUS_KEY, "queued")
        expedite = globals().get("expedite_background_job")
        if expedite:
            expedite("system:scryfall-bulk-sync")
        return created
    return False


def enqueue_scryfall_enrichment(collection_item_id, user_id, item):
    if item.get("game") != "mtg":
        return False
    lookup_key = scryfall_cache_key(item["card_name"], item.get("set_code", ""), item.get("collector_number", ""), item.get("scryfall_id", ""))
    timestamp = now_iso()
    execute(
        """
        INSERT INTO scryfall_enrichment_jobs
            (collection_item_id, user_id, lookup_key, card_name, set_code, collector_number, scryfall_id,
             status, attempts, last_error, available_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', 0, '', '', ?, ?)
        ON CONFLICT(collection_item_id) DO UPDATE SET
            lookup_key = excluded.lookup_key,
            card_name = excluded.card_name,
            set_code = excluded.set_code,
            collector_number = excluded.collector_number,
            scryfall_id = excluded.scryfall_id,
            status = 'pending',
            last_error = '',
            available_at = '',
            completion_notified = 0,
            updated_at = excluded.updated_at
        """,
        (
            collection_item_id,
            user_id,
            lookup_key,
            item["card_name"],
            item.get("set_code", ""),
            item.get("collector_number", ""),
            item.get("scryfall_id", ""),
            timestamp,
            timestamp,
        ),
    )
    return True


def claim_scryfall_enrichment_job():
    with db() as conn:
        job = conn.execute(
            """
            SELECT *
            FROM scryfall_enrichment_jobs
            WHERE status = 'pending' AND (available_at = '' OR available_at <= ?)
            ORDER BY created_at
            LIMIT 1
            """,
            (now_iso(),),
        ).fetchone()
        if not job:
            return None
        conn.execute("UPDATE scryfall_enrichment_jobs SET status = 'processing', updated_at = ? WHERE id = ?", (now_iso(), job["id"]))
        return job


def update_collection_item_from_scryfall(collection_item_id, card_data):
    price = normalize_price_usd(card_data.get("price_usd", ""))
    with db() as conn:
        existing = conn.execute("SELECT * FROM collection_items WHERE id = ?", (collection_item_id,)).fetchone()
        if not existing:
            return
        conn.execute(
            """
            UPDATE collection_items
            SET card_name = ?, set_name = ?, set_code = ?, collector_number = ?,
                scryfall_id = ?, image_url = ?, mana_cost = ?, type_line = ?, oracle_text = ?,
                rarity = ?, colors = ?, color_identity = ?, scryfall_uri = ?, price_usd = ?, price_source = ?,
                tcgplayer_product_id = COALESCE(NULLIF(?, ''), tcgplayer_product_id),
                cardmarket_product_id = COALESCE(NULLIF(?, ''), cardmarket_product_id),
                price_refreshed_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                card_data.get("card_name", ""),
                card_data.get("set_name", ""),
                card_data.get("set_code", ""),
                card_data.get("collector_number", ""),
                card_data.get("scryfall_id", ""),
                card_data.get("image_url", ""),
                card_data.get("mana_cost", ""),
                card_data.get("type_line", ""),
                card_data.get("oracle_text", ""),
                card_data.get("rarity", ""),
                card_data.get("colors", ""),
                card_data.get("color_identity", ""),
                card_data.get("scryfall_uri", ""),
                price,
                "scryfall" if price else "",
                card_data.get("tcgplayer_product_id", ""),
                card_data.get("cardmarket_product_id", ""),
                now_iso() if price else row_value(existing, "price_refreshed_at", ""),
                now_iso(),
                collection_item_id,
            ),
        )
        history_item = dict(existing)
        history_item.update(card_data)
        history_item["price_usd"] = price
        record_price_history_for_item(collection_item_id, existing["user_id"], history_item, row_value(existing, "price_usd", ""), price, conn=conn)


def mark_scryfall_job(job_id, status, error="", attempts=None, retry_after=0):
    with db() as conn:
        current = conn.execute("SELECT attempts, user_id FROM scryfall_enrichment_jobs WHERE id = ?", (job_id,)).fetchone()
        if not current:
            return
        next_attempts = current["attempts"] if attempts is None else attempts
        conn.execute(
            """
            UPDATE scryfall_enrichment_jobs
            SET status = ?, attempts = ?, last_error = ?, available_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, next_attempts, error[:1000], future_iso(retry_after) if retry_after else "", now_iso(), job_id),
        )
        if status in SCRYFALL_ENRICHMENT_TERMINAL_STATUSES:
            notify_scryfall_enrichment_completion(current["user_id"], conn)


def process_scryfall_enrichment_once():
    job = claim_scryfall_enrichment_job()
    if not job:
        return False
    try:
        card_data = lookup_scryfall_card(job["card_name"], job["set_code"], job["collector_number"], job["scryfall_id"])
        if card_data:
            update_collection_item_from_scryfall(job["collection_item_id"], card_data)
            mark_scryfall_job(job["id"], "done", "")
        else:
            mark_scryfall_job(job["id"], "not_found", f"Scryfall did not find {job['card_name']}.")
    except ScryfallRateLimitError as exc:
        mark_scryfall_job(job["id"], "pending", str(exc), attempts=job["attempts"], retry_after=max(30, exc.retry_after))
    except ScryfallError as exc:
        attempts = job["attempts"] + 1
        status = "failed" if attempts >= 3 else "pending"
        mark_scryfall_job(job["id"], status, str(exc), attempts=attempts, retry_after=attempts * 60 if status == "pending" else 0)
    return True


def scryfall_enrichment_worker_loop():
    while True:
        processed = process_scryfall_enrichment_once()
        time.sleep(0.25 if processed else 5)


def start_scryfall_enrichment_worker():
    enqueue = globals().get("enqueue_background_job")
    if enqueue:
        execute("UPDATE scryfall_enrichment_jobs SET status = 'pending', updated_at = ? WHERE status = 'processing'", (now_iso(),))
        _job_id, created = enqueue(
            "scryfall_enrichment",
            unique_key="system:scryfall-enrichment",
            priority=5,
            max_attempts=100000,
        )
        expedite = globals().get("expedite_background_job")
        if expedite:
            expedite("system:scryfall-enrichment")
        return created
    return False


def scryfall_enrichment_stats():
    found = rows("SELECT status, COUNT(*) AS count FROM scryfall_enrichment_jobs GROUP BY status")
    stats = {"pending": 0, "processing": 0, "done": 0, "not_found": 0, "failed": 0}
    for item in found:
        stats[item["status"]] = item["count"]
    return stats


__all__ = [
    "parse_scryfall_iso_datetime",
    "refresh_user_scryfall_prices",
    "scryfall_price_refresh_status",
    "scryfall_price_refresh_due",
    "refresh_all_scryfall_prices",
    "scryfall_price_refresh_worker_loop",
    "start_scryfall_price_refresh_worker",
    "run_scryfall_bulk_sync",
    "start_scryfall_bulk_sync",
    "enqueue_scryfall_enrichment",
    "claim_scryfall_enrichment_job",
    "update_collection_item_from_scryfall",
    "mark_scryfall_job",
    "process_scryfall_enrichment_once",
    "scryfall_enrichment_worker_loop",
    "start_scryfall_enrichment_worker",
    "scryfall_enrichment_stats",
]
