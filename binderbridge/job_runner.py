"""Durable background job orchestration for embedded and external workers."""

import json
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from binderbridge.api import (
    WEBHOOK_DELIVERY_BATCH_SIZE,
    WEBHOOK_DELIVERY_INTERVAL_SECONDS,
    WEBHOOK_DELIVERY_WORKER_ENABLED,
)
from binderbridge.config import config_bool, config_float, config_int, config_str
from binderbridge.maintenance import AUTOMATIC_BACKUP_WORKER_CHECK_SECONDS
from binderbridge.notifications import NOTIFICATION_WORKER_INTERVAL_SECONDS
from binderbridge.pricing import (
    SCRYFALL_BULK_ERROR_KEY,
    SCRYFALL_BULK_STATUS_KEY,
    SCRYFALL_PRICE_REFRESH_AUTO,
)


BACKGROUND_JOB_MODE = config_str(
    "BINDERBRIDGE_JOB_RUNNER_MODE",
    default="embedded",
    section="jobs",
    key="mode",
).strip().lower()
if BACKGROUND_JOB_MODE not in ("embedded", "external", "disabled"):
    BACKGROUND_JOB_MODE = "embedded"
BACKGROUND_JOB_POLL_SECONDS = max(
    0.1,
    config_float("BINDERBRIDGE_JOB_POLL_SECONDS", default=1.0, section="jobs", key="poll_seconds"),
)
BACKGROUND_JOB_LEASE_SECONDS = max(
    60,
    config_int("BINDERBRIDGE_JOB_LEASE_SECONDS", default=3600, section="jobs", key="lease_seconds"),
)
BACKGROUND_JOB_RETRY_BASE_SECONDS = max(
    5,
    config_int("BINDERBRIDGE_JOB_RETRY_BASE_SECONDS", default=30, section="jobs", key="retry_base_seconds"),
)
BACKGROUND_JOB_RETRY_MAX_SECONDS = max(
    BACKGROUND_JOB_RETRY_BASE_SECONDS,
    config_int("BINDERBRIDGE_JOB_RETRY_MAX_SECONDS", default=3600, section="jobs", key="retry_max_seconds"),
)
BACKGROUND_JOB_HISTORY_DAYS = max(
    0,
    config_int("BINDERBRIDGE_JOB_HISTORY_DAYS", default=30, section="jobs", key="history_days"),
)
BACKGROUND_JOB_EMBEDDED_ENABLED = config_bool(
    "BINDERBRIDGE_JOB_RUNNER_ENABLED",
    default=True,
    section="jobs",
    key="enabled",
)

JOB_TYPE_LABELS = {
    "automatic_backup": "Automatic backup",
    "notification_delivery": "Notification delivery",
    "webhook_delivery": "Webhook delivery",
    "scryfall_enrichment": "Scryfall enrichment",
    "scryfall_bulk_sync": "Scryfall bulk sync",
    "scryfall_price_refresh": "Scryfall price refresh",
    "legacy_price_refresh": "Legacy price queue",
}
BACKGROUND_JOB_TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")

_runner_lock = threading.Lock()
_runner_started = False
_runner_wakeup = threading.Event()
_runner_stop = threading.Event()


class BackgroundJobRetry(Exception):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def parse_job_json(value, default=None):
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {} if default is None else default
    return parsed


def job_available_at(delay_seconds=0):
    delay = max(0.0, float(delay_seconds or 0))
    return (datetime.now(timezone.utc) + timedelta(seconds=delay)).replace(microsecond=0).isoformat()


def pending_domain_queue_delay(table_name):
    if table_name not in ("scryfall_enrichment_jobs", "price_refresh_jobs"):
        raise ValueError("Choose a supported domain job queue.")
    timestamp = now_iso()
    found = row(
        f"""
        SELECT COUNT(*) AS count,
            MIN(CASE WHEN available_at = '' THEN ? ELSE available_at END) AS next_available_at
        FROM {table_name}
        WHERE status = 'pending'
        """,
        (timestamp,),
    )
    if not found or not int(found["count"] or 0):
        return None
    try:
        next_at = datetime.fromisoformat(str(found["next_available_at"] or timestamp).replace("Z", "+00:00"))
        if next_at.tzinfo is None:
            next_at = next_at.replace(tzinfo=timezone.utc)
        delay = (next_at.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
    except ValueError:
        delay = 0.25
    return max(0.25, delay)


def enqueue_background_job(
    job_type,
    payload=None,
    unique_key="",
    priority=0,
    max_attempts=5,
    delay_seconds=0,
    conn=None,
):
    clean_type = str(job_type or "").strip().lower()
    if clean_type not in JOB_TYPE_LABELS:
        raise ValueError("Choose a supported background job type.")
    clean_key = str(unique_key or "").strip()[:180]
    timestamp = now_iso()
    payload_json = json.dumps(payload or {}, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    def insert(active_conn):
        if clean_key:
            found = active_conn.execute(
                """
                SELECT id
                FROM background_jobs
                WHERE unique_key = ? AND status IN ('pending', 'running')
                ORDER BY id DESC
                LIMIT 1
                """,
                (clean_key,),
            ).fetchone()
            if found:
                return int(found["id"]), False
        cursor = active_conn.execute(
            """
            INSERT OR IGNORE INTO background_jobs
                (job_type, unique_key, payload_json, status, priority, attempts, max_attempts,
                 available_at, created_at, updated_at)
            VALUES (?, ?, ?, 'pending', ?, 0, ?, ?, ?, ?)
            """,
            (
                clean_type,
                clean_key,
                payload_json,
                int(priority or 0),
                max(1, int(max_attempts or 1)),
                job_available_at(delay_seconds),
                timestamp,
                timestamp,
            ),
        )
        if cursor.rowcount:
            return int(cursor.lastrowid), True
        found = active_conn.execute(
            """
            SELECT id
            FROM background_jobs
            WHERE unique_key = ? AND status IN ('pending', 'running')
            ORDER BY id DESC
            LIMIT 1
            """,
            (clean_key,),
        ).fetchone()
        if found:
            return int(found["id"]), False
        raise RuntimeError("Background job could not be queued.")

    if conn is not None:
        job_id, created = insert(conn)
    else:
        with db() as active_conn:
            job_id, created = insert(active_conn)
    wake_background_job_runner()
    return job_id, created


def expedite_background_job(unique_key, conn=None):
    clean_key = str(unique_key or "").strip()[:180]
    if not clean_key:
        return 0
    timestamp = now_iso()

    def update(active_conn):
        cursor = active_conn.execute(
            """
            UPDATE background_jobs
            SET available_at = ?, updated_at = ?
            WHERE unique_key = ? AND status = 'pending' AND available_at > ?
            """,
            (timestamp, timestamp, clean_key, timestamp),
        )
        return cursor.rowcount

    if conn is not None:
        updated = update(conn)
    else:
        with db() as active_conn:
            updated = update(active_conn)
    wake_background_job_runner()
    return updated


def recover_expired_background_jobs():
    timestamp = now_iso()
    with db() as conn:
        cursor = conn.execute(
            """
            UPDATE background_jobs
            SET status = 'pending',
                lease_owner = '',
                leased_until = '',
                available_at = ?,
                last_error = CASE
                    WHEN last_error = '' THEN 'Worker lease expired; job returned to the queue.'
                    ELSE last_error
                END,
                updated_at = ?
            WHERE status = 'running' AND leased_until != '' AND leased_until <= ?
            """,
            (timestamp, timestamp, timestamp),
        )
        return cursor.rowcount


def claim_background_job(worker_id, lease_seconds=None):
    recover_expired_background_jobs()
    timestamp = now_iso()
    lease_until = job_available_at(lease_seconds or BACKGROUND_JOB_LEASE_SECONDS)
    with db() as conn:
        candidates = conn.execute(
            """
            SELECT id
            FROM background_jobs
            WHERE status = 'pending' AND available_at <= ?
            ORDER BY priority DESC, available_at, created_at, id
            LIMIT 12
            """,
            (timestamp,),
        ).fetchall()
        for candidate in candidates:
            cursor = conn.execute(
                """
                UPDATE background_jobs
                SET status = 'running',
                    attempts = attempts + 1,
                    lease_owner = ?,
                    leased_until = ?,
                    started_at = CASE WHEN started_at = '' THEN ? ELSE started_at END,
                    updated_at = ?
                WHERE id = ? AND status = 'pending' AND available_at <= ?
                """,
                (worker_id, lease_until, timestamp, timestamp, candidate["id"], timestamp),
            )
            if cursor.rowcount:
                return conn.execute("SELECT * FROM background_jobs WHERE id = ?", (candidate["id"],)).fetchone()
    return None


def update_background_job_progress(job_id, worker_id, current=0, total=0, message=""):
    execute(
        """
        UPDATE background_jobs
        SET progress_current = ?,
            progress_total = ?,
            progress_message = ?,
            leased_until = ?,
            updated_at = ?
        WHERE id = ? AND status = 'running' AND lease_owner = ?
        """,
        (
            max(0, int(current or 0)),
            max(0, int(total or 0)),
            sanitize_text_input(message, max_length=300).strip(),
            job_available_at(BACKGROUND_JOB_LEASE_SECONDS),
            now_iso(),
            int(job_id),
            worker_id,
        ),
    )


def finish_background_job(job, worker_id, result=None):
    result = result or {}
    repeat_seconds = result.pop("repeat_seconds", None)
    reschedule_seconds = result.pop("reschedule_seconds", None)
    timestamp = now_iso()
    if repeat_seconds is not None:
        execute(
            """
            UPDATE background_jobs
            SET status = 'pending',
                attempts = 0,
                available_at = ?,
                result_json = ?,
                last_error = '',
                lease_owner = '',
                leased_until = '',
                progress_current = 0,
                progress_total = 0,
                progress_message = 'Waiting for next scheduled run',
                completed_at = '',
                updated_at = ?
            WHERE id = ? AND status = 'running' AND lease_owner = ?
            """,
            (
                job_available_at(repeat_seconds),
                json.dumps(result, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
                timestamp,
                job["id"],
                worker_id,
            ),
        )
        return
    with db() as conn:
        conn.execute(
            """
            UPDATE background_jobs
            SET status = 'succeeded',
                result_json = ?,
                last_error = '',
                lease_owner = '',
                leased_until = '',
                progress_message = CASE WHEN progress_message = '' THEN 'Completed' ELSE progress_message END,
                completed_at = ?,
                updated_at = ?
            WHERE id = ? AND status = 'running' AND lease_owner = ?
            """,
            (
                json.dumps(result, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
                timestamp,
                timestamp,
                job["id"],
                worker_id,
            ),
        )
    if reschedule_seconds is not None:
        enqueue_background_job(
            job["job_type"],
            parse_job_json(job["payload_json"], {}),
            unique_key=job["unique_key"],
            priority=job["priority"],
            max_attempts=job["max_attempts"],
            delay_seconds=max(0, float(reschedule_seconds)),
        )


def fail_background_job(job, worker_id, error, retry_after=None):
    attempts = int(job["attempts"] or 0)
    max_attempts = max(1, int(job["max_attempts"] or 1))
    message = sanitize_text_input(str(error or "Background job failed."), max_length=1000).strip()
    retry = attempts < max_attempts
    if retry_after is None:
        retry_after = min(
            BACKGROUND_JOB_RETRY_MAX_SECONDS,
            BACKGROUND_JOB_RETRY_BASE_SECONDS * (2 ** max(0, attempts - 1)),
        )
    timestamp = now_iso()
    execute(
        """
        UPDATE background_jobs
        SET status = ?,
            available_at = ?,
            lease_owner = '',
            leased_until = '',
            last_error = ?,
            progress_message = ?,
            completed_at = ?,
            updated_at = ?
        WHERE id = ? AND status = 'running' AND lease_owner = ?
        """,
        (
            "pending" if retry else "failed",
            job_available_at(retry_after) if retry else timestamp,
            message,
            f"Retrying after attempt {attempts}" if retry else "Failed",
            "" if retry else timestamp,
            timestamp,
            job["id"],
            worker_id,
        ),
    )


def retry_background_job(job_id):
    try:
        clean_id = int(job_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Choose a background job to retry.") from exc
    timestamp = now_iso()
    with db() as conn:
        job = conn.execute("SELECT * FROM background_jobs WHERE id = ?", (clean_id,)).fetchone()
        if not job:
            raise ValueError("That background job was not found.")
        if job["status"] not in ("failed", "cancelled"):
            raise ValueError("Only failed or cancelled background jobs can be retried.")
        conn.execute(
            """
            UPDATE background_jobs
            SET status = 'pending', attempts = 0, available_at = ?, lease_owner = '', leased_until = '',
                last_error = '', result_json = '{}', progress_current = 0, progress_total = 0,
                progress_message = 'Queued for retry', started_at = '', completed_at = '', updated_at = ?
            WHERE id = ?
            """,
            (timestamp, timestamp, clean_id),
        )
    wake_background_job_runner()
    return dict(job)


def cancel_background_job(job_id):
    try:
        clean_id = int(job_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Choose a background job to cancel.") from exc
    timestamp = now_iso()
    with db() as conn:
        job = conn.execute("SELECT * FROM background_jobs WHERE id = ?", (clean_id,)).fetchone()
        if not job:
            raise ValueError("That background job was not found.")
        if job["status"] not in ("pending", "running"):
            raise ValueError("Only pending or running background jobs can be cancelled.")
        conn.execute(
            """
            UPDATE background_jobs
            SET status = 'cancelled', lease_owner = '', leased_until = '', completed_at = ?,
                progress_message = 'Cancelled by an administrator', updated_at = ?
            WHERE id = ?
            """,
            (timestamp, timestamp, clean_id),
        )
    return dict(job)


def prune_background_job_history(retention_days=None):
    days = BACKGROUND_JOB_HISTORY_DAYS if retention_days is None else max(0, int(retention_days))
    if not days:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()
    with db() as conn:
        cursor = conn.execute(
            """
            DELETE FROM background_jobs
            WHERE status IN ('succeeded', 'failed', 'cancelled')
                AND completed_at != '' AND completed_at < ?
            """,
            (cutoff,),
        )
        return cursor.rowcount


def background_job_counts():
    return rows("SELECT status, COUNT(*) AS count FROM background_jobs GROUP BY status ORDER BY status")


def background_job_rows(limit=25):
    return rows(
        """
        SELECT *
        FROM background_jobs
        ORDER BY
            CASE status WHEN 'failed' THEN 0 WHEN 'running' THEN 1 WHEN 'pending' THEN 2 ELSE 3 END,
            updated_at DESC,
            id DESC
        LIMIT ?
        """,
        (max(1, int(limit)),),
    )


def background_job_runner_status():
    return {
        "mode": BACKGROUND_JOB_MODE,
        "embedded_enabled": BACKGROUND_JOB_EMBEDDED_ENABLED,
        "started": _runner_started,
        "poll_seconds": BACKGROUND_JOB_POLL_SECONDS,
        "lease_seconds": BACKGROUND_JOB_LEASE_SECONDS,
        "history_days": BACKGROUND_JOB_HISTORY_DAYS,
        "counts": background_job_counts(),
    }


def job_handler_scryfall_enrichment(job, payload, worker_id):
    update_background_job_progress(job["id"], worker_id, message="Processing queued Scryfall lookup")
    processed = process_scryfall_enrichment_once()
    delay = pending_domain_queue_delay("scryfall_enrichment_jobs")
    result = {"processed": bool(processed), "pending": delay is not None}
    if delay is not None:
        result["repeat_seconds"] = delay
    return result


def job_handler_scryfall_bulk_sync(job, payload, worker_id):
    update_background_job_progress(job["id"], worker_id, message="Syncing Scryfall bulk data")
    count = run_scryfall_bulk_sync()
    status = get_setting(SCRYFALL_BULK_STATUS_KEY, "idle")
    error = get_setting(SCRYFALL_BULK_ERROR_KEY, "")
    if status == "error" or error:
        raise BackgroundJobRetry(error or "Scryfall bulk sync failed.", retry_after=300)
    return {"cards": count}


def job_handler_scryfall_price_refresh(job, payload, worker_id):
    if payload.get("force") or (SCRYFALL_PRICE_REFRESH_AUTO and scryfall_price_refresh_due()):
        update_background_job_progress(job["id"], worker_id, message="Refreshing Scryfall prices")
        result = refresh_all_scryfall_prices(sync_bulk=True)
    else:
        result = {"due": False}
    if job["unique_key"] == "system:scryfall-price-refresh":
        result["repeat_seconds"] = 300
    return result


def job_handler_automatic_backup(job, payload, worker_id):
    update_background_job_progress(job["id"], worker_id, message="Checking automatic backup schedule")
    result = run_automatic_backup_once()
    if result.get("ran") and result.get("success") is False:
        raise BackgroundJobRetry(result.get("error", "Automatic backup failed."), retry_after=300)
    result["repeat_seconds"] = AUTOMATIC_BACKUP_WORKER_CHECK_SECONDS
    return result


def job_handler_notification_delivery(job, payload, worker_id):
    update_background_job_progress(job["id"], worker_id, message="Processing reminders and notification email")
    result = notification_worker_pass()
    result["repeat_seconds"] = NOTIFICATION_WORKER_INTERVAL_SECONDS
    return result


def job_handler_webhook_delivery(job, payload, worker_id):
    update_background_job_progress(job["id"], worker_id, message="Delivering queued webhooks")
    result = send_pending_webhook_deliveries(limit=WEBHOOK_DELIVERY_BATCH_SIZE)
    result["repeat_seconds"] = WEBHOOK_DELIVERY_INTERVAL_SECONDS
    return result


def job_handler_legacy_price_refresh(job, payload, worker_id):
    processed = process_price_refresh_once()
    delay = pending_domain_queue_delay("price_refresh_jobs")
    result = {"processed": bool(processed), "pending": delay is not None}
    if delay is not None:
        result["repeat_seconds"] = delay
    return result


BACKGROUND_JOB_HANDLERS = {
    "automatic_backup": job_handler_automatic_backup,
    "notification_delivery": job_handler_notification_delivery,
    "webhook_delivery": job_handler_webhook_delivery,
    "scryfall_enrichment": job_handler_scryfall_enrichment,
    "scryfall_bulk_sync": job_handler_scryfall_bulk_sync,
    "scryfall_price_refresh": job_handler_scryfall_price_refresh,
    "legacy_price_refresh": job_handler_legacy_price_refresh,
}


def process_background_job_once(worker_id=None):
    worker_id = worker_id or f"worker-{secrets.token_hex(6)}"
    job = claim_background_job(worker_id)
    if not job:
        return False
    handler = BACKGROUND_JOB_HANDLERS.get(job["job_type"])
    if not handler:
        fail_background_job(job, worker_id, f"No handler is registered for {job['job_type']}.", retry_after=0)
        return True
    try:
        result = handler(job, parse_job_json(job["payload_json"], {}), worker_id) or {}
        finish_background_job(job, worker_id, dict(result))
    except BackgroundJobRetry as exc:
        fail_background_job(job, worker_id, exc, retry_after=exc.retry_after)
    except Exception as exc:
        write_log_message(f"Background job #{job['id']} ({job['job_type']}) failed: {exc}")
        fail_background_job(job, worker_id, exc)
    return True


def ensure_background_job_schedules():
    pending_enrichment = row(
        "SELECT COUNT(*) AS count FROM scryfall_enrichment_jobs WHERE status = 'pending'"
    )["count"]
    if pending_enrichment:
        enqueue_background_job("scryfall_enrichment", unique_key="system:scryfall-enrichment", max_attempts=100000, delay_seconds=1)
    enqueue_background_job("scryfall_price_refresh", unique_key="system:scryfall-price-refresh", max_attempts=10, delay_seconds=2)
    enqueue_background_job("automatic_backup", unique_key="system:automatic-backup", max_attempts=10, delay_seconds=3)
    enqueue_background_job("notification_delivery", unique_key="system:notification-delivery", max_attempts=10, delay_seconds=1)
    if WEBHOOK_DELIVERY_WORKER_ENABLED:
        enqueue_background_job("webhook_delivery", unique_key="system:webhook-delivery", max_attempts=10, delay_seconds=1)


def wake_background_job_runner():
    _runner_wakeup.set()


def run_background_worker_forever(worker_id=None, seed_schedules=True):
    worker_id = worker_id or f"{BACKGROUND_JOB_MODE}-{secrets.token_hex(6)}"
    recover_expired_background_jobs()
    if seed_schedules:
        ensure_background_job_schedules()
    last_prune = 0.0
    while not _runner_stop.is_set():
        processed = process_background_job_once(worker_id)
        if time.monotonic() - last_prune > 3600:
            prune_background_job_history()
            last_prune = time.monotonic()
        if processed:
            continue
        _runner_wakeup.wait(BACKGROUND_JOB_POLL_SECONDS)
        _runner_wakeup.clear()


def start_background_job_runner():
    global _runner_started
    if not BACKGROUND_JOB_EMBEDDED_ENABLED or BACKGROUND_JOB_MODE != "embedded":
        return False
    with _runner_lock:
        if _runner_started:
            return False
        _runner_stop.clear()
        thread = threading.Thread(
            target=run_embedded_background_worker,
            kwargs={"worker_id": f"embedded-{secrets.token_hex(6)}", "seed_schedules": True},
            name="binderbridge-job-runner",
            daemon=True,
        )
        _runner_started = True
        try:
            thread.start()
        except Exception:
            _runner_started = False
            raise
        return True


def run_embedded_background_worker(worker_id, seed_schedules=True):
    global _runner_started
    try:
        run_background_worker_forever(worker_id=worker_id, seed_schedules=seed_schedules)
    finally:
        with _runner_lock:
            _runner_started = False


def stop_background_job_runner():
    _runner_stop.set()
    _runner_wakeup.set()


__all__ = [
    "BACKGROUND_JOB_MODE",
    "BACKGROUND_JOB_POLL_SECONDS",
    "BACKGROUND_JOB_LEASE_SECONDS",
    "BACKGROUND_JOB_RETRY_BASE_SECONDS",
    "BACKGROUND_JOB_RETRY_MAX_SECONDS",
    "BACKGROUND_JOB_HISTORY_DAYS",
    "BACKGROUND_JOB_EMBEDDED_ENABLED",
    "JOB_TYPE_LABELS",
    "BACKGROUND_JOB_TERMINAL_STATUSES",
    "BackgroundJobRetry",
    "parse_job_json",
    "job_available_at",
    "pending_domain_queue_delay",
    "enqueue_background_job",
    "expedite_background_job",
    "recover_expired_background_jobs",
    "claim_background_job",
    "update_background_job_progress",
    "finish_background_job",
    "fail_background_job",
    "retry_background_job",
    "cancel_background_job",
    "prune_background_job_history",
    "background_job_counts",
    "background_job_rows",
    "background_job_runner_status",
    "BACKGROUND_JOB_HANDLERS",
    "process_background_job_once",
    "ensure_background_job_schedules",
    "wake_background_job_runner",
    "run_background_worker_forever",
    "run_embedded_background_worker",
    "start_background_job_runner",
    "stop_background_job_runner",
]
