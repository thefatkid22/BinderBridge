"""CSV import, Scryfall, pricing, and card-photo tests."""

from tests.base import *  # noqa: F401,F403


class ImportsScryfallTests(BinderBridgeTestCase):
    def test_manabox_csv_import_normalizes_common_columns(self):
        user_id = app.create_user("collector", "password123", "Collector")
        csv_bytes = (
            "Name,Set code,Set name,Collector number,Foil,Quantity,Condition,Language,Scryfall ID,Price,Price source,Cardmarket Product ID,Card Kingdom SKU\n"
            "Sol Ring,CMM,Commander Masters,703,false,2,Near Mint,English,abc-123,1.25,Scryfall,222,CK-333\n"
            "Counterspell,DMR,Dominaria Remastered,45,true,1,Lightly Played,en,,0.50,TCGplayer,555,CK-666\n"
        ).encode("utf-8")

        result = app.import_collection_csv(user_id, csv_bytes, source="manabox", enrich_scryfall=False)
        cards = app.rows("SELECT * FROM collection_items WHERE user_id = ? ORDER BY card_name", (user_id,))

        self.assertEqual(result["inserted"], 2)
        self.assertEqual(cards[0]["finish"], "Foil")
        self.assertEqual(cards[0]["condition"], "LP")
        self.assertEqual(cards[0]["set_code"], "DMR")
        self.assertEqual(cards[0]["price_usd"], "")
        self.assertEqual(cards[0]["price_source"], "")
        self.assertEqual(cards[0]["cardmarket_product_id"], "555")
        self.assertEqual(cards[0]["cardkingdom_sku"], "CK-666")
        self.assertEqual(cards[1]["finish"], "Regular")
        self.assertEqual(cards[1]["scryfall_id"], "abc-123")
        self.assertEqual(cards[1]["price_usd"], "")
        self.assertEqual(cards[1]["price_source"], "")
        self.assertEqual(cards[1]["cardmarket_product_id"], "222")

    def test_archidekt_csv_import_merges_duplicates(self):
        user_id = app.create_user("drafter", "password123", "Drafter")
        csv_bytes = (
            "Quantity,Name,Edition,Collector Number,Foil,Condition,Language\n"
            "1,Lightning Bolt,Secret Lair,182,Foil,NM,English\n"
            "2,Lightning Bolt,Secret Lair,182,Foil,NM,English\n"
        ).encode("utf-8")

        result = app.import_collection_csv(user_id, csv_bytes, source="archidekt", enrich_scryfall=False)
        card = app.row("SELECT * FROM collection_items WHERE user_id = ?", (user_id,))

        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(card["quantity"], 3)

    def test_deckbox_source_profile_auto_detects_collection_columns(self):
        user_id = app.create_user("deckboxer", "password123", "Deckboxer")
        csv_bytes = (
            "Count,Tradelist Count,Name,Edition,Card Number,Condition,Language,Foil,Notes\n"
            "3,2,Sol Ring,Commander Masters,703,Near Mint,English,false,Trade binder\n"
        ).encode("utf-8")

        preview = app.preview_collection_import_csv(user_id, csv_bytes, source="auto", enrich_scryfall=False)
        result = app.commit_collection_import_preview(user_id, preview["batch_id"])
        card = app.row("SELECT * FROM collection_items WHERE user_id = ?", (user_id,))

        self.assertEqual(
            app.detect_csv_import_profile(
                ["Count", "Tradelist Count", "Name", "Edition", "Card Number"],
                "collection",
            ),
            "deckbox",
        )
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(card["card_name"], "Sol Ring")
        self.assertEqual(card["collector_number"], "703")
        self.assertEqual(card["quantity"], 3)
        self.assertEqual(card["quantity_for_trade"], 2)
        self.assertEqual(card["notes"], "Trade binder")

    def test_dragonshield_source_profile_maps_collection_and_deck_exports(self):
        csv_bytes = (
            "Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,Printing,Condition,Language\n"
            "Sideboard,2,1,Dispel,BFZ,Battle for Zendikar,76,Foil,Lightly Played,English\n"
        ).encode("utf-8")

        items, warnings = app.normalize_csv_rows(csv_bytes, source="dragonshield")
        section_rows, deck_warnings = app.normalize_csv_rows_by_section(csv_bytes, source="dragonshield")

        self.assertEqual(warnings, [])
        self.assertEqual(deck_warnings, [])
        self.assertEqual(items[0]["card_name"], "Dispel")
        self.assertEqual(items[0]["quantity_for_trade"], 1)
        self.assertEqual(items[0]["finish"], "Foil")
        self.assertEqual(items[0]["condition"], "LP")
        self.assertEqual(section_rows["sideboard"][0]["set_code"], "BFZ")

    def test_deckstats_source_profile_auto_detects_sections(self):
        csv_bytes = (
            "amount,name,set_code,set_name,collector_number,is_foil,section\n"
            "1,Sol Ring,CMM,Commander Masters,703,0,main\n"
            "2,Dispel,BFZ,Battle for Zendikar,76,1,sideboard\n"
        ).encode("utf-8")

        section_rows, warnings = app.normalize_csv_rows_by_section(csv_bytes, source="auto")

        self.assertEqual(
            app.detect_csv_import_profile(
                ["amount", "name", "set_code", "set_name", "collector_number", "is_foil", "section"],
                "deck",
            ),
            "deckstats",
        )
        self.assertEqual(section_rows["main"][0]["card_name"], "Sol Ring")
        self.assertEqual(section_rows["sideboard"][0]["quantity"], 2)
        self.assertEqual(section_rows["sideboard"][0]["finish"], "Foil")
        self.assertTrue(app.deck_import_sections_need_review(section_rows))

    def test_import_source_profile_options_are_visible(self):
        user_id = app.create_user("profileviewer", "password123", "Profile Viewer")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        deck_id = app.create_card_group(user_id, "deck", "Profile deck")

        collection_html = app.render_import(user)
        deck_html = app.render_group_detail(user, deck_id)

        self.assertIn('value="deckbox"', collection_html)
        self.assertIn('value="dragonshield"', collection_html)
        self.assertIn('value="deckstats"', deck_html)
        self.assertIn('value="tappedout"', deck_html)

    def test_custom_csv_import_mapping_preset_applies_to_collection_import(self):
        user_id = app.create_user("mapper", "password123", "Mapper")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        preset = app.save_csv_import_mapping_preset(
            user_id,
            "Store export",
            {
                "name": "CardTitle",
                "quantity": "Owned",
                "trade": "TradeQty",
                "set_code": "EditionCode",
                "collector_number": "No",
                "finish": "FoilFlag",
                "condition": "Grade",
            },
            import_target="collection",
        )
        csv_bytes = (
            "CardTitle,Owned,TradeQty,EditionCode,No,FoilFlag,Grade\n"
            "Rhystic Study,3,2,WOT,25,foil,Lightly Played\n"
        ).encode("utf-8")
        mapping = app.csv_import_mapping_for_user(user_id, preset["id"], import_target="collection")

        preview = app.preview_collection_import_csv(user_id, csv_bytes, source="auto", enrich_scryfall=False, field_mapping=mapping)
        result = app.import_collection_csv(user_id, csv_bytes, source="auto", enrich_scryfall=False, field_mapping=mapping)
        card = app.row("SELECT * FROM collection_items WHERE user_id = ?", (user_id,))
        import_html = app.render_import(user)

        self.assertEqual(preview["inserted"], 1)
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(card["card_name"], "Rhystic Study")
        self.assertEqual(card["quantity"], 3)
        self.assertEqual(card["quantity_for_trade"], 2)
        self.assertEqual(card["set_code"], "WOT")
        self.assertEqual(card["collector_number"], "25")
        self.assertEqual(card["finish"], "Foil")
        self.assertEqual(card["condition"], "LP")
        self.assertIn("Store export", import_html)
        self.assertIn("CSV mapping presets", import_html)

    def test_admin_shared_csv_mapping_preset_is_visible_and_deletable_by_admin(self):
        admin_id = app.create_user("adminmap", "password123", "Admin Map", is_admin=True)
        user_id = app.create_user("regularmap", "password123", "Regular Map", is_admin=False)
        shared = app.save_csv_import_mapping_preset(
            admin_id,
            "Shared deck builder",
            {"name": "Title", "quantity": "Count", "section": "Board"},
            import_target="deck",
            is_shared=True,
            is_admin=True,
        )

        visible_to_user = app.csv_import_preset_rows_for_user(user_id, "deck")
        mapping = app.csv_import_mapping_for_user(user_id, shared["id"], import_target="deck")

        self.assertIn(shared["id"], [preset["id"] for preset in visible_to_user])
        self.assertEqual(mapping["name"], ["Title"])
        self.assertEqual(mapping["section"], ["Board"])
        with self.assertRaises(ValueError):
            app.delete_csv_import_mapping_preset(user_id, shared["id"], is_admin=False)
        self.assertEqual(app.delete_csv_import_mapping_preset(admin_id, shared["id"], is_admin=True), 1)
        self.assertIsNone(app.csv_import_preset_for_user(user_id, shared["id"], import_target="deck"))

    def test_deck_csv_import_mapping_preset_handles_custom_section_column(self):
        user_id = app.create_user("deckmapper", "password123", "Deck Mapper")
        mapping = {
            "name": ["CardTitle"],
            "quantity": ["Qty"],
            "section": ["BoardName"],
        }
        csv_bytes = (
            "BoardName,CardTitle,Qty\n"
            "Main,Sol Ring,1\n"
            "Maybeboard,Mana Crypt,1\n"
        ).encode("utf-8")

        section_rows, warnings = app.normalize_csv_rows_by_section(csv_bytes, field_mapping=mapping)

        self.assertEqual(warnings, [])
        self.assertEqual(section_rows["main"][0]["card_name"], "Sol Ring")
        self.assertEqual(section_rows["maybeboard"][0]["card_name"], "Mana Crypt")
        self.assertTrue(app.deck_import_sections_need_review(section_rows))

    def test_collection_import_preview_commits_and_undoes_batch(self):
        user_id = app.create_user("previewer", "password123", "Previewer")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, set_code, collector_number,
                 finish, condition, language, quantity, quantity_for_trade, notes, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 'CMM', '703',
                    'Regular', 'NM', 'English', 1, 0, 'original note', ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        csv_bytes = (
            "Name,Quantity,Trade,Set Name,Set Code,Collector Number,Foil,Condition,Language\n"
            "Sol Ring,2,1,Commander Masters,CMM,703,false,NM,English\n"
            "Lightning Bolt,1,1,Secret Lair,SLD,182,true,NM,English\n"
        ).encode("utf-8")

        preview = app.preview_collection_import_csv(user_id, csv_bytes, source="manabox", enrich_scryfall=False)
        preview_batch = app.row("SELECT * FROM import_batches WHERE id = ?", (preview["batch_id"],))
        before_commit = app.rows("SELECT * FROM collection_items WHERE user_id = ? ORDER BY card_name", (user_id,))
        preview_html = app.render_import(user, preview=preview)
        result = app.commit_collection_import_preview(user_id, preview["batch_id"])
        after_commit = app.rows("SELECT * FROM collection_items WHERE user_id = ? ORDER BY card_name", (user_id,))
        undo = app.undo_import_batch(user_id, preview["batch_id"])
        after_undo = app.rows("SELECT * FROM collection_items WHERE user_id = ? ORDER BY card_name", (user_id,))
        undone_batch = app.row("SELECT * FROM import_batches WHERE id = ?", (preview["batch_id"],))

        self.assertEqual(preview["inserted"], 1)
        self.assertEqual(preview["updated"], 1)
        self.assertEqual(preview_batch["status"], "preview")
        self.assertEqual([item["card_name"] for item in before_commit], ["Sol Ring"])
        self.assertIn("Import preview", preview_html)
        self.assertIn("Import these rows", preview_html)
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["batch_id"], preview["batch_id"])
        self.assertEqual({item["card_name"]: item["quantity"] for item in after_commit}, {"Lightning Bolt": 1, "Sol Ring": 3})
        self.assertEqual({item["card_name"]: item["quantity_for_trade"] for item in after_commit}, {"Lightning Bolt": 1, "Sol Ring": 1})
        self.assertEqual(undo["undone_items"], 2)
        self.assertEqual([item["card_name"] for item in after_undo], ["Sol Ring"])
        self.assertEqual(after_undo[0]["quantity"], 1)
        self.assertEqual(after_undo[0]["quantity_for_trade"], 0)
        self.assertEqual(after_undo[0]["notes"], "original note")
        self.assertEqual(undone_batch["status"], "undone")

    def test_csv_import_can_enrich_from_local_scryfall_bulk_data(self):
        user_id = app.create_user("oracle", "password123", "Oracle")
        csv_bytes = "Name,Quantity\nSol Ring,1\n".encode("utf-8")
        app.store_scryfall_bulk_cards([
            {
                "object": "card",
                "id": "fake-id",
                "name": "Sol Ring",
                "set_name": "Commander Masters",
                "set": "cmm",
                "collector_number": "703",
                "released_at": "2023-08-04",
                "image_uris": {"small": "https://img.example/sol-ring.jpg"},
                "mana_cost": "{1}",
                "type_line": "Artifact",
                "oracle_text": "Tap: Add two colorless mana.",
                "rarity": "uncommon",
                "color_identity": [],
                "scryfall_uri": "https://scryfall.com/card/cmm/703/sol-ring",
                "prices": {"usd": "1.23"},
                "tcgplayer_id": 123456,
                "cardmarket_id": 654321,
            }
        ])

        result = app.import_collection_csv(user_id, csv_bytes, enrich_scryfall=True)

        card = app.row("SELECT * FROM collection_items WHERE user_id = ?", (user_id,))

        self.assertEqual(result["enriched"], 1)
        self.assertEqual(result["queued"], 0)
        self.assertEqual(card["type_line"], "Artifact")
        self.assertEqual(card["set_code"], "CMM")
        self.assertEqual(card["price_usd"], "1.23")
        self.assertEqual(card["tcgplayer_product_id"], "123456")
        self.assertEqual(card["cardmarket_product_id"], "654321")

    def test_csv_import_queues_background_scryfall_enrichment_misses(self):
        user_id = app.create_user("queue", "password123", "Queue")
        csv_bytes = "Name,Quantity\nMystery Card,1\n".encode("utf-8")

        result = app.import_collection_csv(user_id, csv_bytes, enrich_scryfall=True)

        card = app.row("SELECT * FROM collection_items WHERE user_id = ?", (user_id,))
        job = app.row("SELECT * FROM scryfall_enrichment_jobs WHERE collection_item_id = ?", (card["id"],))

        self.assertEqual(result["enriched"], 0)
        self.assertEqual(result["queued"], 1)
        self.assertEqual(card["type_line"], "")
        self.assertEqual(job["status"], "pending")

    def test_background_scryfall_enrichment_updates_queued_card(self):
        user_id = app.create_user("worker", "password123", "Worker")
        csv_bytes = "Name,Quantity\nMystery Card,1\n".encode("utf-8")
        app.import_collection_csv(user_id, csv_bytes, enrich_scryfall=True)
        original_lookup = app.lookup_scryfall_card

        def fake_lookup(card_name, set_code="", collector_number="", scryfall_id=""):
            return {
                "card_name": "Mystery Card",
                "set_name": "Test Set",
                "set_code": "TST",
                "collector_number": "42",
                "scryfall_id": "mystery-id",
                "image_url": "https://img.example/mystery.jpg",
                "mana_cost": "{2}",
                "type_line": "Creature",
                "oracle_text": "Test text.",
                "rarity": "rare",
                "colors": "",
                "color_identity": "",
                "scryfall_uri": "https://scryfall.com/card/tst/42/mystery-card",
                "price_usd": "0.42",
            }

        try:
            app.lookup_scryfall_card = fake_lookup
            processed = app.process_scryfall_enrichment_once()
        finally:
            app.lookup_scryfall_card = original_lookup

        card = app.row("SELECT * FROM collection_items WHERE user_id = ?", (user_id,))
        job = app.row("SELECT * FROM scryfall_enrichment_jobs WHERE collection_item_id = ?", (card["id"],))
        notification = app.row("SELECT * FROM user_notifications WHERE user_id = ? AND kind = 'scryfall_import'", (user_id,))

        self.assertTrue(processed)
        self.assertEqual(card["scryfall_id"], "mystery-id")
        self.assertEqual(card["type_line"], "Creature")
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["completion_notified"], 1)
        self.assertIsNotNone(notification)
        self.assertIn("1 enriched", notification["body"])

    def test_import_page_hides_manual_scryfall_refresh_controls(self):
        user_id = app.create_user("importui", "password123", "Import UI")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))

        html = app.render_import(user)

        self.assertNotIn("Update local Scryfall data", html)
        self.assertNotIn("Refresh collection prices", html)
        self.assertNotIn('action="/import/scryfall-sync"', html)
        self.assertNotIn('action="/prices/refresh"', html)
        self.assertIn("update automatically in the background", html)

    def test_scryfall_price_history_and_alerts_are_recorded(self):
        user_id = app.create_user("history", "password123", "History")
        card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, set_code, collector_number,
                 quantity, quantity_for_trade, scryfall_id, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 'CMM', '703',
                    1, 0, 'sol-ring-id', '1.00', 'scryfall', ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )

        app.update_collection_item_from_scryfall(
            card_id,
            {
                "card_name": "Sol Ring",
                "set_name": "Commander Masters",
                "set_code": "CMM",
                "collector_number": "703",
                "scryfall_id": "sol-ring-id",
                "type_line": "Artifact",
                "price_usd": "1.50",
            },
        )

        history = app.rows("SELECT * FROM price_history WHERE collection_item_id = ? ORDER BY id", (card_id,))
        alerts = app.rows("SELECT * FROM user_notifications WHERE user_id = ? AND kind = 'price_alert'", (user_id,))
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        form_html = app.render_collection_form(user, app.row("SELECT * FROM collection_items WHERE id = ?", (card_id,)))

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["previous_price_usd"], "1.00")
        self.assertEqual(history[0]["price_usd"], "1.50")
        self.assertEqual(history[0]["change_amount"], "0.50")
        self.assertEqual(len(alerts), 1)
        self.assertIn("Price increased", alerts[0]["title"])
        self.assertIn("Price history", form_html)
        self.assertIn("$1.50", form_html)

    def test_price_alert_threshold_and_toggle_control_notifications(self):
        user_id = app.create_user("threshold", "password123", "Threshold")
        app.update_user_profile(user_id, "threshold", "Threshold", "", "", False, "", True, "10")
        card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade,
                 price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 1, 0,
                    '10.00', 'scryfall', ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        item = app.row("SELECT * FROM collection_items WHERE id = ?", (card_id,))

        app.record_price_history_for_item(card_id, user_id, item, "10.00", "10.50")
        self.assertIsNone(app.row("SELECT * FROM user_notifications WHERE user_id = ?", (user_id,)))

        item = app.row("SELECT * FROM collection_items WHERE id = ?", (card_id,))
        app.record_price_history_for_item(card_id, user_id, item, "10.50", "12.00")
        alert = app.row("SELECT * FROM user_notifications WHERE user_id = ?", (user_id,))
        self.assertIsNotNone(alert)

        app.update_user_profile(user_id, "threshold", "Threshold", "", "", False, "", False, "0")
        item = app.row("SELECT * FROM collection_items WHERE id = ?", (card_id,))
        app.record_price_history_for_item(card_id, user_id, item, "12.00", "15.00")
        alerts = app.rows("SELECT * FROM user_notifications WHERE user_id = ?", (user_id,))
        self.assertEqual(len(alerts), 1)

    def test_collection_price_refresh_uses_local_scryfall_data(self):
        user_id = app.create_user("refresh", "password123", "Refresh")
        card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, set_code, collector_number,
                 quantity, quantity_for_trade, scryfall_id, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 'CMM', '703',
                    1, 0, 'fake-id', '1.00', 'scryfall', ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        app.store_scryfall_bulk_cards([
            {
                "object": "card",
                "id": "fake-id",
                "name": "Sol Ring",
                "set_name": "Commander Masters",
                "set": "cmm",
                "collector_number": "703",
                "released_at": "2023-08-04",
                "type_line": "Artifact",
                "rarity": "uncommon",
                "prices": {"usd": "1.75"},
            }
        ])

        result = app.refresh_user_scryfall_prices(user_id)

        card = app.row("SELECT * FROM collection_items WHERE id = ?", (card_id,))
        alert = app.row("SELECT * FROM user_notifications WHERE user_id = ? AND kind = 'price_alert'", (user_id,))

        self.assertEqual(result["priced"], 1)
        self.assertEqual(result["changed"], 1)
        self.assertEqual(card["price_usd"], "1.75")
        self.assertEqual(card["price_source"], "scryfall")
        self.assertIn("1.00 to $1.75", alert["body"])

    def test_automatic_scryfall_price_refresh_updates_all_users(self):
        alice_id = app.create_user("autoalice", "password123", "Auto Alice")
        bob_id = app.create_user("autobob", "password123", "Auto Bob")
        for user_id, card_name, scryfall_id, old_price, new_price in (
            (alice_id, "Sol Ring", "sol-id", "1.00", "1.25"),
            (bob_id, "Counterspell", "counter-id", "2.00", "2.50"),
        ):
            app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, quantity, quantity_for_trade,
                     scryfall_id, price_usd, price_source, created_at, updated_at)
                VALUES (?, 'mtg', ?, 1, 0, ?, ?, 'scryfall', ?, ?)
                """,
                (user_id, card_name, scryfall_id, old_price, app.now_iso(), app.now_iso()),
            )
            app.store_scryfall_bulk_cards([
                {
                    "object": "card",
                    "id": "sol-id",
                    "name": "Sol Ring",
                    "set_name": "Commander Masters",
                    "set": "cmm",
                    "collector_number": "703",
                    "prices": {"usd": "1.25"},
                },
                {
                    "object": "card",
                    "id": "counter-id",
                    "name": "Counterspell",
                    "set_name": "Dominaria Remastered",
                    "set": "dmr",
                    "collector_number": "45",
                    "prices": {"usd": "2.50"},
                },
            ])

        result = app.refresh_all_scryfall_prices(sync_bulk=False)

        self.assertEqual(result["users"], 2)
        self.assertEqual(result["changed"], 2)
        self.assertFalse(app.scryfall_price_refresh_due())
        self.assertEqual(app.get_setting(app.SCRYFALL_PRICE_REFRESH_STATUS_KEY), "idle")
        self.assertEqual(app.row("SELECT price_usd FROM collection_items WHERE user_id = ?", (alice_id,))["price_usd"], "1.25")
        self.assertEqual(app.row("SELECT price_usd FROM collection_items WHERE user_id = ?", (bob_id,))["price_usd"], "2.50")
        alice_refresh = app.row("SELECT * FROM user_notifications WHERE user_id = ? AND kind = 'price_refresh'", (alice_id,))
        bob_refresh = app.row("SELECT * FROM user_notifications WHERE user_id = ? AND kind = 'price_refresh'", (bob_id,))
        self.assertIsNotNone(alice_refresh)
        self.assertIsNotNone(bob_refresh)
        self.assertIn("Scryfall prices updated", alice_refresh["title"])

    def test_external_price_providers_are_disabled(self):
        user_id = app.create_user("prices", "password123", "Prices")
        card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 1, 0, '1.00', 'scryfall', ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )

        queued = app.schedule_price_refresh_jobs(user_id, provider="cardmarket")
        processed = app.process_price_refresh_once()
        applied = app.apply_cached_provider_prices([user_id], "cardmarket")
        result = app.prepare_price_basis_for_users([user_id], "cardmarket", force=False)
        card = app.row("SELECT * FROM collection_items WHERE id = ?", (card_id,))

        self.assertEqual(app.PRICE_PROVIDER_KEYS, ())
        self.assertEqual(queued, 0)
        self.assertFalse(processed)
        self.assertEqual(applied, 0)
        self.assertEqual(result, {"provider": "scryfall", "applied": 0, "queued": 0, "configured": True})
        self.assertEqual(card["price_source"], "scryfall")

    def test_non_scryfall_price_inputs_normalize_to_scryfall(self):
        self.assertEqual(app.normalize_price_basis("tcgplayer"), "scryfall")
        self.assertEqual(app.normalize_price_source("TCGplayer"), "scryfall")
        self.assertEqual(app.normalize_price_source("Manual"), "scryfall")
        self.assertEqual(app.price_source_label("cardmarket"), "Scryfall")

    def test_manual_card_lookup_can_enrich_before_save(self):
        user_id = app.create_user("manual", "password123", "Manual")
        original_lookup = app.lookup_scryfall_card

        def fake_lookup(card_name, set_code="", collector_number="", scryfall_id=""):
            return {
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
            }

        form = {
            "card_name": ["Rhystic Study"],
            "game": ["mtg"],
            "quantity": ["1"],
            "quantity_for_trade": ["0"],
            "lookup_on_save": ["1"],
        }
        try:
            app.lookup_scryfall_card = fake_lookup
            data = app.validate_collection_form(form)
            enriched = app.enrich_collection_data_from_scryfall(data)
            app.upsert_collection_item(user_id, enriched, merge=False)
        finally:
            app.lookup_scryfall_card = original_lookup

        card = app.row("SELECT * FROM collection_items WHERE user_id = ?", (user_id,))

        self.assertEqual(card["scryfall_id"], "study-id")
        self.assertEqual(card["type_line"], "Enchantment")
        self.assertEqual(card["set_code"], "WOT")

    def test_lookup_enriched_add_form_renders_without_item_id(self):
        user_id = app.create_user("renderer", "password123", "Renderer")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        item = {
            "game": "mtg",
            "card_name": "Sol Ring",
            "set_name": "Commander Masters",
            "set_code": "CMM",
            "collector_number": "703",
            "finish": "Regular",
            "condition": "NM",
            "language": "English",
            "quantity": 1,
            "quantity_for_trade": 0,
            "notes": "",
            "scryfall_id": "fake-id",
            "image_url": "",
            "mana_cost": "{1}",
            "type_line": "Artifact",
            "oracle_text": "Tap: Add two colorless mana.",
            "rarity": "uncommon",
            "colors": "",
            "color_identity": "",
            "scryfall_uri": "https://scryfall.com/card/cmm/703/sol-ring",
            "price_usd": "1.23",
            "lookup_on_save": "1",
        }

        html = app.render_collection_form(user, item)

        self.assertIn('action="/collection/new"', html)
        self.assertIn("Open on Scryfall", html)

    def test_scryfall_partial_search_returns_selectable_prints(self):
        original_get = app.scryfall_get

        def fake_get(path, params=None):
            self.assertEqual(path, "/cards/search")
            self.assertIn("name:sol", params["q"])
            self.assertEqual(params["unique"], "cards")
            return {
                "data": [
                    {
                        "id": "sol-one",
                        "name": "Sol Ring",
                        "set_name": "Commander Masters",
                        "set": "cmm",
                        "collector_number": "703",
                        "type_line": "Artifact",
                        "rarity": "uncommon",
                        "image_uris": {"small": "https://img.example/sol-1.jpg"},
                        "prices": {"usd": "1.00"},
                    },
                    {
                        "id": "sol-two",
                        "name": "Sol Ring",
                        "set_name": "Fallout",
                        "set": "pip",
                        "collector_number": "233",
                        "type_line": "Artifact",
                        "rarity": "uncommon",
                        "image_uris": {"small": "https://img.example/sol-2.jpg"},
                        "prices": {"usd": "2.00"},
                    },
                ]
            }

        try:
            app.scryfall_get = fake_get
            results = app.search_scryfall_cards("sol")
        finally:
            app.scryfall_get = original_get

        selected = app.selected_scryfall_card_data("sol-two")

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["set_code"], "CMM")
        self.assertEqual(selected["set_code"], "PIP")

    def test_scryfall_variation_search_returns_prints_for_selected_card(self):
        original_get = app.scryfall_get

        def fake_get(path, params=None):
            self.assertEqual(path, "/cards/search")
            self.assertIn('!"Sol Ring"', params["q"])
            self.assertEqual(params["unique"], "prints")
            return {
                "data": [
                    {
                        "id": "sol-cmm",
                        "name": "Sol Ring",
                        "set_name": "Commander Masters",
                        "set": "cmm",
                        "collector_number": "703",
                        "type_line": "Artifact",
                        "rarity": "uncommon",
                        "image_uris": {"small": "https://img.example/sol-cmm.jpg"},
                        "prices": {"usd": "1.00"},
                    },
                    {
                        "id": "sol-pip",
                        "name": "Sol Ring",
                        "set_name": "Fallout",
                        "set": "pip",
                        "collector_number": "233",
                        "type_line": "Artifact",
                        "rarity": "uncommon",
                        "image_uris": {"small": "https://img.example/sol-pip.jpg"},
                        "prices": {"usd": "2.00"},
                    },
                ]
            }

        try:
            app.scryfall_get = fake_get
            results = app.search_scryfall_prints("Sol Ring")
        finally:
            app.scryfall_get = original_get

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["set_code"], "CMM")
        self.assertEqual(results[1]["set_code"], "PIP")

    def test_collection_form_renders_scryfall_result_picker(self):
        user_id = app.create_user("picker", "password123", "Picker")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        item = {
            "game": "mtg",
            "card_name": "sol",
            "set_name": "",
            "set_code": "",
            "collector_number": "",
            "finish": "Regular",
            "condition": "NM",
            "language": "English",
            "quantity": 1,
            "quantity_for_trade": 0,
            "notes": "",
            "lookup_on_save": "1",
        }
        results = [
            {
                "scryfall_id": "sol-one",
                "card_name": "Sol Ring",
                "set_name": "Commander Masters",
                "set_code": "CMM",
                "collector_number": "703",
                "image_url": "",
                "type_line": "Artifact",
                "rarity": "uncommon",
                "price_usd": "1.00",
            }
        ]

        html = app.render_collection_form(user, item, scryfall_results=results)

        self.assertIn("Scryfall matches", html)
        self.assertIn('name="selected_scryfall_id"', html)
        self.assertIn("Use selected card", html)

    def test_collection_form_can_render_card_first_picker(self):
        user_id = app.create_user("cardpicker", "password123", "Card Picker")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        item = {
            "game": "mtg",
            "card_name": "sol",
            "set_name": "",
            "set_code": "",
            "collector_number": "",
            "finish": "Regular",
            "condition": "NM",
            "language": "English",
            "quantity": 1,
            "quantity_for_trade": 0,
            "notes": "",
            "lookup_on_save": "1",
        }
        results = [
            {
                "scryfall_id": "sol-one",
                "card_name": "Sol Ring",
                "set_name": "Commander Masters",
                "set_code": "CMM",
                "collector_number": "703",
                "image_url": "",
                "type_line": "Artifact",
                "rarity": "uncommon",
                "price_usd": "1.00",
            }
        ]

        html = app.render_collection_form(
            user,
            item,
            scryfall_results=results,
            scryfall_picker_intent="choose_scryfall_card",
            scryfall_picker_label="Show variations",
            scryfall_picker_title="Scryfall card matches",
        )

        self.assertIn("Scryfall card matches", html)
        self.assertIn('value="choose_scryfall_card"', html)
        self.assertIn("Show variations", html)

    def test_collection_condition_details_and_photo_gallery(self):
        owner_id = factory.create_user("photo-owner", display_name="Photo Owner")
        viewer_id = factory.create_user("photo-viewer", display_name="Photo Viewer")
        owner = app.row("SELECT * FROM users WHERE id = ?", (owner_id,))
        item_id = factory.create_collection_item(
            owner_id,
            "Black Lotus",
            condition="HP",
            condition_notes="Small crease near the lower-left corner.",
            quantity_for_trade=1,
            is_public=1,
        )
        photo_id = app.add_collection_item_photo(
            owner_id,
            item_id,
            {
                "filename": "front.png",
                "content_type": "image/png",
                "content": b"\x89PNG\r\n\x1a\ncondition-photo",
            },
            "Front and lower-left corner",
        )
        item = app.row("SELECT * FROM collection_items WHERE id = ?", (item_id,))
        html = app.render_collection_form(owner, item)
        viewer = app.row("SELECT * FROM users WHERE id = ?", (viewer_id,))
        browse_html = app.render_browse(viewer, {})
        public_photo = app.collection_item_photo_for_user(photo_id, viewer_id)
        api_data = app.api_collection_item_dict(item)
        account_export = app.export_account_data(owner_id)

        self.assertEqual(item["condition_notes"], "Small crease near the lower-left corner.")
        self.assertIn("Condition details", html)
        self.assertIn("Small crease near the lower-left corner.", html)
        self.assertIn("Front and lower-left corner", html)
        self.assertIn(f"/collection/photos/{photo_id}", html)
        self.assertIn('class="photo-preview-trigger"', browse_html)
        self.assertIn("View 1 photo", browse_html)
        self.assertIn(f'id="browse-photo-dialog-{item_id}"', browse_html)
        self.assertIn("Front and lower-left corner", browse_html)
        self.assertIn("Small crease near the lower-left corner.", browse_html)
        self.assertIsNotNone(public_photo)
        self.assertEqual(api_data["photo_count"], 1)
        self.assertEqual(account_export["collection"][0]["photos"][0]["caption"], "Front and lower-left corner")
        self.assertNotIn("content", account_export["collection"][0]["photos"][0])

        app.execute("UPDATE collection_items SET is_public = 0 WHERE id = ?", (item_id,))
        self.assertIsNone(app.collection_item_photo_for_user(photo_id, viewer_id))
        self.assertIsNotNone(app.collection_item_photo_for_user(photo_id, owner_id))
        with self.assertRaisesRegex(ValueError, "PNG, JPG, GIF, or WebP"):
            app.add_collection_item_photo(
                owner_id,
                item_id,
                {"filename": "notes.txt", "content_type": "text/plain", "content": b"not an image"},
            )

    def test_trade_offer_snapshots_condition_details_and_photos(self):
        alice_id = factory.create_user("photo-alice", display_name="Photo Alice")
        bob_id = factory.create_user("photo-bob", display_name="Photo Bob")
        bob = app.row("SELECT * FROM users WHERE id = ?", (bob_id,))
        alice_card_id = factory.create_collection_item(
            alice_id,
            "Mox Pearl",
            condition="LP",
            condition_notes="Light edge whitening visible on the back.",
            quantity=1,
            quantity_for_trade=1,
        )
        bob_card_id = factory.create_collection_item(
            bob_id,
            "Sol Ring",
            quantity=1,
            quantity_for_trade=1,
        )
        original_photo_id = app.add_collection_item_photo(
            alice_id,
            alice_card_id,
            {
                "filename": "back.webp",
                "content_type": "image/webp",
                "content": b"RIFF\x04\x00\x00\x00WEBPphoto",
            },
            "Back edge whitening",
        )
        alice_card = app.row("SELECT * FROM collection_items WHERE id = ?", (alice_card_id,))
        bob_card = app.row("SELECT * FROM collection_items WHERE id = ?", (bob_card_id,))

        trade_id = app.create_trade_offer(alice_id, bob_id, "", [(alice_card, 1)], [(bob_card, 1)])
        trade_item = app.row(
            "SELECT * FROM trade_items WHERE trade_id = ? AND collection_item_id = ?",
            (trade_id, alice_card_id),
        )
        snapshot_photos = app.trade_item_photo_rows(trade_item["id"])
        app.delete_collection_item_photo(alice_id, alice_card_id, original_photo_id)
        app.execute("UPDATE collection_items SET condition_notes = 'Changed later' WHERE id = ?", (alice_card_id,))
        detail_html = app.render_trade_detail(bob, trade_id)

        self.assertEqual(trade_item["condition_notes"], "Light edge whitening visible on the back.")
        self.assertEqual(len(snapshot_photos), 1)
        self.assertIn("Light edge whitening visible on the back.", detail_html)
        self.assertIn("Back edge whitening", detail_html)
        self.assertIn(f"/trades/{trade_id}/photos/{snapshot_photos[0]['id']}", detail_html)

        app.execute("UPDATE trades SET status = 'accepted' WHERE id = ?", (trade_id,))
        app.complete_trade(trade_id, completed_by_user_id=bob_id)
        received = app.row("SELECT * FROM collection_items WHERE user_id = ? AND card_name = 'Mox Pearl'", (bob_id,))
        self.assertEqual(received["condition_notes"], "Light edge whitening visible on the back.")
        self.assertEqual(app.collection_item_photo_count(received["id"]), 1)
