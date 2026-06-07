"""Maintenance, backup, restore, and retention helpers for BinderBridge."""

import json
import secrets
import shutil
import sqlite3
import threading
import time
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from binderbridge.config import config_bool, config_int, config_str


BACKUP_DIR_NAME = "backups"
BACKUP_DATABASE_NAME = "binderbridge.sqlite3"
BACKUP_MAX_BYTES = 128 * 1024 * 1024
AUTOMATIC_BACKUP_PREFIX = "binderbridge-auto"
AUTOMATIC_BACKUP_ENABLED_KEY = "automatic_backup_enabled"
AUTOMATIC_BACKUP_INTERVAL_HOURS_KEY = "automatic_backup_interval_hours"
AUTOMATIC_BACKUP_RETENTION_COUNT_KEY = "automatic_backup_retention_count"
AUTOMATIC_BACKUP_RETENTION_DAYS_KEY = "automatic_backup_retention_days"
AUTOMATIC_BACKUP_LAST_RUN_KEY = "automatic_backup_last_run"
AUTOMATIC_BACKUP_LAST_SUCCESS_KEY = "automatic_backup_last_success"
AUTOMATIC_BACKUP_LAST_ERROR_KEY = "automatic_backup_last_error"
BACKUP_INTEGRITY_LAST_CHECK_KEY = "backup_integrity_last_check"
BACKUP_INTEGRITY_LAST_STATUS_KEY = "backup_integrity_last_status"
BACKUP_INTEGRITY_LAST_MESSAGE_KEY = "backup_integrity_last_message"
BACKUP_INTEGRITY_LAST_CHECKED_KEY = "backup_integrity_last_checked"
BACKUP_INTEGRITY_LAST_FAILED_KEY = "backup_integrity_last_failed"
DATA_RETENTION_NOTIFICATION_DAYS_KEY = "data_retention_notification_days"
DATA_RETENTION_ADMIN_LOG_DAYS_KEY = "data_retention_admin_log_days"
DATA_RETENTION_WEBHOOK_DAYS_KEY = "data_retention_webhook_days"
DATA_RETENTION_LAST_RUN_KEY = "data_retention_last_run"
DATA_RETENTION_LAST_RESULT_KEY = "data_retention_last_result"
DEFAULT_AUTOMATIC_BACKUP_ENABLED = config_bool("BINDERBRIDGE_BACKUP_AUTO_ENABLED", "BACKUP_AUTO_ENABLED", default=True, section="backups", key="auto_enabled")
DEFAULT_AUTOMATIC_BACKUP_INTERVAL_HOURS = max(1, config_int("BINDERBRIDGE_BACKUP_INTERVAL_HOURS", "BACKUP_INTERVAL_HOURS", default=24, section="backups", key="interval_hours"))
DEFAULT_AUTOMATIC_BACKUP_RETENTION_COUNT = max(1, config_int("BINDERBRIDGE_BACKUP_RETENTION_COUNT", "BACKUP_RETENTION_COUNT", default=14, section="backups", key="retention_count"))
DEFAULT_AUTOMATIC_BACKUP_RETENTION_DAYS = max(0, config_int("BINDERBRIDGE_BACKUP_RETENTION_DAYS", "BACKUP_RETENTION_DAYS", default=30, section="backups", key="retention_days"))
DEFAULT_DATA_RETENTION_NOTIFICATION_DAYS = max(0, config_int("BINDERBRIDGE_NOTIFICATION_RETENTION_DAYS", default=90, section="retention", key="notification_days"))
DEFAULT_DATA_RETENTION_ADMIN_LOG_DAYS = max(0, config_int("BINDERBRIDGE_ADMIN_LOG_RETENTION_DAYS", default=365, section="retention", key="admin_log_days"))
DEFAULT_DATA_RETENTION_WEBHOOK_DAYS = max(0, config_int("BINDERBRIDGE_WEBHOOK_RETENTION_DAYS", default=90, section="retention", key="webhook_days"))
AUTOMATIC_BACKUP_WORKER_CHECK_SECONDS = 60

_automatic_backup_worker_lock = threading.Lock()
_automatic_backup_worker_started = False
_job_dashboard_price_retry_lock = threading.Lock()
_job_dashboard_price_retry_running = False


JOB_DASHBOARD_RETRY_STATUSES = ("failed", "not_found", "processing")


def backup_directory():
    path = DATA_DIR / BACKUP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def bytes_label(size):
    size = int(size or 0)
    units = ("B", "KB", "MB", "GB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{size} B"
        value /= 1024
    return f"{size} B"


def backup_archive_name(prefix="binderbridge-backup"):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}-{secrets.token_hex(3)}.zip"


def backup_archive_info(path):
    stat = path.stat()
    return {
        "name": path.name,
        "path": str(path),
        "size": stat.st_size,
        "size_label": bytes_label(stat.st_size),
        "created_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


def backup_archive_paths(prefix=None):
    directory = backup_directory()
    pattern = f"{prefix}-*.zip" if prefix else "*.zip"
    return sorted(directory.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)


def backup_status(limit=6):
    directory = backup_directory()
    archives = backup_archive_paths()
    db_size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    return {
        "backup_dir": str(directory),
        "database_path": str(DB_PATH),
        "database_size_label": bytes_label(db_size),
        "archives": [backup_archive_info(path) for path in archives[:limit]],
        "automatic": automatic_backup_status(),
        "integrity": backup_integrity_status(),
    }


def status_count_rows(table_name, status_column="status", where_sql="", params=()):
    sql = f"""
        SELECT {status_column} AS status, COUNT(*) AS count
        FROM {table_name}
        {where_sql}
        GROUP BY {status_column}
        ORDER BY {status_column} COLLATE NOCASE
    """
    return rows(sql, params)


def table_count(table_name):
    found = row(f"SELECT COUNT(*) AS count FROM {table_name}")
    return found["count"] if found else 0


def normalize_data_retention_days(value, label):
    try:
        days = int(str(value or "0").strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a whole number of days.") from exc
    if days < 0:
        raise ValueError(f"{label} must be 0 or more.")
    if days > 36500:
        raise ValueError(f"{label} must be 36500 or less.")
    return days


def data_retention_setting(key, default):
    try:
        return normalize_data_retention_days(get_setting(key, default), "Retention")
    except ValueError:
        return default


def data_retention_settings():
    return {
        "notification_days": data_retention_setting(DATA_RETENTION_NOTIFICATION_DAYS_KEY, DEFAULT_DATA_RETENTION_NOTIFICATION_DAYS),
        "admin_log_days": data_retention_setting(DATA_RETENTION_ADMIN_LOG_DAYS_KEY, DEFAULT_DATA_RETENTION_ADMIN_LOG_DAYS),
        "webhook_days": data_retention_setting(DATA_RETENTION_WEBHOOK_DAYS_KEY, DEFAULT_DATA_RETENTION_WEBHOOK_DAYS),
        "evidence_days": dispute_evidence_retention_days(),
        "last_run": get_setting(DATA_RETENTION_LAST_RUN_KEY, ""),
        "last_result": get_setting(DATA_RETENTION_LAST_RESULT_KEY, ""),
    }


def set_data_retention_settings(notification_days, admin_log_days, webhook_days, evidence_days):
    settings = {
        "notification_days": normalize_data_retention_days(notification_days, "Read notification retention"),
        "admin_log_days": normalize_data_retention_days(admin_log_days, "Admin log retention"),
        "webhook_days": normalize_data_retention_days(webhook_days, "Webhook delivery retention"),
        "evidence_days": normalize_data_retention_days(evidence_days, "Resolved dispute evidence retention"),
    }
    with db() as conn:
        for key, value in (
            (DATA_RETENTION_NOTIFICATION_DAYS_KEY, settings["notification_days"]),
            (DATA_RETENTION_ADMIN_LOG_DAYS_KEY, settings["admin_log_days"]),
            (DATA_RETENTION_WEBHOOK_DAYS_KEY, settings["webhook_days"]),
            (DISPUTE_EVIDENCE_RETENTION_DAYS_KEY, settings["evidence_days"]),
        ):
            conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (key, str(value)))
    return data_retention_settings()


def data_retention_cutoff(days, reference_time=None):
    days = int(days or 0)
    if days <= 0:
        return ""
    reference_time = reference_time or datetime.now(timezone.utc)
    return (reference_time - timedelta(days=days)).replace(microsecond=0).isoformat()


def data_retention_eligible_counts(settings=None, reference_time=None):
    settings = settings or data_retention_settings()
    cutoffs = {
        key: data_retention_cutoff(settings[key], reference_time)
        for key in ("notification_days", "admin_log_days", "webhook_days", "evidence_days")
    }
    counts = {
        "notifications": 0,
        "admin_logs": 0,
        "webhook_deliveries": 0,
        "dispute_evidence": 0,
    }
    if cutoffs["notification_days"]:
        counts["notifications"] = row(
            "SELECT COUNT(*) AS count FROM user_notifications WHERE is_read = 1 AND created_at < ?",
            (cutoffs["notification_days"],),
        )["count"]
    if cutoffs["admin_log_days"]:
        counts["admin_logs"] = row(
            "SELECT COUNT(*) AS count FROM admin_audit_log WHERE created_at < ?",
            (cutoffs["admin_log_days"],),
        )["count"]
    if cutoffs["webhook_days"]:
        counts["webhook_deliveries"] = row(
            """
            SELECT COUNT(*) AS count
            FROM webhook_deliveries
            WHERE status IN ('sent', 'failed')
                AND COALESCE(NULLIF(completed_at, ''), created_at) < ?
            """,
            (cutoffs["webhook_days"],),
        )["count"]
    if cutoffs["evidence_days"]:
        counts["dispute_evidence"] = row(
            """
            SELECT COUNT(*) AS count
            FROM trade_dispute_evidence
            WHERE dispute_id IN (
                SELECT id
                FROM trade_disputes
                WHERE status IN ('resolved', 'dismissed')
                    AND COALESCE(NULLIF(resolved_at, ''), NULLIF(updated_at, ''), created_at) < ?
            )
            """,
            (cutoffs["evidence_days"],),
        )["count"]
    counts["total"] = sum(counts.values())
    return counts


def data_retention_status(reference_time=None):
    settings = data_retention_settings()
    return {
        **settings,
        "eligible": data_retention_eligible_counts(settings, reference_time),
    }


def prune_data_retention_records(settings=None, reference_time=None):
    settings = settings or data_retention_settings()
    cutoffs = {
        key: data_retention_cutoff(settings[key], reference_time)
        for key in ("notification_days", "admin_log_days", "webhook_days", "evidence_days")
    }
    deleted = {
        "notifications": 0,
        "admin_logs": 0,
        "webhook_deliveries": 0,
        "dispute_evidence": 0,
    }
    timestamp = now_iso()
    with db() as conn:
        if cutoffs["notification_days"]:
            deleted["notifications"] = conn.execute(
                "DELETE FROM user_notifications WHERE is_read = 1 AND created_at < ?",
                (cutoffs["notification_days"],),
            ).rowcount
        if cutoffs["admin_log_days"]:
            deleted["admin_logs"] = conn.execute(
                "DELETE FROM admin_audit_log WHERE created_at < ?",
                (cutoffs["admin_log_days"],),
            ).rowcount
        if cutoffs["webhook_days"]:
            deleted["webhook_deliveries"] = conn.execute(
                """
                DELETE FROM webhook_deliveries
                WHERE status IN ('sent', 'failed')
                    AND COALESCE(NULLIF(completed_at, ''), created_at) < ?
                """,
                (cutoffs["webhook_days"],),
            ).rowcount
        if cutoffs["evidence_days"]:
            deleted["dispute_evidence"] = conn.execute(
                """
                DELETE FROM trade_dispute_evidence
                WHERE dispute_id IN (
                    SELECT id
                    FROM trade_disputes
                    WHERE status IN ('resolved', 'dismissed')
                        AND COALESCE(NULLIF(resolved_at, ''), NULLIF(updated_at, ''), created_at) < ?
                )
                """,
                (cutoffs["evidence_days"],),
            ).rowcount
        deleted["total"] = sum(deleted.values())
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (DATA_RETENTION_LAST_RUN_KEY, timestamp))
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            (DATA_RETENTION_LAST_RESULT_KEY, json.dumps(deleted, ensure_ascii=True, sort_keys=True)),
        )
    return {**deleted, "run_at": timestamp}


def maintenance_health_status(limit=6):
    db_size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    smtp_settings = smtp_email_settings()
    bulk_status_func = globals().get("scryfall_bulk_status")
    price_status_func = globals().get("scryfall_price_refresh_status")
    import_counts = status_count_rows("import_batches")
    scryfall_counts = status_count_rows("scryfall_enrichment_jobs")
    price_counts = status_count_rows("price_refresh_jobs")
    webhook_counts = status_count_rows("webhook_deliveries")
    email_counts = status_count_rows(
        "user_notifications",
        "COALESCE(NULLIF(email_status, ''), 'none')",
        "WHERE email_status != ''",
    )
    failed_notifications = rows(
        """
        SELECT user_notifications.*, users.display_name, users.username, users.email
        FROM user_notifications
        JOIN users ON users.id = user_notifications.user_id
        WHERE user_notifications.email_status = 'failed'
        ORDER BY user_notifications.created_at DESC, user_notifications.id DESC
        LIMIT ?
        """,
        (int(limit),),
    )
    pending_email = row(
        "SELECT COUNT(*) AS count FROM user_notifications WHERE email_status = 'pending'"
    )["count"]
    failed_email = row(
        "SELECT COUNT(*) AS count FROM user_notifications WHERE email_status = 'failed'"
    )["count"]
    health = {
        "database": {
            "path": str(DB_PATH),
            "size": db_size,
            "size_label": bytes_label(db_size),
            "counts": [
                ("Users", table_count("users")),
                ("Collection cards", table_count("collection_items")),
                ("Wanted cards", table_count("want_items")),
                ("Trades", table_count("trades")),
                ("Notifications", table_count("user_notifications")),
            ],
        },
        "retention": data_retention_status(),
        "backups": backup_status(limit=limit),
        "scryfall": {
            "bulk": bulk_status_func() if bulk_status_func else {},
            "prices": price_status_func() if price_status_func else {},
        },
        "jobs": [
            {
                "label": "Scryfall import jobs",
                "counts": scryfall_counts,
            },
            {
                "label": "Price refresh jobs",
                "counts": price_counts,
            },
            {
                "label": "Webhook deliveries",
                "counts": webhook_counts,
            },
            {
                "label": "Email notifications",
                "counts": email_counts,
            },
        ],
        "job_counts": {
            "imports": import_counts,
            "scryfall": scryfall_counts,
            "prices": price_counts,
            "webhooks": webhook_counts,
            "email": email_counts,
        },
        "email": {
            "configured": email_delivery_configured(),
            "host": smtp_settings["host"],
            "port": smtp_settings["port"],
            "username_set": bool(smtp_settings["username"]),
            "password_set": bool(smtp_settings["password"]),
            "from_address": smtp_settings["from_address"],
            "use_ssl": smtp_settings["use_ssl"],
            "use_starttls": smtp_settings["use_starttls"],
        },
        "notifications": {
            "pending_email_count": pending_email,
            "failed_email_count": failed_email,
            "recent_failed": failed_notifications,
        },
    }
    health["setup_warnings"] = maintenance_setup_warnings(health)
    return health


def maintenance_job_status_value(counts, *statuses):
    wanted = {str(status) for status in statuses}
    total = 0
    for item in counts or ():
        if str(item["status"]) in wanted:
            total += int(item["count"] or 0)
    return total


def maintenance_import_batch_rows(limit=12):
    return rows(
        """
        SELECT
            import_batches.*,
            users.username,
            users.display_name,
            users.email,
            card_groups.name AS group_name,
            card_groups.group_type,
            COUNT(import_batch_items.id) AS item_count
        FROM import_batches
        JOIN users ON users.id = import_batches.user_id
        LEFT JOIN card_groups ON card_groups.id = import_batches.group_id
        LEFT JOIN import_batch_items ON import_batch_items.batch_id = import_batches.id
        GROUP BY import_batches.id
        ORDER BY import_batches.updated_at DESC, import_batches.id DESC
        LIMIT ?
        """,
        (int(limit),),
    )


def maintenance_scryfall_job_rows(limit=12):
    return rows(
        """
        SELECT
            scryfall_enrichment_jobs.*,
            users.username,
            users.display_name,
            users.email,
            collection_items.card_name AS collection_card_name,
            collection_items.set_name AS item_set_name,
            collection_items.set_code AS item_set_code,
            collection_items.collector_number AS item_collector_number
        FROM scryfall_enrichment_jobs
        JOIN users ON users.id = scryfall_enrichment_jobs.user_id
        LEFT JOIN collection_items ON collection_items.id = scryfall_enrichment_jobs.collection_item_id
        WHERE scryfall_enrichment_jobs.status IN ('pending', 'processing', 'failed', 'not_found')
            OR scryfall_enrichment_jobs.last_error != ''
        ORDER BY
            CASE scryfall_enrichment_jobs.status
                WHEN 'failed' THEN 0
                WHEN 'not_found' THEN 1
                WHEN 'processing' THEN 2
                WHEN 'pending' THEN 3
                ELSE 4
            END,
            scryfall_enrichment_jobs.updated_at DESC,
            scryfall_enrichment_jobs.id DESC
        LIMIT ?
        """,
        (int(limit),),
    )


def maintenance_price_job_rows(limit=12):
    return rows(
        """
        SELECT
            price_refresh_jobs.*,
            users.username,
            users.display_name,
            users.email,
            collection_items.card_name,
            collection_items.set_name,
            collection_items.set_code,
            collection_items.collector_number
        FROM price_refresh_jobs
        JOIN users ON users.id = price_refresh_jobs.user_id
        LEFT JOIN collection_items ON collection_items.id = price_refresh_jobs.collection_item_id
        WHERE price_refresh_jobs.status IN ('pending', 'processing', 'failed', 'not_found', 'disabled')
            OR price_refresh_jobs.last_error != ''
        ORDER BY
            CASE price_refresh_jobs.status
                WHEN 'failed' THEN 0
                WHEN 'not_found' THEN 1
                WHEN 'disabled' THEN 2
                WHEN 'processing' THEN 3
                WHEN 'pending' THEN 4
                ELSE 5
            END,
            price_refresh_jobs.updated_at DESC,
            price_refresh_jobs.id DESC
        LIMIT ?
        """,
        (int(limit),),
    )


def maintenance_failed_notification_rows(limit=12):
    return rows(
        """
        SELECT user_notifications.*, users.username, users.display_name, users.email
        FROM user_notifications
        JOIN users ON users.id = user_notifications.user_id
        WHERE user_notifications.email_status = 'failed'
        ORDER BY user_notifications.created_at DESC, user_notifications.id DESC
        LIMIT ?
        """,
        (int(limit),),
    )


def maintenance_job_dashboard(limit=12):
    import_counts = status_count_rows("import_batches")
    scryfall_counts = status_count_rows("scryfall_enrichment_jobs")
    price_counts = status_count_rows("price_refresh_jobs")
    email_counts = status_count_rows(
        "user_notifications",
        "COALESCE(NULLIF(email_status, ''), 'none')",
        "WHERE email_status != ''",
    )
    price_status_func = globals().get("scryfall_price_refresh_status")
    return {
        "import_counts": import_counts,
        "scryfall_counts": scryfall_counts,
        "price_counts": price_counts,
        "email_counts": email_counts,
        "scryfall_price_refresh": price_status_func() if price_status_func else {},
        "imports": maintenance_import_batch_rows(limit),
        "scryfall_jobs": maintenance_scryfall_job_rows(limit),
        "price_jobs": maintenance_price_job_rows(limit),
        "failed_notifications": maintenance_failed_notification_rows(limit),
        "metrics": {
            "recent_imports": table_count("import_batches"),
            "scryfall_attention": maintenance_job_status_value(scryfall_counts, "failed", "not_found", "processing"),
            "price_attention": maintenance_job_status_value(price_counts, "failed", "not_found", "processing", "disabled"),
            "failed_emails": maintenance_job_status_value(email_counts, "failed"),
        },
    }


def admin_onboarding_checklist():
    backups = backup_status(limit=1)
    bulk_status_func = globals().get("scryfall_bulk_status")
    bulk_status = bulk_status_func() if bulk_status_func else {}
    email_configured = email_delivery_configured()
    backup_count = len(backups.get("archives", []))
    bulk_card_count = int(bulk_status.get("card_count", 0) or 0)
    bulk_state = str(bulk_status.get("status", "idle") or "idle")
    bulk_error = str(bulk_status.get("error", "") or "")
    invite_count = row("SELECT COUNT(*) AS count FROM registration_invites")["count"]
    import_count = row(
        """
        SELECT COUNT(*) AS count
        FROM import_batches
        WHERE import_type = 'collection_csv'
            AND status IN ('applied', 'undone')
        """
    )["count"]
    bulk_complete = bulk_card_count > 0 and bulk_state != "error" and not bulk_error
    bulk_running = bulk_state == "running"
    bulk_status_label = (
        "Synced"
        if bulk_complete
        else "Running"
        if bulk_running
        else "Error"
        if bulk_state == "error" or bulk_error
        else "Not synced"
    )
    items = [
        {
            "key": "smtp",
            "title": "Configure SMTP",
            "detail": "SMTP host is configured for invites and email notifications."
            if email_configured
            else "Set SMTP values in binderbridge.ini or environment variables when email delivery is ready.",
            "complete": email_configured,
            "status_label": "Complete" if email_configured else "Not configured",
            "action_label": "View email health",
            "action_url": "/admin/health",
            "action_method": "get",
        },
        {
            "key": "backup",
            "title": "Create first backup",
            "detail": f"{backup_count} backup archive{'s' if backup_count != 1 else ''} available."
            if backup_count
            else "Create a safety backup before inviting users or importing large collections.",
            "complete": backup_count > 0,
            "status_label": "Complete" if backup_count else "Not started",
            "action_label": "Download backup",
            "action_url": "/admin/backups/create",
            "action_method": "post",
        },
        {
            "key": "scryfall_bulk",
            "title": "Sync Scryfall bulk data",
            "detail": f"{bulk_card_count} cached cards. Updated {str(bulk_status.get('updated_at', '') or 'not yet')[:16].replace('T', ' ')}."
            if bulk_complete
            else bulk_error
            if bulk_error
            else "Start the local Scryfall cache so imports and finish checks have local card data.",
            "complete": bulk_complete,
            "status_label": bulk_status_label,
            "action_label": "Start sync" if not bulk_running else "Sync running",
            "action_url": "/admin/health/scryfall/sync",
            "action_method": "post",
            "disabled": bulk_running,
        },
        {
            "key": "invites",
            "title": "Invite users",
            "detail": f"{invite_count} invite{'s' if invite_count != 1 else ''} created."
            if invite_count
            else "Create invite links for the first members of this BinderBridge site.",
            "complete": invite_count > 0,
            "status_label": "Complete" if invite_count else "Not started",
            "action_label": "Create invite" if not invite_count else "Review invites",
            "action_url": "/admin#admin-invites",
            "action_method": "get",
        },
        {
            "key": "collection_import",
            "title": "Add first collection import",
            "detail": f"{import_count} applied collection import{'s' if import_count != 1 else ''} recorded."
            if import_count
            else "Import a CSV from ManaBox, Archidekt, or another collection tool.",
            "complete": import_count > 0,
            "status_label": "Complete" if import_count else "Not started",
            "action_label": "Open import",
            "action_url": "/import",
            "action_method": "get",
        },
    ]
    complete_count = sum(1 for item in items if item["complete"])
    return {
        "items": items,
        "complete_count": complete_count,
        "total": len(items),
        "is_complete": complete_count == len(items),
    }


def maintenance_count_for_statuses(counts, *statuses):
    wanted = {str(status) for status in statuses}
    total = 0
    for item in counts or ():
        if str(item["status"]) in wanted:
            total += int(item["count"] or 0)
    return total


def maintenance_setup_warnings(health):
    warnings = []

    def add(severity, title, detail, action_label="", action_url=""):
        warnings.append({
            "severity": severity,
            "title": title,
            "detail": detail,
            "action_label": action_label,
            "action_url": action_url,
        })

    backups = health.get("backups", {})
    automatic = backups.get("automatic", {})
    backup_integrity = backups.get("integrity", {})
    scryfall = health.get("scryfall", {})
    scryfall_bulk = scryfall.get("bulk", {})
    scryfall_prices = scryfall.get("prices", {})
    notifications = health.get("notifications", {})
    counts = health.get("job_counts", {})

    if not backups.get("archives"):
        add("warning", "No backups found", "Create a backup before making major site changes.", "Open backup tools", "/admin")
    if not automatic.get("enabled"):
        add("warning", "Automatic backups are paused", "Scheduled backups are not currently running.", "Open backup tools", "/admin")
    elif not automatic.get("last_success"):
        add("info", "Automatic backups have not completed yet", "The scheduler is enabled, but no successful run has been recorded.", "Run backup now", "/admin")
    if automatic.get("last_error"):
        add("error", "Last automatic backup failed", automatic["last_error"], "Open backup tools", "/admin")
    if backup_integrity.get("last_status") == "failed":
        add("error", "Backup integrity check found a problem", backup_integrity.get("last_message", ""), "Check backups", "/admin/health")

    recoverable_jobs = maintenance_count_for_statuses(counts.get("scryfall"), *JOB_DASHBOARD_RETRY_STATUSES)
    recoverable_jobs += maintenance_count_for_statuses(counts.get("prices"), *JOB_DASHBOARD_RETRY_STATUSES)
    if recoverable_jobs:
        add("warning", "Recoverable jobs need attention", f"{recoverable_jobs} background job(s) can be retried.", "Open jobs", "/admin/jobs")
    webhook_failures = maintenance_count_for_statuses(counts.get("webhooks"), "failed")
    if webhook_failures:
        add("warning", "Webhook deliveries failed", f"{webhook_failures} webhook delivery attempt(s) failed.", "Open API access", "/account/profile")
    failed_emails = int(notifications.get("failed_email_count", 0) or 0)
    if failed_emails:
        add("warning", "Notification emails failed", f"{failed_emails} unread notification email(s) failed delivery.", "Replay emails", "/admin/health")

    if not health.get("email", {}).get("configured"):
        add("info", "SMTP is not configured", "Admins can still create manual invite links, but email delivery is disabled.", "Open admin panel", "/admin")
    if scryfall_bulk.get("status") in ("error", "failed") or scryfall_bulk.get("error"):
        add("error", "Scryfall bulk data needs attention", scryfall_bulk.get("error") or "The local Scryfall bulk cache is not healthy.", "Open jobs", "/admin/jobs")
    if scryfall_prices.get("status") in ("error", "failed") or scryfall_prices.get("error"):
        add("error", "Scryfall price refresh needs attention", scryfall_prices.get("error") or "The automatic price refresh is not healthy.", "Open jobs", "/admin/jobs")
    if not config_str("BINDERBRIDGE_PUBLIC_BASE_URL", "PUBLIC_BASE_URL", default="", section="server", key="public_base_url").strip():
        add("info", "Public base URL is not set", "Email and webhook links may be relative until a public base URL is configured.", "Open admin panel", "/admin")

    return warnings


def retry_recoverable_maintenance_jobs():
    timestamp = now_iso()
    active_providers = tuple(globals().get("PRICE_PROVIDER_KEYS", ()))
    with db() as conn:
        scryfall_cursor = conn.execute(
            f"""
            UPDATE scryfall_enrichment_jobs
            SET status = 'pending',
                attempts = 0,
                last_error = '',
                available_at = '',
                completion_notified = 0,
                updated_at = ?
            WHERE status IN ({','.join('?' for _ in JOB_DASHBOARD_RETRY_STATUSES)})
            """,
            (timestamp, *JOB_DASHBOARD_RETRY_STATUSES),
        )
        price_count = 0
        if active_providers:
            price_cursor = conn.execute(
                f"""
                UPDATE price_refresh_jobs
                SET status = 'pending',
                    attempts = 0,
                    last_error = '',
                    available_at = '',
                    updated_at = ?
                WHERE provider IN ({','.join('?' for _ in active_providers)})
                    AND status IN ({','.join('?' for _ in JOB_DASHBOARD_RETRY_STATUSES)})
                """,
                (timestamp, *active_providers, *JOB_DASHBOARD_RETRY_STATUSES),
            )
            price_count = price_cursor.rowcount
    return {
        "scryfall_jobs": scryfall_cursor.rowcount,
        "price_jobs": price_count,
        "total": scryfall_cursor.rowcount + price_count,
    }


def replay_failed_notification_emails(limit=50):
    if not email_delivery_configured():
        raise ValueError("SMTP is not configured, so failed notification emails cannot be replayed yet.")
    clean_limit = max(1, min(500, int(limit or 50)))
    with db() as conn:
        failed_rows = conn.execute(
            """
            SELECT id
            FROM user_notifications
            WHERE email_status = 'failed'
                AND is_read = 0
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (clean_limit,),
        ).fetchall()
        notification_ids = [item["id"] for item in failed_rows]
        if notification_ids:
            conn.execute(
                f"""
                UPDATE user_notifications
                SET email_status = 'pending',
                    email_sent_at = '',
                    email_error = ''
                WHERE id IN ({','.join('?' for _ in notification_ids)})
                """,
                notification_ids,
            )
    result = send_pending_trade_notification_emails(limit=max(clean_limit, len(notification_ids)))
    result["queued"] = len(notification_ids)
    return result


def retry_scryfall_enrichment_job(job_id):
    try:
        clean_id = int(job_id)
    except (TypeError, ValueError):
        raise ValueError("Choose a Scryfall enrichment job to retry.")
    with db() as conn:
        job = conn.execute("SELECT * FROM scryfall_enrichment_jobs WHERE id = ?", (clean_id,)).fetchone()
        if not job:
            raise ValueError("That Scryfall enrichment job was not found.")
        if job["status"] not in JOB_DASHBOARD_RETRY_STATUSES:
            raise ValueError("Only failed, not-found, or interrupted Scryfall jobs can be retried.")
        conn.execute(
            """
            UPDATE scryfall_enrichment_jobs
            SET status = 'pending',
                attempts = 0,
                last_error = '',
                available_at = '',
                completion_notified = 0,
                updated_at = ?
            WHERE id = ?
            """,
            (now_iso(), clean_id),
        )
        return dict(job)


def retry_price_refresh_job(job_id):
    try:
        clean_id = int(job_id)
    except (TypeError, ValueError):
        raise ValueError("Choose a price refresh job to retry.")
    active_providers = tuple(globals().get("PRICE_PROVIDER_KEYS", ()))
    with db() as conn:
        job = conn.execute("SELECT * FROM price_refresh_jobs WHERE id = ?", (clean_id,)).fetchone()
        if not job:
            raise ValueError("That price refresh job was not found.")
        if job["provider"] not in active_providers:
            raise ValueError("That queued price provider is no longer active. Use the Scryfall price retry action instead.")
        if job["status"] not in JOB_DASHBOARD_RETRY_STATUSES:
            raise ValueError("Only failed, not-found, or interrupted price jobs can be retried.")
        conn.execute(
            """
            UPDATE price_refresh_jobs
            SET status = 'pending',
                attempts = 0,
                last_error = '',
                available_at = '',
                updated_at = ?
            WHERE id = ?
            """,
            (now_iso(), clean_id),
        )
        return dict(job)


def retry_failed_notification_email(notification_id):
    try:
        clean_id = int(notification_id)
    except (TypeError, ValueError):
        raise ValueError("Choose a failed notification email to retry.")
    with db() as conn:
        notification = conn.execute("SELECT * FROM user_notifications WHERE id = ?", (clean_id,)).fetchone()
        if not notification:
            raise ValueError("That notification was not found.")
        if notification["email_status"] != "failed":
            raise ValueError("Only failed notification emails can be retried.")
        conn.execute(
            """
            UPDATE user_notifications
            SET email_status = 'pending',
                email_sent_at = '',
                email_error = ''
            WHERE id = ?
            """,
            (clean_id,),
        )
        return dict(notification)


def retry_scryfall_price_refresh_async():
    global _job_dashboard_price_retry_running
    with _job_dashboard_price_retry_lock:
        if _job_dashboard_price_retry_running:
            return {"started": False, "message": "Scryfall price refresh is already running."}
        _job_dashboard_price_retry_running = True
    status_key = globals().get("SCRYFALL_PRICE_REFRESH_STATUS_KEY")
    if status_key:
        set_setting(status_key, "queued")

    def worker():
        global _job_dashboard_price_retry_running
        try:
            refresh_all_scryfall_prices(sync_bulk=True, notify_users=True)
        except Exception:
            pass
        finally:
            with _job_dashboard_price_retry_lock:
                _job_dashboard_price_retry_running = False

    thread = threading.Thread(target=worker, name="admin-scryfall-price-retry", daemon=True)
    thread.start()
    return {"started": True, "message": "Scryfall price refresh retry started."}


def admin_undo_import_batch(batch_id):
    try:
        clean_id = int(batch_id)
    except (TypeError, ValueError):
        raise ValueError("Choose an import batch to undo.")
    batch = row("SELECT * FROM import_batches WHERE id = ?", (clean_id,))
    if not batch:
        raise ValueError("That import batch was not found.")
    undo_func = globals().get("undo_import_batch")
    if not undo_func:
        raise ValueError("Import undo tools are not available.")
    return undo_func(batch["user_id"], batch["id"])


def create_backup_archive(created_by_user_id=None, prefix="binderbridge-backup"):
    if not DB_PATH.exists():
        raise ValueError("Database file does not exist yet.")
    directory = backup_directory()
    archive_path = directory / backup_archive_name(prefix)
    temp_db_path = directory / f".{archive_path.stem}.sqlite3.tmp"
    metadata = {
        "app": APP_NAME,
        "created_at": now_iso(),
        "created_by_user_id": created_by_user_id or "",
        "database_name": BACKUP_DATABASE_NAME,
    }
    source = None
    target = None
    try:
        source = sqlite3.connect(DB_PATH)
        target = sqlite3.connect(temp_db_path)
        source.backup(target)
        target.close()
        source.close()
        target = None
        source = None
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(temp_db_path, BACKUP_DATABASE_NAME)
            archive.writestr("metadata.json", json.dumps(metadata, indent=2))
            archive.writestr(
                "README.txt",
                "BinderBridge backup archive. Restore from the Admin maintenance panel.\n",
            )
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()
        if temp_db_path.exists():
            temp_db_path.unlink()
    return archive_path


def parse_backup_bool(value):
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def parse_backup_int(value, label, minimum=0, maximum=100000):
    try:
        number = int(str(value or "").strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a whole number.") from exc
    if number < minimum:
        raise ValueError(f"{label} must be at least {minimum}.")
    if number > maximum:
        raise ValueError(f"{label} is too high.")
    return number


def automatic_backup_settings():
    return {
        "enabled": parse_backup_bool(get_setting(AUTOMATIC_BACKUP_ENABLED_KEY, "1" if DEFAULT_AUTOMATIC_BACKUP_ENABLED else "0")),
        "interval_hours": max(1, parse_backup_int(get_setting(AUTOMATIC_BACKUP_INTERVAL_HOURS_KEY, DEFAULT_AUTOMATIC_BACKUP_INTERVAL_HOURS), "Backup interval", minimum=1, maximum=8760)),
        "retention_count": max(1, parse_backup_int(get_setting(AUTOMATIC_BACKUP_RETENTION_COUNT_KEY, DEFAULT_AUTOMATIC_BACKUP_RETENTION_COUNT), "Backup retention count", minimum=1, maximum=10000)),
        "retention_days": parse_backup_int(get_setting(AUTOMATIC_BACKUP_RETENTION_DAYS_KEY, DEFAULT_AUTOMATIC_BACKUP_RETENTION_DAYS), "Backup retention days", minimum=0, maximum=3650),
        "last_run": get_setting(AUTOMATIC_BACKUP_LAST_RUN_KEY, ""),
        "last_success": get_setting(AUTOMATIC_BACKUP_LAST_SUCCESS_KEY, ""),
        "last_error": get_setting(AUTOMATIC_BACKUP_LAST_ERROR_KEY, ""),
    }


def set_automatic_backup_settings(enabled, interval_hours, retention_count, retention_days):
    interval_hours = parse_backup_int(interval_hours, "Backup interval", minimum=1, maximum=8760)
    retention_count = parse_backup_int(retention_count, "Backup retention count", minimum=1, maximum=10000)
    retention_days = parse_backup_int(retention_days, "Backup retention days", minimum=0, maximum=3650)
    set_setting(AUTOMATIC_BACKUP_ENABLED_KEY, "1" if enabled else "0")
    set_setting(AUTOMATIC_BACKUP_INTERVAL_HOURS_KEY, interval_hours)
    set_setting(AUTOMATIC_BACKUP_RETENTION_COUNT_KEY, retention_count)
    set_setting(AUTOMATIC_BACKUP_RETENTION_DAYS_KEY, retention_days)
    return automatic_backup_settings()


def parse_backup_timestamp(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def automatic_backup_next_run(settings=None):
    settings = settings or automatic_backup_settings()
    if not settings["enabled"]:
        return ""
    last_run = parse_backup_timestamp(settings.get("last_run"))
    if not last_run:
        return "due"
    next_run = last_run + timedelta(hours=int(settings["interval_hours"]))
    return next_run.replace(microsecond=0).isoformat()


def automatic_backup_due(reference_time=None):
    settings = automatic_backup_settings()
    if not settings["enabled"] or not DB_PATH.exists():
        return False
    last_run = parse_backup_timestamp(settings.get("last_run"))
    if not last_run:
        return True
    reference_time = reference_time or datetime.now(timezone.utc)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    return reference_time.astimezone(timezone.utc) >= last_run + timedelta(hours=int(settings["interval_hours"]))


def automatic_backup_status():
    settings = automatic_backup_settings()
    archives = backup_archive_paths(AUTOMATIC_BACKUP_PREFIX)
    settings.update({
        "next_run": automatic_backup_next_run(settings),
        "automatic_archive_count": len(archives),
        "automatic_archives": [backup_archive_info(path) for path in archives[:6]],
    })
    return settings


def prune_backup_archives(retention_count=None, retention_days=None, prefix=AUTOMATIC_BACKUP_PREFIX):
    retention_count = DEFAULT_AUTOMATIC_BACKUP_RETENTION_COUNT if retention_count is None else int(retention_count)
    retention_days = DEFAULT_AUTOMATIC_BACKUP_RETENTION_DAYS if retention_days is None else int(retention_days)
    retention_count = max(1, retention_count)
    retention_days = max(0, retention_days)
    archives = backup_archive_paths(prefix)
    keep = set(archives[:retention_count])
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days) if retention_days else None
    deleted = []
    failed = []
    for index, archive_path in enumerate(archives):
        created_at = datetime.fromtimestamp(archive_path.stat().st_mtime, timezone.utc)
        beyond_count = index >= retention_count
        older_than_cutoff = bool(cutoff and created_at < cutoff)
        if archive_path in keep:
            continue
        if not (beyond_count or older_than_cutoff):
            continue
        try:
            archive_path.unlink()
            deleted.append(archive_path.name)
        except OSError as exc:
            failed.append(f"{archive_path.name}: {exc}")
    return {"deleted": deleted, "failed": failed}


def backup_setting_int(key, default=0):
    try:
        return int(get_setting(key, default) or default)
    except (TypeError, ValueError):
        return int(default)


def backup_integrity_status():
    return {
        "last_check": get_setting(BACKUP_INTEGRITY_LAST_CHECK_KEY, ""),
        "last_status": get_setting(BACKUP_INTEGRITY_LAST_STATUS_KEY, ""),
        "last_message": get_setting(BACKUP_INTEGRITY_LAST_MESSAGE_KEY, ""),
        "checked": backup_setting_int(BACKUP_INTEGRITY_LAST_CHECKED_KEY, 0),
        "failed": backup_setting_int(BACKUP_INTEGRITY_LAST_FAILED_KEY, 0),
    }


def backup_archive_database_candidate(archive):
    candidates = [
        info
        for info in archive.infolist()
        if not info.is_dir()
        and Path(info.filename).name.lower() in (BACKUP_DATABASE_NAME, "binderbridge.db")
    ]
    if not candidates:
        candidates = [
            info
            for info in archive.infolist()
            if not info.is_dir()
            and Path(info.filename).suffix.lower() in (".sqlite3", ".sqlite", ".db")
        ]
    return candidates[0] if candidates else None


def verify_backup_archive(path):
    archive_path = Path(path)
    info = backup_archive_info(archive_path)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            bad_member = archive.testzip()
            if bad_member:
                raise ValueError(f"Archive member failed checksum: {bad_member}")
            candidate = backup_archive_database_candidate(archive)
            if not candidate:
                raise ValueError("No SQLite database was found inside the archive.")
            if candidate.file_size > BACKUP_MAX_BYTES:
                raise ValueError("Backup database is too large for the built-in integrity check.")
            with tempfile.TemporaryDirectory(dir=DATA_DIR) as temp_dir:
                sqlite_path = Path(temp_dir) / BACKUP_DATABASE_NAME
                with archive.open(candidate) as source, sqlite_path.open("wb") as target:
                    shutil.copyfileobj(source, target)
                validate_restore_database(sqlite_path)
    except (OSError, sqlite3.DatabaseError, zipfile.BadZipFile, ValueError) as exc:
        info.update({
            "status": "failed",
            "message": str(exc) or "Backup archive failed integrity checks.",
        })
        return info
    info.update({
        "status": "ok",
        "message": "Readable BinderBridge SQLite database found.",
    })
    return info


def verify_backup_archives(limit=None, prefix=None):
    archive_paths = backup_archive_paths(prefix=prefix)
    if limit:
        archive_paths = archive_paths[: max(1, int(limit))]
    results = [verify_backup_archive(path) for path in archive_paths]
    failed = [item for item in results if item["status"] != "ok"]
    status = "ok" if results and not failed else "failed" if failed else "warning"
    if not results:
        message = "No backup archives found."
    elif failed:
        message = f"{len(failed)} of {len(results)} backup archive(s) failed integrity checks."
    else:
        message = f"All {len(results)} backup archive(s) passed integrity checks."
    return {
        "status": status,
        "message": message,
        "checked": len(results),
        "failed": len(failed),
        "archives": results,
    }


def run_backup_integrity_check(limit=None):
    result = verify_backup_archives(limit=limit)
    set_setting(BACKUP_INTEGRITY_LAST_CHECK_KEY, now_iso())
    set_setting(BACKUP_INTEGRITY_LAST_STATUS_KEY, result["status"])
    set_setting(BACKUP_INTEGRITY_LAST_MESSAGE_KEY, result["message"])
    set_setting(BACKUP_INTEGRITY_LAST_CHECKED_KEY, result["checked"])
    set_setting(BACKUP_INTEGRITY_LAST_FAILED_KEY, result["failed"])
    return result


def notify_admins_backup_failure(message):
    admins = rows("SELECT id FROM users WHERE is_admin = 1 AND is_banned = 0")
    for admin in admins:
        create_notification(
            admin["id"],
            "backup_status",
            "Automatic backup failed",
            sanitize_text_input(message, max_length=800).strip(),
            "/admin",
        )


def run_automatic_backup_once(force=False):
    settings = automatic_backup_settings()
    if not force and not automatic_backup_due():
        return {"ran": False, "reason": "not_due", "settings": settings}
    timestamp = now_iso()
    set_setting(AUTOMATIC_BACKUP_LAST_RUN_KEY, timestamp)
    try:
        archive_path = create_backup_archive(prefix=AUTOMATIC_BACKUP_PREFIX)
        pruned = prune_backup_archives(settings["retention_count"], settings["retention_days"], prefix=AUTOMATIC_BACKUP_PREFIX)
    except Exception as exc:
        message = str(exc)
        set_setting(AUTOMATIC_BACKUP_LAST_ERROR_KEY, message)
        try:
            notify_admins_backup_failure(message)
        except Exception:
            pass
        return {"ran": True, "success": False, "error": message, "settings": settings}
    set_setting(AUTOMATIC_BACKUP_LAST_SUCCESS_KEY, timestamp)
    set_setting(AUTOMATIC_BACKUP_LAST_ERROR_KEY, "")
    return {
        "ran": True,
        "success": True,
        "archive": archive_path.name,
        "path": str(archive_path),
        "pruned": pruned,
        "settings": settings,
    }


def automatic_backup_worker_loop():
    while True:
        try:
            if automatic_backup_due():
                run_automatic_backup_once()
        except Exception:
            pass
        time.sleep(AUTOMATIC_BACKUP_WORKER_CHECK_SECONDS)


def start_automatic_backup_worker():
    global _automatic_backup_worker_started
    with _automatic_backup_worker_lock:
        if _automatic_backup_worker_started:
            return False
        thread = threading.Thread(target=automatic_backup_worker_loop, name="automatic-backup", daemon=True)
        thread.start()
        _automatic_backup_worker_started = True
        return True


def validate_restore_database(sqlite_path):
    conn = None
    try:
        conn = sqlite3.connect(sqlite_path)
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError("The uploaded database failed SQLite integrity checks.")
        required = {
            row["name"] if isinstance(row, sqlite3.Row) else row[0]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name IN ('users', 'collection_items', 'want_items')
                """
            ).fetchall()
        }
    except sqlite3.DatabaseError as exc:
        raise ValueError("The uploaded file is not a readable SQLite database.") from exc
    finally:
        if conn is not None:
            conn.close()
    missing = {"users", "collection_items", "want_items"} - required
    if missing:
        raise ValueError("The uploaded database is not a BinderBridge backup.")


def restore_sqlite_from_upload(upload, temp_dir):
    if not upload or not upload.get("content"):
        raise ValueError("Choose a BinderBridge backup file to restore.")
    content = upload["content"]
    if len(content) > BACKUP_MAX_BYTES:
        raise ValueError("Backup file is too large for the built-in restore tool.")
    filename = str(upload.get("filename") or "").lower()
    temp_dir = Path(temp_dir)
    upload_path = temp_dir / "uploaded-backup"
    upload_path.write_bytes(content)
    sqlite_path = temp_dir / "restore.sqlite3"
    is_zip = filename.endswith(".zip") or content[:4] == b"PK\x03\x04"
    if is_zip:
        try:
            with zipfile.ZipFile(upload_path) as archive:
                candidates = [
                    info
                    for info in archive.infolist()
                    if not info.is_dir()
                    and Path(info.filename).name.lower() in (BACKUP_DATABASE_NAME, "binderbridge.db")
                ]
                if not candidates:
                    candidates = [
                        info
                        for info in archive.infolist()
                        if not info.is_dir() and Path(info.filename).suffix.lower() in (".sqlite3", ".sqlite", ".db")
                    ]
                if not candidates:
                    raise ValueError("Backup archive did not contain a SQLite database.")
                candidate = candidates[0]
                if candidate.file_size > BACKUP_MAX_BYTES:
                    raise ValueError("Backup database is too large for the built-in restore tool.")
                with archive.open(candidate) as source, sqlite_path.open("wb") as target:
                    shutil.copyfileobj(source, target)
        except zipfile.BadZipFile as exc:
            raise ValueError("Backup archive could not be opened.") from exc
    else:
        sqlite_path.write_bytes(content)
    validate_restore_database(sqlite_path)
    return sqlite_path


def restore_backup_upload(upload, admin_user_id=None):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=DATA_DIR) as temp_dir:
        sqlite_path = restore_sqlite_from_upload(upload, temp_dir)
        pre_restore_backup = create_backup_archive(admin_user_id, prefix="binderbridge-pre-restore")
        source = None
        target = None
        try:
            source = sqlite3.connect(sqlite_path)
            target = sqlite3.connect(DB_PATH)
            source.backup(target)
        finally:
            if target is not None:
                target.close()
            if source is not None:
                source.close()
    init_db()
    log_admin_action(
        admin_user_id,
        "backup_restored",
        None,
        "backup",
        pre_restore_backup.name,
        f"Database restored. Pre-restore safety backup saved as {pre_restore_backup.name}.",
    )
    return {
        "pre_restore_backup": str(pre_restore_backup),
        "pre_restore_backup_name": pre_restore_backup.name,
        "restored_at": now_iso(),
    }


__all__ = [
    "BACKUP_DIR_NAME",
    "BACKUP_DATABASE_NAME",
    "BACKUP_MAX_BYTES",
    "AUTOMATIC_BACKUP_PREFIX",
    "AUTOMATIC_BACKUP_ENABLED_KEY",
    "AUTOMATIC_BACKUP_INTERVAL_HOURS_KEY",
    "AUTOMATIC_BACKUP_RETENTION_COUNT_KEY",
    "AUTOMATIC_BACKUP_RETENTION_DAYS_KEY",
    "AUTOMATIC_BACKUP_LAST_RUN_KEY",
    "AUTOMATIC_BACKUP_LAST_SUCCESS_KEY",
    "AUTOMATIC_BACKUP_LAST_ERROR_KEY",
    "BACKUP_INTEGRITY_LAST_CHECK_KEY",
    "BACKUP_INTEGRITY_LAST_STATUS_KEY",
    "BACKUP_INTEGRITY_LAST_MESSAGE_KEY",
    "BACKUP_INTEGRITY_LAST_CHECKED_KEY",
    "BACKUP_INTEGRITY_LAST_FAILED_KEY",
    "DATA_RETENTION_NOTIFICATION_DAYS_KEY",
    "DATA_RETENTION_ADMIN_LOG_DAYS_KEY",
    "DATA_RETENTION_WEBHOOK_DAYS_KEY",
    "DATA_RETENTION_LAST_RUN_KEY",
    "DATA_RETENTION_LAST_RESULT_KEY",
    "DEFAULT_AUTOMATIC_BACKUP_ENABLED",
    "DEFAULT_AUTOMATIC_BACKUP_INTERVAL_HOURS",
    "DEFAULT_AUTOMATIC_BACKUP_RETENTION_COUNT",
    "DEFAULT_AUTOMATIC_BACKUP_RETENTION_DAYS",
    "DEFAULT_DATA_RETENTION_NOTIFICATION_DAYS",
    "DEFAULT_DATA_RETENTION_ADMIN_LOG_DAYS",
    "DEFAULT_DATA_RETENTION_WEBHOOK_DAYS",
    "AUTOMATIC_BACKUP_WORKER_CHECK_SECONDS",
    "JOB_DASHBOARD_RETRY_STATUSES",
    "backup_directory",
    "bytes_label",
    "backup_archive_paths",
    "backup_status",
    "status_count_rows",
    "table_count",
    "normalize_data_retention_days",
    "data_retention_setting",
    "data_retention_settings",
    "set_data_retention_settings",
    "data_retention_cutoff",
    "data_retention_eligible_counts",
    "data_retention_status",
    "prune_data_retention_records",
    "maintenance_health_status",
    "maintenance_job_status_value",
    "maintenance_import_batch_rows",
    "maintenance_scryfall_job_rows",
    "maintenance_price_job_rows",
    "maintenance_failed_notification_rows",
    "maintenance_job_dashboard",
    "admin_onboarding_checklist",
    "maintenance_count_for_statuses",
    "maintenance_setup_warnings",
    "retry_recoverable_maintenance_jobs",
    "replay_failed_notification_emails",
    "retry_scryfall_enrichment_job",
    "retry_price_refresh_job",
    "retry_failed_notification_email",
    "retry_scryfall_price_refresh_async",
    "admin_undo_import_batch",
    "create_backup_archive",
    "parse_backup_bool",
    "parse_backup_int",
    "automatic_backup_settings",
    "set_automatic_backup_settings",
    "parse_backup_timestamp",
    "automatic_backup_next_run",
    "automatic_backup_due",
    "automatic_backup_status",
    "prune_backup_archives",
    "backup_setting_int",
    "backup_integrity_status",
    "backup_archive_database_candidate",
    "verify_backup_archive",
    "verify_backup_archives",
    "run_backup_integrity_check",
    "notify_admins_backup_failure",
    "run_automatic_backup_once",
    "automatic_backup_worker_loop",
    "start_automatic_backup_worker",
    "validate_restore_database",
    "restore_sqlite_from_upload",
    "restore_backup_upload",
]
