"""Account security, API, webhook, and account preference tests."""

from tests.base import *  # noqa: F401,F403


class AccountsIntegrationsTests(BinderBridgeTestCase):
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

    def test_update_user_profile_saves_notification_schedule_preferences(self):
        user_id = app.create_user("schedule", "password123", "Schedule")

        app.update_user_profile(
            user_id,
            "schedule",
            "Schedule",
            "schedule@example.test",
            "",
            False,
            email_digest_frequency="weekly",
            email_digest_time="14:30",
            email_digest_weekday=4,
            notification_timezone="America/Chicago",
            quiet_hours_enabled=True,
            quiet_hours_start="21:30",
            quiet_hours_end="06:30",
            stale_trade_reminder_days=5,
        )
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))

        self.assertEqual(user["email_digest_frequency"], "weekly")
        self.assertEqual(user["email_digest_time"], "14:30")
        self.assertEqual(user["email_digest_weekday"], 4)
        self.assertEqual(user["notification_timezone"], "America/Chicago")
        self.assertEqual(user["quiet_hours_enabled"], 1)
        self.assertEqual(user["quiet_hours_start"], "21:30")
        self.assertEqual(user["quiet_hours_end"], "06:30")
        self.assertEqual(user["stale_trade_reminder_days"], 5)

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
        self.assertIn('name="stale_trade_reminder_days"', html)
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
        self.assertIn('name="email_digest_frequency"', html)
        self.assertIn('name="email_digest_time"', html)
        self.assertIn('name="email_digest_weekday"', html)
        self.assertIn('name="notification_timezone"', html)
        self.assertIn('name="quiet_hours_enabled"', html)
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
