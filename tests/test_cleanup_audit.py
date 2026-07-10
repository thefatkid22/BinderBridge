"""Duplicate cleanup, collection audit, and Scryfall finish-gate tests."""

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
        count_summary = app.duplicate_cleanup_count_summary(alice_id)
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
        self.assertEqual(count_summary["collection_duplicate_groups"], 1)
        self.assertEqual(count_summary["collection_duplicate_rows"], 1)
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

    def test_cleanup_and_audit_empty_states_offer_next_actions(self):
        user_id = app.create_user("clearempty", "password123", "Clear Empty")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))

        cleanup_html = app.render_cleanup(user)
        audit_html = app.render_condition_finish_audit(user, {})

        self.assertIn("empty-action-state", cleanup_html)
        self.assertIn("No exact collection duplicates found.", cleanup_html)
        self.assertIn("Review collection", cleanup_html)
        self.assertIn("No exact wanted-card duplicates found.", cleanup_html)
        self.assertIn("Review wishlist", cleanup_html)
        self.assertIn("No collection cards need condition or finish cleanup.", audit_html)
        self.assertIn("Import cards", audit_html)

    def test_audit_workspace_navigation_uses_shared_section_definitions(self):
        user_id = app.create_user("auditnav", "password123", "Audit Nav")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        timestamp = app.now_iso()
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, condition, finish, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Audit Nav Collection', 'NM', 'Regular', 1, 0, ?, ?)
            """,
            (user_id, timestamp, timestamp),
        )
        app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, desired_quantity, priority, condition, finish, language, created_at, updated_at)
            VALUES (?, 'mtg', 'Audit Nav Wishlist', 1, 'normal', 'NM', 'Regular', 'English', ?, ?)
            """,
            (user_id, timestamp, timestamp),
        )

        html = app.render_condition_finish_audit(user, {}, active_section=app.AUDIT_SECTION_WISHLIST_SCRYFALL)
        expected_items = tuple(
            (f'#{section["id"]}', section["label"], section["detail"])
            for section in app.AUDIT_SECTION_DEFINITIONS
        )
        counted_items = app.audit_workspace_items({
            app.AUDIT_SECTION_COLLECTION_SCRYFALL: 1,
            app.AUDIT_SECTION_WISHLIST_SCRYFALL: 1,
        })

        self.assertEqual(app.audit_workspace_items(), expected_items)
        self.assertEqual(counted_items[1], ("#collection-scryfall", "Collection Scryfall", "Queue missing card data", "1"))
        self.assertEqual(counted_items[2], ("#wishlist-scryfall", "Wishlist Scryfall", "Enhance wanted cards", "1"))
        self.assertEqual(tuple(section["id"] for section in app.AUDIT_SECTION_DEFINITIONS), app.AUDIT_SECTION_IDS)
        self.assertEqual(app.audit_section_id("not-real"), app.AUDIT_DEFAULT_SECTION)
        self.assertIn(f'data-active-section="{app.AUDIT_SECTION_WISHLIST_SCRYFALL}"', html)
        self.assertEqual(html.count('class="workspace-nav-badge">1</em>'), 2)
        self.assertIn('class="workspace-nav-badge">2</em>', html)
        for section in app.AUDIT_SECTION_DEFINITIONS:
            self.assertIn(f'href="#{section["id"]}"', html)
            self.assertIn(f'id="{section["id"]}"', html)
            self.assertIn(app.e(section["label"]), html)
            self.assertIn(app.e(section["detail"]), html)
            self.assertEqual(app.audit_section_path(section["id"]), f'/cleanup/audit#{section["id"]}')
        self.assertIn(
            f'name="redirect_to" value="/cleanup/audit#{app.AUDIT_SECTION_COLLECTION_SCRYFALL}"',
            html,
        )
        self.assertIn(
            f'name="redirect_to" value="/cleanup/audit#{app.AUDIT_SECTION_WISHLIST_SCRYFALL}"',
            html,
        )

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
        self.assertIn("Collection audit", html)
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

    def test_scryfall_enhancement_audit_queues_missing_collection_cards(self):
        user_id = app.create_user("scryfallaudit", "password123", "Scryfall Audit")
        other_id = app.create_user("otherscryfallaudit", "password123", "Other Scryfall Audit")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        timestamp = app.now_iso()
        missing_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_code, collector_number, condition, finish,
                 quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Missing Metadata', 'TST', '1', 'NM', 'Regular', 1, 0, ?, ?)
            """,
            (user_id, timestamp, timestamp),
        )
        partial_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_code, collector_number, scryfall_id,
                 condition, finish, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Partial Metadata', 'TST', '2', 'partial-card',
                    'NM', 'Regular', 1, 0, ?, ?)
            """,
            (user_id, timestamp, timestamp),
        )
        complete_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_code, collector_number, scryfall_id, image_url,
                 type_line, scryfall_uri, condition, finish, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Complete Metadata', 'TST', '3', 'complete-card', 'https://cards.example/complete.jpg',
                    'Artifact', 'https://scryfall.example/complete', 'NM', 'Regular', 1, 0, ?, ?)
            """,
            (user_id, timestamp, timestamp),
        )
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, condition, finish, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'pokemon', 'Other Game Card', 'NM', 'Regular', 1, 0, ?, ?)
            """,
            (user_id, timestamp, timestamp),
        )
        other_user_missing_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, condition, finish, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Other User Missing', 'NM', 'Regular', 1, 0, ?, ?)
            """,
            (other_id, timestamp, timestamp),
        )

        summary = app.scryfall_enhancement_audit_summary(user_id)
        audit_rows = app.scryfall_enhancement_audit_rows(user_id)
        html = app.render_condition_finish_audit(user, {})
        queued_selected = app.queue_scryfall_enhancement_by_ids(user_id, [missing_id, complete_id, other_user_missing_id])
        queued_all = app.queue_scryfall_enhancement_matching(user_id)
        jobs = app.rows("SELECT * FROM scryfall_enrichment_jobs WHERE user_id = ? ORDER BY collection_item_id", (user_id,))

        self.assertEqual(summary, {"missing": 2, "queued": 0})
        self.assertEqual([item["id"] for item in audit_rows], [missing_id, partial_id])
        self.assertEqual(audit_rows[0]["missing_scryfall_labels"], ["Scryfall ID", "image", "type line", "Scryfall link"])
        self.assertEqual(audit_rows[1]["missing_scryfall_labels"], ["image", "type line", "Scryfall link"])
        self.assertIn('id="collection-scryfall"', html)
        self.assertIn('id="wishlist-scryfall"', html)
        self.assertIn("Collection Scryfall", html)
        self.assertIn("Wishlist Scryfall", html)
        self.assertIn("/cleanup/audit/scryfall", html)
        self.assertIn("/cleanup/audit/scryfall-delete", html)
        self.assertIn("Queue all missing", html)
        self.assertIn("Delete selected", html)
        self.assertEqual(queued_selected, 1)
        self.assertEqual(queued_all, 2)
        self.assertEqual([job["collection_item_id"] for job in jobs], [missing_id, partial_id])
        self.assertEqual([job["status"] for job in jobs], ["pending", "pending"])

    def test_scryfall_enhancement_audit_delete_removes_selected_user_cards(self):
        user_id = app.create_user("scryfalldelete", "password123", "Scryfall Delete")
        other_id = app.create_user("otherscryfalldelete", "password123", "Other Scryfall Delete")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        timestamp = app.now_iso()
        card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, condition, finish, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Delete Missing Metadata', 'NM', 'Regular', 1, 0, ?, ?)
            """,
            (user_id, timestamp, timestamp),
        )
        other_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, condition, finish, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Other User Missing Metadata', 'NM', 'Regular', 1, 0, ?, ?)
            """,
            (other_id, timestamp, timestamp),
        )
        captured = {}

        class Harness:
            def read_form(self):
                return {
                    "item_id": [str(card_id), str(other_card_id)],
                    "redirect_to": ["/cleanup/audit#collection-scryfall"],
                }

            def condition_finish_audit_query_from_form(self, form):
                return app.condition_finish_audit_query_from_form(self, form)

            def condition_finish_audit_page(self, page_user, query, notice=None, status="info", active_section=""):
                captured.update({
                    "user_id": page_user["id"],
                    "query": query,
                    "notice": notice,
                    "status": status,
                    "active_section": active_section,
                })
                return "deleted"

        response = app.condition_finish_audit_scryfall_delete(Harness(), user)
        deleted_card = app.row("SELECT * FROM collection_items WHERE id = ?", (card_id,))
        other_card = app.row("SELECT * FROM collection_items WHERE id = ?", (other_card_id,))

        self.assertEqual(response, "deleted")
        self.assertIsNone(deleted_card)
        self.assertIsNotNone(other_card)
        self.assertEqual(captured["user_id"], user_id)
        self.assertEqual(captured["notice"], "Deleted 1 selected collection card.")
        self.assertEqual(captured["active_section"], "collection-scryfall")

    def test_wishlist_scryfall_audit_enhances_missing_want_cards(self):
        user_id = app.create_user("wantscryfall", "password123", "Want Scryfall")
        other_id = app.create_user("otherwantscryfall", "password123", "Other Want Scryfall")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        timestamp = app.now_iso()
        app.store_scryfall_bulk_cards([
            {
                "object": "card",
                "id": "missing-want-card",
                "name": "Missing Want Metadata",
                "set_name": "Test Set",
                "set": "tst",
                "collector_number": "10",
                "type_line": "Artifact",
                "image_uris": {"small": "https://cards.example/missing.jpg"},
                "scryfall_uri": "https://scryfall.example/missing",
                "prices": {"usd": "1.25"},
            },
            {
                "object": "card",
                "id": "partial-want-card",
                "name": "Partial Want Metadata",
                "set_name": "Test Set",
                "set": "tst",
                "collector_number": "11",
                "type_line": "Instant",
                "image_uris": {"small": "https://cards.example/partial.jpg"},
                "scryfall_uri": "https://scryfall.example/partial",
                "prices": {"usd": "0.75"},
            },
        ])
        missing_id = app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, set_code, collector_number, desired_quantity,
                 priority, condition, finish, language, created_at, updated_at)
            VALUES (?, 'mtg', 'Missing Want Metadata', 'TST', '10', 2,
                    'high', 'NM', 'Regular', 'English', ?, ?)
            """,
            (user_id, timestamp, timestamp),
        )
        partial_id = app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, set_code, collector_number, scryfall_id,
                 desired_quantity, priority, condition, finish, language, created_at, updated_at)
            VALUES (?, 'mtg', 'Partial Want Metadata', 'TST', '11', 'partial-want-card',
                    1, 'normal', 'NM', 'Regular', 'English', ?, ?)
            """,
            (user_id, timestamp, timestamp),
        )
        complete_id = app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, set_code, collector_number, scryfall_id, image_url,
                 type_line, scryfall_uri, desired_quantity, priority, condition, finish, language, created_at, updated_at)
            VALUES (?, 'mtg', 'Complete Want Metadata', 'TST', '12', 'complete-want-card',
                    'https://cards.example/complete.jpg', 'Creature', 'https://scryfall.example/complete',
                    1, 'normal', 'NM', 'Regular', 'English', ?, ?)
            """,
            (user_id, timestamp, timestamp),
        )
        app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, desired_quantity, priority, condition, finish, language, created_at, updated_at)
            VALUES (?, 'pokemon', 'Other Game Want', 1, 'normal', 'NM', 'Regular', 'English', ?, ?)
            """,
            (user_id, timestamp, timestamp),
        )
        other_user_want_id = app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, desired_quantity, priority, condition, finish, language, created_at, updated_at)
            VALUES (?, 'mtg', 'Other User Want', 1, 'normal', 'NM', 'Regular', 'English', ?, ?)
            """,
            (other_id, timestamp, timestamp),
        )

        summary = app.want_scryfall_enhancement_audit_summary(user_id)
        audit_rows = app.want_scryfall_enhancement_audit_rows(user_id)
        html = app.render_condition_finish_audit(user, {})
        selected_result = app.enhance_want_scryfall_by_ids(user_id, [missing_id, complete_id, other_user_want_id])
        all_result = app.enhance_want_scryfall_matching(user_id)
        missing_want = app.row("SELECT * FROM want_items WHERE id = ?", (missing_id,))
        partial_want = app.row("SELECT * FROM want_items WHERE id = ?", (partial_id,))
        complete_want = app.row("SELECT * FROM want_items WHERE id = ?", (complete_id,))
        remaining_summary = app.want_scryfall_enhancement_audit_summary(user_id)

        self.assertEqual(summary, {"missing": 2})
        self.assertEqual([item["id"] for item in audit_rows], [missing_id, partial_id])
        self.assertEqual(audit_rows[0]["missing_scryfall_labels"], ["Scryfall ID", "image", "type line", "Scryfall link"])
        self.assertIn("/cleanup/audit/wishlist-scryfall", html)
        self.assertIn("/cleanup/audit/wishlist-scryfall-delete", html)
        self.assertIn("Enhance all missing", html)
        self.assertEqual(selected_result, {"checked": 1, "enhanced": 1, "missing": 0})
        self.assertEqual(all_result, {"checked": 1, "enhanced": 1, "missing": 0})
        self.assertEqual(missing_want["scryfall_id"], "missing-want-card")
        self.assertEqual(missing_want["image_url"], "https://cards.example/missing.jpg")
        self.assertEqual(missing_want["type_line"], "Artifact")
        self.assertEqual(missing_want["price_usd"], "1.25")
        self.assertEqual(missing_want["price_source"], "scryfall")
        self.assertEqual(partial_want["image_url"], "https://cards.example/partial.jpg")
        self.assertEqual(partial_want["type_line"], "Instant")
        self.assertEqual(complete_want["scryfall_id"], "complete-want-card")
        self.assertEqual(remaining_summary, {"missing": 0})

    def test_wishlist_scryfall_audit_delete_removes_selected_user_wants(self):
        user_id = app.create_user("wantscryfalldelete", "password123", "Want Scryfall Delete")
        other_id = app.create_user("otherwantscryfalldelete", "password123", "Other Want Scryfall Delete")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        timestamp = app.now_iso()
        want_id = app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, desired_quantity, priority, condition, finish, language, created_at, updated_at)
            VALUES (?, 'mtg', 'Delete Wanted Metadata', 1, 'normal', 'NM', 'Regular', 'English', ?, ?)
            """,
            (user_id, timestamp, timestamp),
        )
        other_want_id = app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, desired_quantity, priority, condition, finish, language, created_at, updated_at)
            VALUES (?, 'mtg', 'Other Delete Wanted Metadata', 1, 'normal', 'NM', 'Regular', 'English', ?, ?)
            """,
            (other_id, timestamp, timestamp),
        )
        captured = {}

        class Harness:
            def read_form(self):
                return {
                    "item_id": [str(want_id), str(other_want_id)],
                    "redirect_to": ["/cleanup/audit#wishlist-scryfall"],
                }

            def condition_finish_audit_query_from_form(self, form):
                return app.condition_finish_audit_query_from_form(self, form)

            def condition_finish_audit_page(self, page_user, query, notice=None, status="info", active_section=""):
                captured.update({
                    "user_id": page_user["id"],
                    "query": query,
                    "notice": notice,
                    "status": status,
                    "active_section": active_section,
                })
                return "deleted"

        response = app.condition_finish_audit_want_scryfall_delete(Harness(), user)
        deleted_want = app.row("SELECT * FROM want_items WHERE id = ?", (want_id,))
        other_want = app.row("SELECT * FROM want_items WHERE id = ?", (other_want_id,))

        self.assertEqual(response, "deleted")
        self.assertIsNone(deleted_want)
        self.assertIsNotNone(other_want)
        self.assertEqual(captured["user_id"], user_id)
        self.assertEqual(captured["notice"], "Deleted 1 selected wanted card.")
        self.assertEqual(captured["active_section"], "wishlist-scryfall")

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
