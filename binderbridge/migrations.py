"""Versioned SQLite schema migrations for BinderBridge."""

SCHEMA_VERSION_KEY = "schema_version"
CURRENT_SCHEMA_VERSION = 4


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


SCHEMA_MIGRATIONS = (
    (1, "hot path indexes", migrate_hot_path_indexes),
    (2, "trade dispute evidence and trends", migrate_dispute_moderation),
    (3, "csv import mapping presets", migrate_csv_import_mapping_presets),
    (4, "user roles and hierarchy", migrate_user_roles),
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
    "SCHEMA_MIGRATIONS",
    "run_schema_migrations",
]
