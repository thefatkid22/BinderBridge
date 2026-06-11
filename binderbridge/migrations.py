"""Versioned SQLite schema migrations for BinderBridge."""

SCHEMA_VERSION_KEY = "schema_version"
CURRENT_SCHEMA_VERSION = 5


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


SCHEMA_MIGRATIONS = (
    (1, "hot path indexes", migrate_hot_path_indexes),
    (2, "trade dispute evidence and trends", migrate_dispute_moderation),
    (3, "csv import mapping presets", migrate_csv_import_mapping_presets),
    (4, "user roles and hierarchy", migrate_user_roles),
    (5, "granular privacy and share links", migrate_granular_privacy),
)


def run_schema_migrations(conn):
    current_version = db_schema_version(conn)
    for version, _description, migration in SCHEMA_MIGRATIONS:
        if current_version < version:
            migration(conn)
            set_db_schema_version(conn, version)
            current_version = version
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
    "SCHEMA_MIGRATIONS",
    "run_schema_migrations",
]
