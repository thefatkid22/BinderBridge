"""Collection, browse, filtering, sorting, privacy, and bulk-action tests."""

from tests.base import *  # noqa: F401,F403


class CollectionBrowseTests(BinderBridgeTestCase):
    def test_wishlist_priority_budget_and_printing_notes_round_trip(self):
        wanter_id = factory.create_user("priority-wanter", display_name="Priority Wanter")
        trader_id = factory.create_user("priority-trader", display_name="Priority Trader")
        user = app.row("SELECT * FROM users WHERE id = ?", (wanter_id,))
        data = app.validate_want_form({
            "card_name": ["Rhystic Study"],
            "game": ["mtg"],
            "desired_quantity": ["2"],
            "priority": ["urgent"],
            "budget_cap_usd": ["$5.50"],
            "preferred_printing_notes": ["Retro frame or storybook art"],
        })
        want_id = app.insert_want_item(wanter_id, data)
        factory.create_want_item(wanter_id, "Low Priority Card", priority="low")
        factory.create_collection_item(
            trader_id,
            "Rhystic Study",
            set_name="Budget Printing",
            price_usd="4.00",
            quantity_for_trade=1,
        )
        factory.create_collection_item(
            trader_id,
            "Rhystic Study",
            set_name="Expensive Printing",
            price_usd="8.00",
            quantity_for_trade=1,
        )

        want = app.row("SELECT * FROM want_items WHERE id = ?", (want_id,))
        availability = app.want_trade_matches(wanter_id, want)
        html = app.render_wants(user)
        api_data = app.api_want_item_dict(want)

        self.assertEqual(want["priority"], "urgent")
        self.assertEqual(want["budget_cap_usd"], "5.50")
        self.assertEqual(want["preferred_printing_notes"], "Retro frame or storybook art")
        self.assertEqual(availability["total_quantity"], 2)
        self.assertEqual(availability["within_budget_quantity"], 1)
        self.assertEqual(availability["matches"][0]["within_budget_quantity"], 1)
        self.assertIn("Urgent", html)
        self.assertIn("Up to $5.50 each", html)
        self.assertIn("1 currently fit the $5.50 per-copy budget.", html)
        self.assertIn("Retro frame or storybook art", html)
        self.assertLess(html.index("Rhystic Study"), html.index("Low Priority Card"))
        self.assertEqual(api_data["priority"], "urgent")
        self.assertEqual(api_data["budget_cap_usd"], "5.50")
        self.assertIn("priority", app.WANT_EXPORT_FIELDS)
        self.assertIn("preferred_printing_notes", app.WANT_EXPORT_FIELDS)
        with self.assertRaisesRegex(ValueError, "valid wishlist priority"):
            app.validate_want_form({"card_name": ["Bad Priority"], "priority": ["now"]})
        with self.assertRaisesRegex(ValueError, "valid non-negative dollar amount"):
            app.validate_want_form({"card_name": ["Bad Budget"], "budget_cap_usd": ["free"]})

    def test_collection_page_paginates_and_renders_bulk_controls(self):
        user_id = app.create_user("pager", "password123", "Pager")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        app.create_card_group(user_id, "binder", "Bulk Binder")
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
        self.assertIn("bulk-danger-zone", html)
        self.assertIn("<summary>Remove cards</summary>", html)
        self.assertLess(html.index("Update selected"), html.index("<summary>Remove cards</summary>"))
        self.assertIn('formaction="/collection/bulk-group"', html)
        self.assertIn('formaction="/collection/group-all"', html)
        self.assertIn("Group qty", html)
        self.assertIn("Binder: Bulk Binder", html)
        self.assertIn('name="quantity_for_trade"', html)
        self.assertIn("Visibility", html)
        self.assertIn('<option value="">No change</option>', html)
        self.assertIn('<option value="trusted">Trusted members</option>', html)
        self.assertIn('<option value="private">Private</option>', html)
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

    def test_wishlist_page_paginates_and_renders_bulk_controls(self):
        user_id = app.create_user("wantpager", "password123", "Want Pager")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        app.create_card_group(user_id, "wishlist", "Bulk Wants")
        for index in range(12):
            factory.create_want_item(user_id, f"Want {index:02d}", priority="normal")

        html = app.render_wants(user, query={"per_page": ["10"], "page": ["1"]})

        self.assertIn("Showing 1-10 of 12", html)
        self.assertIn('id="wants-bulk-form"', html)
        self.assertIn('form="wants-bulk-form"', html)
        self.assertIn('name="want_id"', html)
        self.assertIn("Select page", html)
        self.assertIn('formaction="/wants/bulk-update"', html)
        self.assertIn('formaction="/wants/update-all"', html)
        self.assertIn('formaction="/wants/bulk-group"', html)
        self.assertIn('formaction="/wants/group-all"', html)
        self.assertIn('formaction="/wants/bulk-delete"', html)
        self.assertIn('formaction="/wants/delete-all"', html)
        self.assertIn("Bulk edit wishlist", html)
        self.assertIn("Desired qty", html)
        self.assertIn("Wishlist: Bulk Wants", html)
        self.assertIn("<summary>Remove wants</summary>", html)
        self.assertIn('class="panel flush wants-panel" id="tracked-wants"', html)
        self.assertIn('class="panel want-add-panel" id="add-want"', html)
        self.assertNotIn("Wishlist workspace", html)
        self.assertNotIn("content-grid wants-grid", html)
        self.assertLess(html.index('id="tracked-wants"'), html.index('id="add-want"'))
        self.assertLess(html.index("Tracked wants"), html.index("Add wanted card</span>"))
        self.assertIn("page=2", html)

    def test_empty_navigation_pages_offer_recovery_actions(self):
        user_id = app.create_user("emptyviews", "password123", "Empty Views")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))

        collection_html = app.render_collection(user, {})
        filtered_collection_html = app.render_collection(user, {"q": ["missing"]})
        browse_html = app.render_browse(user, {})
        trades_html = app.render_trades(user)
        wants_html = app.render_wants(user)
        members_html = app.render_members(user, {})

        self.assertIn("No cards in your collection yet.", collection_html)
        self.assertIn("Add card", collection_html)
        self.assertIn("No cards match this view.", filtered_collection_html)
        self.assertIn("Reset filters", filtered_collection_html)
        self.assertIn("No public trade cards are available yet.", browse_html)
        self.assertIn("No trades yet.", trades_html)
        self.assertIn("Find matches", trades_html)
        self.assertIn("No wanted cards yet.", wants_html)
        self.assertIn("Add a want", wants_html)
        self.assertIn('class="panel want-add-panel" id="add-want" open', wants_html)
        self.assertIn("No other members are available yet.", members_html)

    def test_trade_list_filters_and_paginates_offers(self):
        alice_id = factory.create_user("trade-list-alice", display_name="Trade List Alice")
        bob_id = factory.create_user("trade-list-bob", display_name="Trade List Bob")
        carol_id = factory.create_user("trade-list-carol", display_name="Trade List Carol")
        alice = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))
        wanted_trade_id = factory.create_trade(bob_id, alice_id, status="pending")
        factory.create_trade(alice_id, carol_id, status="accepted")
        for _ in range(11):
            factory.create_trade(carol_id, alice_id, status="completed")

        filtered_html = app.render_trades(
            alice,
            query={"direction": ["needs_action"], "status": ["pending"], "q": [str(wanted_trade_id)]},
        )
        paged_html = app.render_trades(alice, query={"per_page": ["10"], "page": ["1"]})

        self.assertIn(f"Trade #{wanted_trade_id}", filtered_html)
        self.assertNotIn("Trade List Carol", filtered_html)
        self.assertIn("Active filters", filtered_html)
        self.assertIn("Needs my response", filtered_html)
        self.assertIn("Showing 1-10 of 13", paged_html)
        self.assertIn('name="status"', paged_html)
        self.assertIn('name="direction"', paged_html)

    def test_wishlist_filters_trade_matches_and_paginates(self):
        user_id = factory.create_user("want-list-owner", display_name="Want List Owner")
        trader_id = factory.create_user("want-list-trader", display_name="Want List Trader")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        factory.create_want_item(user_id, "Matched Card", priority="urgent", type_line="Artifact")
        factory.create_want_item(user_id, "Unmatched Card", priority="urgent", type_line="Artifact")
        for index in range(10):
            factory.create_want_item(user_id, f"Other Want {index:02d}", priority="low")
        factory.create_collection_item(trader_id, "Matched Card", quantity_for_trade=1)

        matched_html = app.render_wants(user, query={"matched_only": ["1"], "priority": ["urgent"]})
        paged_html = app.render_wants(user, query={"per_page": ["10"], "page": ["1"]})
        matched_results_html = matched_html.split('<datalist id="want-search-suggestions">', 1)[0]

        self.assertIn("Matched Card", matched_results_html)
        self.assertNotIn("Unmatched Card", matched_results_html)
        self.assertIn("Trade matches only", matched_html)
        self.assertIn("Active filters", matched_html)
        self.assertIn("Showing 1-10 of 12", paged_html)
        self.assertIn('list="want-search-suggestions"', paged_html)
        self.assertIn('<datalist id="want-search-suggestions">', paged_html)

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

    def test_collection_bulk_route_redirects_with_counted_notice(self):
        user_id = factory.create_user("bulkroute", display_name="Bulk Route")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        token, _expires_at = app.create_session(user_id)
        card_id = factory.create_collection_item(user_id, "Sol Ring", quantity=4)

        class RouteHarness:
            location = ""

            def read_form(self):
                return {
                    "item_id": [str(card_id)],
                    "quantity": ["2"],
                    "redirect_to": ["/collection?q=Sol"],
                }

            def redirect(self, location):
                self.location = location

            def flash_notice(self, notice, status="success"):
                return app.set_session_flash(token, notice, status)

        harness = RouteHarness()
        app.collection_bulk_update(harness, user)
        parsed = app.urlparse(harness.location)
        query = app.parse_qs(parsed.query)
        notice, status = app.consume_session_flash(token)
        html = app.render_collection(user, query, notice=notice, status=status)
        next_notice, next_status = app.consume_session_flash(token)

        self.assertEqual(parsed.path, "/collection")
        self.assertEqual(query["q"], ["Sol"])
        self.assertEqual(notice, "Updated 1 selected card.")
        self.assertEqual(status, "success")
        self.assertEqual((next_notice, next_status), ("", "info"))
        self.assertIn("Updated 1 selected card.", html)
        self.assertNotIn("_notice", html)

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

    def test_bulk_add_collection_to_group_selected_and_matching_respects_user(self):
        alice_id = factory.create_user("alice", display_name="Alice")
        bob_id = factory.create_user("bob", display_name="Bob")
        binder_id = app.create_card_group(alice_id, "binder", "Bulk Binder")
        sol_id = factory.create_collection_item(alice_id, "Sol Ring", quantity=4)
        solitude_id = factory.create_collection_item(alice_id, "Solitude", quantity=2)
        pikachu_id = factory.create_collection_item(alice_id, "Pikachu", game="pokemon", quantity=1)
        bob_card_id = factory.create_collection_item(bob_id, "Sol Ring", quantity=4)

        selected = app.bulk_add_collection_items_to_group_by_ids(alice_id, binder_id, [sol_id, bob_card_id, "bad"], quantity=3)
        matching = app.bulk_add_collection_items_to_group_matching(
            alice_id,
            binder_id,
            {"q": "sol", "game": "mtg", "trade_only": False},
            quantity=1,
        )
        group_rows = app.rows(
            "SELECT collection_item_id, quantity FROM group_collection_items WHERE group_id = ? ORDER BY collection_item_id",
            (binder_id,),
        )

        self.assertEqual(selected, 1)
        self.assertEqual(matching, 2)
        self.assertEqual(
            [(item["collection_item_id"], item["quantity"]) for item in group_rows],
            [(sol_id, 1), (solitude_id, 1)],
        )
        self.assertNotIn(pikachu_id, [item["collection_item_id"] for item in group_rows])
        self.assertNotIn(bob_card_id, [item["collection_item_id"] for item in group_rows])

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

    def test_bulk_update_accepts_granular_visibility(self):
        user_id = factory.create_user("bulkprivacy", display_name="Bulk Privacy")
        card_id = factory.create_collection_item(user_id, "Trusted Bulk Card")

        quantity, quantity_for_trade, visibility = app.parse_bulk_collection_update({
            "quantity": [""],
            "quantity_for_trade": [""],
            "visibility": ["trusted"],
        })
        app.update_collection_items_by_ids(
            user_id,
            [card_id],
            quantity=quantity,
            quantity_for_trade=quantity_for_trade,
            is_public=visibility,
        )
        card = app.row("SELECT visibility, is_public FROM collection_items WHERE id = ?", (card_id,))

        self.assertEqual(visibility, "trusted")
        self.assertEqual(card["visibility"], "trusted")
        self.assertEqual(card["is_public"], 0)

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

    def test_want_bulk_route_redirects_with_counted_notice(self):
        user_id = factory.create_user("wantbulkroute", display_name="Want Bulk Route")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        token, _expires_at = app.create_session(user_id)
        want_id = factory.create_want_item(user_id, "Sol Ring")

        class RouteHarness:
            location = ""

            def read_form(self):
                return {
                    "want_id": [str(want_id)],
                    "redirect_to": ["/wants?q=Sol"],
                }

            def redirect(self, location):
                self.location = location

            def flash_notice(self, notice, status="success"):
                return app.set_session_flash(token, notice, status)

        harness = RouteHarness()
        app.want_bulk_delete(harness, user)
        parsed = app.urlparse(harness.location)
        query = app.parse_qs(parsed.query)
        notice, status = app.consume_session_flash(token)
        html = app.render_wants(user, query=query, notice=notice, status=status)
        next_notice, next_status = app.consume_session_flash(token)

        self.assertEqual(parsed.path, "/wants")
        self.assertEqual(query["q"], ["Sol"])
        self.assertEqual(notice, "Deleted 1 selected wanted card.")
        self.assertEqual(status, "success")
        self.assertEqual((next_notice, next_status), ("", "info"))
        self.assertIn("Deleted 1 selected wanted card.", html)
        self.assertNotIn("_notice", html)

    def test_bulk_want_update_group_and_delete_respect_user_and_filters(self):
        alice_id = factory.create_user("alice", display_name="Alice")
        bob_id = factory.create_user("bob", display_name="Bob")
        group_id = app.create_card_group(alice_id, "wishlist", "Bulk Wants")
        sol_id = factory.create_want_item(alice_id, "Sol Ring", priority="normal", desired_quantity=1)
        solitude_id = factory.create_want_item(alice_id, "Solitude", priority="normal", desired_quantity=1)
        pikachu_id = factory.create_want_item(alice_id, "Pikachu", game="pokemon", priority="low", desired_quantity=1)
        bob_want_id = factory.create_want_item(bob_id, "Sol Ring", priority="normal", desired_quantity=1)

        matching_updated = app.update_want_items_matching(
            alice_id,
            {"q": "sol", "priority": "", "game": "", "visibility": "", "matched_only": False},
            desired_quantity=2,
        )
        desired_quantity, priority, visibility = app.parse_bulk_want_update({
            "desired_quantity": ["3"],
            "priority": ["urgent"],
            "visibility": ["private"],
        })
        selected_updated = app.update_want_items_by_ids(
            alice_id,
            [sol_id, bob_want_id, "bad"],
            desired_quantity=desired_quantity,
            priority=priority,
            visibility=visibility,
        )
        matching_added = app.bulk_add_want_items_to_group_matching(
            alice_id,
            group_id,
            {"q": "sol", "priority": "", "game": "", "visibility": "", "matched_only": False},
        )
        deleted = app.delete_want_items_matching(
            alice_id,
            {"q": "", "priority": "low", "game": "pokemon", "visibility": "", "matched_only": False},
        )
        sol = app.row("SELECT * FROM want_items WHERE id = ?", (sol_id,))
        solitude = app.row("SELECT * FROM want_items WHERE id = ?", (solitude_id,))
        bob_want = app.row("SELECT * FROM want_items WHERE id = ?", (bob_want_id,))
        group_rows = app.rows("SELECT want_item_id FROM group_want_items WHERE group_id = ? ORDER BY want_item_id", (group_id,))

        self.assertEqual(matching_updated, 2)
        self.assertEqual(selected_updated, 1)
        self.assertEqual(matching_added, 2)
        self.assertEqual(deleted, 1)
        self.assertEqual(sol["desired_quantity"], 3)
        self.assertEqual(sol["priority"], "urgent")
        self.assertEqual(sol["visibility"], "private")
        self.assertEqual(solitude["desired_quantity"], 2)
        self.assertIsNone(app.row("SELECT * FROM want_items WHERE id = ?", (pikachu_id,)))
        self.assertEqual(bob_want["desired_quantity"], 1)
        self.assertEqual([item["want_item_id"] for item in group_rows], [sol_id, solitude_id])
