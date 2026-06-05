import csv
import hashlib
import io
import json
import os
import tempfile
import time
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import app
from binderbridge import config as bb_config
from tests import factories as factory


def _der_integer(value):
    data = int(value).to_bytes(max(1, (int(value).bit_length() + 7) // 8), "big")
    if data[0] & 0x80:
        data = b"\x00" + data
    return b"\x02" + bytes([len(data)]) + data


def _der_signature(r, s):
    payload = _der_integer(r) + _der_integer(s)
    return b"\x30" + bytes([len(payload)]) + payload


def _p256_sign(message, private_key, nonce=9):
    z = int.from_bytes(hashlib.sha256(message).digest(), "big")
    k = nonce % app.P256_N
    while True:
        point = app.p256_scalar_mult(k, (app.P256_GX, app.P256_GY))
        r = point[0] % app.P256_N if point else 0
        if r:
            s = (app.p256_inverse(k, app.P256_N) * (z + r * private_key)) % app.P256_N
            if s:
                return _der_signature(r, s)
        k = (k + 1) % app.P256_N


class BinderBridgeTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        app.DATA_DIR = Path(self.tmpdir.name)
        app.DB_PATH = app.DATA_DIR / "test.sqlite3"
        app.init_db()
        app.clear_rate_limits()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_config_file_values_are_loaded_with_environment_override(self):
        original = {
            "BINDERBRIDGE_CONFIG": os.environ.get("BINDERBRIDGE_CONFIG"),
            "BINDERBRIDGE_SMTP_HOST": os.environ.get("BINDERBRIDGE_SMTP_HOST"),
        }
        try:
            config_path = Path(self.tmpdir.name) / "binderbridge.ini"
            config_path.write_text(
                """
                [smtp]
                host = smtp.file.test
                port = 2525
                tls = false

                [scryfall]
                search_limit = 12
                """,
                encoding="utf-8",
            )
            os.environ["BINDERBRIDGE_CONFIG"] = str(config_path)
            os.environ.pop("BINDERBRIDGE_SMTP_HOST", None)
            bb_config.reset_config_cache()

            self.assertEqual(bb_config.config_str("BINDERBRIDGE_SMTP_HOST", section="smtp", key="host"), "smtp.file.test")
            self.assertEqual(bb_config.config_int("BINDERBRIDGE_SMTP_PORT", section="smtp", key="port"), 2525)
            self.assertFalse(bb_config.config_bool("BINDERBRIDGE_SMTP_TLS", default=True, section="smtp", key="tls"))
            self.assertEqual(bb_config.config_int("SCRYFALL_SEARCH_LIMIT", section="scryfall", key="search_limit"), 12)

            os.environ["BINDERBRIDGE_SMTP_HOST"] = "smtp.env.test"
            self.assertEqual(bb_config.config_str("BINDERBRIDGE_SMTP_HOST", section="smtp", key="host"), "smtp.env.test")
        finally:
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            bb_config.reset_config_cache()

    def test_schema_migrations_record_version_and_create_hot_path_indexes(self):
        app.init_db()

        version = app.row("SELECT value FROM app_settings WHERE key = ?", (app.SCHEMA_VERSION_KEY,))
        collection_indexes = {item["name"] for item in app.rows("PRAGMA index_list(collection_items)")}
        want_indexes = {item["name"] for item in app.rows("PRAGMA index_list(want_items)")}
        trade_indexes = {item["name"] for item in app.rows("PRAGMA index_list(trades)")}
        scryfall_indexes = {item["name"] for item in app.rows("PRAGMA index_list(scryfall_bulk_cards)")}
        passkey_indexes = {item["name"] for item in app.rows("PRAGMA index_list(passkey_credentials)")}
        passkey_challenge_indexes = {item["name"] for item in app.rows("PRAGMA index_list(passkey_challenges)")}
        csv_preset_indexes = {item["name"] for item in app.rows("PRAGMA index_list(csv_import_mapping_presets)")}
        dispute_columns = {item["name"] for item in app.rows("PRAGMA table_info(trade_disputes)")}
        evidence_indexes = {item["name"] for item in app.rows("PRAGMA index_list(trade_dispute_evidence)")}
        with app.db() as conn:
            busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()

        self.assertEqual(int(version["value"]), app.CURRENT_SCHEMA_VERSION)
        self.assertGreaterEqual(busy_timeout, app.SQLITE_BUSY_TIMEOUT_MS)
        self.assertEqual(journal_mode, "wal")
        self.assertTrue({
            "idx_collection_user_name_sort",
            "idx_collection_user_public_trade_name",
            "idx_collection_public_trade_game_name",
        }.issubset(collection_indexes))
        self.assertTrue({"idx_wants_user_name_sort", "idx_wants_public_name"}.issubset(want_indexes))
        self.assertTrue({"idx_trades_proposer_updated", "idx_trades_recipient_updated"}.issubset(trade_indexes))
        self.assertIn("idx_scryfall_bulk_cards_print_release", scryfall_indexes)
        self.assertIn("idx_passkey_credentials_user", passkey_indexes)
        self.assertIn("idx_passkey_challenges_user", passkey_challenge_indexes)
        self.assertIn("idx_csv_import_mapping_presets_user", csv_preset_indexes)
        self.assertIn("idx_csv_import_mapping_presets_shared", csv_preset_indexes)
        self.assertIn("resolution_note", dispute_columns)
        self.assertIn("idx_trade_dispute_evidence_dispute", evidence_indexes)

    def test_scryfall_helpers_live_in_focused_modules(self):
        self.assertEqual(app.scryfall_get.__module__, "binderbridge.scryfall_client")
        self.assertEqual(app.sync_scryfall_bulk_data.__module__, "binderbridge.scryfall_client")
        self.assertEqual(app.refresh_all_scryfall_prices.__module__, "binderbridge.scryfall_jobs")
        self.assertEqual(app.start_scryfall_enrichment_worker.__module__, "binderbridge.scryfall_jobs")
        self.assertEqual(app.SCRYFALL_COLLECTION_FIELDS[0], "scryfall_id")

    def test_password_hash_round_trip(self):
        stored = app.hash_password("correct horse battery staple")

        self.assertTrue(app.verify_password("correct horse battery staple", stored))
        self.assertFalse(app.verify_password("wrong password", stored))

    def test_user_session_round_trip(self):
        user_id = factory.create_user("jace", display_name="Jace")
        token, _ = app.create_session(user_id)

        found = app.get_user_by_session(token)

        self.assertEqual(found["username"], "jace")

    def test_csrf_tokens_are_injected_and_validated_for_session_forms(self):
        _, token, _ = factory.create_session_user("csrfuser", display_name="CSRF User")

        injected = app.inject_csrf_tokens('<form method="post" action="/account/profile"><button>Save</button></form>', token)
        valid_form = {app.CSRF_FIELD_NAME: [app.csrf_token_for_session(token)]}
        invalid_form = {app.CSRF_FIELD_NAME: ["wrong"]}

        self.assertIn(f'name="{app.CSRF_FIELD_NAME}"', injected)
        self.assertTrue(app.csrf_form_valid(valid_form, token))
        self.assertFalse(app.csrf_form_valid(invalid_form, token))

    def test_rate_limit_helper_blocks_after_limit(self):
        app.clear_rate_limits()

        self.assertTrue(app.rate_limit_allowed("unit-test", "same-key", limit=2, window_seconds=60))
        self.assertTrue(app.rate_limit_allowed("unit-test", "same-key", limit=2, window_seconds=60))
        self.assertFalse(app.rate_limit_allowed("unit-test", "same-key", limit=2, window_seconds=60))
        self.assertTrue(app.rate_limit_allowed("unit-test", "other-key", limit=2, window_seconds=60))

    def test_passkey_options_render_on_login_and_account_pages(self):
        user = factory.user_row("passkeyui", display_name="Passkey User")

        login_html = app.render_login()
        account_html = app.render_account(user)

        self.assertIn("Sign in with passkey", login_html)
        self.assertIn("/login/passkey/options", login_html)
        self.assertIn("Passkeys", account_html)
        self.assertIn("passkey-register-button", account_html)
        self.assertIn("/account/passkeys/register/options", account_html)

    def test_passkey_authentication_verifies_synthetic_es256_assertion(self):
        user = factory.user_row("passkeyauth", display_name="Passkey Auth")
        private_key = 123456789
        public_point = app.p256_scalar_mult(private_key, (app.P256_GX, app.P256_GY))
        credential_raw = b"binderbridge-test-passkey"
        timestamp = app.now_iso()
        app.execute(
            """
            INSERT INTO passkey_credentials
                (user_id, credential_id, public_key_cose, public_key_x, public_key_y,
                 sign_count, nickname, aaguid, transports, created_at)
            VALUES (?, ?, '', ?, ?, 0, 'Unit test key', '', '[]', ?)
            """,
            (
                user["id"],
                app.passkey_b64encode(credential_raw),
                app.passkey_b64encode(public_point[0].to_bytes(32, "big")),
                app.passkey_b64encode(public_point[1].to_bytes(32, "big")),
                timestamp,
            ),
        )

        options = app.passkey_authentication_options("passkeyauth", "127.0.0.1", "http://127.0.0.1:8000")
        auth_data = (
            hashlib.sha256(b"127.0.0.1").digest()
            + bytes([app.PASSKEY_UP_FLAG | app.PASSKEY_UV_FLAG])
            + (1).to_bytes(4, "big")
        )
        client_data = json.dumps(
            {
                "type": "webauthn.get",
                "challenge": options["publicKey"]["challenge"],
                "origin": "http://127.0.0.1:8000",
                "crossOrigin": False,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        signature = _p256_sign(auth_data + hashlib.sha256(client_data).digest(), private_key)
        payload = {
            "id": app.passkey_b64encode(credential_raw),
            "rawId": app.passkey_b64encode(credential_raw),
            "type": "public-key",
            "response": {
                "clientDataJSON": app.passkey_b64encode(client_data),
                "authenticatorData": app.passkey_b64encode(auth_data),
                "signature": app.passkey_b64encode(signature),
                "userHandle": app.passkey_user_handle(user["id"]),
            },
        }

        verified_user, credential = app.complete_passkey_authentication(options["token"], json.dumps(payload))
        updated = app.row("SELECT * FROM passkey_credentials WHERE id = ?", (credential["id"],))

        self.assertEqual(verified_user["username"], "passkeyauth")
        self.assertEqual(updated["sign_count"], 1)
        self.assertTrue(updated["last_used_at"])
        self.assertIsNone(app.passkey_challenge_row(options["token"], "authentication"))

    def test_passkey_authentication_rejects_missing_user_verification(self):
        user = factory.user_row("passkeyuv", display_name="Passkey UV")
        private_key = 987654321
        public_point = app.p256_scalar_mult(private_key, (app.P256_GX, app.P256_GY))
        credential_raw = b"binderbridge-test-passkey-uv"
        app.execute(
            """
            INSERT INTO passkey_credentials
                (user_id, credential_id, public_key_cose, public_key_x, public_key_y,
                 sign_count, nickname, aaguid, transports, created_at)
            VALUES (?, ?, '', ?, ?, 0, 'Unit test key', '', '[]', ?)
            """,
            (
                user["id"],
                app.passkey_b64encode(credential_raw),
                app.passkey_b64encode(public_point[0].to_bytes(32, "big")),
                app.passkey_b64encode(public_point[1].to_bytes(32, "big")),
                app.now_iso(),
            ),
        )
        options = app.passkey_authentication_options("passkeyuv", "127.0.0.1", "http://127.0.0.1:8000")
        auth_data = hashlib.sha256(b"127.0.0.1").digest() + bytes([app.PASSKEY_UP_FLAG]) + (1).to_bytes(4, "big")
        client_data = json.dumps(
            {
                "type": "webauthn.get",
                "challenge": options["publicKey"]["challenge"],
                "origin": "http://127.0.0.1:8000",
            },
            separators=(",", ":"),
        ).encode("utf-8")
        signature = _p256_sign(auth_data + hashlib.sha256(client_data).digest(), private_key)
        payload = {
            "rawId": app.passkey_b64encode(credential_raw),
            "response": {
                "clientDataJSON": app.passkey_b64encode(client_data),
                "authenticatorData": app.passkey_b64encode(auth_data),
                "signature": app.passkey_b64encode(signature),
            },
        }

        with self.assertRaisesRegex(ValueError, "user verification"):
            app.complete_passkey_authentication(options["token"], json.dumps(payload))

    def test_first_user_becomes_admin(self):
        first_id = app.create_user("first", "password123", "First")
        second_id = app.create_user("second", "password123", "Second")

        first = app.row("SELECT * FROM users WHERE id = ?", (first_id,))
        second = app.row("SELECT * FROM users WHERE id = ?", (second_id,))

        self.assertEqual(first["is_admin"], 1)
        self.assertEqual(second["is_admin"], 0)

    def test_admin_panel_renders_user_controls_for_admin(self):
        admin_id = app.create_user("admin", "password123", "Admin")
        app.create_user("user", "password123", "User")
        admin = app.row("SELECT * FROM users WHERE id = ?", (admin_id,))
        original_invites_configured = app.smtp_invites_configured
        original_email_configured = app.email_delivery_configured
        app.smtp_invites_configured = lambda: False
        app.email_delivery_configured = lambda: False
        try:
            html = app.render_admin(admin)
        finally:
            app.smtp_invites_configured = original_invites_configured
            app.email_delivery_configured = original_email_configured

        self.assertIn("User control panel", html)
        self.assertIn("Onboarding checklist", html)
        self.assertIn("0 of 5 complete", html)
        self.assertIn("Configure SMTP", html)
        self.assertIn("Create first backup", html)
        self.assertIn("Sync Scryfall bulk data", html)
        self.assertIn("Invite users", html)
        self.assertIn("Add first collection import", html)
        self.assertIn('/admin/health/scryfall/sync', html)
        self.assertIn('name="redirect_to" value="/admin"', html)
        self.assertIn("Trade policy", html)
        self.assertIn("Completed trades to earn trust", html)
        self.assertIn("Integration access", html)
        self.assertIn('name="api_access_policy"', html)
        self.assertIn('name="webhook_access_policy"', html)
        self.assertIn("Reset password", html)
        self.assertIn("Ban", html)
        self.assertIn("Make admin", html)
        self.assertIn("Trust user", html)
        self.assertIn("Registration", html)
        self.assertIn("Invite links", html)
        self.assertIn("Create invite link", html)
        self.assertIn('/admin/invites', html)
        self.assertIn("Backup and restore", html)
        self.assertIn('/admin/backups/create', html)
        self.assertIn('/admin/backups/settings', html)
        self.assertIn('/admin/backups/run', html)
        self.assertIn('/admin/backups/restore', html)
        self.assertIn("Admin activity log", html)
        self.assertIn('/admin/logs', html)
        self.assertIn('/admin/health', html)

    def test_admin_onboarding_checklist_tracks_completed_setup(self):
        admin = factory.user_row("onboardadmin", display_name="Onboard Admin")
        original_email_configured = app.email_delivery_configured
        app.email_delivery_configured = lambda: True
        try:
            app.create_backup_archive(admin["id"])
            app.store_scryfall_bulk_cards([
                {
                    "object": "card",
                    "id": "onboarding-sol-ring",
                    "name": "Sol Ring",
                    "set_name": "Commander Masters",
                    "set": "cmm",
                    "collector_number": "703",
                    "released_at": "2023-08-04",
                    "type_line": "Artifact",
                    "rarity": "uncommon",
                    "color_identity": [],
                    "prices": {"usd": "1.23"},
                }
            ])
            app.create_registration_invite(admin["id"], "newuser@example.com")
            app.create_import_batch(admin["id"], "collection_csv", "ManaBox CSV", status="applied")

            checklist = app.admin_onboarding_checklist()
            html = app.render_admin(admin)
        finally:
            app.email_delivery_configured = original_email_configured

        self.assertTrue(checklist["is_complete"])
        self.assertEqual(checklist["complete_count"], 5)
        self.assertIn("5 of 5 complete", html)
        self.assertIn("Complete", html)
        self.assertIn("1 backup archive available", html)
        self.assertIn("1 invite created", html)
        self.assertIn("1 applied collection import recorded", html)

    def test_admin_health_page_summarizes_maintenance_status(self):
        admin = factory.user_row("healthadmin", display_name="Health Admin")
        card_id = factory.create_collection_item(admin["id"], "Sol Ring", quantity=2, quantity_for_trade=1)
        app.set_setting(app.SCRYFALL_PRICE_REFRESH_STATUS_KEY, "error")
        app.set_setting(app.SCRYFALL_PRICE_REFRESH_ERROR_KEY, "Price refresh unavailable")
        app.set_setting(app.SCRYFALL_BULK_STATUS_KEY, "idle")
        app.execute(
            """
            INSERT INTO scryfall_enrichment_jobs
                (collection_item_id, user_id, lookup_key, card_name, status, last_error, available_at, created_at, updated_at)
            VALUES (?, ?, 'sol-ring', 'Sol Ring', 'failed', 'Not found', '', ?, ?)
            """,
            (card_id, admin["id"], app.now_iso(), app.now_iso()),
        )
        app.execute(
            """
            INSERT INTO user_notifications
                (user_id, kind, title, body, url, email_status, email_error, created_at)
            VALUES (?, 'trade_offer', 'Failed email', 'Could not send.', '/notifications', 'failed', 'SMTP refused', ?)
            """,
            (admin["id"], app.now_iso()),
        )

        health = app.maintenance_health_status()
        html = app.render_admin_health(admin)

        self.assertEqual(health["notifications"]["failed_email_count"], 1)
        self.assertEqual(health["scryfall"]["prices"]["status"], "error")
        self.assertIn("Maintenance health", html)
        self.assertIn("Needs attention", html)
        self.assertIn("health-attention-panel", html)
        self.assertIn("attention-group-heading", html)
        self.assertIn("Scryfall jobs", html)
        self.assertIn("database size", html)
        self.assertIn("Backup status", html)
        self.assertIn("Scryfall refresh", html)
        self.assertIn("Queued jobs", html)
        self.assertIn("Email configuration", html)
        self.assertIn("Failed notifications", html)
        self.assertIn("Maintenance actions", html)
        self.assertIn("Setup warnings", html)
        self.assertIn("health-severity-card severity-error", html)
        self.assertIn("job-row severity-error", html)
        self.assertIn('/admin/health/jobs/retry', html)
        self.assertIn('/admin/health/notifications/replay', html)
        self.assertIn('/admin/health/backups/check', html)
        self.assertIn('/admin/jobs/scryfall/retry', html)
        self.assertIn('/admin/jobs/notifications/retry', html)
        self.assertIn('name="redirect_to" value="/admin/health"', html)
        self.assertIn("SMTP refused", html)
        self.assertIn("Price refresh unavailable", html)
        self.assertTrue(health["setup_warnings"])

    def test_admin_jobs_dashboard_shows_imports_retries_and_failed_emails(self):
        admin = factory.user_row("jobadmin", display_name="Job Admin")
        user_id = factory.create_user("jobuser", display_name="Job User", email="jobuser@example.com")
        import_card_id = factory.create_collection_item(user_id, "Sol Ring", quantity=1)
        lookup_card_id = factory.create_collection_item(user_id, "Arcane Signet", quantity=1)
        batch_id = app.create_import_batch(
            user_id,
            "collection_csv",
            "ManaBox CSV",
            status="applied",
            summary={"inserted": 1, "updated": 0, "queued": 1, "skipped": 0},
            payload={},
        )
        app.record_import_batch_item(batch_id, "collection_item", "inserted", "collection_items", import_card_id)
        app.execute(
            """
            INSERT INTO scryfall_enrichment_jobs
                (collection_item_id, user_id, lookup_key, card_name, status, attempts, last_error, available_at, created_at, updated_at)
            VALUES (?, ?, 'arcane-signet', 'Arcane Signet', 'failed', 2, 'Lookup failed', '', ?, ?)
            """,
            (lookup_card_id, user_id, app.now_iso(), app.now_iso()),
        )
        app.set_setting(app.SCRYFALL_PRICE_REFRESH_STATUS_KEY, "error")
        app.set_setting(app.SCRYFALL_PRICE_REFRESH_ERROR_KEY, "Bulk cache unavailable")
        app.execute(
            """
            INSERT INTO user_notifications
                (user_id, kind, title, body, url, email_status, email_error, created_at)
            VALUES (?, 'trade_offer', 'Trade offer failed', 'Could not email.', '/notifications', 'failed', 'SMTP refused', ?)
            """,
            (user_id, app.now_iso()),
        )

        dashboard = app.maintenance_job_dashboard()
        html = app.render_admin_jobs(admin)

        self.assertEqual(dashboard["metrics"]["recent_imports"], 1)
        self.assertEqual(dashboard["metrics"]["scryfall_attention"], 1)
        self.assertEqual(dashboard["metrics"]["failed_emails"], 1)
        self.assertIn("Import and job dashboard", html)
        self.assertIn("ManaBox CSV", html)
        self.assertIn(f"/admin/jobs/imports/{batch_id}/undo", html)
        self.assertIn('/admin/jobs/scryfall/retry', html)
        self.assertIn('/admin/jobs/scryfall-prices/retry', html)
        self.assertIn('/admin/jobs/notifications/retry', html)
        self.assertIn("Lookup failed", html)
        self.assertIn("SMTP refused", html)
        self.assertIn("Bulk cache unavailable", html)

    def test_admin_job_retry_and_undo_helpers_reset_recoverable_work(self):
        user_id = factory.create_user("retryuser", display_name="Retry User", email="retry@example.com")
        retry_card_id = factory.create_collection_item(user_id, "Arcane Signet", quantity=1)
        undo_card_id = factory.create_collection_item(user_id, "Command Tower", quantity=1)
        timestamp = app.now_iso()
        app.execute(
            """
            INSERT INTO scryfall_enrichment_jobs
                (collection_item_id, user_id, lookup_key, card_name, status, attempts, last_error, available_at, created_at, updated_at)
            VALUES (?, ?, 'arcane-signet', 'Arcane Signet', 'failed', 3, 'Rate limited', '2099-01-01T00:00:00+00:00', ?, ?)
            """,
            (retry_card_id, user_id, timestamp, timestamp),
        )
        job = app.row("SELECT * FROM scryfall_enrichment_jobs WHERE collection_item_id = ?", (retry_card_id,))
        notification_id = app.execute(
            """
            INSERT INTO user_notifications
                (user_id, kind, title, body, url, email_status, email_error, created_at)
            VALUES (?, 'trade_offer', 'Failed email', 'Retry me.', '/notifications', 'failed', 'SMTP refused', ?)
            """,
            (user_id, timestamp),
        )
        batch_id = app.create_import_batch(
            user_id,
            "collection_csv",
            "Undo CSV",
            status="applied",
            summary={"inserted": 1},
            payload={},
        )
        app.record_import_batch_item(batch_id, "collection_item", "inserted", "collection_items", undo_card_id)

        retried_job = app.retry_scryfall_enrichment_job(job["id"])
        retried_notification = app.retry_failed_notification_email(notification_id)
        undo = app.admin_undo_import_batch(batch_id)

        refreshed_job = app.row("SELECT * FROM scryfall_enrichment_jobs WHERE id = ?", (job["id"],))
        refreshed_notification = app.row("SELECT * FROM user_notifications WHERE id = ?", (notification_id,))
        undone_batch = app.row("SELECT * FROM import_batches WHERE id = ?", (batch_id,))
        deleted_card = app.row("SELECT * FROM collection_items WHERE id = ?", (undo_card_id,))

        self.assertEqual(retried_job["id"], job["id"])
        self.assertEqual(refreshed_job["status"], "pending")
        self.assertEqual(refreshed_job["attempts"], 0)
        self.assertEqual(refreshed_job["last_error"], "")
        self.assertEqual(refreshed_job["available_at"], "")
        self.assertEqual(retried_notification["id"], notification_id)
        self.assertEqual(refreshed_notification["email_status"], "pending")
        self.assertEqual(refreshed_notification["email_error"], "")
        self.assertEqual(undo["undone_items"], 1)
        self.assertEqual(undone_batch["status"], "undone")
        self.assertIsNone(deleted_card)

    def test_admin_health_actions_retry_replay_and_check_backups(self):
        admin = factory.user_row("maintenanceadmin", display_name="Maintenance Admin", email="admin@example.com")
        card_id = factory.create_collection_item(admin["id"], "Lightning Bolt", quantity=1)
        timestamp = app.now_iso()
        app.execute(
            """
            INSERT INTO scryfall_enrichment_jobs
                (collection_item_id, user_id, lookup_key, card_name, status, attempts, last_error, available_at, created_at, updated_at)
            VALUES (?, ?, 'lightning-bolt', 'Lightning Bolt', 'failed', 2, 'Lookup failed', '2099-01-01T00:00:00+00:00', ?, ?)
            """,
            (card_id, admin["id"], timestamp, timestamp),
        )
        notification_id = app.execute(
            """
            INSERT INTO user_notifications
                (user_id, kind, title, body, url, email_status, email_error, created_at)
            VALUES (?, 'trade_offer', 'Failed email', 'Replay me.', '/notifications', 'failed', 'SMTP refused', ?)
            """,
            (admin["id"], timestamp),
        )
        original_email_configured = app.email_delivery_configured
        original_send_pending = app.send_pending_trade_notification_emails
        app.email_delivery_configured = lambda: True
        app.send_pending_trade_notification_emails = lambda user_id=None, limit=20: {"sent": 1, "failed": 0, "skipped": 0}
        try:
            retry_result = app.retry_recoverable_maintenance_jobs()
            replay_result = app.replay_failed_notification_emails(limit=10)
        finally:
            app.email_delivery_configured = original_email_configured
            app.send_pending_trade_notification_emails = original_send_pending
        app.create_backup_archive(admin["id"])
        backup_result = app.run_backup_integrity_check()

        retried_job = app.row("SELECT * FROM scryfall_enrichment_jobs WHERE collection_item_id = ?", (card_id,))
        replayed_notification = app.row("SELECT * FROM user_notifications WHERE id = ?", (notification_id,))
        integrity = app.backup_integrity_status()

        self.assertEqual(retry_result["total"], 1)
        self.assertEqual(retried_job["status"], "pending")
        self.assertEqual(retried_job["attempts"], 0)
        self.assertEqual(retried_job["last_error"], "")
        self.assertEqual(replay_result["queued"], 1)
        self.assertEqual(replay_result["sent"], 1)
        self.assertEqual(replayed_notification["email_status"], "pending")
        self.assertEqual(replayed_notification["email_error"], "")
        self.assertEqual(backup_result["status"], "ok")
        self.assertEqual(integrity["last_status"], "ok")
        self.assertEqual(integrity["failed"], 0)

    def test_registration_invite_can_be_created_and_accepted(self):
        admin_id = app.create_user("admin", "password123", "Admin")
        app.set_invite_only_registration(True)

        invite = app.create_registration_invite(admin_id, "newuser@example.com", "http://binder.test")
        found = app.registration_invite_from_token(invite["token"], "newuser@example.com")
        invited_id = app.create_user("invited", "password123", "Invited", email=found["email"])
        app.accept_registration_invite(invite["token"], invited_id)
        accepted = app.row("SELECT * FROM registration_invites WHERE id = ?", (invite["id"],))

        self.assertTrue(app.registration_requires_invite())
        self.assertIn("http://binder.test/register?invite=", invite["link"])
        self.assertFalse(invite["sent"])
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(accepted["accepted_by_user_id"], invited_id)
        self.assertIsNone(app.registration_invite_from_token(invite["token"]))
        self.assertEqual(app.row("SELECT email FROM users WHERE id = ?", (invited_id,))["email"], "newuser@example.com")

    def test_registration_invite_can_be_revoked(self):
        admin_id = app.create_user("admin", "password123", "Admin")
        invite = app.create_registration_invite(admin_id, "revoke@example.com", "http://binder.test")

        app.revoke_registration_invite(admin_id, invite["id"])
        revoked = app.row("SELECT * FROM registration_invites WHERE id = ?", (invite["id"],))

        self.assertEqual(revoked["status"], "revoked")
        self.assertIsNone(app.registration_invite_from_token(invite["token"]))

    def test_admin_invite_revoke_form_is_not_nested_in_create_form(self):
        admin_id = app.create_user("admin", "password123", "Admin")
        invite = app.create_registration_invite(admin_id, "nested@example.com", "http://binder.test")
        admin = app.row("SELECT * FROM users WHERE id = ?", (admin_id,))

        html = app.render_admin(admin)
        create_form = 'action="/admin/invites"'
        revoke_form = f'action="/admin/invites/{invite["id"]}/revoke"'
        create_start = html.index(create_form)
        create_end = html.index("</form>", create_start)
        revoke_start = html.index(revoke_form)

        self.assertGreater(revoke_start, create_end)

    def test_invite_only_registration_page_requires_valid_invite(self):
        app.create_user("admin", "password123", "Admin")
        app.set_invite_only_registration(True)

        html = app.render_register(invite_required=app.registration_requires_invite())

        self.assertIn("Invite required", html)
        self.assertIn("Registration is currently invite-only", html)
        self.assertNotIn('method="post" action="/register"', html)

    def test_backup_archive_contains_database_and_metadata(self):
        admin_id = app.create_user("admin", "password123", "Admin")
        app.execute(
            """
            INSERT INTO collection_items (user_id, card_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'Sol Ring', 1, 0, ?, ?)
            """,
            (admin_id, app.now_iso(), app.now_iso()),
        )

        archive_path = app.create_backup_archive(admin_id)

        self.assertTrue(archive_path.exists())
        self.assertIn("binderbridge-backup", archive_path.name)
        with zipfile.ZipFile(archive_path) as archive:
            self.assertIn(app.BACKUP_DATABASE_NAME, archive.namelist())
            metadata = json.loads(archive.read("metadata.json").decode("utf-8"))
        self.assertEqual(metadata["app"], app.APP_NAME)
        self.assertEqual(metadata["created_by_user_id"], admin_id)

    def test_automatic_backup_settings_run_and_due_state(self):
        admin_id = app.create_user("admin", "password123", "Admin")
        app.execute(
            """
            INSERT INTO collection_items (user_id, card_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'Sol Ring', 1, 0, ?, ?)
            """,
            (admin_id, app.now_iso(), app.now_iso()),
        )

        settings = app.set_automatic_backup_settings(True, "12", "3", "30")
        result = app.run_automatic_backup_once(force=True)
        status = app.automatic_backup_status()

        self.assertTrue(settings["enabled"])
        self.assertTrue(result["success"])
        self.assertIn(app.AUTOMATIC_BACKUP_PREFIX, result["archive"])
        self.assertTrue((app.DATA_DIR / "backups" / result["archive"]).exists())
        self.assertFalse(app.automatic_backup_due())
        self.assertEqual(status["last_error"], "")
        self.assertTrue(status["last_success"])
        self.assertIn("next_run", status)

    def test_automatic_backup_retention_prunes_only_auto_archives(self):
        directory = app.backup_directory()
        now = time.time()
        auto_names = []
        for index in range(3):
            archive_path = directory / f"{app.AUTOMATIC_BACKUP_PREFIX}-test-{index}.zip"
            archive_path.write_text("backup", encoding="utf-8")
            os.utime(archive_path, (now - index * 60, now - index * 60))
            auto_names.append(archive_path.name)
        manual_path = directory / "binderbridge-backup-manual.zip"
        manual_path.write_text("manual", encoding="utf-8")
        os.utime(manual_path, (now - 3600, now - 3600))

        pruned = app.prune_backup_archives(retention_count=2, retention_days=0)
        remaining_auto = sorted(path.name for path in app.backup_archive_paths(app.AUTOMATIC_BACKUP_PREFIX))

        self.assertEqual(len(pruned["deleted"]), 1)
        self.assertIn(auto_names[-1], pruned["deleted"])
        self.assertEqual(len(remaining_auto), 2)
        self.assertTrue(manual_path.exists())

    def test_restore_backup_upload_replaces_database_and_keeps_safety_backup(self):
        admin_id = app.create_user("admin", "password123", "Admin")
        app.execute(
            """
            INSERT INTO collection_items (user_id, card_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'Before Restore', 1, 0, ?, ?)
            """,
            (admin_id, app.now_iso(), app.now_iso()),
        )
        archive_path = app.create_backup_archive(admin_id)
        app.execute("DELETE FROM collection_items")
        app.execute(
            """
            INSERT INTO collection_items (user_id, card_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'After Backup', 1, 0, ?, ?)
            """,
            (admin_id, app.now_iso(), app.now_iso()),
        )

        result = app.restore_backup_upload(
            {"filename": archive_path.name, "content": archive_path.read_bytes()},
            admin_id,
        )

        restored = app.rows("SELECT card_name FROM collection_items ORDER BY card_name")
        self.assertEqual([item["card_name"] for item in restored], ["Before Restore"])
        self.assertIn("binderbridge-pre-restore", result["pre_restore_backup_name"])
        self.assertTrue((app.DATA_DIR / "backups" / result["pre_restore_backup_name"]).exists())

    def test_restore_backup_rejects_invalid_sqlite_upload(self):
        admin_id = app.create_user("admin", "password123", "Admin")

        with self.assertRaisesRegex(ValueError, "not a readable SQLite"):
            app.restore_backup_upload({"filename": "bad.sqlite3", "content": b"not sqlite"}, admin_id)

    def test_admin_ban_clears_sessions_and_hides_banned_user(self):
        admin_id = app.create_user("admin", "password123", "Admin")
        target_id = app.create_user("problem", "password123", "Problem")
        token, _ = app.create_session(target_id)

        app.admin_set_user_ban(admin_id, target_id, True, "spam")
        target = app.row("SELECT * FROM users WHERE id = ?", (target_id,))

        self.assertEqual(target["is_banned"], 1)
        self.assertEqual(target["ban_reason"], "spam")
        self.assertIsNone(app.get_user_by_session(token))

    def test_admin_cannot_ban_self(self):
        admin_id = app.create_user("admin", "password123", "Admin")

        with self.assertRaisesRegex(ValueError, "own account"):
            app.admin_set_user_ban(admin_id, admin_id, True, "nope")

    def test_admin_password_reset_changes_password_and_clears_sessions(self):
        admin_id = app.create_user("admin", "password123", "Admin")
        target_id = app.create_user("target", "password123", "Target")
        token, _ = app.create_session(target_id)

        app.admin_reset_user_password(admin_id, target_id, "temporary123", "temporary123")
        target = app.row("SELECT * FROM users WHERE id = ?", (target_id,))

        self.assertTrue(app.verify_password("temporary123", target["password_hash"]))
        self.assertIsNone(app.get_user_by_session(token))

    def test_admin_actions_are_written_to_audit_log(self):
        admin_id = app.create_user("admin", "password123", "Admin")
        target_id = app.create_user("target", "password123", "Target")

        app.admin_set_user_ban(admin_id, target_id, True, "spam")
        app.admin_reset_user_password(admin_id, target_id, "temporary123", "temporary123")
        app.admin_set_user_role(admin_id, target_id, True)
        app.admin_update_notes(target_id, "Private note", admin_id)
        app.admin_set_user_trust(target_id, "trust", admin_id)

        logs = app.rows("SELECT * FROM admin_audit_log ORDER BY id")
        actions = [item["action"] for item in logs]

        self.assertEqual(
            actions,
            ["user_banned", "password_reset", "admin_granted", "admin_notes_updated", "trust_granted"],
        )
        self.assertIn("spam", logs[0]["details"])
        self.assertNotIn("temporary123", " ".join(item["details"] for item in logs))
        self.assertTrue(all(item["admin_user_id"] == admin_id for item in logs))
        self.assertTrue(all(item["target_user_id"] == target_id for item in logs))

    def test_admin_log_page_filters_and_renders_actions(self):
        admin_id = app.create_user("admin", "password123", "Admin")
        app.log_admin_action(admin_id, "registration_mode_updated", None, "setting", "Registration", "Invite-only registration enabled.")
        app.log_admin_action(admin_id, "backup_created", None, "backup", "binderbridge-backup-test.zip", "Manual backup downloaded.")
        admin = app.row("SELECT * FROM users WHERE id = ?", (admin_id,))

        overview = app.render_admin(admin)
        filtered = app.render_admin_logs(admin, {"action": ["registration_mode_updated"], "q": ["Registration"]})

        self.assertIn("Registration mode updated", overview)
        self.assertIn("Activity log", filtered)
        self.assertIn("Registration mode updated", filtered)
        self.assertIn("Invite-only registration enabled.", filtered)
        self.assertNotIn("binderbridge-backup-test.zip", filtered)

    def test_admin_role_requires_at_least_one_admin(self):
        admin_id = app.create_user("admin", "password123", "Admin")
        target_id = app.create_user("target", "password123", "Target")

        app.admin_set_user_role(admin_id, target_id, True)
        app.admin_set_user_role(target_id, admin_id, False)
        former_admin = app.row("SELECT * FROM users WHERE id = ?", (admin_id,))
        target = app.row("SELECT * FROM users WHERE id = ?", (target_id,))

        self.assertEqual(former_admin["is_admin"], 0)
        self.assertEqual(target["is_admin"], 1)
        with self.assertRaisesRegex(ValueError, "At least one admin"):
            app.admin_set_user_role(admin_id, target_id, False)

    def test_admin_trusted_trade_threshold_setting(self):
        self.assertEqual(app.trusted_trade_threshold(), 5)

        threshold = app.set_trusted_trade_threshold("3")

        self.assertEqual(threshold, 3)
        self.assertEqual(app.trusted_trade_threshold(), 3)

    def test_admin_trade_policy_settings_render_and_persist(self):
        admin_id = app.create_user("admin", "password123", "Admin")
        admin = app.row("SELECT * FROM users WHERE id = ?", (admin_id,))

        html = app.render_admin(admin)
        settings = app.set_trade_policy_settings("admins", "4", "15", "35", "3", "180")
        updated_html = app.render_admin(admin)

        self.assertIn('/admin/trade-policy', html)
        self.assertIn('name="one_way_trade_policy"', html)
        self.assertIn('name="dispute_escalation_days"', html)
        self.assertIn('name="evidence_retention_days"', html)
        self.assertEqual(settings["one_way_policy"], "admins")
        self.assertEqual(settings["trusted_threshold"], 4)
        self.assertEqual(settings["fairness"]["warn_percent"], "15")
        self.assertEqual(settings["fairness"]["block_percent"], "35")
        self.assertEqual(settings["dispute_escalation_days"], 3)
        self.assertEqual(settings["evidence_retention_days"], 180)
        self.assertIn("Admins only", updated_html)

    def test_user_can_earn_trusted_status_from_completed_trades(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        app.set_trusted_trade_threshold(2)
        for _ in range(2):
            app.execute(
                """
                INSERT INTO trades (proposer_id, recipient_id, status, created_at, updated_at)
                VALUES (?, ?, 'completed', ?, ?)
                """,
                (alice_id, bob_id, app.now_iso(), app.now_iso()),
            )
        alice = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))

        self.assertTrue(app.is_trusted_user(alice))

    def test_admin_revocation_blocks_automatic_trusted_status(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        app.set_trusted_trade_threshold(1)
        app.execute(
            """
            INSERT INTO trades (proposer_id, recipient_id, status, created_at, updated_at)
            VALUES (?, ?, 'completed', ?, ?)
            """,
            (alice_id, bob_id, app.now_iso(), app.now_iso()),
        )

        app.admin_set_user_trust(alice_id, "revoke")
        alice = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))

        self.assertFalse(app.is_trusted_user(alice))
        app.admin_set_user_trust(alice_id, "reset")
        alice = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))
        self.assertTrue(app.is_trusted_user(alice))

    def test_update_user_profile_changes_account_fields(self):
        user_id = app.create_user("chandra", "password123", "Chandra")

        app.update_user_profile(
            user_id,
            "chandra_nalaar",
            "Chandra Nalaar",
            "chandra@example.com",
            "Mostly here for red cards.",
            True,
            "cardmarket",
            False,
            "5.25",
            False,
        )
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))

        self.assertEqual(user["username"], "chandra_nalaar")
        self.assertEqual(user["display_name"], "Chandra Nalaar")
        self.assertEqual(user["email"], "chandra@example.com")
        self.assertEqual(user["bio"], "Mostly here for red cards.")
        self.assertEqual(user["public_email"], 1)
        self.assertEqual(user["preferred_price_source"], "scryfall")
        self.assertEqual(user["price_alerts_enabled"], 0)
        self.assertEqual(user["price_alert_threshold_percent"], "5.25")
        self.assertEqual(user["watchlist_alerts_enabled"], 0)
        self.assertEqual(user["notify_trade_offer_enabled"], 1)
        self.assertEqual(user["notify_import_complete_enabled"], 1)
        self.assertEqual(user["notify_admin_notice_enabled"], 1)

    def test_update_user_profile_rejects_duplicate_username(self):
        app.create_user("liliana", "password123", "Liliana")
        user_id = app.create_user("gideon", "password123", "Gideon")

        with self.assertRaisesRegex(ValueError, "already taken"):
            app.update_user_profile(user_id, "liliana", "Gideon Jura", "", "", False)

    def test_change_password_updates_hash_and_removes_other_sessions(self):
        user_id = app.create_user("nissa", "password123", "Nissa")
        keep_token, _ = app.create_session(user_id)
        other_token, _ = app.create_session(user_id)

        app.change_user_password(user_id, "password123", "newpassword123", "newpassword123", keep_session_token=keep_token)
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))

        self.assertTrue(app.verify_password("newpassword123", user["password_hash"]))
        self.assertIsNotNone(app.get_user_by_session(keep_token))
        self.assertIsNone(app.get_user_by_session(other_token))

    def test_change_password_requires_current_password(self):
        user_id = app.create_user("teferi", "password123", "Teferi")

        with self.assertRaisesRegex(ValueError, "Current password"):
            app.change_user_password(user_id, "wrong", "newpassword123", "newpassword123")

    def test_account_page_renders_profile_and_password_forms(self):
        user_id = app.create_user("vivien", "password123", "Vivien")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        original_configured = app.email_delivery_configured
        app.email_delivery_configured = lambda: False
        try:
            html = app.render_account(user)
        finally:
            app.email_delivery_configured = original_configured

        self.assertIn('action="/account/profile"', html)
        self.assertIn('action="/account/password"', html)
        self.assertIn('href="/account/export"', html)
        self.assertIn("Download account data", html)
        self.assertIn("API access", html)
        self.assertIn('action="/account/api-tokens"', html)
        self.assertIn('action="/account/webhooks"', html)
        self.assertIn("Show email on my member profile", html)
        self.assertIn("Notification preferences", html)
        self.assertIn("Price alerts", html)
        self.assertIn('name="price_alert_threshold_percent"', html)
        self.assertIn("Watchlist alerts", html)
        self.assertIn('name="watchlist_alerts_enabled"', html)
        self.assertIn('name="notify_trade_offer_enabled"', html)
        self.assertIn('name="notify_import_complete_enabled"', html)
        self.assertIn('name="notify_admin_notice_enabled"', html)
        self.assertNotIn("Email notifications", html)
        self.assertNotIn('name="email_trade_notifications_enabled"', html)
        self.assertNotIn('name="email_trade_offer_enabled"', html)
        self.assertNotIn('name="email_price_alert_enabled"', html)
        self.assertNotIn("Default trade price basis", html)

    def test_account_page_renders_email_preferences_when_smtp_configured(self):
        user_id = app.create_user("vivien", "password123", "Vivien")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        original_configured = app.email_delivery_configured
        app.email_delivery_configured = lambda: True
        try:
            html = app.render_account(user)
        finally:
            app.email_delivery_configured = original_configured

        self.assertIn("Email notifications", html)
        self.assertIn('name="email_trade_notifications_enabled"', html)
        self.assertIn('name="email_trade_offer_enabled"', html)
        self.assertIn('name="email_price_alert_enabled"', html)
        self.assertIn('name="email_import_complete_enabled"', html)
        self.assertIn('name="email_admin_notice_enabled"', html)
        self.assertIn("SMTP configured", html)

    def test_api_token_can_authenticate_and_be_revoked(self):
        user_id = app.create_user("apiuser", "password123", "API User")

        created = app.create_api_token(user_id, "Sync token", ["read", "write"])
        found_user, token_row = app.get_user_by_api_token(created["token"])
        app.revoke_api_token(user_id, created["id"])
        revoked_user, revoked_token = app.get_user_by_api_token(created["token"])

        self.assertTrue(created["token"].startswith(app.API_TOKEN_PREFIX))
        self.assertEqual(found_user["username"], "apiuser")
        self.assertEqual(token_row["scopes"], "read,write")
        self.assertIsNone(revoked_user)
        self.assertIsNone(revoked_token)

    def test_integration_access_policy_hides_and_enforces_api_and_webhooks(self):
        admin_id = app.create_user("admin", "password123", "Admin")
        user_id = app.create_user("apiuser", "password123", "API User")
        trusted_id = app.create_user("trusted", "password123", "Trusted User")
        token = app.create_api_token(user_id, "Existing token", ["read"])
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        trusted = app.row("SELECT * FROM users WHERE id = ?", (trusted_id,))
        admin = app.row("SELECT * FROM users WHERE id = ?", (admin_id,))

        settings = app.set_integration_access_settings("admins", "trusted")
        restricted_html = app.render_account(user)

        self.assertEqual(settings["api_policy"], "admins")
        self.assertEqual(settings["webhook_policy"], "trusted")
        self.assertNotIn("API access", restricted_html)
        self.assertNotIn('action="/account/api-tokens"', restricted_html)
        self.assertNotIn('action="/account/webhooks"', restricted_html)
        self.assertEqual(app.get_user_by_api_token(token["token"]), (None, None))
        with self.assertRaisesRegex(ValueError, "API access"):
            app.create_api_token(user_id, "Blocked", ["read"])
        with self.assertRaisesRegex(ValueError, "Webhook access"):
            app.create_webhook_endpoint(user_id, "Blocked hook", "https://example.com/hook")

        app.admin_set_user_trust(trusted_id, "trust")
        trusted = app.row("SELECT * FROM users WHERE id = ?", (trusted_id,))
        trusted_html = app.render_account(trusted)
        admin_html = app.render_account(admin)
        trusted_hook = app.create_webhook_endpoint(
            trusted_id,
            "Trusted hook",
            "https://example.com/trusted",
            ["notification.created"],
            "secret",
        )

        self.assertNotIn('action="/account/api-tokens"', trusted_html)
        self.assertIn('action="/account/webhooks"', trusted_html)
        self.assertIn('action="/account/api-tokens"', admin_html)
        self.assertIn('action="/account/webhooks"', admin_html)
        self.assertTrue(trusted_hook["id"])

        with app.db() as conn:
            queued = app.queue_notification_webhooks(
                trusted_id,
                99,
                "trade_offer",
                "Trade offer",
                "A trade arrived.",
                "/trades/1",
                1,
                conn=conn,
            )
        self.assertEqual(queued, 1)

        app.set_integration_access_settings("admins", "disabled")
        result = app.send_pending_webhook_deliveries(user_id=trusted_id, limit=5)
        delivery = app.row("SELECT * FROM webhook_deliveries WHERE user_id = ?", (trusted_id,))

        self.assertEqual(result["failed"], 1)
        self.assertEqual(delivery["status"], "failed")
        self.assertIn("Webhook access", delivery["error"])

    def test_api_authenticate_reports_invalid_token_as_handled_error(self):
        class DummyApiRequest:
            headers = {"Authorization": "Bearer bbapi_missing"}
            command = "GET"
            _request_path = "/api/v1/me"

            def __init__(self):
                self.error = None

            def api_error(self, message, status):
                self.error = (message, status)

            def client_ip(self):
                return "127.0.0.1"

        request = DummyApiRequest()

        user, token_row, handled = app.api_authenticate(request, "read")
        audit_log = app.row("SELECT * FROM admin_audit_log WHERE action = 'api_auth_failed'")

        self.assertIsNone(user)
        self.assertIsNone(token_row)
        self.assertTrue(handled)
        self.assertIsNotNone(audit_log)
        self.assertEqual(request.error[0], "A valid API bearer token is required.")

    def test_notification_webhooks_are_queued_and_delivered_with_payload(self):
        user_id = app.create_user("hookuser", "password123", "Hook User")
        app.create_webhook_endpoint(
            user_id,
            "Trade webhook",
            "https://example.com/binderbridge",
            ["notification.created", "trade.offer"],
            "secret",
        )
        with app.db() as conn:
            queued = app.queue_notification_webhooks(
                user_id,
                42,
                "trade_offer",
                "New trade offer",
                "Someone sent a trade.",
                "/trades/7",
                7,
                conn=conn,
            )
        deliveries = app.rows("SELECT * FROM webhook_deliveries ORDER BY event_type")
        calls = []
        original_sender = app.send_webhook_http_request

        def fake_sender(endpoint, delivery):
            calls.append((endpoint["url"], delivery["event_type"], json.loads(delivery["payload_json"])))
            return 204, "ok"

        app.send_webhook_http_request = fake_sender
        try:
            result = app.send_pending_webhook_deliveries(user_id=user_id)
        finally:
            app.send_webhook_http_request = original_sender

        updated = app.rows("SELECT status FROM webhook_deliveries ORDER BY event_type")

        self.assertEqual(queued, 2)
        self.assertEqual([item["event_type"] for item in deliveries], ["notification.created", "trade.offer"])
        self.assertEqual(result["sent"], 2)
        self.assertEqual([item["status"] for item in updated], ["sent", "sent"])
        self.assertEqual(calls[0][0], "https://example.com/binderbridge")
        self.assertEqual(calls[0][2]["data"]["notification"]["id"], 42)

    def test_totp_two_factor_setup_login_and_recovery_codes(self):
        user_id = app.create_user("secure", "password123", "Secure User")

        setup = app.start_user_totp_setup(user_id)
        code = app.totp_code(setup["secret"])
        recovery_codes = app.enable_user_totp(user_id, code)
        enabled = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        challenge_token, _ = app.create_two_factor_challenge(user_id)
        verified_user, method = app.complete_two_factor_login(challenge_token, code)
        recovery_token, _ = app.create_two_factor_challenge(user_id)
        recovered_user, recovery_method = app.complete_two_factor_login(recovery_token, recovery_codes[0])
        after_recovery = app.row("SELECT * FROM users WHERE id = ?", (user_id,))

        self.assertTrue(app.two_factor_enabled(enabled))
        self.assertIn("otpauth://totp/", setup["otpauth_uri"])
        self.assertIn("<svg", setup["qr_svg"])
        self.assertIn("Two-factor setup QR code", setup["qr_svg"])
        self.assertEqual(len(app.qr_matrix(setup["otpauth_uri"])), app.QR_SIZE)
        self.assertEqual(len(recovery_codes), 10)
        self.assertEqual(verified_user["id"], user_id)
        self.assertEqual(method, "totp")
        self.assertEqual(recovered_user["id"], user_id)
        self.assertEqual(recovery_method, "recovery")
        self.assertEqual(len(app.load_recovery_code_hashes(after_recovery)), 9)
        with self.assertRaisesRegex(ValueError, "did not match"):
            app.complete_two_factor_login(app.create_two_factor_challenge(user_id)[0], recovery_codes[0])

    def test_account_page_renders_two_factor_controls_and_recovery_codes(self):
        user_id = app.create_user("totpui", "password123", "TOTP UI")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))

        initial_html = app.render_account(user)
        setup = app.start_user_totp_setup(user_id)
        setup_user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        setup_html = app.render_account(setup_user)
        recovery_codes = app.enable_user_totp(user_id, app.totp_code(setup["secret"]))
        enabled_user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        enabled_html = app.render_account(enabled_user, recovery_codes=recovery_codes)
        challenge_html = app.render_two_factor_login("challenge-token")

        self.assertIn("Two-factor authentication", initial_html)
        self.assertIn("Start 2FA setup", initial_html)
        self.assertIn("Scan with your authenticator app", setup_html)
        self.assertIn("totp-qr-svg", setup_html)
        self.assertIn("Manual setup key", setup_html)
        self.assertIn("Authenticator URI", setup_html)
        self.assertIn("Enable 2FA", setup_html)
        self.assertIn("Save these recovery codes now", enabled_html)
        self.assertIn(recovery_codes[0], enabled_html)
        self.assertIn("Generate new recovery codes", enabled_html)
        self.assertIn("Disable 2FA", enabled_html)
        self.assertIn('action="/login/2fa"', challenge_html)
        self.assertIn("Verify and sign in", challenge_html)

    def test_admin_can_reset_user_two_factor(self):
        admin_id = app.create_user("admin2fa", "password123", "Admin 2FA")
        target_id = app.create_user("target2fa", "password123", "Target 2FA")
        setup = app.start_user_totp_setup(target_id)
        app.enable_user_totp(target_id, app.totp_code(setup["secret"]))
        admin = app.row("SELECT * FROM users WHERE id = ?", (admin_id,))
        before_reset_html = app.render_admin(admin)

        app.admin_reset_user_two_factor(admin_id, target_id)
        target = app.row("SELECT * FROM users WHERE id = ?", (target_id,))
        logs = app.rows("SELECT * FROM admin_audit_log WHERE action = 'two_factor_reset'")

        self.assertIn("2FA on", before_reset_html)
        self.assertIn("/admin/user/", before_reset_html)
        self.assertIn("Reset 2FA", before_reset_html)
        self.assertFalse(app.two_factor_enabled(target))
        self.assertEqual(target["totp_secret"], "")
        self.assertEqual(len(logs), 1)

    def test_layout_renders_theme_toggle(self):
        user_id = app.create_user("themeuser", "password123", "Theme User")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))

        html = app.render_layout(user, "Theme test", "<p>Theme</p>")

        self.assertIn('id="theme-toggle"', html)
        self.assertIn("binderbridge_theme", html)
        self.assertIn("Light mode", html)
        self.assertIn('href="/collection"', html)
        self.assertIn(">My Cards</a>", html)
        self.assertIn('href="/wants"', html)
        self.assertIn(">Wishlist</a>", html)
        self.assertIn('href="/browse"', html)
        self.assertIn(">Browse</a>", html)
        self.assertNotIn(">Import</a>", html)
        self.assertNotIn(">Groups</a>", html)

    def test_deck_binder_and_wishlist_groups_manage_items(self):
        user_id = app.create_user("organizer", "password123", "Organizer")
        other_id = app.create_user("outsider", "password123", "Outsider")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, collector_number, finish, condition, language,
                 quantity, quantity_for_trade, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', '703', 'Regular', 'NM', 'English',
                    2, 1, '1.25', 'scryfall', ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        want_id = app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, set_name, desired_quantity, created_at, updated_at)
            VALUES (?, 'mtg', 'Rhystic Study', 'Wilds of Eldraine', 1, ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )

        deck_id = app.create_card_group(user_id, "deck", "Commander deck", "Cards currently sleeved.")
        binder_id = app.create_card_group(user_id, "binder", "Trade binder")
        wishlist_id = app.create_card_group(user_id, "wishlist", "High priority")
        app.add_collection_item_to_group(user_id, deck_id, card_id, 5)
        app.add_collection_item_to_group(user_id, binder_id, card_id, 1)
        app.add_want_item_to_group(user_id, wishlist_id, want_id)

        deck_items = app.collection_group_items(deck_id)
        groups_html = app.render_groups(user)
        wishlist_groups_html = app.render_groups(user, view="wishlist")
        deck_html = app.render_group_detail(user, deck_id)
        wishlist_html = app.render_group_detail(user, wishlist_id)

        self.assertEqual(deck_items[0]["group_quantity"], 2)
        self.assertIn("Commander deck", groups_html)
        self.assertIn("Trade binder", groups_html)
        self.assertNotIn("High priority", groups_html)
        self.assertIn("High priority", wishlist_groups_html)
        self.assertIn("Decks &amp; Binders", groups_html)
        self.assertIn("Wishlist Groups", wishlist_groups_html)
        self.assertIn("Visibility", groups_html)
        self.assertIn('<option value="1" selected>Public</option>', groups_html)
        self.assertIn("2 x Sol Ring", deck_html)
        self.assertIn("Add card", deck_html)
        self.assertIn("Make private", deck_html)
        self.assertIn(f'/groups/{deck_id}/export', deck_html)
        self.assertIn("Rhystic Study", wishlist_html)
        self.assertIn("Add want", wishlist_html)
        with self.assertRaisesRegex(ValueError, "Deck and binder"):
            app.add_collection_item_to_group(other_id, deck_id, card_id, 1)
        removed = app.remove_group_item(user_id, deck_id, deck_items[0]["group_item_id"])
        self.assertEqual(removed, 1)
        self.assertEqual(app.collection_group_items(deck_id), [])
        self.assertEqual(app.delete_card_group(user_id, binder_id), 1)
        self.assertIsNone(app.user_group(user_id, binder_id))

    def test_group_visibility_controls_public_member_group_views(self):
        owner_id = app.create_user("groupowner", "password123", "Group Owner")
        viewer_id = app.create_user("groupviewer", "password123", "Group Viewer")
        viewer = app.row("SELECT * FROM users WHERE id = ?", (viewer_id,))
        public_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, quantity, quantity_for_trade, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Public Group Card', 1, 0, 1, ?, ?)
            """,
            (owner_id, app.now_iso(), app.now_iso()),
        )
        private_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, quantity, quantity_for_trade, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Private Group Card', 1, 0, 0, ?, ?)
            """,
            (owner_id, app.now_iso(), app.now_iso()),
        )
        public_group_id = app.create_card_group(owner_id, "binder", "Public Binder", "", True)
        private_group_id = app.create_card_group(owner_id, "deck", "Private Deck", "", False)
        app.add_collection_item_to_group(owner_id, public_group_id, public_card_id, 1)
        app.add_collection_item_to_group(owner_id, public_group_id, private_card_id, 1)

        member_html = app.render_member_detail(viewer, owner_id)
        public_group_html = app.render_public_group_detail(viewer, owner_id, public_group_id)
        private_group_html = app.render_public_group_detail(viewer, owner_id, private_group_id)
        updated = app.update_card_group_visibility(owner_id, private_group_id, True)
        member_after_update = app.render_member_detail(viewer, owner_id)

        self.assertIn("Public Binder", member_html)
        self.assertNotIn("Private Deck", member_html)
        self.assertIn("Public Group Card", public_group_html)
        self.assertNotIn("Private Group Card", public_group_html)
        self.assertIsNone(private_group_html)
        self.assertEqual(updated, 1)
        self.assertIn("Private Deck", member_after_update)

    def test_group_detail_sorts_collection_and_wishlist_items(self):
        user_id = app.create_user("groupsorter", "password123", "Group Sorter")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        deck_id = app.create_card_group(user_id, "deck", "Sorted deck")
        wishlist_id = app.create_card_group(user_id, "wishlist", "Sorted wants")
        for name, quantity, price in [
            ("Low Group Value", 1, "1.00"),
            ("High Group Value", 3, "5.00"),
        ]:
            card_id = app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, quantity, quantity_for_trade, price_usd, created_at, updated_at)
                VALUES (?, 'mtg', ?, 4, 1, ?, ?, ?)
                """,
                (user_id, name, price, app.now_iso(), app.now_iso()),
            )
            app.add_collection_item_to_group(user_id, deck_id, card_id, quantity)
        for name, desired in [("Small Group Want", 1), ("Big Group Want", 4)]:
            want_id = app.execute(
                """
                INSERT INTO want_items
                    (user_id, game, card_name, desired_quantity, created_at, updated_at)
                VALUES (?, 'mtg', ?, ?, ?, ?)
                """,
                (user_id, name, desired, app.now_iso(), app.now_iso()),
            )
            app.add_want_item_to_group(user_id, wishlist_id, want_id)

        deck_html = app.render_group_detail(user, deck_id, query={"sort": ["value"], "dir": ["desc"]})
        wishlist_html = app.render_group_detail(user, wishlist_id, query={"sort": ["qty"], "dir": ["desc"]})

        self.assertIn('name="sort"', deck_html)
        self.assertIn('value="value" selected', deck_html)
        self.assertLess(deck_html.index("High Group Value"), deck_html.index("Low Group Value"))
        self.assertIn('value="qty" selected', wishlist_html)
        self.assertLess(wishlist_html.index("Big Group Want"), wishlist_html.index("Small Group Want"))

    def test_deck_group_bulk_imports_csv_and_deck_text(self):
        user_id = app.create_user("deckimport", "password123", "Deck Import")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        deck_id = app.create_card_group(user_id, "deck", "Friday commander")
        for name, set_code, collector_number, quantity in (
            ("Lightning Bolt", "", "182", 3),
            ("Sol Ring", "", "", 1),
            ("Counterspell", "DMR", "45", 4),
        ):
            app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, set_code, collector_number, quantity, quantity_for_trade, created_at, updated_at)
                VALUES (?, 'mtg', ?, ?, ?, ?, 0, ?, ?)
                """,
                (user_id, name, set_code, collector_number, quantity, app.now_iso(), app.now_iso()),
            )
        csv_bytes = (
            "Quantity,Name,Edition,Collector Number,Foil,Condition,Language\n"
            "1,Lightning Bolt,Secret Lair,182,Foil,NM,English\n"
            "2,Lightning Bolt,Secret Lair,182,Foil,NM,English\n"
        ).encode("utf-8")
        deck_text = """
Commander
1 Sol Ring #Mana
Deck
4 Counterspell (DMR) 45
Maybeboard
1 Mana Crypt
"""

        csv_result = app.import_deck_group_csv(user_id, deck_id, csv_bytes, source="archidekt", enrich_scryfall=False)
        text_result = app.import_deck_group_text(user_id, deck_id, deck_text, enrich_scryfall=False)
        grouped = {
            (item["card_name"], item["set_code"], item["collector_number"]): item["group_quantity"]
            for item in app.collection_group_items(deck_id)
        }
        html = app.render_group_detail(user, deck_id, import_result=text_result)

        self.assertEqual(csv_result["grouped"], 3)
        self.assertEqual(text_result["grouped"], 5)
        self.assertEqual(text_result["missing"], 0)
        self.assertEqual(grouped[("Lightning Bolt", "", "182")], 3)
        self.assertEqual(grouped[("Counterspell", "DMR", "45")], 4)
        self.assertEqual(grouped[("Sol Ring", "", "")], 1)
        self.assertNotIn(("Mana Crypt", "", ""), grouped)
        self.assertIn("Bulk import deck", html)
        self.assertIn("Deck-list URL", html)

    def test_deck_url_json_extracts_moxfield_and_archidekt_shapes(self):
        moxfield_items, _ = app.decklist_items_from_json({
            "commanders": {
                "Atraxa, Praetors' Voice": {
                    "quantity": 1,
                    "card": {"name": "Atraxa, Praetors' Voice", "set": "c16", "collector_number": "28"},
                }
            },
            "mainboard": {
                "Sol Ring": {
                    "quantity": 1,
                    "card": {"name": "Sol Ring", "set": "cmm", "collector_number": "703", "scryfall_id": "sf-sol"},
                }
            },
            "maybeboard": {
                "Mana Crypt": {"quantity": 1, "card": {"name": "Mana Crypt"}}
            },
        })
        archidekt_items, warnings = app.decklist_items_from_json({
            "cards": [
                {"quantity": 1, "card": {"name": "Arcane Signet", "set": "c20", "collector_number": "252"}},
                {"quantity": 1, "categories": ["Maybeboard"], "card": {"name": "Mana Crypt"}},
            ]
        })

        self.assertEqual([item["card_name"] for item in moxfield_items], ["Atraxa, Praetors' Voice", "Sol Ring"])
        self.assertEqual(moxfield_items[1]["set_code"], "CMM")
        self.assertEqual(moxfield_items[1]["scryfall_id"], "sf-sol")
        self.assertEqual(len(archidekt_items), 1)
        self.assertEqual(archidekt_items[0]["card_name"], "Arcane Signet")
        self.assertEqual(archidekt_items[0]["collector_number"], "252")
        self.assertTrue(any("Excluded" in warning for warning in warnings))

    def test_deck_import_review_detects_optional_sections(self):
        user_id = app.create_user("sectionreview", "password123", "Section Review")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        deck_id = app.create_card_group(user_id, "deck", "Sectioned deck")
        for name, quantity in (("Sol Ring", 1), ("Mana Crypt", 1), ("Treasure Token", 3)):
            app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, quantity, quantity_for_trade, created_at, updated_at)
                VALUES (?, 'mtg', ?, ?, 0, ?, ?)
                """,
                (user_id, name, quantity, app.now_iso(), app.now_iso()),
            )
        deck_text = """
Deck
1 Sol Ring
Sideboard
2 Dispel
Maybeboard
1 Mana Crypt
Tokens
3 Treasure Token
Considering
1 Rhystic Study
"""

        section_rows, warnings = app.deck_import_sections_from_text(deck_text)
        review = app.deck_import_preview_payload("decklist", section_rows, warnings=warnings, enrich_scryfall=False, merge=True)
        html = app.render_group_detail(user, deck_id, import_review=review)
        result = app.import_deck_group_sections(
            user_id,
            deck_id,
            section_rows,
            included_sections={"maybeboard", "tokens"},
            enrich_scryfall=False,
        )
        grouped = {
            item["card_name"]: item["group_quantity"]
            for item in app.collection_group_items(deck_id)
        }

        self.assertTrue(app.deck_import_sections_need_review(section_rows))
        self.assertIn("Review detected sections", html)
        self.assertIn("Sideboard", html)
        self.assertIn("Maybeboard", html)
        self.assertIn("Tokens", html)
        self.assertIn("Considering", html)
        self.assertEqual(result["grouped"], 5)
        self.assertEqual(grouped["Sol Ring"], 1)
        self.assertEqual(grouped["Mana Crypt"], 1)
        self.assertEqual(grouped["Treasure Token"], 3)
        self.assertNotIn("Dispel", grouped)
        self.assertNotIn("Rhystic Study", grouped)

    def test_deck_import_preview_commits_and_undoes_group_changes(self):
        user_id = app.create_user("deckpreview", "password123", "Deck Preview")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        deck_id = app.create_card_group(user_id, "deck", "Preview deck")
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 2, 0, ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        section_rows, warnings = app.deck_import_sections_from_text("1 Sol Ring\n1 Mana Crypt\n")

        preview = app.preview_deck_group_import(
            user_id,
            deck_id,
            section_rows,
            enrich_scryfall=False,
            warnings=warnings,
        )
        preview_html = app.render_group_detail(user, deck_id, import_review=preview)
        before_commit = app.collection_group_items(deck_id)
        result = app.commit_deck_import_preview(user_id, deck_id, preview["batch_id"])
        grouped = app.collection_group_items(deck_id)
        result_html = app.render_group_detail(user, deck_id, import_result=result)
        undo = app.undo_import_batch(user_id, preview["batch_id"])
        after_undo = app.collection_group_items(deck_id)
        batch = app.row("SELECT * FROM import_batches WHERE id = ?", (preview["batch_id"],))

        self.assertEqual(preview["grouped"], 1)
        self.assertEqual(preview["missing"], 1)
        self.assertEqual(before_commit, [])
        self.assertIn("Preview deck import", preview_html)
        self.assertIn("Import deck cards", preview_html)
        self.assertEqual(result["grouped"], 1)
        self.assertEqual(result["missing"], 1)
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["card_name"], "Sol Ring")
        self.assertIn("Add missing cards to wishlist", result_html)
        self.assertEqual(undo["undone_items"], 1)
        self.assertEqual(after_undo, [])
        self.assertEqual(batch["status"], "undone")

    def test_deck_import_prompts_missing_cards_for_grouped_wishlist(self):
        user_id = app.create_user("deckmissing", "password123", "Deck Missing")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        deck_id = app.create_card_group(user_id, "deck", "Boros build")
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 1, 0, ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        deck_text = """
Deck
2 Sol Ring
1 Mana Crypt
"""

        result = app.import_deck_group_text(user_id, deck_id, deck_text, enrich_scryfall=False)
        html = app.render_group_detail(user, deck_id, import_result=result)

        self.assertEqual(result["grouped"], 1)
        self.assertEqual(result["missing"], 2)
        self.assertEqual({item["card_name"]: item["quantity"] for item in result["missing_items"]}, {"Sol Ring": 1, "Mana Crypt": 1})
        self.assertIn("Add missing cards to wishlist", html)
        self.assertIn('/groups/1/missing-wants', html)
        self.assertIn("1 x Sol Ring", html)
        self.assertIn("1 x Mana Crypt", html)

    def test_deck_missing_cards_can_be_added_to_new_wishlist_group(self):
        user_id = app.create_user("deckwants", "password123", "Deck Wants")
        deck_id = app.create_card_group(user_id, "deck", "Dimir build")
        deck_text = """
Deck
1 Rhystic Study
2 Counterspell
"""
        result = app.import_deck_group_text(user_id, deck_id, deck_text, enrich_scryfall=False)
        selected = [item["key"] for item in result["missing_items"]]

        added = app.add_deck_missing_items_to_wishlist(
            user_id,
            deck_id,
            result["missing_items"],
            selected,
            new_group_name="Dimir wants",
        )
        wishlist = app.user_group(user_id, added["wishlist_group_id"])
        wants = app.wishlist_group_items(added["wishlist_group_id"])

        self.assertEqual(added["added"], 2)
        self.assertEqual(wishlist["group_type"], "wishlist")
        self.assertEqual(wishlist["name"], "Dimir wants")
        self.assertEqual({want["card_name"]: want["desired_quantity"] for want in wants}, {"Counterspell": 2, "Rhystic Study": 1})

    def test_collection_and_wants_csv_exports(self):
        user_id = app.create_user("exporter", "password123", "Exporter")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 2, 1, '1.25', 'scryfall', ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, type_line, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Forest', 'Basic Land', 4, 0, ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, desired_quantity, condition, finish, created_at, updated_at)
            VALUES (?, 'mtg', 'Rhystic Study', 1, 'NM,LP', 'Foil', ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )

        collection_filename, collection_csv = app.export_collection_csv(user_id, {"q": ["sol"]})
        wants_filename, wants_csv = app.export_wants_csv(user_id)
        collection_rows = list(csv.DictReader(io.StringIO(collection_csv.decode("utf-8-sig"))))
        want_rows = list(csv.DictReader(io.StringIO(wants_csv.decode("utf-8-sig"))))
        collection_html = app.render_collection(user, {})
        wants_html = app.render_wants(user)

        self.assertTrue(collection_filename.startswith("binderbridge-collection-"))
        self.assertTrue(wants_filename.startswith("binderbridge-wants-"))
        self.assertEqual([row["card_name"] for row in collection_rows], ["Sol Ring"])
        self.assertEqual(collection_rows[0]["quantity_for_trade"], "1")
        self.assertEqual(want_rows[0]["card_name"], "Rhystic Study")
        self.assertEqual(want_rows[0]["condition"], "NM,LP")
        self.assertIn('href="/collection/export?page=1"', collection_html)
        self.assertIn('href="/wants/export"', wants_html)

    def test_collection_statistics_summarize_values_and_breakdowns(self):
        user_id = app.create_user("statsuser", "password123", "Stats User")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        deck_id = app.create_card_group(user_id, "deck", "Stats deck")
        binder_id = app.create_card_group(user_id, "binder", "Stats binder")
        sol_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, set_code, collector_number, type_line, rarity,
                 condition, finish, language, quantity, quantity_for_trade, color_identity,
                 scryfall_id, image_url, price_usd, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 'CMM', '703', 'Artifact', 'uncommon',
                    'NM', 'Regular', 'English', 2, 1, '', 'sol-id', 'https://img.example/sol.jpg',
                    '1.25', 1, ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        bolt_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, set_code, collector_number, type_line, rarity,
                 condition, finish, language, quantity, quantity_for_trade, color_identity,
                 scryfall_id, price_usd, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Lightning Bolt', 'Secret Lair', 'SLD', '182', 'Instant', 'rare',
                    'LP', 'Foil', 'Japanese', 1, 1, 'R', 'bolt-id', '2.00', 0, ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, condition, finish, language,
                 quantity, quantity_for_trade, is_public, created_at, updated_at)
            VALUES (?, 'pokemon', 'Pikachu', 'Base Set', 'NM', 'Regular', 'English',
                    3, 0, 1, ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        app.add_collection_item_to_group(user_id, deck_id, sol_id, 1)
        app.add_collection_item_to_group(user_id, binder_id, bolt_id, 1)

        stats = app.collection_statistics(user_id)
        html = app.render_collection_statistics(user)
        collection_html = app.render_collection(user, {})

        self.assertEqual(stats["total_cards"], 6)
        self.assertEqual(stats["trade_cards"], 2)
        self.assertEqual(stats["unique_entries"], 3)
        self.assertEqual(stats["unique_cards"], 3)
        self.assertEqual(stats["total_value_cents"], 450)
        self.assertEqual(stats["trade_value_cents"], 325)
        self.assertEqual(stats["priced_cards"], 3)
        self.assertEqual(stats["public_cards"], 5)
        self.assertEqual(stats["private_cards"], 1)
        self.assertEqual(stats["price_coverage_percent"], 50.0)
        self.assertEqual(stats["buckets"]["condition"][0]["label"], "NM")
        self.assertEqual(stats["buckets"]["condition"][0]["quantity"], 5)
        self.assertEqual(stats["buckets"]["game"][0]["quantity"], 3)
        self.assertIn("Collection stats", html)
        self.assertIn("$4.50", html)
        self.assertIn("Rarity mix", html)
        self.assertIn("Condition mix", html)
        self.assertIn("Language coverage", html)
        self.assertIn("Most valuable entries", html)
        self.assertIn("Deck", html)
        self.assertIn("Binder", html)
        self.assertIn('href="/collection/stats"', collection_html)

    def test_group_csv_exports_decks_and_wishlist_groups(self):
        user_id = app.create_user("groupexport", "password123", "Group Export")
        card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 2, 0, ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        want_id = app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, desired_quantity, created_at, updated_at)
            VALUES (?, 'mtg', 'Mana Crypt', 1, ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        deck_id = app.create_card_group(user_id, "deck", "Export Deck")
        wishlist_id = app.create_card_group(user_id, "wishlist", "Export Wants")
        app.add_collection_item_to_group(user_id, deck_id, card_id, 2)
        app.add_want_item_to_group(user_id, wishlist_id, want_id)

        deck_filename, deck_csv = app.export_group_csv(user_id, deck_id)
        wishlist_filename, wishlist_csv = app.export_group_csv(user_id, wishlist_id)
        deck_rows = list(csv.DictReader(io.StringIO(deck_csv.decode("utf-8-sig"))))
        wishlist_rows = list(csv.DictReader(io.StringIO(wishlist_csv.decode("utf-8-sig"))))

        self.assertIn("export-deck", deck_filename)
        self.assertIn("export-wants", wishlist_filename)
        self.assertEqual(deck_rows[0]["group_name"], "Export Deck")
        self.assertEqual(deck_rows[0]["group_quantity"], "2")
        self.assertEqual(deck_rows[0]["card_name"], "Sol Ring")
        self.assertEqual(wishlist_rows[0]["group_name"], "Export Wants")
        self.assertEqual(wishlist_rows[0]["desired_quantity"], "1")
        self.assertEqual(wishlist_rows[0]["card_name"], "Mana Crypt")

    def test_full_account_json_export_includes_user_owned_data_without_password_hash(self):
        user_id = app.create_user("fullexport", "password123", "Full Export")
        other_id = app.create_user("other", "password123", "Other")
        card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 1, 0, ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        want_id = app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, desired_quantity, created_at, updated_at)
            VALUES (?, 'mtg', 'Mana Crypt', 1, ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        group_id = app.create_card_group(user_id, "deck", "Exported Deck")
        app.add_collection_item_to_group(user_id, group_id, card_id, 1)
        app.create_notification(user_id, "trade_status", "Export notice", "Included in account export.")
        trade_id = app.execute(
            """
            INSERT INTO trades (proposer_id, recipient_id, status, created_at, updated_at)
            VALUES (?, ?, 'completed', ?, ?)
            """,
            (user_id, other_id, app.now_iso(), app.now_iso()),
        )

        filename, data = app.export_account_json(user_id)
        exported = json.loads(data.decode("utf-8"))

        self.assertIn("fullexport", filename)
        self.assertEqual(exported["format"], "binderbridge-account-export")
        self.assertEqual(exported["account"]["username"], "fullexport")
        self.assertNotIn("password_hash", exported["account"])
        self.assertEqual(exported["collection"][0]["card_name"], "Sol Ring")
        self.assertEqual(exported["wants"][0]["id"], want_id)
        self.assertEqual(exported["groups"][0]["name"], "Exported Deck")
        self.assertEqual(exported["groups"][0]["items"][0]["card_name"], "Sol Ring")
        self.assertEqual(exported["trades"][0]["id"], trade_id)
        self.assertEqual(exported["notifications"][0]["title"], "Export notice")

    def test_collection_quantity_validation(self):
        form = {
            "card_name": ["Sol Ring"],
            "game": ["mtg"],
            "quantity": ["1"],
            "quantity_for_trade": ["2"],
        }

        with self.assertRaisesRegex(ValueError, "cannot be higher"):
            app.validate_collection_form(form)

    def test_input_sanitization_removes_controls_and_caps_length(self):
        dirty = "Safe\x00<script>\x08\nTabbed\t"
        self.assertEqual(app.sanitize_text_input(dirty), "Safe<script>\nTabbed\t")

        long_text = "x" * (app.MAX_FORM_VALUE_LENGTH + 25)
        self.assertEqual(len(app.sanitize_text_input(long_text)), app.MAX_FORM_VALUE_LENGTH)

        form = app.sanitize_form_values({"na\x00me": ["one\x01", "two"], "": ["skip"]})
        self.assertEqual(form["name"], ["one", "two"])
        self.assertNotIn("", form)

    def test_safe_local_redirect_rejects_external_or_wrong_paths(self):
        self.assertEqual(
            app.safe_local_redirect_path("/collection?page=1", default="/collection", allowed_prefix="/collection"),
            "/collection?page=1",
        )
        self.assertEqual(
            app.safe_local_redirect_path("/collection/new", default="/collection", allowed_prefix="/collection"),
            "/collection/new",
        )
        for target in ("//evil.example/collection", "https://evil.example", "/admin", "/collectionish", "/collection\\bad"):
            self.assertEqual(
                app.safe_local_redirect_path(target, default="/collection", allowed_prefix="/collection"),
                "/collection",
            )

    def test_large_collection_quantities_are_capped(self):
        form = {
            "card_name": ["Sol Ring"],
            "game": ["mtg"],
            "quantity": [str(app.MAX_CARD_QUANTITY * 10)],
            "quantity_for_trade": [str(app.MAX_CARD_QUANTITY * 10)],
        }

        data = app.validate_collection_form(form)

        self.assertEqual(data["quantity"], app.MAX_CARD_QUANTITY)
        self.assertEqual(data["quantity_for_trade"], app.MAX_CARD_QUANTITY)

    def test_csv_import_sanitizes_text_and_caps_quantity(self):
        user_id = app.create_user("csvsafe", "password123", "CSV Safe")
        csv_data = (
            "Name,Quantity,Notes\n"
            f"Unsafe\x00 Bolt,{app.MAX_CARD_QUANTITY * 10},hello\x01 world\n"
        ).encode("utf-8")

        result = app.import_collection_csv(user_id, csv_data, enrich_scryfall=False, merge=False)
        item = app.row("SELECT * FROM collection_items WHERE user_id = ?", (user_id,))

        self.assertEqual(result["inserted"], 1)
        self.assertEqual(item["card_name"], "Unsafe Bolt")
        self.assertEqual(item["quantity"], app.MAX_CARD_QUANTITY)
        self.assertEqual(item["notes"], "hello world")

    def test_trade_quantity_picker_caps_to_owner_and_tradeable_quantity(self):
        owner_id = app.create_user("alice", "password123", "Alice")
        other_id = app.create_user("bob", "password123", "Bob")
        item_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Lightning Bolt', 4, 2, ?, ?)
            """,
            (owner_id, app.now_iso(), app.now_iso()),
        )

        valid = app.parse_trade_quantities({f"offer_{item_id}": ["2"]}, "offer", owner_id)
        too_many = app.parse_trade_quantities({f"offer_{item_id}": ["3"]}, "offer", owner_id)
        wrong_owner = app.parse_trade_quantities({f"offer_{item_id}": ["1"]}, "offer", other_id)

        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0][1], 2)
        self.assertEqual(too_many, [])
        self.assertEqual(wrong_owner, [])

    def test_new_trade_screen_filters_paginates_and_suggests_card_pickers(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        alice = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))
        for index in range(12):
            app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, set_name, type_line, quantity, quantity_for_trade, created_at, updated_at)
                VALUES (?, 'mtg', ?, 'Offer Set', 'Creature - Wizard', 1, 1, ?, ?)
                """,
                (alice_id, f"Offer Card {index:02d}", app.now_iso(), app.now_iso()),
            )
            app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, set_name, type_line, quantity, quantity_for_trade, created_at, updated_at)
                VALUES (?, 'mtg', ?, 'Request Set', 'Artifact', 1, 1, ?, ?)
                """,
                (bob_id, f"Request Card {index:02d}", app.now_iso(), app.now_iso()),
            )

        html = app.render_new_trade(
            alice,
            bob_id,
            {"recipient_id": [str(bob_id)], "offer_per_page": ["10"], "request_per_page": ["10"]},
        )

        self.assertIn('id="trade-submit-form"', html)
        self.assertIn("Selected for trade", html)
        self.assertIn('data-trade-summary="offer"', html)
        self.assertIn('data-trade-summary="request"', html)
        self.assertIn('name="offer_q"', html)
        self.assertIn('name="request_q"', html)
        self.assertIn('list="trade-offer-search-suggestions"', html)
        self.assertIn('list="trade-request-search-suggestions"', html)
        self.assertIn("Advanced filters", html)
        self.assertIn("offer_page=2", html)
        self.assertIn("request_page=2", html)
        self.assertIn('form="trade-submit-form"', html)
        self.assertIn("Review trade", html)

    def test_new_trade_screen_recommends_wishlist_matches(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        alice = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))
        alice_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Dockside Extortionist', 'Double Masters', 1, 1, '40.00', 'scryfall', ?, ?)
            """,
            (alice_id, app.now_iso(), app.now_iso()),
        )
        bob_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Rhystic Study', 'Wilds of Eldraine', 1, 1, '35.00', 'scryfall', ?, ?)
            """,
            (bob_id, app.now_iso(), app.now_iso()),
        )
        app.execute(
            """
            INSERT INTO want_items (user_id, game, card_name, desired_quantity, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Dockside Extortionist', 1, 1, ?, ?)
            """,
            (bob_id, app.now_iso(), app.now_iso()),
        )
        app.execute(
            """
            INSERT INTO want_items (user_id, game, card_name, desired_quantity, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Rhystic Study', 1, 0, ?, ?)
            """,
            (alice_id, app.now_iso(), app.now_iso()),
        )

        html = app.render_new_trade(alice, bob_id, {"recipient_id": [str(bob_id)]})

        self.assertIn("Trade recommendations", html)
        self.assertIn("They want from you", html)
        self.assertIn("Matches their wishlist", html)
        self.assertIn("Dockside Extortionist", html)
        self.assertIn(f'data-recommend-side="offer"', html)
        self.assertIn(f'data-recommend-id="{alice_card_id}"', html)
        self.assertIn("You want from them", html)
        self.assertIn("Matches your wishlist", html)
        self.assertIn("Rhystic Study", html)
        self.assertIn(f'data-recommend-side="request"', html)
        self.assertIn(f'data-recommend-id="{bob_card_id}"', html)

    def test_new_trade_screen_recommends_value_balance_helpers(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        alice = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))
        small_offer_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Arcane Signet', 'Commander Masters', 1, 1, '2.00', 'scryfall', ?, ?)
            """,
            (alice_id, app.now_iso(), app.now_iso()),
        )
        balance_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Smothering Tithe', 'Ravnica Allegiance', 1, 1, '18.00', 'scryfall', ?, ?)
            """,
            (alice_id, app.now_iso(), app.now_iso()),
        )
        request_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Cyclonic Rift', 'Double Masters', 1, 1, '20.00', 'scryfall', ?, ?)
            """,
            (bob_id, app.now_iso(), app.now_iso()),
        )
        form = {
            "recipient_id": [str(bob_id)],
            f"offer_{small_offer_id}": ["1"],
            f"request_{request_id}": ["1"],
        }

        html = app.render_new_trade(alice, bob_id, form)

        self.assertIn("Balance helpers for your offer", html)
        self.assertIn("Helps balance the higher request value", html)
        self.assertIn("Smothering Tithe", html)
        self.assertIn(f'data-recommend-id="{balance_card_id}"', html)

    def test_trade_review_allows_edit_before_send(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        alice = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))
        alice_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 2, 1, ?, ?)
            """,
            (alice_id, app.now_iso(), app.now_iso()),
        )
        bob_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Counterspell', 'Dominaria Remastered', 2, 1, ?, ?)
            """,
            (bob_id, app.now_iso(), app.now_iso()),
        )
        form = {
            "recipient_id": [str(bob_id)],
            f"offer_{alice_card_id}": ["1"],
            f"request_{bob_card_id}": ["1"],
            "proposer_note": ["Looks good"],
            "offer_q": ["sol"],
        }
        offered = app.parse_trade_quantities(form, "offer", alice_id)
        requested = app.parse_trade_quantities(form, "request", bob_id)

        review_html = app.render_trade_review(alice, bob_id, form, offered, requested)
        edit_html = app.render_new_trade(
            alice,
            bob_id,
            form,
            selected_quantities=app.trade_selected_quantities_from_form(form),
            proposer_note=form["proposer_note"][0],
        )

        self.assertIn("Confirm with Bob", review_html)
        self.assertIn("Edit trade", review_html)
        self.assertIn('name="intent" value="send"', review_html)
        self.assertIn("1 x Sol Ring", review_html)
        self.assertIn("Looks good", review_html)
        self.assertIn(f'name="offer_{alice_card_id}" value="1"', edit_html)
        self.assertIn("1 x Counterspell", edit_html)
        self.assertIn("Looks good", edit_html)

    def test_trade_value_balancing_uses_prices_and_sources(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        alice = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))
        alice_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 2, 1, '10.00', 'scryfall', ?, ?)
            """,
            (alice_id, app.now_iso(), app.now_iso()),
        )
        bob_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Counterspell', 'Dominaria Remastered', 2, 1, '12.00', 'manual', ?, ?)
            """,
            (bob_id, app.now_iso(), app.now_iso()),
        )
        form = {
            "recipient_id": [str(bob_id)],
            f"offer_{alice_card_id}": ["1"],
            f"request_{bob_card_id}": ["1"],
        }
        offered = app.parse_trade_quantities(form, "offer", alice_id)
        requested = app.parse_trade_quantities(form, "request", bob_id)

        builder_html = app.render_new_trade(alice, bob_id, form)
        review_html = app.render_trade_review(alice, bob_id, form, offered, requested)

        self.assertIn("$10.00", builder_html)
        self.assertIn("$12.00", builder_html)
        self.assertIn("Request side is $2.00 higher", builder_html)
        self.assertIn("Scryfall", builder_html)
        self.assertNotIn("Manual", builder_html)
        self.assertNotIn("Apply prices", builder_html)
        self.assertNotIn("data-price-basis-select", builder_html)
        self.assertIn("Request side is $2.00 higher", review_html)

    def test_trade_fairness_warning_requires_acknowledgement(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        alice = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))
        alice_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 2, 1, '10.00', 'scryfall', ?, ?)
            """,
            (alice_id, app.now_iso(), app.now_iso()),
        )
        bob_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Rhystic Study', 'Wilds of Eldraine', 2, 1, '20.00', 'scryfall', ?, ?)
            """,
            (bob_id, app.now_iso(), app.now_iso()),
        )
        form = {
            "recipient_id": [str(bob_id)],
            f"offer_{alice_card_id}": ["1"],
            f"request_{bob_card_id}": ["1"],
        }
        offered = app.parse_trade_quantities(form, "offer", alice_id)
        requested = app.parse_trade_quantities(form, "request", bob_id)

        review_html = app.render_trade_review(alice, bob_id, form, offered, requested)

        self.assertIn("Trade fairness warning", review_html)
        self.assertIn("name=\"fairness_ack\"", review_html)
        with self.assertRaisesRegex(ValueError, "Acknowledge"):
            app.validate_trade_fairness_for_send(offered, requested, acknowledged=False)
        app.validate_trade_fairness_for_send(offered, requested, acknowledged=True)

    def test_trade_fairness_block_prevents_creation(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        app.set_trade_fairness_settings("20", "40")
        alice_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 2, 1, '10.00', 'scryfall', ?, ?)
            """,
            (alice_id, app.now_iso(), app.now_iso()),
        )
        bob_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Rhystic Study', 'Wilds of Eldraine', 2, 1, '20.00', 'scryfall', ?, ?)
            """,
            (bob_id, app.now_iso(), app.now_iso()),
        )
        alice_card = app.row("SELECT * FROM collection_items WHERE id = ?", (alice_card_id,))
        bob_card = app.row("SELECT * FROM collection_items WHERE id = ?", (bob_card_id,))

        with self.assertRaisesRegex(ValueError, "block threshold"):
            app.create_trade_offer(alice_id, bob_id, "Too far apart", [(alice_card, 1)], [(bob_card, 1)])

    def test_trade_fairness_warning_requires_acknowledgement_before_accepting(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        alice_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 2, 1, '10.00', 'scryfall', ?, ?)
            """,
            (alice_id, app.now_iso(), app.now_iso()),
        )
        bob_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Rhystic Study', 'Wilds of Eldraine', 2, 1, '20.00', 'scryfall', ?, ?)
            """,
            (bob_id, app.now_iso(), app.now_iso()),
        )
        alice_card = app.row("SELECT * FROM collection_items WHERE id = ?", (alice_card_id,))
        bob_card = app.row("SELECT * FROM collection_items WHERE id = ?", (bob_card_id,))
        trade_id = app.create_trade_offer(alice_id, bob_id, "Warning trade", [(alice_card, 1)], [(bob_card, 1)])
        bob = app.row("SELECT * FROM users WHERE id = ?", (bob_id,))

        detail_html = app.render_trade_detail(bob, trade_id)

        self.assertIn("Trade fairness warning", detail_html)
        with self.assertRaisesRegex(ValueError, "Acknowledge"):
            app.update_trade_response(trade_id, bob_id, "accepted")
        app.update_trade_response(trade_id, bob_id, "accepted", fairness_acknowledged=True)
        trade = app.row("SELECT * FROM trades WHERE id = ?", (trade_id,))
        self.assertEqual(trade["status"], "accepted")

    def test_trade_items_snapshot_price_source_for_detail_balance(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        alice_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 2, 1, '4.00', 'scryfall', ?, ?)
            """,
            (alice_id, app.now_iso(), app.now_iso()),
        )
        bob_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Lightning Bolt', 'Secret Lair', 2, 1, '9.50', 'tcgplayer', ?, ?)
            """,
            (bob_id, app.now_iso(), app.now_iso()),
        )
        alice_card = app.row("SELECT * FROM collection_items WHERE id = ?", (alice_card_id,))
        bob_card = app.row("SELECT * FROM collection_items WHERE id = ?", (bob_card_id,))
        trade_id = app.create_trade_offer(alice_id, bob_id, "Value check", [(alice_card, 1)], [(bob_card, 1)])
        trade_item = app.row("SELECT * FROM trade_items WHERE trade_id = ? AND side = 'requested'", (trade_id,))
        bob = app.row("SELECT * FROM users WHERE id = ?", (bob_id,))

        html = app.render_trade_detail(bob, trade_id)

        self.assertEqual(trade_item["price_usd"], "9.50")
        self.assertEqual(trade_item["price_source"], "scryfall")
        self.assertIn("Request side is $5.50 higher", html)
        self.assertIn("Scryfall", html)

    def test_trade_price_basis_is_scryfall_and_is_locked_to_trade(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        alice = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))
        alice_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 2, 1, '5.00', 'scryfall', ?, ?)
            """,
            (alice_id, app.now_iso(), app.now_iso()),
        )
        bob_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Counterspell', 'Dominaria Remastered', 2, 1, '8.00', 'scryfall', ?, ?)
            """,
            (bob_id, app.now_iso(), app.now_iso()),
        )
        form = {
            "recipient_id": [str(bob_id)],
            "price_source_preference": ["cardmarket"],
            f"offer_{alice_card_id}": ["1"],
            f"request_{bob_card_id}": ["1"],
        }
        price_basis = app.trade_price_basis_for(alice, form)
        offered = app.parse_trade_quantities(form, "offer", alice_id, price_basis)
        requested = app.parse_trade_quantities(form, "request", bob_id, price_basis)

        review_html = app.render_trade_review(alice, bob_id, form, offered, requested)
        trade_id = app.create_trade_offer(alice_id, bob_id, "Provider basis", offered, requested, price_source_preference=price_basis)
        app.execute("UPDATE collection_items SET price_usd = '99.00', price_source = 'manual' WHERE id IN (?, ?)", (alice_card_id, bob_card_id))
        detail_html = app.render_trade_detail(alice, trade_id)
        trade = app.row("SELECT * FROM trades WHERE id = ?", (trade_id,))
        requested_item = app.row("SELECT * FROM trade_items WHERE trade_id = ? AND side = 'requested'", (trade_id,))

        self.assertEqual(price_basis, "scryfall")
        self.assertIn("Price basis: Scryfall", review_html)
        self.assertEqual(trade["price_source_preference"], "scryfall")
        self.assertEqual(requested_item["price_usd"], "8.00")
        self.assertEqual(requested_item["price_source"], "scryfall")
        self.assertIn("Request side is $3.00 higher", detail_html)
        self.assertIn("Price basis: Scryfall", detail_html)

    def test_trade_picker_default_search_only_matches_card_name_or_type(self):
        owner_id = app.create_user("owner", "password123", "Owner")
        samples = [
            ("Forest", "Dragon Shield", "Basic Land", 1),
            ("Shivan Dragon", "Core Set", "Creature - Dragon", 1),
            ("Dragon Fodder", "Core Set", "Sorcery", 0),
        ]
        for name, set_name, type_line, trade_qty in samples:
            app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, set_name, type_line, quantity, quantity_for_trade, created_at, updated_at)
                VALUES (?, 'mtg', ?, ?, ?, 2, ?, ?, ?)
                """,
                (owner_id, name, set_name, type_line, trade_qty, app.now_iso(), app.now_iso()),
            )

        filters = app.trade_picker_filter_values({"offer_q": ["dragon"]}, "offer")
        where, params = app.trade_picker_where(owner_id, filters)
        default_matches = app.rows(f"SELECT card_name FROM collection_items WHERE {' AND '.join(where)} ORDER BY card_name", params)
        set_filters = app.trade_picker_filter_values({"offer_set_name": ["dragon"]}, "offer")
        set_where, set_params = app.trade_picker_where(owner_id, set_filters)
        set_matches = app.rows(f"SELECT card_name FROM collection_items WHERE {' AND '.join(set_where)}", set_params)

        self.assertEqual([item["card_name"] for item in default_matches], ["Shivan Dragon"])
        self.assertEqual([item["card_name"] for item in set_matches], ["Forest"])

    def test_trade_picker_renders_prefix_sort_controls_and_sorts_cards(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        alice = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))
        for name, trade_qty in [("Low Offer", 1), ("High Offer", 5)]:
            app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, quantity, quantity_for_trade, created_at, updated_at)
                VALUES (?, 'mtg', ?, 5, ?, ?, ?)
                """,
                (alice_id, name, trade_qty, app.now_iso(), app.now_iso()),
            )
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Request Card', 5, 1, ?, ?)
            """,
            (bob_id, app.now_iso(), app.now_iso()),
        )

        html = app.render_new_trade(
            alice,
            bob_id,
            {"recipient_id": [str(bob_id)], "offer_sort": ["trade"], "offer_dir": ["desc"]},
        )

        self.assertIn('name="offer_sort"', html)
        self.assertIn('name="offer_dir"', html)
        self.assertIn('name="request_sort"', html)
        self.assertLess(html.index("High Offer"), html.index("Low Offer"))

    def test_one_directional_trade_requires_trusted_user(self):
        app.create_user("admin", "password123", "Admin")
        user_id = app.create_user("alice", "password123", "Alice")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))

        with self.assertRaisesRegex(ValueError, "trusted users"):
            app.validate_trade_sides(user, [("card", 1)], [])

        app.admin_set_user_trust(user_id, "trust")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))

        app.validate_trade_sides(user, [("card", 1)], [])

    def test_one_way_trade_policy_controls_validation(self):
        admin_id = app.create_user("admin", "password123", "Admin")
        user_id = app.create_user("alice", "password123", "Alice")
        admin = app.row("SELECT * FROM users WHERE id = ?", (admin_id,))
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))

        with self.assertRaisesRegex(ValueError, "trusted users"):
            app.validate_trade_sides(user, [("card", 1)], [])

        app.set_trade_policy_settings("anyone", "5", "20", "0", "7", "0")
        app.validate_trade_sides(user, [("card", 1)], [])

        app.set_trade_policy_settings("admins", "5", "20", "0", "7", "0")
        with self.assertRaisesRegex(ValueError, "admins"):
            app.validate_trade_sides(user, [("card", 1)], [])
        app.validate_trade_sides(admin, [("card", 1)], [])

        app.set_trade_policy_settings("disabled", "5", "20", "0", "7", "0")
        with self.assertRaisesRegex(ValueError, "disabled"):
            app.validate_trade_sides(admin, [("card", 1)], [])

    def test_trade_detail_warns_recipient_about_one_directional_offer(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        alice_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 1, 1, ?, ?)
            """,
            (alice_id, app.now_iso(), app.now_iso()),
        )
        trade_id = app.execute(
            """
            INSERT INTO trades (proposer_id, recipient_id, status, created_at, updated_at)
            VALUES (?, ?, 'pending', ?, ?)
            """,
            (alice_id, bob_id, app.now_iso(), app.now_iso()),
        )
        alice_card = app.row("SELECT * FROM collection_items WHERE id = ?", (alice_card_id,))
        app.add_trade_item(trade_id, alice_id, alice_card, 1, "offered")
        bob = app.row("SELECT * FROM users WHERE id = ?", (bob_id,))

        html = app.render_trade_detail(bob, trade_id)

        self.assertIn("One-directional trade", html)
        self.assertIn("offering cards without requesting", html)

    def test_trade_comments_are_visible_to_trade_participants(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        outsider_id = app.create_user("outsider", "password123", "Outsider")
        trade_id = app.execute(
            """
            INSERT INTO trades (proposer_id, recipient_id, status, created_at, updated_at)
            VALUES (?, ?, 'pending', ?, ?)
            """,
            (alice_id, bob_id, app.now_iso(), app.now_iso()),
        )

        app.add_trade_comment(trade_id, bob_id, "Can you ship in a top loader?\nThanks <3")
        alice = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))
        html = app.render_trade_detail(alice, trade_id)

        self.assertIn("Comments", html)
        self.assertIn("Can you ship in a top loader?", html)
        self.assertIn("Thanks &lt;3", html)
        self.assertIn("Post comment", html)
        with self.assertRaisesRegex(ValueError, "empty"):
            app.add_trade_comment(trade_id, alice_id, "   ")
        with self.assertRaisesRegex(ValueError, "Trade not found"):
            app.add_trade_comment(trade_id, outsider_id, "I should not be here")

    def test_trade_issue_report_notifies_admins_and_renders_on_trade(self):
        admin_id = app.create_user("admin", "password123", "Admin")
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        trade_id = app.execute(
            """
            INSERT INTO trades (proposer_id, recipient_id, status, created_at, updated_at)
            VALUES (?, ?, 'completed', ?, ?)
            """,
            (alice_id, bob_id, app.now_iso(), app.now_iso()),
        )

        dispute_id = app.create_trade_dispute(trade_id, alice_id, "condition", "Card arrived bent <bad>")

        dispute = app.row("SELECT * FROM trade_disputes WHERE id = ?", (dispute_id,))
        admin_notification = app.row("SELECT * FROM user_notifications WHERE user_id = ? AND kind = 'trade_dispute'", (admin_id,))
        alice = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))
        admin = app.row("SELECT * FROM users WHERE id = ?", (admin_id,))
        trade_html = app.render_trade_detail(alice, trade_id)
        admin_html = app.render_admin(admin)

        self.assertEqual(dispute["status"], "open")
        self.assertEqual(dispute["category"], "condition")
        self.assertEqual(dispute["body"], "Card arrived bent <bad>")
        self.assertIn("Trade #", admin_notification["title"])
        self.assertIn("/admin/disputes", admin_notification["url"])
        self.assertIn("Trade issues", trade_html)
        self.assertIn("Card arrived bent &lt;bad&gt;", trade_html)
        self.assertIn(f'action="/trades/{trade_id}/disputes"', trade_html)
        self.assertIn("Trade issue queue", admin_html)
        self.assertIn("1 open", admin_html)

    def test_trade_issue_evidence_attachments_are_validated_and_download_authorized(self):
        admin_id = app.create_user("admin", "password123", "Admin")
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        outsider_id = app.create_user("outsider", "password123", "Outsider")
        trade_id = app.execute(
            """
            INSERT INTO trades (proposer_id, recipient_id, status, created_at, updated_at)
            VALUES (?, ?, 'completed', ?, ?)
            """,
            (alice_id, bob_id, app.now_iso(), app.now_iso()),
        )

        dispute_id = app.create_trade_dispute(
            trade_id,
            alice_id,
            "condition",
            "Card arrived bent.",
            {"filename": "corner.png", "content": b"\x89PNG\r\n\x1a\nexample", "content_type": "image/png"},
            "Photo of the damaged corner.",
        )
        evidence = app.row("SELECT * FROM trade_dispute_evidence WHERE dispute_id = ?", (dispute_id,))
        participant_download = app.trade_dispute_evidence_for_user(evidence["id"], bob_id, False)
        admin_download = app.trade_dispute_evidence_for_user(evidence["id"], admin_id, True)
        outsider_download = app.trade_dispute_evidence_for_user(evidence["id"], outsider_id, False)
        alice = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))
        admin = app.row("SELECT * FROM users WHERE id = ?", (admin_id,))
        trade_html = app.render_trade_detail(alice, trade_id)
        admin_html = app.render_admin_trade_disputes(admin, {"status": ["open"], "q": [str(trade_id)]})

        self.assertEqual(evidence["original_filename"], "corner.png")
        self.assertEqual(evidence["content_type"], "image/png")
        self.assertEqual(evidence["note"], "Photo of the damaged corner.")
        self.assertEqual(participant_download["content"], evidence["content"])
        self.assertEqual(admin_download["content"], evidence["content"])
        self.assertIsNone(outsider_download)
        self.assertIn("corner.png", trade_html)
        self.assertIn("Photo of the damaged corner.", trade_html)
        self.assertIn("Attach evidence", trade_html)
        self.assertIn("corner.png", admin_html)

        second_id = app.add_trade_dispute_evidence(
            dispute_id,
            bob_id,
            {"filename": "chat.txt", "content": b"Seller confirmed replacement.", "content_type": "text/plain"},
            "Chat excerpt.",
            trade_id=trade_id,
        )
        self.assertTrue(second_id)
        self.assertEqual(app.row("SELECT COUNT(*) AS count FROM trade_dispute_evidence WHERE dispute_id = ?", (dispute_id,))["count"], 2)
        with self.assertRaisesRegex(ValueError, "PNG, JPG"):
            app.add_trade_dispute_evidence(
                dispute_id,
                alice_id,
                {"filename": "unsafe.svg", "content": b"<svg></svg>", "content_type": "image/svg+xml"},
                "",
                trade_id=trade_id,
            )

    def test_dispute_escalation_and_evidence_retention_policy(self):
        admin_id = app.create_user("admin", "password123", "Admin")
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        trade_id = app.execute(
            """
            INSERT INTO trades (proposer_id, recipient_id, status, created_at, updated_at)
            VALUES (?, ?, 'completed', ?, ?)
            """,
            (alice_id, bob_id, app.now_iso(), app.now_iso()),
        )
        dispute_id = app.create_trade_dispute(
            trade_id,
            alice_id,
            "condition",
            "Card arrived bent.",
            {"filename": "corner.png", "content": b"\x89PNG\r\n\x1a\nexample", "content_type": "image/png"},
            "Photo of the damaged corner.",
        )
        old_timestamp = (datetime.now(timezone.utc) - timedelta(days=4)).replace(microsecond=0).isoformat()
        app.set_trade_policy_settings("trusted", "5", "20", "0", "2", "1")
        app.execute(
            "UPDATE trade_disputes SET created_at = ?, updated_at = ? WHERE id = ?",
            (old_timestamp, old_timestamp, dispute_id),
        )
        admin = app.row("SELECT * FROM users WHERE id = ?", (admin_id,))

        queue_html = app.render_admin_trade_disputes(admin, {"status": ["open"], "q": [str(trade_id)]})
        open_prune = app.prune_trade_dispute_evidence(1)

        self.assertIn("Needs attention", queue_html)
        self.assertIn("policy escalates after 2", queue_html)
        self.assertEqual(open_prune["deleted"], 0)
        self.assertEqual(app.row("SELECT COUNT(*) AS count FROM trade_dispute_evidence WHERE dispute_id = ?", (dispute_id,))["count"], 1)

        app.update_trade_dispute_admin(dispute_id, admin_id, "resolved", "Resolved.", "127.0.0.1", "test-agent")
        app.execute(
            "UPDATE trade_disputes SET resolved_at = ?, updated_at = ? WHERE id = ?",
            (old_timestamp, old_timestamp, dispute_id),
        )

        keep_forever = app.prune_trade_dispute_evidence(0)
        pruned = app.prune_trade_dispute_evidence(1)

        self.assertEqual(keep_forever["deleted"], 0)
        self.assertEqual(pruned["deleted"], 1)
        self.assertEqual(app.row("SELECT COUNT(*) AS count FROM trade_dispute_evidence WHERE dispute_id = ?", (dispute_id,))["count"], 0)

    def test_admin_trade_issue_queue_updates_status_and_logs_action(self):
        admin_id = app.create_user("admin", "password123", "Admin")
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        trade_id = app.execute(
            """
            INSERT INTO trades (proposer_id, recipient_id, status, created_at, updated_at)
            VALUES (?, ?, 'completed', ?, ?)
            """,
            (alice_id, bob_id, app.now_iso(), app.now_iso()),
        )
        dispute_id = app.create_trade_dispute(trade_id, bob_id, "shipping", "Tracking never updated.")
        admin = app.row("SELECT * FROM users WHERE id = ?", (admin_id,))

        app.update_trade_dispute_admin(dispute_id, admin_id, "resolved", "Both users confirmed delivery.", "127.0.0.1", "test-agent")

        dispute = app.row("SELECT * FROM trade_disputes WHERE id = ?", (dispute_id,))
        notifications = app.rows("SELECT * FROM user_notifications WHERE kind = 'trade_dispute' ORDER BY user_id, id")
        audit = app.row("SELECT * FROM admin_audit_log WHERE action = 'trade_dispute_updated'")
        queue_html = app.render_admin_trade_disputes(admin, {"status": ["resolved"], "q": [str(trade_id)]})
        trade_html = app.render_trade_detail(app.row("SELECT * FROM users WHERE id = ?", (bob_id,)), trade_id)

        self.assertEqual(dispute["status"], "resolved")
        self.assertEqual(dispute["admin_note"], "Both users confirmed delivery.")
        self.assertEqual(dispute["resolved_by_user_id"], admin_id)
        self.assertTrue(dispute["resolved_at"])
        self.assertIn("trade_dispute_updated", audit["action"])
        self.assertIn(f"Trade #{trade_id} issue #{dispute_id}", audit["target_label"])
        self.assertIn("Both users confirmed delivery.", trade_html)
        self.assertIn("Resolved", queue_html)
        self.assertIn("Tracking never updated.", queue_html)
        self.assertEqual([item["user_id"] for item in notifications], [admin_id, alice_id, bob_id])

    def test_admin_trade_issue_resolution_notes_and_repeat_trends_render(self):
        admin_id = app.create_user("admin", "password123", "Admin")
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        trade_one = app.execute(
            """
            INSERT INTO trades (proposer_id, recipient_id, status, created_at, updated_at)
            VALUES (?, ?, 'completed', ?, ?)
            """,
            (alice_id, bob_id, app.now_iso(), app.now_iso()),
        )
        trade_two = app.execute(
            """
            INSERT INTO trades (proposer_id, recipient_id, status, created_at, updated_at)
            VALUES (?, ?, 'completed', ?, ?)
            """,
            (alice_id, bob_id, app.now_iso(), app.now_iso()),
        )
        dispute_one = app.create_trade_dispute(trade_one, alice_id, "shipping", "Package arrived late.")
        app.create_trade_dispute(trade_two, alice_id, "condition", "Second trade had condition issues.")
        admin = app.row("SELECT * FROM users WHERE id = ?", (admin_id,))

        app.update_trade_dispute_admin(
            dispute_one,
            admin_id,
            "resolved",
            "Replacement agreed.",
            "127.0.0.1",
            "test-agent",
            resolution_note="Evidence reviewed; warned Bob about repeat shipping issues.",
        )

        dispute = app.row("SELECT * FROM trade_disputes WHERE id = ?", (dispute_one,))
        queue_html = app.render_admin_trade_disputes(admin, {"status": [""], "q": [""]})

        self.assertEqual(dispute["resolution_note"], "Evidence reviewed; warned Bob about repeat shipping issues.")
        self.assertIn("Repeat issue trends", queue_html)
        self.assertIn("Bob (@bob)", queue_html)
        self.assertIn("2 reports in 90 days", queue_html)
        self.assertIn("Issue type trends", queue_html)
        self.assertIn("Evidence reviewed; warned Bob", queue_html)

    def test_trade_events_create_notifications(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        alice_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 2, 1, ?, ?)
            """,
            (alice_id, app.now_iso(), app.now_iso()),
        )
        bob_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Counterspell', 'Dominaria Remastered', 2, 1, ?, ?)
            """,
            (bob_id, app.now_iso(), app.now_iso()),
        )
        alice_card = app.row("SELECT * FROM collection_items WHERE id = ?", (alice_card_id,))
        bob_card = app.row("SELECT * FROM collection_items WHERE id = ?", (bob_card_id,))

        trade_id = app.create_trade_offer(alice_id, bob_id, "Offer", [(alice_card, 1)], [(bob_card, 1)])
        app.add_trade_comment(trade_id, bob_id, "Can you ship this week?")
        app.update_trade_response(trade_id, bob_id, "accepted", "Works for me")

        bob_notifications = app.rows("SELECT * FROM user_notifications WHERE user_id = ? ORDER BY id", (bob_id,))
        alice_notifications = app.rows("SELECT * FROM user_notifications WHERE user_id = ? ORDER BY id", (alice_id,))
        alice = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))
        inbox_html = app.render_notifications(alice)
        layout_html = app.render_layout(alice, "Activity", "<p>Body</p>")

        self.assertEqual([item["kind"] for item in bob_notifications], ["trade_offer"])
        self.assertEqual([item["kind"] for item in alice_notifications], ["trade_comment", "trade_status"])
        self.assertIn("New comment on Trade", inbox_html)
        self.assertIn("Trade #", inbox_html)
        self.assertIn("/delete", inbox_html)
        self.assertNotIn("Refresh prices", inbox_html)
        self.assertIn('href="/"', layout_html)
        self.assertIn(">Dashboard", layout_html)
        self.assertIn("nav-badge", layout_html)

    def test_trade_email_notifications_respect_user_preferences(self):
        alice_id = app.create_user("alice", "password123", "Alice", email="alice@example.test")
        bob_id = app.create_user("bob", "password123", "Bob", email="bob@example.test")
        app.execute(
            """
            UPDATE users
            SET email_trade_notifications_enabled = 1,
                email_trade_offer_enabled = 1,
                email_trade_comment_enabled = 0,
                email_trade_counter_enabled = 1,
                email_trade_status_enabled = 1
            WHERE id = ?
            """,
            (bob_id,),
        )
        app.execute(
            """
            UPDATE users
            SET email_trade_notifications_enabled = 1,
                email_trade_offer_enabled = 1,
                email_trade_comment_enabled = 0,
                email_trade_counter_enabled = 1,
                email_trade_status_enabled = 1
            WHERE id = ?
            """,
            (alice_id,),
        )
        alice_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 2, 1, ?, ?)
            """,
            (alice_id, app.now_iso(), app.now_iso()),
        )
        bob_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Counterspell', 'Dominaria Remastered', 2, 1, ?, ?)
            """,
            (bob_id, app.now_iso(), app.now_iso()),
        )
        alice_card = app.row("SELECT * FROM collection_items WHERE id = ?", (alice_card_id,))
        bob_card = app.row("SELECT * FROM collection_items WHERE id = ?", (bob_card_id,))
        sent = []
        original_sender = app.send_email_message
        original_configured = app.email_delivery_configured

        def fake_sender(to_email, subject, body):
            sent.append((to_email, subject, body))
            return True, "Email sent."

        app.send_email_message = fake_sender
        app.email_delivery_configured = lambda: True
        try:
            trade_id = app.create_trade_offer(alice_id, bob_id, "Offer", [(alice_card, 1)], [(bob_card, 1)])
            app.add_trade_comment(trade_id, bob_id, "Can you ship this week?")
        finally:
            app.send_email_message = original_sender
            app.email_delivery_configured = original_configured

        bob_offer = app.row("SELECT * FROM user_notifications WHERE user_id = ? AND kind = 'trade_offer'", (bob_id,))
        alice_comment = app.row("SELECT * FROM user_notifications WHERE user_id = ? AND kind = 'trade_comment'", (alice_id,))

        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], "bob@example.test")
        self.assertIn("New trade offer", sent[0][1])
        self.assertIn(f"/trades/{trade_id}", sent[0][2])
        self.assertEqual(bob_offer["email_status"], "sent")
        self.assertEqual(alice_comment["email_status"], "")

    def test_trade_email_notifications_fail_without_breaking_trade(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob", email="bob@example.test")
        app.execute("UPDATE users SET email_trade_notifications_enabled = 1 WHERE id = ?", (bob_id,))
        alice_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 2, 1, ?, ?)
            """,
            (alice_id, app.now_iso(), app.now_iso()),
        )
        bob_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Counterspell', 'Dominaria Remastered', 2, 1, ?, ?)
            """,
            (bob_id, app.now_iso(), app.now_iso()),
        )
        alice_card = app.row("SELECT * FROM collection_items WHERE id = ?", (alice_card_id,))
        bob_card = app.row("SELECT * FROM collection_items WHERE id = ?", (bob_card_id,))

        original_sender = app.send_email_message
        original_configured = app.email_delivery_configured

        def failing_sender(to_email, subject, body):
            return False, "SMTP is not configured."

        app.send_email_message = failing_sender
        app.email_delivery_configured = lambda: True
        try:
            trade_id = app.create_trade_offer(alice_id, bob_id, "Offer", [(alice_card, 1)], [(bob_card, 1)])
        finally:
            app.send_email_message = original_sender
            app.email_delivery_configured = original_configured
        notification = app.row("SELECT * FROM user_notifications WHERE user_id = ? AND kind = 'trade_offer'", (bob_id,))

        self.assertIsNotNone(app.row("SELECT * FROM trades WHERE id = ?", (trade_id,)))
        self.assertEqual(notification["email_status"], "failed")
        self.assertIn("SMTP", notification["email_error"])

    def test_trade_email_notifications_are_not_queued_without_smtp(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob", email="bob@example.test")
        app.execute("UPDATE users SET email_trade_notifications_enabled = 1 WHERE id = ?", (bob_id,))
        alice_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 2, 1, ?, ?)
            """,
            (alice_id, app.now_iso(), app.now_iso()),
        )
        bob_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Counterspell', 'Dominaria Remastered', 2, 1, ?, ?)
            """,
            (bob_id, app.now_iso(), app.now_iso()),
        )
        alice_card = app.row("SELECT * FROM collection_items WHERE id = ?", (alice_card_id,))
        bob_card = app.row("SELECT * FROM collection_items WHERE id = ?", (bob_card_id,))
        original_configured = app.email_delivery_configured
        app.email_delivery_configured = lambda: False
        try:
            app.create_trade_offer(alice_id, bob_id, "Offer", [(alice_card, 1)], [(bob_card, 1)])
        finally:
            app.email_delivery_configured = original_configured
        notification = app.row("SELECT * FROM user_notifications WHERE user_id = ? AND kind = 'trade_offer'", (bob_id,))

        self.assertEqual(notification["email_status"], "")
        self.assertEqual(notification["email_error"], "")

    def test_notification_preferences_v2_control_in_app_categories(self):
        user_id = app.create_user("notifyoff", "password123", "Notify Off")
        app.execute(
            """
            UPDATE users
            SET notify_trade_offer_enabled = 0,
                notify_import_complete_enabled = 0,
                notify_admin_notice_enabled = 0
            WHERE id = ?
            """,
            (user_id,),
        )

        suppressed_offer = app.create_notification(user_id, "trade_offer", "Offer")
        kept_comment = app.create_notification(user_id, "trade_comment", "Comment")
        suppressed_import = app.create_notification(user_id, "scryfall_import", "Import")
        suppressed_admin = app.create_notification(user_id, "backup_status", "Backup failed")
        notifications = app.rows("SELECT * FROM user_notifications WHERE user_id = ? ORDER BY id", (user_id,))

        self.assertEqual(suppressed_offer, 0)
        self.assertGreater(kept_comment, 0)
        self.assertEqual(suppressed_import, 0)
        self.assertEqual(suppressed_admin, 0)
        self.assertEqual([item["kind"] for item in notifications], ["trade_comment"])

    def test_notification_preferences_v2_email_categories_include_price_import_and_admin(self):
        user_id = app.create_user("emailcats", "password123", "Email Cats", email="emailcats@example.test")
        app.execute(
            """
            UPDATE users
            SET email_trade_notifications_enabled = 1,
                email_price_alert_enabled = 1,
                email_import_complete_enabled = 0,
                email_admin_notice_enabled = 1
            WHERE id = ?
            """,
            (user_id,),
        )
        sent = []
        original_sender = app.send_email_message
        original_configured = app.email_delivery_configured

        def fake_sender(to_email, subject, body):
            sent.append((to_email, subject, body))
            return True, "Email sent."

        app.send_email_message = fake_sender
        app.email_delivery_configured = lambda: True
        try:
            app.create_notification(user_id, "price_alert", "Price moved", "Sol Ring changed.")
            app.create_notification(user_id, "scryfall_import", "Import complete", "Cards enriched.")
            app.create_notification(user_id, "backup_status", "Backup failed", "Disk full.")
        finally:
            app.send_email_message = original_sender
            app.email_delivery_configured = original_configured

        price = app.row("SELECT * FROM user_notifications WHERE user_id = ? AND kind = 'price_alert'", (user_id,))
        import_notice = app.row("SELECT * FROM user_notifications WHERE user_id = ? AND kind = 'scryfall_import'", (user_id,))
        admin_notice = app.row("SELECT * FROM user_notifications WHERE user_id = ? AND kind = 'backup_status'", (user_id,))

        self.assertEqual(len(sent), 2)
        self.assertEqual(price["email_status"], "sent")
        self.assertEqual(import_notice["email_status"], "")
        self.assertEqual(admin_notice["email_status"], "sent")

    def test_notifications_can_be_deleted_by_owner(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        alice_first = app.create_notification(alice_id, "trade_status", "First")
        alice_second = app.create_notification(alice_id, "trade_status", "Second")
        bob_notification = app.create_notification(bob_id, "trade_status", "Bob")

        app.mark_notification_read(alice_id, alice_first)
        self.assertEqual(app.delete_notification(alice_id, bob_notification), 0)
        self.assertEqual(app.delete_read_notifications(alice_id), 1)

        alice_notifications = app.rows("SELECT * FROM user_notifications WHERE user_id = ? ORDER BY id", (alice_id,))
        bob_notifications = app.rows("SELECT * FROM user_notifications WHERE user_id = ?", (bob_id,))

        self.assertEqual([item["id"] for item in alice_notifications], [alice_second])
        self.assertEqual(len(bob_notifications), 1)
        self.assertEqual(app.delete_all_notifications(alice_id), 1)
        self.assertIsNone(app.row("SELECT * FROM user_notifications WHERE user_id = ?", (alice_id,)))

    def test_completed_trade_feedback_can_be_submitted_and_updated(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        trade_id = app.execute(
            """
            INSERT INTO trades (proposer_id, recipient_id, status, created_at, updated_at)
            VALUES (?, ?, 'completed', ?, ?)
            """,
            (alice_id, bob_id, app.now_iso(), app.now_iso()),
        )

        feedback_id = app.submit_trade_feedback(trade_id, alice_id, "5", "Great packing and communication.")
        updated_id = app.submit_trade_feedback(trade_id, alice_id, "4", "Still a good trade.")

        feedback = app.row("SELECT * FROM trade_feedback WHERE id = ?", (feedback_id,))
        notifications = app.rows("SELECT * FROM user_notifications WHERE user_id = ?", (bob_id,))
        summary = app.reputation_summary(bob_id)

        self.assertEqual(feedback_id, updated_id)
        self.assertEqual(feedback["reviewee_id"], bob_id)
        self.assertEqual(feedback["rating"], 4)
        self.assertEqual(feedback["body"], "Still a good trade.")
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0]["kind"], "trade_feedback")
        self.assertEqual(summary["feedback_count"], 1)
        self.assertEqual(summary["average_rating"], 4.0)

    def test_trade_feedback_requires_completed_trade_and_participant(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        outsider_id = app.create_user("outsider", "password123", "Outsider")
        pending_trade_id = app.execute(
            """
            INSERT INTO trades (proposer_id, recipient_id, status, created_at, updated_at)
            VALUES (?, ?, 'pending', ?, ?)
            """,
            (alice_id, bob_id, app.now_iso(), app.now_iso()),
        )
        completed_trade_id = app.execute(
            """
            INSERT INTO trades (proposer_id, recipient_id, status, created_at, updated_at)
            VALUES (?, ?, 'completed', ?, ?)
            """,
            (alice_id, bob_id, app.now_iso(), app.now_iso()),
        )

        with self.assertRaisesRegex(ValueError, "completed trades"):
            app.submit_trade_feedback(pending_trade_id, alice_id, "5", "Too soon")
        with self.assertRaisesRegex(ValueError, "completed trades"):
            app.submit_trade_feedback(completed_trade_id, outsider_id, "5", "Not my trade")
        with self.assertRaisesRegex(ValueError, "1 to 5"):
            app.submit_trade_feedback(completed_trade_id, alice_id, "6", "Nope")

    def test_trade_detail_renders_feedback_form_after_completion(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        trade_id = app.execute(
            """
            INSERT INTO trades (proposer_id, recipient_id, status, created_at, updated_at)
            VALUES (?, ?, 'completed', ?, ?)
            """,
            (alice_id, bob_id, app.now_iso(), app.now_iso()),
        )
        alice = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))

        before_html = app.render_trade_detail(alice, trade_id)
        app.submit_trade_feedback(trade_id, alice_id, "5", "Fast shipping.")
        after_html = app.render_trade_detail(alice, trade_id)

        self.assertIn("Trade feedback", before_html)
        self.assertIn("Leave feedback for Bob", before_html)
        self.assertIn(f'action="/trades/{trade_id}/feedback"', before_html)
        self.assertIn("Update your feedback", after_html)
        self.assertIn("Fast shipping.", after_html)
        self.assertIn("Alice rated Bob 5/5", after_html)

    def test_member_detail_shows_reputation_summary(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        trade_id = app.execute(
            """
            INSERT INTO trades (proposer_id, recipient_id, status, created_at, updated_at)
            VALUES (?, ?, 'completed', ?, ?)
            """,
            (alice_id, bob_id, app.now_iso(), app.now_iso()),
        )
        app.submit_trade_feedback(trade_id, alice_id, "5", "Friendly and quick.")
        alice = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))

        html = app.render_member_detail(alice, bob_id)

        self.assertIn("Reputation", html)
        self.assertIn("5.0/5", html)
        self.assertIn("1 feedback", html)
        self.assertIn("<strong>1</strong>", html)
        self.assertIn("completed trades", html)
        self.assertIn("Friendly and quick.", html)

    def test_public_member_profile_shows_public_trade_wants_groups_and_actions(self):
        viewer_id = app.create_user("profileviewer", "password123", "Profile Viewer")
        member_id = app.create_user("profileowner", "password123", "Profile Owner")
        viewer = app.row("SELECT * FROM users WHERE id = ?", (viewer_id,))
        app.execute(
            "UPDATE users SET bio = ?, public_email = 1, email = ? WHERE id = ?",
            ("Trade notes go here.", "owner@example.test", member_id),
        )
        public_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, price_usd, condition, finish, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Public Trade Card', 'Commander Masters', 3, 2, '4.50', 'Near Mint', 'Foil', 1, ?, ?)
            """,
            (member_id, app.now_iso(), app.now_iso()),
        )
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, quantity, quantity_for_trade, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Private Trade Card', 1, 1, 0, ?, ?)
            """,
            (member_id, app.now_iso(), app.now_iso()),
        )
        app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, desired_quantity, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Public Wanted Card', 1, 1, ?, ?)
            """,
            (member_id, app.now_iso(), app.now_iso()),
        )
        app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, desired_quantity, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Private Wanted Card', 1, 0, ?, ?)
            """,
            (member_id, app.now_iso(), app.now_iso()),
        )
        public_group_id = app.create_card_group(member_id, "binder", "Public Profile Binder", "Public cards.", True)
        app.create_card_group(member_id, "deck", "Private Profile Deck", "", False)
        app.add_collection_item_to_group(member_id, public_group_id, public_card_id, 1)

        stats = app.public_profile_stats(member_id)
        html = app.render_member_detail(viewer, member_id)

        self.assertEqual(stats["available_trade_quantity"], 2)
        self.assertEqual(stats["unique_trade_cards"], 1)
        self.assertIn("Public profile", html)
        self.assertIn("Trade notes go here.", html)
        self.assertIn("owner@example.test", html)
        self.assertIn("Public Trade Card", html)
        self.assertNotIn("Private Trade Card", html)
        self.assertIn("Public Wanted Card", html)
        self.assertNotIn("Private Wanted Card", html)
        self.assertIn("Public Profile Binder", html)
        self.assertNotIn("Private Profile Deck", html)
        self.assertIn(f'name="request_{public_card_id}"', html)
        self.assertIn(f'value="{member_id}"', html)
        self.assertIn("Propose trade", html)

    def test_counter_offer_preloads_cards_and_links_trades(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        alice_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 2, 1, ?, ?)
            """,
            (alice_id, app.now_iso(), app.now_iso()),
        )
        extra_alice_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Rhystic Study', 'Wilds of Eldraine', 2, 1, ?, ?)
            """,
            (alice_id, app.now_iso(), app.now_iso()),
        )
        bob_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Counterspell', 'Dominaria Remastered', 2, 1, ?, ?)
            """,
            (bob_id, app.now_iso(), app.now_iso()),
        )
        trade_id = app.execute(
            """
            INSERT INTO trades (proposer_id, recipient_id, status, created_at, updated_at)
            VALUES (?, ?, 'pending', ?, ?)
            """,
            (alice_id, bob_id, app.now_iso(), app.now_iso()),
        )
        alice_card = app.row("SELECT * FROM collection_items WHERE id = ?", (alice_card_id,))
        bob_card = app.row("SELECT * FROM collection_items WHERE id = ?", (bob_card_id,))
        app.add_trade_item(trade_id, alice_id, alice_card, 1, "offered")
        app.add_trade_item(trade_id, bob_id, bob_card, 1, "requested")
        bob = app.row("SELECT * FROM users WHERE id = ?", (bob_id,))

        counter_html = app.render_counter_trade(bob, trade_id)

        self.assertIn(f'name="counter_trade_id" value="{trade_id}"', counter_html)
        self.assertIn("Counter offer for", counter_html)
        self.assertIn("You request from Alice", counter_html)
        self.assertIn("1 x Counterspell", counter_html)
        self.assertIn("1 x Sol Ring", counter_html)
        self.assertIn("Rhystic Study", counter_html)

        form = {
            "recipient_id": [str(alice_id)],
            "counter_trade_id": [str(trade_id)],
            f"offer_{bob_card_id}": ["1"],
            f"request_{extra_alice_card_id}": ["1"],
        }
        offered = app.parse_trade_quantities(form, "offer", bob_id)
        requested = app.parse_trade_quantities(form, "request", alice_id)
        counter_id = app.create_trade_offer(bob_id, alice_id, "Counter message", offered, requested, trade_id)
        original = app.row("SELECT * FROM trades WHERE id = ?", (trade_id,))
        counter = app.row("SELECT * FROM trades WHERE id = ?", (counter_id,))

        self.assertEqual(original["status"], "countered")
        self.assertEqual(original["counter_trade_id"], counter_id)
        self.assertEqual(counter["countered_from_trade_id"], trade_id)
        counter_request = app.row("SELECT * FROM trade_items WHERE trade_id = ? AND side = 'requested'", (counter_id,))
        self.assertEqual(counter_request["card_name"], "Rhystic Study")
        alice = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))
        self.assertIn(f"Trade #{counter_id}", app.render_trade_detail(alice, trade_id))
        self.assertIn(f"Trade #{trade_id}", app.render_trade_detail(alice, counter_id))

    def test_completed_trade_moves_cards_between_collections(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        alice_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, collector_number, finish, condition, language,
                 quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', '703', 'Regular', 'NM', 'English', 2, 1, ?, ?)
            """,
            (alice_id, app.now_iso(), app.now_iso()),
        )
        bob_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, collector_number, finish, condition, language,
                 quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Lightning Bolt', 'Secret Lair', '182', 'Foil', 'NM', 'English', 3, 2, ?, ?)
            """,
            (bob_id, app.now_iso(), app.now_iso()),
        )
        trade_id = app.execute(
            """
            INSERT INTO trades (proposer_id, recipient_id, status, created_at, updated_at)
            VALUES (?, ?, 'accepted', ?, ?)
            """,
            (alice_id, bob_id, app.now_iso(), app.now_iso()),
        )
        alice_card = app.row("SELECT * FROM collection_items WHERE id = ?", (alice_card_id,))
        bob_card = app.row("SELECT * FROM collection_items WHERE id = ?", (bob_card_id,))
        app.add_trade_item(trade_id, alice_id, alice_card, 1, "offered")
        app.add_trade_item(trade_id, bob_id, bob_card, 2, "requested")

        app.complete_trade(trade_id)

        trade = app.row("SELECT * FROM trades WHERE id = ?", (trade_id,))
        alice_source = app.row("SELECT * FROM collection_items WHERE id = ?", (alice_card_id,))
        bob_source = app.row("SELECT * FROM collection_items WHERE id = ?", (bob_card_id,))
        bob_received = app.row("SELECT * FROM collection_items WHERE user_id = ? AND card_name = 'Sol Ring'", (bob_id,))
        alice_received = app.row("SELECT * FROM collection_items WHERE user_id = ? AND card_name = 'Lightning Bolt'", (alice_id,))

        self.assertEqual(trade["status"], "completed")
        self.assertEqual(alice_source["quantity"], 1)
        self.assertEqual(alice_source["quantity_for_trade"], 0)
        self.assertEqual(bob_source["quantity"], 1)
        self.assertEqual(bob_source["quantity_for_trade"], 0)
        self.assertEqual(bob_received["quantity"], 1)
        self.assertEqual(alice_received["quantity"], 2)

    def test_manabox_csv_import_normalizes_common_columns(self):
        user_id = app.create_user("collector", "password123", "Collector")
        csv_bytes = (
            "Name,Set code,Set name,Collector number,Foil,Quantity,Condition,Language,Scryfall ID,Price,Price source,Cardmarket Product ID,Card Kingdom SKU\n"
            "Sol Ring,CMM,Commander Masters,703,false,2,Near Mint,English,abc-123,1.25,Scryfall,222,CK-333\n"
            "Counterspell,DMR,Dominaria Remastered,45,true,1,Lightly Played,en,,0.50,TCGplayer,555,CK-666\n"
        ).encode("utf-8")

        result = app.import_collection_csv(user_id, csv_bytes, source="manabox", enrich_scryfall=False)
        cards = app.rows("SELECT * FROM collection_items WHERE user_id = ? ORDER BY card_name", (user_id,))

        self.assertEqual(result["inserted"], 2)
        self.assertEqual(cards[0]["finish"], "Foil")
        self.assertEqual(cards[0]["condition"], "LP")
        self.assertEqual(cards[0]["set_code"], "DMR")
        self.assertEqual(cards[0]["price_usd"], "")
        self.assertEqual(cards[0]["price_source"], "")
        self.assertEqual(cards[0]["cardmarket_product_id"], "555")
        self.assertEqual(cards[0]["cardkingdom_sku"], "CK-666")
        self.assertEqual(cards[1]["finish"], "Regular")
        self.assertEqual(cards[1]["scryfall_id"], "abc-123")
        self.assertEqual(cards[1]["price_usd"], "")
        self.assertEqual(cards[1]["price_source"], "")
        self.assertEqual(cards[1]["cardmarket_product_id"], "222")

    def test_archidekt_csv_import_merges_duplicates(self):
        user_id = app.create_user("drafter", "password123", "Drafter")
        csv_bytes = (
            "Quantity,Name,Edition,Collector Number,Foil,Condition,Language\n"
            "1,Lightning Bolt,Secret Lair,182,Foil,NM,English\n"
            "2,Lightning Bolt,Secret Lair,182,Foil,NM,English\n"
        ).encode("utf-8")

        result = app.import_collection_csv(user_id, csv_bytes, source="archidekt", enrich_scryfall=False)
        card = app.row("SELECT * FROM collection_items WHERE user_id = ?", (user_id,))

        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(card["quantity"], 3)

    def test_custom_csv_import_mapping_preset_applies_to_collection_import(self):
        user_id = app.create_user("mapper", "password123", "Mapper")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        preset = app.save_csv_import_mapping_preset(
            user_id,
            "Store export",
            {
                "name": "CardTitle",
                "quantity": "Owned",
                "trade": "TradeQty",
                "set_code": "EditionCode",
                "collector_number": "No",
                "finish": "FoilFlag",
                "condition": "Grade",
            },
            import_target="collection",
        )
        csv_bytes = (
            "CardTitle,Owned,TradeQty,EditionCode,No,FoilFlag,Grade\n"
            "Rhystic Study,3,2,WOT,25,foil,Lightly Played\n"
        ).encode("utf-8")
        mapping = app.csv_import_mapping_for_user(user_id, preset["id"], import_target="collection")

        preview = app.preview_collection_import_csv(user_id, csv_bytes, source="auto", enrich_scryfall=False, field_mapping=mapping)
        result = app.import_collection_csv(user_id, csv_bytes, source="auto", enrich_scryfall=False, field_mapping=mapping)
        card = app.row("SELECT * FROM collection_items WHERE user_id = ?", (user_id,))
        import_html = app.render_import(user)

        self.assertEqual(preview["inserted"], 1)
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(card["card_name"], "Rhystic Study")
        self.assertEqual(card["quantity"], 3)
        self.assertEqual(card["quantity_for_trade"], 2)
        self.assertEqual(card["set_code"], "WOT")
        self.assertEqual(card["collector_number"], "25")
        self.assertEqual(card["finish"], "Foil")
        self.assertEqual(card["condition"], "LP")
        self.assertIn("Store export", import_html)
        self.assertIn("CSV mapping presets", import_html)

    def test_admin_shared_csv_mapping_preset_is_visible_and_deletable_by_admin(self):
        admin_id = app.create_user("adminmap", "password123", "Admin Map", is_admin=True)
        user_id = app.create_user("regularmap", "password123", "Regular Map", is_admin=False)
        shared = app.save_csv_import_mapping_preset(
            admin_id,
            "Shared deck builder",
            {"name": "Title", "quantity": "Count", "section": "Board"},
            import_target="deck",
            is_shared=True,
            is_admin=True,
        )

        visible_to_user = app.csv_import_preset_rows_for_user(user_id, "deck")
        mapping = app.csv_import_mapping_for_user(user_id, shared["id"], import_target="deck")

        self.assertIn(shared["id"], [preset["id"] for preset in visible_to_user])
        self.assertEqual(mapping["name"], ["Title"])
        self.assertEqual(mapping["section"], ["Board"])
        with self.assertRaises(ValueError):
            app.delete_csv_import_mapping_preset(user_id, shared["id"], is_admin=False)
        self.assertEqual(app.delete_csv_import_mapping_preset(admin_id, shared["id"], is_admin=True), 1)
        self.assertIsNone(app.csv_import_preset_for_user(user_id, shared["id"], import_target="deck"))

    def test_deck_csv_import_mapping_preset_handles_custom_section_column(self):
        user_id = app.create_user("deckmapper", "password123", "Deck Mapper")
        mapping = {
            "name": ["CardTitle"],
            "quantity": ["Qty"],
            "section": ["BoardName"],
        }
        csv_bytes = (
            "BoardName,CardTitle,Qty\n"
            "Main,Sol Ring,1\n"
            "Maybeboard,Mana Crypt,1\n"
        ).encode("utf-8")

        section_rows, warnings = app.normalize_csv_rows_by_section(csv_bytes, field_mapping=mapping)

        self.assertEqual(warnings, [])
        self.assertEqual(section_rows["main"][0]["card_name"], "Sol Ring")
        self.assertEqual(section_rows["maybeboard"][0]["card_name"], "Mana Crypt")
        self.assertTrue(app.deck_import_sections_need_review(section_rows))

    def test_collection_import_preview_commits_and_undoes_batch(self):
        user_id = app.create_user("previewer", "password123", "Previewer")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, set_code, collector_number,
                 finish, condition, language, quantity, quantity_for_trade, notes, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 'CMM', '703',
                    'Regular', 'NM', 'English', 1, 0, 'original note', ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        csv_bytes = (
            "Name,Quantity,Trade,Set Name,Set Code,Collector Number,Foil,Condition,Language\n"
            "Sol Ring,2,1,Commander Masters,CMM,703,false,NM,English\n"
            "Lightning Bolt,1,1,Secret Lair,SLD,182,true,NM,English\n"
        ).encode("utf-8")

        preview = app.preview_collection_import_csv(user_id, csv_bytes, source="manabox", enrich_scryfall=False)
        preview_batch = app.row("SELECT * FROM import_batches WHERE id = ?", (preview["batch_id"],))
        before_commit = app.rows("SELECT * FROM collection_items WHERE user_id = ? ORDER BY card_name", (user_id,))
        preview_html = app.render_import(user, preview=preview)
        result = app.commit_collection_import_preview(user_id, preview["batch_id"])
        after_commit = app.rows("SELECT * FROM collection_items WHERE user_id = ? ORDER BY card_name", (user_id,))
        undo = app.undo_import_batch(user_id, preview["batch_id"])
        after_undo = app.rows("SELECT * FROM collection_items WHERE user_id = ? ORDER BY card_name", (user_id,))
        undone_batch = app.row("SELECT * FROM import_batches WHERE id = ?", (preview["batch_id"],))

        self.assertEqual(preview["inserted"], 1)
        self.assertEqual(preview["updated"], 1)
        self.assertEqual(preview_batch["status"], "preview")
        self.assertEqual([item["card_name"] for item in before_commit], ["Sol Ring"])
        self.assertIn("Import preview", preview_html)
        self.assertIn("Import these rows", preview_html)
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["batch_id"], preview["batch_id"])
        self.assertEqual({item["card_name"]: item["quantity"] for item in after_commit}, {"Lightning Bolt": 1, "Sol Ring": 3})
        self.assertEqual({item["card_name"]: item["quantity_for_trade"] for item in after_commit}, {"Lightning Bolt": 1, "Sol Ring": 1})
        self.assertEqual(undo["undone_items"], 2)
        self.assertEqual([item["card_name"] for item in after_undo], ["Sol Ring"])
        self.assertEqual(after_undo[0]["quantity"], 1)
        self.assertEqual(after_undo[0]["quantity_for_trade"], 0)
        self.assertEqual(after_undo[0]["notes"], "original note")
        self.assertEqual(undone_batch["status"], "undone")

    def test_csv_import_can_enrich_from_local_scryfall_bulk_data(self):
        user_id = app.create_user("oracle", "password123", "Oracle")
        csv_bytes = "Name,Quantity\nSol Ring,1\n".encode("utf-8")
        app.store_scryfall_bulk_cards([
            {
                "object": "card",
                "id": "fake-id",
                "name": "Sol Ring",
                "set_name": "Commander Masters",
                "set": "cmm",
                "collector_number": "703",
                "released_at": "2023-08-04",
                "image_uris": {"small": "https://img.example/sol-ring.jpg"},
                "mana_cost": "{1}",
                "type_line": "Artifact",
                "oracle_text": "Tap: Add two colorless mana.",
                "rarity": "uncommon",
                "color_identity": [],
                "scryfall_uri": "https://scryfall.com/card/cmm/703/sol-ring",
                "prices": {"usd": "1.23"},
                "tcgplayer_id": 123456,
                "cardmarket_id": 654321,
            }
        ])

        result = app.import_collection_csv(user_id, csv_bytes, enrich_scryfall=True)

        card = app.row("SELECT * FROM collection_items WHERE user_id = ?", (user_id,))

        self.assertEqual(result["enriched"], 1)
        self.assertEqual(result["queued"], 0)
        self.assertEqual(card["type_line"], "Artifact")
        self.assertEqual(card["set_code"], "CMM")
        self.assertEqual(card["price_usd"], "1.23")
        self.assertEqual(card["tcgplayer_product_id"], "123456")
        self.assertEqual(card["cardmarket_product_id"], "654321")

    def test_csv_import_queues_background_scryfall_enrichment_misses(self):
        user_id = app.create_user("queue", "password123", "Queue")
        csv_bytes = "Name,Quantity\nMystery Card,1\n".encode("utf-8")

        result = app.import_collection_csv(user_id, csv_bytes, enrich_scryfall=True)

        card = app.row("SELECT * FROM collection_items WHERE user_id = ?", (user_id,))
        job = app.row("SELECT * FROM scryfall_enrichment_jobs WHERE collection_item_id = ?", (card["id"],))

        self.assertEqual(result["enriched"], 0)
        self.assertEqual(result["queued"], 1)
        self.assertEqual(card["type_line"], "")
        self.assertEqual(job["status"], "pending")

    def test_background_scryfall_enrichment_updates_queued_card(self):
        user_id = app.create_user("worker", "password123", "Worker")
        csv_bytes = "Name,Quantity\nMystery Card,1\n".encode("utf-8")
        app.import_collection_csv(user_id, csv_bytes, enrich_scryfall=True)
        original_lookup = app.lookup_scryfall_card

        def fake_lookup(card_name, set_code="", collector_number="", scryfall_id=""):
            return {
                "card_name": "Mystery Card",
                "set_name": "Test Set",
                "set_code": "TST",
                "collector_number": "42",
                "scryfall_id": "mystery-id",
                "image_url": "https://img.example/mystery.jpg",
                "mana_cost": "{2}",
                "type_line": "Creature",
                "oracle_text": "Test text.",
                "rarity": "rare",
                "colors": "",
                "color_identity": "",
                "scryfall_uri": "https://scryfall.com/card/tst/42/mystery-card",
                "price_usd": "0.42",
            }

        try:
            app.lookup_scryfall_card = fake_lookup
            processed = app.process_scryfall_enrichment_once()
        finally:
            app.lookup_scryfall_card = original_lookup

        card = app.row("SELECT * FROM collection_items WHERE user_id = ?", (user_id,))
        job = app.row("SELECT * FROM scryfall_enrichment_jobs WHERE collection_item_id = ?", (card["id"],))
        notification = app.row("SELECT * FROM user_notifications WHERE user_id = ? AND kind = 'scryfall_import'", (user_id,))

        self.assertTrue(processed)
        self.assertEqual(card["scryfall_id"], "mystery-id")
        self.assertEqual(card["type_line"], "Creature")
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["completion_notified"], 1)
        self.assertIsNotNone(notification)
        self.assertIn("1 enriched", notification["body"])

    def test_import_page_hides_manual_scryfall_refresh_controls(self):
        user_id = app.create_user("importui", "password123", "Import UI")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))

        html = app.render_import(user)

        self.assertNotIn("Update local Scryfall data", html)
        self.assertNotIn("Refresh collection prices", html)
        self.assertNotIn('action="/import/scryfall-sync"', html)
        self.assertNotIn('action="/prices/refresh"', html)
        self.assertIn("update automatically in the background", html)

    def test_scryfall_price_history_and_alerts_are_recorded(self):
        user_id = app.create_user("history", "password123", "History")
        card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, set_code, collector_number,
                 quantity, quantity_for_trade, scryfall_id, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 'CMM', '703',
                    1, 0, 'sol-ring-id', '1.00', 'scryfall', ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )

        app.update_collection_item_from_scryfall(
            card_id,
            {
                "card_name": "Sol Ring",
                "set_name": "Commander Masters",
                "set_code": "CMM",
                "collector_number": "703",
                "scryfall_id": "sol-ring-id",
                "type_line": "Artifact",
                "price_usd": "1.50",
            },
        )

        history = app.rows("SELECT * FROM price_history WHERE collection_item_id = ? ORDER BY id", (card_id,))
        alerts = app.rows("SELECT * FROM user_notifications WHERE user_id = ? AND kind = 'price_alert'", (user_id,))
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        form_html = app.render_collection_form(user, app.row("SELECT * FROM collection_items WHERE id = ?", (card_id,)))

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["previous_price_usd"], "1.00")
        self.assertEqual(history[0]["price_usd"], "1.50")
        self.assertEqual(history[0]["change_amount"], "0.50")
        self.assertEqual(len(alerts), 1)
        self.assertIn("Price increased", alerts[0]["title"])
        self.assertIn("Price history", form_html)
        self.assertIn("$1.50", form_html)

    def test_price_alert_threshold_and_toggle_control_notifications(self):
        user_id = app.create_user("threshold", "password123", "Threshold")
        app.update_user_profile(user_id, "threshold", "Threshold", "", "", False, "", True, "10")
        card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade,
                 price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 1, 0,
                    '10.00', 'scryfall', ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        item = app.row("SELECT * FROM collection_items WHERE id = ?", (card_id,))

        app.record_price_history_for_item(card_id, user_id, item, "10.00", "10.50")
        self.assertIsNone(app.row("SELECT * FROM user_notifications WHERE user_id = ?", (user_id,)))

        item = app.row("SELECT * FROM collection_items WHERE id = ?", (card_id,))
        app.record_price_history_for_item(card_id, user_id, item, "10.50", "12.00")
        alert = app.row("SELECT * FROM user_notifications WHERE user_id = ?", (user_id,))
        self.assertIsNotNone(alert)

        app.update_user_profile(user_id, "threshold", "Threshold", "", "", False, "", False, "0")
        item = app.row("SELECT * FROM collection_items WHERE id = ?", (card_id,))
        app.record_price_history_for_item(card_id, user_id, item, "12.00", "15.00")
        alerts = app.rows("SELECT * FROM user_notifications WHERE user_id = ?", (user_id,))
        self.assertEqual(len(alerts), 1)

    def test_collection_price_refresh_uses_local_scryfall_data(self):
        user_id = app.create_user("refresh", "password123", "Refresh")
        card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, set_code, collector_number,
                 quantity, quantity_for_trade, scryfall_id, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 'CMM', '703',
                    1, 0, 'fake-id', '1.00', 'scryfall', ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        app.store_scryfall_bulk_cards([
            {
                "object": "card",
                "id": "fake-id",
                "name": "Sol Ring",
                "set_name": "Commander Masters",
                "set": "cmm",
                "collector_number": "703",
                "released_at": "2023-08-04",
                "type_line": "Artifact",
                "rarity": "uncommon",
                "prices": {"usd": "1.75"},
            }
        ])

        result = app.refresh_user_scryfall_prices(user_id)

        card = app.row("SELECT * FROM collection_items WHERE id = ?", (card_id,))
        alert = app.row("SELECT * FROM user_notifications WHERE user_id = ? AND kind = 'price_alert'", (user_id,))

        self.assertEqual(result["priced"], 1)
        self.assertEqual(result["changed"], 1)
        self.assertEqual(card["price_usd"], "1.75")
        self.assertEqual(card["price_source"], "scryfall")
        self.assertIn("1.00 to $1.75", alert["body"])

    def test_automatic_scryfall_price_refresh_updates_all_users(self):
        alice_id = app.create_user("autoalice", "password123", "Auto Alice")
        bob_id = app.create_user("autobob", "password123", "Auto Bob")
        for user_id, card_name, scryfall_id, old_price, new_price in (
            (alice_id, "Sol Ring", "sol-id", "1.00", "1.25"),
            (bob_id, "Counterspell", "counter-id", "2.00", "2.50"),
        ):
            app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, quantity, quantity_for_trade,
                     scryfall_id, price_usd, price_source, created_at, updated_at)
                VALUES (?, 'mtg', ?, 1, 0, ?, ?, 'scryfall', ?, ?)
                """,
                (user_id, card_name, scryfall_id, old_price, app.now_iso(), app.now_iso()),
            )
            app.store_scryfall_bulk_cards([
                {
                    "object": "card",
                    "id": "sol-id",
                    "name": "Sol Ring",
                    "set_name": "Commander Masters",
                    "set": "cmm",
                    "collector_number": "703",
                    "prices": {"usd": "1.25"},
                },
                {
                    "object": "card",
                    "id": "counter-id",
                    "name": "Counterspell",
                    "set_name": "Dominaria Remastered",
                    "set": "dmr",
                    "collector_number": "45",
                    "prices": {"usd": "2.50"},
                },
            ])

        result = app.refresh_all_scryfall_prices(sync_bulk=False)

        self.assertEqual(result["users"], 2)
        self.assertEqual(result["changed"], 2)
        self.assertFalse(app.scryfall_price_refresh_due())
        self.assertEqual(app.get_setting(app.SCRYFALL_PRICE_REFRESH_STATUS_KEY), "idle")
        self.assertEqual(app.row("SELECT price_usd FROM collection_items WHERE user_id = ?", (alice_id,))["price_usd"], "1.25")
        self.assertEqual(app.row("SELECT price_usd FROM collection_items WHERE user_id = ?", (bob_id,))["price_usd"], "2.50")
        alice_refresh = app.row("SELECT * FROM user_notifications WHERE user_id = ? AND kind = 'price_refresh'", (alice_id,))
        bob_refresh = app.row("SELECT * FROM user_notifications WHERE user_id = ? AND kind = 'price_refresh'", (bob_id,))
        self.assertIsNotNone(alice_refresh)
        self.assertIsNotNone(bob_refresh)
        self.assertIn("Scryfall prices updated", alice_refresh["title"])

    def test_external_price_providers_are_disabled(self):
        user_id = app.create_user("prices", "password123", "Prices")
        card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 1, 0, '1.00', 'scryfall', ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )

        queued = app.schedule_price_refresh_jobs(user_id, provider="cardmarket")
        processed = app.process_price_refresh_once()
        applied = app.apply_cached_provider_prices([user_id], "cardmarket")
        result = app.prepare_price_basis_for_users([user_id], "cardmarket", force=False)
        card = app.row("SELECT * FROM collection_items WHERE id = ?", (card_id,))

        self.assertEqual(app.PRICE_PROVIDER_KEYS, ())
        self.assertEqual(queued, 0)
        self.assertFalse(processed)
        self.assertEqual(applied, 0)
        self.assertEqual(result, {"provider": "scryfall", "applied": 0, "queued": 0, "configured": True})
        self.assertEqual(card["price_source"], "scryfall")

    def test_non_scryfall_price_inputs_normalize_to_scryfall(self):
        self.assertEqual(app.normalize_price_basis("tcgplayer"), "scryfall")
        self.assertEqual(app.normalize_price_source("TCGplayer"), "scryfall")
        self.assertEqual(app.normalize_price_source("Manual"), "scryfall")
        self.assertEqual(app.price_source_label("cardmarket"), "Scryfall")

    def test_manual_card_lookup_can_enrich_before_save(self):
        user_id = app.create_user("manual", "password123", "Manual")
        original_lookup = app.lookup_scryfall_card

        def fake_lookup(card_name, set_code="", collector_number="", scryfall_id=""):
            return {
                "card_name": "Rhystic Study",
                "set_name": "Wilds of Eldraine: Enchanting Tales",
                "set_code": "WOT",
                "collector_number": "25",
                "scryfall_id": "study-id",
                "image_url": "https://img.example/rhystic-study.jpg",
                "mana_cost": "{2}{U}",
                "type_line": "Enchantment",
                "oracle_text": "Whenever an opponent casts a spell...",
                "rarity": "rare",
                "colors": "U",
                "color_identity": "U",
                "scryfall_uri": "https://scryfall.com/card/wot/25/rhystic-study",
                "price_usd": "42.00",
            }

        form = {
            "card_name": ["Rhystic Study"],
            "game": ["mtg"],
            "quantity": ["1"],
            "quantity_for_trade": ["0"],
            "lookup_on_save": ["1"],
        }
        try:
            app.lookup_scryfall_card = fake_lookup
            data = app.validate_collection_form(form)
            enriched = app.enrich_collection_data_from_scryfall(data)
            app.upsert_collection_item(user_id, enriched, merge=False)
        finally:
            app.lookup_scryfall_card = original_lookup

        card = app.row("SELECT * FROM collection_items WHERE user_id = ?", (user_id,))

        self.assertEqual(card["scryfall_id"], "study-id")
        self.assertEqual(card["type_line"], "Enchantment")
        self.assertEqual(card["set_code"], "WOT")

    def test_lookup_enriched_add_form_renders_without_item_id(self):
        user_id = app.create_user("renderer", "password123", "Renderer")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        item = {
            "game": "mtg",
            "card_name": "Sol Ring",
            "set_name": "Commander Masters",
            "set_code": "CMM",
            "collector_number": "703",
            "finish": "Regular",
            "condition": "NM",
            "language": "English",
            "quantity": 1,
            "quantity_for_trade": 0,
            "notes": "",
            "scryfall_id": "fake-id",
            "image_url": "",
            "mana_cost": "{1}",
            "type_line": "Artifact",
            "oracle_text": "Tap: Add two colorless mana.",
            "rarity": "uncommon",
            "colors": "",
            "color_identity": "",
            "scryfall_uri": "https://scryfall.com/card/cmm/703/sol-ring",
            "price_usd": "1.23",
            "lookup_on_save": "1",
        }

        html = app.render_collection_form(user, item)

        self.assertIn('action="/collection/new"', html)
        self.assertIn("Open on Scryfall", html)

    def test_scryfall_partial_search_returns_selectable_prints(self):
        original_get = app.scryfall_get

        def fake_get(path, params=None):
            self.assertEqual(path, "/cards/search")
            self.assertIn("name:sol", params["q"])
            self.assertEqual(params["unique"], "cards")
            return {
                "data": [
                    {
                        "id": "sol-one",
                        "name": "Sol Ring",
                        "set_name": "Commander Masters",
                        "set": "cmm",
                        "collector_number": "703",
                        "type_line": "Artifact",
                        "rarity": "uncommon",
                        "image_uris": {"small": "https://img.example/sol-1.jpg"},
                        "prices": {"usd": "1.00"},
                    },
                    {
                        "id": "sol-two",
                        "name": "Sol Ring",
                        "set_name": "Fallout",
                        "set": "pip",
                        "collector_number": "233",
                        "type_line": "Artifact",
                        "rarity": "uncommon",
                        "image_uris": {"small": "https://img.example/sol-2.jpg"},
                        "prices": {"usd": "2.00"},
                    },
                ]
            }

        try:
            app.scryfall_get = fake_get
            results = app.search_scryfall_cards("sol")
        finally:
            app.scryfall_get = original_get

        selected = app.selected_scryfall_card_data("sol-two")

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["set_code"], "CMM")
        self.assertEqual(selected["set_code"], "PIP")

    def test_scryfall_variation_search_returns_prints_for_selected_card(self):
        original_get = app.scryfall_get

        def fake_get(path, params=None):
            self.assertEqual(path, "/cards/search")
            self.assertIn('!"Sol Ring"', params["q"])
            self.assertEqual(params["unique"], "prints")
            return {
                "data": [
                    {
                        "id": "sol-cmm",
                        "name": "Sol Ring",
                        "set_name": "Commander Masters",
                        "set": "cmm",
                        "collector_number": "703",
                        "type_line": "Artifact",
                        "rarity": "uncommon",
                        "image_uris": {"small": "https://img.example/sol-cmm.jpg"},
                        "prices": {"usd": "1.00"},
                    },
                    {
                        "id": "sol-pip",
                        "name": "Sol Ring",
                        "set_name": "Fallout",
                        "set": "pip",
                        "collector_number": "233",
                        "type_line": "Artifact",
                        "rarity": "uncommon",
                        "image_uris": {"small": "https://img.example/sol-pip.jpg"},
                        "prices": {"usd": "2.00"},
                    },
                ]
            }

        try:
            app.scryfall_get = fake_get
            results = app.search_scryfall_prints("Sol Ring")
        finally:
            app.scryfall_get = original_get

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["set_code"], "CMM")
        self.assertEqual(results[1]["set_code"], "PIP")

    def test_collection_form_renders_scryfall_result_picker(self):
        user_id = app.create_user("picker", "password123", "Picker")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        item = {
            "game": "mtg",
            "card_name": "sol",
            "set_name": "",
            "set_code": "",
            "collector_number": "",
            "finish": "Regular",
            "condition": "NM",
            "language": "English",
            "quantity": 1,
            "quantity_for_trade": 0,
            "notes": "",
            "lookup_on_save": "1",
        }
        results = [
            {
                "scryfall_id": "sol-one",
                "card_name": "Sol Ring",
                "set_name": "Commander Masters",
                "set_code": "CMM",
                "collector_number": "703",
                "image_url": "",
                "type_line": "Artifact",
                "rarity": "uncommon",
                "price_usd": "1.00",
            }
        ]

        html = app.render_collection_form(user, item, scryfall_results=results)

        self.assertIn("Scryfall matches", html)
        self.assertIn('name="selected_scryfall_id"', html)
        self.assertIn("Use selected card", html)

    def test_collection_form_can_render_card_first_picker(self):
        user_id = app.create_user("cardpicker", "password123", "Card Picker")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        item = {
            "game": "mtg",
            "card_name": "sol",
            "set_name": "",
            "set_code": "",
            "collector_number": "",
            "finish": "Regular",
            "condition": "NM",
            "language": "English",
            "quantity": 1,
            "quantity_for_trade": 0,
            "notes": "",
            "lookup_on_save": "1",
        }
        results = [
            {
                "scryfall_id": "sol-one",
                "card_name": "Sol Ring",
                "set_name": "Commander Masters",
                "set_code": "CMM",
                "collector_number": "703",
                "image_url": "",
                "type_line": "Artifact",
                "rarity": "uncommon",
                "price_usd": "1.00",
            }
        ]

        html = app.render_collection_form(
            user,
            item,
            scryfall_results=results,
            scryfall_picker_intent="choose_scryfall_card",
            scryfall_picker_label="Show variations",
            scryfall_picker_title="Scryfall card matches",
        )

        self.assertIn("Scryfall card matches", html)
        self.assertIn('value="choose_scryfall_card"', html)
        self.assertIn("Show variations", html)

    def test_want_form_renders_scryfall_result_picker(self):
        user_id = app.create_user("wishlist", "password123", "Wishlist")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        draft = app.default_want_item()
        draft["card_name"] = "rhystic"
        results = [
            {
                "scryfall_id": "rhystic-one",
                "card_name": "Rhystic Study",
                "set_name": "Wilds of Eldraine: Enchanting Tales",
                "set_code": "WOT",
                "collector_number": "25",
                "image_url": "",
                "type_line": "Enchantment",
                "rarity": "rare",
                "price_usd": "42.00",
            }
        ]

        html = app.render_wants(user, draft=draft, scryfall_results=results)

        self.assertIn("Scryfall matches", html)
        self.assertIn("Use selected want", html)
        self.assertIn('name="selected_scryfall_id"', html)

    def test_want_variant_picker_can_multi_select_printings(self):
        user_id = app.create_user("wishlistmulti", "password123", "Wishlist Multi")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        draft = app.default_want_item()
        draft["card_name"] = "Sol Ring"
        draft["selected_scryfall_id"] = "sol-card"
        results = [
            {
                "scryfall_id": "sol-cmm",
                "card_name": "Sol Ring",
                "set_name": "Commander Masters",
                "set_code": "CMM",
                "collector_number": "703",
                "image_url": "",
                "type_line": "Artifact",
                "rarity": "uncommon",
                "price_usd": "1.00",
            },
            {
                "scryfall_id": "sol-pip",
                "card_name": "Sol Ring",
                "set_name": "Fallout",
                "set_code": "PIP",
                "collector_number": "233",
                "image_url": "",
                "type_line": "Artifact",
                "rarity": "uncommon",
                "price_usd": "2.00",
            },
        ]

        html = app.render_wants(
            user,
            draft=draft,
            scryfall_results=results,
            scryfall_picker_intent="add_scryfall_wants",
            scryfall_picker_label="Add selected wants",
            scryfall_picker_title="Printings and variants",
            scryfall_picker_multiple=True,
        )

        self.assertIn("Printings and variants", html)
        self.assertIn("Select all shown", html)
        self.assertIn('data-scryfall-select-all', html)
        self.assertIn('name="selected_scryfall_ids"', html)
        self.assertIn('type="checkbox"', html)
        self.assertIn('name="selected_scryfall_id" value="sol-card"', html)
        self.assertIn("Add selected wants", html)

    def test_selected_want_printings_insert_multiple_wants(self):
        user_id = app.create_user("multiwant", "password123", "Multi Want")
        data = app.validate_want_form({
            "card_name": ["Sol Ring"],
            "game": ["mtg"],
            "desired_quantity": ["2"],
            "notes": ["Any commander copies"],
            "lookup_on_save": ["1"],
        })
        card_data = {
            "sol-cmm": {
                "card_name": "Sol Ring",
                "set_name": "Commander Masters",
                "set_code": "CMM",
                "collector_number": "703",
                "scryfall_id": "sol-cmm",
                "type_line": "Artifact",
                "price_usd": "1.00",
            },
            "sol-pip": {
                "card_name": "Sol Ring",
                "set_name": "Fallout",
                "set_code": "PIP",
                "collector_number": "233",
                "scryfall_id": "sol-pip",
                "type_line": "Artifact",
                "price_usd": "2.00",
            },
        }
        original_selected = app.selected_scryfall_card_data

        def fake_selected(selected_scryfall_id):
            return card_data[selected_scryfall_id]

        try:
            app.selected_scryfall_card_data = fake_selected
            inserted = app.insert_selected_want_items(user_id, data, ["sol-cmm", "sol-pip", "sol-cmm", ""])
        finally:
            app.selected_scryfall_card_data = original_selected

        wants = app.rows("SELECT * FROM want_items WHERE user_id = ? ORDER BY set_code", (user_id,))

        self.assertEqual(inserted, 2)
        self.assertEqual([want["set_code"] for want in wants], ["CMM", "PIP"])
        self.assertEqual([want["desired_quantity"] for want in wants], [2, 2])
        self.assertEqual(wants[0]["notes"], "Any commander copies")
        self.assertEqual(wants[1]["price_usd"], "2.00")

    def test_want_insert_stores_scryfall_metadata(self):
        user_id = app.create_user("wanter", "password123", "Wanter")
        form = {
            "card_name": ["Rhystic Study"],
            "game": ["mtg"],
            "set_name": [""],
            "set_code": [""],
            "collector_number": [""],
            "desired_quantity": ["2"],
            "condition": ["LP"],
            "finish": ["Foil"],
            "language": ["Japanese"],
            "notes": ["Need a commander copy"],
            "lookup_on_save": ["1"],
        }
        data = app.validate_want_form(form)
        enriched = app.apply_scryfall_data(
            data,
            {
                "card_name": "Rhystic Study",
                "set_name": "Wilds of Eldraine: Enchanting Tales",
                "set_code": "WOT",
                "collector_number": "25",
                "scryfall_id": "study-id",
                "image_url": "https://img.example/rhystic-study.jpg",
                "mana_cost": "{2}{U}",
                "type_line": "Enchantment",
                "oracle_text": "Whenever an opponent casts a spell...",
                "rarity": "rare",
                "colors": "U",
                "color_identity": "U",
                "scryfall_uri": "https://scryfall.com/card/wot/25/rhystic-study",
                "price_usd": "42.00",
            },
        )

        app.insert_want_item(user_id, enriched)
        want = app.row("SELECT * FROM want_items WHERE user_id = ?", (user_id,))

        self.assertEqual(want["desired_quantity"], 2)
        self.assertEqual(want["condition"], "LP")
        self.assertEqual(want["finish"], "Foil")
        self.assertEqual(want["language"], "Japanese")
        self.assertEqual(want["scryfall_id"], "study-id")
        self.assertEqual(want["set_code"], "WOT")
        self.assertEqual(want["type_line"], "Enchantment")

    def test_want_edit_form_and_update_supports_preferences(self):
        user_id = app.create_user("editor", "password123", "Editor")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        want_id = app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, set_name, desired_quantity, condition, finish, language, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 1, 'NM,LP', 'Regular,Foil', 'English', ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        want = app.row("SELECT * FROM want_items WHERE id = ?", (want_id,))
        html = app.render_wants(user, want, edit_want_id=want_id)
        data = app.validate_want_form({
            "card_name": ["Sol Ring"],
            "game": ["mtg"],
            "set_name": ["Fallout"],
            "set_code": ["PIP"],
            "collector_number": ["233"],
            "desired_quantity": ["3"],
            "condition": ["LP", "NM"],
            "finish": ["Foil", "Regular"],
            "language": ["Japanese", "English"],
            "notes": ["Prefer the Fallout printing"],
            "lookup_on_save": [""],
        })

        updated = app.update_want_item(user_id, want_id, data)
        saved = app.row("SELECT * FROM want_items WHERE id = ?", (want_id,))

        self.assertIn(f'action="/wants/{want_id}/edit"', html)
        self.assertIn('class="want-card editing"', html)
        self.assertIn('action="/wants/new"', html)
        self.assertIn("Edit wanted card", html)
        self.assertIn("Add wanted card", html)
        self.assertIn("Cancel edit", html)
        self.assertIn('data-preference-select-all', html)
        self.assertIn('name="condition" value="NM" checked', html)
        self.assertIn('name="condition" value="LP" checked', html)
        self.assertIn('name="finish" value="Regular" checked', html)
        self.assertIn('name="finish" value="Foil" checked', html)
        self.assertEqual(updated, 1)
        self.assertEqual(saved["set_name"], "Fallout")
        self.assertEqual(saved["set_code"], "PIP")
        self.assertEqual(saved["collector_number"], "233")
        self.assertEqual(saved["desired_quantity"], 3)
        self.assertEqual(saved["condition"], "NM,LP")
        self.assertEqual(saved["finish"], "Regular,Foil")
        self.assertEqual(saved["language"], "English,Japanese")
        self.assertEqual(saved["notes"], "Prefer the Fallout printing")

    def test_want_trade_matches_honor_condition_finish_and_language_preferences(self):
        wanter_id = app.create_user("preference", "password123", "Preference")
        trader_id = app.create_user("trader", "password123", "Trader")
        app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, desired_quantity, condition, finish, language, created_at, updated_at)
            VALUES (?, 'mtg', 'Counterspell', 1, 'NM,LP', 'Regular,Foil', 'English,Japanese', ?, ?)
            """,
            (wanter_id, app.now_iso(), app.now_iso()),
        )
        for condition, finish, language, trade_qty in (
            ("NM", "Foil", "Japanese", 2),
            ("LP", "Foil", "Japanese", 4),
            ("NM", "Regular", "English", 3),
            ("NM", "Foil", "English", 5),
            ("MP", "Foil", "Japanese", 6),
            ("LP", "Etched", "English", 7),
            ("NM", "Foil", "German", 8),
        ):
            app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, condition, finish, language, quantity, quantity_for_trade, created_at, updated_at)
                VALUES (?, 'mtg', 'Counterspell', ?, ?, ?, 5, ?, ?, ?)
                """,
                (trader_id, condition, finish, language, trade_qty, app.now_iso(), app.now_iso()),
            )

        want = app.row("SELECT * FROM want_items WHERE user_id = ?", (wanter_id,))
        availability = app.want_trade_matches(wanter_id, want)

        self.assertEqual(availability["total_quantity"], 14)
        self.assertEqual(availability["user_count"], 1)

    def test_want_trade_matches_find_other_users_tradeable_cards(self):
        wanter_id = app.create_user("wanter", "password123", "Wanter")
        trader_id = app.create_user("trader", "password123", "Trader")
        other_id = app.create_user("other", "password123", "Other")
        app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, set_code, collector_number, desired_quantity, scryfall_id, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'CMM', '703', 1, 'sol-id', ?, ?)
            """,
            (wanter_id, app.now_iso(), app.now_iso()),
        )
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_code, collector_number, quantity, quantity_for_trade, scryfall_id, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'CMM', '703', 2, 2, 'sol-id', ?, ?)
            """,
            (trader_id, app.now_iso(), app.now_iso()),
        )
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 1, 1, ?, ?)
            """,
            (wanter_id, app.now_iso(), app.now_iso()),
        )
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'pokemon', 'Sol Ring', 1, 1, ?, ?)
            """,
            (other_id, app.now_iso(), app.now_iso()),
        )

        want = app.row("SELECT * FROM want_items WHERE user_id = ?", (wanter_id,))
        availability = app.want_trade_matches(wanter_id, want)

        self.assertEqual(availability["total_quantity"], 2)
        self.assertEqual(availability["user_count"], 1)
        self.assertEqual(availability["matches"][0]["display_name"], "Trader")

    def test_wants_page_renders_available_trade_indicator(self):
        wanter_id = app.create_user("wanter", "password123", "Wanter")
        trader_id = app.create_user("trader", "password123", "Trader")
        user = app.row("SELECT * FROM users WHERE id = ?", (wanter_id,))
        app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, desired_quantity, created_at, updated_at)
            VALUES (?, 'mtg', 'Counterspell', 1, ?, ?)
            """,
            (wanter_id, app.now_iso(), app.now_iso()),
        )
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Counterspell', 4, 1, ?, ?)
            """,
            (trader_id, app.now_iso(), app.now_iso()),
        )

        html = app.render_wants(user)

        self.assertIn("Available for trade", html)
        self.assertIn("Trader", html)
        self.assertIn("want-card", html)

    def test_wants_page_sorts_by_desired_quantity(self):
        user_id = app.create_user("wanter", "password123", "Wanter")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        for name, desired in [("Small Want", 1), ("Big Want", 4)]:
            app.execute(
                """
                INSERT INTO want_items
                    (user_id, game, card_name, desired_quantity, created_at, updated_at)
                VALUES (?, 'mtg', ?, ?, ?, ?)
                """,
                (user_id, name, desired, app.now_iso(), app.now_iso()),
            )

        html = app.render_wants(user, query={"sort": ["qty"], "dir": ["desc"]})

        self.assertIn('name="sort"', html)
        self.assertIn('name="dir"', html)
        self.assertLess(html.index("Big Want"), html.index("Small Want"))

    def test_private_wants_are_hidden_from_member_profile_but_visible_to_owner(self):
        owner_id = app.create_user("owner", "password123", "Owner")
        viewer_id = app.create_user("viewer", "password123", "Viewer")
        owner = app.row("SELECT * FROM users WHERE id = ?", (owner_id,))
        viewer = app.row("SELECT * FROM users WHERE id = ?", (viewer_id,))
        app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, desired_quantity, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Public Want', 1, 1, ?, ?)
            """,
            (owner_id, app.now_iso(), app.now_iso()),
        )
        app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, desired_quantity, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Private Want', 1, 0, ?, ?)
            """,
            (owner_id, app.now_iso(), app.now_iso()),
        )

        owner_html = app.render_wants(owner)
        member_html = app.render_member_detail(viewer, owner_id)
        default_data = app.validate_want_form({"card_name": ["Visible Want"], "game": ["mtg"], "desired_quantity": ["1"]})
        private_data = app.validate_want_form({
            "card_name": ["Hidden Want"],
            "game": ["mtg"],
            "desired_quantity": ["1"],
            "_visibility_present": ["1"],
        })

        self.assertIn("Public Want", owner_html)
        self.assertIn("Private Want", owner_html)
        self.assertIn("Private", owner_html)
        self.assertIn("Public Want", member_html)
        self.assertNotIn("Private Want", member_html)
        self.assertEqual(default_data["is_public"], 1)
        self.assertEqual(private_data["is_public"], 0)

    def test_watchlist_alert_created_when_trade_card_matches_want(self):
        wanter_id = app.create_user("wanter", "password123", "Wanter")
        trader_id = app.create_user("trader", "password123", "Trader")
        app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, set_code, collector_number, desired_quantity, scryfall_id, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'CMM', '703', 1, 'sol-id', ?, ?)
            """,
            (wanter_id, app.now_iso(), app.now_iso()),
        )

        app.upsert_collection_item(
            trader_id,
            {
                "game": "mtg",
                "card_name": "Sol Ring",
                "set_name": "Commander Masters",
                "set_code": "CMM",
                "collector_number": "703",
                "quantity": 2,
                "quantity_for_trade": 2,
                "scryfall_id": "sol-id",
            },
        )

        notification = app.row("SELECT * FROM user_notifications WHERE user_id = ?", (wanter_id,))
        trader_notification = app.row("SELECT * FROM user_notifications WHERE user_id = ?", (trader_id,))

        self.assertIsNotNone(notification)
        self.assertEqual(notification["kind"], "watchlist_alert")
        self.assertEqual(notification["title"], "Watchlist match: Sol Ring")
        self.assertIn("Trader added 2 Sol Ring cards", notification["body"])
        self.assertIn("/browse?", notification["url"])
        self.assertIn("q=Sol+Ring", notification["url"])
        self.assertIn(f"user={trader_id}", notification["url"])
        self.assertIsNone(trader_notification)

    def test_watchlist_alert_respects_toggle_and_existing_tradeable_items(self):
        wanter_id = app.create_user("wanter", "password123", "Wanter")
        disabled_id = app.create_user("disabled", "password123", "Disabled")
        trader_id = app.create_user("trader", "password123", "Trader")
        app.execute(
            "UPDATE users SET watchlist_alerts_enabled = 0 WHERE id = ?",
            (disabled_id,),
        )
        for user_id, card_name in (
            (wanter_id, "Counterspell"),
            (disabled_id, "Lightning Bolt"),
        ):
            app.execute(
                """
                INSERT INTO want_items
                    (user_id, game, card_name, desired_quantity, created_at, updated_at)
                VALUES (?, 'mtg', ?, 1, ?, ?)
                """,
                (user_id, card_name, app.now_iso(), app.now_iso()),
            )

        app.upsert_collection_item(
            trader_id,
            {
                "game": "mtg",
                "card_name": "Counterspell",
                "quantity": 1,
                "quantity_for_trade": 1,
            },
        )
        app.upsert_collection_item(
            trader_id,
            {
                "game": "mtg",
                "card_name": "Counterspell",
                "quantity": 1,
                "quantity_for_trade": 1,
            },
        )
        app.upsert_collection_item(
            trader_id,
            {
                "game": "mtg",
                "card_name": "Lightning Bolt",
                "quantity": 1,
                "quantity_for_trade": 1,
            },
        )

        wanter_notifications = app.rows("SELECT * FROM user_notifications WHERE user_id = ?", (wanter_id,))
        disabled_notifications = app.rows("SELECT * FROM user_notifications WHERE user_id = ?", (disabled_id,))

        self.assertEqual(len(wanter_notifications), 1)
        self.assertEqual(wanter_notifications[0]["kind"], "watchlist_alert")
        self.assertEqual(disabled_notifications, [])

    def test_watchlist_alert_created_by_bulk_trade_quantity_update(self):
        wanter_id = app.create_user("wanter", "password123", "Wanter")
        trader_id = app.create_user("trader", "password123", "Trader")
        app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, desired_quantity, created_at, updated_at)
            VALUES (?, 'mtg', 'Rhystic Study', 1, ?, ?)
            """,
            (wanter_id, app.now_iso(), app.now_iso()),
        )
        card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Rhystic Study', 2, 0, ?, ?)
            """,
            (trader_id, app.now_iso(), app.now_iso()),
        )

        updated = app.update_collection_items_by_ids(trader_id, [card_id], quantity_for_trade=1)
        app.update_collection_items_by_ids(trader_id, [card_id], quantity_for_trade=2)

        notifications = app.rows("SELECT * FROM user_notifications WHERE user_id = ?", (wanter_id,))

        self.assertEqual(updated, 1)
        self.assertEqual(len(notifications), 1)
        self.assertIn("Rhystic Study", notifications[0]["title"])

    def test_watchlist_alert_honors_want_preferences(self):
        wanter_id = app.create_user("wanter", "password123", "Wanter")
        trader_id = app.create_user("trader", "password123", "Trader")
        app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, desired_quantity, condition, finish, language, created_at, updated_at)
            VALUES (?, 'mtg', 'Lightning Bolt', 1, 'NM,LP', 'Foil', 'English,Japanese', ?, ?)
            """,
            (wanter_id, app.now_iso(), app.now_iso()),
        )

        app.upsert_collection_item(
            trader_id,
            {
                "game": "mtg",
                "card_name": "Lightning Bolt",
                "quantity": 1,
                "quantity_for_trade": 1,
                "condition": "LP",
                "finish": "Regular",
                "language": "Japanese",
            },
            merge=False,
        )
        app.upsert_collection_item(
            trader_id,
            {
                "game": "mtg",
                "card_name": "Lightning Bolt",
                "quantity": 1,
                "quantity_for_trade": 1,
                "condition": "NM",
                "finish": "Foil",
                "language": "Japanese",
            },
            merge=False,
        )

        notifications = app.rows("SELECT * FROM user_notifications WHERE user_id = ?", (wanter_id,))

        self.assertEqual(len(notifications), 1)
        self.assertIn("Lightning Bolt", notifications[0]["title"])

    def test_private_trade_cards_do_not_trigger_watchlist_alerts(self):
        wanter_id = app.create_user("wanter", "password123", "Wanter")
        trader_id = app.create_user("trader", "password123", "Trader")
        app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, desired_quantity, created_at, updated_at)
            VALUES (?, 'mtg', 'Demonic Tutor', 1, ?, ?)
            """,
            (wanter_id, app.now_iso(), app.now_iso()),
        )

        app.upsert_collection_item(
            trader_id,
            {
                "game": "mtg",
                "card_name": "Demonic Tutor",
                "quantity": 1,
                "quantity_for_trade": 1,
                "is_public": 0,
            },
            merge=False,
        )

        notifications = app.rows("SELECT * FROM user_notifications WHERE user_id = ?", (wanter_id,))

        self.assertEqual(notifications, [])

    def test_collection_page_paginates_and_renders_bulk_controls(self):
        user_id = app.create_user("pager", "password123", "Pager")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        for index in range(12):
            app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, quantity, quantity_for_trade, created_at, updated_at)
                VALUES (?, 'mtg', ?, 1, 0, ?, ?)
                """,
                (user_id, f"Card {index:02d}", app.now_iso(), app.now_iso()),
            )

        html = app.render_collection(user, {"per_page": ["10"], "page": ["1"]})

        self.assertIn("Showing 1-10 of 12", html)
        self.assertIn('action="/collection/bulk-update"', html)
        self.assertIn('name="item_id"', html)
        self.assertIn("select-all-control", html)
        self.assertIn("Update selected", html)
        self.assertIn("Update all", html)
        self.assertIn("Delete selected", html)
        self.assertIn("Delete all", html)
        self.assertIn('name="quantity_for_trade"', html)
        self.assertIn("Visibility", html)
        self.assertIn('<option value="">No change</option>', html)
        self.assertIn('<option value="0">Private</option>', html)
        self.assertIn('list="collection-search-suggestions"', html)
        self.assertIn('<datalist id="collection-search-suggestions">', html)
        self.assertIn('value="Card 00"', html)
        self.assertIn("Advanced filters", html)
        self.assertIn('name="condition"', html)
        self.assertIn('name="finish"', html)
        self.assertIn('name="quantity_min"', html)
        self.assertIn("page=2", html)
        self.assertIn('name="sort"', html)
        self.assertIn('name="dir"', html)

    def test_mobile_card_table_markup_is_available_for_wide_lists(self):
        alice_id = factory.create_user("mobilealice", display_name="Mobile Alice")
        bob_id = factory.create_user("mobilebob", display_name="Mobile Bob")
        alice = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))
        factory.create_collection_item(alice_id, "Sol Ring", quantity=2, quantity_for_trade=1)
        factory.create_collection_item(bob_id, "Lightning Bolt", quantity=4, quantity_for_trade=2, is_public=1)
        factory.create_trade(alice_id, bob_id)
        app.log_admin_action(
            alice_id,
            "user_banned",
            target_user_id=bob_id,
            target_type="user",
            details="Mobile layout audit",
        )

        collection_html = app.render_collection(alice, {})
        browse_html = app.render_browse(alice, {})
        trades_html = app.render_trades(alice)
        admin_logs_html = app.render_admin_logs(alice, {})

        self.assertIn('<table class="responsive-card-table collection-table">', collection_html)
        self.assertIn('data-label="Card"', collection_html)
        self.assertIn('data-label="Details"', collection_html)
        self.assertIn('data-label="Actions"', collection_html)
        self.assertIn('<table class="responsive-card-table browse-table">', browse_html)
        self.assertIn('data-label="Available"', browse_html)
        self.assertIn('data-label="Trade"', browse_html)
        self.assertIn('<table class="responsive-card-table trades-table">', trades_html)
        self.assertIn('data-label="Status"', trades_html)
        self.assertIn('<table class="admin-table responsive-card-table admin-log-table">', admin_logs_html)
        self.assertIn('data-label="Target"', admin_logs_html)

    def test_collection_page_sorts_cards_by_trade_quantity_and_value(self):
        user_id = app.create_user("sorter", "password123", "Sorter")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        for name, quantity, trade_qty, price in [
            ("Low Trade", 10, 1, "10.00"),
            ("High Trade", 1, 5, "1.00"),
            ("High Value", 4, 2, "30.00"),
        ]:
            app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, quantity, quantity_for_trade, price_usd, created_at, updated_at)
                VALUES (?, 'mtg', ?, ?, ?, ?, ?, ?)
                """,
                (user_id, name, quantity, trade_qty, price, app.now_iso(), app.now_iso()),
            )

        trade_html = app.render_collection(user, {"sort": ["trade"], "dir": ["desc"], "per_page": ["10"]})
        value_html = app.render_collection(user, {"sort": ["value"], "dir": ["desc"], "per_page": ["10"]})

        trade_table = trade_html.split('<section class="panel flush">', 1)[1]
        value_table = value_html.split('<section class="panel flush">', 1)[1]
        self.assertLess(trade_table.index("High Trade"), trade_table.index("Low Trade"))
        self.assertLess(value_table.index("High Value"), value_table.index("Low Trade"))

    def test_collection_default_search_only_matches_card_name_or_type(self):
        user_id = app.create_user("searcher", "password123", "Searcher")
        samples = [
            ("Forest", "Dragon Shield", "Basic Land"),
            ("Shivan Dragon", "Core Set", "Creature - Dragon"),
            ("Goblin Guide", "Zendikar", "Creature - Goblin"),
        ]
        for name, set_name, type_line in samples:
            app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, set_name, type_line, quantity, quantity_for_trade, created_at, updated_at)
                VALUES (?, 'mtg', ?, ?, ?, 1, 0, ?, ?)
                """,
                (user_id, name, set_name, type_line, app.now_iso(), app.now_iso()),
            )

        filters = app.collection_filter_values({"q": ["dragon"]})
        where, params = app.collection_where(user_id, filters)
        default_matches = app.rows(f"SELECT card_name FROM collection_items WHERE {' AND '.join(where)} ORDER BY card_name", params)
        set_filters = app.collection_filter_values({"set_name": ["dragon"]})
        set_where, set_params = app.collection_where(user_id, set_filters)
        set_matches = app.rows(f"SELECT card_name FROM collection_items WHERE {' AND '.join(set_where)}", set_params)

        self.assertEqual([item["card_name"] for item in default_matches], ["Shivan Dragon"])
        self.assertEqual([item["card_name"] for item in set_matches], ["Forest"])

    def test_collection_advanced_filters_render_open_and_filter_rows(self):
        user_id = app.create_user("filterer", "password123", "Filterer")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        samples = [
            ("Sol Ring", "Dominaria Remastered", "DMR", "703", "Foil", "LP", "English", "rare", "W,U", 4, 2, "image.jpg"),
            ("Solitude", "Modern Horizons 2", "MH2", "32", "Regular", "NM", "English", "mythic", "W", 1, 0, ""),
        ]
        for name, set_name, set_code, collector, finish, condition, language, rarity, colors, quantity, trade_qty, image_url in samples:
            app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, set_name, set_code, collector_number, finish, condition,
                     language, rarity, color_identity, quantity, quantity_for_trade, image_url, created_at, updated_at)
                VALUES (?, 'mtg', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, name, set_name, set_code, collector, finish, condition, language, rarity, colors, quantity, trade_qty, image_url, app.now_iso(), app.now_iso()),
            )

        query = {
            "q": ["sol"],
            "set_code": ["dmr"],
            "condition": ["LP"],
            "finish": ["Foil"],
            "language": ["English"],
            "rarity": ["rare"],
            "color_identity": ["U"],
            "card_data": ["with_image"],
            "quantity_min": ["2"],
            "trade_min": ["1"],
        }
        html = app.render_collection(user, query)
        filters = app.collection_filter_values(query)
        where, params = app.collection_where(user_id, filters)
        found = app.rows(f"SELECT card_name FROM collection_items WHERE {' AND '.join(where)}", params)

        self.assertEqual([item["card_name"] for item in found], ["Sol Ring"])
        self.assertIn('<details class="advanced-filter" open>', html)
        self.assertIn("9 active", html)
        self.assertIn('value="DMR"', html)
        self.assertIn('value="with_image" selected', html)
        self.assertIn('name="trade_min" value="1"', html)
        self.assertIn('class="active-filter-bar collection-active-filters"', html)
        self.assertIn('data-filter-key="q"', html)
        self.assertIn('data-filter-key="set_code"', html)
        self.assertIn("Search: sol", html)
        self.assertIn("Set code: DMR", html)
        self.assertIn("Qty: &gt;= 2", html)
        self.assertIn("Clear filters", html)

    def test_browse_page_lists_other_trade_cards_with_filters(self):
        viewer_id = app.create_user("viewer", "password123", "Viewer")
        trader_id = app.create_user("trader", "password123", "Trader")
        other_id = app.create_user("other", "password123", "Other")
        user = app.row("SELECT * FROM users WHERE id = ?", (viewer_id,))
        for user_id, name, condition, finish, trade_qty in [
            (viewer_id, "Counterspell", "LP", "Foil", 1),
            (trader_id, "Counterspell", "LP", "Foil", 2),
            (other_id, "Counterspell", "NM", "Regular", 3),
            (trader_id, "Lightning Bolt", "LP", "Foil", 2),
        ]:
            app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, set_name, set_code, finish, condition, quantity, quantity_for_trade, created_at, updated_at)
                VALUES (?, 'mtg', ?, 'Dominaria Remastered', 'DMR', ?, ?, 4, ?, ?, ?)
                """,
                (user_id, name, finish, condition, trade_qty, app.now_iso(), app.now_iso()),
            )

        html = app.render_browse(
            user,
            {"q": ["Counter"], "quality": ["LP"], "user": [str(trader_id)], "game": ["mtg"], "finish": ["Foil"], "per_page": ["10"]},
        )

        self.assertIn("Available trade cards", html)
        self.assertIn("Counterspell", html)
        self.assertIn("Trader", html)
        self.assertIn("Propose trade", html)
        self.assertIn('name="quality"', html)
        self.assertIn('list="browse-search-suggestions"', html)
        self.assertIn('<datalist id="browse-search-suggestions">', html)
        self.assertIn("Advanced filters", html)
        self.assertIn('list="browse-set-name-suggestions"', html)
        self.assertIn('name="trade_min"', html)
        self.assertIn('class="inline-trade-form"', html)
        self.assertIn('name="recipient_id" value="2"', html)
        self.assertIn('class="mini-input trade-request-quantity"', html)
        self.assertIn('type="number" min="1" max="2"', html)
        self.assertNotIn("chooseBrowseTradeQuantity", html)
        self.assertIn("Showing 1-1 of 1", html)
        self.assertIn('name="sort"', html)
        self.assertIn('name="dir"', html)
        self.assertIn('class="active-filter-bar browse-active-filters"', html)
        self.assertIn('data-filter-key="q"', html)
        self.assertIn('data-filter-key="user"', html)
        self.assertIn("Search: Counter", html)
        self.assertIn("User: Trader (@trader)", html)
        self.assertIn("Quality: LP", html)
        self.assertIn("Finish: Foil", html)
        self.assertNotIn("Other</strong>", html)
        self.assertNotIn("<strong>Lightning Bolt</strong>", html)

    def test_browse_page_sorts_available_cards_by_value(self):
        viewer_id = app.create_user("viewer", "password123", "Viewer")
        trader_id = app.create_user("trader", "password123", "Trader")
        viewer = app.row("SELECT * FROM users WHERE id = ?", (viewer_id,))
        for name, trade_qty, price in [
            ("Cheap Card", 4, "1.00"),
            ("Pricy Card", 1, "20.00"),
        ]:
            app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, quantity, quantity_for_trade, price_usd, created_at, updated_at)
                VALUES (?, 'mtg', ?, 4, ?, ?, ?, ?)
                """,
                (trader_id, name, trade_qty, price, app.now_iso(), app.now_iso()),
            )

        html = app.render_browse(viewer, {"sort": ["value"], "dir": ["desc"], "per_page": ["10"]})

        browse_table = html.split('<section class="panel flush">', 1)[1]
        self.assertLess(browse_table.index("Pricy Card"), browse_table.index("Cheap Card"))

    def test_browse_propose_trade_quantity_preloads_requested_card(self):
        viewer_id = app.create_user("viewer", "password123", "Viewer")
        trader_id = app.create_user("trader", "password123", "Trader")
        viewer = app.row("SELECT * FROM users WHERE id = ?", (viewer_id,))
        card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Counterspell', 'Dominaria Remastered', 4, 3, ?, ?)
            """,
            (trader_id, app.now_iso(), app.now_iso()),
        )

        browse_html = app.render_browse(viewer, {})
        trade_html = app.render_new_trade(
            viewer,
            trader_id,
            {"recipient_id": [str(trader_id)], f"request_{card_id}": ["2"]},
        )

        self.assertIn(f'name="request_{card_id}" value="1"', browse_html)
        self.assertIn('type="number" min="1" max="3"', browse_html)
        self.assertIn("2 x Counterspell", trade_html)
        self.assertIn(f'name="request_{card_id}" value="2"', trade_html)

    def test_trade_picker_renders_active_filter_chips(self):
        alice_id = factory.create_user("pickeralice", display_name="Picker Alice")
        bob_id = factory.create_user("pickerbob", display_name="Picker Bob")
        alice = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))
        factory.create_collection_item(alice_id, "Sol Ring", quantity=2, quantity_for_trade=1, condition="NM")
        bob_card_id = factory.create_collection_item(
            bob_id,
            "Lightning Bolt",
            quantity=4,
            quantity_for_trade=2,
            finish="Foil",
        )

        html = app.render_new_trade(
            alice,
            bob_id,
            {
                "recipient_id": [str(bob_id)],
                "offer_q": ["Sol"],
                "offer_condition": ["NM"],
                "request_finish": ["Foil"],
                "request_trade_min": ["1"],
                f"request_{bob_card_id}": ["1"],
            },
        )

        self.assertIn('class="active-filter-bar trade-picker-active-filters"', html)
        self.assertIn('data-filter-key="offer_q"', html)
        self.assertIn('data-filter-key="offer_condition"', html)
        self.assertIn('data-filter-key="request_finish"', html)
        self.assertIn('data-filter-key="request_trade_min"', html)
        self.assertIn("Search: Sol", html)
        self.assertIn("Condition: NM", html)
        self.assertIn("Finish: Foil", html)
        self.assertIn("Available: &gt;= 1", html)
        self.assertIn(f'name="request_{bob_card_id}" value="1"', html)

    def test_trade_matchmaking_finds_mutual_overlap_and_prefills_trade(self):
        alice_id = factory.create_user("alice", display_name="Alice")
        bob_id = factory.create_user("bob", display_name="Bob")
        carol_id = factory.create_user("carol", display_name="Carol")
        alice = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))
        factory.create_want_item(alice_id, "Sol Ring", desired_quantity=2)
        factory.create_want_item(alice_id, "Mana Crypt")
        bob_sol_id = factory.create_collection_item(
            bob_id,
            "Sol Ring",
            quantity=4,
            quantity_for_trade=2,
            price_usd="1.50",
            is_public=1,
        )
        factory.create_collection_item(
            bob_id,
            "Mana Crypt",
            quantity=1,
            quantity_for_trade=1,
            price_usd="100.00",
            is_public=0,
        )
        factory.create_collection_item(
            carol_id,
            "Sol Ring",
            quantity=1,
            quantity_for_trade=1,
            price_usd="1.50",
            is_public=1,
        )
        alice_counter_id = factory.create_collection_item(
            alice_id,
            "Counterspell",
            quantity=2,
            quantity_for_trade=1,
            price_usd="2.00",
        )
        factory.create_collection_item(
            alice_id,
            "Lightning Bolt",
            quantity=2,
            quantity_for_trade=1,
            price_usd="0.25",
        )
        factory.create_want_item(bob_id, "Counterspell", is_public=1)
        factory.create_want_item(bob_id, "Lightning Bolt", is_public=0)

        matches = app.trade_matchmaking_results(alice_id)
        html = app.render_trade_matchmaking(alice, {})
        prefill_url = app.trade_matchmaking_prefill_url(matches[0])

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["member_id"], bob_id)
        self.assertEqual(matches[0]["they_have_count"], 2)
        self.assertEqual(matches[0]["they_want_count"], 1)
        self.assertEqual(matches[0]["they_have_value_cents"], 300)
        self.assertEqual(matches[0]["they_want_value_cents"], 200)
        self.assertIn("Trade matchmaking", html)
        self.assertIn("Bob", html)
        self.assertIn("Sol Ring", html)
        self.assertIn("Counterspell", html)
        self.assertIn("Start matched trade", html)
        self.assertNotIn("Carol", html)
        self.assertNotIn("Mana Crypt", html)
        self.assertNotIn("Lightning Bolt", html)
        self.assertIn(f"recipient_id={bob_id}", prefill_url)
        self.assertIn(f"request_{bob_sol_id}=2", prefill_url)
        self.assertIn(f"offer_{alice_counter_id}=1", prefill_url)

    def test_trades_page_links_to_matchmaking(self):
        user_id = app.create_user("alice", "password123", "Alice")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))

        html = app.render_trades(user)

        self.assertIn("/trades/matches", html)
        self.assertIn("Find matches", html)

    def test_private_collection_cards_are_hidden_from_other_users(self):
        viewer_id = app.create_user("viewer", "password123", "Viewer")
        trader_id = app.create_user("trader", "password123", "Trader")
        viewer = app.row("SELECT * FROM users WHERE id = ?", (viewer_id,))
        public_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, quantity, quantity_for_trade, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Public Counterspell', 3, 2, 1, ?, ?)
            """,
            (trader_id, app.now_iso(), app.now_iso()),
        )
        private_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, quantity, quantity_for_trade, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Private Black Lotus', 1, 1, 0, ?, ?)
            """,
            (trader_id, app.now_iso(), app.now_iso()),
        )

        browse_html = app.render_browse(viewer, {})
        member_html = app.render_member_detail(viewer, trader_id)
        trade_html = app.render_new_trade(
            viewer,
            trader_id,
            {
                "recipient_id": [str(trader_id)],
                f"request_{public_card_id}": ["1"],
                f"request_{private_card_id}": ["1"],
            },
        )
        public_requested = app.parse_trade_quantities({f"request_{public_card_id}": ["1"]}, "request", trader_id, viewer_id=viewer_id)
        private_requested = app.parse_trade_quantities({f"request_{private_card_id}": ["1"]}, "request", trader_id, viewer_id=viewer_id)
        owner_offer = app.parse_trade_quantities({f"offer_{private_card_id}": ["1"]}, "offer", trader_id, viewer_id=trader_id)

        self.assertIn("Public Counterspell", browse_html)
        self.assertNotIn("Private Black Lotus", browse_html)
        self.assertIn("Public Counterspell", member_html)
        self.assertNotIn("Private Black Lotus", member_html)
        self.assertIn("1 x Public Counterspell", trade_html)
        self.assertNotIn("1 x Private Black Lotus", trade_html)
        self.assertEqual(len(public_requested), 1)
        self.assertEqual(private_requested, [])
        self.assertEqual(len(owner_offer), 1)

    def test_browse_advanced_filters_render_open_and_filter_trade_cards(self):
        viewer_id = app.create_user("viewer", "password123", "Viewer")
        trader_id = app.create_user("trader", "password123", "Trader")
        other_id = app.create_user("other", "password123", "Other")
        user = app.row("SELECT * FROM users WHERE id = ?", (viewer_id,))
        samples = [
            (trader_id, "Sol Ring", "Dominaria Remastered", "DMR", "703", "Artifact", "English", "rare", "W,U", 4, 2, "image.jpg"),
            (other_id, "Solitude", "Modern Horizons 2", "MH2", "32", "Creature - Elemental Incarnation", "English", "mythic", "W", 1, 1, ""),
            (viewer_id, "Sol Ring", "Dominaria Remastered", "DMR", "703", "Artifact", "English", "rare", "W,U", 4, 4, "image.jpg"),
        ]
        for owner_id, name, set_name, set_code, collector, type_line, language, rarity, colors, quantity, trade_qty, image_url in samples:
            app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, set_name, set_code, collector_number, type_line,
                     language, rarity, color_identity, quantity, quantity_for_trade, image_url, created_at, updated_at)
                VALUES (?, 'mtg', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (owner_id, name, set_name, set_code, collector, type_line, language, rarity, colors, quantity, trade_qty, image_url, app.now_iso(), app.now_iso()),
            )

        query = {
            "q": ["sol"],
            "set_code": ["dmr"],
            "type_line": ["artifact"],
            "language": ["English"],
            "rarity": ["rare"],
            "color_identity": ["U"],
            "card_data": ["with_image"],
            "quantity_min": ["2"],
            "trade_min": ["2"],
        }
        html = app.render_browse(user, query)
        filters = app.browse_filter_values(query)
        where, params = app.browse_where(viewer_id, filters)
        found = app.rows(
            f"""
            SELECT collection_items.card_name
            FROM collection_items
            JOIN users ON users.id = collection_items.user_id
            WHERE {' AND '.join(where)}
            """,
            params,
        )

        self.assertEqual([item["card_name"] for item in found], ["Sol Ring"])
        self.assertIn('<details class="advanced-filter" open>', html)
        self.assertIn("8 active", html)
        self.assertIn('value="DMR"', html)
        self.assertIn('value="with_image" selected', html)
        self.assertIn('list="browse-type-line-suggestions"', html)

    def test_browse_page_paginates_trade_cards(self):
        viewer_id = app.create_user("viewer", "password123", "Viewer")
        trader_id = app.create_user("trader", "password123", "Trader")
        user = app.row("SELECT * FROM users WHERE id = ?", (viewer_id,))
        for index in range(12):
            app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, quantity, quantity_for_trade, created_at, updated_at)
                VALUES (?, 'mtg', ?, 1, 1, ?, ?)
                """,
                (trader_id, f"Trade Card {index:02d}", app.now_iso(), app.now_iso()),
            )

        html = app.render_browse(user, {"per_page": ["10"], "page": ["1"]})

        self.assertIn("Showing 1-10 of 12", html)
        self.assertIn("page=2", html)
        self.assertIn('action="/browse"', html)

    def test_bulk_delete_only_removes_current_users_items(self):
        alice_id = factory.create_user("alice", display_name="Alice")
        bob_id = factory.create_user("bob", display_name="Bob")
        alice_card_id = factory.create_collection_item(alice_id, "Sol Ring")
        bob_card_id = factory.create_collection_item(bob_id, "Lightning Bolt")

        deleted = app.bulk_delete_collection_items(alice_id, [alice_card_id, bob_card_id, "not-an-id"])

        alice_card = app.row("SELECT * FROM collection_items WHERE id = ?", (alice_card_id,))
        bob_card = app.row("SELECT * FROM collection_items WHERE id = ?", (bob_card_id,))

        self.assertEqual(deleted, 1)
        self.assertIsNone(alice_card)
        self.assertIsNotNone(bob_card)

    def test_bulk_update_selected_caps_trade_quantity_and_respects_user(self):
        alice_id = factory.create_user("alice", display_name="Alice")
        bob_id = factory.create_user("bob", display_name="Bob")
        alice_card_id = factory.create_collection_item(alice_id, "Sol Ring", quantity=4, quantity_for_trade=1)
        bob_card_id = factory.create_collection_item(bob_id, "Lightning Bolt", quantity=4, quantity_for_trade=1)

        updated = app.update_collection_items_by_ids(alice_id, [alice_card_id, bob_card_id], quantity=2, quantity_for_trade=5, is_public=0)
        alice_card = app.row("SELECT * FROM collection_items WHERE id = ?", (alice_card_id,))
        bob_card = app.row("SELECT * FROM collection_items WHERE id = ?", (bob_card_id,))

        self.assertEqual(updated, 1)
        self.assertEqual(alice_card["quantity"], 2)
        self.assertEqual(alice_card["quantity_for_trade"], 2)
        self.assertEqual(alice_card["is_public"], 0)
        self.assertEqual(bob_card["quantity"], 4)

    def test_update_all_matching_changes_only_filtered_collection_items(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        samples = [
            (alice_id, "Sol Ring", "mtg", 4, 0),
            (alice_id, "Solitude", "mtg", 4, 0),
            (alice_id, "Pikachu", "pokemon", 4, 0),
            (bob_id, "Sol Ring", "mtg", 4, 0),
        ]
        for user_id, name, game, qty, trade_qty in samples:
            app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, quantity, quantity_for_trade, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, game, name, qty, trade_qty, app.now_iso(), app.now_iso()),
            )

        updated = app.update_collection_items_matching(alice_id, q="sol", game="mtg", trade_only=False, quantity_for_trade=2, is_public=0)
        alice_cards = app.rows("SELECT card_name, quantity, quantity_for_trade, is_public FROM collection_items WHERE user_id = ? ORDER BY card_name", (alice_id,))
        bob_card = app.row("SELECT quantity_for_trade, is_public FROM collection_items WHERE user_id = ?", (bob_id,))

        self.assertEqual(updated, 2)
        self.assertEqual([(card["card_name"], card["quantity_for_trade"]) for card in alice_cards], [("Pikachu", 0), ("Sol Ring", 2), ("Solitude", 2)])
        self.assertEqual([(card["card_name"], card["is_public"]) for card in alice_cards], [("Pikachu", 1), ("Sol Ring", 0), ("Solitude", 0)])
        self.assertEqual(bob_card["quantity_for_trade"], 0)
        self.assertEqual(bob_card["is_public"], 1)

    def test_parse_bulk_collection_update_accepts_visibility_only(self):
        quantity, quantity_for_trade, is_public = app.parse_bulk_collection_update({
            "quantity": [""],
            "quantity_for_trade": [""],
            "is_public": ["0"],
        })

        self.assertIsNone(quantity)
        self.assertIsNone(quantity_for_trade)
        self.assertEqual(is_public, 0)

    def test_bulk_update_requires_at_least_one_value(self):
        with self.assertRaisesRegex(ValueError, "Enter a quantity"):
            app.parse_bulk_collection_update({"quantity": [""], "quantity_for_trade": [""]})

    def test_delete_all_matching_respects_filters_and_user(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        samples = [
            (alice_id, "Sol Ring", "mtg", 1),
            (alice_id, "Solitude", "mtg", 0),
            (alice_id, "Pikachu", "pokemon", 1),
            (bob_id, "Sol Ring", "mtg", 1),
        ]
        for user_id, name, game, trade_qty in samples:
            app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, quantity, quantity_for_trade, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?, ?)
                """,
                (user_id, game, name, trade_qty, app.now_iso(), app.now_iso()),
            )

        deleted = app.delete_collection_items_matching(alice_id, q="sol", game="mtg", trade_only=True)
        remaining_alice = app.rows("SELECT card_name FROM collection_items WHERE user_id = ? ORDER BY card_name", (alice_id,))
        remaining_bob = app.rows("SELECT card_name FROM collection_items WHERE user_id = ?", (bob_id,))

        self.assertEqual(deleted, 1)
        self.assertEqual([card["card_name"] for card in remaining_alice], ["Pikachu", "Solitude"])
        self.assertEqual(remaining_bob[0]["card_name"], "Sol Ring")

    def test_collection_duplicate_cleanup_merges_rows_and_preserves_references(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        timestamp = app.now_iso()
        first_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, set_code, collector_number, finish, condition,
                 language, quantity, quantity_for_trade, notes, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 'CMM', '703', 'Regular', 'NM',
                    'English', 2, 1, 'first copy', 0, ?, ?)
            """,
            (alice_id, timestamp, timestamp),
        )
        second_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, set_code, collector_number, finish, condition,
                 language, quantity, quantity_for_trade, scryfall_id, price_usd, price_source,
                 notes, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 'cmm', '703', 'Regular', 'NM',
                    'English', 3, 4, 'scryfall-sol', '1.25', 'scryfall', 'second copy', 1, ?, ?)
            """,
            (alice_id, timestamp, timestamp),
        )
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, set_code, collector_number, finish, condition,
                 language, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 'CMM', '703', 'Regular', 'NM',
                    'English', 1, 1, ?, ?)
            """,
            (bob_id, timestamp, timestamp),
        )
        group_id = app.create_card_group(alice_id, "binder", "Trade binder")
        app.add_collection_item_to_group(alice_id, group_id, first_card_id, 2)
        app.add_collection_item_to_group(alice_id, group_id, second_card_id, 3)
        trade_id = app.execute(
            """
            INSERT INTO trades (proposer_id, recipient_id, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (alice_id, bob_id, timestamp, timestamp),
        )
        app.execute(
            """
            INSERT INTO trade_items (trade_id, owner_id, collection_item_id, card_name, quantity, side)
            VALUES (?, ?, ?, 'Sol Ring', 1, 'offered')
            """,
            (trade_id, alice_id, second_card_id),
        )
        app.execute(
            """
            INSERT INTO price_history
                (collection_item_id, user_id, card_name, price_usd, observed_at)
            VALUES (?, ?, 'Sol Ring', '1.25', ?)
            """,
            (second_card_id, alice_id, timestamp),
        )
        for card_id, lookup_key in ((first_card_id, "keep"), (second_card_id, "duplicate")):
            app.execute(
                """
                INSERT INTO scryfall_enrichment_jobs
                    (collection_item_id, user_id, lookup_key, card_name, created_at, updated_at)
                VALUES (?, ?, ?, 'Sol Ring', ?, ?)
                """,
                (card_id, alice_id, lookup_key, timestamp, timestamp),
            )
            app.execute(
                """
                INSERT INTO card_price_sources
                    (collection_item_id, provider, price_usd, fetched_at)
                VALUES (?, 'scryfall', '1.25', ?)
                """,
                (card_id, timestamp),
            )
            app.execute(
                """
                INSERT INTO price_refresh_jobs
                    (collection_item_id, user_id, provider, created_at, updated_at)
                VALUES (?, ?, 'scryfall', ?, ?)
                """,
                (card_id, alice_id, timestamp, timestamp),
            )

        html = app.render_cleanup(app.row("SELECT * FROM users WHERE id = ?", (alice_id,)))
        groups = app.collection_duplicate_groups(alice_id)
        result = app.cleanup_collection_duplicates(alice_id, [groups[0]["key"]])

        cards = app.rows("SELECT * FROM collection_items WHERE user_id = ? AND card_name = 'Sol Ring' ORDER BY id", (alice_id,))
        group_rows = app.rows("SELECT * FROM group_collection_items WHERE group_id = ?", (group_id,))
        trade_item = app.row("SELECT * FROM trade_items WHERE trade_id = ?", (trade_id,))
        history = app.row("SELECT * FROM price_history WHERE user_id = ?", (alice_id,))
        jobs = app.rows("SELECT * FROM scryfall_enrichment_jobs WHERE user_id = ?", (alice_id,))
        sources = app.rows("SELECT * FROM card_price_sources WHERE collection_item_id = ?", (first_card_id,))
        refresh_jobs = app.rows("SELECT * FROM price_refresh_jobs WHERE user_id = ?", (alice_id,))

        self.assertEqual(result, {"groups": 1, "merged": 1})
        self.assertIn("/cleanup/collection", html)
        self.assertIn("Sol Ring", html)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["id"], first_card_id)
        self.assertEqual(cards[0]["quantity"], 5)
        self.assertEqual(cards[0]["quantity_for_trade"], 5)
        self.assertEqual(cards[0]["scryfall_id"], "scryfall-sol")
        self.assertEqual(cards[0]["is_public"], 1)
        self.assertIn("first copy", cards[0]["notes"])
        self.assertIn("second copy", cards[0]["notes"])
        self.assertEqual(len(group_rows), 1)
        self.assertEqual(group_rows[0]["collection_item_id"], first_card_id)
        self.assertEqual(group_rows[0]["quantity"], 5)
        self.assertEqual(trade_item["collection_item_id"], first_card_id)
        self.assertEqual(history["collection_item_id"], first_card_id)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["collection_item_id"], first_card_id)
        self.assertEqual(len(sources), 1)
        self.assertEqual(len(refresh_jobs), 1)
        self.assertEqual(refresh_jobs[0]["collection_item_id"], first_card_id)

    def test_want_duplicate_cleanup_merges_rows_and_group_links(self):
        user_id = app.create_user("wishlist", "password123", "Wishlist")
        timestamp = app.now_iso()
        first_want_id = app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, set_name, set_code, collector_number, desired_quantity,
                 condition, finish, language, scryfall_id, notes, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Counterspell', 'Dominaria Remastered', 'DMR', '45', 1,
                    'NM,LP', 'Regular,Foil', 'English', 'scryfall-counter', 'first want', 0, ?, ?)
            """,
            (user_id, timestamp, timestamp),
        )
        second_want_id = app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, set_name, set_code, collector_number, desired_quantity,
                 condition, finish, language, scryfall_id, price_usd, notes, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Counterspell', 'Dominaria Remastered', 'DMR', '45', 2,
                    'NM,LP', 'Regular,Foil', 'English', 'scryfall-counter', '0.75', 'second want', 1, ?, ?)
            """,
            (user_id, timestamp, timestamp),
        )
        group_id = app.create_card_group(user_id, "wishlist", "Control wants")
        app.add_want_item_to_group(user_id, group_id, first_want_id)
        app.add_want_item_to_group(user_id, group_id, second_want_id)

        html = app.render_cleanup(app.row("SELECT * FROM users WHERE id = ?", (user_id,)))
        groups = app.want_duplicate_groups(user_id)
        result = app.cleanup_want_duplicates(user_id, [groups[0]["key"]])

        wants = app.rows("SELECT * FROM want_items WHERE user_id = ? ORDER BY id", (user_id,))
        group_rows = app.rows("SELECT * FROM group_want_items WHERE group_id = ?", (group_id,))

        self.assertEqual(result, {"groups": 1, "merged": 1})
        self.assertEqual(len(wants), 1)
        self.assertEqual(wants[0]["id"], first_want_id)
        self.assertEqual(wants[0]["desired_quantity"], 3)
        self.assertEqual(wants[0]["price_usd"], "0.75")
        self.assertEqual(wants[0]["is_public"], 1)
        self.assertIn("first want", wants[0]["notes"])
        self.assertIn("second want", wants[0]["notes"])
        self.assertEqual(len(group_rows), 1)
        self.assertEqual(group_rows[0]["want_item_id"], first_want_id)
        self.assertIn("Duplicate cleanup", html)
        self.assertIn("/cleanup/wants", html)

    def test_condition_finish_audit_flags_and_normalizes_import_labels(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        timestamp = app.now_iso()
        samples = [
            (alice_id, "Sol Ring", "Near Mint", "nonfoil", 1),
            (alice_id, "Counterspell", "", "", 1),
            (alice_id, "Island", "Gem Mint", "Rainbow", 0),
            (alice_id, "Clean Card", "NM", "Regular", 0),
            (bob_id, "Bob Card", "Near Mint", "nonfoil", 1),
        ]
        item_ids = {}
        for user_id, name, condition, finish, trade_qty in samples:
            item_ids[name] = app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, condition, finish, quantity, quantity_for_trade, created_at, updated_at)
                VALUES (?, 'mtg', ?, ?, ?, 1, ?, ?, ?)
                """,
                (user_id, name, condition, finish, trade_qty, timestamp, timestamp),
            )

        summary = app.condition_finish_audit_summary(alice_id)
        rows = app.collection_condition_finish_audit_rows(alice_id, {})
        normalize_rows = app.collection_condition_finish_audit_rows(
            alice_id,
            app.condition_finish_audit_filter_values({"issue": ["normalize_condition"]}),
        )
        html = app.render_condition_finish_audit(app.row("SELECT * FROM users WHERE id = ?", (alice_id,)), {})
        normalized = app.normalize_collection_condition_finish_by_ids(
            alice_id,
            [item_ids["Sol Ring"], item_ids["Island"], item_ids["Bob Card"]],
        )
        sol_ring = app.row("SELECT * FROM collection_items WHERE id = ?", (item_ids["Sol Ring"],))
        island = app.row("SELECT * FROM collection_items WHERE id = ?", (item_ids["Island"],))
        bob_card = app.row("SELECT * FROM collection_items WHERE id = ?", (item_ids["Bob Card"],))

        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["missing_condition"], 1)
        self.assertEqual(summary["missing_finish"], 1)
        self.assertEqual(summary["invalid_condition"], 1)
        self.assertEqual(summary["invalid_finish"], 1)
        self.assertEqual(summary["normalize_condition"], 1)
        self.assertEqual(summary["normalize_finish"], 1)
        self.assertEqual(summary["trade_needs_review"], 2)
        self.assertEqual([item["card_name"] for item in rows], ["Counterspell", "Island", "Sol Ring"])
        self.assertEqual([item["card_name"] for item in normalize_rows], ["Sol Ring"])
        self.assertIn("Condition &amp; finish audit", html)
        self.assertIn("/cleanup/audit/normalize", html)
        self.assertIn("Apply all matching", html)
        self.assertEqual(normalized, 1)
        self.assertEqual(sol_ring["condition"], "NM")
        self.assertEqual(sol_ring["finish"], "Regular")
        self.assertEqual(island["condition"], "Gem Mint")
        self.assertEqual(island["finish"], "Rainbow")
        self.assertEqual(bob_card["condition"], "Near Mint")

    def test_condition_finish_audit_bulk_updates_selected_and_matching_rows(self):
        alice_id = app.create_user("alice", "password123", "Alice")
        bob_id = app.create_user("bob", "password123", "Bob")
        timestamp = app.now_iso()
        first_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, condition, finish, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Missing Both', '', '', 1, 0, ?, ?)
            """,
            (alice_id, timestamp, timestamp),
        )
        second_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, condition, finish, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Missing Finish', 'NM', '', 1, 0, ?, ?)
            """,
            (alice_id, timestamp, timestamp),
        )
        clean_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, condition, finish, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Clean', 'NM', 'Regular', 1, 0, ?, ?)
            """,
            (alice_id, timestamp, timestamp),
        )
        bob_id_card = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, condition, finish, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Bob Missing', '', '', 1, 0, ?, ?)
            """,
            (bob_id, timestamp, timestamp),
        )

        selected_updated = app.update_collection_condition_finish_by_ids(alice_id, [first_id, bob_id_card], condition="LP")
        filters = app.condition_finish_audit_filter_values({"issue": ["missing_finish"]})
        matching_updated = app.update_collection_condition_finish_matching(alice_id, filters, finish="Foil")

        first = app.row("SELECT * FROM collection_items WHERE id = ?", (first_id,))
        second = app.row("SELECT * FROM collection_items WHERE id = ?", (second_id,))
        clean = app.row("SELECT * FROM collection_items WHERE id = ?", (clean_id,))
        bob = app.row("SELECT * FROM collection_items WHERE id = ?", (bob_id_card,))

        self.assertEqual(selected_updated, 1)
        self.assertEqual(matching_updated, 2)
        self.assertEqual((first["condition"], first["finish"]), ("LP", "Foil"))
        self.assertEqual((second["condition"], second["finish"]), ("NM", "Foil"))
        self.assertEqual((clean["condition"], clean["finish"]), ("NM", "Regular"))
        self.assertEqual((bob["condition"], bob["finish"]), ("", ""))

    def test_condition_finish_audit_flags_scryfall_finish_mismatches(self):
        user_id = app.create_user("finishcheck", "password123", "Finish Check")
        timestamp = app.now_iso()
        app.store_scryfall_bulk_cards([
            {
                "object": "card",
                "id": "nonfoil-only",
                "name": "Sol Ring",
                "set_name": "Commander Masters",
                "set": "cmm",
                "collector_number": "703",
                "released_at": "2023-08-04",
                "type_line": "Artifact",
                "finishes": ["nonfoil"],
            },
            {
                "object": "card",
                "id": "foil-ok",
                "name": "Counterspell",
                "set_name": "Dominaria Remastered",
                "set": "dmr",
                "collector_number": "45",
                "released_at": "2023-01-13",
                "type_line": "Instant",
                "finishes": ["nonfoil", "foil"],
            },
        ])
        for name, scryfall_id, set_code, collector_number, finish in (
            ("Sol Ring", "nonfoil-only", "CMM", "703", "Foil"),
            ("Counterspell", "foil-ok", "DMR", "45", "Foil"),
            ("Arcane Signet", "", "", "", "Foil"),
        ):
            app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, set_code, collector_number, scryfall_id,
                     condition, finish, quantity, quantity_for_trade, created_at, updated_at)
                VALUES (?, 'mtg', ?, ?, ?, ?, 'NM', ?, 1, 1, ?, ?)
                """,
                (user_id, name, set_code, collector_number, scryfall_id, finish, timestamp, timestamp),
            )

        filters = app.condition_finish_audit_filter_values({"issue": ["scryfall_finish_mismatch"]})
        rows = app.collection_condition_finish_audit_rows(user_id, filters)
        summary = app.condition_finish_audit_summary(user_id)
        html = app.render_condition_finish_audit(app.row("SELECT * FROM users WHERE id = ?", (user_id,)), {})
        stored_finishes = app.row("SELECT finishes FROM scryfall_bulk_cards WHERE scryfall_id = 'nonfoil-only'")["finishes"]

        self.assertEqual([item["card_name"] for item in rows], ["Sol Ring"])
        self.assertIn("scryfall_finish_mismatch", rows[0]["issues"])
        self.assertEqual(rows[0]["scryfall_finish_labels"], "Regular")
        self.assertEqual(summary["scryfall_finish_mismatch"], 1)
        self.assertEqual(summary["trade_needs_review"], 1)
        self.assertIn("Finish not in Scryfall printing", html)
        self.assertIn("Available finishes: Regular", html)
        self.assertEqual(stored_finishes, '["nonfoil"]')

    def test_condition_finish_audit_can_use_cached_scryfall_finishes(self):
        user_id = app.create_user("cachefinish", "password123", "Cache Finish")
        timestamp = app.now_iso()
        raw_card = {
            "object": "card",
            "id": "cache-card",
            "name": "Cached Card",
            "set_name": "Test Set",
            "set": "tst",
            "collector_number": "1",
            "type_line": "Artifact",
            "finishes": ["foil"],
        }
        card_data = app.flatten_scryfall_card(raw_card)
        app.cache_scryfall_card(app.scryfall_cache_key("Cached Card", scryfall_id="cache-card"), card_data, raw_card)
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_code, collector_number, scryfall_id,
                 condition, finish, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Cached Card', 'TST', '1', 'cache-card', 'NM', 'Etched', 1, 0, ?, ?)
            """,
            (user_id, timestamp, timestamp),
        )

        rows = app.collection_condition_finish_audit_rows(
            user_id,
            app.condition_finish_audit_filter_values({"issue": ["scryfall_finish_mismatch"]}),
        )

        self.assertEqual([item["card_name"] for item in rows], ["Cached Card"])
        self.assertEqual(rows[0]["scryfall_finish_labels"], "Foil")

    def test_condition_finish_audit_shows_available_finish_suggestions(self):
        user_id = app.create_user("suggestfinish", "password123", "Suggest Finish")
        timestamp = app.now_iso()
        app.store_scryfall_bulk_cards([
            {
                "object": "card",
                "id": "suggest-card",
                "name": "Suggestion Card",
                "set_name": "Test Set",
                "set": "tst",
                "collector_number": "12",
                "released_at": "2026-01-01",
                "type_line": "Artifact",
                "finishes": ["nonfoil", "foil"],
            }
        ])
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_code, collector_number, scryfall_id,
                 condition, finish, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Suggestion Card', 'TST', '12', 'suggest-card',
                    'NM', '', 1, 0, ?, ?)
            """,
            (user_id, timestamp, timestamp),
        )

        rows = app.collection_condition_finish_audit_rows(user_id, {})
        html = app.render_condition_finish_audit(app.row("SELECT * FROM users WHERE id = ?", (user_id,)), {})

        self.assertEqual([item["card_name"] for item in rows], ["Suggestion Card"])
        self.assertEqual(rows[0]["scryfall_finish_labels"], "Regular, Foil")
        self.assertIn("Available finishes: Regular, Foil", html)

    def test_scryfall_finish_gate_blocks_collection_and_want_without_override(self):
        user_id = app.create_user("finishgate", "password123", "Finish Gate")
        app.store_scryfall_bulk_cards([
            {
                "object": "card",
                "id": "finish-gate-card",
                "name": "Finish Gate Card",
                "set_name": "Test Set",
                "set": "tst",
                "collector_number": "77",
                "type_line": "Artifact",
                "finishes": ["nonfoil"],
            }
        ])
        collection_data = {
            "game": "mtg",
            "card_name": "Finish Gate Card",
            "set_code": "TST",
            "collector_number": "77",
            "scryfall_id": "finish-gate-card",
            "finish": "Foil",
        }
        want_data = {
            "game": "mtg",
            "card_name": "Finish Gate Card",
            "set_code": "TST",
            "collector_number": "77",
            "finish": "Foil",
            "scryfall_finish_override": "",
        }

        with self.assertRaisesRegex(ValueError, "available in Regular"):
            app.ensure_scryfall_finish_allowed(collection_data)
        override_message = app.ensure_scryfall_finish_allowed(collection_data, allow_override=True)
        with self.assertRaisesRegex(ValueError, "available in Regular"):
            app.insert_selected_want_items(user_id, want_data, ["finish-gate-card"])
        want_data["scryfall_finish_override"] = "1"
        inserted = app.insert_selected_want_items(user_id, want_data, ["finish-gate-card"])
        want = app.row("SELECT * FROM want_items WHERE user_id = ?", (user_id,))

        self.assertIn("Foil was selected", override_message)
        self.assertEqual(inserted, 1)
        self.assertEqual(want["finish"], "Foil")

    def test_collection_import_skips_scryfall_finish_mismatches_without_override(self):
        user_id = app.create_user("importfinish", "password123", "Import Finish")
        app.store_scryfall_bulk_cards([
            {
                "object": "card",
                "id": "import-finish-card",
                "name": "Import Finish Card",
                "set_name": "Test Set",
                "set": "tst",
                "collector_number": "22",
                "type_line": "Artifact",
                "finishes": ["nonfoil"],
            }
        ])
        csv_bytes = (
            "Name,Quantity,Set Code,Collector Number,Foil,Condition,Scryfall ID\n"
            "Import Finish Card,1,TST,22,Foil,NM,import-finish-card\n"
        ).encode("utf-8")

        skipped = app.import_collection_csv(user_id, csv_bytes, enrich_scryfall=True)
        skipped_rows = app.rows("SELECT * FROM collection_items WHERE user_id = ?", (user_id,))
        allowed = app.import_collection_csv(
            user_id,
            csv_bytes,
            enrich_scryfall=True,
            allow_scryfall_finish_mismatch=True,
        )
        imported = app.row("SELECT * FROM collection_items WHERE user_id = ?", (user_id,))

        self.assertEqual(skipped["inserted"], 0)
        self.assertEqual(skipped["skipped"], 1)
        self.assertEqual(skipped_rows, [])
        self.assertIn("Row skipped", skipped["warnings"][0])
        self.assertEqual(allowed["inserted"], 1)
        self.assertEqual(allowed["skipped"], 0)
        self.assertIn("Override allowed", allowed["warnings"][0])
        self.assertEqual(imported["finish"], "Foil")
        self.assertEqual(imported["scryfall_id"], "import-finish-card")

    def test_scryfall_finish_override_controls_render_on_forms(self):
        user_id = app.create_user("finishforms", "password123", "Finish Forms")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))

        collection_html = app.render_collection_form(user)
        wants_html = app.render_wants(user)
        import_html = app.render_import(user)

        self.assertIn('name="scryfall_finish_override"', collection_html)
        self.assertIn('Override Scryfall finish check', collection_html)
        self.assertIn('name="scryfall_finish_override"', wants_html)
        self.assertIn('Allow Scryfall finish mismatches', import_html)


if __name__ == "__main__":
    unittest.main()
