"""Registration moderation and ban-evasion signal tests."""

from tests.base import *  # noqa: F401,F403


class RegistrationModerationTests(BinderBridgeTestCase):
    def test_registration_moderation_schema_exists(self):
        user_columns = {item["name"] for item in app.rows("PRAGMA table_info(users)")}
        attempt_columns = {item["name"] for item in app.rows("PRAGMA table_info(registration_attempts)")}
        attempt_indexes = {item["name"] for item in app.rows("PRAGMA index_list(registration_attempts)")}

        self.assertIn("registration_status", user_columns)
        self.assertIn("registration_review_note", user_columns)
        self.assertTrue({"email_hash", "ip_hash", "subnet_hash", "risk_score", "risk_reasons_json"}.issubset(attempt_columns))
        self.assertIn("idx_registration_attempts_email_hash", attempt_indexes)
        self.assertIn("idx_registration_attempts_ip_hash", attempt_indexes)

    def test_approval_mode_creates_pending_account_and_admin_can_approve(self):
        admin_id = app.create_user("owner", "password123", "Owner")
        app.set_registration_moderation_settings("all", 50)
        assessment = app.registration_risk_assessment(
            "newmember",
            "New Member",
            "new@example.test",
            None,
            "198.51.100.10",
            "test-agent",
        )
        status = app.registration_status_for_new_account(1, assessment["score"])
        user_id = app.create_user(
            "newmember",
            "password123",
            "New Member",
            email="new@example.test",
            registration_status=status,
        )
        app.record_registration_attempt(
            user_id,
            "newmember",
            "New Member",
            "new@example.test",
            None,
            "198.51.100.10",
            "test-agent",
            assessment,
            status,
        )

        pending = app.pending_registration_rows()
        pending_user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))

        self.assertEqual(status, app.REGISTRATION_STATUS_PENDING)
        self.assertEqual(app.pending_registration_count(), 1)
        self.assertEqual(pending[0]["id"], user_id)
        self.assertFalse(app.user_can_write_content(pending_user))
        with self.assertRaisesRegex(ValueError, "active accounts"):
            app.create_session(user_id)

        reviewed = app.admin_review_registration(admin_id, user_id, "approve", "Known local trader.")

        self.assertEqual(reviewed["registration_status"], app.REGISTRATION_STATUS_ACTIVE)
        token, _expires = app.create_session(user_id)
        self.assertIsNotNone(app.get_user_by_session(token))
        notification = app.row("SELECT * FROM user_notifications WHERE user_id = ? AND kind = 'admin_notice'", (user_id,))
        self.assertIsNotNone(notification)

    def test_suspicious_mode_flags_banned_email_match_for_review(self):
        admin_id = app.create_user("owner", "password123", "Owner")
        banned_id = app.create_user("banned", "password123", "Banned User", email="same@example.test")
        app.admin_set_user_ban(admin_id, banned_id, True, "Problematic user.")
        app.set_registration_moderation_settings("suspicious", 50)

        assessment = app.registration_risk_assessment(
            "newname",
            "New Name",
            "same@example.test",
            None,
            "203.0.113.55",
            "test-agent",
        )

        self.assertGreaterEqual(assessment["score"], 50)
        self.assertIn("email_match", {reason["code"] for reason in assessment["reasons"]})
        self.assertEqual(app.registration_status_for_new_account(2, assessment["score"]), app.REGISTRATION_STATUS_PENDING)

    def test_admin_can_deny_pending_registration(self):
        admin_id = app.create_user("owner", "password123", "Owner")
        user_id = app.create_user(
            "pendingdeny",
            "password123",
            "Pending Deny",
            registration_status=app.REGISTRATION_STATUS_PENDING,
        )
        assessment = {"score": 0, "reasons": [], "signals": app.registration_signal_values("", "198.51.100.22", "agent")}
        app.record_registration_attempt(user_id, "pendingdeny", "Pending Deny", "", None, "198.51.100.22", "agent", assessment, "pending")

        reviewed = app.admin_review_registration(admin_id, user_id, "deny", "Not recognized by the group.")
        attempt = app.row("SELECT * FROM registration_attempts WHERE user_id = ?", (user_id,))

        self.assertEqual(reviewed["registration_status"], app.REGISTRATION_STATUS_DENIED)
        self.assertEqual(attempt["status"], app.REGISTRATION_STATUS_DENIED)
        self.assertEqual(reviewed["registration_review_note"], "Not recognized by the group.")
        with self.assertRaisesRegex(ValueError, "active accounts"):
            app.create_session(user_id)

    def test_ban_revokes_sessions_integrations_invites_and_marks_attempts(self):
        admin_id = app.create_user("owner", "password123", "Owner")
        user_id = app.create_user("baduser", "password123", "Bad User", email="bad@example.test")
        token, _expires = app.create_session(user_id)
        api_token = app.create_api_token(user_id, "Sync", ["read", "write"])
        webhook = app.create_webhook_endpoint(user_id, "Hook", "https://example.com/hook")
        invite = app.create_registration_invite(user_id, "future@example.test")
        assessment = {"score": 0, "reasons": [], "signals": app.registration_signal_values("bad@example.test", "198.51.100.99", "agent")}
        app.record_registration_attempt(user_id, "baduser", "Bad User", "bad@example.test", None, "198.51.100.99", "agent", assessment, "active")

        app.admin_set_user_ban(admin_id, user_id, True, "Ban cleanup.")

        self.assertIsNone(app.get_user_by_session(token))
        self.assertIsNone(app.get_user_by_api_token(api_token["token"])[0])
        self.assertNotEqual(app.row("SELECT * FROM api_tokens WHERE id = ?", (api_token["id"],))["revoked_at"], "")
        self.assertEqual(app.row("SELECT * FROM webhook_endpoints WHERE id = ?", (webhook["id"],))["is_active"], 0)
        self.assertEqual(app.row("SELECT * FROM registration_invites WHERE id = ?", (invite["id"],))["status"], "revoked")
        self.assertEqual(app.row("SELECT * FROM registration_attempts WHERE user_id = ?", (user_id,))["status"], "banned")


if __name__ == "__main__":
    unittest.main()
