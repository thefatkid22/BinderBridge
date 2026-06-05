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
    "trade_feedback": "trade_status",
    "price_alert": "price_alert",
    "watchlist_alert": "watchlist_alert",
    "scryfall_import": "import_complete",
    "backup_status": "admin_notice",
    "admin_notice": "admin_notice",
    "trade_dispute": "admin_notice",
}

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

def send_pending_trade_notification_emails(user_id=None, limit=20):
    sent = 0
    failed = 0
    skipped = 0
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
                users.email_admin_notice_enabled
            FROM user_notifications
            JOIN users ON users.id = user_notifications.user_id
            WHERE {' AND '.join(where)}
            ORDER BY user_notifications.created_at ASC, user_notifications.id ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
        for notification in notifications:
            if not notification_email_enabled(notification, notification["kind"]):
                conn.execute(
                    "UPDATE user_notifications SET email_status = '', email_error = '' WHERE id = ?",
                    (notification["id"],),
                )
                skipped += 1
                continue
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
    return {"sent": sent, "failed": failed, "skipped": skipped}

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
        send_pending_trade_notification_emails(user_id=user_id, limit=5)
    send_webhooks = globals().get("send_pending_webhook_deliveries")
    if send_webhooks:
        send_webhooks(user_id=user_id, limit=5)
    return notification_id

def unread_notification_count(user_id):
    found = row(
        "SELECT COUNT(*) AS count FROM user_notifications WHERE user_id = ? AND is_read = 0",
        (user_id,),
    )
    return found["count"] if found else 0

def notification_rows(user_id, limit=80):
    return rows(
        """
        SELECT *
        FROM user_notifications
        WHERE user_id = ?
        ORDER BY is_read ASC, created_at DESC, id DESC
        LIMIT ?
        """,
        (user_id, int(limit)),
    )

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
    'send_pending_trade_notification_emails',
    'create_notification',
    'unread_notification_count',
    'notification_rows',
    'mark_notification_read',
    'mark_all_notifications_read',
    'delete_notification',
    'delete_read_notifications',
    'delete_all_notifications',
    'SCRYFALL_ENRICHMENT_TERMINAL_STATUSES',
    'notify_scryfall_enrichment_completion',
]
