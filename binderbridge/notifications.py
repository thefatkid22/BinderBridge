"""User notifications, email delivery, and Scryfall import completion notifications.

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
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from email.message import EmailMessage
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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

TRADE_EMAIL_NOTIFICATION_COLUMNS = {
    "trade_offer": "email_trade_offer_enabled",
    "trade_counter": "email_trade_counter_enabled",
    "trade_comment": "email_trade_comment_enabled",
    "trade_status": "email_trade_status_enabled",
}

NOTIFICATION_KIND_CATEGORIES = {
    "trade_offer": "trade_offer",
    "trade_comment": "trade_comment",
    "trade_counter": "trade_counter",
    "trade_status": "trade_status",
    "trade_reminder": "trade_status",
    "trade_feedback": "trade_status",
    "price_alert": "price_alert",
    "watchlist_alert": "watchlist_alert",
    "scryfall_import": "import_complete",
    "backup_status": "admin_notice",
    "admin_notice": "admin_notice",
    "trade_dispute": "admin_notice",
}

EMAIL_DIGEST_FREQUENCIES = ("immediate", "daily", "weekly")
EMAIL_DIGEST_FREQUENCY_LABELS = {
    "immediate": "Immediate",
    "daily": "Daily digest",
    "weekly": "Weekly digest",
}
EMAIL_DIGEST_WEEKDAY_LABELS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
NOTIFICATION_WORKER_INTERVAL_SECONDS = max(
    30.0,
    config_float(
        "BINDERBRIDGE_NOTIFICATION_WORKER_INTERVAL_SECONDS",
        default=60.0,
        section="notifications",
        key="worker_interval_seconds",
    ),
)

_notification_worker_lock = threading.Lock()
_notification_worker_started = False

NOTIFICATION_IN_APP_COLUMNS = {
    "trade_offer": "notify_trade_offer_enabled",
    "trade_comment": "notify_trade_comment_enabled",
    "trade_counter": "notify_trade_counter_enabled",
    "trade_status": "notify_trade_status_enabled",
    "price_alert": "price_alerts_enabled",
    "watchlist_alert": "watchlist_alerts_enabled",
    "import_complete": "notify_import_complete_enabled",
    "admin_notice": "notify_admin_notice_enabled",
}

NOTIFICATION_EMAIL_COLUMNS = {
    **TRADE_EMAIL_NOTIFICATION_COLUMNS,
    "price_alert": "email_price_alert_enabled",
    "import_complete": "email_import_complete_enabled",
    "admin_notice": "email_admin_notice_enabled",
}

def email_delivery_configured():
    return bool(config_str("BINDERBRIDGE_SMTP_HOST", "SMTP_HOST", default="", section="smtp", key="host"))


def normalize_email_digest_frequency(value):
    frequency = sanitize_text_input(value, max_length=20).strip().lower()
    if frequency not in EMAIL_DIGEST_FREQUENCIES:
        raise ValueError("Choose immediate, daily, or weekly email delivery.")
    return frequency


def normalize_notification_time(value, label="Notification time"):
    text = sanitize_text_input(value, max_length=5).strip()
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", text):
        raise ValueError(f"{label} must use a valid 24-hour time.")
    return text


def normalize_email_digest_weekday(value):
    try:
        weekday = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Choose a valid weekly digest day.") from exc
    if weekday < 0 or weekday > 6:
        raise ValueError("Choose a valid weekly digest day.")
    return weekday


def normalize_notification_timezone(value):
    name = sanitize_text_input(value or "UTC", max_length=80).strip() or "UTC"
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Enter a valid IANA timezone, such as America/Chicago or UTC.") from exc
    return name


def normalize_stale_trade_reminder_days(value):
    try:
        days = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Stale trade reminder days must be a whole number.") from exc
    if days < 0 or days > 90:
        raise ValueError("Stale trade reminder days must be between 0 and 90.")
    return days


def parse_notification_datetime(value):
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def notification_local_time(user, reference_time=None):
    reference_time = reference_time or datetime.now(timezone.utc)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    name = str(row_value(user, "notification_timezone", "UTC") or "UTC")
    try:
        zone = ZoneInfo(name)
    except ZoneInfoNotFoundError:
        zone = timezone.utc
    return reference_time.astimezone(zone)


def notification_time_minutes(value):
    hour, minute = normalize_notification_time(value).split(":")
    return int(hour) * 60 + int(minute)


def notification_quiet_hours_active(user, reference_time=None):
    if not bool(int(row_value(user, "quiet_hours_enabled", 0) or 0)):
        return False
    start = notification_time_minutes(row_value(user, "quiet_hours_start", "22:00") or "22:00")
    end = notification_time_minutes(row_value(user, "quiet_hours_end", "07:00") or "07:00")
    if start == end:
        return False
    local = notification_local_time(user, reference_time)
    current = local.hour * 60 + local.minute
    return start <= current < end if start < end else current >= start or current < end


def notification_digest_due(user, reference_time=None):
    frequency = str(row_value(user, "email_digest_frequency", "immediate") or "immediate")
    if frequency == "immediate":
        return True
    local = notification_local_time(user, reference_time)
    delivery_minutes = notification_time_minutes(row_value(user, "email_digest_time", "09:00") or "09:00")
    delivery_hour, delivery_minute = divmod(delivery_minutes, 60)
    last_run = parse_notification_datetime(row_value(user, "last_notification_digest_at", ""))
    last_local = notification_local_time(user, last_run) if last_run else None
    if frequency == "daily":
        scheduled = local.replace(hour=delivery_hour, minute=delivery_minute, second=0, microsecond=0)
        return local >= scheduled and (not last_local or last_local < scheduled)
    weekday = int(row_value(user, "email_digest_weekday", 0) or 0)
    scheduled_date = (local - timedelta(days=local.weekday())).date() + timedelta(days=weekday)
    scheduled = datetime(
        scheduled_date.year,
        scheduled_date.month,
        scheduled_date.day,
        delivery_hour,
        delivery_minute,
        tzinfo=local.tzinfo,
    )
    return local >= scheduled and (not last_local or last_local < scheduled)

def smtp_email_settings():
    use_ssl = config_bool("BINDERBRIDGE_SMTP_SSL", "SMTP_SSL", default=False, section="smtp", key="ssl")
    return {
        "host": config_str("BINDERBRIDGE_SMTP_HOST", "SMTP_HOST", default="", section="smtp", key="host"),
        "port": config_int("BINDERBRIDGE_SMTP_PORT", "SMTP_PORT", default=587, section="smtp", key="port"),
        "username": config_str("BINDERBRIDGE_SMTP_USERNAME", "SMTP_USERNAME", default="", section="smtp", key="username"),
        "password": config_str("BINDERBRIDGE_SMTP_PASSWORD", "SMTP_PASSWORD", default="", section="smtp", key="password"),
        "from_address": config_str("BINDERBRIDGE_SMTP_FROM", "SMTP_FROM", default="", section="smtp", key="from_address"),
        "use_ssl": use_ssl,
        "use_starttls": config_bool("BINDERBRIDGE_SMTP_TLS", "SMTP_TLS", default=not use_ssl, section="smtp", key="tls"),
    }

def send_email_message(to_email, subject, body):
    settings = smtp_email_settings()
    if not settings["host"]:
        return False, "SMTP is not configured."
    to_email = sanitize_text_input(to_email, max_length=254).strip()
    if not to_email or "@" not in to_email:
        return False, "The user does not have a valid email address."
    from_address = settings["from_address"] or settings["username"] or "noreply@localhost"
    message = EmailMessage()
    message["Subject"] = sanitize_text_input(subject, max_length=160).strip() or APP_NAME
    message["From"] = from_address
    message["To"] = to_email
    message.set_content(sanitize_text_input(body, max_length=5000).strip())
    try:
        if settings["use_ssl"]:
            server = smtplib.SMTP_SSL(settings["host"], settings["port"], timeout=15)
        else:
            server = smtplib.SMTP(settings["host"], settings["port"], timeout=15)
        with server:
            if settings["use_starttls"] and not settings["use_ssl"]:
                server.starttls()
            if settings["username"] or settings["password"]:
                server.login(settings["username"], settings["password"])
            server.send_message(message)
    except Exception as exc:
        return False, f"Email could not be sent: {exc}"
    return True, "Email sent."

def notification_email_link(url):
    path = safe_local_redirect_path(url, default="") if url else ""
    if not path:
        return ""
    base_url = config_str("BINDERBRIDGE_PUBLIC_BASE_URL", "PUBLIC_BASE_URL", default="", section="server", key="public_base_url").strip().rstrip("/")
    return f"{base_url}{path}" if base_url else path

def notification_category_for(kind):
    return NOTIFICATION_KIND_CATEGORIES.get(str(kind or "").strip(), "")

def notification_in_app_enabled(user, kind):
    if not user:
        return False
    category = notification_category_for(kind)
    column = NOTIFICATION_IN_APP_COLUMNS.get(category)
    if not column:
        return True
    return bool(int(row_value(user, column, 1) or 0))

def notification_email_enabled(user, kind):
    if not user:
        return False
    category = notification_category_for(kind)
    column = NOTIFICATION_EMAIL_COLUMNS.get(category)
    if not column:
        return False
    if not email_delivery_configured():
        return False
    if not int(row_value(user, "email_trade_notifications_enabled", 0) or 0):
        return False
    if not str(row_value(user, "email", "") or "").strip():
        return False
    return bool(int(row_value(user, column, 1 if category.startswith("trade_") else 0) or 0))

def trade_notification_email_enabled(user, kind):
    return notification_email_enabled(user, kind)

def notification_email_status_for(user_id, kind, conn=None):
    found = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone() if conn is not None else row("SELECT * FROM users WHERE id = ?", (user_id,))
    return "pending" if notification_email_enabled(found, kind) else ""

def notification_email_body(notification):
    body = [
        row_value(notification, "title", "Notification"),
        "",
    ]
    detail = row_value(notification, "body", "")
    if detail:
        body.extend([detail, ""])
    link = notification_email_link(row_value(notification, "url", ""))
    if link:
        body.extend(["Open this in BinderBridge:", link, ""])
    body.append("You can change trade email notification preferences from your BinderBridge account page.")
    return "\n".join(body)


def notification_digest_email_body(notifications):
    body = [
        f"You have {len(notifications)} unread BinderBridge notification{'s' if len(notifications) != 1 else ''}.",
        "",
    ]
    for notification in notifications:
        body.append(f"- {row_value(notification, 'title', 'Notification')}")
        detail = row_value(notification, "body", "")
        if detail:
            body.append(f"  {detail}")
        link = notification_email_link(row_value(notification, "url", ""))
        if link:
            body.append(f"  {link}")
    body.extend(["", "You can change email digest and quiet-hour preferences from your BinderBridge account page."])
    return "\n".join(body)


def send_pending_trade_notification_emails(user_id=None, limit=20, reference_time=None):
    sent = 0
    failed = 0
    skipped = 0
    deferred = 0
    reference_time = reference_time or datetime.now(timezone.utc)
    with db() as conn:
        where = ["user_notifications.email_status = 'pending'", "user_notifications.is_read = 0"]
        params = []
        if user_id:
            where.append("user_notifications.user_id = ?")
            params.append(user_id)
        params.append(int(limit))
        notifications = conn.execute(
            f"""
            SELECT user_notifications.*, users.email, users.email_trade_notifications_enabled,
                users.email_trade_offer_enabled, users.email_trade_comment_enabled,
                users.email_trade_counter_enabled, users.email_trade_status_enabled,
                users.email_price_alert_enabled, users.email_import_complete_enabled,
                users.email_admin_notice_enabled, users.email_digest_frequency,
                users.email_digest_time, users.email_digest_weekday, users.notification_timezone,
                users.quiet_hours_enabled, users.quiet_hours_start, users.quiet_hours_end,
                users.last_notification_digest_at
            FROM user_notifications
            JOIN users ON users.id = user_notifications.user_id
            WHERE {' AND '.join(where)}
            ORDER BY user_notifications.created_at ASC, user_notifications.id ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
        eligible = []
        for notification in notifications:
            if not notification_email_enabled(notification, notification["kind"]):
                conn.execute(
                    "UPDATE user_notifications SET email_status = '', email_error = '' WHERE id = ?",
                    (notification["id"],),
                )
                skipped += 1
                continue
            eligible.append(notification)

        grouped = {}
        for notification in eligible:
            grouped.setdefault(notification["user_id"], []).append(notification)

        for pending_user_id, pending in grouped.items():
            user_settings = pending[0]
            if notification_quiet_hours_active(user_settings, reference_time):
                deferred += len(pending)
                continue
            frequency = str(row_value(user_settings, "email_digest_frequency", "immediate") or "immediate")
            if frequency != "immediate":
                if not notification_digest_due(user_settings, reference_time):
                    deferred += len(pending)
                    continue
                all_pending = conn.execute(
                    """
                    SELECT user_notifications.*, users.email, users.email_trade_notifications_enabled,
                        users.email_trade_offer_enabled, users.email_trade_comment_enabled,
                        users.email_trade_counter_enabled, users.email_trade_status_enabled,
                        users.email_price_alert_enabled, users.email_import_complete_enabled,
                        users.email_admin_notice_enabled, users.email_digest_frequency,
                        users.email_digest_time, users.email_digest_weekday, users.notification_timezone,
                        users.quiet_hours_enabled, users.quiet_hours_start, users.quiet_hours_end,
                        users.last_notification_digest_at
                    FROM user_notifications
                    JOIN users ON users.id = user_notifications.user_id
                    WHERE user_notifications.user_id = ?
                        AND user_notifications.email_status = 'pending'
                        AND user_notifications.is_read = 0
                    ORDER BY user_notifications.created_at ASC, user_notifications.id ASC
                    """,
                    (pending_user_id,),
                ).fetchall()
                pending = []
                for notification in all_pending:
                    if notification_email_enabled(notification, notification["kind"]):
                        pending.append(notification)
                    else:
                        conn.execute(
                            "UPDATE user_notifications SET email_status = '', email_error = '' WHERE id = ?",
                            (notification["id"],),
                        )
                        skipped += 1
                if not pending:
                    continue
                ok, message = send_email_message(
                    user_settings["email"],
                    f"[{APP_NAME}] {len(pending)} unread notification{'s' if len(pending) != 1 else ''}",
                    notification_digest_email_body(pending),
                )
                notification_ids = [item["id"] for item in pending]
                placeholders = ",".join("?" for _ in notification_ids)
                if ok:
                    sent_at = now_iso()
                    conn.execute(
                        f"UPDATE user_notifications SET email_status = 'sent', email_sent_at = ?, email_error = '' WHERE id IN ({placeholders})",
                        (sent_at, *notification_ids),
                    )
                    conn.execute(
                        "UPDATE users SET last_notification_digest_at = ? WHERE id = ?",
                        (sent_at, pending_user_id),
                    )
                    sent += len(pending)
                else:
                    conn.execute(
                        f"UPDATE user_notifications SET email_status = 'failed', email_error = ? WHERE id IN ({placeholders})",
                        (sanitize_text_input(message, max_length=500).strip(), *notification_ids),
                    )
                    failed += len(pending)
                continue

            for notification in pending:
                ok, message = send_email_message(
                    notification["email"],
                    f"[{APP_NAME}] {notification['title']}",
                    notification_email_body(notification),
                )
                if ok:
                    conn.execute(
                        "UPDATE user_notifications SET email_status = 'sent', email_sent_at = ?, email_error = '' WHERE id = ?",
                        (now_iso(), notification["id"]),
                    )
                    sent += 1
                else:
                    conn.execute(
                        "UPDATE user_notifications SET email_status = 'failed', email_error = ? WHERE id = ?",
                        (sanitize_text_input(message, max_length=500).strip(), notification["id"]),
                    )
                    failed += 1
    return {"sent": sent, "failed": failed, "skipped": skipped, "deferred": deferred}

def create_notification(user_id, kind, title, body="", url="", related_trade_id=None, conn=None):
    if not user_id:
        return 0
    timestamp = now_iso()
    clean_kind = sanitize_text_input(kind or "general", max_length=60).strip()
    notification_user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone() if conn is not None else row("SELECT * FROM users WHERE id = ?", (user_id,))
    if not notification_in_app_enabled(notification_user, clean_kind):
        return 0
    email_status = "pending" if notification_email_enabled(notification_user, clean_kind) else ""
    params = (
        int(user_id),
        clean_kind,
        sanitize_text_input(title or "Notification", max_length=160).strip(),
        sanitize_text_input(body, max_length=800).strip(),
        safe_local_redirect_path(url, default="") if url else "",
        related_trade_id,
        email_status,
        timestamp,
    )
    if conn is not None:
        cursor = conn.execute(
            """
            INSERT INTO user_notifications
                (user_id, kind, title, body, url, related_trade_id, email_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
        notification_id = cursor.lastrowid
        queue_webhooks = globals().get("queue_notification_webhooks")
        if queue_webhooks:
            queue_webhooks(user_id, notification_id, clean_kind, params[2], params[3], params[4], related_trade_id, conn=conn)
        if email_status == "pending":
            start_notification_worker(conn=conn)
        return notification_id
    with db() as new_conn:
        cursor = new_conn.execute(
            """
            INSERT INTO user_notifications
                (user_id, kind, title, body, url, related_trade_id, email_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
        notification_id = cursor.lastrowid
        queue_webhooks = globals().get("queue_notification_webhooks")
        if queue_webhooks:
            queue_webhooks(user_id, notification_id, clean_kind, params[2], params[3], params[4], related_trade_id, conn=new_conn)
    if email_status == "pending":
        start_notification_worker()
    return notification_id

def unread_notification_count(user_id):
    found = row(
        "SELECT COUNT(*) AS count FROM user_notifications WHERE user_id = ? AND is_read = 0",
        (user_id,),
    )
    return found["count"] if found else 0


def unread_trade_notification_count(user_id):
    found = row(
        """
        SELECT COUNT(*) AS count
        FROM user_notifications
        WHERE user_id = ?
            AND is_read = 0
            AND kind IN ('trade_offer', 'trade_counter', 'trade_comment', 'trade_status', 'trade_reminder', 'trade_dispute', 'trade_feedback')
        """,
        (user_id,),
    )
    return found["count"] if found else 0


def stale_trade_reminder_rows(user_id, limit=5):
    return rows(
        """
        SELECT user_notifications.*, trades.updated_at AS trade_updated_at,
            proposer.display_name AS proposer_name, recipient.display_name AS recipient_name
        FROM user_notifications
        JOIN trades ON trades.id = user_notifications.related_trade_id
        JOIN users proposer ON proposer.id = trades.proposer_id
        JOIN users recipient ON recipient.id = trades.recipient_id
        WHERE user_notifications.user_id = ?
            AND user_notifications.is_read = 0
            AND user_notifications.kind = 'trade_reminder'
            AND trades.status = 'pending'
        ORDER BY user_notifications.created_at DESC, user_notifications.id DESC
        LIMIT ?
        """,
        (user_id, int(limit)),
    )


def create_stale_trade_reminders(reference_time=None):
    reference_time = reference_time or datetime.now(timezone.utc)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=timezone.utc)
    reference_time = reference_time.astimezone(timezone.utc)
    created = 0
    with db() as conn:
        pending = conn.execute(
            """
            SELECT trades.*, proposer.display_name AS proposer_name,
                recipient.display_name AS recipient_name,
                recipient.stale_trade_reminder_days
            FROM trades
            JOIN users proposer ON proposer.id = trades.proposer_id
            JOIN users recipient ON recipient.id = trades.recipient_id
            WHERE trades.status = 'pending'
                AND recipient.stale_trade_reminder_days > 0
                AND recipient.is_banned = 0
                AND (
                    trades.stale_reminder_sent_at = ''
                    OR trades.stale_reminder_sent_at < trades.updated_at
                )
            ORDER BY trades.updated_at ASC, trades.id ASC
            """
        ).fetchall()
        for trade in pending:
            updated_at = parse_notification_datetime(trade["updated_at"])
            reminder_days = int(trade["stale_trade_reminder_days"] or 0)
            if not updated_at or updated_at > reference_time - timedelta(days=reminder_days):
                continue
            notification_id = create_notification(
                trade["recipient_id"],
                "trade_reminder",
                f"Trade #{trade['id']} needs your response",
                f"{trade['proposer_name']}'s trade offer has been waiting for {reminder_days} day{'s' if reminder_days != 1 else ''}. Accept, decline, comment, or send a counter offer.",
                f"/trades/{trade['id']}",
                trade["id"],
                conn=conn,
            )
            if notification_id:
                conn.execute(
                    "UPDATE trades SET stale_reminder_sent_at = ? WHERE id = ?",
                    (now_iso(), trade["id"]),
                )
                created += 1
    return created


def notification_worker_pass(reference_time=None):
    return {
        "trade_reminders": create_stale_trade_reminders(reference_time),
        "email": send_pending_trade_notification_emails(limit=100, reference_time=reference_time),
    }


def notification_worker_loop():
    while True:
        try:
            notification_worker_pass()
        except Exception:
            pass
        time.sleep(NOTIFICATION_WORKER_INTERVAL_SECONDS)


def start_notification_worker(conn=None):
    enqueue = globals().get("enqueue_background_job")
    if enqueue:
        _job_id, created = enqueue(
            "notification_delivery",
            unique_key="system:notification-delivery",
            max_attempts=10,
            conn=conn,
        )
        expedite = globals().get("expedite_background_job")
        if expedite:
            expedite("system:notification-delivery", conn=conn)
        return created
    return False


NOTIFICATION_LIST_CATEGORY_KINDS = {
    "trade": (
        "trade_offer",
        "trade_counter",
        "trade_comment",
        "trade_dispute",
        "trade_feedback",
        "trade_status",
        "trade_reminder",
    ),
    "price": ("price_alert", "price_refresh"),
    "watchlist": ("watchlist_alert",),
    "import": ("scryfall_import",),
    "admin": ("backup_status", "admin_notice"),
}


def notification_filter_values(query):
    query = query or {}
    filters = {
        "q": str(query.get("q", [""])[0] or "").strip(),
        "category": str(query.get("category", [""])[0] or "").strip().lower(),
        "state": str(query.get("state", [""])[0] or "").strip().lower(),
    }
    if filters["category"] not in ("", *NOTIFICATION_LIST_CATEGORY_KINDS.keys()):
        filters["category"] = ""
    if filters["state"] not in ("", "unread", "read"):
        filters["state"] = ""
    return filters


def notification_list_where(user_id, filters):
    where = ["user_id = ?"]
    params = [user_id]
    if filters.get("q"):
        term = f"%{filters['q']}%"
        where.append("(title LIKE ? OR body LIKE ?)")
        params.extend([term, term])
    if filters.get("state") == "unread":
        where.append("is_read = 0")
    elif filters.get("state") == "read":
        where.append("is_read = 1")
    kinds = NOTIFICATION_LIST_CATEGORY_KINDS.get(filters.get("category", ""), ())
    if kinds:
        placeholders = ", ".join("?" for _ in kinds)
        where.append(f"kind IN ({placeholders})")
        params.extend(kinds)
    return where, params


def notification_count(user_id, filters=None):
    where, params = notification_list_where(user_id, filters or {})
    return row(
        f"SELECT COUNT(*) AS count FROM user_notifications WHERE {' AND '.join(where)}",
        params,
    )["count"]


def notification_page_rows(user_id, filters, limit, offset):
    where, params = notification_list_where(user_id, filters)
    return rows(
        f"""
        SELECT *
        FROM user_notifications
        WHERE {' AND '.join(where)}
        ORDER BY is_read ASC, created_at DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, int(limit), int(offset)],
    )


def notification_rows(user_id, limit=80):
    return notification_page_rows(user_id, {}, limit, 0)


def mark_notification_read(user_id, notification_id):
    execute(
        """
        UPDATE user_notifications
        SET is_read = 1,
            email_status = CASE WHEN email_status = 'pending' THEN '' ELSE email_status END
        WHERE id = ? AND user_id = ?
        """,
        (notification_id, user_id),
    )

def mark_all_notifications_read(user_id):
    execute(
        """
        UPDATE user_notifications
        SET is_read = 1,
            email_status = CASE WHEN email_status = 'pending' THEN '' ELSE email_status END
        WHERE user_id = ?
        """,
        (user_id,),
    )

def delete_notification(user_id, notification_id):
    with db() as conn:
        cursor = conn.execute(
            "DELETE FROM user_notifications WHERE id = ? AND user_id = ?",
            (notification_id, user_id),
        )
        return cursor.rowcount

def delete_read_notifications(user_id):
    with db() as conn:
        cursor = conn.execute(
            "DELETE FROM user_notifications WHERE user_id = ? AND is_read = 1",
            (user_id,),
        )
        return cursor.rowcount

def delete_all_notifications(user_id):
    with db() as conn:
        cursor = conn.execute(
            "DELETE FROM user_notifications WHERE user_id = ?",
            (user_id,),
        )
        return cursor.rowcount

SCRYFALL_ENRICHMENT_TERMINAL_STATUSES = ("done", "not_found", "failed")

def notify_scryfall_enrichment_completion(user_id, conn):
    active = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM scryfall_enrichment_jobs
        WHERE user_id = ?
            AND completion_notified = 0
            AND status IN ('pending', 'processing')
        """,
        (user_id,),
    ).fetchone()["count"]
    if active:
        return 0
    found = conn.execute(
        """
        SELECT status, COUNT(*) AS count
        FROM scryfall_enrichment_jobs
        WHERE user_id = ?
            AND completion_notified = 0
            AND status IN ('done', 'not_found', 'failed')
        GROUP BY status
        """,
        (user_id,),
    ).fetchall()
    stats = {item["status"]: item["count"] for item in found}
    total = sum(stats.values())
    if not total:
        return 0
    enriched = stats.get("done", 0)
    not_found = stats.get("not_found", 0)
    failed = stats.get("failed", 0)
    details = [f"{enriched} enriched"]
    if not_found:
        details.append(f"{not_found} not found")
    if failed:
        details.append(f"{failed} need review")
    create_notification(
        user_id,
        "scryfall_import",
        "Scryfall import lookup complete",
        f"Background Scryfall lookup finished for {total} queued card{'s' if total != 1 else ''}: {', '.join(details)}.",
        "/import",
        None,
        conn=conn,
    )
    conn.execute(
        """
        UPDATE scryfall_enrichment_jobs
        SET completion_notified = 1
        WHERE user_id = ?
            AND completion_notified = 0
            AND status IN ('done', 'not_found', 'failed')
        """,
        (user_id,),
    )
    return total

__all__ = [
    'TRADE_EMAIL_NOTIFICATION_COLUMNS',
    'NOTIFICATION_KIND_CATEGORIES',
    'NOTIFICATION_IN_APP_COLUMNS',
    'NOTIFICATION_EMAIL_COLUMNS',
    'email_delivery_configured',
    'smtp_email_settings',
    'send_email_message',
    'notification_email_link',
    'notification_category_for',
    'notification_in_app_enabled',
    'notification_email_enabled',
    'trade_notification_email_enabled',
    'notification_email_status_for',
    'notification_email_body',
    'notification_digest_email_body',
    'send_pending_trade_notification_emails',
    'create_notification',
    'unread_notification_count',
    'unread_trade_notification_count',
    'stale_trade_reminder_rows',
    'create_stale_trade_reminders',
    'notification_worker_pass',
    'notification_worker_loop',
    'start_notification_worker',
    'notification_rows',
    'NOTIFICATION_LIST_CATEGORY_KINDS',
    'notification_filter_values',
    'notification_list_where',
    'notification_count',
    'notification_page_rows',
    'mark_notification_read',
    'mark_all_notifications_read',
    'delete_notification',
    'delete_read_notifications',
    'delete_all_notifications',
    'SCRYFALL_ENRICHMENT_TERMINAL_STATUSES',
    'notify_scryfall_enrichment_completion',
    'EMAIL_DIGEST_FREQUENCIES',
    'EMAIL_DIGEST_FREQUENCY_LABELS',
    'EMAIL_DIGEST_WEEKDAY_LABELS',
    'NOTIFICATION_WORKER_INTERVAL_SECONDS',
    'normalize_email_digest_frequency',
    'normalize_notification_time',
    'normalize_email_digest_weekday',
    'normalize_notification_timezone',
    'normalize_stale_trade_reminder_days',
    'parse_notification_datetime',
    'notification_local_time',
    'notification_time_minutes',
    'notification_quiet_hours_active',
    'notification_digest_due',
]
