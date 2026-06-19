from tests.base import BinderBridgeTestCase, app


class DemoDataTests(BinderBridgeTestCase):
    def test_demo_seed_populates_rich_empty_database(self):
        result = app.seed_demo_data(enabled=True)

        self.assertTrue(result["seeded"])
        self.assertEqual(result["accounts"]["alice"], app.DEMO_PASSWORD)
        self.assertEqual(app.row("SELECT COUNT(*) AS count FROM users")["count"], 4)
        self.assertGreaterEqual(app.row("SELECT COUNT(*) AS count FROM collection_items")["count"], 10)
        self.assertGreaterEqual(app.row("SELECT COUNT(*) AS count FROM want_items")["count"], 6)
        self.assertGreaterEqual(app.row("SELECT COUNT(*) AS count FROM card_groups")["count"], 4)
        self.assertEqual(app.row("SELECT COUNT(*) AS count FROM trades")["count"], 3)
        self.assertEqual(app.row("SELECT COUNT(*) AS count FROM trade_disputes")["count"], 1)
        self.assertGreaterEqual(app.row("SELECT COUNT(*) AS count FROM user_notifications")["count"], 1)
        self.assertGreaterEqual(app.row("SELECT COUNT(*) AS count FROM collection_item_photos")["count"], 1)
        self.assertGreaterEqual(app.row("SELECT COUNT(*) AS count FROM trade_feedback")["count"], 2)

    def test_demo_seed_refuses_non_empty_database_by_default(self):
        app.create_user("realuser", "password123", "Real User")

        result = app.seed_demo_data(enabled=True)

        self.assertFalse(result["seeded"])
        self.assertEqual(result["reason"], "existing_users")
        self.assertFalse(app.row("SELECT id FROM users WHERE username = 'alice'"))

    def test_demo_seed_reset_only_recreates_known_demo_users(self):
        app.create_user("realuser", "password123", "Real User")
        first = app.seed_demo_data(enabled=True, allow_existing=True)
        self.assertTrue(first["seeded"])

        app.execute(
            "UPDATE users SET display_name = 'Changed Alice' WHERE username = 'alice'"
        )
        second = app.seed_demo_data(enabled=True, reset=True, allow_existing=True)

        self.assertTrue(second["seeded"])
        self.assertTrue(app.row("SELECT id FROM users WHERE username = 'realuser'"))
        self.assertEqual(
            app.row("SELECT display_name FROM users WHERE username = 'alice'")["display_name"],
            "Alice Owner",
        )
        self.assertEqual(
            app.row("SELECT COUNT(*) AS count FROM users WHERE username IN ('alice', 'bob', 'cara', 'drew')")["count"],
            4,
        )
