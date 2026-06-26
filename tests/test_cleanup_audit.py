"""Duplicate cleanup, condition/finish audit, and Scryfall finish-gate tests."""

from tests.base import *  # noqa: F401,F403


class CleanupAuditTests(BinderBridgeTestCase):
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
        app.execute("UPDATE collection_items SET condition_notes = 'Second copy has light whitening.' WHERE id = ?", (second_card_id,))
        app.add_collection_item_photo(
            alice_id,
            second_card_id,
            {
                "filename": "duplicate.png",
                "content_type": "image/png",
                "content": b"\x89PNG\r\n\x1a\nduplicate-photo",
            },
            "Duplicate row photo",
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
        history_html = app.render_price_history_panel(alice_id, second_card_id)
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
        photos = app.collection_item_photo_rows(first_card_id)

        self.assertEqual(result, {"groups": 1, "merged": 1})
        self.assertIn("/cleanup/collection", html)
        self.assertIn("Sol Ring", html)
        self.assertIn('id="cleanup-collection"', html)
        self.assertIn('id="cleanup-wants"', html)
        self.assertIn('data-workspace-tabs', html)
        self.assertIn('workspace-side-nav', html)
        cleanup_user = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))
        self.assertIn('data-active-section="cleanup-wants"', app.render_cleanup(cleanup_user, active_section="cleanup-wants"))
        self.assertIn('<table class="responsive-card-table price-history-table">', history_html)
        self.assertIn('data-label="Observed"', history_html)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["id"], first_card_id)
        self.assertEqual(cards[0]["quantity"], 5)
        self.assertEqual(cards[0]["quantity_for_trade"], 5)
        self.assertEqual(cards[0]["scryfall_id"], "scryfall-sol")
        self.assertEqual(cards[0]["is_public"], 1)
        self.assertEqual(cards[0]["condition_notes"], "Second copy has light whitening.")
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
        self.assertEqual(len(photos), 1)
        self.assertEqual(photos[0]["caption"], "Duplicate row photo")

    def test_want_duplicate_cleanup_merges_rows_and_group_links(self):
        user_id = app.create_user("wishlist", "password123", "Wishlist")
        timestamp = app.now_iso()
        first_want_id = app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, set_name, set_code, collector_number, desired_quantity,
                 priority, budget_cap_usd, condition, finish, language, scryfall_id,
                 preferred_printing_notes, notes, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Counterspell', 'Dominaria Remastered', 'DMR', '45', 1,
                    'high', '2.00', 'NM,LP', 'Regular,Foil', 'English', 'scryfall-counter',
                    'retro frame', 'first want', 0, ?, ?)
            """,
            (user_id, timestamp, timestamp),
        )
        second_want_id = app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, set_name, set_code, collector_number, desired_quantity,
                 priority, budget_cap_usd, condition, finish, language, scryfall_id, price_usd,
                 preferred_printing_notes, notes, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Counterspell', 'Dominaria Remastered', 'DMR', '45', 2,
                    'urgent', '1.50', 'NM,LP', 'Regular,Foil', 'English', 'scryfall-counter', '0.75',
                    'old border', 'second want', 1, ?, ?)
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
        self.assertEqual(wants[0]["priority"], "urgent")
        self.assertEqual(wants[0]["budget_cap_usd"], "1.50")
        self.assertEqual(wants[0]["is_public"], 1)
        self.assertIn("retro frame", wants[0]["preferred_printing_notes"])
        self.assertIn("old border", wants[0]["preferred_printing_notes"])
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
        self.assertIn('<table class="responsive-card-table audit-table">', html)
        self.assertIn('id="audit-results"', html)
        self.assertIn('id="audit-summary"', html)
        self.assertIn('data-workspace-tabs', html)
        self.assertIn('workspace-side-nav', html)
        self.assertIn('data-label="Condition"', html)
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
