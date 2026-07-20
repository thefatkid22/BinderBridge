"""Account security, API, webhook, and account preference tests."""

import json
from http import HTTPStatus

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

    def test_password_recovery_without_smtp_creates_one_admin_request(self):
        admin_id = app.create_user("owner", "password123", "Owner")
        user_id = app.create_user("recoverme", "password123", "Recover Me")
        original_configured = app.email_delivery_configured
        app.email_delivery_configured = lambda: False
        try:
            first = app.request_password_recovery("recoverme", "https://cards.example.test")
            second = app.request_password_recovery("recoverme", "https://cards.example.test")
            missing = app.request_password_recovery("missing", "https://cards.example.test")
            recovery_html = app.render_password_recovery()
        finally:
            app.email_delivery_configured = original_configured

        requests = app.rows("SELECT * FROM password_recovery_requests WHERE user_id = ?", (user_id,))
        notifications = app.rows(
            "SELECT * FROM user_notifications WHERE user_id = ? AND title = 'Password recovery assistance requested'",
            (admin_id,),
        )

        self.assertEqual(first["delivery"], "admin")
        self.assertEqual(second["delivery"], "admin")
        self.assertFalse(missing["matched"])
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["status"], "pending")
        self.assertEqual(len(notifications), 1)
        self.assertIn("administrator will be notified", recovery_html)
        self.assertIn("Forgot your password?", app.render_login())

    def test_emailed_password_reset_is_hashed_single_use_and_preserves_two_factor(self):
        user_id = app.create_user("emailreset", "password123", "Email Reset", email="reset@example.test")
        setup = app.start_user_totp_setup(user_id)
        app.enable_user_totp(user_id, app.totp_code(setup["secret"]))
        session_token, _ = app.create_session(user_id)
        android_session = app.create_api_token(
            user_id,
            "BinderBridge Android - Reset phone",
            ["read", "write"],
            credential_kind="android_session",
        )
        manual_token = app.create_api_token(user_id, "Backup export", ["read"])
        sent = []
        original_configured = app.email_delivery_configured
        original_sender = app.send_email_message
        app.email_delivery_configured = lambda: True
        app.send_email_message = lambda to_email, subject, body: (sent.append((to_email, subject, body)) or True, "Email sent.")
        try:
            result = app.request_password_recovery("reset@example.test", "https://cards.example.test")
        finally:
            app.email_delivery_configured = original_configured
            app.send_email_message = original_sender
        raw_token = sent[0][2].split("token=", 1)[1].split()[0]
        stored = app.row("SELECT * FROM password_reset_tokens WHERE user_id = ?", (user_id,))

        app.complete_password_reset(raw_token, "newpassword123", "newpassword123")
        updated = app.row("SELECT * FROM users WHERE id = ?", (user_id,))

        self.assertEqual(result["delivery"], "email")
        self.assertTrue(result["sent"])
        self.assertEqual(sent[0][0], "reset@example.test")
        self.assertNotEqual(stored["token_hash"], raw_token)
        self.assertNotIn(raw_token, stored["token_hash"])
        self.assertTrue(app.verify_password("newpassword123", updated["password_hash"]))
        self.assertTrue(app.two_factor_enabled(updated))
        self.assertIsNone(app.get_user_by_session(session_token))
        self.assertIsNone(app.get_user_by_api_token(android_session["token"])[0])
        self.assertIsNotNone(app.get_user_by_api_token(manual_token["token"])[0])
        self.assertIsNone(app.password_reset_from_token(raw_token))
        with self.assertRaisesRegex(ValueError, "invalid, expired, or already used"):
            app.complete_password_reset(raw_token, "anotherpassword", "anotherpassword")

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
        self.assertIn('action="/account/password#account-security"', html)
        self.assertIn('data-workspace-tabs', html)
        self.assertIn('workspace-side-nav', html)
        self.assertIn('aria-label="Account settings"', html)
        self.assertIn('id="account-profile"', html)
        self.assertIn('id="account-notifications"', html)
        self.assertIn('id="account-security"', html)
        self.assertIn('id="account-integrations"', html)
        self.assertIn('id="account-data"', html)
        self.assertIn('href="/account/export"', html)
        self.assertIn("Download account data", html)
        self.assertIn("API access", html)
        self.assertIn('action="/account/api-tokens#account-integrations"', html)
        self.assertIn('action="/account/webhooks#account-integrations"', html)
        self.assertIn("empty-action-state", html)
        self.assertIn("No API tokens yet.", html)
        self.assertIn("No webhooks configured yet.", html)
        self.assertIn("No passkeys registered yet.", html)
        self.assertIn('submitter.hasAttribute("formaction")', html)
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
        revoked_html = app.render_account(app.row("SELECT * FROM users WHERE id = ?", (user_id,)))
        deleted = app.delete_revoked_api_token(user_id, created["id"])
        revoked_user, revoked_token = app.get_user_by_api_token(created["token"])

        self.assertTrue(created["token"].startswith(app.API_TOKEN_PREFIX))
        self.assertEqual(found_user["username"], "apiuser")
        self.assertEqual(token_row["scopes"], "read,write")
        self.assertIn(f'action="/account/api-tokens/{created["id"]}/delete#account-integrations"', revoked_html)
        self.assertEqual(deleted, 1)
        self.assertIsNone(app.row("SELECT * FROM api_tokens WHERE id = ?", (created["id"],)))
        self.assertIsNone(revoked_user)
        self.assertIsNone(revoked_token)

    def test_android_password_login_issues_expiring_token_and_supports_two_factor(self):
        app.create_user("android-owner", "password123", "Android Owner")
        user_id = app.create_user("android-user", "password123", "Android User")

        class DummyApiRequest:
            command = "POST"

            def __init__(self, path, payload=None):
                self._request_path = path
                self.payload = payload or {}
                self.headers = {"User-Agent": "BinderBridgeAndroid/test"}
                self.response = None

            def __getattr__(self, name):
                if name.startswith("api_"):
                    return lambda *args, **kwargs: getattr(app, name)(self, *args, **kwargs)
                raise AttributeError(name)

            def api_read_json(self):
                return self.payload

            def api_json(self, payload, status=HTTPStatus.OK):
                self.response = (payload, status)

            def api_error(self, message, status=HTTPStatus.BAD_REQUEST):
                self.response = ({"error": message}, status)

            def client_ip(self):
                return "198.51.100.15"

        invalid = DummyApiRequest("/api/v1/auth/login", {
            "username": "android-user",
            "password": "wrong-password",
            "device_name": "Test phone",
        })
        app.api_dispatch(invalid, "POST", invalid._request_path, {})
        self.assertEqual(invalid.response[1], HTTPStatus.UNAUTHORIZED)
        self.assertEqual(invalid.response[0]["error"], "That username and password did not match.")

        login = DummyApiRequest("/api/v1/auth/login", {
            "username": "android-user",
            "password": "password123",
            "device_name": "Test phone",
        })
        app.api_dispatch(login, "POST", login._request_path, {})
        self.assertEqual(login.response[1], HTTPStatus.OK)
        login_data = login.response[0]["data"]
        self.assertTrue(login_data["access_token"].startswith(app.API_TOKEN_PREFIX))
        self.assertEqual(login_data["scopes"], ["read", "write", "account"])
        self.assertEqual(login_data["user"]["id"], user_id)
        self.assertGreater(login_data["expires_at"], app.now_iso())
        authenticated, token_row = app.get_user_by_api_token(login_data["access_token"])
        self.assertEqual(authenticated["id"], user_id)
        self.assertEqual(token_row["name"], "BinderBridge Android - Test phone")
        self.assertEqual(token_row["credential_kind"], "android_session")
        self.assertNotIn("password123", json.dumps(login.response[0]))

        two_factor_id = app.create_user("android-2fa", "password123", "Android 2FA")
        setup = app.start_user_totp_setup(two_factor_id)
        app.enable_user_totp(two_factor_id, app.totp_code(setup["secret"]))
        challenge = DummyApiRequest("/api/v1/auth/login", {
            "username": "android-2fa",
            "password": "password123",
            "device_name": "2FA phone",
        })
        app.api_dispatch(challenge, "POST", challenge._request_path, {})
        self.assertEqual(challenge.response[1], HTTPStatus.ACCEPTED)
        challenge_data = challenge.response[0]["data"]
        self.assertTrue(challenge_data["requires_two_factor"])

        verify = DummyApiRequest("/api/v1/auth/login/2fa", {
            "challenge_token": challenge_data["challenge_token"],
            "two_factor_code": app.totp_code(setup["secret"]),
            "device_name": "2FA phone",
        })
        app.api_dispatch(verify, "POST", verify._request_path, {})
        self.assertEqual(verify.response[1], HTTPStatus.OK)
        self.assertTrue(verify.response[0]["data"]["access_token"].startswith(app.API_TOKEN_PREFIX))
        self.assertEqual(verify.response[0]["data"]["user"]["id"], two_factor_id)

    def test_android_account_summary_lists_and_revokes_only_app_sessions(self):
        app.create_user("session-owner", "password123", "Session Owner")
        user_id = app.create_user("session-user", "password123", "Session User")

        class DummyApiRequest:
            def __init__(self, method, path, payload=None, token=""):
                self.command = method
                self._request_path = path
                self.payload = payload or {}
                self.headers = {"User-Agent": "BinderBridgeAndroid/test"}
                if token:
                    self.headers["Authorization"] = "Bearer " + token
                self.response = None

            def __getattr__(self, name):
                if name.startswith("api_"):
                    return lambda *args, **kwargs: getattr(app, name)(self, *args, **kwargs)
                raise AttributeError(name)

            def api_read_json(self):
                return self.payload

            def api_json(self, payload, status=HTTPStatus.OK):
                self.response = (payload, status)

            def api_error(self, message, status=HTTPStatus.BAD_REQUEST):
                self.response = ({"error": message}, status)

            def client_ip(self):
                return "198.51.100.18"

        def dispatch(method, path, payload=None, token=""):
            request = DummyApiRequest(method, path, payload, token)
            app.api_dispatch(request, method, path, {})
            return request.response

        first_login, first_status = dispatch("POST", "/api/v1/auth/login", {
            "username": "session-user",
            "password": "password123",
            "device_name": "First phone",
        })
        second_login, second_status = dispatch("POST", "/api/v1/auth/login", {
            "username": "session-user",
            "password": "password123",
            "device_name": "Tablet",
        })
        self.assertEqual(first_status, HTTPStatus.OK)
        self.assertEqual(second_status, HTTPStatus.OK)
        first_token = first_login["data"]["access_token"]
        second_token = second_login["data"]["access_token"]
        manual = app.create_api_token(user_id, "Collection export", ["read"])

        summary, summary_status = dispatch("GET", "/api/v1/account", token=first_token)
        self.assertEqual(summary_status, HTTPStatus.OK)
        self.assertEqual(summary["data"]["profile"]["username"], "session-user")
        self.assertFalse(summary["data"]["security"]["two_factor_enabled"])
        self.assertEqual(summary["data"]["security"]["passkey_count"], 0)
        self.assertEqual(summary["data"]["security"]["active_android_session_count"], 2)
        self.assertTrue(summary["data"]["current_session"]["current"])
        self.assertEqual(summary["data"]["current_session"]["device_name"], "First phone")

        sessions, sessions_status = dispatch("GET", "/api/v1/account/sessions", token=first_token)
        self.assertEqual(sessions_status, HTTPStatus.OK)
        self.assertEqual(sessions["total"], 2)
        self.assertEqual({item["device_name"] for item in sessions["data"]}, {"First phone", "Tablet"})
        self.assertEqual(sum(1 for item in sessions["data"] if item["current"]), 1)
        second_session_id = next(item["id"] for item in sessions["data"] if item["device_name"] == "Tablet")

        revoked, revoked_status = dispatch(
            "DELETE",
            f"/api/v1/account/sessions/{second_session_id}",
            token=first_token,
        )
        self.assertEqual(revoked_status, HTTPStatus.OK)
        self.assertTrue(revoked["data"]["revoked"])
        rejected = dispatch("GET", "/api/v1/me", token=second_token)
        self.assertEqual(rejected[1], HTTPStatus.UNAUTHORIZED)

        manual_summary = dispatch("GET", "/api/v1/account", token=manual["token"])
        self.assertEqual(manual_summary[1], HTTPStatus.FORBIDDEN)
        manual_logout, manual_logout_status = dispatch("POST", "/api/v1/auth/logout", token=manual["token"])
        self.assertEqual(manual_logout_status, HTTPStatus.OK)
        self.assertFalse(manual_logout["data"]["revoked"])
        self.assertEqual(manual_logout["data"]["credential_kind"], "api_token")
        self.assertIsNotNone(app.get_user_by_api_token(manual["token"])[0])

        logout, logout_status = dispatch("POST", "/api/v1/auth/logout", token=first_token)
        self.assertEqual(logout_status, HTTPStatus.OK)
        self.assertTrue(logout["data"]["revoked"])
        self.assertEqual(logout["data"]["credential_kind"], "android_session")
        self.assertEqual(dispatch("GET", "/api/v1/me", token=first_token)[1], HTTPStatus.UNAUTHORIZED)

        read_only_id = app.create_user("session-reader", "password123", "Session Reader")
        app.execute("UPDATE users SET role = 'read_only', is_admin = 0 WHERE id = ?", (read_only_id,))
        reader_login, reader_status = dispatch("POST", "/api/v1/auth/login", {
            "username": "session-reader",
            "password": "password123",
            "device_name": "Reader phone",
        })
        self.assertEqual(reader_status, HTTPStatus.OK)
        self.assertEqual(reader_login["data"]["scopes"], ["read", "account"])
        reader_token = reader_login["data"]["access_token"]
        self.assertEqual(dispatch("GET", "/api/v1/account", token=reader_token)[1], HTTPStatus.OK)
        self.assertEqual(dispatch("POST", "/api/v1/auth/logout", token=reader_token)[1], HTTPStatus.OK)

    def test_android_account_center_updates_profile_and_notification_preferences(self):
        user_id = app.create_user("account-center", "password123", "Account Center")

        class DummyApiRequest:
            def __init__(self, method, path, payload=None, token=""):
                self.command = method
                self._request_path = path
                self.payload = payload or {}
                self.headers = {"User-Agent": "BinderBridgeAndroid/test"}
                if token:
                    self.headers["Authorization"] = "Bearer " + token
                self.response = None

            def __getattr__(self, name):
                if name.startswith("api_"):
                    return lambda *args, **kwargs: getattr(app, name)(self, *args, **kwargs)
                raise AttributeError(name)

            def api_read_json(self):
                return self.payload

            def api_json(self, payload, status=HTTPStatus.OK):
                self.response = (payload, status)

            def api_error(self, message, status=HTTPStatus.BAD_REQUEST):
                self.response = ({"error": message}, status)

            def client_ip(self):
                return "198.51.100.27"

        def dispatch(method, path, payload=None, token=""):
            request = DummyApiRequest(method, path, payload, token)
            app.api_dispatch(request, method, path, {})
            return request.response

        login, login_status = dispatch("POST", "/api/v1/auth/login", {
            "username": "account-center",
            "password": "password123",
            "device_name": "Preference phone",
        })
        self.assertEqual(login_status, HTTPStatus.OK)
        token = login["data"]["access_token"]

        summary, summary_status = dispatch("GET", "/api/v1/account", token=token)
        self.assertEqual(summary_status, HTTPStatus.OK)
        self.assertTrue(summary["data"]["notification_preferences"]["in_app"]["trade_offer"])
        self.assertEqual(
            [item["value"] for item in summary["data"]["options"]["collection_value_visibility"]],
            ["members", "trusted", "private"],
        )

        rejected, rejected_status = dispatch("PATCH", "/api/v1/account/profile", {
            "current_password": "wrong-password",
            "display_name": "Should Not Save",
        }, token)
        self.assertEqual(rejected_status, HTTPStatus.UNAUTHORIZED)
        self.assertEqual(rejected["error"], "Current password is incorrect.")
        self.assertEqual(app.row("SELECT display_name FROM users WHERE id = ?", (user_id,))["display_name"], "Account Center")

        malformed_password, malformed_status = dispatch("PATCH", "/api/v1/account/profile", {
            "current_password": {"unexpected": "object"},
            "display_name": "Should Still Not Save",
        }, token)
        self.assertEqual(malformed_status, HTTPStatus.UNAUTHORIZED)
        self.assertEqual(malformed_password["error"], "Current password is incorrect.")

        profile, profile_status = dispatch("PATCH", "/api/v1/account/profile", {
            "current_password": "password123",
            "username": "account-center-updated",
            "display_name": "Native Account",
            "email": "native@example.test",
            "bio": "Updated from Android.",
            "public_email": True,
            "collection_value_visibility": "trusted",
        }, token)
        self.assertEqual(profile_status, HTTPStatus.OK)
        self.assertEqual(profile["data"]["profile"]["username"], "account-center-updated")
        self.assertEqual(profile["data"]["profile"]["display_name"], "Native Account")
        self.assertEqual(profile["data"]["profile"]["collection_value_visibility"], "trusted")
        self.assertTrue(profile["data"]["notification_preferences"]["in_app"]["trade_offer"])

        original_email_delivery_configured = app._api.email_delivery_configured
        app._api.email_delivery_configured = lambda: True
        try:
            preferences, preferences_status = dispatch("PATCH", "/api/v1/account/notifications", {
                "current_password": "password123",
                "in_app": {
                    "trade_offer": False,
                    "trade_comment": True,
                    "trade_counter": False,
                    "trade_status": True,
                    "price_alerts": False,
                    "watchlist_alerts": True,
                    "import_complete": False,
                    "admin_notice": True,
                },
                "price_alert_threshold_percent": "7.5",
                "stale_trade_reminder_days": 6,
                "email": {
                    "enabled": True,
                    "categories": {
                        "trade_offer": True,
                        "trade_comment": False,
                        "trade_counter": True,
                        "trade_status": False,
                        "price_alert": True,
                        "import_complete": True,
                        "admin_notice": False,
                    },
                    "digest_frequency": "weekly",
                    "digest_time": "14:30",
                    "digest_weekday": 4,
                    "timezone": "America/Chicago",
                    "quiet_hours": {"enabled": True, "start": "21:30", "end": "06:30"},
                },
            }, token)
        finally:
            app._api.email_delivery_configured = original_email_delivery_configured

        self.assertEqual(preferences_status, HTTPStatus.OK)
        saved = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        self.assertEqual(saved["notify_trade_offer_enabled"], 0)
        self.assertEqual(saved["notify_trade_counter_enabled"], 0)
        self.assertEqual(saved["price_alert_threshold_percent"], "7.5")
        self.assertEqual(saved["stale_trade_reminder_days"], 6)
        self.assertEqual(saved["email_trade_notifications_enabled"], 1)
        self.assertEqual(saved["email_trade_comment_enabled"], 0)
        self.assertEqual(saved["email_digest_frequency"], "weekly")
        self.assertEqual(saved["notification_timezone"], "America/Chicago")
        self.assertEqual(saved["quiet_hours_enabled"], 1)
        self.assertFalse(preferences["data"]["notification_preferences"]["in_app"]["trade_offer"])

    def test_android_account_center_changes_password_and_revokes_other_sessions(self):
        user_id = app.create_user("account-security", "password123", "Account Security")

        class DummyApiRequest:
            def __init__(self, method, path, payload=None, token=""):
                self.command = method
                self._request_path = path
                self.payload = payload or {}
                self.headers = {"User-Agent": "BinderBridgeAndroid/security-test"}
                if token:
                    self.headers["Authorization"] = "Bearer " + token
                self.response = None

            def __getattr__(self, name):
                if name.startswith("api_"):
                    return lambda *args, **kwargs: getattr(app, name)(self, *args, **kwargs)
                raise AttributeError(name)

            def api_read_json(self):
                return self.payload

            def api_json(self, payload, status=HTTPStatus.OK):
                self.response = (payload, status)

            def api_error(self, message, status=HTTPStatus.BAD_REQUEST):
                self.response = ({"error": message}, status)

            def client_ip(self):
                return "198.51.100.31"

        def dispatch(method, path, payload=None, token=""):
            request = DummyApiRequest(method, path, payload, token)
            app.api_dispatch(request, method, path, {})
            return request.response

        first_login, _ = dispatch("POST", "/api/v1/auth/login", {
            "username": "account-security",
            "password": "password123",
            "device_name": "Current phone",
        })
        second_login, _ = dispatch("POST", "/api/v1/auth/login", {
            "username": "account-security",
            "password": "password123",
            "device_name": "Old tablet",
        })
        current_token = first_login["data"]["access_token"]
        other_token = second_login["data"]["access_token"]
        manual_token = app.create_api_token(user_id, "Local export", ["read"])
        app.create_session(user_id)
        challenge_token, _ = app.create_two_factor_challenge(user_id)

        rejected, rejected_status = dispatch("PATCH", "/api/v1/account/password", {
            "current_password": "wrong-password",
            "new_password": "newpassword123",
            "confirm_password": "newpassword123",
        }, current_token)
        self.assertEqual(rejected_status, HTTPStatus.UNAUTHORIZED)
        self.assertEqual(rejected["error"], "Current password is incorrect.")

        same, same_status = dispatch("PATCH", "/api/v1/account/password", {
            "current_password": "password123",
            "new_password": "password123",
            "confirm_password": "password123",
        }, current_token)
        self.assertEqual(same_status, HTTPStatus.BAD_REQUEST)
        self.assertIn("different", same["error"])

        changed, changed_status = dispatch("PATCH", "/api/v1/account/password", {
            "current_password": "password123",
            "new_password": "newpassword123",
            "confirm_password": "newpassword123",
        }, current_token)
        self.assertEqual(changed_status, HTTPStatus.OK)
        self.assertEqual(changed["data"]["security"]["active_android_session_count"], 1)
        self.assertIn("Other signed-in", changed["data"]["message"])
        self.assertEqual(dispatch("GET", "/api/v1/account", token=current_token)[1], HTTPStatus.OK)
        self.assertEqual(dispatch("GET", "/api/v1/me", token=other_token)[1], HTTPStatus.UNAUTHORIZED)
        self.assertIsNotNone(app.get_user_by_api_token(manual_token["token"])[0])
        self.assertEqual(app.row("SELECT COUNT(*) AS count FROM sessions WHERE user_id = ?", (user_id,))["count"], 0)
        self.assertIsNone(app.row("SELECT * FROM two_factor_challenges WHERE token = ?", (challenge_token,)))

        old_login = dispatch("POST", "/api/v1/auth/login", {
            "username": "account-security",
            "password": "password123",
        })
        new_login = dispatch("POST", "/api/v1/auth/login", {
            "username": "account-security",
            "password": "newpassword123",
        })
        self.assertEqual(old_login[1], HTTPStatus.UNAUTHORIZED)
        self.assertEqual(new_login[1], HTTPStatus.OK)
        self.assertTrue(app.rows("SELECT * FROM admin_audit_log WHERE action = 'android_password_changed'"))

    def test_android_account_center_manages_authenticator_and_recovery_codes(self):
        user_id = app.create_user("android-security-2fa", "password123", "Android Security 2FA")

        class DummyApiRequest:
            def __init__(self, method, path, payload=None, token=""):
                self.command = method
                self._request_path = path
                self.payload = payload or {}
                self.headers = {"User-Agent": "BinderBridgeAndroid/security-test"}
                if token:
                    self.headers["Authorization"] = "Bearer " + token
                self.response = None

            def __getattr__(self, name):
                if name.startswith("api_"):
                    return lambda *args, **kwargs: getattr(app, name)(self, *args, **kwargs)
                raise AttributeError(name)

            def api_read_json(self):
                return self.payload

            def api_json(self, payload, status=HTTPStatus.OK):
                self.response = (payload, status)

            def api_error(self, message, status=HTTPStatus.BAD_REQUEST):
                self.response = ({"error": message}, status)

            def client_ip(self):
                return "198.51.100.32"

        def dispatch(method, path, payload=None, token=""):
            request = DummyApiRequest(method, path, payload, token)
            app.api_dispatch(request, method, path, {})
            return request.response

        login, _ = dispatch("POST", "/api/v1/auth/login", {
            "username": "android-security-2fa",
            "password": "password123",
            "device_name": "Security phone",
        })
        token = login["data"]["access_token"]

        summary, _ = dispatch("GET", "/api/v1/account", token=token)
        initial = summary["data"]["security"]["two_factor"]
        self.assertFalse(initial["enabled"])
        self.assertFalse(initial["setup_pending"])
        self.assertEqual(initial["recovery_code_count"], 0)

        rejected, rejected_status = dispatch("POST", "/api/v1/account/2fa/setup", {
            "current_password": "wrong-password",
        }, token)
        self.assertEqual(rejected_status, HTTPStatus.UNAUTHORIZED)
        self.assertEqual(rejected["error"], "Current password is incorrect.")

        started, started_status = dispatch("POST", "/api/v1/account/2fa/setup", {
            "current_password": "password123",
        }, token)
        self.assertEqual(started_status, HTTPStatus.OK)
        pending = started["data"]["security"]["two_factor"]
        self.assertTrue(pending["setup_pending"])
        self.assertTrue(pending["setup"]["formatted_secret"])
        self.assertTrue(pending["setup"]["otpauth_uri"].startswith("otpauth://totp/"))
        self.assertNotIn("qr_svg", pending["setup"])
        secret = pending["setup"]["formatted_secret"].replace(" ", "")

        invalid_code, invalid_status = dispatch("POST", "/api/v1/account/2fa/enable", {
            "current_password": "password123",
            "two_factor_code": "000000",
        }, token)
        self.assertEqual(invalid_status, HTTPStatus.BAD_REQUEST)
        self.assertIn("did not match", invalid_code["error"])

        enabled, enabled_status = dispatch("POST", "/api/v1/account/2fa/enable", {
            "current_password": "password123",
            "two_factor_code": app.totp_code(secret),
        }, token)
        self.assertEqual(enabled_status, HTTPStatus.OK)
        recovery_codes = enabled["data"]["recovery_codes"]
        self.assertEqual(len(recovery_codes), 10)
        enabled_state = enabled["data"]["security"]["two_factor"]
        self.assertTrue(enabled_state["enabled"])
        self.assertFalse(enabled_state["setup_pending"])
        self.assertEqual(enabled_state["recovery_code_count"], 10)
        self.assertNotIn("setup", enabled_state)
        stored_user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        self.assertNotIn(recovery_codes[0], stored_user["totp_recovery_codes"])

        regenerated, regenerated_status = dispatch("POST", "/api/v1/account/2fa/recovery-codes", {
            "current_password": "password123",
        }, token)
        self.assertEqual(regenerated_status, HTTPStatus.OK)
        new_codes = regenerated["data"]["recovery_codes"]
        self.assertEqual(len(new_codes), 10)
        self.assertNotEqual(set(recovery_codes), set(new_codes))
        self.assertEqual(app.verify_user_two_factor(user_id, new_codes[0]), (True, "recovery"))
        after_recovery, _ = dispatch("GET", "/api/v1/account", token=token)
        self.assertEqual(after_recovery["data"]["security"]["two_factor"]["recovery_code_count"], 9)
        self.assertEqual(app.verify_user_two_factor(user_id, new_codes[0]), (False, ""))

        wrong_disable, wrong_disable_status = dispatch("POST", "/api/v1/account/2fa/disable", {
            "current_password": "wrong-password",
        }, token)
        self.assertEqual(wrong_disable_status, HTTPStatus.UNAUTHORIZED)
        self.assertTrue(app.two_factor_enabled(app.row("SELECT * FROM users WHERE id = ?", (user_id,))))

        disabled, disabled_status = dispatch("POST", "/api/v1/account/2fa/disable", {
            "current_password": "password123",
        }, token)
        self.assertEqual(disabled_status, HTTPStatus.OK)
        self.assertFalse(disabled["data"]["security"]["two_factor"]["enabled"])
        self.assertFalse(app.two_factor_enabled(app.row("SELECT * FROM users WHERE id = ?", (user_id,))))

        restarted, restarted_status = dispatch("POST", "/api/v1/account/2fa/setup", {
            "current_password": "password123",
        }, token)
        self.assertEqual(restarted_status, HTTPStatus.OK)
        self.assertTrue(restarted["data"]["security"]["two_factor"]["setup_pending"])
        cancelled, cancelled_status = dispatch("POST", "/api/v1/account/2fa/disable", {
            "current_password": "password123",
        }, token)
        self.assertEqual(cancelled_status, HTTPStatus.OK)
        self.assertFalse(cancelled["data"]["security"]["two_factor"]["setup_pending"])
        actions = {item["action"] for item in app.rows("SELECT * FROM admin_audit_log WHERE target_user_id = ?", (user_id,))}
        self.assertTrue({
            "android_two_factor_setup_started",
            "android_two_factor_enabled",
            "android_recovery_codes_regenerated",
            "android_two_factor_disabled",
            "android_two_factor_setup_cancelled",
        }.issubset(actions))

    def test_api_token_post_handlers_keep_integrations_section_active(self):
        class FakeRequest:
            headers = {}
            client_address = ("127.0.0.1", 0)

            def __init__(self, form=None):
                self.form = form or {}
                self.status = None

            def read_form(self):
                return self.form

            def enforce_rate_limit(self, *args, **kwargs):
                return None

            def html(self, body, status=app.HTTPStatus.OK):
                self.status = status
                return body

        user_id = app.create_user("token-nav", "password123", "Token Nav")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))

        created_html = app.account_api_token_create(
            FakeRequest({"name": ["CLI"], "scope": ["read"], "current_password": ["password123"]}),
            user,
        )
        token = app.row("SELECT * FROM api_tokens WHERE user_id = ?", (user_id,))
        app.revoke_api_token(user_id, token["id"])
        deleted_html = app.account_api_token_delete(FakeRequest(), user, f"/account/api-tokens/{token['id']}/delete")

        self.assertIn('data-active-section="account-integrations"', created_html)
        self.assertIn("API token created.", created_html)
        self.assertIn('data-active-section="account-integrations"', deleted_html)
        self.assertIn("Revoked API token deleted.", deleted_html)

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
        self.assertNotIn('action="/account/api-tokens#account-integrations"', restricted_html)
        self.assertNotIn('action="/account/webhooks#account-integrations"', restricted_html)
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

        self.assertNotIn('action="/account/api-tokens#account-integrations"', trusted_html)
        self.assertIn('action="/account/webhooks#account-integrations"', trusted_html)
        self.assertIn('action="/account/api-tokens#account-integrations"', admin_html)
        self.assertIn('action="/account/webhooks#account-integrations"', admin_html)
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

    def test_webhook_urls_block_private_destinations_unless_explicitly_enabled(self):
        original = app.ALLOW_PRIVATE_WEBHOOKS
        try:
            app.ALLOW_PRIVATE_WEBHOOKS = False
            with self.assertRaisesRegex(ValueError, "public destination"):
                app.validate_webhook_url("http://127.0.0.1:9000/hook")
            with self.assertRaisesRegex(ValueError, "public destination"):
                app.validate_webhook_url("http://service.local/hook")
            self.assertEqual(
                app.validate_webhook_url("https://93.184.216.34/hook"),
                "https://93.184.216.34/hook",
            )

            app.ALLOW_PRIVATE_WEBHOOKS = True
            self.assertEqual(
                app.validate_webhook_url("http://127.0.0.1:9000/hook"),
                "http://127.0.0.1:9000/hook",
            )
        finally:
            app.ALLOW_PRIVATE_WEBHOOKS = original

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

    def test_api_read_and_health_rate_limits_are_enforced(self):
        user_id = app.create_user("api-limited", "password123", "API Limited")
        token = app.create_api_token(user_id, "Read token", ["read"])["token"]
        original_limits = dict(app.RATE_LIMITS)

        class DummyApiRequest:
            command = "GET"
            _request_path = "/api/v1/me"

            def __init__(self, auth_token=None):
                self.headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
                self.response = None

            def api_json(self, payload, status=HTTPStatus.OK):
                self.response = (payload, status)

            def api_error(self, message, status=HTTPStatus.BAD_REQUEST):
                self.response = ({"error": message}, status)

            def api_authenticate(self, required_scope="read"):
                return app.api_authenticate(self, required_scope)

            def client_ip(self):
                return "198.51.100.8"

        try:
            app.RATE_LIMITS["api_read"] = (1, 60)
            request = DummyApiRequest(token)
            app.api_dispatch(request, "GET", "/api/v1/me", {})
            self.assertEqual(request.response[1], HTTPStatus.OK)

            limited = DummyApiRequest(token)
            app.api_dispatch(limited, "GET", "/api/v1/me", {})
            self.assertEqual(limited.response[1], HTTPStatus.TOO_MANY_REQUESTS)
            self.assertIn("read", limited.response[0]["error"])

            app.clear_rate_limits()
            app.RATE_LIMITS["api_health"] = (1, 60)
            health = DummyApiRequest()
            app.api_dispatch(health, "GET", "/api/v1/health", {})
            self.assertEqual(health.response[1], HTTPStatus.OK)
            self.assertEqual(health.response[0]["api"]["major"], app.API_MAJOR_VERSION)
            self.assertEqual(health.response[0]["api"]["capabilities"], list(app.API_CAPABILITIES))
            self.assertIn("dashboard", health.response[0]["api"]["capabilities"])
            self.assertIn("trade.matches", health.response[0]["api"]["capabilities"])
            self.assertIn("trade.proposals", health.response[0]["api"]["capabilities"])

            health_limited = DummyApiRequest()
            app.api_dispatch(health_limited, "GET", "/api/v1/health", {})
            self.assertEqual(health_limited.response[1], HTTPStatus.TOO_MANY_REQUESTS)
            self.assertIn("health", health_limited.response[0]["error"])
        finally:
            app.RATE_LIMITS.clear()
            app.RATE_LIMITS.update(original_limits)
            app.clear_rate_limits()

    def test_api_me_includes_safe_token_metadata(self):
        user_id = app.create_user("api-me-token", "password123", "API Me Token")
        token_result = app.create_api_token(user_id, "Mobile", ["read", "write"], expires_at="2030-01-01T00:00:00")

        class DummyApiRequest:
            command = "GET"
            _request_path = "/api/v1/me"

            def __init__(self):
                self.headers = {"Authorization": f"Bearer {token_result['token']}"}
                self.response = None

            def api_json(self, payload, status=HTTPStatus.OK):
                self.response = (payload, status)

            def api_error(self, message, status=HTTPStatus.BAD_REQUEST):
                self.response = ({"error": message}, status)

            def api_authenticate(self, required_scope="read"):
                return app.api_authenticate(self, required_scope)

            def client_ip(self):
                return "198.51.100.9"

        request = DummyApiRequest()
        app.api_dispatch(request, "GET", "/api/v1/me", {})

        self.assertEqual(request.response[1], HTTPStatus.OK)
        token = request.response[0]["data"]["api_token"]
        self.assertEqual(token["name"], "Mobile")
        self.assertEqual(token["scopes"], ["read", "write"])
        self.assertEqual(token["token_hint"], token_result["token"][-8:])
        self.assertEqual(token["expires_at"], "2030-01-01T00:00:00")
        self.assertNotIn(token_result["token"], json.dumps(request.response[0]))

    def test_api_collection_create_returns_json_for_scryfall_lookup_errors(self):
        user_id = app.create_user("api-scryfall", "password123", "API Scryfall")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        original_lookup = app.lookup_scryfall_card

        class DummyApiRequest:
            def __init__(self):
                self.response = None

            def api_read_json(self):
                return {
                    "game": "mtg",
                    "card_name": "Lightning Bolt",
                    "quantity": 1,
                    "quantity_for_trade": 0,
                    "condition": "NM",
                    "finish": "Regular",
                    "language": "English",
                    "is_public": True,
                    "lookup_on_save": True,
                }

            def api_json(self, payload, status=HTTPStatus.OK):
                self.response = (payload, status)

            def api_error(self, message, status=HTTPStatus.BAD_REQUEST):
                self.response = ({"error": message}, status)

        def failing_lookup(*_args, **_kwargs):
            raise app.ScryfallError("Scryfall lookup failed: offline")

        app.lookup_scryfall_card = failing_lookup
        try:
            request = DummyApiRequest()
            app.api_collection_create(request, user)
        finally:
            app.lookup_scryfall_card = original_lookup

        self.assertEqual(request.response[1], HTTPStatus.BAD_REQUEST)
        self.assertIn("Scryfall lookup failed", request.response[0]["error"])
        self.assertEqual(app.row("SELECT COUNT(*) AS count FROM collection_items")["count"], 0)

    def test_api_card_search_returns_scryfall_matches(self):
        user_id = app.create_user("api-card-search", "password123", "API Card Search")
        token = app.create_api_token(user_id, "Read token", ["read"])["token"]
        original_search = app.search_scryfall_cards

        class DummyApiRequest:
            command = "GET"
            _request_path = "/api/v1/cards/search"

            def __init__(self):
                self.headers = {"Authorization": f"Bearer {token}"}
                self.response = None

            def api_json(self, payload, status=HTTPStatus.OK):
                self.response = (payload, status)

            def api_error(self, message, status=HTTPStatus.BAD_REQUEST):
                self.response = ({"error": message}, status)

            def api_authenticate(self, required_scope="read"):
                return app.api_authenticate(self, required_scope)

            def api_card_search(self, user, query):
                return app.api_card_search(self, user, query)

            def client_ip(self):
                return "203.0.113.9"

        def fake_search(card_name, set_code="", limit=8):
            self.assertEqual(card_name, "Sol Ring")
            self.assertEqual(set_code, "CMM")
            self.assertEqual(limit, 3)
            return [{"card_name": "Sol Ring", "set_code": "CMM", "collector_number": "703"}]

        app.search_scryfall_cards = fake_search
        try:
            request = DummyApiRequest()
            app.api_dispatch(request, "GET", "/api/v1/cards/search", {"q": ["Sol Ring"], "set_code": ["CMM"], "limit": ["3"]})
        finally:
            app.search_scryfall_cards = original_search

        self.assertEqual(request.response[1], HTTPStatus.OK)
        self.assertEqual(request.response[0]["data"][0]["card_name"], "Sol Ring")
        self.assertEqual(request.response[0]["data"][0]["collector_number"], "703")

    def test_api_want_detail_update_and_delete(self):
        user_id = app.create_user("api-wants", "password123", "API Wants")
        token = app.create_api_token(user_id, "Write token", ["read", "write"])["token"]
        want_id = factory.create_want_item(user_id, "Sol Ring", desired_quantity=1)

        class DummyApiRequest:
            command = "GET"

            def __init__(self, body=None):
                self.headers = {"Authorization": f"Bearer {token}"}
                self.response = None
                self.body = body or {}
                self._request_path = "/api/v1/wants"

            def __getattr__(self, name):
                if name.startswith("api_"):
                    return lambda *args, **kwargs: getattr(app, name)(self, *args, **kwargs)
                raise AttributeError(name)

            def api_json(self, payload, status=HTTPStatus.OK):
                self.response = (payload, status)

            def api_error(self, message, status=HTTPStatus.BAD_REQUEST):
                self.response = ({"error": message}, status)

            def api_read_json(self):
                return self.body

            def client_ip(self):
                return "203.0.113.10"

        detail = DummyApiRequest()
        app.api_dispatch(detail, "GET", f"/api/v1/wants/{want_id}", {})
        self.assertEqual(detail.response[1], HTTPStatus.OK)
        self.assertEqual(detail.response[0]["data"]["card_name"], "Sol Ring")

        update = DummyApiRequest({"card_name": "Sol Ring", "desired_quantity": 3, "priority": "high"})
        app.api_dispatch(update, "PATCH", f"/api/v1/wants/{want_id}", {})
        self.assertEqual(update.response[1], HTTPStatus.OK)
        self.assertEqual(update.response[0]["data"]["desired_quantity"], 3)
        self.assertEqual(update.response[0]["data"]["priority"], "high")

        delete = DummyApiRequest()
        app.api_dispatch(delete, "DELETE", f"/api/v1/wants/{want_id}", {})
        self.assertEqual(delete.response[1], HTTPStatus.OK)
        self.assertEqual(delete.response[0]["deleted"], 1)
        self.assertIsNone(app.row("SELECT * FROM want_items WHERE id = ?", (want_id,)))

    def test_api_want_create_can_enrich_with_scryfall(self):
        user_id = app.create_user("api-want-enrich", "password123", "API Want Enrich")
        token = app.create_api_token(user_id, "Write token", ["read", "write"])["token"]
        original_lookup = app.lookup_scryfall_card

        class DummyApiRequest:
            command = "POST"
            _request_path = "/api/v1/wants"

            def __init__(self):
                self.headers = {"Authorization": f"Bearer {token}"}
                self.response = None

            def __getattr__(self, name):
                if name.startswith("api_"):
                    return lambda *args, **kwargs: getattr(app, name)(self, *args, **kwargs)
                raise AttributeError(name)

            def api_json(self, payload, status=HTTPStatus.OK):
                self.response = (payload, status)

            def api_error(self, message, status=HTTPStatus.BAD_REQUEST):
                self.response = ({"error": message}, status)

            def api_read_json(self):
                return {
                    "game": "mtg",
                    "card_name": "Rhystic Study",
                    "desired_quantity": 1,
                    "lookup_on_save": True,
                }

            def client_ip(self):
                return "203.0.113.15"

        def fake_lookup(card_name, set_code="", collector_number="", scryfall_id=""):
            self.assertEqual(card_name, "Rhystic Study")
            return {
                "card_name": "Rhystic Study",
                "set_name": "Wilds of Eldraine: Enchanting Tales",
                "set_code": "WOT",
                "collector_number": "25",
                "scryfall_id": "study-id",
                "image_url": "https://img.example/rhystic-study.jpg",
                "type_line": "Enchantment",
                "rarity": "rare",
                "price_usd": "42.00",
            }

        app.lookup_scryfall_card = fake_lookup
        try:
            request = DummyApiRequest()
            app.api_dispatch(request, "POST", "/api/v1/wants", {})
        finally:
            app.lookup_scryfall_card = original_lookup

        self.assertEqual(request.response[1], HTTPStatus.CREATED)
        item = request.response[0]["data"]
        self.assertEqual(item["scryfall_id"], "study-id")
        self.assertEqual(item["image_url"], "https://img.example/rhystic-study.jpg")
        self.assertEqual(item["set_code"], "WOT")

    def test_api_group_crud_and_collection_items(self):
        user_id = app.create_user("api-groups", "password123", "API Groups")
        token = app.create_api_token(user_id, "Write token", ["read", "write"])["token"]
        collection_id = factory.create_collection_item(
            user_id,
            "Arcane Signet",
            set_name="Commander Legends",
            set_code="CMR",
            collector_number="312",
            quantity=4,
            quantity_for_trade=3,
            image_url="https://img.example/arcane-signet.jpg",
        )

        class DummyApiRequest:
            command = "GET"

            def __init__(self, body=None):
                self.headers = {"Authorization": f"Bearer {token}"}
                self.response = None
                self.body = body or {}
                self._request_path = "/api/v1/groups"

            def __getattr__(self, name):
                if name.startswith("api_"):
                    return lambda *args, **kwargs: getattr(app, name)(self, *args, **kwargs)
                raise AttributeError(name)

            def api_json(self, payload, status=HTTPStatus.OK):
                self.response = (payload, status)

            def api_error(self, message, status=HTTPStatus.BAD_REQUEST):
                self.response = ({"error": message}, status)

            def api_read_json(self):
                return self.body

            def client_ip(self):
                return "203.0.113.11"

        create = DummyApiRequest({"group_type": "deck", "name": "Landfall", "description": "Commander pile", "visibility": "private"})
        app.api_dispatch(create, "POST", "/api/v1/groups", {})
        self.assertEqual(create.response[1], HTTPStatus.CREATED)
        group = create.response[0]["data"]
        self.assertEqual(group["group_type"], "deck")
        self.assertEqual(group["name"], "Landfall")
        self.assertEqual(group["visibility"], "private")

        group_id = group["id"]
        listing = DummyApiRequest()
        app.api_dispatch(listing, "GET", "/api/v1/groups", {"type": ["collection"]})
        self.assertEqual(listing.response[1], HTTPStatus.OK)
        self.assertEqual(listing.response[0]["pagination"]["total"], 1)
        self.assertEqual(listing.response[0]["data"][0]["collection_entries"], 0)

        add_item = DummyApiRequest({"collection_item_id": collection_id, "quantity": 3})
        app.api_dispatch(add_item, "POST", f"/api/v1/groups/{group_id}/collection-items", {})
        self.assertEqual(add_item.response[1], HTTPStatus.CREATED)
        group_item = add_item.response[0]["data"]
        self.assertEqual(group_item["card_name"], "Arcane Signet")
        self.assertEqual(group_item["group_quantity"], 3)
        self.assertEqual(group_item["quantity"], 4)
        self.assertEqual(group_item["quantity_for_trade"], 1)
        self.assertTrue(add_item.response[0]["trade_availability"]["trade_availability_adjusted"])
        self.assertEqual(add_item.response[0]["trade_availability"]["previous_quantity_for_trade"], 3)
        self.assertEqual(add_item.response[0]["trade_availability"]["quantity_for_trade"], 1)
        collection_row = app.row("SELECT quantity, quantity_for_trade FROM collection_items WHERE id = ?", (collection_id,))
        self.assertEqual(collection_row["quantity"], 4)
        self.assertEqual(collection_row["quantity_for_trade"], 1)

        override_collection_id = factory.create_collection_item(
            user_id,
            "Chromatic Lantern",
            quantity=4,
            quantity_for_trade=3,
        )
        add_override_item = DummyApiRequest({
            "collection_item_id": override_collection_id,
            "quantity": 3,
            "adjust_trade_availability": False,
        })
        app.api_dispatch(add_override_item, "POST", f"/api/v1/groups/{group_id}/collection-items", {})
        self.assertEqual(add_override_item.response[1], HTTPStatus.CREATED)
        self.assertFalse(add_override_item.response[0]["trade_availability"]["trade_availability_adjusted"])
        override_row = app.row("SELECT quantity, quantity_for_trade FROM collection_items WHERE id = ?", (override_collection_id,))
        self.assertEqual(override_row["quantity"], 4)
        self.assertEqual(override_row["quantity_for_trade"], 3)

        detail = DummyApiRequest()
        app.api_dispatch(detail, "GET", f"/api/v1/groups/{group_id}", {"per_page": ["10"]})
        self.assertEqual(detail.response[1], HTTPStatus.OK)
        self.assertEqual(detail.response[0]["data"]["collection_quantity"], 6)
        self.assertEqual(detail.response[0]["pagination"]["total"], 2)
        self.assertEqual(detail.response[0]["items"][0]["group_item_id"], group_item["group_item_id"])

        update_item = DummyApiRequest({"quantity": 2})
        app.api_dispatch(update_item, "PATCH", f"/api/v1/groups/{group_id}/collection-items/{group_item['group_item_id']}", {})
        self.assertEqual(update_item.response[1], HTTPStatus.OK)
        self.assertEqual(update_item.response[0]["data"]["group_quantity"], 2)

        copy_group_id = app.create_card_group(user_id, "binder", "Trade Binder")
        copy_item = DummyApiRequest({"target_group_id": copy_group_id, "quantity": 1, "mode": "copy"})
        app.api_dispatch(copy_item, "POST", f"/api/v1/groups/{group_id}/collection-items/{group_item['group_item_id']}/transfer", {})
        self.assertEqual(copy_item.response[1], HTTPStatus.OK)
        self.assertEqual(copy_item.response[0]["mode"], "copy")
        self.assertEqual(copy_item.response[0]["data"]["group_quantity"], 1)
        self.assertFalse(copy_item.response[0]["source"]["deleted"])
        self.assertEqual(copy_item.response[0]["source"]["group_quantity"], 2)
        self.assertEqual(app.row("SELECT quantity FROM group_collection_items WHERE id = ?", (group_item["group_item_id"],))["quantity"], 2)

        move_group_id = app.create_card_group(user_id, "deck", "Maybe Deck")
        move_item = DummyApiRequest({"target_group_id": move_group_id, "quantity": 1, "mode": "move"})
        app.api_dispatch(move_item, "POST", f"/api/v1/groups/{group_id}/collection-items/{group_item['group_item_id']}/transfer", {})
        self.assertEqual(move_item.response[1], HTTPStatus.OK)
        self.assertEqual(move_item.response[0]["mode"], "move")
        self.assertEqual(move_item.response[0]["source"]["group_quantity"], 1)
        self.assertEqual(app.row("SELECT quantity FROM group_collection_items WHERE id = ?", (group_item["group_item_id"],))["quantity"], 1)
        self.assertEqual(
            app.row(
                "SELECT quantity FROM group_collection_items WHERE group_id = ? AND collection_item_id = ?",
                (move_group_id, collection_id),
            )["quantity"],
            1,
        )

        update_group = DummyApiRequest({"name": "Landfall Maybeboard", "description": "Updated notes", "is_public": True})
        app.api_dispatch(update_group, "PATCH", f"/api/v1/groups/{group_id}", {})
        self.assertEqual(update_group.response[1], HTTPStatus.OK)
        self.assertEqual(update_group.response[0]["data"]["name"], "Landfall Maybeboard")
        self.assertEqual(update_group.response[0]["data"]["visibility"], "members")

        remove_item = DummyApiRequest()
        app.api_dispatch(remove_item, "DELETE", f"/api/v1/groups/{group_id}/collection-items/{group_item['group_item_id']}", {})
        self.assertEqual(remove_item.response[1], HTTPStatus.OK)
        self.assertEqual(remove_item.response[0]["deleted"], 1)

        delete_group = DummyApiRequest()
        app.api_dispatch(delete_group, "DELETE", f"/api/v1/groups/{group_id}", {})
        self.assertEqual(delete_group.response[1], HTTPStatus.OK)
        self.assertEqual(delete_group.response[0]["deleted"], 1)
        self.assertIsNone(app.row("SELECT * FROM card_groups WHERE id = ?", (group_id,)))

    def test_api_collection_batch_import_can_target_group(self):
        user_id = app.create_user("api-batch-import", "password123", "API Batch Import")
        token = app.create_api_token(user_id, "Write token", ["read", "write"])["token"]
        group_id = app.create_card_group(user_id, "deck", "Draft deck", is_public=False)

        class DummyApiRequest:
            command = "POST"
            _request_path = "/api/v1/collection/import"

            def __init__(self, body):
                self.headers = {"Authorization": f"Bearer {token}"}
                self.response = None
                self.body = body

            def __getattr__(self, name):
                if name.startswith("api_"):
                    return lambda *args, **kwargs: getattr(app, name)(self, *args, **kwargs)
                raise AttributeError(name)

            def api_json(self, payload, status=HTTPStatus.OK):
                self.response = (payload, status)

            def api_error(self, message, status=HTTPStatus.BAD_REQUEST):
                self.response = ({"error": message}, status)

            def api_read_json(self):
                return self.body

            def client_ip(self):
                return "203.0.113.12"

        payload = {
            "group_id": group_id,
            "items": [
                {
                    "game": "mtg",
                    "card_name": "Llanowar Elves",
                    "set_code": "FDN",
                    "collector_number": "227",
                    "quantity": 4,
                    "quantity_for_trade": 0,
                    "condition": "NM",
                    "finish": "Regular",
                    "language": "English",
                    "is_public": True,
                    "lookup_on_save": False,
                    "merge": True,
                },
                {
                    "game": "mtg",
                    "card_name": "Forest",
                    "set_code": "FDN",
                    "collector_number": "281",
                    "quantity": 10,
                    "quantity_for_trade": 0,
                    "condition": "NM",
                    "finish": "Regular",
                    "language": "English",
                    "is_public": True,
                    "lookup_on_save": False,
                    "merge": True,
                },
            ],
        }

        request = DummyApiRequest(payload)
        app.api_dispatch(request, "POST", "/api/v1/collection/import", {})

        self.assertEqual(request.response[1], HTTPStatus.OK)
        self.assertEqual(request.response[0]["summary"]["inserted"], 2)
        self.assertEqual(request.response[0]["summary"]["failed"], 0)
        self.assertEqual(request.response[0]["summary"]["grouped"], 2)
        self.assertEqual(request.response[0]["data"][0]["group"]["group_quantity"], 4)
        self.assertEqual(app.row("SELECT COUNT(*) AS count FROM collection_items WHERE user_id = ?", (user_id,))["count"], 2)
        self.assertEqual(app.row("SELECT COUNT(*) AS count FROM group_collection_items WHERE group_id = ?", (group_id,))["count"], 2)

    def test_api_dashboard_matches_web_home_summary_and_activity(self):
        user_id = app.create_user("api-dashboard", "password123", "API Dashboard")
        partner_id = app.create_user("api-dashboard-partner", "password123", "Dashboard Partner")
        token = app.create_api_token(user_id, "Read token", ["read"])["token"]
        factory.create_collection_item(
            user_id,
            "Recently Tradeable",
            quantity=3,
            quantity_for_trade=2,
            condition="LP",
            updated_at="2026-07-14T12:00:00+00:00",
        )
        factory.create_collection_item(user_id, "Not Tradeable", quantity=4, quantity_for_trade=0)
        factory.create_want_item(user_id, "Wanted Card")
        trade_id = factory.create_trade(
            user_id,
            partner_id,
            status="pending",
            updated_at="2026-07-14T13:00:00+00:00",
        )
        factory.create_trade_item(trade_id, user_id, "Recently Tradeable", side="offered")
        app.create_notification(
            user_id,
            "trade_offer",
            "Dashboard trade update",
            "A pending trade changed.",
            f"/trades/{trade_id}",
            trade_id,
        )

        class DummyApiRequest:
            command = "GET"
            _request_path = "/api/v1/dashboard"

            def __init__(self):
                self.headers = {"Authorization": f"Bearer {token}"}
                self.response = None

            def __getattr__(self, name):
                if name.startswith("api_"):
                    return lambda *args, **kwargs: getattr(app, name)(self, *args, **kwargs)
                raise AttributeError(name)

            def api_json(self, payload, status=HTTPStatus.OK):
                self.response = (payload, status)

            def api_error(self, message, status=HTTPStatus.BAD_REQUEST):
                self.response = ({"error": message}, status)

            def client_ip(self):
                return "203.0.113.13"

        request = DummyApiRequest()
        app.api_dispatch(request, "GET", "/api/v1/dashboard", {})

        self.assertEqual(request.response[1], HTTPStatus.OK)
        data = request.response[0]["data"]
        self.assertEqual(data["summary"], {
            "total_cards": 7,
            "trade_cards": 2,
            "unique_cards": 2,
            "wants_count": 1,
        })
        self.assertEqual(data["pending_trades"][0]["id"], trade_id)
        self.assertEqual(data["pending_trades"][0]["unread_trade_notifications"], 1)
        self.assertEqual(data["trade_matches"], [])
        self.assertEqual(data["recent_tradeable"][0]["card_name"], "Recently Tradeable")
        self.assertEqual(data["notifications"][0]["title"], "Dashboard trade update")

    def test_api_trade_matches_returns_privacy_safe_prefill_cards(self):
        alice_id = app.create_user("api-match-alice", "password123", "Alice")
        bob_id = app.create_user("api-match-bob", "password123", "Bob")
        carol_id = app.create_user("api-match-carol", "password123", "Carol")
        token = app.create_api_token(alice_id, "Read token", ["read"])["token"]

        factory.create_want_item(alice_id, "Sol Ring", desired_quantity=2)
        bob_sol_id = factory.create_collection_item(
            bob_id,
            "Sol Ring",
            quantity=3,
            quantity_for_trade=2,
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
        factory.create_want_item(bob_id, "Counterspell", is_public=1)
        factory.create_collection_item(
            carol_id,
            "Sol Ring",
            quantity=1,
            quantity_for_trade=1,
            is_public=1,
        )

        class DummyApiRequest:
            command = "GET"
            _request_path = "/api/v1/trade-matches"

            def __init__(self):
                self.headers = {"Authorization": f"Bearer {token}"}
                self.response = None

            def __getattr__(self, name):
                if name.startswith("api_"):
                    return lambda *args, **kwargs: getattr(app, name)(self, *args, **kwargs)
                raise AttributeError(name)

            def api_json(self, payload, status=HTTPStatus.OK):
                self.response = (payload, status)

            def api_error(self, message, status=HTTPStatus.BAD_REQUEST):
                self.response = ({"error": message}, status)

            def client_ip(self):
                return "203.0.113.14"

        request = DummyApiRequest()
        app.api_dispatch(request, "GET", "/api/v1/trade-matches", {"per_page": ["20"]})

        self.assertEqual(request.response[1], HTTPStatus.OK)
        self.assertEqual(request.response[0]["pagination"]["total"], 1)
        match = request.response[0]["data"][0]
        self.assertEqual(match["member_id"], bob_id)
        self.assertEqual(match["mutual_quantity"], 1)
        self.assertEqual(match["they_have_cards"][0]["collection_item_id"], bob_sol_id)
        self.assertEqual(match["they_have_cards"][0]["quantity"], 2)
        self.assertEqual(match["they_want_cards"][0]["collection_item_id"], alice_counter_id)
        self.assertNotIn("Carol", json.dumps(request.response[0]))

    def test_api_notification_detail_read_and_delete(self):
        user_id = app.create_user("api-notifications", "password123", "API Notifications")
        token = app.create_api_token(user_id, "Write token", ["read", "write"])["token"]
        notification_id = app.create_notification(
            user_id,
            "trade_offer",
            "New trade offer",
            "A trade arrived.",
            "/trades/42",
            None,
        )

        class DummyApiRequest:
            command = "GET"

            def __init__(self):
                self.headers = {"Authorization": f"Bearer {token}"}
                self.response = None
                self._request_path = "/api/v1/notifications"

            def __getattr__(self, name):
                if name.startswith("api_"):
                    return lambda *args, **kwargs: getattr(app, name)(self, *args, **kwargs)
                raise AttributeError(name)

            def api_json(self, payload, status=HTTPStatus.OK):
                self.response = (payload, status)

            def api_error(self, message, status=HTTPStatus.BAD_REQUEST):
                self.response = ({"error": message}, status)

            def client_ip(self):
                return "203.0.113.11"

        detail = DummyApiRequest()
        app.api_dispatch(detail, "GET", f"/api/v1/notifications/{notification_id}", {})
        self.assertEqual(detail.response[1], HTTPStatus.OK)
        self.assertEqual(detail.response[0]["data"]["title"], "New trade offer")
        self.assertFalse(detail.response[0]["data"]["is_read"])

        mark_read = DummyApiRequest()
        app.api_dispatch(mark_read, "POST", f"/api/v1/notifications/{notification_id}/read", {})
        self.assertEqual(mark_read.response[1], HTTPStatus.OK)
        self.assertTrue(mark_read.response[0]["data"]["is_read"])

        delete = DummyApiRequest()
        app.api_dispatch(delete, "DELETE", f"/api/v1/notifications/{notification_id}", {})
        self.assertEqual(delete.response[1], HTTPStatus.OK)
        self.assertEqual(delete.response[0]["deleted"], 1)
        self.assertIsNone(app.row("SELECT * FROM user_notifications WHERE id = ?", (notification_id,)))

    def test_api_trade_detail_includes_items_and_comments(self):
        proposer_id = app.create_user("api-proposer", "password123", "API Proposer")
        recipient_id = app.create_user("api-recipient", "password123", "API Recipient")
        token = app.create_api_token(proposer_id, "Read token", ["read"])["token"]
        trade_id = factory.create_trade(proposer_id, recipient_id, status="pending")
        factory.create_trade_item(trade_id, proposer_id, "Lightning Bolt", side="offered", quantity=2)
        app.execute(
            "INSERT INTO trade_comments (trade_id, user_id, body, created_at) VALUES (?, ?, ?, ?)",
            (trade_id, recipient_id, "Looks good.", app.now_iso()),
        )

        class DummyApiRequest:
            command = "GET"
            _request_path = "/api/v1/trades"

            def __init__(self):
                self.headers = {"Authorization": f"Bearer {token}"}
                self.response = None

            def __getattr__(self, name):
                if name.startswith("api_"):
                    return lambda *args, **kwargs: getattr(app, name)(self, *args, **kwargs)
                raise AttributeError(name)

            def api_json(self, payload, status=HTTPStatus.OK):
                self.response = (payload, status)

            def api_error(self, message, status=HTTPStatus.BAD_REQUEST):
                self.response = ({"error": message}, status)

            def client_ip(self):
                return "203.0.113.12"

        request = DummyApiRequest()
        app.api_dispatch(request, "GET", f"/api/v1/trades/{trade_id}", {})

        self.assertEqual(request.response[1], HTTPStatus.OK)
        self.assertEqual(request.response[0]["data"]["id"], trade_id)
        self.assertEqual(request.response[0]["data"]["items"][0]["card_name"], "Lightning Bolt")
        self.assertEqual(request.response[0]["data"]["items"][0]["quantity"], 2)
        self.assertEqual(request.response[0]["data"]["comments"][0]["body"], "Looks good.")

    def test_api_can_create_trade_comment(self):
        proposer_id = app.create_user("api-comment-proposer", "password123", "Comment Proposer")
        recipient_id = app.create_user("api-comment-recipient", "password123", "Comment Recipient")
        token = app.create_api_token(recipient_id, "Write token", ["read", "write"])["token"]
        proposer_card = factory.collection_item_row(proposer_id, "Sol Ring", quantity=2, quantity_for_trade=1)
        recipient_card = factory.collection_item_row(recipient_id, "Lightning Bolt", quantity=2, quantity_for_trade=1)
        trade_id = app.create_trade_offer(
            proposer_id,
            recipient_id,
            "Mobile comment test",
            [(proposer_card, 1)],
            [(recipient_card, 1)],
        )

        class DummyApiRequest:
            _request_path = "/api/v1/trades"

            def __init__(self, payload):
                self.headers = {"Authorization": f"Bearer {token}"}
                self.payload = payload
                self.response = None

            def __getattr__(self, name):
                if name.startswith("api_"):
                    return lambda *args, **kwargs: getattr(app, name)(self, *args, **kwargs)
                raise AttributeError(name)

            def api_read_json(self):
                return self.payload

            def api_json(self, payload, status=HTTPStatus.OK):
                self.response = (payload, status)

            def api_error(self, message, status=HTTPStatus.BAD_REQUEST):
                self.response = ({"error": message}, status)

            def client_ip(self):
                return "203.0.113.12"

        request = DummyApiRequest({"body": "Can you ship this week?"})
        app.api_dispatch(request, "POST", f"/api/v1/trades/{trade_id}/comments", {})

        self.assertEqual(request.response[1], HTTPStatus.CREATED)
        self.assertEqual(request.response[0]["data"]["comments"][0]["body"], "Can you ship this week?")
        notification = app.row(
            "SELECT * FROM user_notifications WHERE user_id = ? AND kind = 'trade_comment'",
            (proposer_id,),
        )
        self.assertIsNotNone(notification)
        self.assertEqual(notification["related_trade_id"], trade_id)

    def test_api_can_propose_trade_with_visible_trade_cards(self):
        proposer_id = app.create_user("api-mobile-proposer", "password123", "Mobile Proposer")
        recipient_id = app.create_user("api-mobile-recipient", "password123", "Mobile Recipient")
        hidden_recipient_id = app.create_user("api-mobile-hidden", "password123", "Hidden Recipient")
        token = app.create_api_token(proposer_id, "Write token", ["read", "write"])["token"]
        proposer_card = factory.collection_item_row(proposer_id, "Sol Ring", quantity=2, quantity_for_trade=1)
        recipient_card = factory.collection_item_row(recipient_id, "Lightning Bolt", quantity=2, quantity_for_trade=1)
        factory.collection_item_row(recipient_id, "Private Tutor", quantity=1, quantity_for_trade=1, visibility="private", is_public=0)
        factory.collection_item_row(hidden_recipient_id, "Hidden Card", quantity=1, quantity_for_trade=0)

        class DummyApiRequest:
            _request_path = "/api/v1/trades"

            def __init__(self, payload=None):
                self.headers = {"Authorization": f"Bearer {token}"}
                self.payload = payload or {}
                self.response = None

            def __getattr__(self, name):
                if name.startswith("api_"):
                    return lambda *args, **kwargs: getattr(app, name)(self, *args, **kwargs)
                raise AttributeError(name)

            def api_read_json(self):
                return self.payload

            def api_json(self, payload, status=HTTPStatus.OK):
                self.response = (payload, status)

            def api_error(self, message, status=HTTPStatus.BAD_REQUEST):
                self.response = ({"error": message}, status)

            def client_ip(self):
                return "203.0.113.12"

        partners = DummyApiRequest()
        app.api_dispatch(partners, "GET", "/api/v1/trade-partners", {"q": ["recipient"]})
        self.assertEqual(partners.response[1], HTTPStatus.OK)
        partner_ids = {item["id"] for item in partners.response[0]["data"]}
        self.assertIn(recipient_id, partner_ids)
        self.assertNotIn(hidden_recipient_id, partner_ids)

        cards = DummyApiRequest()
        app.api_dispatch(cards, "GET", "/api/v1/trade-cards", {"owner_id": [str(recipient_id)]})
        self.assertEqual(cards.response[1], HTTPStatus.OK)
        self.assertEqual([item["card_name"] for item in cards.response[0]["data"]], ["Lightning Bolt"])

        create = DummyApiRequest({
            "recipient_id": recipient_id,
            "proposer_note": "Built from Android.",
            "offered": [{"collection_item_id": proposer_card["id"], "quantity": 1}],
            "requested": [{"collection_item_id": recipient_card["id"], "quantity": 1}],
        })
        app.api_dispatch(create, "POST", "/api/v1/trades", {})
        self.assertEqual(create.response[1], HTTPStatus.CREATED)
        self.assertEqual(create.response[0]["data"]["status"], "pending")
        self.assertEqual(create.response[0]["data"]["proposer"]["id"], proposer_id)
        self.assertEqual(create.response[0]["data"]["recipient"]["id"], recipient_id)
        self.assertEqual(len(create.response[0]["data"]["items"]), 2)

    def test_api_can_create_counter_offer(self):
        proposer_id = app.create_user("api-counter-proposer", "password123", "Counter Proposer")
        recipient_id = app.create_user("api-counter-recipient", "password123", "Counter Recipient")
        recipient_token = app.create_api_token(recipient_id, "Write token", ["read", "write"])["token"]
        proposer_card = factory.collection_item_row(proposer_id, "Sol Ring", quantity=2, quantity_for_trade=1)
        recipient_card = factory.collection_item_row(recipient_id, "Lightning Bolt", quantity=2, quantity_for_trade=1)
        trade_id = app.create_trade_offer(
            proposer_id,
            recipient_id,
            "Original mobile offer",
            [(proposer_card, 1)],
            [(recipient_card, 1)],
        )

        class DummyApiRequest:
            _request_path = "/api/v1/trades"

            def __init__(self, payload=None):
                self.headers = {"Authorization": f"Bearer {recipient_token}"}
                self.payload = payload or {}
                self.response = None

            def __getattr__(self, name):
                if name.startswith("api_"):
                    return lambda *args, **kwargs: getattr(app, name)(self, *args, **kwargs)
                raise AttributeError(name)

            def api_read_json(self):
                return self.payload

            def api_json(self, payload, status=HTTPStatus.OK):
                self.response = (payload, status)

            def api_error(self, message, status=HTTPStatus.BAD_REQUEST):
                self.response = ({"error": message}, status)

            def client_ip(self):
                return "203.0.113.12"

        detail = DummyApiRequest()
        app.api_dispatch(detail, "GET", f"/api/v1/trades/{trade_id}", {})
        self.assertEqual(detail.response[1], HTTPStatus.OK)
        self.assertTrue(detail.response[0]["data"]["viewer"]["can_counter"])

        counter = DummyApiRequest({
            "recipient_id": proposer_id,
            "counter_trade_id": trade_id,
            "proposer_note": "Could we do this instead?",
            "offered": [{"collection_item_id": recipient_card["id"], "quantity": 1}],
            "requested": [{"collection_item_id": proposer_card["id"], "quantity": 1}],
        })
        app.api_dispatch(counter, "POST", "/api/v1/trades", {})

        self.assertEqual(counter.response[1], HTTPStatus.OK)
        counter_trade = counter.response[0]["data"]
        self.assertEqual(counter_trade["id"], trade_id)
        self.assertEqual(counter_trade["countered_from_trade_id"], 0)
        self.assertEqual(counter_trade["counter_trade_id"], 0)
        self.assertEqual(counter_trade["viewer"]["role"], "proposer")
        original = app.row("SELECT * FROM trades WHERE id = ?", (trade_id,))
        self.assertEqual(original["status"], "pending")
        self.assertEqual(original["proposer_id"], recipient_id)
        self.assertEqual(original["recipient_id"], proposer_id)
        self.assertIsNone(original["counter_trade_id"])

    def test_api_trade_actions_update_trade_statuses(self):
        proposer_id = app.create_user("api-action-proposer", "password123", "Action Proposer")
        recipient_id = app.create_user("api-action-recipient", "password123", "Action Recipient")
        proposer_token = app.create_api_token(proposer_id, "Write token", ["read", "write"])["token"]
        recipient_token = app.create_api_token(recipient_id, "Write token", ["read", "write"])["token"]

        proposer_card = factory.collection_item_row(proposer_id, "Sol Ring", quantity=2, quantity_for_trade=1)
        recipient_card = factory.collection_item_row(recipient_id, "Lightning Bolt", quantity=2, quantity_for_trade=1)
        trade_id = app.create_trade_offer(proposer_id, recipient_id, "Mobile review", [(proposer_card, 1)], [(recipient_card, 1)])

        class DummyApiRequest:
            _request_path = "/api/v1/trades"

            def __init__(self, token, payload=None):
                self.headers = {"Authorization": f"Bearer {token}"}
                self.payload = payload or {}
                self.response = None

            def __getattr__(self, name):
                if name.startswith("api_"):
                    return lambda *args, **kwargs: getattr(app, name)(self, *args, **kwargs)
                raise AttributeError(name)

            def api_read_json(self):
                return self.payload

            def api_json(self, payload, status=HTTPStatus.OK):
                self.response = (payload, status)

            def api_error(self, message, status=HTTPStatus.BAD_REQUEST):
                self.response = ({"error": message}, status)

            def client_ip(self):
                return "203.0.113.12"

        listing = DummyApiRequest(recipient_token)
        app.api_dispatch(listing, "GET", "/api/v1/trades", {"direction": ["needs_action"]})
        self.assertEqual(listing.response[1], HTTPStatus.OK)
        self.assertEqual(listing.response[0]["data"][0]["viewer"]["direction"], "incoming")
        self.assertTrue(listing.response[0]["data"][0]["viewer"]["can_accept"])
        self.assertEqual(listing.response[0]["metrics"]["needs_action"], 1)

        accept = DummyApiRequest(recipient_token, {"response_note": "Looks fair."})
        app.api_dispatch(accept, "POST", f"/api/v1/trades/{trade_id}/accept", {})
        self.assertEqual(accept.response[1], HTTPStatus.OK)
        self.assertEqual(accept.response[0]["data"]["status"], "accepted")
        self.assertTrue(accept.response[0]["data"]["viewer"]["can_complete"])

        complete = DummyApiRequest(recipient_token)
        app.api_dispatch(complete, "POST", f"/api/v1/trades/{trade_id}/complete", {})
        self.assertEqual(complete.response[1], HTTPStatus.OK)
        self.assertEqual(complete.response[0]["data"]["status"], "completed")

        cancel_proposer_card = factory.collection_item_row(proposer_id, "Counterspell", quantity=1, quantity_for_trade=1)
        cancel_recipient_card = factory.collection_item_row(recipient_id, "Opt", quantity=1, quantity_for_trade=1)
        cancel_trade_id = app.create_trade_offer(
            proposer_id,
            recipient_id,
            "Cancel from mobile",
            [(cancel_proposer_card, 1)],
            [(cancel_recipient_card, 1)],
        )
        cancel = DummyApiRequest(proposer_token)
        app.api_dispatch(cancel, "POST", f"/api/v1/trades/{cancel_trade_id}/cancel", {})
        self.assertEqual(cancel.response[1], HTTPStatus.OK)
        self.assertEqual(cancel.response[0]["data"]["status"], "cancelled")

        decline_trade_id = factory.create_trade(proposer_id, recipient_id, status="pending")
        decline = DummyApiRequest(recipient_token, {"response_note": "Not this time."})
        app.api_dispatch(decline, "POST", f"/api/v1/trades/{decline_trade_id}/decline", {})
        self.assertEqual(decline.response[1], HTTPStatus.OK)
        self.assertEqual(decline.response[0]["data"]["status"], "declined")

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
        self.assertIn(app.APP_VERSION, html)
        self.assertIn("AGPL-3.0 license", html)
        self.assertIn(app.SOURCE_URL, html)
        self.assertNotIn(">Import</a>", html)
        self.assertNotIn(">Groups</a>", html)
