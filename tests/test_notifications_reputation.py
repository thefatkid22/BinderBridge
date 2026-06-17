"""Notification delivery, preferences, reminders, feedback, and reputation tests."""

from tests.base import *  # noqa: F401,F403


class NotificationsReputationTests(BinderBridgeTestCase):
    def test_notification_inbox_filters_categories_state_and_paginates(self):
        user_id = factory.create_user("notification-inbox", display_name="Notification Inbox")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        for index in range(12):
            app.create_notification(user_id, "trade_status", f"Trade update {index:02d}", "Trade activity")
        price_id = app.create_notification(user_id, "price_alert", "Sol Ring moved", "Price activity")
        app.execute("UPDATE user_notifications SET is_read = 1 WHERE id = ?", (price_id,))

        trade_html = app.render_notifications(
            user,
            query={"category": ["trade"], "state": ["unread"], "per_page": ["10"], "page": ["1"]},
        )
        price_html = app.render_notifications(user, query={"category": ["price"], "state": ["read"]})

        self.assertIn("Showing 1-10 of 12", trade_html)
        self.assertIn("Trade update 11", trade_html)
        self.assertNotIn("Sol Ring moved", trade_html)
        self.assertIn("Active filters", trade_html)
        self.assertIn('name="state"', trade_html)
        self.assertIn("Sol Ring moved", price_html)
        self.assertNotIn("Trade update 11", price_html)
        self.assertIn('id="notification-inbox"', price_html)
        self.assertIn('id="notification-values"', price_html)

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
            self.assertEqual(sent, [])
            app.process_background_job_once("trade-email-test-worker")
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
            app.process_background_job_once("trade-email-failure-worker")
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
            self.assertEqual(sent, [])
            app.process_background_job_once("notification-test-worker")
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

    def test_notification_digest_groups_pending_emails_at_scheduled_time(self):
        user_id = app.create_user("digestuser", "password123", "Digest User", email="digest@example.test")
        app.execute(
            """
            UPDATE users
            SET email_trade_notifications_enabled = 1,
                email_digest_frequency = 'daily',
                email_digest_time = '09:00',
                notification_timezone = 'UTC'
            WHERE id = ?
            """,
            (user_id,),
        )
        timestamp = app.now_iso()
        first_id = app.execute(
            "INSERT INTO user_notifications (user_id, kind, title, email_status, created_at) VALUES (?, 'trade_offer', 'First offer', 'pending', ?)",
            (user_id, timestamp),
        )
        second_id = app.execute(
            "INSERT INTO user_notifications (user_id, kind, title, email_status, created_at) VALUES (?, 'trade_comment', 'New comment', 'pending', ?)",
            (user_id, timestamp),
        )
        sent = []
        original_sender = app.send_email_message
        original_configured = app.email_delivery_configured
        app.send_email_message = lambda to_email, subject, body: (sent.append((to_email, subject, body)) or True, "Email sent.")
        app.email_delivery_configured = lambda: True
        try:
            result = app.send_pending_trade_notification_emails(
                limit=1,
                reference_time=datetime(2026, 6, 8, 10, 0, tzinfo=timezone.utc)
            )
        finally:
            app.send_email_message = original_sender
            app.email_delivery_configured = original_configured

        self.assertEqual(result["sent"], 2)
        self.assertEqual(len(sent), 1)
        self.assertIn("2 unread notifications", sent[0][1])
        self.assertIn("First offer", sent[0][2])
        self.assertIn("New comment", sent[0][2])
        self.assertEqual(app.row("SELECT email_status FROM user_notifications WHERE id = ?", (first_id,))["email_status"], "sent")
        self.assertEqual(app.row("SELECT email_status FROM user_notifications WHERE id = ?", (second_id,))["email_status"], "sent")

    def test_quiet_hours_defer_immediate_email_until_delivery_window(self):
        user_id = app.create_user("quietuser", "password123", "Quiet User", email="quiet@example.test")
        app.execute(
            """
            UPDATE users
            SET email_trade_notifications_enabled = 1,
                quiet_hours_enabled = 1,
                quiet_hours_start = '22:00',
                quiet_hours_end = '07:00',
                notification_timezone = 'UTC'
            WHERE id = ?
            """,
            (user_id,),
        )
        notification_id = app.execute(
            "INSERT INTO user_notifications (user_id, kind, title, email_status, created_at) VALUES (?, 'trade_offer', 'Quiet offer', 'pending', ?)",
            (user_id, app.now_iso()),
        )
        sent = []
        original_sender = app.send_email_message
        original_configured = app.email_delivery_configured
        app.send_email_message = lambda to_email, subject, body: (sent.append(subject) or True, "Email sent.")
        app.email_delivery_configured = lambda: True
        try:
            quiet_result = app.send_pending_trade_notification_emails(
                reference_time=datetime(2026, 6, 8, 23, 0, tzinfo=timezone.utc)
            )
            daytime_result = app.send_pending_trade_notification_emails(
                reference_time=datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
            )
        finally:
            app.send_email_message = original_sender
            app.email_delivery_configured = original_configured

        self.assertEqual(quiet_result["deferred"], 1)
        self.assertEqual(daytime_result["sent"], 1)
        self.assertEqual(len(sent), 1)
        self.assertEqual(app.row("SELECT email_status FROM user_notifications WHERE id = ?", (notification_id,))["email_status"], "sent")

    def test_weekly_digest_remains_due_after_scheduled_day(self):
        settings = {
            "email_digest_frequency": "weekly",
            "email_digest_time": "09:00",
            "email_digest_weekday": 4,
            "notification_timezone": "UTC",
            "last_notification_digest_at": "",
        }

        due = app.notification_digest_due(settings, datetime(2026, 6, 13, 10, 0, tzinfo=timezone.utc))
        settings["last_notification_digest_at"] = "2026-06-12T10:00:00+00:00"
        already_sent = app.notification_digest_due(settings, datetime(2026, 6, 13, 10, 0, tzinfo=timezone.utc))

        self.assertTrue(due)
        self.assertFalse(already_sent)

    def test_stale_trade_reminders_are_idempotent_and_highlight_trade_activity(self):
        proposer_id = app.create_user("staleproposer", "password123", "Stale Proposer")
        recipient_id = app.create_user("stalerecipient", "password123", "Stale Recipient")
        old_at = (datetime.now(timezone.utc) - timedelta(days=4)).replace(microsecond=0).isoformat()
        trade_id = factory.create_trade(proposer_id, recipient_id, created_at=old_at, updated_at=old_at)

        first_created = app.create_stale_trade_reminders()
        second_created = app.create_stale_trade_reminders()
        recipient = app.row("SELECT * FROM users WHERE id = ?", (recipient_id,))
        reminders = app.stale_trade_reminder_rows(recipient_id)
        trades_html = app.render_trades(recipient)
        layout_html = app.render_layout(recipient, "Test", "<p>Body</p>", active="trades")
        unread_trade_count = app.unread_trade_notification_count(recipient_id)
        app.delete_notification(recipient_id, reminders[0]["id"])
        after_delete_created = app.create_stale_trade_reminders()

        self.assertEqual(first_created, 1)
        self.assertEqual(second_created, 0)
        self.assertEqual(after_delete_created, 0)
        self.assertEqual(len(reminders), 1)
        self.assertEqual(reminders[0]["related_trade_id"], trade_id)
        self.assertIn("needs your response", reminders[0]["title"])
        self.assertEqual(unread_trade_count, 1)
        self.assertIn("Your response needed", trades_html)
        self.assertIn("1 unread", trades_html)
        self.assertIn("trade-nav-badge", layout_html)
        self.assertIn('class="skip-link" href="#main-content"', layout_html)
        self.assertIn('id="main-content" tabindex="-1"', layout_html)
        self.assertIn('aria-current="page"', layout_html)
        self.assertIn('class="mobile-nav-toggle"', layout_html)
        self.assertIn('id="primary-navigation"', layout_html)
        self.assertIn('id="confirm-dialog"', layout_html)
        self.assertIn('data-confirm="Delete test records?"', app.render_layout(recipient, "Test", '<button data-confirm="Delete test records?">Delete</button>'))
        self.assertIn('role="status" aria-live="polite"', app.render_layout(recipient, "Test", "<p>Body</p>", notice="Saved"))
        self.assertIn('role="alert"', app.render_layout(recipient, "Test", "<p>Body</p>", notice="Failed", status="error"))

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
        self.assertIn('aria-label="Public profile sections"', html)
        self.assertIn('href="#member-trades"', html)
        self.assertIn('id="member-reputation"', html)
        self.assertIn('id="member-groups"', html)
        self.assertIn('id="member-wants"', html)
