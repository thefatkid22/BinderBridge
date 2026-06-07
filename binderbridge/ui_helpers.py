"""Extracted BinderBridge feature code.

The app facade injects shared helpers/constants into this module at import time
so the legacy app.py public API remains compatible during the split.
"""

import base64
import binascii
import hashlib
import hmac
import html
import json
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
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

from binderbridge import db as _db_helpers
from binderbridge import formatting as _formatting_helpers
from binderbridge import notifications as _notification_helpers
from binderbridge import pricing as _pricing_helpers
from binderbridge import security as _security_helpers

__binderbridge_feature_modules__ = (
    _db_helpers,
    _formatting_helpers,
    _notification_helpers,
    _pricing_helpers,
    _security_helpers,
)

from binderbridge.db import *
from binderbridge.formatting import *
from binderbridge.notifications import *
from binderbridge.pricing import *
from binderbridge.security import *


def env_int(name, default):
    return config_int(name, default=default)


def env_float(name, default):
    return config_float(name, default=default)


def env_str(*names, default=""):
    return config_str(*names, default=default)


SCRYFALL_DELAY_SECONDS = config_float("SCRYFALL_DELAY_SECONDS", default=0.12, section="scryfall", key="delay_seconds")
SCRYFALL_USER_AGENT = config_str("SCRYFALL_USER_AGENT", default="BinderBridge/0.1 self-hosted collection manager", section="scryfall", key="user_agent")
SCRYFALL_ACCEPT = "application/json;q=0.9,*/*;q=0.8"
DECK_IMPORT_ACCEPT = "application/json;q=0.9,text/plain;q=0.8,text/csv;q=0.7,text/html;q=0.4,*/*;q=0.1"
DECK_IMPORT_MAX_BYTES = max(64_000, config_int("DECK_IMPORT_MAX_BYTES", default=1_500_000, section="imports", key="deck_import_max_bytes"))
SCRYFALL_SEARCH_LIMIT = config_int("SCRYFALL_SEARCH_LIMIT", default=24, section="scryfall", key="search_limit")
TRUSTED_TRADE_THRESHOLD_KEY = "trusted_trade_threshold"
DEFAULT_TRUSTED_TRADE_THRESHOLD = 5
ONE_WAY_TRADE_POLICY_KEY = "one_way_trade_policy"
DEFAULT_ONE_WAY_TRADE_POLICY = "trusted"
ONE_WAY_TRADE_POLICY_OPTIONS = (
    ("trusted", "Trusted users only"),
    ("admins", "Admins only"),
    ("anyone", "Any active user"),
    ("disabled", "Disabled"),
)
TRADE_FAIRNESS_WARN_PERCENT_KEY = "trade_fairness_warn_percent"
TRADE_FAIRNESS_BLOCK_PERCENT_KEY = "trade_fairness_block_percent"
DEFAULT_TRADE_FAIRNESS_WARN_PERCENT = "20"
DEFAULT_TRADE_FAIRNESS_BLOCK_PERCENT = "0"
DISPUTE_ESCALATION_DAYS_KEY = "dispute_escalation_days"
DISPUTE_EVIDENCE_RETENTION_DAYS_KEY = "dispute_evidence_retention_days"
DEFAULT_DISPUTE_ESCALATION_DAYS = 7
DEFAULT_DISPUTE_EVIDENCE_RETENTION_DAYS = 0
INVITE_ONLY_REGISTRATION_KEY = "invite_only_registration"
REGISTRATION_INVITE_EXPIRY_DAYS = max(1, config_int("BINDERBRIDGE_REGISTRATION_INVITE_EXPIRY_DAYS", "REGISTRATION_INVITE_EXPIRY_DAYS", default=14, section="registration", key="invite_expiry_days"))
ADMIN_AUDIT_ACTION_LABELS = {
    "user_banned": "User banned",
    "user_unbanned": "User unbanned",
    "password_reset": "Password reset",
    "admin_granted": "Admin access granted",
    "admin_removed": "Admin access removed",
    "admin_notes_updated": "Admin notes updated",
    "trust_granted": "Trusted status granted",
    "trust_revoked": "Trusted status revoked",
    "trust_reset": "Trusted status reset",
    "trusted_threshold_updated": "Trusted threshold updated",
    "trade_fairness_updated": "Trade fairness updated",
    "trade_policy_updated": "Trade policy updated",
    "dispute_evidence_retention_pruned": "Dispute evidence pruned",
    "data_retention_updated": "Data retention updated",
    "data_retention_pruned": "Data retention cleanup run",
    "registration_mode_updated": "Registration mode updated",
    "invite_created": "Invite created",
    "invite_revoked": "Invite revoked",
    "backup_created": "Backup created",
    "backup_settings_updated": "Backup settings updated",
    "backup_run": "Automatic backup run",
    "backup_restored": "Backup restored",
    "scryfall_bulk_sync_started": "Scryfall bulk sync started",
    "trade_dispute_updated": "Trade issue updated",
    "two_factor_reset": "Two-factor reset",
    "api_token_created": "API token created",
    "api_token_revoked": "API token revoked",
    "api_auth_failed": "API authentication failed",
    "api_write": "API write action",
    "integration_policy_updated": "Integration policy updated",
    "webhook_created": "Webhook created",
    "webhook_deleted": "Webhook deleted",
    "webhook_tested": "Webhook tested",
}
ADMIN_AUDIT_ACTION_OPTIONS = tuple(ADMIN_AUDIT_ACTION_LABELS.items())




























































































































def clean_log_user_id(conn, user_id):
    try:
        parsed = int(user_id)
    except (TypeError, ValueError):
        return None, None
    if parsed <= 0:
        return None, None
    found = conn.execute("SELECT id, username, display_name, email FROM users WHERE id = ?", (parsed,)).fetchone()
    if not found:
        return None, None
    return parsed, found


def admin_audit_action_label(action):
    return ADMIN_AUDIT_ACTION_LABELS.get(str(action or ""), str(action or "").replace("_", " ").title() or "Admin action")


def admin_audit_user_label(user):
    if not user:
        return ""
    display_name = row_value(user, "display_name", "")
    username = row_value(user, "username", "")
    if display_name and username:
        return f"{display_name} (@{username})"
    return display_name or (f"@{username}" if username else "")


def log_admin_action(
    admin_user_id,
    action,
    target_user_id=None,
    target_type="",
    target_label="",
    details="",
    request_ip="",
    user_agent="",
    conn=None,
):
    action = sanitize_text_input(action, max_length=80).strip()
    if not action:
        return None
    target_type = sanitize_text_input(target_type, max_length=80).strip()
    target_label = sanitize_text_input(target_label, max_length=200).strip()
    if isinstance(details, (dict, list, tuple)):
        details = json.dumps(details, ensure_ascii=True, sort_keys=True)
    details = sanitize_text_input(details, max_length=2000).strip()
    request_ip = sanitize_text_input(request_ip, max_length=80).strip()
    user_agent = sanitize_text_input(user_agent, max_length=300).strip()
    timestamp = now_iso()

    def insert_with_connection(connection):
        cleaned_admin_id, _ = clean_log_user_id(connection, admin_user_id)
        cleaned_target_id, target = clean_log_user_id(connection, target_user_id)
        label = target_label or admin_audit_user_label(target)
        cursor = connection.execute(
            """
            INSERT INTO admin_audit_log
                (admin_user_id, target_user_id, target_type, target_label, action, details, request_ip, user_agent, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (cleaned_admin_id, cleaned_target_id, target_type, label, action, details, request_ip, user_agent, timestamp),
        )
        return cursor.lastrowid

    if conn is not None:
        return insert_with_connection(conn)
    with db() as connection:
        return insert_with_connection(connection)


def admin_audit_log_filters(filters=None):
    filters = filters or {}
    q = sanitize_text_input(filters.get("q", ""), max_length=120).strip()
    action = sanitize_text_input(filters.get("action", ""), max_length=80).strip()
    if action and action not in ADMIN_AUDIT_ACTION_LABELS:
        action = ""
    return {"q": q, "action": action}


def admin_audit_log_where(filters=None):
    filters = admin_audit_log_filters(filters)
    where = []
    params = []
    if filters["q"]:
        term = f"%{filters['q']}%"
        where.append(
            """
            (
                admin_audit_log.action LIKE ?
                OR admin_audit_log.target_label LIKE ?
                OR admin_audit_log.details LIKE ?
                OR admin.username LIKE ?
                OR admin.display_name LIKE ?
                OR target.username LIKE ?
                OR target.display_name LIKE ?
            )
            """
        )
        params.extend([term, term, term, term, term, term, term])
    if filters["action"]:
        where.append("admin_audit_log.action = ?")
        params.append(filters["action"])
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    return where_sql, params


def admin_audit_log_count(filters=None):
    where_sql, params = admin_audit_log_where(filters)
    found = row(
        f"""
        SELECT COUNT(*) AS count
        FROM admin_audit_log
        LEFT JOIN users admin ON admin.id = admin_audit_log.admin_user_id
        LEFT JOIN users target ON target.id = admin_audit_log.target_user_id
        {where_sql}
        """,
        params,
    )
    return found["count"] if found else 0


def admin_audit_log_rows(filters=None, limit=25, offset=0):
    filters = admin_audit_log_filters(filters)
    limit = max(1, min(int(limit or 25), 100))
    offset = max(0, int(offset or 0))
    where_sql, params = admin_audit_log_where(filters)
    return rows(
        f"""
        SELECT
            admin_audit_log.*,
            admin.username AS admin_username,
            admin.display_name AS admin_display_name,
            target.username AS target_username,
            target.display_name AS target_display_name
        FROM admin_audit_log
        LEFT JOIN users admin ON admin.id = admin_audit_log.admin_user_id
        LEFT JOIN users target ON target.id = admin_audit_log.target_user_id
        {where_sql}
        ORDER BY admin_audit_log.created_at DESC, admin_audit_log.id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    )






















































































def trusted_trade_threshold():
    try:
        threshold = int(get_setting(TRUSTED_TRADE_THRESHOLD_KEY, str(DEFAULT_TRUSTED_TRADE_THRESHOLD)))
    except (TypeError, ValueError):
        threshold = DEFAULT_TRUSTED_TRADE_THRESHOLD
    return max(1, threshold)


def normalize_trusted_trade_threshold(value):
    try:
        threshold = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("Trusted trade threshold must be a number.") from exc
    if threshold < 1:
        raise ValueError("Trusted trade threshold must be at least 1.")
    return threshold


def set_trusted_trade_threshold(value):
    threshold = normalize_trusted_trade_threshold(value)
    set_setting(TRUSTED_TRADE_THRESHOLD_KEY, threshold)
    return threshold


def normalize_one_way_trade_policy(value):
    policy = str(value or "").strip().lower()
    allowed = {key for key, _label in ONE_WAY_TRADE_POLICY_OPTIONS}
    if policy not in allowed:
        raise ValueError("Choose a valid one-way trade policy.")
    return policy


def one_way_trade_policy():
    try:
        return normalize_one_way_trade_policy(get_setting(ONE_WAY_TRADE_POLICY_KEY, DEFAULT_ONE_WAY_TRADE_POLICY))
    except ValueError:
        return DEFAULT_ONE_WAY_TRADE_POLICY


def set_one_way_trade_policy(value):
    policy = normalize_one_way_trade_policy(value)
    set_setting(ONE_WAY_TRADE_POLICY_KEY, policy)
    return policy


def one_way_trade_policy_label(policy=None):
    labels = dict(ONE_WAY_TRADE_POLICY_OPTIONS)
    return labels.get(policy or one_way_trade_policy(), labels[DEFAULT_ONE_WAY_TRADE_POLICY])


def user_can_propose_one_way_trade(user):
    if not user or row_value(user, "is_banned", 0):
        return False
    policy = one_way_trade_policy()
    if policy == "disabled":
        return False
    if policy == "anyone":
        return True
    if policy == "admins":
        return bool(row_value(user, "is_admin", 0))
    return is_trusted_user(user)


def normalize_trade_fairness_percent(value, label="Trade fairness threshold"):
    text = str(value or "").strip()
    if not text:
        return "0"
    try:
        percent = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if percent < 0:
        raise ValueError(f"{label} cannot be negative.")
    if percent > Decimal("10000"):
        raise ValueError(f"{label} is too high.")
    normalized = percent.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(normalized.normalize(), "f") if normalized else "0"


def trade_fairness_settings():
    warn_percent = normalize_trade_fairness_percent(
        get_setting(TRADE_FAIRNESS_WARN_PERCENT_KEY, DEFAULT_TRADE_FAIRNESS_WARN_PERCENT),
        "Trade fairness warning threshold",
    )
    block_percent = normalize_trade_fairness_percent(
        get_setting(TRADE_FAIRNESS_BLOCK_PERCENT_KEY, DEFAULT_TRADE_FAIRNESS_BLOCK_PERCENT),
        "Trade fairness block threshold",
    )
    return {
        "warn_percent": warn_percent,
        "block_percent": block_percent,
        "warn_enabled": Decimal(warn_percent) > 0,
        "block_enabled": Decimal(block_percent) > 0,
    }


def set_trade_fairness_settings(warn_percent, block_percent):
    warn_percent = normalize_trade_fairness_percent(warn_percent, "Trade fairness warning threshold")
    block_percent = normalize_trade_fairness_percent(block_percent, "Trade fairness block threshold")
    if Decimal(warn_percent) > 0 and Decimal(block_percent) > 0 and Decimal(block_percent) < Decimal(warn_percent):
        raise ValueError("Trade fairness block threshold must be equal to or higher than the warning threshold.")
    set_setting(TRADE_FAIRNESS_WARN_PERCENT_KEY, warn_percent)
    set_setting(TRADE_FAIRNESS_BLOCK_PERCENT_KEY, block_percent)
    return trade_fairness_settings()


def normalize_policy_days(value, label, minimum=0, maximum=3650):
    try:
        days = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if days < minimum:
        raise ValueError(f"{label} must be at least {minimum}.")
    if days > maximum:
        raise ValueError(f"{label} must be {maximum} or less.")
    return days


def dispute_escalation_days():
    try:
        return normalize_policy_days(
            get_setting(DISPUTE_ESCALATION_DAYS_KEY, DEFAULT_DISPUTE_ESCALATION_DAYS),
            "Dispute escalation timing",
            minimum=1,
        )
    except ValueError:
        return DEFAULT_DISPUTE_ESCALATION_DAYS


def set_dispute_escalation_days(value):
    days = normalize_policy_days(value, "Dispute escalation timing", minimum=1)
    set_setting(DISPUTE_ESCALATION_DAYS_KEY, days)
    return days


def dispute_evidence_retention_days():
    try:
        return normalize_policy_days(
            get_setting(DISPUTE_EVIDENCE_RETENTION_DAYS_KEY, DEFAULT_DISPUTE_EVIDENCE_RETENTION_DAYS),
            "Evidence retention",
            minimum=0,
        )
    except ValueError:
        return DEFAULT_DISPUTE_EVIDENCE_RETENTION_DAYS


def set_dispute_evidence_retention_days(value):
    days = normalize_policy_days(value, "Evidence retention", minimum=0)
    set_setting(DISPUTE_EVIDENCE_RETENTION_DAYS_KEY, days)
    return days


def trade_policy_settings():
    return {
        "one_way_policy": one_way_trade_policy(),
        "one_way_policy_label": one_way_trade_policy_label(),
        "trusted_threshold": trusted_trade_threshold(),
        "fairness": trade_fairness_settings(),
        "dispute_escalation_days": dispute_escalation_days(),
        "evidence_retention_days": dispute_evidence_retention_days(),
    }


def set_trade_policy_settings(
    one_way_policy_value,
    trusted_threshold_value,
    fairness_warn_percent,
    fairness_block_percent,
    dispute_escalation_days_value,
    evidence_retention_days_value,
):
    policy = normalize_one_way_trade_policy(one_way_policy_value)
    threshold = normalize_trusted_trade_threshold(trusted_threshold_value)
    warn_percent = normalize_trade_fairness_percent(fairness_warn_percent, "Trade fairness warning threshold")
    block_percent = normalize_trade_fairness_percent(fairness_block_percent, "Trade fairness block threshold")
    if Decimal(warn_percent) > 0 and Decimal(block_percent) > 0 and Decimal(block_percent) < Decimal(warn_percent):
        raise ValueError("Trade fairness block threshold must be equal to or higher than the warning threshold.")
    escalation_days = normalize_policy_days(dispute_escalation_days_value, "Dispute escalation timing", minimum=1)
    retention_days = normalize_policy_days(evidence_retention_days_value, "Evidence retention", minimum=0)
    set_setting(ONE_WAY_TRADE_POLICY_KEY, policy)
    set_setting(TRUSTED_TRADE_THRESHOLD_KEY, threshold)
    set_setting(TRADE_FAIRNESS_WARN_PERCENT_KEY, warn_percent)
    set_setting(TRADE_FAIRNESS_BLOCK_PERCENT_KEY, block_percent)
    set_setting(DISPUTE_ESCALATION_DAYS_KEY, escalation_days)
    set_setting(DISPUTE_EVIDENCE_RETENTION_DAYS_KEY, retention_days)
    return trade_policy_settings()


def parse_policy_datetime(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def trade_dispute_escalation_status(dispute, reference_time=None):
    status = str(row_value(dispute, "status", "") or "").strip().lower()
    threshold_days = dispute_escalation_days()
    created_at = parse_policy_datetime(row_value(dispute, "created_at", ""))
    if not created_at or status not in ("open", "reviewing"):
        return {"escalated": False, "age_days": 0, "threshold_days": threshold_days}
    now = reference_time or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    age_days = max(0, (now.astimezone(timezone.utc) - created_at).days)
    return {
        "escalated": age_days >= threshold_days,
        "age_days": age_days,
        "threshold_days": threshold_days,
    }


def completed_trade_count(user_id):
    found = row(
        """
        SELECT COUNT(*) AS count
        FROM trades
        WHERE status = 'completed' AND (proposer_id = ? OR recipient_id = ?)
        """,
        (user_id, user_id),
    )
    return found["count"] if found else 0


def is_trusted_user(user):
    if not user or row_value(user, "is_banned", 0):
        return False
    override = int(row_value(user, "trusted_override", 0) or 0)
    if override == 1:
        return True
    if override == -1:
        return False
    completed = row_value(user, "completed_trade_count")
    if completed is None:
        completed = completed_trade_count(user["id"])
    return int(completed or 0) >= trusted_trade_threshold()


def trusted_status_details(user):
    completed = row_value(user, "completed_trade_count")
    if completed is None:
        completed = completed_trade_count(user["id"])
    override = int(row_value(user, "trusted_override", 0) or 0)
    threshold = trusted_trade_threshold()
    if override == 1:
        return "trusted", "Trusted", f"Manually trusted. {completed} completed trade{'s' if completed != 1 else ''}."
    if override == -1:
        return "revoked", "Trust revoked", f"Cannot auto-earn trust. {completed} completed trade{'s' if completed != 1 else ''}."
    if int(completed or 0) >= threshold:
        return "earned", "Trusted", f"Earned with {completed} completed trade{'s' if completed != 1 else ''}."
    remaining = threshold - int(completed or 0)
    return "pending", "Not trusted", f"{remaining} more completed trade{'s' if remaining != 1 else ''} to earn trust."


def admin_set_user_trust(target_user_id, action, admin_user_id=None):
    target = row("SELECT * FROM users WHERE id = ?", (target_user_id,))
    if not target:
        raise ValueError("User not found.")
    if action == "trust":
        override = 1
        audit_action = "trust_granted"
    elif action == "revoke":
        override = -1
        audit_action = "trust_revoked"
    elif action == "reset":
        override = 0
        audit_action = "trust_reset"
    else:
        raise ValueError("Unknown trust action.")
    execute("UPDATE users SET trusted_override = ?, updated_at = ? WHERE id = ?", (override, now_iso(), target_user_id))
    if admin_user_id:
        log_admin_action(
            admin_user_id,
            audit_action,
            target_user_id,
            "user",
            admin_audit_user_label(target),
        )
















































__all__ = [
    "env_int",
    "env_float",
    "env_str",
    "config_bool",
    "config_float",
    "config_int",
    "config_str",
    "SCRYFALL_DELAY_SECONDS",
    "SCRYFALL_USER_AGENT",
    "SCRYFALL_ACCEPT",
    "DECK_IMPORT_ACCEPT",
    "DECK_IMPORT_MAX_BYTES",
    "SCRYFALL_SEARCH_LIMIT",
    "SCRYFALL_BULK_TYPE",
    "TRUSTED_TRADE_THRESHOLD_KEY",
    "DEFAULT_TRUSTED_TRADE_THRESHOLD",
    "ONE_WAY_TRADE_POLICY_KEY",
    "DEFAULT_ONE_WAY_TRADE_POLICY",
    "ONE_WAY_TRADE_POLICY_OPTIONS",
    "TRADE_FAIRNESS_WARN_PERCENT_KEY",
    "TRADE_FAIRNESS_BLOCK_PERCENT_KEY",
    "DEFAULT_TRADE_FAIRNESS_WARN_PERCENT",
    "DEFAULT_TRADE_FAIRNESS_BLOCK_PERCENT",
    "DISPUTE_ESCALATION_DAYS_KEY",
    "DISPUTE_EVIDENCE_RETENTION_DAYS_KEY",
    "DEFAULT_DISPUTE_ESCALATION_DAYS",
    "DEFAULT_DISPUTE_EVIDENCE_RETENTION_DAYS",
    "INVITE_ONLY_REGISTRATION_KEY",
    "SCHEMA_VERSION_KEY",
    "CURRENT_SCHEMA_VERSION",
    "REGISTRATION_INVITE_EXPIRY_DAYS",
    "ADMIN_AUDIT_ACTION_LABELS",
    "ADMIN_AUDIT_ACTION_OPTIONS",
    "SCRYFALL_BULK_STATUS_KEY",
    "SCRYFALL_BULK_UPDATED_KEY",
    "SCRYFALL_BULK_ERROR_KEY",
    "SCRYFALL_PRICE_REFRESH_STATUS_KEY",
    "SCRYFALL_PRICE_REFRESH_UPDATED_KEY",
    "SCRYFALL_PRICE_REFRESH_ERROR_KEY",
    "PRICE_REFRESH_BATCH_SIZE",
    "PRICE_REFRESH_DELAY_SECONDS",
    "PRICE_REFRESH_INTERVAL_HOURS",
    "PRICE_REFRESH_AUTO",
    "SCRYFALL_PRICE_REFRESH_INTERVAL_HOURS",
    "SCRYFALL_PRICE_REFRESH_AUTO",
    "CARD_GAMES",
    "CONDITION_OPTIONS",
    "FINISH_OPTIONS",
    "LANGUAGE_OPTIONS",
    "RARITY_OPTIONS",
    "COLOR_IDENTITY_OPTIONS",
    "CARD_DATA_FILTER_OPTIONS",
    "GROUP_TYPE_OPTIONS",
    "PRICE_SOURCE_OPTIONS",
    "PRICE_PROVIDER_KEYS",
    "PRICE_PROVIDER_OPTIONS",
    "PRICE_BASIS_OPTIONS",
    "PRICE_PROVIDER_ID_FIELDS",
    "TRADE_STATUS_LABELS",
    "TRADE_DISPUTE_CATEGORY_OPTIONS",
    "TRADE_DISPUTE_STATUS_OPTIONS",
    "TRADE_EMAIL_NOTIFICATION_COLUMNS",
    "CSV_SOURCE_OPTIONS",
    "DECK_IMPORT_SOURCE_OPTIONS",
    "PAGE_SIZE_OPTIONS",
    "now_iso",
    "future_iso",
    "SQLITE_BUSY_TIMEOUT_MS",
    "configure_sqlite_connection",
    "db",
    "init_db",
    "migrate_db",
    "db_schema_version",
    "set_db_schema_version",
    "migrate_hot_path_indexes",
    "SCHEMA_MIGRATIONS",
    "run_schema_migrations",
    "hash_password",
    "verify_password",
    "generate_totp_secret",
    "normalize_totp_secret",
    "normalize_totp_code",
    "totp_secret_bytes",
    "totp_code",
    "verify_totp_code",
    "user_totp_label",
    "totp_otpauth_uri",
    "format_totp_secret",
    "QR_VERSION",
    "QR_SIZE",
    "QR_DATA_CODEWORDS",
    "QR_ECC_CODEWORDS_PER_BLOCK",
    "QR_BLOCK_COUNT",
    "QR_DATA_CODEWORDS_PER_BLOCK",
    "qr_gf_tables",
    "QR_GF_EXP",
    "QR_GF_LOG",
    "qr_gf_multiply",
    "qr_reed_solomon_generator",
    "qr_reed_solomon_remainder",
    "qr_bit_buffer_for_text",
    "qr_codewords_for_text",
    "qr_empty_matrix",
    "qr_set_function",
    "qr_draw_finder",
    "qr_draw_alignment",
    "qr_draw_function_patterns",
    "qr_format_bits",
    "qr_draw_format_bits",
    "qr_matrix",
    "qr_svg",
    "generate_recovery_codes",
    "normalize_recovery_code",
    "hash_recovery_code",
    "verify_recovery_code",
    "recovery_code_hashes",
    "load_recovery_code_hashes",
    "two_factor_enabled",
    "user_totp_setup_details",
    "start_user_totp_setup",
    "enable_user_totp",
    "disable_user_totp",
    "regenerate_user_totp_recovery_codes",
    "consume_user_recovery_code",
    "verify_user_two_factor",
    "create_two_factor_challenge",
    "two_factor_challenge",
    "delete_two_factor_challenge",
    "complete_two_factor_login",
    "PASSKEY_CHALLENGE_TTL_SECONDS",
    "PASSKEY_SUPPORTED_ALG",
    "PASSKEY_UP_FLAG",
    "PASSKEY_UV_FLAG",
    "PASSKEY_AT_FLAG",
    "P256_P",
    "P256_A",
    "P256_B",
    "P256_GX",
    "P256_GY",
    "P256_N",
    "passkey_b64encode",
    "passkey_b64decode",
    "passkey_user_handle",
    "passkey_clean_rp_id",
    "passkey_existing_credentials",
    "passkey_credential_count",
    "create_passkey_challenge",
    "passkey_challenge_row",
    "delete_passkey_challenge",
    "passkey_registration_options",
    "passkey_authentication_options",
    "cbor_read_length",
    "cbor_read",
    "passkey_client_data",
    "passkey_parse_authenticator_data",
    "passkey_require_flags",
    "passkey_validate_rp_hash",
    "passkey_parse_cose_ec2_key",
    "p256_inverse",
    "p256_is_on_curve",
    "p256_point_add",
    "p256_scalar_mult",
    "ecdsa_der_signature_rs",
    "ecdsa_verify_p256",
    "passkey_payload_value",
    "parse_passkey_payload",
    "complete_passkey_registration",
    "complete_passkey_authentication",
    "delete_passkey_credential",
    "create_session",
    "delete_session",
    "get_user_by_session",
    "get_user_by_username",
    "create_user",
    "rows",
    "row",
    "execute",
    "clean_log_user_id",
    "admin_audit_action_label",
    "admin_audit_user_label",
    "log_admin_action",
    "admin_audit_log_filters",
    "admin_audit_log_where",
    "admin_audit_log_count",
    "admin_audit_log_rows",
    "row_value",
    "normalize_price_usd",
    "price_to_cents",
    "money",
    "normalize_price_source",
    "price_source_label",
    "normalize_price_basis",
    "price_basis_label",
    "user_price_preference",
    "price_pill",
    "price_text_from_cents",
    "signed_money",
    "signed_price_text",
    "price_change_percent",
    "normalize_price_alert_threshold",
    "user_price_alert_settings",
    "should_send_price_alert",
    "email_delivery_configured",
    "smtp_email_settings",
    "send_email_message",
    "notification_email_link",
    "notification_category_for",
    "notification_in_app_enabled",
    "notification_email_enabled",
    "trade_notification_email_enabled",
    "notification_email_status_for",
    "notification_email_body",
    "send_pending_trade_notification_emails",
    "create_notification",
    "unread_notification_count",
    "notification_rows",
    "mark_notification_read",
    "mark_all_notifications_read",
    "delete_notification",
    "delete_read_notifications",
    "delete_all_notifications",
    "price_history_rows",
    "price_history_summary",
    "card_price_history_label",
    "record_price_history_for_item",
    "SCRYFALL_ENRICHMENT_TERMINAL_STATUSES",
    "notify_scryfall_enrichment_completion",
    "get_setting",
    "set_setting",
    "trusted_trade_threshold",
    "normalize_trusted_trade_threshold",
    "set_trusted_trade_threshold",
    "normalize_one_way_trade_policy",
    "one_way_trade_policy",
    "set_one_way_trade_policy",
    "one_way_trade_policy_label",
    "user_can_propose_one_way_trade",
    "normalize_trade_fairness_percent",
    "trade_fairness_settings",
    "set_trade_fairness_settings",
    "normalize_policy_days",
    "dispute_escalation_days",
    "set_dispute_escalation_days",
    "dispute_evidence_retention_days",
    "set_dispute_evidence_retention_days",
    "trade_policy_settings",
    "set_trade_policy_settings",
    "parse_policy_datetime",
    "trade_dispute_escalation_status",
    "completed_trade_count",
    "is_trusted_user",
    "trusted_status_details",
    "admin_set_user_trust",
    "e",
    "selected",
    "checked",
    "form_public_flag",
    "game_label",
    "option_tags",
    "simple_option_tags",
    "SUGGESTION_COLUMNS",
    "datalist_options",
    "render_datalist",
    "collection_field_suggestions",
    "collection_search_suggestions",
    "browse_search_suggestions",
    "browse_field_suggestions",
    "trade_picker_field_suggestions",
    "trade_picker_search_suggestions",
    "member_search_suggestions",
    "get_trade_participants",
    "compact_card_label",
    "stat_percent",
    "stats_color_identity_label",
    "collection_stat_bucket",
    "collection_statistics",
    "get_collection_summary",
]
