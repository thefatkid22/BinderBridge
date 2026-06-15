"""Repeatable release-critical smoke checks for BinderBridge."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app  # noqa: E402


def use_data_directory(path):
    app.DATA_DIR = Path(path)
    app.DB_PATH = app.DATA_DIR / "binderbridge.sqlite3"
    app.clear_rate_limits()


def database_integrity_check():
    with app.db() as conn:
        quick_check = str(conn.execute("PRAGMA quick_check").fetchone()[0] or "")
        foreign_key_issues = conn.execute("PRAGMA foreign_key_check").fetchall()
    assert quick_check == "ok"
    assert not foreign_key_issues
    return {"quick_check": quick_check, "foreign_key_issues": len(foreign_key_issues)}


def fresh_install_check():
    with tempfile.TemporaryDirectory() as temp_dir:
        use_data_directory(temp_dir)
        app.init_db()
        user_id = app.create_user("releaseadmin", "password123", "Release Admin")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        version = app.row("SELECT value FROM app_settings WHERE key = ?", (app.SCHEMA_VERSION_KEY,))
        assert user["role"] == app.ROLE_OWNER
        assert int(version["value"]) == app.CURRENT_SCHEMA_VERSION
        return {
            "schema_version": int(version["value"]),
            "first_user_role": user["role"],
            "database": database_integrity_check(),
        }


def upgrade_check():
    with tempfile.TemporaryDirectory() as temp_dir:
        use_data_directory(temp_dir)
        app.init_db()
        user_id = app.create_user("upgradeadmin", "password123", "Upgrade Admin")
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, card_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'Upgrade Sentinel', 1, 0, ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        with app.db() as conn:
            conn.execute("DROP TABLE schema_migration_history")
            conn.execute("DROP TABLE database_storage_snapshots")
            conn.execute("DROP TABLE database_maintenance_runs")
            app.set_db_schema_version(conn, 7)
        app.init_db()
        sentinel = app.row("SELECT card_name FROM collection_items WHERE user_id = ?", (user_id,))
        version = app.row("SELECT value FROM app_settings WHERE key = ?", (app.SCHEMA_VERSION_KEY,))
        history = app.rows("SELECT version FROM schema_migration_history ORDER BY version")
        assert sentinel["card_name"] == "Upgrade Sentinel"
        assert int(version["value"]) == app.CURRENT_SCHEMA_VERSION
        assert len(history) == app.CURRENT_SCHEMA_VERSION
        return {
            "from_version": 7,
            "to_version": int(version["value"]),
            "preserved_rows": 1,
            "database": database_integrity_check(),
        }


def backup_restore_check():
    with tempfile.TemporaryDirectory() as temp_dir:
        use_data_directory(temp_dir)
        app.init_db()
        user_id = app.create_user("backupadmin", "password123", "Backup Admin")
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, card_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'Backup Sentinel', 1, 0, ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        archive = app.create_backup_archive(user_id)
        app.execute("UPDATE collection_items SET card_name = 'Changed After Backup'")
        result = app.restore_backup_upload({"filename": archive.name, "content": archive.read_bytes()}, user_id)
        restored = app.row("SELECT card_name FROM collection_items WHERE user_id = ?", (user_id,))
        assert restored["card_name"] == "Backup Sentinel"
        assert result["pre_restore_backup_name"]
        return {
            "archive": archive.name,
            "restored_card": restored["card_name"],
            "database": database_integrity_check(),
        }


def core_trade_workflow_check():
    with tempfile.TemporaryDirectory() as temp_dir:
        use_data_directory(temp_dir)
        app.init_db()
        alice_id = app.create_user("tradealice", "password123", "Trade Alice")
        bob_id = app.create_user("tradebob", "password123", "Trade Bob")
        timestamp = app.now_iso()
        alice_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Release Offer', 'Release Set', 1, 1, ?, ?)
            """,
            (alice_id, timestamp, timestamp),
        )
        bob_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Release Request', 'Release Set', 1, 1, ?, ?)
            """,
            (bob_id, timestamp, timestamp),
        )
        app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, set_name, desired_quantity, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Release Request', 'Release Set', 1, 1, ?, ?)
            """,
            (alice_id, timestamp, timestamp),
        )
        app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, set_name, desired_quantity, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Release Offer', 'Release Set', 1, 1, ?, ?)
            """,
            (bob_id, timestamp, timestamp),
        )
        alice_card = app.row("SELECT * FROM collection_items WHERE id = ?", (alice_card_id,))
        bob_card = app.row("SELECT * FROM collection_items WHERE id = ?", (bob_card_id,))
        trade_id = app.create_trade_offer(
            alice_id,
            bob_id,
            "Release smoke trade",
            [(alice_card, 1)],
            [(bob_card, 1)],
        )
        app.update_trade_response(trade_id, bob_id, "accepted", "Accepted during release smoke check.")
        app.complete_trade(trade_id, bob_id)

        trade = app.row("SELECT status FROM trades WHERE id = ?", (trade_id,))
        alice_received = app.row(
            "SELECT quantity FROM collection_items WHERE user_id = ? AND card_name = 'Release Request'",
            (alice_id,),
        )
        bob_received = app.row(
            "SELECT quantity FROM collection_items WHERE user_id = ? AND card_name = 'Release Offer'",
            (bob_id,),
        )
        notifications = app.row(
            "SELECT COUNT(*) AS count FROM user_notifications WHERE related_trade_id = ?",
            (trade_id,),
        )
        assert trade["status"] == "completed"
        assert alice_received["quantity"] == 1
        assert bob_received["quantity"] == 1
        assert notifications["count"] >= 3
        return {
            "trade_status": trade["status"],
            "notifications": notifications["count"],
            "database": database_integrity_check(),
        }


def large_import_check(row_count):
    with tempfile.TemporaryDirectory() as temp_dir:
        use_data_directory(temp_dir)
        app.init_db()
        user_id = app.create_user("importadmin", "password123", "Import Admin")
        rows = ["Name,Quantity,Trade,Set Name,Set Code,Collector Number,Foil,Condition,Language"]
        rows.extend(
            f"Release Card {index},1,{index % 2},Release Set,RLS,{index},false,NM,English"
            for index in range(1, row_count + 1)
        )
        started = time.perf_counter()
        result = app.import_collection_csv(user_id, "\n".join(rows).encode("utf-8"), enrich_scryfall=False)
        duration = round(time.perf_counter() - started, 3)
        count = app.row("SELECT COUNT(*) AS count FROM collection_items WHERE user_id = ?", (user_id,))["count"]
        assert result["inserted"] == row_count
        assert count == row_count
        return {"rows": row_count, "duration_seconds": duration}


def main():
    row_count = max(100, min(10000, int(os.environ.get("BINDERBRIDGE_RELEASE_IMPORT_ROWS", "5000"))))
    results = {
        "version": app.APP_VERSION,
        "fresh_install": fresh_install_check(),
        "upgrade": upgrade_check(),
        "backup_restore": backup_restore_check(),
        "core_trade_workflow": core_trade_workflow_check(),
        "large_import": large_import_check(row_count),
    }
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
