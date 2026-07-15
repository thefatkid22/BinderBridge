"""Group, privacy, deck grouping, statistics, and export tests."""

from tests.base import *  # noqa: F401,F403


class GroupsExportsTests(BinderBridgeTestCase):
    def test_deck_binder_and_wishlist_groups_manage_items(self):
        user_id = app.create_user("organizer", "password123", "Organizer")
        other_id = app.create_user("outsider", "password123", "Outsider")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, collector_number, finish, condition, language,
                 quantity, quantity_for_trade, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', '703', 'Regular', 'NM', 'English',
                    2, 1, '1.25', 'scryfall', ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        want_id = app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, set_name, desired_quantity, created_at, updated_at)
            VALUES (?, 'mtg', 'Rhystic Study', 'Wilds of Eldraine', 1, ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )

        deck_id = app.create_card_group(user_id, "deck", "Commander deck", "Cards currently sleeved.")
        binder_id = app.create_card_group(user_id, "binder", "Trade binder")
        wishlist_id = app.create_card_group(user_id, "wishlist", "High priority")
        app.add_collection_item_to_group(user_id, deck_id, card_id, 5)
        app.add_collection_item_to_group(user_id, binder_id, card_id, 1)
        app.add_want_item_to_group(user_id, wishlist_id, want_id)

        deck_items = app.collection_group_items(deck_id)
        groups_html = app.render_groups(user)
        wishlist_groups_html = app.render_groups(user, view="wishlist")
        deck_html = app.render_group_detail(user, deck_id)
        wishlist_html = app.render_group_detail(user, wishlist_id)

        self.assertEqual(deck_items[0]["group_quantity"], 2)
        self.assertIn("Commander deck", groups_html)
        self.assertIn("Trade binder", groups_html)
        self.assertNotIn("High priority", groups_html)
        self.assertIn("High priority", wishlist_groups_html)
        self.assertIn("Decks &amp; Binders", groups_html)
        self.assertIn("Wishlist Groups", wishlist_groups_html)
        self.assertIn("Visibility", groups_html)
        self.assertIn('<option value="members" selected>All members</option>', groups_html)
        self.assertIn("2 x Sol Ring", deck_html)
        self.assertIn("Add card", deck_html)
        self.assertIn('name="keep_trade_availability"', deck_html)
        self.assertIn("Keep available-for-trade unchanged", deck_html)
        self.assertIn("Sharing defaults", deck_html)
        self.assertIn("Private share links", deck_html)
        self.assertIn(f'/groups/{deck_id}/export', deck_html)
        self.assertIn('data-workspace-tabs', deck_html)
        self.assertIn('workspace-side-nav', deck_html)
        self.assertIn('id="group-cards"', deck_html)
        self.assertIn('id="group-sharing"', deck_html)
        self.assertIn('id="group-import"', deck_html)
        self.assertIn('id="group-danger"', deck_html)
        self.assertLess(deck_html.index('id="group-cards"'), deck_html.index('id="group-sharing"'))
        self.assertLess(deck_html.index('id="group-sharing"'), deck_html.index('id="group-import"'))
        self.assertNotIn('id="group-import"', wishlist_html)
        self.assertIn("Rhystic Study", wishlist_html)
        self.assertIn("Add want", wishlist_html)
        with self.assertRaisesRegex(ValueError, "Deck and binder"):
            app.add_collection_item_to_group(other_id, deck_id, card_id, 1)
        removed = app.remove_group_item(user_id, deck_id, deck_items[0]["group_item_id"])
        self.assertEqual(removed, 1)
        self.assertEqual(app.collection_group_items(deck_id), [])
        self.assertEqual(app.delete_card_group(user_id, binder_id), 1)
        self.assertIsNone(app.user_group(user_id, binder_id))

    def test_group_add_route_can_adjust_or_keep_trade_availability(self):
        user_id = app.create_user("grouptrade", "password123", "Group Trade")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        group_id = app.create_card_group(user_id, "binder", "Trade binder")
        default_card_id = factory.create_collection_item(
            user_id,
            "Default Adjust",
            quantity=4,
            quantity_for_trade=3,
        )
        override_card_id = factory.create_collection_item(
            user_id,
            "Keep Trade",
            quantity=4,
            quantity_for_trade=3,
        )

        class RouteHarness:
            def __init__(self, form):
                self.form = form

            def read_form(self):
                return self.form

            def redirect(self, location):
                return location

            def html(self, content, status=200):
                return content, status

            def not_found(self, current_user):
                raise AssertionError(f"Unexpected not found for {current_user['id']}")

        redirect = app.group_action(
            RouteHarness({
                "collection_item_id": [str(default_card_id)],
                "quantity": ["2"],
            }),
            "POST",
            user,
            f"/groups/{group_id}/add",
        )
        default_row = app.row("SELECT quantity, quantity_for_trade FROM collection_items WHERE id = ?", (default_card_id,))
        self.assertEqual(redirect, f"/groups/{group_id}#group-cards")
        self.assertEqual(default_row["quantity"], 4)
        self.assertEqual(default_row["quantity_for_trade"], 2)

        app.group_action(
            RouteHarness({
                "collection_item_id": [str(override_card_id)],
                "quantity": ["2"],
                "keep_trade_availability": ["1"],
            }),
            "POST",
            user,
            f"/groups/{group_id}/add",
        )
        override_row = app.row("SELECT quantity, quantity_for_trade FROM collection_items WHERE id = ?", (override_card_id,))
        self.assertEqual(override_row["quantity"], 4)
        self.assertEqual(override_row["quantity_for_trade"], 3)

    def test_group_visibility_controls_public_member_group_views(self):
        owner_id = app.create_user("groupowner", "password123", "Group Owner")
        viewer_id = app.create_user("groupviewer", "password123", "Group Viewer")
        viewer = app.row("SELECT * FROM users WHERE id = ?", (viewer_id,))
        public_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, quantity, quantity_for_trade, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Public Group Card', 1, 0, 1, ?, ?)
            """,
            (owner_id, app.now_iso(), app.now_iso()),
        )
        private_card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, quantity, quantity_for_trade, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Private Group Card', 1, 0, 0, ?, ?)
            """,
            (owner_id, app.now_iso(), app.now_iso()),
        )
        public_group_id = app.create_card_group(owner_id, "binder", "Public Binder", "", True)
        private_group_id = app.create_card_group(owner_id, "deck", "Private Deck", "", False)
        app.add_collection_item_to_group(owner_id, public_group_id, public_card_id, 1)
        app.add_collection_item_to_group(owner_id, public_group_id, private_card_id, 1)

        member_html = app.render_member_detail(viewer, owner_id)
        public_group_html = app.render_public_group_detail(viewer, owner_id, public_group_id)
        private_group_html = app.render_public_group_detail(viewer, owner_id, private_group_id)
        updated = app.update_card_group_visibility(owner_id, private_group_id, True)
        member_after_update = app.render_member_detail(viewer, owner_id)

        self.assertIn("Public Binder", member_html)
        self.assertNotIn("Private Deck", member_html)
        self.assertIn("Public Group Card", public_group_html)
        self.assertNotIn("Private Group Card", public_group_html)
        self.assertIsNone(private_group_html)
        self.assertEqual(updated, 1)
        self.assertIn("Private Deck", member_after_update)

    def test_granular_privacy_trusted_visibility_and_hidden_values(self):
        owner_id = factory.create_user("privacyowner", display_name="Privacy Owner")
        member_id = factory.create_user("privacyviewer", display_name="Privacy Viewer")
        trusted_id = factory.create_user("trustedviewer", display_name="Trusted Viewer")
        app.admin_set_user_trust(trusted_id, "trust", owner_id)
        app.execute(
            "UPDATE users SET collection_value_visibility = 'trusted' WHERE id = ?",
            (owner_id,),
        )
        factory.create_collection_item(
            owner_id,
            "Members Card",
            quantity_for_trade=1,
            price_usd="4.25",
            visibility="members",
            is_public=1,
        )
        factory.create_collection_item(
            owner_id,
            "Trusted Card",
            quantity_for_trade=1,
            price_usd="9.50",
            visibility="trusted",
            is_public=0,
        )
        factory.create_collection_item(
            owner_id,
            "Link Card",
            quantity_for_trade=1,
            visibility="link",
            is_public=0,
        )
        factory.create_collection_item(
            owner_id,
            "Private Card",
            quantity_for_trade=1,
            visibility="private",
            is_public=0,
        )
        member = app.row("SELECT * FROM users WHERE id = ?", (member_id,))
        trusted = app.row("SELECT * FROM users WHERE id = ?", (trusted_id,))

        member_html = app.render_browse(member, {})
        trusted_html = app.render_browse(trusted, {})

        self.assertIn("Members Card", member_html)
        self.assertNotIn("Trusted Card", member_html)
        self.assertNotIn("Link Card", member_html)
        self.assertNotIn("Private Card", member_html)
        self.assertNotIn("$4.25", member_html)
        self.assertIn("Members Card", trusted_html)
        self.assertIn("Trusted Card", trusted_html)
        self.assertNotIn("Link Card", trusted_html)
        self.assertNotIn("Private Card", trusted_html)
        self.assertIn("$4.25", trusted_html)

    def test_private_group_share_links_are_hashed_revocable_and_control_values(self):
        owner_id = factory.create_user("shareowner", display_name="Share Owner")
        item_id = factory.create_collection_item(
            owner_id,
            "Secret Binder Card",
            price_usd="19.99",
            image_url="https://cards.example.test/secret.jpg",
            visibility="private",
            is_public=0,
        )
        group_id = app.create_card_group(
            owner_id,
            "binder",
            "Private Binder",
            visibility="private",
            default_item_visibility="trusted",
            show_values=False,
            show_photos=False,
        )
        app.add_collection_item_to_group(owner_id, group_id, item_id, 1)

        token, link = app.create_group_share_link(owner_id, group_id, "Meetup link", 30, False, False)
        found = app.share_link_from_token(token, touch=False)
        hidden_html = app.render_shared_group(found, token)
        stored = app.row("SELECT * FROM privacy_share_links WHERE id = ?", (link["id"],))

        self.assertTrue(token.startswith(app.SHARE_TOKEN_PREFIX))
        self.assertNotEqual(stored["token_hash"], token)
        self.assertIn("Secret Binder Card", hidden_html)
        self.assertNotIn("$19.99", hidden_html)
        self.assertIn("Values hidden", hidden_html)
        self.assertIn('referrerpolicy="no-referrer"', hidden_html)

        visible_token, _ = app.create_group_share_link(owner_id, group_id, "Values link", 0, True, True)
        visible_html = app.render_shared_group(app.share_link_from_token(visible_token, touch=False), visible_token)
        self.assertIn("$19.99", visible_html)

        self.assertEqual(app.revoke_group_share_link(owner_id, group_id, link["id"]), 1)
        self.assertIsNone(app.share_link_from_token(token, touch=False))

    def test_private_collection_card_links_render_on_edit_and_control_values_photos(self):
        owner_id = factory.create_user("cardshareowner", display_name="Card Share Owner")
        owner = app.row("SELECT * FROM users WHERE id = ?", (owner_id,))
        item_id = factory.create_collection_item(
            owner_id,
            "Private Card Link",
            set_name="Secret Lair",
            condition_notes="Small mark on the back.",
            quantity=2,
            quantity_for_trade=1,
            price_usd="42.00",
            image_url="https://cards.example.test/private.jpg",
            visibility="private",
            is_public=0,
        )
        photo_id = app.add_collection_item_photo(
            owner_id,
            item_id,
            {
                "filename": "condition.png",
                "content_type": "image/png",
                "content": b"\x89PNG\r\n\x1a\nprivate-card-photo",
            },
            "Back mark",
        )
        item = app.row("SELECT * FROM collection_items WHERE id = ?", (item_id,))
        edit_html = app.render_collection_form(owner, item)

        hidden_token, hidden_link = app.create_collection_share_link(owner_id, item_id, "Condition review", 7, False, False)
        hidden_found = app.share_link_from_token(hidden_token, touch=False)
        hidden_html = app.render_shared_collection_card(hidden_found, hidden_token)
        visible_token, _ = app.create_collection_share_link(owner_id, item_id, "Full review", 0, True, True)
        visible_html = app.render_shared_collection_card(app.share_link_from_token(visible_token, touch=False), visible_token)
        stored = app.row("SELECT * FROM privacy_share_links WHERE id = ?", (hidden_link["id"],))

        self.assertIn("Private card links", edit_html)
        self.assertIn(f'action="/collection/{item_id}/share-links"', edit_html)
        self.assertNotEqual(stored["token_hash"], hidden_token)
        self.assertEqual(hidden_found["target_type"], "collection")
        self.assertIn("Private Card Link", hidden_html)
        self.assertIn("Small mark on the back.", hidden_html)
        self.assertNotIn("2 owned", hidden_html)
        self.assertIn("1 available for trade", hidden_html)
        self.assertNotIn("$42.00", hidden_html)
        self.assertNotIn(f"/share/{hidden_token}/photos/{photo_id}", hidden_html)
        self.assertIn("$42.00", visible_html)
        self.assertIn(f"/share/{visible_token}/photos/{photo_id}", visible_html)
        self.assertIn('referrerpolicy="no-referrer"', visible_html)

        self.assertEqual(app.revoke_collection_share_link(owner_id, item_id, hidden_link["id"]), 1)
        self.assertIsNone(app.share_link_from_token(hidden_token, touch=False))
        app.execute("DELETE FROM collection_items WHERE id = ?", (item_id,))
        self.assertEqual(app.row("SELECT COUNT(*) AS count FROM privacy_share_links WHERE target_type = 'collection' AND target_id = ?", (item_id,))["count"], 0)

    def test_share_only_wanted_card_links_render_on_edit_and_control_values(self):
        owner_id = factory.create_user("wantshareowner", display_name="Want Share Owner")
        owner = app.row("SELECT * FROM users WHERE id = ?", (owner_id,))
        want_id = factory.create_want_item(
            owner_id,
            "Shared Want",
            set_name="Desired Set",
            set_code="DSR",
            collector_number="17",
            desired_quantity=3,
            priority="high",
            budget_cap_usd="25.00",
            condition="NM,LP",
            finish="Regular,Foil",
            language="English",
            type_line="Artifact",
            preferred_printing_notes="Prefer the alternate art.",
            notes="Needed for the local league.",
            price_usd="21.50",
            price_source="scryfall",
            scryfall_uri="https://scryfall.example.test/shared-want",
            visibility="link",
            is_public=0,
        )
        want = app.row("SELECT * FROM want_items WHERE id = ?", (want_id,))
        edit_html = app.render_wants(owner, want, edit_want_id=want_id)

        hidden_token, hidden_link = app.create_want_share_link(owner_id, want_id, "League request", 7, False)
        hidden_found = app.share_link_from_token(hidden_token, touch=False)
        hidden_html = app.render_shared_want_card(hidden_found, hidden_token)
        visible_token, _ = app.create_want_share_link(owner_id, want_id, "Value request", 0, True)
        visible_html = app.render_shared_want_card(app.share_link_from_token(visible_token, touch=False), visible_token)
        stored = app.row("SELECT * FROM privacy_share_links WHERE id = ?", (hidden_link["id"],))

        self.assertIn("Private wanted-card links", edit_html)
        self.assertIn(f'action="/wants/{want_id}/share-links"', edit_html)
        self.assertNotEqual(stored["token_hash"], hidden_token)
        self.assertEqual(hidden_found["target_type"], "want")
        self.assertIn("Shared Want", hidden_html)
        self.assertIn("Want 3", hidden_html)
        self.assertIn("Prefer the alternate art.", hidden_html)
        self.assertIn("Condition: NM, LP", hidden_html)
        self.assertNotIn("$21.50", hidden_html)
        self.assertIn("$21.50", visible_html)

        self.assertEqual(app.revoke_want_share_link(owner_id, want_id, hidden_link["id"]), 1)
        self.assertIsNone(app.share_link_from_token(hidden_token, touch=False))
        app.execute("UPDATE want_items SET visibility = 'private' WHERE id = ?", (want_id,))
        self.assertIsNone(app.share_link_from_token(visible_token, touch=False))
        with self.assertRaisesRegex(ValueError, "Share-link only"):
            app.create_want_share_link(owner_id, want_id)
        app.execute("DELETE FROM want_items WHERE id = ?", (want_id,))
        self.assertEqual(app.row("SELECT COUNT(*) AS count FROM privacy_share_links WHERE target_type = 'want' AND target_id = ?", (want_id,))["count"], 0)

    def test_group_sharing_defaults_update_without_changing_existing_items(self):
        owner_id = factory.create_user("defaultowner", display_name="Default Owner")
        item_id = factory.create_collection_item(owner_id, "Existing Public Card", visibility="members", is_public=1)
        group_id = app.create_card_group(owner_id, "binder", "Default Binder")
        app.add_collection_item_to_group(owner_id, group_id, item_id, 1)

        updated = app.update_card_group_sharing_defaults(
            owner_id,
            group_id,
            "trusted",
            "private",
            False,
            False,
        )
        group = app.user_group(owner_id, group_id)
        item = app.row("SELECT * FROM collection_items WHERE id = ?", (item_id,))

        self.assertEqual(updated, 1)
        self.assertEqual(group["visibility"], "trusted")
        self.assertEqual(group["default_item_visibility"], "private")
        self.assertEqual(group["show_values"], 0)
        self.assertEqual(group["show_photos"], 0)
        self.assertEqual(item["visibility"], "members")

    def test_group_detail_sorts_collection_and_wishlist_items(self):
        user_id = app.create_user("groupsorter", "password123", "Group Sorter")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        deck_id = app.create_card_group(user_id, "deck", "Sorted deck")
        wishlist_id = app.create_card_group(user_id, "wishlist", "Sorted wants")
        for name, quantity, price in [
            ("Low Group Value", 1, "1.00"),
            ("High Group Value", 3, "5.00"),
        ]:
            card_id = app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, quantity, quantity_for_trade, price_usd, created_at, updated_at)
                VALUES (?, 'mtg', ?, 4, 1, ?, ?, ?)
                """,
                (user_id, name, price, app.now_iso(), app.now_iso()),
            )
            app.add_collection_item_to_group(user_id, deck_id, card_id, quantity)
        for name, desired in [("Small Group Want", 1), ("Big Group Want", 4)]:
            want_id = app.execute(
                """
                INSERT INTO want_items
                    (user_id, game, card_name, desired_quantity, created_at, updated_at)
                VALUES (?, 'mtg', ?, ?, ?, ?)
                """,
                (user_id, name, desired, app.now_iso(), app.now_iso()),
            )
            app.add_want_item_to_group(user_id, wishlist_id, want_id)

        deck_html = app.render_group_detail(user, deck_id, query={"sort": ["value"], "dir": ["desc"]})
        wishlist_html = app.render_group_detail(user, wishlist_id, query={"sort": ["qty"], "dir": ["desc"]})

        self.assertIn('name="sort"', deck_html)
        self.assertIn('value="value" selected', deck_html)
        self.assertLess(deck_html.index("High Group Value"), deck_html.index("Low Group Value"))
        self.assertIn('value="qty" selected', wishlist_html)
        self.assertLess(wishlist_html.index("Big Group Want"), wishlist_html.index("Small Group Want"))

    def test_group_contents_filter_paginate_and_bulk_remove_links_only(self):
        user_id = app.create_user("largegroup", "password123", "Large Group")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        deck_id = app.create_card_group(user_id, "deck", "Large deck")
        card_ids = []
        group_item_ids = []
        for index in range(30):
            card_id = factory.create_collection_item(
                user_id,
                f'Group Card {index:02d}',
                set_name="Filtered Set" if index >= 28 else "Main Set",
                condition="LP" if index >= 28 else "NM",
                finish="Foil" if index >= 28 else "Regular",
                quantity=4,
            )
            app.add_collection_item_to_group(user_id, deck_id, card_id, 1)
            card_ids.append(card_id)
        group_item_ids = [item["group_item_id"] for item in app.collection_group_items(deck_id)]

        first_page = app.render_group_detail(user, deck_id)
        second_page = app.render_group_detail(user, deck_id, query={"page": ["2"]})
        filtered = app.render_group_detail(
            user,
            deck_id,
            query={"q": ["Filtered Set"], "condition": ["LP"], "finish": ["Foil"]},
        )
        removed = app.remove_group_items(user_id, deck_id, group_item_ids[:2])

        self.assertIn("Showing 1-25 of 30", first_page)
        self.assertNotIn("<strong>1 x Group Card 29</strong>", first_page)
        self.assertIn("Showing 26-30 of 30", second_page)
        self.assertIn("<strong>1 x Group Card 29</strong>", second_page)
        self.assertIn('class="filter-bar group-item-filter-bar"', filtered)
        self.assertIn("Search: Filtered Set", filtered)
        self.assertIn("Condition: LP", filtered)
        self.assertIn("Finish: Foil", filtered)
        self.assertIn("Showing 1-2 of 2", filtered)
        self.assertIn(f'action="/groups/{deck_id}/items/bulk-delete"', filtered)
        self.assertIn(f'formaction="/groups/{deck_id}/items/bulk-update"', filtered)
        self.assertIn(f'formaction="/groups/{deck_id}/items/update-all"', filtered)
        self.assertIn(f'formaction="/groups/{deck_id}/items/delete-all"', filtered)
        self.assertIn('name="group_quantity"', filtered)
        self.assertIn('<input type="hidden" name="q" value="Filtered Set">', filtered)
        self.assertIn("Select page", filtered)
        self.assertEqual(removed, 2)
        self.assertEqual(app.collection_group_item_count(deck_id), 28)
        self.assertEqual(app.row("SELECT COUNT(*) AS count FROM collection_items WHERE user_id = ?", (user_id,))["count"], 30)

        remaining_group_item = app.collection_group_items(deck_id)[0]
        token, _expires_at = app.create_session(user_id)

        class RouteHarness:
            def __init__(self, form):
                self.form = form

            def read_form(self):
                return self.form

            def redirect(self, location):
                return location

            def not_found(self, current_user):
                raise AssertionError(f"Unexpected not found for {current_user['id']}")

            def flash_notice(self, notice, status="success"):
                return app.set_session_flash(token, notice, status)

        def assert_group_notice_redirect(location, notice):
            parsed = app.urlparse(location)
            query = app.parse_qs(parsed.query)
            flashed_notice, flashed_status = app.consume_session_flash(token)
            self.assertEqual(parsed.path, f"/groups/{deck_id}")
            self.assertEqual(parsed.fragment, "group-cards")
            self.assertNotIn("_notice", query)
            self.assertEqual(flashed_notice, notice)
            self.assertEqual(flashed_status, "success")
            self.assertEqual(app.consume_session_flash(token), ("", "info"))
            return query

        redirect = app.group_action(
            RouteHarness(
                {
                    "group_item_id": [str(remaining_group_item["group_item_id"])],
                    "group_quantity": ["3"],
                    "redirect_to": [f"/groups/{deck_id}?q=Group#group-cards"],
                }
            ),
            "POST",
            user,
            f"/groups/{deck_id}/items/bulk-update",
        )
        updated_row = app.row("SELECT quantity FROM group_collection_items WHERE id = ?", (remaining_group_item["group_item_id"],))
        redirect_query = assert_group_notice_redirect(redirect, "Updated 1 selected group card.")
        self.assertEqual(redirect_query["q"], ["Group"])
        self.assertEqual(updated_row["quantity"], 3)

        redirect = app.group_action(
            RouteHarness(
                {
                    "q": ["Filtered Set"],
                    "condition": ["LP"],
                    "finish": ["Foil"],
                    "group_quantity": ["2"],
                    "redirect_to": [f"/groups/{deck_id}?q=Filtered+Set&condition=LP&finish=Foil#group-cards"],
                }
            ),
            "POST",
            user,
            f"/groups/{deck_id}/items/update-all",
        )
        filtered_rows = app.collection_group_items(
            deck_id,
            filters={"q": "Filtered Set", "condition": "LP", "finish": "Foil"},
        )
        redirect_query = assert_group_notice_redirect(redirect, "Updated 2 matching group cards.")
        self.assertEqual(redirect_query["q"], ["Filtered Set"])
        self.assertEqual(redirect_query["condition"], ["LP"])
        self.assertEqual(redirect_query["finish"], ["Foil"])
        self.assertEqual([row["group_quantity"] for row in filtered_rows], [2, 2])

        redirect = app.group_action(
            RouteHarness(
                {
                    "group_item_id": [str(remaining_group_item["group_item_id"])],
                    "redirect_to": [f"/groups/{deck_id}?q=Group#group-cards"],
                }
            ),
            "POST",
            user,
            f"/groups/{deck_id}/items/bulk-delete",
        )
        redirect_query = assert_group_notice_redirect(redirect, "Removed 1 selected card from this group.")
        self.assertEqual(redirect_query["q"], ["Group"])
        self.assertEqual(app.collection_group_item_count(deck_id), 27)

        redirect = app.group_action(
            RouteHarness(
                {
                    "q": ["Filtered Set"],
                    "condition": ["LP"],
                    "finish": ["Foil"],
                    "redirect_to": [f"/groups/{deck_id}?q=Filtered+Set&condition=LP&finish=Foil#group-cards"],
                }
            ),
            "POST",
            user,
            f"/groups/{deck_id}/items/delete-all",
        )
        redirect_query = assert_group_notice_redirect(redirect, "Removed 2 matching cards from this group.")
        self.assertEqual(redirect_query["q"], ["Filtered Set"])
        self.assertEqual(redirect_query["condition"], ["LP"])
        self.assertEqual(redirect_query["finish"], ["Foil"])
        self.assertEqual(app.collection_group_item_count(deck_id, {"q": "Filtered Set", "condition": "LP", "finish": "Foil"}), 0)
        self.assertEqual(app.collection_group_item_count(deck_id), 25)
        self.assertEqual(app.row("SELECT COUNT(*) AS count FROM collection_items WHERE user_id = ?", (user_id,))["count"], 30)

    def test_wishlist_group_filters_by_priority_and_paginates(self):
        user_id = app.create_user("groupwants", "password123", "Group Wants")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        wishlist_id = app.create_card_group(user_id, "wishlist", "Large wishlist")
        for index in range(27):
            want_id = factory.create_want_item(
                user_id,
                f'Wanted Group Card {index:02d}',
                priority="urgent" if index >= 25 else "normal",
            )
            app.add_want_item_to_group(user_id, wishlist_id, want_id)

        filtered = app.render_group_detail(user, wishlist_id, query={"priority": ["urgent"]})

        self.assertEqual(app.wishlist_group_item_count(wishlist_id), 27)
        self.assertEqual(app.wishlist_group_item_count(wishlist_id, {"priority": "urgent"}), 2)
        self.assertIn("Priority: Urgent", filtered)
        self.assertIn("Showing 1-2 of 2", filtered)
        self.assertIn(f'formaction="/groups/{wishlist_id}/items/delete-all"', filtered)
        self.assertIn("Remove all matching", filtered)
        self.assertNotIn('name="group_quantity"', filtered)
        self.assertNotIn("<strong>Wanted Group Card 00</strong>", filtered)
        token, _expires_at = app.create_session(user_id)

        class RouteHarness:
            def read_form(self):
                return {
                    "priority": ["urgent"],
                    "redirect_to": [f"/groups/{wishlist_id}?priority=urgent#group-cards"],
                }

            def redirect(self, location):
                return location

            def not_found(self, current_user):
                raise AssertionError(f"Unexpected not found for {current_user['id']}")

            def flash_notice(self, notice, status="success"):
                return app.set_session_flash(token, notice, status)

        redirect = app.group_action(
            RouteHarness(),
            "POST",
            user,
            f"/groups/{wishlist_id}/items/delete-all",
        )
        parsed = app.urlparse(redirect)
        redirect_query = app.parse_qs(parsed.query)
        self.assertEqual(parsed.path, f"/groups/{wishlist_id}")
        self.assertEqual(parsed.fragment, "group-cards")
        self.assertEqual(redirect_query["priority"], ["urgent"])
        self.assertNotIn("_notice", redirect_query)
        self.assertEqual(app.consume_session_flash(token), ("Removed 2 matching wanted cards from this group.", "success"))
        self.assertEqual(app.consume_session_flash(token), ("", "info"))
        self.assertEqual(app.wishlist_group_item_count(wishlist_id, {"priority": "urgent"}), 0)
        self.assertEqual(app.wishlist_group_item_count(wishlist_id), 25)
        self.assertEqual(app.row("SELECT COUNT(*) AS count FROM want_items WHERE user_id = ?", (user_id,))["count"], 27)

    def test_deck_group_bulk_imports_csv_and_deck_text(self):
        user_id = app.create_user("deckimport", "password123", "Deck Import")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        deck_id = app.create_card_group(user_id, "deck", "Friday commander")
        for name, set_code, collector_number, quantity in (
            ("Lightning Bolt", "", "182", 3),
            ("Sol Ring", "", "", 1),
            ("Counterspell", "DMR", "45", 4),
        ):
            app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, set_code, collector_number, quantity, quantity_for_trade, created_at, updated_at)
                VALUES (?, 'mtg', ?, ?, ?, ?, 0, ?, ?)
                """,
                (user_id, name, set_code, collector_number, quantity, app.now_iso(), app.now_iso()),
            )
        csv_bytes = (
            "Quantity,Name,Edition,Collector Number,Foil,Condition,Language\n"
            "1,Lightning Bolt,Secret Lair,182,Foil,NM,English\n"
            "2,Lightning Bolt,Secret Lair,182,Foil,NM,English\n"
        ).encode("utf-8")
        deck_text = """
Commander
1 Sol Ring #Mana
Deck
4 Counterspell (DMR) 45
Maybeboard
1 Mana Crypt
"""

        csv_result = app.import_deck_group_csv(user_id, deck_id, csv_bytes, source="archidekt", enrich_scryfall=False)
        text_result = app.import_deck_group_text(user_id, deck_id, deck_text, enrich_scryfall=False)
        grouped = {
            (item["card_name"], item["set_code"], item["collector_number"]): item["group_quantity"]
            for item in app.collection_group_items(deck_id)
        }
        html = app.render_group_detail(user, deck_id, import_result=text_result)

        self.assertEqual(csv_result["grouped"], 3)
        self.assertEqual(text_result["grouped"], 5)
        self.assertEqual(text_result["missing"], 0)
        self.assertEqual(grouped[("Lightning Bolt", "", "182")], 3)
        self.assertEqual(grouped[("Counterspell", "DMR", "45")], 4)
        self.assertEqual(grouped[("Sol Ring", "", "")], 1)
        self.assertNotIn(("Mana Crypt", "", ""), grouped)
        self.assertIn("Bulk import deck", html)
        self.assertIn("Deck-list URL", html)

    def test_deck_url_json_extracts_moxfield_and_archidekt_shapes(self):
        moxfield_items, _ = app.decklist_items_from_json({
            "commanders": {
                "Atraxa, Praetors' Voice": {
                    "quantity": 1,
                    "card": {"name": "Atraxa, Praetors' Voice", "set": "c16", "collector_number": "28"},
                }
            },
            "mainboard": {
                "Sol Ring": {
                    "quantity": 1,
                    "card": {"name": "Sol Ring", "set": "cmm", "collector_number": "703", "scryfall_id": "sf-sol"},
                }
            },
            "maybeboard": {
                "Mana Crypt": {"quantity": 1, "card": {"name": "Mana Crypt"}}
            },
        })
        archidekt_items, warnings = app.decklist_items_from_json({
            "cards": [
                {"quantity": 1, "card": {"name": "Arcane Signet", "set": "c20", "collector_number": "252"}},
                {"quantity": 1, "categories": ["Maybeboard"], "card": {"name": "Mana Crypt"}},
            ]
        })

        self.assertEqual([item["card_name"] for item in moxfield_items], ["Atraxa, Praetors' Voice", "Sol Ring"])
        self.assertEqual(moxfield_items[1]["set_code"], "CMM")
        self.assertEqual(moxfield_items[1]["scryfall_id"], "sf-sol")
        self.assertEqual(len(archidekt_items), 1)
        self.assertEqual(archidekt_items[0]["card_name"], "Arcane Signet")
        self.assertEqual(archidekt_items[0]["collector_number"], "252")
        self.assertTrue(any("Excluded" in warning for warning in warnings))

    def test_deck_url_candidates_include_known_text_export_adapters(self):
        tappedout = app.deck_import_candidate_urls("https://tappedout.net/mtg-decks/example-deck/")
        deckstats = app.deck_import_candidate_urls("https://deckstats.net/decks/12/34-example")

        self.assertIn("fmt=txt", tappedout[0])
        self.assertIn("export_txt=1", deckstats[0])
        self.assertEqual(tappedout[-1], "https://tappedout.net/mtg-decks/example-deck/")
        self.assertEqual(deckstats[-1], "https://deckstats.net/decks/12/34-example")

    def test_deck_url_validation_blocks_private_hosts_and_embedded_credentials(self):
        with self.assertRaisesRegex(ValueError, "public deck-building sites"):
            app.validate_deck_import_url("http://127.0.0.1:8000/private-deck")
        with self.assertRaisesRegex(ValueError, "embedded credentials"):
            app.validate_deck_import_url("https://user:secret@example.com/deck")

        parsed = app.validate_deck_import_url("https://93.184.216.34/deck")
        self.assertEqual(parsed.hostname, "93.184.216.34")

    def test_deck_import_review_detects_optional_sections(self):
        user_id = app.create_user("sectionreview", "password123", "Section Review")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        deck_id = app.create_card_group(user_id, "deck", "Sectioned deck")
        for name, quantity in (("Sol Ring", 1), ("Mana Crypt", 1), ("Treasure Token", 3)):
            app.execute(
                """
                INSERT INTO collection_items
                    (user_id, game, card_name, quantity, quantity_for_trade, created_at, updated_at)
                VALUES (?, 'mtg', ?, ?, 0, ?, ?)
                """,
                (user_id, name, quantity, app.now_iso(), app.now_iso()),
            )
        deck_text = """
Deck
1 Sol Ring
Sideboard
2 Dispel
Maybeboard
1 Mana Crypt
Tokens
3 Treasure Token
Considering
1 Rhystic Study
"""

        section_rows, warnings = app.deck_import_sections_from_text(deck_text)
        review = app.deck_import_preview_payload("decklist", section_rows, warnings=warnings, enrich_scryfall=False, merge=True)
        html = app.render_group_detail(user, deck_id, import_review=review)
        result = app.import_deck_group_sections(
            user_id,
            deck_id,
            section_rows,
            included_sections={"maybeboard", "tokens"},
            enrich_scryfall=False,
        )
        grouped = {
            item["card_name"]: item["group_quantity"]
            for item in app.collection_group_items(deck_id)
        }

        self.assertTrue(app.deck_import_sections_need_review(section_rows))
        self.assertIn("Review detected sections", html)
        self.assertIn("Sideboard", html)
        self.assertIn("Maybeboard", html)
        self.assertIn("Tokens", html)
        self.assertIn("Considering", html)
        self.assertEqual(result["grouped"], 5)
        self.assertEqual(grouped["Sol Ring"], 1)
        self.assertEqual(grouped["Mana Crypt"], 1)
        self.assertEqual(grouped["Treasure Token"], 3)
        self.assertNotIn("Dispel", grouped)
        self.assertNotIn("Rhystic Study", grouped)

    def test_deck_import_preview_commits_and_undoes_group_changes(self):
        user_id = app.create_user("deckpreview", "password123", "Deck Preview")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        deck_id = app.create_card_group(user_id, "deck", "Preview deck")
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 2, 0, ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        section_rows, warnings = app.deck_import_sections_from_text("1 Sol Ring\n1 Mana Crypt\n")

        preview = app.preview_deck_group_import(
            user_id,
            deck_id,
            section_rows,
            enrich_scryfall=False,
            warnings=warnings,
        )
        preview_html = app.render_group_detail(user, deck_id, import_review=preview)
        before_commit = app.collection_group_items(deck_id)
        result = app.commit_deck_import_preview(user_id, deck_id, preview["batch_id"])
        grouped = app.collection_group_items(deck_id)
        result_html = app.render_group_detail(user, deck_id, import_result=result)
        undo = app.undo_import_batch(user_id, preview["batch_id"])
        after_undo = app.collection_group_items(deck_id)
        batch = app.row("SELECT * FROM import_batches WHERE id = ?", (preview["batch_id"],))

        self.assertEqual(preview["grouped"], 1)
        self.assertEqual(preview["missing"], 1)
        self.assertEqual(before_commit, [])
        self.assertIn("Preview deck import", preview_html)
        self.assertIn("Import deck cards", preview_html)
        self.assertIn('<table class="responsive-card-table import-preview-card-table">', preview_html)
        self.assertIn('data-label="Quality"', preview_html)
        self.assertEqual(result["grouped"], 1)
        self.assertEqual(result["missing"], 1)
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["card_name"], "Sol Ring")
        self.assertIn("Add missing cards to wishlist", result_html)
        self.assertEqual(undo["undone_items"], 1)
        self.assertEqual(after_undo, [])
        self.assertEqual(batch["status"], "undone")

    def test_deck_import_prompts_missing_cards_for_grouped_wishlist(self):
        user_id = app.create_user("deckmissing", "password123", "Deck Missing")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        deck_id = app.create_card_group(user_id, "deck", "Boros build")
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 1, 0, ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        deck_text = """
Deck
2 Sol Ring
1 Mana Crypt
"""

        result = app.import_deck_group_text(user_id, deck_id, deck_text, enrich_scryfall=False)
        html = app.render_group_detail(user, deck_id, import_result=result)

        self.assertEqual(result["grouped"], 1)
        self.assertEqual(result["missing"], 2)
        self.assertEqual({item["card_name"]: item["quantity"] for item in result["missing_items"]}, {"Sol Ring": 1, "Mana Crypt": 1})
        self.assertIn("Add missing cards to wishlist", html)
        self.assertIn('/groups/1/missing-wants', html)
        self.assertIn("1 x Sol Ring", html)
        self.assertIn("1 x Mana Crypt", html)

    def test_deck_missing_cards_can_be_added_to_new_wishlist_group(self):
        user_id = app.create_user("deckwants", "password123", "Deck Wants")
        deck_id = app.create_card_group(user_id, "deck", "Dimir build")
        deck_text = """
Deck
1 Rhystic Study
2 Counterspell
"""
        result = app.import_deck_group_text(user_id, deck_id, deck_text, enrich_scryfall=False)
        selected = [item["key"] for item in result["missing_items"]]

        added = app.add_deck_missing_items_to_wishlist(
            user_id,
            deck_id,
            result["missing_items"],
            selected,
            new_group_name="Dimir wants",
        )
        wishlist = app.user_group(user_id, added["wishlist_group_id"])
        wants = app.wishlist_group_items(added["wishlist_group_id"])

        self.assertEqual(added["added"], 2)
        self.assertEqual(wishlist["group_type"], "wishlist")
        self.assertEqual(wishlist["name"], "Dimir wants")
        self.assertEqual({want["card_name"]: want["desired_quantity"] for want in wants}, {"Counterspell": 2, "Rhystic Study": 1})

    def test_collection_and_wants_csv_exports(self):
        user_id = app.create_user("exporter", "password123", "Exporter")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, price_usd, price_source, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 2, 1, '1.25', 'scryfall', ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, type_line, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Forest', 'Basic Land', 4, 0, ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, desired_quantity, condition, finish, created_at, updated_at)
            VALUES (?, 'mtg', 'Rhystic Study', 1, 'NM,LP', 'Foil', ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )

        collection_filename, collection_csv = app.export_collection_csv(user_id, {"q": ["sol"]})
        wants_filename, wants_csv = app.export_wants_csv(user_id)
        collection_rows = list(csv.DictReader(io.StringIO(collection_csv.decode("utf-8-sig"))))
        want_rows = list(csv.DictReader(io.StringIO(wants_csv.decode("utf-8-sig"))))
        collection_html = app.render_collection(user, {})
        wants_html = app.render_wants(user)

        self.assertTrue(collection_filename.startswith("binderbridge-collection-"))
        self.assertTrue(wants_filename.startswith("binderbridge-wants-"))
        self.assertEqual([row["card_name"] for row in collection_rows], ["Sol Ring"])
        self.assertEqual(collection_rows[0]["quantity_for_trade"], "1")
        self.assertEqual(want_rows[0]["card_name"], "Rhystic Study")
        self.assertEqual(want_rows[0]["condition"], "NM,LP")
        self.assertIn('href="/collection/export?page=1"', collection_html)
        self.assertIn('href="/wants/export"', wants_html)

    def test_collection_statistics_summarize_values_and_breakdowns(self):
        user_id = app.create_user("statsuser", "password123", "Stats User")
        user = app.row("SELECT * FROM users WHERE id = ?", (user_id,))
        deck_id = app.create_card_group(user_id, "deck", "Stats deck")
        binder_id = app.create_card_group(user_id, "binder", "Stats binder")
        sol_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, set_code, collector_number, type_line, rarity,
                 condition, finish, language, quantity, quantity_for_trade, color_identity,
                 scryfall_id, image_url, price_usd, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 'CMM', '703', 'Artifact', 'uncommon',
                    'NM', 'Regular', 'English', 2, 1, '', 'sol-id', 'https://img.example/sol.jpg',
                    '1.25', 1, ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        bolt_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, set_code, collector_number, type_line, rarity,
                 condition, finish, language, quantity, quantity_for_trade, color_identity,
                 scryfall_id, price_usd, is_public, created_at, updated_at)
            VALUES (?, 'mtg', 'Lightning Bolt', 'Secret Lair', 'SLD', '182', 'Instant', 'rare',
                    'LP', 'Foil', 'Japanese', 1, 1, 'R', 'bolt-id', '2.00', 0, ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, condition, finish, language,
                 quantity, quantity_for_trade, is_public, created_at, updated_at)
            VALUES (?, 'pokemon', 'Pikachu', 'Base Set', 'NM', 'Regular', 'English',
                    3, 0, 1, ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        app.add_collection_item_to_group(user_id, deck_id, sol_id, 1)
        app.add_collection_item_to_group(user_id, binder_id, bolt_id, 1)

        stats = app.collection_statistics(user_id)
        html = app.render_collection_statistics(user)
        collection_html = app.render_collection(user, {})

        self.assertEqual(stats["total_cards"], 6)
        self.assertEqual(stats["trade_cards"], 2)
        self.assertEqual(stats["unique_entries"], 3)
        self.assertEqual(stats["unique_cards"], 3)
        self.assertEqual(stats["total_value_cents"], 450)
        self.assertEqual(stats["trade_value_cents"], 325)
        self.assertEqual(stats["priced_cards"], 3)
        self.assertEqual(stats["public_cards"], 5)
        self.assertEqual(stats["private_cards"], 1)
        self.assertEqual(stats["price_coverage_percent"], 50.0)
        self.assertEqual(stats["buckets"]["condition"][0]["label"], "NM")
        self.assertEqual(stats["buckets"]["condition"][0]["quantity"], 5)
        self.assertEqual(stats["buckets"]["game"][0]["quantity"], 3)
        self.assertIn("Collection stats", html)
        self.assertIn("$4.50", html)
        self.assertIn("Rarity mix", html)
        self.assertIn("Condition mix", html)
        self.assertIn("Language coverage", html)
        self.assertIn("Most valuable entries", html)
        self.assertIn("Deck", html)
        self.assertIn("Binder", html)
        self.assertIn('href="/collection/stats"', collection_html)

    def test_group_csv_exports_decks_and_wishlist_groups(self):
        user_id = app.create_user("groupexport", "password123", "Group Export")
        card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, set_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 'Commander Masters', 2, 0, ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        want_id = app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, desired_quantity, created_at, updated_at)
            VALUES (?, 'mtg', 'Mana Crypt', 1, ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        deck_id = app.create_card_group(user_id, "deck", "Export Deck")
        wishlist_id = app.create_card_group(user_id, "wishlist", "Export Wants")
        app.add_collection_item_to_group(user_id, deck_id, card_id, 2)
        app.add_want_item_to_group(user_id, wishlist_id, want_id)

        deck_filename, deck_csv = app.export_group_csv(user_id, deck_id)
        wishlist_filename, wishlist_csv = app.export_group_csv(user_id, wishlist_id)
        deck_rows = list(csv.DictReader(io.StringIO(deck_csv.decode("utf-8-sig"))))
        wishlist_rows = list(csv.DictReader(io.StringIO(wishlist_csv.decode("utf-8-sig"))))

        self.assertIn("export-deck", deck_filename)
        self.assertIn("export-wants", wishlist_filename)
        self.assertEqual(deck_rows[0]["group_name"], "Export Deck")
        self.assertEqual(deck_rows[0]["group_quantity"], "2")
        self.assertEqual(deck_rows[0]["card_name"], "Sol Ring")
        self.assertEqual(wishlist_rows[0]["group_name"], "Export Wants")
        self.assertEqual(wishlist_rows[0]["desired_quantity"], "1")
        self.assertEqual(wishlist_rows[0]["card_name"], "Mana Crypt")

    def test_full_account_json_export_includes_user_owned_data_without_password_hash(self):
        user_id = app.create_user("fullexport", "password123", "Full Export")
        other_id = app.create_user("other", "password123", "Other")
        card_id = app.execute(
            """
            INSERT INTO collection_items
                (user_id, game, card_name, quantity, quantity_for_trade, created_at, updated_at)
            VALUES (?, 'mtg', 'Sol Ring', 1, 0, ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        want_id = app.execute(
            """
            INSERT INTO want_items
                (user_id, game, card_name, desired_quantity, created_at, updated_at)
            VALUES (?, 'mtg', 'Mana Crypt', 1, ?, ?)
            """,
            (user_id, app.now_iso(), app.now_iso()),
        )
        group_id = app.create_card_group(user_id, "deck", "Exported Deck")
        app.add_collection_item_to_group(user_id, group_id, card_id, 1)
        app.create_notification(user_id, "trade_status", "Export notice", "Included in account export.")
        trade_id = app.execute(
            """
            INSERT INTO trades (proposer_id, recipient_id, status, created_at, updated_at)
            VALUES (?, ?, 'completed', ?, ?)
            """,
            (user_id, other_id, app.now_iso(), app.now_iso()),
        )

        filename, data = app.export_account_json(user_id)
        exported = json.loads(data.decode("utf-8"))

        self.assertIn("fullexport", filename)
        self.assertEqual(exported["format"], "binderbridge-account-export")
        self.assertEqual(exported["account"]["username"], "fullexport")
        self.assertNotIn("password_hash", exported["account"])
        self.assertEqual(exported["collection"][0]["card_name"], "Sol Ring")
        self.assertEqual(exported["wants"][0]["id"], want_id)
        self.assertEqual(exported["groups"][0]["name"], "Exported Deck")
        self.assertEqual(exported["groups"][0]["items"][0]["card_name"], "Sol Ring")
        self.assertEqual(exported["trades"][0]["id"], trade_id)
        self.assertEqual(exported["notifications"][0]["title"], "Export notice")
