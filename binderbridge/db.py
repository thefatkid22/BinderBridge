"""SQLite connection helpers and schema bootstrapping for BinderBridge.

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

def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def future_iso(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat()

@contextmanager
def db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

def init_db():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                email TEXT NOT NULL DEFAULT '',
                display_name TEXT NOT NULL,
                bio TEXT NOT NULL DEFAULT '',
                public_email INTEGER NOT NULL DEFAULT 0,
                is_admin INTEGER NOT NULL DEFAULT 0,
                is_banned INTEGER NOT NULL DEFAULT 0,
                trusted_override INTEGER NOT NULL DEFAULT 0,
                ban_reason TEXT NOT NULL DEFAULT '',
                admin_notes TEXT NOT NULL DEFAULT '',
                banned_at TEXT NOT NULL DEFAULT '',
                preferred_price_source TEXT NOT NULL DEFAULT '',
                price_alerts_enabled INTEGER NOT NULL DEFAULT 1,
                price_alert_threshold_percent TEXT NOT NULL DEFAULT '0',
                watchlist_alerts_enabled INTEGER NOT NULL DEFAULT 1,
                notify_trade_offer_enabled INTEGER NOT NULL DEFAULT 1,
                notify_trade_comment_enabled INTEGER NOT NULL DEFAULT 1,
                notify_trade_counter_enabled INTEGER NOT NULL DEFAULT 1,
                notify_trade_status_enabled INTEGER NOT NULL DEFAULT 1,
                notify_import_complete_enabled INTEGER NOT NULL DEFAULT 1,
                notify_admin_notice_enabled INTEGER NOT NULL DEFAULT 1,
                email_trade_notifications_enabled INTEGER NOT NULL DEFAULT 0,
                email_trade_offer_enabled INTEGER NOT NULL DEFAULT 1,
                email_trade_comment_enabled INTEGER NOT NULL DEFAULT 1,
                email_trade_counter_enabled INTEGER NOT NULL DEFAULT 1,
                email_trade_status_enabled INTEGER NOT NULL DEFAULT 1,
                email_price_alert_enabled INTEGER NOT NULL DEFAULT 0,
                email_import_complete_enabled INTEGER NOT NULL DEFAULT 0,
                email_admin_notice_enabled INTEGER NOT NULL DEFAULT 0,
                totp_secret TEXT NOT NULL DEFAULT '',
                totp_enabled INTEGER NOT NULL DEFAULT 0,
                totp_recovery_codes TEXT NOT NULL DEFAULT '',
                totp_enabled_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS two_factor_challenges (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS passkey_credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                credential_id TEXT NOT NULL UNIQUE,
                public_key_cose TEXT NOT NULL,
                public_key_x TEXT NOT NULL,
                public_key_y TEXT NOT NULL,
                sign_count INTEGER NOT NULL DEFAULT 0,
                nickname TEXT NOT NULL DEFAULT '',
                aaguid TEXT NOT NULL DEFAULT '',
                transports TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                last_used_at TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_passkey_credentials_user
                ON passkey_credentials(user_id, created_at);

            CREATE TABLE IF NOT EXISTS passkey_challenges (
                token TEXT PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                challenge TEXT NOT NULL,
                challenge_type TEXT NOT NULL,
                rp_id TEXT NOT NULL,
                origin TEXT NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_passkey_challenges_user
                ON passkey_challenges(user_id, challenge_type, expires_at);

            CREATE TABLE IF NOT EXISTS collection_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                game TEXT NOT NULL DEFAULT 'mtg',
                card_name TEXT NOT NULL,
                set_name TEXT NOT NULL DEFAULT '',
                set_code TEXT NOT NULL DEFAULT '',
                collector_number TEXT NOT NULL DEFAULT '',
                finish TEXT NOT NULL DEFAULT 'Regular',
                condition TEXT NOT NULL DEFAULT 'NM',
                language TEXT NOT NULL DEFAULT 'English',
                quantity INTEGER NOT NULL DEFAULT 1,
                quantity_for_trade INTEGER NOT NULL DEFAULT 0,
                scryfall_id TEXT NOT NULL DEFAULT '',
                image_url TEXT NOT NULL DEFAULT '',
                mana_cost TEXT NOT NULL DEFAULT '',
                type_line TEXT NOT NULL DEFAULT '',
                oracle_text TEXT NOT NULL DEFAULT '',
                rarity TEXT NOT NULL DEFAULT '',
                colors TEXT NOT NULL DEFAULT '',
                color_identity TEXT NOT NULL DEFAULT '',
                scryfall_uri TEXT NOT NULL DEFAULT '',
                price_usd TEXT NOT NULL DEFAULT '',
                price_source TEXT NOT NULL DEFAULT '',
                tcgplayer_product_id TEXT NOT NULL DEFAULT '',
                cardmarket_product_id TEXT NOT NULL DEFAULT '',
                cardkingdom_sku TEXT NOT NULL DEFAULT '',
                price_refreshed_at TEXT NOT NULL DEFAULT '',
                price_status TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                is_public INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS want_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                game TEXT NOT NULL DEFAULT 'mtg',
                card_name TEXT NOT NULL,
                set_name TEXT NOT NULL DEFAULT '',
                set_code TEXT NOT NULL DEFAULT '',
                collector_number TEXT NOT NULL DEFAULT '',
                desired_quantity INTEGER NOT NULL DEFAULT 1,
                condition TEXT NOT NULL DEFAULT '',
                finish TEXT NOT NULL DEFAULT '',
                language TEXT NOT NULL DEFAULT '',
                scryfall_id TEXT NOT NULL DEFAULT '',
                image_url TEXT NOT NULL DEFAULT '',
                mana_cost TEXT NOT NULL DEFAULT '',
                type_line TEXT NOT NULL DEFAULT '',
                oracle_text TEXT NOT NULL DEFAULT '',
                rarity TEXT NOT NULL DEFAULT '',
                colors TEXT NOT NULL DEFAULT '',
                color_identity TEXT NOT NULL DEFAULT '',
                scryfall_uri TEXT NOT NULL DEFAULT '',
                price_usd TEXT NOT NULL DEFAULT '',
                price_source TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                is_public INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS card_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                group_type TEXT NOT NULL CHECK (group_type IN ('deck', 'binder', 'wishlist')),
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                is_public INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_card_groups_user
                ON card_groups(user_id, group_type, name);

            CREATE TABLE IF NOT EXISTS group_collection_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL REFERENCES card_groups(id) ON DELETE CASCADE,
                collection_item_id INTEGER NOT NULL REFERENCES collection_items(id) ON DELETE CASCADE,
                quantity INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(group_id, collection_item_id)
            );

            CREATE INDEX IF NOT EXISTS idx_group_collection_items_group
                ON group_collection_items(group_id);
            CREATE INDEX IF NOT EXISTS idx_group_collection_items_item
                ON group_collection_items(collection_item_id);

            CREATE TABLE IF NOT EXISTS group_want_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id INTEGER NOT NULL REFERENCES card_groups(id) ON DELETE CASCADE,
                want_item_id INTEGER NOT NULL REFERENCES want_items(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(group_id, want_item_id)
            );

            CREATE INDEX IF NOT EXISTS idx_group_want_items_group
                ON group_want_items(group_id);
            CREATE INDEX IF NOT EXISTS idx_group_want_items_item
                ON group_want_items(want_item_id);

            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'pending',
                proposer_note TEXT NOT NULL DEFAULT '',
                response_note TEXT NOT NULL DEFAULT '',
                price_source_preference TEXT NOT NULL DEFAULT '',
                countered_from_trade_id INTEGER REFERENCES trades(id) ON DELETE SET NULL,
                counter_trade_id INTEGER REFERENCES trades(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK (proposer_id != recipient_id)
            );

            CREATE TABLE IF NOT EXISTS trade_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
                owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                collection_item_id INTEGER REFERENCES collection_items(id) ON DELETE SET NULL,
                card_name TEXT NOT NULL,
                set_name TEXT NOT NULL DEFAULT '',
                quantity INTEGER NOT NULL DEFAULT 1,
                condition TEXT NOT NULL DEFAULT '',
                finish TEXT NOT NULL DEFAULT '',
                price_usd TEXT NOT NULL DEFAULT '',
                price_source TEXT NOT NULL DEFAULT '',
                side TEXT NOT NULL CHECK (side IN ('offered', 'requested'))
            );

            CREATE TABLE IF NOT EXISTS trade_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                body TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_trade_comments_trade
                ON trade_comments(trade_id, created_at);

            CREATE TABLE IF NOT EXISTS trade_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
                reviewer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                reviewee_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
                body TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT '',
                UNIQUE(trade_id, reviewer_id),
                CHECK (reviewer_id != reviewee_id)
            );

            CREATE INDEX IF NOT EXISTS idx_trade_feedback_reviewee
                ON trade_feedback(reviewee_id, updated_at);
            CREATE INDEX IF NOT EXISTS idx_trade_feedback_trade
                ON trade_feedback(trade_id);

            CREATE TABLE IF NOT EXISTS trade_disputes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
                reporter_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                category TEXT NOT NULL DEFAULT 'other',
                status TEXT NOT NULL DEFAULT 'open',
                body TEXT NOT NULL,
                admin_note TEXT NOT NULL DEFAULT '',
                resolution_note TEXT NOT NULL DEFAULT '',
                resolved_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                resolved_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_trade_disputes_trade
                ON trade_disputes(trade_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_trade_disputes_status
                ON trade_disputes(status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_trade_disputes_reporter
                ON trade_disputes(reporter_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_trade_disputes_category_status
                ON trade_disputes(category, status, created_at);

            CREATE TABLE IF NOT EXISTS trade_dispute_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dispute_id INTEGER NOT NULL REFERENCES trade_disputes(id) ON DELETE CASCADE,
                uploaded_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                original_filename TEXT NOT NULL,
                content_type TEXT NOT NULL,
                file_size INTEGER NOT NULL DEFAULT 0,
                checksum_sha256 TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                content BLOB NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_trade_dispute_evidence_dispute
                ON trade_dispute_evidence(dispute_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_trade_dispute_evidence_uploader
                ON trade_dispute_evidence(uploaded_by_user_id, created_at);

            CREATE TABLE IF NOT EXISTS import_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                group_id INTEGER NOT NULL DEFAULT 0,
                import_type TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'preview',
                summary_json TEXT NOT NULL DEFAULT '{}',
                payload_json TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                undone_at TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_import_batches_user
                ON import_batches(user_id, import_type, status, created_at);
            CREATE INDEX IF NOT EXISTS idx_import_batches_group
                ON import_batches(group_id, import_type, status, created_at);

            CREATE TABLE IF NOT EXISTS import_batch_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
                item_type TEXT NOT NULL,
                action TEXT NOT NULL,
                target_table TEXT NOT NULL,
                target_id INTEGER NOT NULL DEFAULT 0,
                previous_state TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_import_batch_items_batch
                ON import_batch_items(batch_id, id);

            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection_item_id INTEGER REFERENCES collection_items(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                card_name TEXT NOT NULL,
                set_name TEXT NOT NULL DEFAULT '',
                set_code TEXT NOT NULL DEFAULT '',
                collector_number TEXT NOT NULL DEFAULT '',
                price_usd TEXT NOT NULL,
                price_source TEXT NOT NULL DEFAULT 'scryfall',
                previous_price_usd TEXT NOT NULL DEFAULT '',
                change_amount TEXT NOT NULL DEFAULT '',
                change_percent TEXT NOT NULL DEFAULT '',
                observed_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_price_history_item
                ON price_history(collection_item_id, observed_at);
            CREATE INDEX IF NOT EXISTS idx_price_history_user
                ON price_history(user_id, observed_at);

            CREATE TABLE IF NOT EXISTS user_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                related_trade_id INTEGER REFERENCES trades(id) ON DELETE SET NULL,
                is_read INTEGER NOT NULL DEFAULT 0,
                email_status TEXT NOT NULL DEFAULT '',
                email_sent_at TEXT NOT NULL DEFAULT '',
                email_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_user_notifications_user
                ON user_notifications(user_id, is_read, created_at);
            CREATE INDEX IF NOT EXISTS idx_user_notifications_trade
                ON user_notifications(related_trade_id);

            CREATE TABLE IF NOT EXISTS api_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                token_hint TEXT NOT NULL DEFAULT '',
                scopes TEXT NOT NULL DEFAULT 'read',
                last_used_at TEXT NOT NULL DEFAULT '',
                expires_at TEXT NOT NULL DEFAULT '',
                revoked_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_api_tokens_user
                ON api_tokens(user_id, revoked_at, created_at);

            CREATE TABLE IF NOT EXISTS webhook_endpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                secret TEXT NOT NULL,
                event_types TEXT NOT NULL DEFAULT 'notification.created',
                is_active INTEGER NOT NULL DEFAULT 1,
                last_success_at TEXT NOT NULL DEFAULT '',
                last_failure_at TEXT NOT NULL DEFAULT '',
                last_error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_webhook_endpoints_user
                ON webhook_endpoints(user_id, is_active, created_at);

            CREATE TABLE IF NOT EXISTS webhook_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                webhook_id INTEGER NOT NULL REFERENCES webhook_endpoints(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                http_status INTEGER NOT NULL DEFAULT 0,
                response_body TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                completed_at TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_status
                ON webhook_deliveries(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_user
                ON webhook_deliveries(user_id, created_at);

            CREATE TABLE IF NOT EXISTS admin_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                target_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                target_type TEXT NOT NULL DEFAULT '',
                target_label TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                request_ip TEXT NOT NULL DEFAULT '',
                user_agent TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_admin_audit_log_created
                ON admin_audit_log(created_at);
            CREATE INDEX IF NOT EXISTS idx_admin_audit_log_action
                ON admin_audit_log(action, created_at);
            CREATE INDEX IF NOT EXISTS idx_admin_audit_log_admin
                ON admin_audit_log(admin_user_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_admin_audit_log_target
                ON admin_audit_log(target_user_id, created_at);

            CREATE TABLE IF NOT EXISTS scryfall_cache (
                lookup_key TEXT PRIMARY KEY,
                scryfall_id TEXT NOT NULL DEFAULT '',
                card_name TEXT NOT NULL DEFAULT '',
                set_name TEXT NOT NULL DEFAULT '',
                set_code TEXT NOT NULL DEFAULT '',
                collector_number TEXT NOT NULL DEFAULT '',
                image_url TEXT NOT NULL DEFAULT '',
                mana_cost TEXT NOT NULL DEFAULT '',
                type_line TEXT NOT NULL DEFAULT '',
                oracle_text TEXT NOT NULL DEFAULT '',
                rarity TEXT NOT NULL DEFAULT '',
                colors TEXT NOT NULL DEFAULT '',
                color_identity TEXT NOT NULL DEFAULT '',
                scryfall_uri TEXT NOT NULL DEFAULT '',
                price_usd TEXT NOT NULL DEFAULT '',
                tcgplayer_product_id TEXT NOT NULL DEFAULT '',
                cardmarket_product_id TEXT NOT NULL DEFAULT '',
                raw_json TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS registration_invites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                token_hint TEXT NOT NULL DEFAULT '',
                created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                accepted_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                sent_at TEXT NOT NULL DEFAULT '',
                accepted_at TEXT NOT NULL DEFAULT '',
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_registration_invites_email
                ON registration_invites(email);
            CREATE INDEX IF NOT EXISTS idx_registration_invites_status
                ON registration_invites(status, expires_at);

            CREATE TABLE IF NOT EXISTS scryfall_bulk_cards (
                scryfall_id TEXT PRIMARY KEY,
                card_name TEXT NOT NULL,
                search_name TEXT NOT NULL DEFAULT '',
                set_name TEXT NOT NULL DEFAULT '',
                set_code TEXT NOT NULL DEFAULT '',
                collector_number TEXT NOT NULL DEFAULT '',
                released_at TEXT NOT NULL DEFAULT '',
                image_url TEXT NOT NULL DEFAULT '',
                mana_cost TEXT NOT NULL DEFAULT '',
                type_line TEXT NOT NULL DEFAULT '',
                oracle_text TEXT NOT NULL DEFAULT '',
                rarity TEXT NOT NULL DEFAULT '',
                colors TEXT NOT NULL DEFAULT '',
                color_identity TEXT NOT NULL DEFAULT '',
                scryfall_uri TEXT NOT NULL DEFAULT '',
                price_usd TEXT NOT NULL DEFAULT '',
                tcgplayer_product_id TEXT NOT NULL DEFAULT '',
                cardmarket_product_id TEXT NOT NULL DEFAULT '',
                finishes TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_scryfall_bulk_cards_name
                ON scryfall_bulk_cards(search_name);
            CREATE INDEX IF NOT EXISTS idx_scryfall_bulk_cards_print
                ON scryfall_bulk_cards(set_code, collector_number);

            CREATE TABLE IF NOT EXISTS scryfall_enrichment_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection_item_id INTEGER NOT NULL UNIQUE REFERENCES collection_items(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                lookup_key TEXT NOT NULL,
                card_name TEXT NOT NULL,
                set_code TEXT NOT NULL DEFAULT '',
                collector_number TEXT NOT NULL DEFAULT '',
                scryfall_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                available_at TEXT NOT NULL DEFAULT '',
                completion_notified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_scryfall_enrichment_jobs_status
                ON scryfall_enrichment_jobs(status, available_at, created_at);

            CREATE TABLE IF NOT EXISTS card_price_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection_item_id INTEGER NOT NULL REFERENCES collection_items(id) ON DELETE CASCADE,
                provider TEXT NOT NULL,
                external_id TEXT NOT NULL DEFAULT '',
                price_usd TEXT NOT NULL DEFAULT '',
                price_label TEXT NOT NULL DEFAULT '',
                raw_json TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL,
                UNIQUE(collection_item_id, provider)
            );

            CREATE INDEX IF NOT EXISTS idx_card_price_sources_item
                ON card_price_sources(collection_item_id);

            CREATE TABLE IF NOT EXISTS price_refresh_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                collection_item_id INTEGER NOT NULL REFERENCES collection_items(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                provider TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                available_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(collection_item_id, provider)
            );

            CREATE INDEX IF NOT EXISTS idx_price_refresh_jobs_status
                ON price_refresh_jobs(status, available_at, created_at);
            """
        )
        migrate_db(conn)

def migrate_db(conn):
    user_columns = {column["name"] for column in conn.execute("PRAGMA table_info(users)").fetchall()}
    user_missing_columns = {
        "email": "TEXT NOT NULL DEFAULT ''",
        "public_email": "INTEGER NOT NULL DEFAULT 0",
        "is_admin": "INTEGER NOT NULL DEFAULT 0",
        "is_banned": "INTEGER NOT NULL DEFAULT 0",
        "trusted_override": "INTEGER NOT NULL DEFAULT 0",
        "ban_reason": "TEXT NOT NULL DEFAULT ''",
        "admin_notes": "TEXT NOT NULL DEFAULT ''",
        "banned_at": "TEXT NOT NULL DEFAULT ''",
        "preferred_price_source": "TEXT NOT NULL DEFAULT ''",
        "price_alerts_enabled": "INTEGER NOT NULL DEFAULT 1",
        "price_alert_threshold_percent": "TEXT NOT NULL DEFAULT '0'",
        "watchlist_alerts_enabled": "INTEGER NOT NULL DEFAULT 1",
        "notify_trade_offer_enabled": "INTEGER NOT NULL DEFAULT 1",
        "notify_trade_comment_enabled": "INTEGER NOT NULL DEFAULT 1",
        "notify_trade_counter_enabled": "INTEGER NOT NULL DEFAULT 1",
        "notify_trade_status_enabled": "INTEGER NOT NULL DEFAULT 1",
        "notify_import_complete_enabled": "INTEGER NOT NULL DEFAULT 1",
        "notify_admin_notice_enabled": "INTEGER NOT NULL DEFAULT 1",
        "email_trade_notifications_enabled": "INTEGER NOT NULL DEFAULT 0",
        "email_trade_offer_enabled": "INTEGER NOT NULL DEFAULT 1",
        "email_trade_comment_enabled": "INTEGER NOT NULL DEFAULT 1",
        "email_trade_counter_enabled": "INTEGER NOT NULL DEFAULT 1",
        "email_trade_status_enabled": "INTEGER NOT NULL DEFAULT 1",
        "email_price_alert_enabled": "INTEGER NOT NULL DEFAULT 0",
        "email_import_complete_enabled": "INTEGER NOT NULL DEFAULT 0",
        "email_admin_notice_enabled": "INTEGER NOT NULL DEFAULT 0",
        "totp_secret": "TEXT NOT NULL DEFAULT ''",
        "totp_enabled": "INTEGER NOT NULL DEFAULT 0",
        "totp_recovery_codes": "TEXT NOT NULL DEFAULT ''",
        "totp_enabled_at": "TEXT NOT NULL DEFAULT ''",
        "updated_at": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in user_missing_columns.items():
        if name not in user_columns:
            conn.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")
    admin_count = conn.execute("SELECT COUNT(*) AS count FROM users WHERE is_admin = 1").fetchone()["count"]
    user_count = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
    if user_count and not admin_count:
        first_user = conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
        conn.execute("UPDATE users SET is_admin = 1, updated_at = ? WHERE id = ?", (now_iso(), first_user["id"]))
    timestamp = now_iso()
    conn.execute("UPDATE users SET preferred_price_source = 'scryfall', updated_at = ? WHERE preferred_price_source != 'scryfall'", (timestamp,))

    collection_columns = {column["name"] for column in conn.execute("PRAGMA table_info(collection_items)").fetchall()}
    missing_columns = {
        "set_code": "TEXT NOT NULL DEFAULT ''",
        "scryfall_id": "TEXT NOT NULL DEFAULT ''",
        "image_url": "TEXT NOT NULL DEFAULT ''",
        "mana_cost": "TEXT NOT NULL DEFAULT ''",
        "type_line": "TEXT NOT NULL DEFAULT ''",
        "oracle_text": "TEXT NOT NULL DEFAULT ''",
        "rarity": "TEXT NOT NULL DEFAULT ''",
        "colors": "TEXT NOT NULL DEFAULT ''",
        "color_identity": "TEXT NOT NULL DEFAULT ''",
        "scryfall_uri": "TEXT NOT NULL DEFAULT ''",
        "price_usd": "TEXT NOT NULL DEFAULT ''",
        "price_source": "TEXT NOT NULL DEFAULT ''",
        "tcgplayer_product_id": "TEXT NOT NULL DEFAULT ''",
        "cardmarket_product_id": "TEXT NOT NULL DEFAULT ''",
        "cardkingdom_sku": "TEXT NOT NULL DEFAULT ''",
        "price_refreshed_at": "TEXT NOT NULL DEFAULT ''",
        "price_status": "TEXT NOT NULL DEFAULT ''",
        "is_public": "INTEGER NOT NULL DEFAULT 1",
    }
    for name, definition in missing_columns.items():
        if name not in collection_columns:
            conn.execute(f"ALTER TABLE collection_items ADD COLUMN {name} {definition}")
    conn.execute(
        """
        UPDATE collection_items
        SET price_source = 'scryfall'
        WHERE price_source = '' AND price_usd != '' AND (scryfall_id != '' OR scryfall_uri != '')
        """
    )
    conn.execute(
        """
        UPDATE collection_items
        SET price_source = 'scryfall',
            price_status = '',
            updated_at = ?
        WHERE price_source != '' AND price_source != 'scryfall'
        """,
        (timestamp,),
    )

    want_columns = {column["name"] for column in conn.execute("PRAGMA table_info(want_items)").fetchall()}
    want_missing_columns = {
        "set_code": "TEXT NOT NULL DEFAULT ''",
        "collector_number": "TEXT NOT NULL DEFAULT ''",
        "condition": "TEXT NOT NULL DEFAULT ''",
        "finish": "TEXT NOT NULL DEFAULT ''",
        "language": "TEXT NOT NULL DEFAULT ''",
        "scryfall_id": "TEXT NOT NULL DEFAULT ''",
        "image_url": "TEXT NOT NULL DEFAULT ''",
        "mana_cost": "TEXT NOT NULL DEFAULT ''",
        "type_line": "TEXT NOT NULL DEFAULT ''",
        "oracle_text": "TEXT NOT NULL DEFAULT ''",
        "rarity": "TEXT NOT NULL DEFAULT ''",
        "colors": "TEXT NOT NULL DEFAULT ''",
        "color_identity": "TEXT NOT NULL DEFAULT ''",
        "scryfall_uri": "TEXT NOT NULL DEFAULT ''",
        "price_usd": "TEXT NOT NULL DEFAULT ''",
        "price_source": "TEXT NOT NULL DEFAULT ''",
        "is_public": "INTEGER NOT NULL DEFAULT 1",
    }
    for name, definition in want_missing_columns.items():
        if name not in want_columns:
            conn.execute(f"ALTER TABLE want_items ADD COLUMN {name} {definition}")
    conn.execute(
        """
        UPDATE want_items
        SET price_source = 'scryfall'
        WHERE price_source = '' AND price_usd != '' AND (scryfall_id != '' OR scryfall_uri != '')
        """
    )
    conn.execute(
        """
        UPDATE want_items
        SET price_source = 'scryfall',
            updated_at = ?
        WHERE price_source != '' AND price_source != 'scryfall'
        """,
        (timestamp,),
    )

    group_columns = {column["name"] for column in conn.execute("PRAGMA table_info(card_groups)").fetchall()}
    group_missing_columns = {
        "is_public": "INTEGER NOT NULL DEFAULT 1",
    }
    for name, definition in group_missing_columns.items():
        if name not in group_columns:
            conn.execute(f"ALTER TABLE card_groups ADD COLUMN {name} {definition}")

    trade_columns = {column["name"] for column in conn.execute("PRAGMA table_info(trades)").fetchall()}
    trade_missing_columns = {
        "price_source_preference": "TEXT NOT NULL DEFAULT ''",
        "countered_from_trade_id": "INTEGER REFERENCES trades(id) ON DELETE SET NULL",
        "counter_trade_id": "INTEGER REFERENCES trades(id) ON DELETE SET NULL",
    }
    for name, definition in trade_missing_columns.items():
        if name not in trade_columns:
            conn.execute(f"ALTER TABLE trades ADD COLUMN {name} {definition}")

    trade_item_columns = {column["name"] for column in conn.execute("PRAGMA table_info(trade_items)").fetchall()}
    trade_item_missing_columns = {
        "price_usd": "TEXT NOT NULL DEFAULT ''",
        "price_source": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in trade_item_missing_columns.items():
        if name not in trade_item_columns:
            conn.execute(f"ALTER TABLE trade_items ADD COLUMN {name} {definition}")
    conn.execute("UPDATE trades SET price_source_preference = 'scryfall', updated_at = ? WHERE price_source_preference != 'scryfall'", (timestamp,))
    conn.execute("UPDATE trade_items SET price_source = 'scryfall' WHERE price_source != '' AND price_source != 'scryfall'")

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS trade_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_trade_comments_trade
            ON trade_comments(trade_id, created_at);

        CREATE TABLE IF NOT EXISTS trade_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
            reviewer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            reviewee_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
            body TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT '',
            UNIQUE(trade_id, reviewer_id),
            CHECK (reviewer_id != reviewee_id)
        );

        CREATE INDEX IF NOT EXISTS idx_trade_feedback_reviewee
            ON trade_feedback(reviewee_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_trade_feedback_trade
            ON trade_feedback(trade_id);

        CREATE TABLE IF NOT EXISTS trade_disputes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
            reporter_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            category TEXT NOT NULL DEFAULT 'other',
            status TEXT NOT NULL DEFAULT 'open',
            body TEXT NOT NULL,
            admin_note TEXT NOT NULL DEFAULT '',
            resolution_note TEXT NOT NULL DEFAULT '',
            resolved_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            resolved_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_trade_disputes_trade
            ON trade_disputes(trade_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_trade_disputes_status
            ON trade_disputes(status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_trade_disputes_reporter
            ON trade_disputes(reporter_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_trade_disputes_category_status
            ON trade_disputes(category, status, created_at);

        CREATE TABLE IF NOT EXISTS trade_dispute_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dispute_id INTEGER NOT NULL REFERENCES trade_disputes(id) ON DELETE CASCADE,
            uploaded_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            original_filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            file_size INTEGER NOT NULL DEFAULT 0,
            checksum_sha256 TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            content BLOB NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_trade_dispute_evidence_dispute
            ON trade_dispute_evidence(dispute_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_trade_dispute_evidence_uploader
            ON trade_dispute_evidence(uploaded_by_user_id, created_at);

        CREATE TABLE IF NOT EXISTS import_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            group_id INTEGER NOT NULL DEFAULT 0,
            import_type TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'preview',
            summary_json TEXT NOT NULL DEFAULT '{}',
            payload_json TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            undone_at TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_import_batches_user
            ON import_batches(user_id, import_type, status, created_at);
        CREATE INDEX IF NOT EXISTS idx_import_batches_group
            ON import_batches(group_id, import_type, status, created_at);

        CREATE TABLE IF NOT EXISTS import_batch_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
            item_type TEXT NOT NULL,
            action TEXT NOT NULL,
            target_table TEXT NOT NULL,
            target_id INTEGER NOT NULL DEFAULT 0,
            previous_state TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_import_batch_items_batch
            ON import_batch_items(batch_id, id);

        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection_item_id INTEGER REFERENCES collection_items(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            card_name TEXT NOT NULL,
            set_name TEXT NOT NULL DEFAULT '',
            set_code TEXT NOT NULL DEFAULT '',
            collector_number TEXT NOT NULL DEFAULT '',
            price_usd TEXT NOT NULL,
            price_source TEXT NOT NULL DEFAULT 'scryfall',
            previous_price_usd TEXT NOT NULL DEFAULT '',
            change_amount TEXT NOT NULL DEFAULT '',
            change_percent TEXT NOT NULL DEFAULT '',
            observed_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_price_history_item
            ON price_history(collection_item_id, observed_at);
        CREATE INDEX IF NOT EXISTS idx_price_history_user
            ON price_history(user_id, observed_at);

        CREATE TABLE IF NOT EXISTS user_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            related_trade_id INTEGER REFERENCES trades(id) ON DELETE SET NULL,
            is_read INTEGER NOT NULL DEFAULT 0,
            email_status TEXT NOT NULL DEFAULT '',
            email_sent_at TEXT NOT NULL DEFAULT '',
            email_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_user_notifications_user
            ON user_notifications(user_id, is_read, created_at);
        CREATE INDEX IF NOT EXISTS idx_user_notifications_trade
            ON user_notifications(related_trade_id);

        CREATE TABLE IF NOT EXISTS admin_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            target_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            target_type TEXT NOT NULL DEFAULT '',
            target_label TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            request_ip TEXT NOT NULL DEFAULT '',
            user_agent TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_admin_audit_log_created
            ON admin_audit_log(created_at);
        CREATE INDEX IF NOT EXISTS idx_admin_audit_log_action
            ON admin_audit_log(action, created_at);
        CREATE INDEX IF NOT EXISTS idx_admin_audit_log_admin
            ON admin_audit_log(admin_user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_admin_audit_log_target
            ON admin_audit_log(target_user_id, created_at);
        """
    )
    notification_columns = {column["name"] for column in conn.execute("PRAGMA table_info(user_notifications)").fetchall()}
    notification_missing_columns = {
        "email_status": "TEXT NOT NULL DEFAULT ''",
        "email_sent_at": "TEXT NOT NULL DEFAULT ''",
        "email_error": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in notification_missing_columns.items():
        if name not in notification_columns:
            conn.execute(f"ALTER TABLE user_notifications ADD COLUMN {name} {definition}")
    conn.execute(
        """
        INSERT INTO price_history
            (collection_item_id, user_id, card_name, set_name, set_code, collector_number,
             price_usd, price_source, previous_price_usd, change_amount, change_percent, observed_at)
        SELECT
            collection_items.id,
            collection_items.user_id,
            collection_items.card_name,
            collection_items.set_name,
            collection_items.set_code,
            collection_items.collector_number,
            collection_items.price_usd,
            'scryfall',
            '',
            '',
            '',
            COALESCE(NULLIF(collection_items.price_refreshed_at, ''), NULLIF(collection_items.updated_at, ''), collection_items.created_at, ?)
        FROM collection_items
        WHERE collection_items.price_usd != ''
            AND NOT EXISTS (
                SELECT 1
                FROM price_history
                WHERE price_history.collection_item_id = collection_items.id
            )
        """,
        (timestamp,),
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
        (TRUSTED_TRADE_THRESHOLD_KEY, str(DEFAULT_TRUSTED_TRADE_THRESHOLD)),
    )
    conn.execute(
        "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
        (INVITE_ONLY_REGISTRATION_KEY, "0"),
    )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS registration_invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            token_hint TEXT NOT NULL DEFAULT '',
            created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            accepted_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            sent_at TEXT NOT NULL DEFAULT '',
            accepted_at TEXT NOT NULL DEFAULT '',
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_registration_invites_email
            ON registration_invites(email);
        CREATE INDEX IF NOT EXISTS idx_registration_invites_status
            ON registration_invites(status, expires_at);

        CREATE TABLE IF NOT EXISTS card_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            group_type TEXT NOT NULL CHECK (group_type IN ('deck', 'binder', 'wishlist')),
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_card_groups_user
            ON card_groups(user_id, group_type, name);

        CREATE TABLE IF NOT EXISTS group_collection_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL REFERENCES card_groups(id) ON DELETE CASCADE,
            collection_item_id INTEGER NOT NULL REFERENCES collection_items(id) ON DELETE CASCADE,
            quantity INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(group_id, collection_item_id)
        );

        CREATE INDEX IF NOT EXISTS idx_group_collection_items_group
            ON group_collection_items(group_id);
        CREATE INDEX IF NOT EXISTS idx_group_collection_items_item
            ON group_collection_items(collection_item_id);

        CREATE TABLE IF NOT EXISTS group_want_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL REFERENCES card_groups(id) ON DELETE CASCADE,
            want_item_id INTEGER NOT NULL REFERENCES want_items(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(group_id, want_item_id)
        );

        CREATE INDEX IF NOT EXISTS idx_group_want_items_group
            ON group_want_items(group_id);
        CREATE INDEX IF NOT EXISTS idx_group_want_items_item
            ON group_want_items(want_item_id);
        """
    )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS scryfall_bulk_cards (
            scryfall_id TEXT PRIMARY KEY,
            card_name TEXT NOT NULL,
            search_name TEXT NOT NULL DEFAULT '',
            set_name TEXT NOT NULL DEFAULT '',
            set_code TEXT NOT NULL DEFAULT '',
            collector_number TEXT NOT NULL DEFAULT '',
            released_at TEXT NOT NULL DEFAULT '',
            image_url TEXT NOT NULL DEFAULT '',
            mana_cost TEXT NOT NULL DEFAULT '',
            type_line TEXT NOT NULL DEFAULT '',
            oracle_text TEXT NOT NULL DEFAULT '',
            rarity TEXT NOT NULL DEFAULT '',
            colors TEXT NOT NULL DEFAULT '',
            color_identity TEXT NOT NULL DEFAULT '',
            scryfall_uri TEXT NOT NULL DEFAULT '',
            price_usd TEXT NOT NULL DEFAULT '',
            tcgplayer_product_id TEXT NOT NULL DEFAULT '',
            cardmarket_product_id TEXT NOT NULL DEFAULT '',
            finishes TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_scryfall_bulk_cards_name
            ON scryfall_bulk_cards(search_name);
        CREATE INDEX IF NOT EXISTS idx_scryfall_bulk_cards_print
            ON scryfall_bulk_cards(set_code, collector_number);

        CREATE TABLE IF NOT EXISTS scryfall_enrichment_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection_item_id INTEGER NOT NULL UNIQUE REFERENCES collection_items(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            lookup_key TEXT NOT NULL,
            card_name TEXT NOT NULL,
            set_code TEXT NOT NULL DEFAULT '',
            collector_number TEXT NOT NULL DEFAULT '',
            scryfall_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            available_at TEXT NOT NULL DEFAULT '',
            completion_notified INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_scryfall_enrichment_jobs_status
            ON scryfall_enrichment_jobs(status, available_at, created_at);

        CREATE TABLE IF NOT EXISTS card_price_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection_item_id INTEGER NOT NULL REFERENCES collection_items(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            external_id TEXT NOT NULL DEFAULT '',
            price_usd TEXT NOT NULL DEFAULT '',
            price_label TEXT NOT NULL DEFAULT '',
            raw_json TEXT NOT NULL DEFAULT '',
            fetched_at TEXT NOT NULL,
            UNIQUE(collection_item_id, provider)
        );

        CREATE INDEX IF NOT EXISTS idx_card_price_sources_item
            ON card_price_sources(collection_item_id);

        CREATE TABLE IF NOT EXISTS price_refresh_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection_item_id INTEGER NOT NULL REFERENCES collection_items(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            available_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(collection_item_id, provider)
        );

        CREATE INDEX IF NOT EXISTS idx_price_refresh_jobs_status
            ON price_refresh_jobs(status, available_at, created_at);
        """
    )
    scryfall_cache_columns = {column["name"] for column in conn.execute("PRAGMA table_info(scryfall_cache)").fetchall()}
    for name in ("tcgplayer_product_id", "cardmarket_product_id"):
        if name not in scryfall_cache_columns:
            conn.execute(f"ALTER TABLE scryfall_cache ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
    scryfall_bulk_columns = {column["name"] for column in conn.execute("PRAGMA table_info(scryfall_bulk_cards)").fetchall()}
    for name in ("tcgplayer_product_id", "cardmarket_product_id", "finishes"):
        if name not in scryfall_bulk_columns:
            conn.execute(f"ALTER TABLE scryfall_bulk_cards ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
    scryfall_job_columns = {column["name"] for column in conn.execute("PRAGMA table_info(scryfall_enrichment_jobs)").fetchall()}
    if "completion_notified" not in scryfall_job_columns:
        conn.execute("ALTER TABLE scryfall_enrichment_jobs ADD COLUMN completion_notified INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        """
        UPDATE price_refresh_jobs
        SET status = 'disabled',
            last_error = 'External price refresh has been removed. Scryfall is the only pricing source.',
            available_at = '',
            updated_at = ?
        WHERE status IN ('pending', 'processing')
        """,
        (timestamp,),
    )
    conn.execute("DELETE FROM card_price_sources WHERE provider != 'scryfall'")
    run_schema_migrations(conn)
    conn.execute("PRAGMA optimize")

def rows(query, params=()):
    with db() as conn:
        return conn.execute(query, params).fetchall()

def row(query, params=()):
    with db() as conn:
        return conn.execute(query, params).fetchone()

def execute(query, params=()):
    with db() as conn:
        cursor = conn.execute(query, params)
        return cursor.lastrowid

def get_setting(key, default=""):
    found = row("SELECT value FROM app_settings WHERE key = ?", (key,))
    return found["value"] if found else default

def set_setting(key, value):
    execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (key, str(value)))

__all__ = [
    'now_iso',
    'future_iso',
    'db',
    'init_db',
    'migrate_db',
    'rows',
    'row',
    'execute',
    'get_setting',
    'set_setting',
]
