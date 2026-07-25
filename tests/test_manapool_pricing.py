"""Mana Pool feed, resolver, preference, history, and trade-basis tests."""

import sqlite3
from urllib.error import URLError

from tests.base import *  # noqa: F401,F403


class _JsonResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.payload


class ManaPoolPricingTests(BinderBridgeTestCase):
    def feed(self):
        return {
            "meta": {"updated_at": "2026-07-25T12:00:00Z"},
            "data": [
                {
                    "scryfall_id": "card-1",
                    "set_code": "TST",
                    "number": "7",
                    "url": "https://manapool.com/card/card-1",
                    "available_quantity": 4,
                    "price_market": 1234,
                    "price_market_foil": 2345,
                },
                {
                    "scryfall_id": "card-2",
                    "set_code": "TST",
                    "number": "8",
                    "url": "https://manapool.com/card/card-2",
                    "available_quantity": 0,
                    "price_market": 0,
                    "price_market_foil": "bad",
                },
            ],
        }

    def test_schema_and_feed_price_parsing(self):
        columns = {
            item["name"] for item in app.rows("PRAGMA table_info(provider_price_cache)")
        }
        history_columns = {
            item["name"] for item in app.rows("PRAGMA table_info(price_history)")
        }
        parsed = app.parse_manapool_price_feed(self.feed())

        self.assertIn("regular_price_usd", columns)
        self.assertIn("foil_price_usd", columns)
        self.assertIn("price_basis", history_columns)
        self.assertEqual(parsed[0]["regular_price_usd"], "12.34")
        self.assertEqual(parsed[0]["foil_price_usd"], "23.45")
        self.assertEqual(parsed[1]["regular_price_usd"], "")
        self.assertEqual(parsed[1]["foil_price_usd"], "")
        with self.assertRaises(app.ManaPoolPriceFeedError):
            app.parse_manapool_price_feed({"data": "not-a-list"})
        duplicate_payload = self.feed()
        duplicate_payload["data"].append(
            {
                **duplicate_payload["data"][0],
                "number": "7-TOKEN-1",
                "price_market": 9999,
            }
        )
        unambiguous = app.parse_manapool_price_feed(duplicate_payload)
        self.assertEqual(
            [entry["scryfall_id"] for entry in unambiguous], ["card-2"]
        )

    def test_migration_17_backfills_legacy_history_as_scryfall(self):
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE schema_migration_history (
                    version INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY,
                    preferred_price_source TEXT NOT NULL
                );
                CREATE TABLE trades (
                    id INTEGER PRIMARY KEY,
                    price_source_preference TEXT NOT NULL
                );
                CREATE TABLE price_history (
                    id INTEGER PRIMARY KEY,
                    collection_item_id INTEGER,
                    price_source TEXT NOT NULL,
                    observed_at TEXT NOT NULL
                );
                INSERT INTO users VALUES (1, 'cardmarket');
                INSERT INTO trades VALUES (1, 'tcgplayer');
                INSERT INTO price_history VALUES (
                    1, 10, 'cardmarket', '2026-01-01T00:00:00+00:00'
                );
                """
            )
            app.set_db_schema_version(conn, 16)
            app.run_schema_migrations(conn)

            history = conn.execute(
                "SELECT price_source, price_basis FROM price_history WHERE id = 1"
            ).fetchone()
            self.assertEqual(tuple(history), ("scryfall", "scryfall"))
            self.assertEqual(
                conn.execute(
                    "SELECT preferred_price_source FROM users WHERE id = 1"
                ).fetchone()[0],
                "scryfall",
            )
            self.assertEqual(
                conn.execute(
                    "SELECT price_source_preference FROM trades WHERE id = 1"
                ).fetchone()[0],
                "scryfall",
            )

    def test_failed_refresh_preserves_last_successful_snapshot(self):
        parsed = app.parse_manapool_price_feed(self.feed())
        app.replace_manapool_price_cache(parsed)

        def unavailable(_request, timeout=0):
            raise URLError("offline")

        with self.assertRaises(app.ManaPoolPriceFeedError):
            app.refresh_manapool_price_cache(opener=unavailable)
        with self.assertRaises(app.ManaPoolPriceFeedError):
            app.refresh_manapool_price_cache(
                opener=lambda _request, timeout=0: _JsonResponse(
                    {"meta": {}, "data": "incompatible"}
                )
            )

        cached = app.rows(
            "SELECT * FROM provider_price_cache WHERE provider = 'manapool' ORDER BY scryfall_id"
        )
        self.assertEqual(len(cached), 2)
        self.assertEqual(cached[0]["regular_price_usd"], "12.34")
        self.assertEqual(
            app.get_setting(app.MANAPOOL_PRICE_REFRESH_STATUS_KEY), "error"
        )

    def test_successful_refresh_replaces_cache_and_reports_status(self):
        result = app.refresh_manapool_price_cache(
            opener=lambda _request, timeout=0: _JsonResponse(self.feed())
        )
        status = app.manapool_price_refresh_status()

        self.assertEqual(result["cached"], 2)
        self.assertEqual(status["cached_prices"], 2)
        self.assertEqual(status["status"], "idle")
        self.assertFalse(app.manapool_price_refresh_due())

    def test_due_check_and_manual_refresh_enqueue(self):
        app.set_setting(
            app.MANAPOOL_PRICE_REFRESH_UPDATED_KEY,
            "2000-01-01T00:00:00+00:00",
        )
        self.assertTrue(app.manapool_price_refresh_due())

        result = app.retry_manapool_price_refresh_async()
        queued = app.row(
            "SELECT * FROM background_jobs WHERE id = ?", (result["job_id"],)
        )

        self.assertTrue(result["started"])
        self.assertEqual(queued["job_type"], "manapool_price_sync")
        self.assertEqual(json.loads(queued["payload_json"]), {"force": True})
        self.assertEqual(
            app.get_setting(app.MANAPOOL_PRICE_REFRESH_STATUS_KEY), "queued"
        )

    def test_resolver_uses_market_finish_and_scryfall_fallbacks(self):
        app.replace_manapool_price_cache(app.parse_manapool_price_feed(self.feed()))
        regular = {
            "game": "mtg",
            "scryfall_id": "card-1",
            "set_code": "TST",
            "collector_number": "7",
            "finish": "Regular",
            "price_usd": "9.99",
            "price_source": "scryfall",
        }
        foil = dict(regular, finish="Foil")
        showcase = dict(regular, finish="Showcase")
        etched = dict(regular, finish="Etched")
        non_mtg = dict(regular, game="pokemon")
        missing = dict(regular, scryfall_id="missing", set_code="NONE")

        self.assertEqual(
            app.resolve_card_price(regular, "manapool"),
            ("12.34", "manapool", "manapool", False),
        )
        self.assertEqual(
            app.resolve_card_price(foil, "manapool"),
            ("23.45", "manapool", "manapool", False),
        )
        self.assertEqual(
            app.resolve_card_price(showcase, "manapool"),
            ("12.34", "manapool", "manapool", False),
        )
        self.assertEqual(
            app.resolve_card_price(etched, "manapool"),
            ("9.99", "scryfall", "manapool", True),
        )
        self.assertEqual(
            app.resolve_card_price(non_mtg, "manapool"),
            ("9.99", "scryfall", "manapool", True),
        )
        self.assertEqual(
            app.resolve_card_price(missing, "manapool"),
            ("9.99", "scryfall", "manapool", True),
        )

    def test_ambiguous_set_collector_match_never_prices_by_name(self):
        entries = app.parse_manapool_price_feed(self.feed())
        duplicate = dict(entries[0])
        duplicate["scryfall_id"] = "card-duplicate"
        app.replace_manapool_price_cache([entries[0], duplicate])
        item = {
            "game": "mtg",
            "card_name": "Same Name",
            "set_code": "TST",
            "collector_number": "7",
            "finish": "Regular",
            "price_usd": "4.00",
            "price_source": "scryfall",
        }

        self.assertEqual(
            app.resolve_card_price(item, "manapool"),
            ("4.00", "scryfall", "manapool", True),
        )

    def test_viewer_preference_resolves_api_fields_and_friendly_options(self):
        user_id = factory.create_user("mana_viewer")
        app.execute(
            "UPDATE users SET preferred_price_source = 'manapool' WHERE id = ?",
            (user_id,),
        )
        item = factory.collection_item_row(
            user_id,
            scryfall_id="card-1",
            set_code="TST",
            collector_number="7",
            price_usd="9.99",
            price_source="scryfall",
        )
        owner_id = factory.create_user("mana_other_member")
        other_item = factory.collection_item_row(
            owner_id,
            "Other Member Card",
            quantity_for_trade=2,
            scryfall_id="card-1",
            set_code="TST",
            collector_number="7",
            price_usd="9.99",
            price_source="scryfall",
        )
        app.replace_manapool_price_cache(app.parse_manapool_price_feed(self.feed()))
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))

        data = app.api_collection_item_dict(item, viewer=user)
        other_data = app.api_collection_item_dict(other_item, viewer=user)
        options = app._api.api_account_options()
        stats = app.collection_statistics(user_id, "manapool")
        other_stats = app.public_profile_stats(owner_id, user)
        exported = app.export_collection_rows(user_id)
        account_html = app.render_account(user)

        self.assertEqual(data["price_usd"], "12.34")
        self.assertEqual(data["price_source"], "manapool")
        self.assertEqual(data["requested_price_basis"], "manapool")
        self.assertEqual(other_data["price_usd"], "12.34")
        self.assertEqual(other_stats["trade_value"], 24.68)
        self.assertIn(
            {"value": "manapool", "label": "Mana Pool"},
            options["price_sources"],
        )
        self.assertEqual(stats["total_value_cents"], 1234)
        self.assertEqual(exported[0]["price_usd"], "12.34")
        self.assertEqual(exported[0]["price_source"], "manapool")
        self.assertIn(
            '<option value="manapool" selected>Mana Pool</option>',
            account_html,
        )

    def test_wishlist_budget_uses_the_viewers_selected_source(self):
        viewer_id = factory.create_user("mana_budget")
        owner_id = factory.create_user("mana_seller")
        app.execute(
            "UPDATE users SET preferred_price_source = 'manapool' WHERE id = ?",
            (viewer_id,),
        )
        factory.collection_item_row(
            owner_id,
            "Budget Card",
            quantity_for_trade=2,
            scryfall_id="card-1",
            set_code="TST",
            collector_number="7",
            price_usd="9.99",
            price_source="scryfall",
        )
        want = factory.want_item_row(
            viewer_id,
            "Budget Card",
            scryfall_id="card-1",
            set_code="TST",
            collector_number="7",
            budget_cap_usd="11.00",
        )
        app.replace_manapool_price_cache(
            app.parse_manapool_price_feed(self.feed())
        )

        availability = app.want_trade_matches(viewer_id, want)

        self.assertEqual(availability["total_quantity"], 2)
        self.assertEqual(availability["within_budget_quantity"], 0)

    def test_value_sort_resolves_the_full_result_before_pagination(self):
        user_id = factory.create_user("mana_sort")
        app.execute(
            "UPDATE users SET preferred_price_source = 'manapool' WHERE id = ?",
            (user_id,),
        )
        for index in range(25):
            factory.collection_item_row(
                user_id,
                f"Fallback {index:02}",
                scryfall_id=f"fallback-{index}",
                price_usd="10.00",
                price_source="scryfall",
            )
        factory.collection_item_row(
            user_id,
            "Mana Leader",
            scryfall_id="mana-leader",
            price_usd="0.01",
            price_source="scryfall",
        )
        app.replace_manapool_price_cache(
            [
                {
                    "scryfall_id": "mana-leader",
                    "set_code": "",
                    "collector_number": "",
                    "regular_price_usd": "100.00",
                    "foil_price_usd": "",
                    "source_url": "",
                    "available_quantity": 1,
                    "source_updated_at": "",
                }
            ]
        )
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))

        html = app.render_collection(
            user, {"sort": ["value"], "dir": ["desc"], "page": ["1"]}
        )
        table = html[html.index('<tbody id="collection-items">') :]

        self.assertIn("Mana Leader", table)
        self.assertLess(table.index("Mana Leader"), table.index("Fallback 00"))

    def test_history_isolated_by_requested_basis_and_actual_source(self):
        user_id = factory.create_user("history_basis")
        item = factory.collection_item_row(
            user_id, price_usd="1.00", price_source="scryfall"
        )
        app.record_price_history_for_item(
            item["id"], user_id, item, "", "1.00",
            price_basis="scryfall", price_source="scryfall"
        )
        app.execute(
            "UPDATE users SET preferred_price_source = 'manapool' WHERE id = ?",
            (user_id,),
        )
        baseline = app.record_price_history_for_item(
            item["id"], user_id, item, "", "2.00",
            price_basis="manapool", price_source="manapool"
        )
        changed = app.record_price_history_for_item(
            item["id"], user_id, item, "", "2.50",
            price_basis="manapool", price_source="manapool"
        )

        self.assertEqual(baseline, "baseline")
        self.assertEqual(changed, "changed")
        notification = app.row(
            "SELECT * FROM user_notifications WHERE user_id = ? AND kind = 'price_alert'",
            (user_id,),
        )
        self.assertIn("Mana Pool moved", notification["body"])

    def test_preference_change_forces_a_silent_current_basis_baseline(self):
        user_id = factory.create_user("basis_switch")
        item = factory.collection_item_row(
            user_id,
            scryfall_id="card-1",
            set_code="TST",
            collector_number="7",
            price_usd="9.99",
            price_source="scryfall",
        )
        app.record_price_history_for_item(
            item["id"],
            user_id,
            item,
            "",
            "2.00",
            price_basis="manapool",
            price_source="manapool",
        )
        app.replace_manapool_price_cache(
            app.parse_manapool_price_feed(self.feed())
        )

        app.update_user_profile(
            user_id,
            "basis_switch",
            "basis_switch",
            "",
            "",
            False,
            "manapool",
        )

        latest = app.row(
            """
            SELECT *
            FROM price_history
            WHERE collection_item_id = ? AND price_basis = 'manapool'
            ORDER BY id DESC
            LIMIT 1
            """,
            (item["id"],),
        )
        self.assertEqual(latest["price_usd"], "12.34")
        self.assertEqual(latest["previous_price_usd"], "")
        self.assertIsNone(
            app.row(
                """
                SELECT *
                FROM user_notifications
                WHERE user_id = ? AND kind = 'price_alert'
                """,
                (user_id,),
            )
        )

    def test_durable_schedule_includes_manapool(self):
        app.ensure_background_job_schedules()
        job = app.row(
            "SELECT * FROM background_jobs WHERE unique_key = 'system:manapool-price-sync'"
        )

        self.assertIsNotNone(job)
        self.assertEqual(job["job_type"], "manapool_price_sync")

    def test_trade_uses_initiator_basis_snapshots_fallbacks_and_retains_counter_basis(self):
        alice_id = factory.create_user("mana_initiator")
        bob_id = factory.create_user("mana_counter")
        app.execute(
            "UPDATE users SET preferred_price_source = 'manapool' WHERE id = ?",
            (alice_id,),
        )
        alice_card = factory.collection_item_row(
            alice_id,
            "Mana Card",
            quantity_for_trade=1,
            scryfall_id="card-1",
            set_code="TST",
            collector_number="7",
            price_usd="9.99",
            price_source="scryfall",
        )
        bob_card = factory.collection_item_row(
            bob_id,
            "Fallback Card",
            quantity_for_trade=1,
            scryfall_id="missing",
            set_code="NONE",
            collector_number="1",
            price_usd="8.00",
            price_source="scryfall",
        )
        app.replace_manapool_price_cache(app.parse_manapool_price_feed(self.feed()))
        alice = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))
        basis = app.trade_price_basis_for(alice, {})
        offered = [(app.apply_trade_price_basis(alice_card, basis), 1)]
        requested = [(app.apply_trade_price_basis(bob_card, basis), 1)]

        trade_id = app.create_trade_offer(
            alice_id, bob_id, "", offered, requested,
            price_source_preference=basis
        )
        snapshots = app.rows(
            "SELECT * FROM trade_items WHERE trade_id = ? ORDER BY side",
            (trade_id,),
        )
        self.assertEqual(basis, "manapool")
        self.assertEqual(
            {item["price_source"] for item in snapshots},
            {"manapool", "scryfall"},
        )

        app.execute(
            "UPDATE provider_price_cache SET regular_price_usd = '99.00' "
            "WHERE provider = 'manapool' AND scryfall_id = 'card-1'"
        )
        saved = app.row(
            "SELECT * FROM trade_items WHERE trade_id = ? AND owner_id = ?",
            (trade_id, alice_id),
        )
        self.assertEqual(saved["price_usd"], "12.34")

        app.execute(
            "UPDATE provider_price_cache SET regular_price_usd = '12.34' "
            "WHERE provider = 'manapool' AND scryfall_id = 'card-1'"
        )
        counter_offered = [(app.apply_trade_price_basis(bob_card, basis), 1)]
        counter_requested = [(app.apply_trade_price_basis(alice_card, basis), 1)]
        app.create_trade_offer(
            bob_id,
            alice_id,
            "Counter",
            counter_offered,
            counter_requested,
            counter_trade_id=trade_id,
            price_source_preference="scryfall",
        )
        counter = app.row("SELECT * FROM trades WHERE id = ?", (trade_id,))
        self.assertEqual(counter["price_source_preference"], "manapool")


if __name__ == "__main__":
    unittest.main()
