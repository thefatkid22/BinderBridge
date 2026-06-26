"""Trade creation, fairness, counters, completion, and dispute tests."""

from tests.base import *  # noqa: F401,F403


class TradeWorkflowTests(BinderBridgeTestCase):
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
        collection_data = app.validate_collection_form({
            "card_name": ["Safe Card"],
            "quantity": ["1"],
            "quantity_for_trade": ["0"],
            "condition_notes": ["Edge wear\x00 and a small crease."],
        })
        self.assertEqual(collection_data["condition_notes"], "Edge wear and a small crease.")

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

        self.assertNotIn("\x00", app.decode_csv(csv_data))
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
        self.assertIn("trade-builder-steps", html)
        self.assertIn('data-workspace-tabs', html)
        self.assertIn('workspace-side-nav', html)
        self.assertIn('href="#trade-selected"', html)
        self.assertIn('id="trade-offer"', html)
        self.assertIn('id="trade-request"', html)

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
        self.assertIn('data-workspace-tabs', detail_html)
        self.assertIn('workspace-side-nav', detail_html)
        self.assertIn('id="trade-response"', detail_html)
        self.assertIn('href="#trade-cards"', detail_html)
        self.assertLess(detail_html.index('id="trade-response"'), detail_html.index('id="trade-cards"'))
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
        self.assertIn("Image preview", admin_html)
        self.assertIn('<table class="admin-table responsive-card-table admin-dispute-table">', admin_html)
        self.assertIn('data-label="Admin review"', admin_html)
        self.assertIn(f'/trades/{trade_id}/disputes/{dispute_id}/evidence/{evidence["id"]}', admin_html)

        second_id = app.add_trade_dispute_evidence(
            dispute_id,
            bob_id,
            {"filename": "chat.txt", "content": b"Seller confirmed <replacement>.", "content_type": "text/plain"},
            "Chat excerpt.",
            trade_id=trade_id,
        )
        admin_html = app.render_admin_trade_disputes(admin, {"status": ["open"], "q": [str(trade_id)]})
        self.assertTrue(second_id)
        self.assertEqual(app.row("SELECT COUNT(*) AS count FROM trade_dispute_evidence WHERE dispute_id = ?", (dispute_id,))["count"], 2)
        self.assertIn("Text preview", admin_html)
        self.assertIn("Seller confirmed &lt;replacement&gt;.", admin_html)
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

    def test_data_retention_controls_prune_only_eligible_old_records(self):
        admin_id = app.create_user("retentionadmin", "password123", "Retention Admin", is_admin=True)
        user_id = app.create_user("retentionuser", "password123", "Retention User", is_admin=False)
        old_at = (datetime.now(timezone.utc) - timedelta(days=60)).replace(microsecond=0).isoformat()
        recent_at = app.now_iso()

        old_read_notification = app.execute(
            "INSERT INTO user_notifications (user_id, kind, title, is_read, created_at) VALUES (?, 'admin_notice', 'Old read', 1, ?)",
            (user_id, old_at),
        )
        old_unread_notification = app.execute(
            "INSERT INTO user_notifications (user_id, kind, title, is_read, created_at) VALUES (?, 'admin_notice', 'Old unread', 0, ?)",
            (user_id, old_at),
        )
        recent_read_notification = app.execute(
            "INSERT INTO user_notifications (user_id, kind, title, is_read, created_at) VALUES (?, 'admin_notice', 'Recent read', 1, ?)",
            (user_id, recent_at),
        )
        old_log = app.log_admin_action(admin_id, "admin_notes_updated", user_id, "user", "Retention User", "Old log")
        recent_log = app.log_admin_action(admin_id, "admin_notes_updated", user_id, "user", "Retention User", "Recent log")
        app.execute("UPDATE admin_audit_log SET created_at = ? WHERE id = ?", (old_at, old_log))

        webhook_id = app.create_webhook_endpoint(user_id, "Retention webhook", "https://example.com/hook")["id"]
        old_sent_delivery = app.execute(
            """
            INSERT INTO webhook_deliveries
                (webhook_id, user_id, event_type, payload_json, status, created_at, completed_at)
            VALUES (?, ?, 'notification.created', '{}', 'sent', ?, ?)
            """,
            (webhook_id, user_id, old_at, old_at),
        )
        old_pending_delivery = app.execute(
            """
            INSERT INTO webhook_deliveries
                (webhook_id, user_id, event_type, payload_json, status, created_at)
            VALUES (?, ?, 'notification.created', '{}', 'pending', ?)
            """,
            (webhook_id, user_id, old_at),
        )
        recent_failed_delivery = app.execute(
            """
            INSERT INTO webhook_deliveries
                (webhook_id, user_id, event_type, payload_json, status, created_at, completed_at)
            VALUES (?, ?, 'notification.created', '{}', 'failed', ?, ?)
            """,
            (webhook_id, user_id, recent_at, recent_at),
        )

        trade_id = factory.create_trade(admin_id, user_id, status="completed")
        resolved_dispute = app.execute(
            """
            INSERT INTO trade_disputes
                (trade_id, reporter_id, category, status, body, resolved_at, created_at, updated_at)
            VALUES (?, ?, 'other', 'resolved', 'Resolved issue', ?, ?, ?)
            """,
            (trade_id, user_id, old_at, old_at, old_at),
        )
        open_dispute = app.execute(
            """
            INSERT INTO trade_disputes
                (trade_id, reporter_id, category, status, body, created_at, updated_at)
            VALUES (?, ?, 'other', 'open', 'Open issue', ?, ?)
            """,
            (trade_id, user_id, old_at, old_at),
        )
        resolved_evidence = app.execute(
            """
            INSERT INTO trade_dispute_evidence
                (dispute_id, uploaded_by_user_id, original_filename, content_type, file_size, checksum_sha256, content, created_at)
            VALUES (?, ?, 'resolved.txt', 'text/plain', 8, 'resolved', ?, ?)
            """,
            (resolved_dispute, user_id, b"resolved", old_at),
        )
        open_evidence = app.execute(
            """
            INSERT INTO trade_dispute_evidence
                (dispute_id, uploaded_by_user_id, original_filename, content_type, file_size, checksum_sha256, content, created_at)
            VALUES (?, ?, 'open.txt', 'text/plain', 4, 'open', ?, ?)
            """,
            (open_dispute, user_id, b"open", old_at),
        )

        revoked_token = app.create_api_token(user_id, "Old revoked token", ["read"])
        active_token = app.create_api_token(user_id, "Old active token", ["read"])
        app.revoke_api_token(user_id, revoked_token["id"])
        app.execute("UPDATE api_tokens SET created_at = ?, revoked_at = ? WHERE id = ?", (old_at, old_at, revoked_token["id"]))
        app.execute("UPDATE api_tokens SET created_at = ? WHERE id = ?", (old_at, active_token["id"]))

        old_invite = app.create_registration_invite(admin_id, "oldinvite@example.com", "http://binder.test")
        pending_invite = app.create_registration_invite(admin_id, "pendinginvite@example.com", "http://binder.test")
        app.revoke_registration_invite(admin_id, old_invite["id"])
        app.execute("UPDATE registration_invites SET created_at = ?, updated_at = ? WHERE id = ?", (old_at, old_at, old_invite["id"]))
        future_invite_expiry = (datetime.now(timezone.utc) + timedelta(days=60)).replace(microsecond=0).isoformat()
        app.execute(
            "UPDATE registration_invites SET created_at = ?, updated_at = ?, expires_at = ? WHERE id = ?",
            (old_at, old_at, future_invite_expiry, pending_invite["id"]),
        )

        settings = app.set_data_retention_settings("30", "30", "30", "30", "30", "30")
        status = app.data_retention_status()
        result = app.prune_data_retention_records(settings)
        html = app.render_admin_health(app.row("SELECT * FROM users WHERE id = ?", (admin_id,)))

        self.assertEqual(status["eligible"]["total"], 6)
        self.assertEqual(result["notifications"], 1)
        self.assertEqual(result["admin_logs"], 1)
        self.assertEqual(result["webhook_deliveries"], 1)
        self.assertEqual(result["dispute_evidence"], 1)
        self.assertEqual(result["api_tokens"], 1)
        self.assertEqual(result["registration_invites"], 1)
        self.assertIsNone(app.row("SELECT id FROM user_notifications WHERE id = ?", (old_read_notification,)))
        self.assertIsNotNone(app.row("SELECT id FROM user_notifications WHERE id = ?", (old_unread_notification,)))
        self.assertIsNotNone(app.row("SELECT id FROM user_notifications WHERE id = ?", (recent_read_notification,)))
        self.assertIsNone(app.row("SELECT id FROM admin_audit_log WHERE id = ?", (old_log,)))
        self.assertIsNotNone(app.row("SELECT id FROM admin_audit_log WHERE id = ?", (recent_log,)))
        self.assertIsNone(app.row("SELECT id FROM webhook_deliveries WHERE id = ?", (old_sent_delivery,)))
        self.assertIsNotNone(app.row("SELECT id FROM webhook_deliveries WHERE id = ?", (old_pending_delivery,)))
        self.assertIsNotNone(app.row("SELECT id FROM webhook_deliveries WHERE id = ?", (recent_failed_delivery,)))
        self.assertIsNone(app.row("SELECT id FROM trade_dispute_evidence WHERE id = ?", (resolved_evidence,)))
        self.assertIsNotNone(app.row("SELECT id FROM trade_dispute_evidence WHERE id = ?", (open_evidence,)))
        self.assertIsNone(app.row("SELECT id FROM api_tokens WHERE id = ?", (revoked_token["id"],)))
        self.assertIsNotNone(app.row("SELECT id FROM api_tokens WHERE id = ?", (active_token["id"],)))
        self.assertIsNone(app.row("SELECT id FROM registration_invites WHERE id = ?", (old_invite["id"],)))
        self.assertIsNotNone(app.row("SELECT id FROM registration_invites WHERE id = ?", (pending_invite["id"],)))
        self.assertIn("Last cleanup:", html)
        self.assertIn("0 eligible", html)

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
