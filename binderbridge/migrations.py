"""Versioned SQLite schema migrations for BinderBridge."""

from datetime import datetime, timezone

from binderbridge.session_tokens import is_session_token_hash, session_token_hash


SCHEMA_VERSION_KEY = "schema_version"
CURRENT_SCHEMA_VERSION = 15


def db_schema_version(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    found = conn.execute("SELECT value FROM app_settings WHERE key = ?", (SCHEMA_VERSION_KEY,)).fetchone()
    if not found:
        return 0
    try:
        return max(0, int(found["value"]))
    except (TypeError, ValueError):
        return 0


def set_db_schema_version(conn, version):
    conn.execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
        (SCHEMA_VERSION_KEY, str(int(version))),
    )


def migrate_hot_path_indexes(conn):
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_users_browse_name
            ON users(is_banned, display_name COLLATE NOCASE, username COLLATE NOCASE);

        CREATE INDEX IF NOT EXISTS idx_collection_user_name_sort
            ON collection_items(user_id, card_name COLLATE NOCASE, set_name COLLATE NOCASE, collector_number COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_collection_user_set_sort
            ON collection_items(user_id, set_name COLLATE NOCASE, set_code COLLATE NOCASE, collector_number COLLATE NOCASE, card_name COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_collection_user_game_name
            ON collection_items(user_id, game, card_name COLLATE NOCASE, set_name COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_collection_user_set_code_number
            ON collection_items(user_id, set_code COLLATE NOCASE, collector_number COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_collection_user_type_line
            ON collection_items(user_id, type_line COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_collection_user_condition_finish
            ON collection_items(user_id, condition, finish);
        CREATE INDEX IF NOT EXISTS idx_collection_user_updated
            ON collection_items(user_id, updated_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_collection_user_scryfall
            ON collection_items(user_id, scryfall_id)
            WHERE scryfall_id != '';
        CREATE INDEX IF NOT EXISTS idx_collection_user_trade_name
            ON collection_items(user_id, card_name COLLATE NOCASE, set_name COLLATE NOCASE, collector_number COLLATE NOCASE)
            WHERE quantity_for_trade > 0;
        CREATE INDEX IF NOT EXISTS idx_collection_user_public_trade_name
            ON collection_items(user_id, card_name COLLATE NOCASE, set_name COLLATE NOCASE, collector_number COLLATE NOCASE)
            WHERE quantity_for_trade > 0 AND is_public = 1;
        CREATE INDEX IF NOT EXISTS idx_collection_public_trade_name
            ON collection_items(card_name COLLATE NOCASE, set_name COLLATE NOCASE, collector_number COLLATE NOCASE, user_id)
            WHERE quantity_for_trade > 0 AND is_public = 1;
        CREATE INDEX IF NOT EXISTS idx_collection_public_trade_game_name
            ON collection_items(game, card_name COLLATE NOCASE, set_name COLLATE NOCASE, collector_number COLLATE NOCASE, user_id)
            WHERE quantity_for_trade > 0 AND is_public = 1;

        CREATE INDEX IF NOT EXISTS idx_wants_user_name_sort
            ON want_items(user_id, card_name COLLATE NOCASE, set_name COLLATE NOCASE, collector_number COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_wants_user_set_sort
            ON want_items(user_id, set_name COLLATE NOCASE, set_code COLLATE NOCASE, collector_number COLLATE NOCASE, card_name COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_wants_user_updated
            ON want_items(user_id, updated_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_wants_user_scryfall
            ON want_items(user_id, scryfall_id)
            WHERE scryfall_id != '';
        CREATE INDEX IF NOT EXISTS idx_wants_public_name
            ON want_items(game, card_name COLLATE NOCASE, set_code COLLATE NOCASE, collector_number COLLATE NOCASE, user_id)
            WHERE is_public = 1;
        CREATE INDEX IF NOT EXISTS idx_wants_public_scryfall
            ON want_items(scryfall_id, user_id)
            WHERE is_public = 1 AND scryfall_id != '';

        CREATE INDEX IF NOT EXISTS idx_trades_proposer_updated
            ON trades(proposer_id, updated_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_trades_recipient_updated
            ON trades(recipient_id, updated_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_trades_status_updated
            ON trades(status, updated_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_trade_items_trade_side_name
            ON trade_items(trade_id, side, card_name COLLATE NOCASE);

        CREATE INDEX IF NOT EXISTS idx_scryfall_bulk_cards_name_release
            ON scryfall_bulk_cards(search_name, released_at DESC);
        CREATE INDEX IF NOT EXISTS idx_scryfall_bulk_cards_print_release
            ON scryfall_bulk_cards(set_code COLLATE NOCASE, collector_number COLLATE NOCASE, released_at DESC);
        """
    )


def migrate_dispute_moderation(conn):
    dispute_columns = {column["name"] for column in conn.execute("PRAGMA table_info(trade_disputes)").fetchall()}
    if "resolution_note" not in dispute_columns:
        conn.execute("ALTER TABLE trade_disputes ADD COLUMN resolution_note TEXT NOT NULL DEFAULT ''")
    conn.executescript(
        """
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
        """
    )


def migrate_csv_import_mapping_presets(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS csv_import_mapping_presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            import_target TEXT NOT NULL DEFAULT 'collection',
            mapping_json TEXT NOT NULL DEFAULT '{}',
            is_shared INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, import_target, name)
        );

        CREATE INDEX IF NOT EXISTS idx_csv_import_mapping_presets_user
            ON csv_import_mapping_presets(user_id, import_target, name);
        CREATE INDEX IF NOT EXISTS idx_csv_import_mapping_presets_shared
            ON csv_import_mapping_presets(is_shared, import_target, name);
        """
    )


def migrate_user_roles(conn):
    user_columns = {column["name"] for column in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "role" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'member'")
    conn.execute("UPDATE users SET role = 'owner' WHERE is_admin = 1 AND role IN ('', 'member')")
    conn.execute("UPDATE users SET role = 'member' WHERE role NOT IN ('owner', 'admin', 'moderator', 'organizer', 'member', 'read_only')")
    conn.execute("UPDATE users SET is_admin = CASE WHEN role IN ('owner', 'admin') THEN 1 ELSE 0 END")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_users_role_status
        ON users(role, is_banned, display_name COLLATE NOCASE)
        """
    )


def migrate_granular_privacy(conn):
    for table in ("collection_items", "want_items", "card_groups"):
        columns = {column["name"] for column in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if "visibility" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN visibility TEXT NOT NULL DEFAULT 'members'")
        conn.execute(
            f"UPDATE {table} SET visibility = CASE WHEN is_public = 1 THEN 'members' ELSE 'private' END "
            "WHERE visibility NOT IN ('private', 'trusted', 'members', 'link') OR visibility = '' "
            "OR (visibility = 'members' AND is_public = 0)"
        )
        conn.execute(f"UPDATE {table} SET is_public = CASE WHEN visibility = 'members' THEN 1 ELSE 0 END")
    user_columns = {column["name"] for column in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "collection_value_visibility" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN collection_value_visibility TEXT NOT NULL DEFAULT 'members'")
    group_columns = {column["name"] for column in conn.execute("PRAGMA table_info(card_groups)").fetchall()}
    for name, definition in {
        "default_item_visibility": "TEXT NOT NULL DEFAULT 'members'",
        "show_values": "INTEGER NOT NULL DEFAULT 1",
        "show_photos": "INTEGER NOT NULL DEFAULT 1",
    }.items():
        if name not in group_columns:
            conn.execute(f"ALTER TABLE card_groups ADD COLUMN {name} {definition}")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS privacy_share_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            target_type TEXT NOT NULL CHECK (target_type IN ('group')),
            target_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            token_hint TEXT NOT NULL DEFAULT '',
            label TEXT NOT NULL DEFAULT '',
            show_values INTEGER NOT NULL DEFAULT 0,
            show_photos INTEGER NOT NULL DEFAULT 1,
            expires_at TEXT NOT NULL DEFAULT '',
            revoked_at TEXT NOT NULL DEFAULT '',
            last_accessed_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_privacy_share_links_target
            ON privacy_share_links(user_id, target_type, target_id, revoked_at);
        CREATE INDEX IF NOT EXISTS idx_collection_visibility_trade
            ON collection_items(visibility, user_id, card_name COLLATE NOCASE)
            WHERE quantity_for_trade > 0;
        CREATE INDEX IF NOT EXISTS idx_wants_visibility_name
            ON want_items(visibility, user_id, card_name COLLATE NOCASE);
        CREATE INDEX IF NOT EXISTS idx_groups_visibility_name
            ON card_groups(visibility, user_id, group_type, name COLLATE NOCASE);

        CREATE TRIGGER IF NOT EXISTS trg_collection_privacy_insert
        AFTER INSERT ON collection_items WHEN NEW.visibility = 'members' AND NEW.is_public = 0
        BEGIN UPDATE collection_items SET visibility = 'private' WHERE id = NEW.id; END;
        CREATE TRIGGER IF NOT EXISTS trg_collection_privacy_legacy_update
        AFTER UPDATE OF is_public ON collection_items
        WHEN NEW.is_public != OLD.is_public AND NEW.visibility = OLD.visibility
        BEGIN UPDATE collection_items SET visibility = CASE WHEN NEW.is_public = 1 THEN 'members' ELSE 'private' END WHERE id = NEW.id; END;
        CREATE TRIGGER IF NOT EXISTS trg_collection_privacy_visibility_update
        AFTER UPDATE OF visibility ON collection_items WHEN NEW.visibility != OLD.visibility
        BEGIN UPDATE collection_items SET is_public = CASE WHEN NEW.visibility = 'members' THEN 1 ELSE 0 END WHERE id = NEW.id; END;

        CREATE TRIGGER IF NOT EXISTS trg_wants_privacy_insert
        AFTER INSERT ON want_items WHEN NEW.visibility = 'members' AND NEW.is_public = 0
        BEGIN UPDATE want_items SET visibility = 'private' WHERE id = NEW.id; END;
        CREATE TRIGGER IF NOT EXISTS trg_wants_privacy_legacy_update
        AFTER UPDATE OF is_public ON want_items
        WHEN NEW.is_public != OLD.is_public AND NEW.visibility = OLD.visibility
        BEGIN UPDATE want_items SET visibility = CASE WHEN NEW.is_public = 1 THEN 'members' ELSE 'private' END WHERE id = NEW.id; END;
        CREATE TRIGGER IF NOT EXISTS trg_wants_privacy_visibility_update
        AFTER UPDATE OF visibility ON want_items WHEN NEW.visibility != OLD.visibility
        BEGIN UPDATE want_items SET is_public = CASE WHEN NEW.visibility = 'members' THEN 1 ELSE 0 END WHERE id = NEW.id; END;

        CREATE TRIGGER IF NOT EXISTS trg_groups_privacy_insert
        AFTER INSERT ON card_groups WHEN NEW.visibility = 'members' AND NEW.is_public = 0
        BEGIN UPDATE card_groups SET visibility = 'private' WHERE id = NEW.id; END;
        CREATE TRIGGER IF NOT EXISTS trg_groups_privacy_legacy_update
        AFTER UPDATE OF is_public ON card_groups
        WHEN NEW.is_public != OLD.is_public AND NEW.visibility = OLD.visibility
        BEGIN UPDATE card_groups SET visibility = CASE WHEN NEW.is_public = 1 THEN 'members' ELSE 'private' END WHERE id = NEW.id; END;
        CREATE TRIGGER IF NOT EXISTS trg_groups_privacy_visibility_update
        AFTER UPDATE OF visibility ON card_groups WHEN NEW.visibility != OLD.visibility
        BEGIN UPDATE card_groups SET is_public = CASE WHEN NEW.visibility = 'members' THEN 1 ELSE 0 END WHERE id = NEW.id; END;
        """
    )


def migrate_collection_share_links(conn):
    conn.executescript(
        """
        ALTER TABLE privacy_share_links RENAME TO privacy_share_links_legacy;
        CREATE TABLE privacy_share_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            target_type TEXT NOT NULL CHECK (target_type IN ('group', 'collection')),
            target_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            token_hint TEXT NOT NULL DEFAULT '',
            label TEXT NOT NULL DEFAULT '',
            show_values INTEGER NOT NULL DEFAULT 0,
            show_photos INTEGER NOT NULL DEFAULT 1,
            expires_at TEXT NOT NULL DEFAULT '',
            revoked_at TEXT NOT NULL DEFAULT '',
            last_accessed_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        INSERT INTO privacy_share_links
            (id, user_id, target_type, target_id, token_hash, token_hint, label, show_values, show_photos,
             expires_at, revoked_at, last_accessed_at, created_at)
        SELECT id, user_id, target_type, target_id, token_hash, token_hint, label, show_values, show_photos,
             expires_at, revoked_at, last_accessed_at, created_at
        FROM privacy_share_links_legacy;
        DROP TABLE privacy_share_links_legacy;
        CREATE INDEX idx_privacy_share_links_target
            ON privacy_share_links(user_id, target_type, target_id, revoked_at);
        CREATE TRIGGER IF NOT EXISTS trg_collection_share_links_delete
        AFTER DELETE ON collection_items
        BEGIN DELETE FROM privacy_share_links WHERE target_type = 'collection' AND target_id = OLD.id; END;
        CREATE TRIGGER IF NOT EXISTS trg_group_share_links_delete
        AFTER DELETE ON card_groups
        BEGIN DELETE FROM privacy_share_links WHERE target_type = 'group' AND target_id = OLD.id; END;
        """
    )


def migrate_want_share_links(conn):
    conn.executescript(
        """
        DROP TRIGGER IF EXISTS trg_collection_share_links_delete;
        DROP TRIGGER IF EXISTS trg_group_share_links_delete;
        DROP TRIGGER IF EXISTS trg_want_share_links_delete;
        ALTER TABLE privacy_share_links RENAME TO privacy_share_links_legacy;
        CREATE TABLE privacy_share_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            target_type TEXT NOT NULL CHECK (target_type IN ('group', 'collection', 'want')),
            target_id INTEGER NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            token_hint TEXT NOT NULL DEFAULT '',
            label TEXT NOT NULL DEFAULT '',
            show_values INTEGER NOT NULL DEFAULT 0,
            show_photos INTEGER NOT NULL DEFAULT 1,
            expires_at TEXT NOT NULL DEFAULT '',
            revoked_at TEXT NOT NULL DEFAULT '',
            last_accessed_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        INSERT INTO privacy_share_links
            (id, user_id, target_type, target_id, token_hash, token_hint, label, show_values, show_photos,
             expires_at, revoked_at, last_accessed_at, created_at)
        SELECT id, user_id, target_type, target_id, token_hash, token_hint, label, show_values, show_photos,
             expires_at, revoked_at, last_accessed_at, created_at
        FROM privacy_share_links_legacy;
        DROP TABLE privacy_share_links_legacy;
        CREATE INDEX idx_privacy_share_links_target
            ON privacy_share_links(user_id, target_type, target_id, revoked_at);
        CREATE TRIGGER trg_collection_share_links_delete
        AFTER DELETE ON collection_items
        BEGIN DELETE FROM privacy_share_links WHERE target_type = 'collection' AND target_id = OLD.id; END;
        CREATE TRIGGER trg_group_share_links_delete
        AFTER DELETE ON card_groups
        BEGIN DELETE FROM privacy_share_links WHERE target_type = 'group' AND target_id = OLD.id; END;
        CREATE TRIGGER trg_want_share_links_delete
        AFTER DELETE ON want_items
        BEGIN DELETE FROM privacy_share_links WHERE target_type = 'want' AND target_id = OLD.id; END;
        """
    )


def migrate_database_maintenance(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_migration_history (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS database_storage_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            database_bytes INTEGER NOT NULL DEFAULT 0,
            wal_bytes INTEGER NOT NULL DEFAULT 0,
            shm_bytes INTEGER NOT NULL DEFAULT 0,
            total_bytes INTEGER NOT NULL DEFAULT 0,
            page_count INTEGER NOT NULL DEFAULT 0,
            page_size INTEGER NOT NULL DEFAULT 0,
            freelist_count INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'health'
        );

        CREATE INDEX IF NOT EXISTS idx_database_storage_snapshots_recorded
            ON database_storage_snapshots(recorded_at DESC, id DESC);

        CREATE TABLE IF NOT EXISTS database_maintenance_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL CHECK (action IN ('analyze', 'vacuum', 'snapshot')),
            status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
            before_bytes INTEGER NOT NULL DEFAULT 0,
            after_bytes INTEGER NOT NULL DEFAULT 0,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            details TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_database_maintenance_runs_completed
            ON database_maintenance_runs(completed_at DESC, id DESC);
        """
    )


def migrate_password_recovery(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS password_recovery_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'issued', 'completed', 'dismissed')),
            handled_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            requested_at TEXT NOT NULL,
            handled_at TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_password_recovery_requests_status
            ON password_recovery_requests(status, requested_at DESC);
        CREATE INDEX IF NOT EXISTS idx_password_recovery_requests_user
            ON password_recovery_requests(user_id, status, requested_at DESC);

        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            token_hint TEXT NOT NULL DEFAULT '',
            created_by_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            delivery_method TEXT NOT NULL DEFAULT 'manual',
            sent_at TEXT NOT NULL DEFAULT '',
            expires_at TEXT NOT NULL,
            used_at TEXT NOT NULL DEFAULT '',
            revoked_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_user
            ON password_reset_tokens(user_id, expires_at DESC);
        CREATE INDEX IF NOT EXISTS idx_password_reset_tokens_active
            ON password_reset_tokens(expires_at, used_at, revoked_at);
        """
    )


def migrate_background_job_runner(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS background_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_type TEXT NOT NULL,
            unique_key TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')),
            priority INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 5,
            available_at TEXT NOT NULL,
            lease_owner TEXT NOT NULL DEFAULT '',
            leased_until TEXT NOT NULL DEFAULT '',
            progress_current INTEGER NOT NULL DEFAULT 0,
            progress_total INTEGER NOT NULL DEFAULT 0,
            progress_message TEXT NOT NULL DEFAULT '',
            result_json TEXT NOT NULL DEFAULT '{}',
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT NOT NULL DEFAULT '',
            completed_at TEXT NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_background_jobs_claim
            ON background_jobs(status, available_at, priority DESC, created_at, id);
        CREATE INDEX IF NOT EXISTS idx_background_jobs_type_status
            ON background_jobs(job_type, status, updated_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_background_jobs_lease
            ON background_jobs(status, leased_until);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_background_jobs_active_unique
            ON background_jobs(unique_key)
            WHERE unique_key != '' AND status IN ('pending', 'running');
        """
    )


def migrate_saved_searches(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS saved_searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            context TEXT NOT NULL,
            name TEXT NOT NULL COLLATE NOCASE,
            query_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, context, name)
        );

        CREATE INDEX IF NOT EXISTS idx_saved_searches_user_context
            ON saved_searches(user_id, context, name COLLATE NOCASE, id);
        """
    )


def migrate_rate_limit_events(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS rate_limit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bucket TEXT NOT NULL,
            rate_key TEXT NOT NULL,
            event_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_rate_limit_events_bucket_key_time
            ON rate_limit_events(bucket, rate_key, event_at);
        CREATE INDEX IF NOT EXISTS idx_rate_limit_events_event_at
            ON rate_limit_events(event_at);
        """
    )


def migrate_registration_moderation(conn):
    user_columns = {column["name"] for column in conn.execute("PRAGMA table_info(users)").fetchall()}
    for name, definition in {
        "registration_status": "TEXT NOT NULL DEFAULT 'active'",
        "registration_review_note": "TEXT NOT NULL DEFAULT ''",
        "registration_reviewed_by_user_id": "INTEGER NOT NULL DEFAULT 0",
        "registration_reviewed_at": "TEXT NOT NULL DEFAULT ''",
    }.items():
        if name not in user_columns:
            conn.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")
    conn.execute("UPDATE users SET registration_status = 'active' WHERE registration_status NOT IN ('active', 'pending', 'denied')")
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_users_registration_status
            ON users(registration_status, is_banned, created_at);

        CREATE TABLE IF NOT EXISTS registration_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            invite_id INTEGER REFERENCES registration_invites(id) ON DELETE SET NULL,
            inviter_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            username TEXT NOT NULL DEFAULT '',
            display_name TEXT NOT NULL DEFAULT '',
            email_domain TEXT NOT NULL DEFAULT '',
            email_hash TEXT NOT NULL DEFAULT '',
            ip_hash TEXT NOT NULL DEFAULT '',
            subnet_hash TEXT NOT NULL DEFAULT '',
            user_agent_hash TEXT NOT NULL DEFAULT '',
            risk_score INTEGER NOT NULL DEFAULT 0,
            risk_reasons_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'active',
            decision_note TEXT NOT NULL DEFAULT '',
            reviewed_by_user_id INTEGER NOT NULL DEFAULT 0,
            reviewed_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_registration_attempts_status
            ON registration_attempts(status, created_at);
        CREATE INDEX IF NOT EXISTS idx_registration_attempts_user
            ON registration_attempts(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_registration_attempts_email_hash
            ON registration_attempts(email_hash, status, created_at);
        CREATE INDEX IF NOT EXISTS idx_registration_attempts_ip_hash
            ON registration_attempts(ip_hash, status, created_at);
        CREATE INDEX IF NOT EXISTS idx_registration_attempts_subnet_hash
            ON registration_attempts(subnet_hash, status, created_at);
        """
    )


def migrate_api_session_credentials(conn):
    token_columns = {column["name"] for column in conn.execute("PRAGMA table_info(api_tokens)").fetchall()}
    if "credential_kind" not in token_columns:
        conn.execute("ALTER TABLE api_tokens ADD COLUMN credential_kind TEXT NOT NULL DEFAULT 'api_token'")
    conn.execute(
        """
        UPDATE api_tokens
        SET credential_kind = 'android_session'
        WHERE credential_kind = 'api_token'
          AND (name = 'BinderBridge Android' OR name LIKE 'BinderBridge Android - %')
        """
    )
    conn.execute(
        """
        UPDATE api_tokens
        SET scopes = scopes || ',account'
        WHERE credential_kind = 'android_session'
          AND instr(',' || scopes || ',', ',account,') = 0
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_api_tokens_user_kind
        ON api_tokens(user_id, credential_kind, revoked_at, expires_at, created_at)
        """
    )


def migrate_browser_session_storage(conn):
    """Replace recoverable browser-session tokens with one-way hashes."""
    columns = {
        column["name"] for column in conn.execute("PRAGMA table_info(sessions)").fetchall()
    }
    if "token_hash" in columns and "token" not in columns:
        return
    if "token" not in columns and "token_hash" not in columns:
        return

    source_column = "token_hash" if "token_hash" in columns else "token"
    legacy_rows = conn.execute(
        f"""
        SELECT {source_column} AS stored_token, user_id, expires_at,
               flash_notice, flash_status, created_at
        FROM sessions
        """
    ).fetchall()
    conn.execute("ALTER TABLE sessions RENAME TO sessions_before_token_hashing")
    conn.execute(
        """
        CREATE TABLE sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at INTEGER NOT NULL,
            flash_notice TEXT NOT NULL DEFAULT '',
            flash_status TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    for legacy in legacy_rows:
        stored_token = str(legacy["stored_token"] or "").strip()
        token_hash = (
            stored_token
            if source_column == "token_hash" and is_session_token_hash(stored_token)
            else session_token_hash(stored_token)
        )
        if not token_hash:
            continue
        conn.execute(
            """
            INSERT INTO sessions
                (token_hash, user_id, expires_at, flash_notice, flash_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                token_hash,
                legacy["user_id"],
                legacy["expires_at"],
                legacy["flash_notice"],
                legacy["flash_status"],
                legacy["created_at"],
            ),
        )
    conn.execute("DROP TABLE sessions_before_token_hashing")


SCHEMA_MIGRATIONS = (
    (1, "hot path indexes", migrate_hot_path_indexes),
    (2, "trade dispute evidence and trends", migrate_dispute_moderation),
    (3, "csv import mapping presets", migrate_csv_import_mapping_presets),
    (4, "user roles and hierarchy", migrate_user_roles),
    (5, "granular privacy and share links", migrate_granular_privacy),
    (6, "collection card share links", migrate_collection_share_links),
    (7, "wanted card share links", migrate_want_share_links),
    (8, "database maintenance history and storage snapshots", migrate_database_maintenance),
    (9, "secure password recovery requests and reset tokens", migrate_password_recovery),
    (10, "durable background job runner", migrate_background_job_runner),
    (11, "saved searches and filter presets", migrate_saved_searches),
    (12, "persistent API rate limiting", migrate_rate_limit_events),
    (13, "registration moderation and ban-evasion signals", migrate_registration_moderation),
    (14, "typed Android app sessions and account scope", migrate_api_session_credentials),
    (15, "one-way browser session token storage", migrate_browser_session_storage),
)


def migration_timestamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def record_schema_migration_history(conn, current_version):
    if current_version < 8:
        return
    timestamp = migration_timestamp()
    for version, description, _migration in SCHEMA_MIGRATIONS:
        if version <= current_version:
            conn.execute(
                """
                INSERT OR IGNORE INTO schema_migration_history (version, description, applied_at)
                VALUES (?, ?, ?)
                """,
                (version, description, timestamp),
            )


def run_schema_migrations(conn):
    current_version = db_schema_version(conn)
    for version, _description, migration in SCHEMA_MIGRATIONS:
        if current_version < version:
            migration(conn)
            set_db_schema_version(conn, version)
            current_version = version
            record_schema_migration_history(conn, current_version)
    record_schema_migration_history(conn, current_version)
    return current_version


__all__ = [
    "SCHEMA_VERSION_KEY",
    "CURRENT_SCHEMA_VERSION",
    "db_schema_version",
    "set_db_schema_version",
    "migrate_hot_path_indexes",
    "migrate_dispute_moderation",
    "migrate_csv_import_mapping_presets",
    "migrate_user_roles",
    "migrate_granular_privacy",
    "migrate_collection_share_links",
    "migrate_want_share_links",
    "migrate_database_maintenance",
    "migrate_password_recovery",
    "migrate_background_job_runner",
    "migrate_saved_searches",
    "migrate_rate_limit_events",
    "migrate_registration_moderation",
    "migrate_api_session_credentials",
    "migrate_browser_session_storage",
    "SCHEMA_MIGRATIONS",
    "migration_timestamp",
    "record_schema_migration_history",
    "run_schema_migrations",
]
