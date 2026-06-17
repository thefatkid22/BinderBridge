"""Saved search and reusable filter preset tests."""

from urllib.parse import parse_qs, urlparse

from tests.base import *  # noqa: F401,F403


class SavedSearchTests(BinderBridgeTestCase):
    def test_saved_search_schema_and_index_exist(self):
        columns = {item["name"] for item in app.rows("PRAGMA table_info(saved_searches)")}
        indexes = {item["name"] for item in app.rows("PRAGMA index_list(saved_searches)")}

        self.assertTrue({"user_id", "context", "name", "query_json", "created_at", "updated_at"}.issubset(columns))
        self.assertIn("idx_saved_searches_user_context", indexes)

    def test_saved_search_sanitizes_fields_and_updates_by_name(self):
        user_id = factory.create_user("preset-owner")
        first = app.save_saved_search(
            user_id,
            "collection",
            "Trade Binder",
            {
                "q": ["dragon"],
                "trade_only": ["1"],
                "sort": ["value"],
                "page": ["8"],
                "recipient_id": ["999"],
                "unsupported": ["discard me"],
            },
        )
        updated = app.save_saved_search(
            user_id,
            "collection",
            "trade binder",
            {"finish": ["Foil"], "dir": ["desc"], "page": ["3"]},
        )

        searches = app.saved_search_rows(user_id, "collection")
        payload = app.saved_search_payload(updated)
        self.assertEqual(first["id"], updated["id"])
        self.assertEqual(len(searches), 1)
        self.assertEqual(payload, {"dir": "desc", "finish": "Foil"})
        self.assertNotIn("page", payload)
        self.assertNotIn("unsupported", payload)
        self.assertNotIn("recipient_id", payload)

    def test_saved_search_requires_supported_context_name_and_filters(self):
        user_id = factory.create_user("invalid-preset-owner")

        with self.assertRaisesRegex(ValueError, "supported saved-search"):
            app.save_saved_search(user_id, "admin", "Nope", {"q": ["x"]})
        with self.assertRaisesRegex(ValueError, "Enter a name"):
            app.save_saved_search(user_id, "collection", " ", {"q": ["x"]})
        with self.assertRaisesRegex(ValueError, "at least one filter"):
            app.save_saved_search(user_id, "collection", "Empty", {"page": ["2"]})

    def test_saved_search_delete_is_owner_scoped(self):
        owner_id = factory.create_user("saved-owner")
        other_id = factory.create_user("saved-other")
        search = app.save_saved_search(owner_id, "browse", "Foils", {"finish": ["Foil"]})

        with self.assertRaisesRegex(ValueError, "not found"):
            app.delete_saved_search(other_id, search["id"])
        app.delete_saved_search(owner_id, search["id"])

        self.assertIsNone(app.row("SELECT * FROM saved_searches WHERE id = ?", (search["id"],)))

    def test_saved_search_apply_url_replaces_regular_view_filters(self):
        user_id = factory.create_user("regular-apply")
        search = app.save_saved_search(
            user_id,
            "browse",
            "Blue Foils",
            {"q": ["counter"], "finish": ["Foil"], "sort": ["value"], "dir": ["desc"]},
        )

        url = app.saved_search_apply_url(search, current_query={"quality": ["LP"], "page": ["4"]})
        parsed = urlparse(url)
        query = parse_qs(parsed.query)

        self.assertEqual(parsed.path, "/browse")
        self.assertEqual(query["q"], ["counter"])
        self.assertEqual(query["finish"], ["Foil"])
        self.assertEqual(query["sort"], ["value"])
        self.assertNotIn("quality", query)
        self.assertNotIn("page", query)

    def test_trade_saved_search_preserves_selections_and_other_picker(self):
        user_id = factory.create_user("trade-preset-owner")
        search = app.save_saved_search(
            user_id,
            "trade_offer",
            "Artifact offers",
            {"offer_type_line": ["Artifact"], "offer_finish": ["Foil"], "offer_sort": ["value"]},
        )
        current_query = {
            "recipient_id": ["42"],
            "offer_q": ["old"],
            "offer_page": ["3"],
            "offer_101": ["2"],
            "request_q": ["dragon"],
            "request_202": ["1"],
        }

        url = app.saved_search_apply_url(search, current_query=current_query, required_params={"recipient_id": 42})
        query = parse_qs(urlparse(url).query)

        self.assertEqual(query["offer_type_line"], ["Artifact"])
        self.assertEqual(query["offer_finish"], ["Foil"])
        self.assertNotIn("offer_q", query)
        self.assertNotIn("offer_page", query)
        self.assertEqual(query["offer_101"], ["2"])
        self.assertEqual(query["request_q"], ["dragon"])
        self.assertEqual(query["request_202"], ["1"])
        self.assertEqual(query["recipient_id"], ["42"])

    def test_filterable_pages_render_saved_search_controls(self):
        alice_id = factory.create_user("saved-alice", display_name="Saved Alice")
        bob_id = factory.create_user("saved-bob", display_name="Saved Bob")
        alice = app.row("SELECT * FROM users WHERE id = ?", (alice_id,))
        factory.create_collection_item(alice_id, "Sol Ring", quantity_for_trade=1)
        factory.create_collection_item(bob_id, "Lightning Bolt", quantity_for_trade=1)
        factory.create_want_item(alice_id, "Counterspell")
        app.save_saved_search(alice_id, "collection", "My trades", {"trade_only": ["1"]})
        app.save_saved_search(alice_id, "browse", "Foils", {"finish": ["Foil"]})
        app.save_saved_search(alice_id, "wants", "Urgent", {"priority": ["urgent"]})
        app.save_saved_search(alice_id, "trade_offer", "Offer artifacts", {"offer_type_line": ["Artifact"]})
        app.save_saved_search(alice_id, "trade_request", "Request foils", {"request_finish": ["Foil"]})

        collection_html = app.render_collection(alice, {"trade_only": ["1"]})
        browse_html = app.render_browse(alice, {"finish": ["Foil"]})
        wants_html = app.render_wants(alice, query={"priority": ["urgent"]})
        trade_html = app.render_new_trade(alice, bob_id, {"recipient_id": [str(bob_id)]})

        for html in (collection_html, browse_html, wants_html, trade_html):
            self.assertIn("Saved filters", html)
            self.assertIn('action="/saved-searches"', html)
        self.assertIn("My trades", collection_html)
        self.assertIn("Foils", browse_html)
        self.assertIn("Urgent", wants_html)
        self.assertIn("Offer artifacts", trade_html)
        self.assertIn("Request foils", trade_html)
        self.assertIn('name="recipient_id" value="2"', trade_html)

    def test_read_only_users_can_manage_personal_saved_searches(self):
        user = {"role": app.ROLE_READ_ONLY}

        self.assertTrue(app.user_can_mutate_path(user, "/saved-searches"))
        self.assertTrue(app.user_can_mutate_path(user, "/saved-searches/1/delete"))


if __name__ == "__main__":
    unittest.main()
