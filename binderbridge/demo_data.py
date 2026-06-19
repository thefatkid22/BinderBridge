"""Demo data seeding for local evaluation and UI smoke testing.

The app facade injects shared helpers into this module. Demo seeding is safe by
default: startup seeding only runs on empty databases, while the CLI can reset
the known demo users explicitly for repeatable local testing.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone


DEMO_PASSWORD = "password123"
DEMO_USERNAMES = ("alice", "bob", "cara", "drew")
DEMO_EMAIL_DOMAIN = "demo.binderbridge.test"


def _iso_days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()


def _demo_text_upload(filename, text):
    content = text.encode("utf-8")
    return {
        "filename": filename,
        "content_type": "text/plain",
        "content": content,
    }


def _demo_png_upload(filename, caption="Demo condition photo"):
    # 1x1 transparent PNG.
    content = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\x0bIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
        b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    return {
        "filename": filename,
        "content_type": "image/png",
        "content": content,
        "caption": caption,
    }


def _delete_known_demo_data():
    placeholders = ", ".join("?" for _ in DEMO_USERNAMES)
    with db() as conn:
        conn.execute(
            f"DELETE FROM users WHERE username COLLATE NOCASE IN ({placeholders})",
            DEMO_USERNAMES,
        )
        conn.execute(
            "DELETE FROM registration_invites WHERE email LIKE ?",
            (f"%@{DEMO_EMAIL_DOMAIN}",),
        )
    return {"deleted_users": len(DEMO_USERNAMES)}


def _create_demo_user(username, display_name, role="member", bio="", trusted=False):
    user_id = create_user(
        username,
        DEMO_PASSWORD,
        display_name,
        email=f"{username}@{DEMO_EMAIL_DOMAIN}",
        role=role,
    )
    execute(
        """
        UPDATE users
        SET bio = ?, trusted_override = ?, public_email = ?, updated_at = ?
        WHERE id = ?
        """,
        (bio, 1 if trusted else 0, 1 if role in ("owner", "organizer") else 0, now_iso(), user_id),
    )
    return user_id


def _collection(user_id, card_name, **overrides):
    data = {
        "game": "mtg",
        "card_name": card_name,
        "set_name": "",
        "set_code": "",
        "collector_number": "",
        "finish": "Regular",
        "condition": "NM",
        "condition_notes": "",
        "language": "English",
        "quantity": 1,
        "quantity_for_trade": 0,
        "type_line": "",
        "rarity": "",
        "color_identity": "",
        "scryfall_uri": "",
        "price_usd": "",
        "notes": "Demo data",
        "visibility": "members",
    }
    data.update(overrides)
    _action, item_id = upsert_collection_item(user_id, data, merge=False, return_id=True)
    if data.get("price_usd"):
        old_price = data.get("demo_previous_price_usd", "")
        if old_price:
            record_price_history_for_item(
                item_id,
                user_id,
                {**data, "id": item_id},
                "",
                old_price,
                observed_at=_iso_days_ago(10),
            )
        record_price_history_for_item(item_id, user_id, {**data, "id": item_id}, old_price, data["price_usd"])
    return item_id


def _want(user_id, card_name, **overrides):
    data = {
        "game": "mtg",
        "card_name": card_name,
        "set_name": "",
        "set_code": "",
        "collector_number": "",
        "desired_quantity": 1,
        "priority": "normal",
        "budget_cap_usd": "",
        "condition": "NM,LP",
        "finish": "Regular",
        "language": "English",
        "type_line": "",
        "price_usd": "",
        "price_source": "scryfall" if overrides.get("price_usd") else "",
        "preferred_printing_notes": "",
        "notes": "Demo wishlist entry",
        "visibility": "members",
    }
    data.update(overrides)
    return insert_want_item(user_id, data)


def _add_demo_photo(collection_item_id):
    upload = _demo_png_upload("demo-condition.png", "Demo close-up for condition review")
    add_collection_item_photo(
        row("SELECT user_id FROM collection_items WHERE id = ?", (collection_item_id,))["user_id"],
        collection_item_id,
        upload,
        upload["caption"],
    )


def _group(user_id, group_type, name, description, visibility="members"):
    return create_card_group(
        user_id,
        group_type,
        name,
        description,
        is_public=visibility != "private",
        visibility=visibility,
        default_item_visibility=visibility,
        show_values=True,
        show_photos=True,
    )


def _item(item_id):
    return row("SELECT * FROM collection_items WHERE id = ?", (item_id,))


def _seed_demo_dataset():
    alice_id = _create_demo_user(
        "alice",
        "Alice Owner",
        role="owner",
        trusted=True,
        bio="Runs the local Commander night and keeps the trade rules tidy.",
    )
    bob_id = _create_demo_user(
        "bob",
        "Bob Trader",
        trusted=True,
        bio="Usually brings a small red deck box full of staples.",
    )
    cara_id = _create_demo_user(
        "cara",
        "Cara Organizer",
        role="organizer",
        bio="Builds casual decks and helps newer players find upgrades.",
    )
    drew_id = _create_demo_user(
        "drew",
        "Drew Readonly",
        role="read_only",
        bio="Demo read-only account for browsing without mutation access.",
    )

    alice_cards = {
        "sol_ring": _collection(
            alice_id,
            "Sol Ring",
            set_name="Commander Masters",
            set_code="CMM",
            collector_number="703",
            quantity=3,
            quantity_for_trade=1,
            type_line="Artifact",
            rarity="uncommon",
            price_usd="1.49",
            demo_previous_price_usd="1.10",
            scryfall_uri="https://scryfall.com/search?q=!%22Sol+Ring%22",
            notes="Extra copy for small trades.",
        ),
        "counterspell": _collection(
            alice_id,
            "Counterspell",
            set_name="Dominaria Remastered",
            set_code="DMR",
            collector_number="45",
            quantity=4,
            quantity_for_trade=2,
            finish="Foil",
            condition="LP",
            condition_notes="Light clouding on one foil copy.",
            type_line="Instant",
            rarity="common",
            color_identity="U",
            price_usd="0.79",
            scryfall_uri="https://scryfall.com/search?q=!%22Counterspell%22",
        ),
        "cyclonic_rift": _collection(
            alice_id,
            "Cyclonic Rift",
            set_name="Return to Ravnica",
            set_code="RTR",
            collector_number="35",
            quantity=1,
            quantity_for_trade=0,
            visibility="private",
            type_line="Instant",
            rarity="rare",
            color_identity="U",
            price_usd="32.50",
            notes="Private personal copy, not visible to the group.",
        ),
        "swords": _collection(
            alice_id,
            "Swords to Plowshares",
            set_name="Commander Legends",
            set_code="CMR",
            collector_number="50",
            quantity=2,
            quantity_for_trade=1,
            type_line="Instant",
            rarity="uncommon",
            color_identity="W",
            price_usd="1.10",
        ),
    }

    bob_cards = {
        "lightning_bolt": _collection(
            bob_id,
            "Lightning Bolt",
            set_name="Magic 2011",
            set_code="M11",
            collector_number="149",
            quantity=4,
            quantity_for_trade=2,
            finish="Regular",
            condition="NM",
            type_line="Instant",
            rarity="common",
            color_identity="R",
            price_usd="1.25",
            scryfall_uri="https://scryfall.com/search?q=!%22Lightning+Bolt%22",
        ),
        "arcane_signet": _collection(
            bob_id,
            "Arcane Signet",
            set_name="Commander Masters",
            set_code="CMM",
            collector_number="739",
            quantity=3,
            quantity_for_trade=1,
            type_line="Artifact",
            rarity="common",
            price_usd="0.85",
        ),
        "path": _collection(
            bob_id,
            "Path to Exile",
            set_name="Double Masters",
            set_code="2XM",
            collector_number="25",
            quantity=2,
            quantity_for_trade=1,
            condition="MP",
            condition_notes="Playable but visibly shuffled.",
            type_line="Instant",
            rarity="uncommon",
            color_identity="W",
            price_usd="1.40",
        ),
        "command_tower": _collection(
            bob_id,
            "Command Tower",
            set_name="Commander Masters",
            set_code="CMM",
            collector_number="430",
            quantity=5,
            quantity_for_trade=3,
            type_line="Land",
            rarity="common",
            price_usd="0.55",
        ),
    }

    cara_cards = {
        "llanowar": _collection(
            cara_id,
            "Llanowar Elves",
            set_name="Dominaria",
            set_code="DOM",
            collector_number="168",
            quantity=4,
            quantity_for_trade=2,
            type_line="Creature - Elf Druid",
            rarity="common",
            color_identity="G",
            price_usd="0.25",
        ),
        "rhystic": _collection(
            cara_id,
            "Rhystic Study",
            set_name="Wilds of Eldraine: Enchanting Tales",
            set_code="WOT",
            collector_number="25",
            quantity=1,
            quantity_for_trade=0,
            visibility="trusted",
            type_line="Enchantment",
            rarity="mythic",
            color_identity="U",
            price_usd="42.00",
        ),
        "nature": _collection(
            cara_id,
            "Nature's Lore",
            set_name="Commander Masters",
            set_code="CMM",
            collector_number="304",
            quantity=3,
            quantity_for_trade=1,
            type_line="Sorcery",
            rarity="common",
            color_identity="G",
            price_usd="0.90",
        ),
    }

    _add_demo_photo(bob_cards["path"])

    alice_wants = {
        "bolt": _want(
            alice_id,
            "Lightning Bolt",
            priority="high",
            budget_cap_usd="2.00",
            type_line="Instant",
            color_identity="R",
            price_usd="1.25",
            preferred_printing_notes="Any clean nonfoil copy is fine.",
        ),
        "tower": _want(
            alice_id,
            "Command Tower",
            priority="normal",
            budget_cap_usd="1.00",
            type_line="Land",
            price_usd="0.55",
        ),
    }
    bob_wants = {
        "counterspell": _want(
            bob_id,
            "Counterspell",
            priority="high",
            finish="Regular,Foil",
            budget_cap_usd="1.50",
            type_line="Instant",
            color_identity="U",
            price_usd="0.79",
        ),
        "swords": _want(
            bob_id,
            "Swords to Plowshares",
            priority="normal",
            budget_cap_usd="2.00",
            type_line="Instant",
            color_identity="W",
            price_usd="1.10",
        ),
    }
    cara_wants = {
        "sol_ring": _want(
            cara_id,
            "Sol Ring",
            priority="high",
            budget_cap_usd="2.00",
            type_line="Artifact",
            price_usd="1.49",
        ),
        "path": _want(
            cara_id,
            "Path to Exile",
            priority="low",
            condition="LP,MP",
            budget_cap_usd="1.50",
            type_line="Instant",
            color_identity="W",
            price_usd="1.40",
        ),
    }
    _want(
        drew_id,
        "Arcane Signet",
        priority="normal",
        budget_cap_usd="1.00",
        type_line="Artifact",
        price_usd="0.85",
        notes="Read-only user can browse this want but cannot edit it.",
    )

    alice_binder = _group(alice_id, "binder", "Commander Trade Binder", "Staples Alice is willing to trade at meetups.")
    add_collection_item_to_group(alice_id, alice_binder, alice_cards["sol_ring"], 1)
    add_collection_item_to_group(alice_id, alice_binder, alice_cards["counterspell"], 2)
    add_collection_item_to_group(alice_id, alice_binder, alice_cards["swords"], 1)

    alice_wishlist = _group(alice_id, "wishlist", "Friday Night Upgrade Queue", "Cards Alice is actively looking for this week.")
    add_want_item_to_group(alice_id, alice_wishlist, alice_wants["bolt"])
    add_want_item_to_group(alice_id, alice_wishlist, alice_wants["tower"])

    bob_binder = _group(bob_id, "binder", "Red Box Trade Binder", "A small public binder with condition photos and quick-trade cards.")
    for item_id in bob_cards.values():
        add_collection_item_to_group(bob_id, bob_binder, item_id, 1)

    cara_deck = _group(cara_id, "deck", "Selesnya Tokens", "A casual deck list with a few missing upgrades.")
    add_collection_item_to_group(cara_id, cara_deck, cara_cards["llanowar"], 3)
    add_collection_item_to_group(cara_id, cara_deck, cara_cards["nature"], 2)

    pending_trade = create_trade_offer(
        alice_id,
        bob_id,
        "Would Counterspell for Lightning Bolt work before Commander night?",
        [(_item(alice_cards["counterspell"]), 1)],
        [(_item(bob_cards["lightning_bolt"]), 1)],
    )
    add_trade_comment(pending_trade, bob_id, "Looks close. I may counter if I find another blue card I need.")

    completed_trade = create_trade_offer(
        bob_id,
        cara_id,
        "Arcane Signet for Llanowar Elves keeps both decks moving.",
        [(_item(bob_cards["arcane_signet"]), 1)],
        [(_item(cara_cards["llanowar"]), 1)],
    )
    update_trade_response(completed_trade, cara_id, "accepted", "Works for me.", fairness_acknowledged=True)
    complete_trade(completed_trade, completed_by_user_id=bob_id)
    submit_trade_feedback(completed_trade, bob_id, "5", "Easy meetup trade and clear communication.")
    submit_trade_feedback(completed_trade, cara_id, "5", "Card was exactly as described.")

    disputed_trade = create_trade_offer(
        cara_id,
        alice_id,
        "Could you use Nature's Lore for Sol Ring?",
        [(_item(cara_cards["nature"]), 1)],
        [(_item(alice_cards["sol_ring"]), 1)],
    )
    update_trade_response(disputed_trade, alice_id, "accepted", "Let's review condition first.", fairness_acknowledged=True)
    dispute_id = create_trade_dispute(
        disputed_trade,
        alice_id,
        "condition",
        "The card looks more played than the listed condition. Adding a note so an admin can review the example flow.",
        evidence_upload=_demo_text_upload(
            "condition-note.txt",
            "Demo evidence: front lower-left corner has more whitening than expected.",
        ),
        evidence_note="Text evidence included for preview testing.",
    )
    update_trade_dispute_admin(
        dispute_id,
        alice_id,
        "reviewing",
        "Demo issue moved to review so the admin queue has an active example.",
    )

    invite = create_registration_invite(alice_id, f"newmember@{DEMO_EMAIL_DOMAIN}", base_url="http://127.0.0.1:8000")
    create_notification(alice_id, "admin_notice", "Demo data ready", "Sample users, cards, trades, groups, and notifications were seeded.", "/admin")
    create_notification(bob_id, "watchlist_alert", "Wishlist match available", "Alice has Counterspell available for trade.", "/browse?q=Counterspell")
    create_notification(cara_id, "scryfall_import", "Demo import complete", "Sample card data was added without contacting Scryfall.", "/import")

    demo_ids = (alice_id, bob_id, cara_id, drew_id)
    placeholders = ", ".join("?" for _ in demo_ids)
    return {
        "users": 4,
        "collection_items": row(
            f"SELECT COUNT(*) AS count FROM collection_items WHERE user_id IN ({placeholders})",
            demo_ids,
        )["count"],
        "wants": row(
            f"SELECT COUNT(*) AS count FROM want_items WHERE user_id IN ({placeholders})",
            demo_ids,
        )["count"],
        "groups": row(
            f"SELECT COUNT(*) AS count FROM card_groups WHERE user_id IN ({placeholders})",
            demo_ids,
        )["count"],
        "trades": row(
            f"SELECT COUNT(*) AS count FROM trades WHERE proposer_id IN ({placeholders}) OR recipient_id IN ({placeholders})",
            (*demo_ids, *demo_ids),
        )["count"],
        "disputes": row(
            f"""
            SELECT COUNT(*) AS count
            FROM trade_disputes
            JOIN trades ON trades.id = trade_disputes.trade_id
            WHERE trades.proposer_id IN ({placeholders}) OR trades.recipient_id IN ({placeholders})
            """,
            (*demo_ids, *demo_ids),
        )["count"],
        "notifications": row(
            f"SELECT COUNT(*) AS count FROM user_notifications WHERE user_id IN ({placeholders})",
            demo_ids,
        )["count"],
        "invite_id": invite["id"],
    }


def seed_demo_data(enabled=None, reset=False, allow_existing=False):
    if enabled is None:
        enabled = config_bool("BINDERBRIDGE_DEMO", default=False, section="app", key="demo")
    if not enabled and not reset:
        return {"seeded": False, "reason": "disabled"}

    if reset:
        _delete_known_demo_data()

    placeholders = ", ".join("?" for _ in DEMO_USERNAMES)
    demo_user_count = row(
        f"SELECT COUNT(*) AS count FROM users WHERE username COLLATE NOCASE IN ({placeholders})",
        DEMO_USERNAMES,
    )["count"]
    if demo_user_count:
        return {"seeded": False, "reason": "demo_exists", "demo_users": demo_user_count}

    user_count = row("SELECT COUNT(*) AS count FROM users")["count"]
    if user_count and not allow_existing:
        return {"seeded": False, "reason": "existing_users", "users": user_count}

    summary = _seed_demo_dataset()
    return {"seeded": True, "accounts": {username: DEMO_PASSWORD for username in DEMO_USERNAMES}, **summary}


__all__ = [
    "DEMO_PASSWORD",
    "DEMO_USERNAMES",
    "seed_demo_data",
]
