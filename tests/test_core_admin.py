"""Core, administration, roles, invitations, and backup tests."""

from tests.base import *  # noqa: F401,F403


class CoreAdminTests(BinderBridgeTestCase):
    def test_release_metadata_is_centralized(self):
        from binderbridge import APP_NAME, APP_VERSION, RELEASE_TAG, __version__

        self.assertEqual(APP_NAME, app.APP_NAME)
        self.assertEqual(APP_VERSION, app.APP_VERSION)
        self.assertEqual(__version__, app.APP_VERSION)
        self.assertEqual(RELEASE_TAG, f"v{app.APP_VERSION}")
        self.assertEqual(app.App.server_version, f"{app.APP_NAME}/{app.APP_VERSION}")

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

    def test_log_messages_escape_control_and_unencodable_characters(self):
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding="cp1252", errors="strict")

        app.write_log_message("request \x9c path \U0001f600\nnext", stream=stream)
        stream.flush()
        output = buffer.getvalue().decode("cp1252")

        self.assertEqual(output.rstrip("\r\n"), "request \\x9c path \\U0001f600\\x0anext")

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
        privacy_link_indexes = {item["name"] for item in app.rows("PRAGMA index_list(privacy_share_links)")}
        storage_snapshot_indexes = {item["name"] for item in app.rows("PRAGMA index_list(database_storage_snapshots)")}
        maintenance_run_indexes = {item["name"] for item in app.rows("PRAGMA index_list(database_maintenance_runs)")}
        password_request_indexes = {item["name"] for item in app.rows("PRAGMA index_list(password_recovery_requests)")}
        password_token_indexes = {item["name"] for item in app.rows("PRAGMA index_list(password_reset_tokens)")}
        rate_limit_indexes = {item["name"] for item in app.rows("PRAGMA index_list(rate_limit_events)")}
        saved_search_indexes = {item["name"] for item in app.rows("PRAGMA index_list(saved_searches)")}
        user_indexes = {item["name"] for item in app.rows("PRAGMA index_list(users)")}
        dispute_columns = {item["name"] for item in app.rows("PRAGMA table_info(trade_disputes)")}
        evidence_indexes = {item["name"] for item in app.rows("PRAGMA index_list(trade_dispute_evidence)")}
        triggers = {item["name"] for item in app.rows("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        migration_history = app.rows("SELECT * FROM schema_migration_history ORDER BY version")
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
        self.assertIn("idx_privacy_share_links_target", privacy_link_indexes)
        self.assertIn("idx_database_storage_snapshots_recorded", storage_snapshot_indexes)
        self.assertIn("idx_database_maintenance_runs_completed", maintenance_run_indexes)
        self.assertIn("idx_password_recovery_requests_status", password_request_indexes)
        self.assertIn("idx_password_reset_tokens_active", password_token_indexes)
        self.assertIn("idx_rate_limit_events_bucket_key_time", rate_limit_indexes)
        self.assertIn("idx_saved_searches_user_context", saved_search_indexes)
        self.assertIn("idx_users_role_status", user_indexes)
        self.assertIn("trg_collection_privacy_legacy_update", triggers)
        self.assertIn("trg_collection_privacy_visibility_update", triggers)
        self.assertIn("trg_collection_share_links_delete", triggers)
        self.assertIn("trg_want_share_links_delete", triggers)
        self.assertIn("resolution_note", dispute_columns)
        self.assertIn("idx_trade_dispute_evidence_dispute", evidence_indexes)
        self.assertEqual(len(migration_history), app.CURRENT_SCHEMA_VERSION)
        self.assertEqual(migration_history[-1]["version"], app.CURRENT_SCHEMA_VERSION)

    def test_extracted_helpers_live_in_focused_modules(self):
        self.assertEqual(app.scryfall_get.__module__, "binderbridge.scryfall_client")
        self.assertEqual(app.sync_scryfall_bulk_data.__module__, "binderbridge.scryfall_client")
        self.assertEqual(app.refresh_all_scryfall_prices.__module__, "binderbridge.scryfall_jobs")
        self.assertEqual(app.start_scryfall_enrichment_worker.__module__, "binderbridge.scryfall_jobs")
        self.assertEqual(app.normalize_csv_rows.__module__, "binderbridge.import_mapping")
        self.assertEqual(app.create_import_batch.__module__, "binderbridge.import_batches")
        self.assertEqual(app.import_collection_csv.__module__, "binderbridge.collection_imports")
        self.assertEqual(app.import_deck_group_csv.__module__, "binderbridge.deck_import_service")
        self.assertEqual(app.undo_import_batch.__module__, "binderbridge.import_batches")
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
        self.assertEqual(
            app.row(
                "SELECT COUNT(*) AS count FROM rate_limit_events WHERE bucket = ? AND rate_key = ?",
                ("unit-test", "same-key"),
            )["count"],
            2,
        )
        app.clear_rate_limits()
        self.assertEqual(app.row("SELECT COUNT(*) AS count FROM rate_limit_events")["count"], 0)

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
        self.assertEqual(first["role"], "owner")
        self.assertEqual(second["role"], "member")

    def test_role_hierarchy_and_capabilities(self):
        owner = factory.user_row("role-owner", display_name="Role Owner")
        admin = factory.user_row("role-admin", display_name="Role Admin", role="admin")
        moderator = factory.user_row("role-mod", display_name="Role Moderator", role="moderator")
        organizer = factory.user_row("role-organizer", display_name="Role Organizer", role="organizer")
        member = factory.user_row("role-member", display_name="Role Member")
        read_only = factory.user_row("role-reader", display_name="Role Reader", role="read_only")

        self.assertTrue(app.user_has_capability(owner, app.CAP_MANAGE_ROLES))
        self.assertTrue(app.user_has_capability(admin, app.CAP_MANAGE_SETTINGS))
        self.assertTrue(app.user_has_capability(moderator, app.CAP_MODERATE_DISPUTES))
        self.assertFalse(app.user_has_capability(moderator, app.CAP_MANAGE_BACKUPS))
        self.assertTrue(app.user_has_capability(organizer, app.CAP_MANAGE_INVITES))
        self.assertFalse(app.user_has_capability(organizer, app.CAP_MODERATE_USERS))
        self.assertTrue(app.user_can_write_content(member))
        self.assertFalse(app.user_can_write_content(read_only))
        self.assertFalse(app.user_can_mutate_path(read_only, "/collection/new"))
        self.assertTrue(app.user_can_mutate_path(read_only, "/account/password"))
        self.assertTrue(app.user_can_assign_role(owner, admin, app.ROLE_MODERATOR))
        self.assertFalse(app.user_can_assign_role(admin, owner, app.ROLE_MEMBER))

    def test_staff_dashboards_match_moderator_and_organizer_roles(self):
        factory.create_user("staff-owner", display_name="Staff Owner")
        moderator = factory.user_row("staff-mod", display_name="Staff Moderator", role="moderator")
        organizer = factory.user_row("staff-organizer", display_name="Staff Organizer", role="organizer")

        moderator_html = app.render_admin(moderator)
        organizer_html = app.render_admin(organizer)
        moderator_layout = app.render_layout(moderator, "Test", "")

        self.assertIn("Moderator control panel", moderator_html)
        self.assertIn("Trade issue queue", moderator_html)
        self.assertIn("Activity log", moderator_html)
        self.assertNotIn("Create invite", moderator_html)
        self.assertIn("Organizer control panel", organizer_html)
        self.assertIn("Create invite", organizer_html)
        self.assertNotIn("Trade issue queue", organizer_html)
        self.assertIn('href="/admin"', moderator_layout)

    def test_admin_setup_wizard_renders_first_run_controls(self):
        owner = factory.user_row("setup-owner", display_name="Setup Owner")

        html = app.render_admin_setup_wizard(owner)

        self.assertIn("First-run setup wizard", html)
        self.assertIn('action="/admin/setup/public-url"', html)
        self.assertIn('action="/admin/setup/registration"', html)
        self.assertIn('action="/admin/setup/backup"', html)
        self.assertIn('action="/admin/setup/scryfall"', html)
        self.assertIn('name="redirect_to" value="/admin/setup"', html)
        self.assertIn("Recommended defaults for small local groups", html)
        self.assertIn("Recommended for small local groups", html)
        self.assertIn("Configuration reference", html)
        self.assertIn("Deployment first-run checklist", html)
        self.assertIn("Public URL guidance", html)
        self.assertIn("Open setup wizard", app.render_admin(owner))

        invite_html = app.render_admin_setup_wizard(owner, invite_result={
            "email": "friend@example.com",
            "link": "https://cards.example.test/register?invite=abc123",
            "expires_at": "2026-07-01T00:00:00+00:00",
            "email_status": "Manual link created.",
        })

        self.assertIn("Manual invite link", invite_html)
        self.assertIn("Copy invite link", invite_html)
        self.assertIn('data-copy-target="#setup-invite-link"', invite_html)

    def test_admin_setup_public_url_setting_feeds_generated_links(self):
        owner = factory.user_row("setup-url-owner", display_name="Setup URL Owner")

        checklist = app.admin_onboarding_checklist()
        public_item = next(item for item in checklist["items"] if item["key"] == "public_url")
        self.assertFalse(public_item["complete"])

        saved = app.set_public_base_url_setting("https://cards.example.test/")
        fake_request = type("FakeRequest", (), {"headers": {}})()

        updated = app.admin_onboarding_checklist()
        updated_public_item = next(item for item in updated["items"] if item["key"] == "public_url")
        html = app.render_admin_setup_wizard(owner)

        self.assertEqual(saved, "https://cards.example.test")
        self.assertEqual(app.public_base_url(fake_request), "https://cards.example.test")
        self.assertTrue(updated_public_item["complete"])
        self.assertIn("https://cards.example.test", html)
        with self.assertRaisesRegex(ValueError, "http:// or https://"):
            app.set_public_base_url_setting("cards.example.test")

    def test_admin_setup_summary_tracks_registration_backup_and_complete_marker(self):
        owner_id = app.create_user("setup-summary-owner", "password123", "Setup Summary Owner")

        app.set_invite_only_registration(True)
        app.set_registration_moderation_settings("suspicious", "25")
        app.create_backup_archive(owner_id)
        completed_at = app.mark_admin_setup_complete()
        summary = app.admin_setup_summary()
        owner = app.row("SELECT * FROM users WHERE id = ?", (owner_id,))
        admin_html = app.render_admin(owner)
        registration_item = next(item for item in summary["checklist"]["items"] if item["key"] == "registration")
        backup_item = next(item for item in summary["checklist"]["items"] if item["key"] == "backup")

        self.assertTrue(summary["registration_invite_only"])
        self.assertEqual(summary["registration_moderation"]["approval_mode"], "suspicious")
        self.assertEqual(summary["registration_moderation"]["risk_threshold"], 25)
        self.assertEqual(summary["completed_at"], completed_at)
        self.assertIn("First-run setup complete", admin_html)
        self.assertIn("Review setup wizard", admin_html)
        self.assertIn("Invite-only", registration_item["detail"])
        self.assertTrue(backup_item["complete"])

    def test_moderator_can_manage_members_but_not_higher_roles(self):
        owner_id = factory.create_user("mod-owner", display_name="Mod Owner")
        moderator_id = factory.create_user("mod-user", display_name="Mod User", role="moderator")
        member_id = factory.create_user("mod-member", display_name="Mod Member")

        app.admin_set_user_ban(moderator_id, member_id, True, "community rules")
        self.assertEqual(app.row("SELECT is_banned FROM users WHERE id = ?", (member_id,))["is_banned"], 1)
        with self.assertRaisesRegex(ValueError, "cannot manage"):
            app.admin_set_user_ban(moderator_id, owner_id, True, "no")

    def test_moderator_receives_and_can_resolve_trade_disputes(self):
        factory.create_user("dispute-owner", display_name="Dispute Owner")
        moderator_id = factory.create_user("dispute-mod", display_name="Dispute Moderator", role="moderator")
        alice_id = factory.create_user("dispute-alice", display_name="Dispute Alice")
        bob_id = factory.create_user("dispute-bob", display_name="Dispute Bob")
        trade_id = factory.create_trade(alice_id, bob_id, status="completed")

        dispute_id = app.create_trade_dispute(trade_id, alice_id, "condition", "Card condition differs.")
        app.update_trade_dispute_admin(dispute_id, moderator_id, "resolved", "Reviewed by moderator.", resolution_note="Issue resolved locally.")

        notification = app.row(
            "SELECT * FROM user_notifications WHERE user_id = ? AND kind = 'trade_dispute'",
            (moderator_id,),
        )
        dispute = app.row("SELECT * FROM trade_disputes WHERE id = ?", (dispute_id,))
        self.assertIsNotNone(notification)
        self.assertEqual(dispute["status"], "resolved")
        self.assertEqual(dispute["resolved_by_user_id"], moderator_id)

    def test_read_only_role_is_preserved_for_api_authentication(self):
        factory.create_user("api-owner", display_name="API Owner")
        reader_id = factory.create_user("api-reader", display_name="API Reader", role="read_only")
        app.set_integration_access_settings("all", "all")
        token_result = app.create_api_token(reader_id, "Read token", ["write"])
        token = token_result["token"]

        authenticated, _token_row = app.get_user_by_api_token(token)

        self.assertEqual(app.user_role(authenticated), app.ROLE_READ_ONLY)
        self.assertFalse(app.user_can_write_content(authenticated))
        self.assertEqual(token_result["scopes"], ["read"])
        self.assertFalse(app.user_can_use_webhooks(authenticated))

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
            active_html = app.render_admin(admin, active_section="admin-access")
        finally:
            app.smtp_invites_configured = original_invites_configured
            app.email_delivery_configured = original_email_configured

        self.assertIn("Admin control panel", html)
        self.assertIn('data-workspace-tabs', html)
        self.assertIn('workspace-side-nav', html)
        self.assertIn('aria-label="Admin control panel"', html)
        self.assertIn('id="admin-overview"', html)
        self.assertIn('id="admin-policies"', html)
        self.assertIn('id="admin-access"', html)
        self.assertIn('data-active-section="admin-access"', active_html)
        self.assertIn('id="admin-operations"', html)
        self.assertIn('id="admin-users"', html)
        self.assertIn('<table class="admin-table responsive-card-table">', html)
        self.assertIn('data-label="Controls"', html)
        self.assertIn("Onboarding checklist", html)
        self.assertNotIn("First-run setup complete", html)
        self.assertIn("1 of 7 complete", html)
        self.assertIn("Set public site URL", html)
        self.assertIn("Choose registration policy", html)
        self.assertIn("Open setup wizard", html)
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
        self.assertIn("Issue reset link", html)
        self.assertIn('name="current_password"', html)
        self.assertNotIn('name="new_password"', html)
        self.assertIn("Ban", html)
        self.assertIn("Change role", html)
        self.assertIn(">Moderator</option>", html)
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
            app.set_public_base_url_setting("https://cards.example.test")
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
        self.assertEqual(checklist["complete_count"], 7)
        self.assertIn("7 of 7 complete", html)
        self.assertIn("Complete", html)
        self.assertIn("Links use https://cards.example.test.", html)
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
        self.assertIn("Data retention", html)
        self.assertIn('/admin/health/retention', html)
        self.assertIn('name="notification_days"', html)
        self.assertIn('name="admin_log_days"', html)
        self.assertIn('name="webhook_days"', html)
        self.assertIn('name="evidence_days"', html)
        self.assertIn('name="api_token_days"', html)
        self.assertIn('name="invite_days"', html)
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

    def test_admin_database_maintenance_tracks_storage_indexes_and_migrations(self):
        admin = factory.user_row("databaseadmin", display_name="Database Admin", is_admin=True)
        factory.create_collection_item(admin["id"], "Database Test Card", quantity=2, quantity_for_trade=1)

        first_snapshot = app.record_database_storage_snapshot(force=True, source="test")
        reused_snapshot = app.record_database_storage_snapshot(force=False, source="test")
        analyze = app.run_database_maintenance("analyze")
        vacuum = app.run_database_maintenance("vacuum")
        dashboard = app.database_maintenance_dashboard()
        html = app.render_admin_database(admin)

        self.assertEqual(first_snapshot["id"], reused_snapshot["id"])
        self.assertEqual(analyze["status"], "completed")
        self.assertEqual(vacuum["status"], "completed")
        self.assertGreaterEqual(len(dashboard["storage_history"]), 3)
        self.assertGreaterEqual(len(dashboard["runs"]), 2)
        self.assertGreater(dashboard["indexes"]["summary"]["total"], 0)
        self.assertGreater(dashboard["indexes"]["summary"]["with_stats"], 0)
        self.assertTrue(dashboard["indexes"]["sample_plans"])
        self.assertEqual(dashboard["migrations"]["current_version"], app.CURRENT_SCHEMA_VERSION)
        self.assertEqual(len(dashboard["migrations"]["migrations"]), app.CURRENT_SCHEMA_VERSION)
        self.assertTrue(hasattr(app.App, "admin_database_page"))
        self.assertTrue(hasattr(app.App, "admin_database_analyze"))
        self.assertTrue(hasattr(app.App, "admin_database_vacuum"))
        self.assertIn("Database maintenance", html)
        self.assertIn("Storage growth", html)
        self.assertIn("Index visibility", html)
        self.assertIn("Migration history", html)
        self.assertIn('<table class="admin-table responsive-card-table database-index-table">', html)
        self.assertIn('data-label="Sample planner use"', html)
        self.assertIn("/admin/database/analyze", html)
        self.assertIn("/admin/database/vacuum", html)
        self.assertIn("/admin/database/snapshot", html)
        self.assertIn("SQLite does not expose cumulative index-use counters", html)
        self.assertIn("/admin/database", app.render_admin_health(admin))

    def test_admin_collection_health_dashboard_tracks_quality_and_privacy(self):
        admin = factory.user_row("collectionhealthadmin", display_name="Collection Health Admin", is_admin=True)
        collector = factory.user_row("healthcollector", display_name="Health Collector")
        app.execute("UPDATE users SET collection_value_visibility = 'private' WHERE id = ?", (collector["id"],))
        current = app.now_iso()
        stale = (datetime.now(timezone.utc) - timedelta(hours=72)).replace(microsecond=0).isoformat()
        complete = {
            "scryfall_id": "known-card",
            "scryfall_uri": "https://scryfall.com/card/test/1",
            "type_line": "Artifact",
            "price_usd": "1.00",
            "price_source": "scryfall",
            "price_refreshed_at": current,
        }
        factory.create_collection_item(collector["id"], "Duplicate Card")
        factory.create_collection_item(collector["id"], "Duplicate Card")
        factory.create_collection_item(collector["id"], "Missing Card")
        factory.create_collection_item(collector["id"], "Invalid Finish", finish="Glitter", visibility="private", **complete)
        factory.create_collection_item(
            collector["id"],
            "Stale Price",
            visibility="trusted",
            price_refreshed_at=stale,
            **{key: value for key, value in complete.items() if key != "price_refreshed_at"},
        )
        factory.create_collection_item(collector["id"], "Healthy Card", visibility="link", **complete)

        dashboard = app.collection_health_dashboard()
        summary = dashboard["summary"]
        collector_health = next(item for item in dashboard["users"] if item["user_id"] == collector["id"])
        html = app.render_admin_collection_health(admin)

        self.assertEqual(summary["total_cards"], 6)
        self.assertEqual(summary["healthy_cards"], 1)
        self.assertEqual(summary["affected_cards"], 5)
        self.assertEqual(summary["duplicate_rows"], 1)
        self.assertEqual(summary["missing_scryfall"], 3)
        self.assertEqual(summary["invalid_finishes"], 1)
        self.assertEqual(summary["stale_prices"], 1)
        self.assertEqual(summary["visibility"], {"members": 3, "trusted": 1, "link": 1, "private": 1})
        self.assertEqual(summary["value_visibility"]["private"], 1)
        self.assertEqual(collector_health["health_percent"], 16)
        self.assertEqual(collector_health["severity"], "error")
        self.assertTrue(hasattr(app.App, "admin_collection_health_page"))
        self.assertIn("Collection health", html)
        self.assertIn("Public/private coverage", html)
        self.assertIn("Health Collector", html)
        self.assertIn("Missing Scryfall: 3", html)
        self.assertIn("/admin/collection-health", app.render_admin(admin))

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
        self.assertIn("Durable background runner", html)
        self.assertIn("runner jobs needing attention", html)
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

        with self.assertRaisesRegex(ValueError, "Pending invites"):
            app.delete_registration_invite(admin_id, invite["id"])
        app.revoke_registration_invite(admin_id, invite["id"])
        revoked = app.row("SELECT * FROM registration_invites WHERE id = ?", (invite["id"],))
        html = app.render_admin(app.row("SELECT * FROM users WHERE id = ?", (admin_id,)))
        app.delete_registration_invite(admin_id, invite["id"])

        self.assertEqual(revoked["status"], "revoked")
        self.assertIn(f'action="/admin/invites/{invite["id"]}/delete#admin-access"', html)
        self.assertIsNone(app.row("SELECT * FROM registration_invites WHERE id = ?", (invite["id"],)))
        self.assertIsNone(app.registration_invite_from_token(invite["token"]))

    def test_admin_invite_revoke_form_is_not_nested_in_create_form(self):
        admin_id = app.create_user("admin", "password123", "Admin")
        invite = app.create_registration_invite(admin_id, "nested@example.com", "http://binder.test")
        admin = app.row("SELECT * FROM users WHERE id = ?", (admin_id,))

        html = app.render_admin(admin)
        create_form = 'action="/admin/invites#admin-access"'
        revoke_form = f'action="/admin/invites/{invite["id"]}/revoke#admin-access"'
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
        self.assertEqual(metadata["version"], app.APP_VERSION)
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

    def test_admin_password_recovery_issues_link_without_learning_password(self):
        admin_id = app.create_user("admin", "password123", "Admin")
        target_id = app.create_user("target", "password123", "Target")
        token, _ = app.create_session(target_id)
        original_configured = app.email_delivery_configured
        app.email_delivery_configured = lambda: False
        try:
            result = app.admin_issue_user_password_recovery(admin_id, target_id, "password123", "https://cards.example.test")
        finally:
            app.email_delivery_configured = original_configured
        target = app.row("SELECT * FROM users WHERE id = ?", (target_id,))
        stored = app.row("SELECT * FROM password_reset_tokens WHERE user_id = ?", (target_id,))

        self.assertTrue(app.verify_password("password123", target["password_hash"]))
        self.assertIsNone(app.get_user_by_session(token))
        self.assertFalse(result["sent"])
        self.assertIn("/password/reset?token=", result["link"])
        self.assertNotEqual(stored["token_hash"], result["token"])
        with self.assertRaisesRegex(ValueError, "current password"):
            app.admin_issue_user_password_recovery(admin_id, target_id, "wrong")

    def test_admin_actions_are_written_to_audit_log(self):
        admin_id = app.create_user("admin", "password123", "Admin")
        target_id = app.create_user("target", "password123", "Target")

        original_configured = app.email_delivery_configured
        app.email_delivery_configured = lambda: False
        try:
            app.admin_issue_user_password_recovery(admin_id, target_id, "password123")
        finally:
            app.email_delivery_configured = original_configured
        app.admin_set_user_ban(admin_id, target_id, True, "spam")
        app.admin_set_user_role(admin_id, target_id, True)
        app.admin_update_notes(target_id, "Private note", admin_id)
        app.admin_set_user_trust(target_id, "trust", admin_id)

        logs = app.rows("SELECT * FROM admin_audit_log ORDER BY id")
        actions = [item["action"] for item in logs]

        self.assertEqual(
            actions,
            ["password_recovery_issued", "user_banned", "admin_granted", "admin_notes_updated", "trust_granted"],
        )
        self.assertIn("spam", logs[1]["details"])
        self.assertNotIn("password123", " ".join(item["details"] for item in logs))
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

    def test_owner_can_assign_roles_and_admin_cannot_manage_owner(self):
        owner_id = app.create_user("admin", "password123", "Admin")
        target_id = app.create_user("target", "password123", "Target")

        app.admin_set_user_role(owner_id, target_id, "admin")
        target = app.row("SELECT * FROM users WHERE id = ?", (target_id,))

        self.assertEqual(target["role"], "admin")
        self.assertEqual(target["is_admin"], 1)
        with self.assertRaisesRegex(ValueError, "cannot assign"):
            app.admin_set_user_role(target_id, owner_id, "member")
        with self.assertRaisesRegex(ValueError, "valid user role"):
            app.admin_set_user_role(owner_id, target_id, "super-admin")

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
        self.assertNotIn('name="evidence_retention_days"', html)
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
