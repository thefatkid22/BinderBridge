"""Wishlist preferences, matching, sharing, and watchlist alert tests."""

from tests.base import *  # noqa: F401,F403


class WantsWatchlistTests(BinderBridgeTestCase):
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
            "priority": ["high"],
            "budget_cap_usd": ["4.25"],
            "condition": ["LP", "NM"],
            "finish": ["Foil", "Regular"],
            "language": ["Japanese", "English"],
            "preferred_printing_notes": ["Pip-Boy showcase art"],
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
        self.assertIn('name="priority"', html)
        self.assertIn('name="budget_cap_usd"', html)
        self.assertIn('name="preferred_printing_notes"', html)
        self.assertEqual(updated, 1)
        self.assertEqual(saved["set_name"], "Fallout")
        self.assertEqual(saved["set_code"], "PIP")
        self.assertEqual(saved["collector_number"], "233")
        self.assertEqual(saved["desired_quantity"], 3)
        self.assertEqual(saved["priority"], "high")
        self.assertEqual(saved["budget_cap_usd"], "4.25")
        self.assertEqual(saved["condition"], "NM,LP")
        self.assertEqual(saved["finish"], "Regular,Foil")
        self.assertEqual(saved["language"], "English,Japanese")
        self.assertEqual(saved["preferred_printing_notes"], "Pip-Boy showcase art")
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
