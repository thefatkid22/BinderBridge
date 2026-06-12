"""Deck-group import orchestration and missing-card wishlist support for BinderBridge."""

import base64
import json

def deck_group_item_row(group_id, collection_item_id):
    return row(
        """
        SELECT *
        FROM group_collection_items
        WHERE group_id = ? AND collection_item_id = ?
        """,
        (group_id, collection_item_id),
    )

def deck_import_result(source, total_rows, warnings=None):
    result = {
        "source": source,
        "total_rows": total_rows,
        "inserted": 0,
        "updated": 0,
        "enriched": 0,
        "queued": 0,
        "not_found": 0,
        "grouped": 0,
        "matched": 0,
        "missing": 0,
        "missing_entries": 0,
        "missing_items": [],
        "warning_count": 0,
        "warnings": [],
    }
    for warning in warnings or []:
        add_import_warning(result, warning)
    return result

def deck_import_preview_from_items(user_id, group_id, items, source="decklist", enrich_scryfall=True, merge=True, warnings=None):
    group = user_group(user_id, group_id)
    if not group or group["group_type"] != "deck":
        raise ValueError("Deck imports are only available for deck groups.")
    result = deck_import_result(source, len(items), warnings)
    result["preview"] = True
    result["merge"] = bool(merge)
    result["rows"] = []
    lookup_cache = {}
    deck_cards = {}
    for item in items:
        import_item = item
        if enrich_scryfall and item["game"] == "mtg":
            import_item = apply_local_scryfall_data(item, lookup_cache)
            if import_item is not item:
                result["enriched"] += 1
            else:
                result["not_found"] += 1

        key = deck_import_collection_key(import_item)
        if key not in deck_cards:
            deck_cards[key] = dict(import_item)
            deck_cards[key]["quantity"] = 0
        deck_cards[key]["quantity"] = min(MAX_CARD_QUANTITY, deck_cards[key]["quantity"] + int(item["quantity"] or 0))

    for deck_item in deck_cards.values():
        remaining = max(0, int(deck_item["quantity"] or 0))
        owned_total = 0
        for collection_item in deck_import_collection_matches(user_id, deck_item):
            if remaining <= 0:
                break
            available = max(0, int(collection_item["quantity"] or 0))
            if available <= 0:
                continue
            group_quantity = min(available, remaining)
            existing_group_item = deck_group_item_row(group_id, collection_item["id"])
            result["grouped"] += group_quantity
            result["matched"] += group_quantity
            owned_total += group_quantity
            remaining -= group_quantity
            if len(result["rows"]) < 12:
                result["rows"].append({
                    "action": "update group" if existing_group_item else "add to group",
                    "card_name": collection_item["card_name"],
                    "set_name": collection_item["set_name"],
                    "set_code": collection_item["set_code"],
                    "collector_number": collection_item["collector_number"],
                    "quantity": group_quantity,
                    "quantity_for_trade": collection_item["quantity_for_trade"],
                    "finish": collection_item["finish"],
                    "condition": collection_item["condition"],
                    "note": "Already in this group" if existing_group_item else "Owned copy matched",
                })
        if remaining > 0:
            missing = deck_missing_item(deck_item, remaining, owned_total)
            result["missing"] += remaining
            result["missing_entries"] += 1
            result["missing_items"].append(missing)
            add_import_warning(result, f"{missing['card_name']}: missing {remaining} from your collection.")
            if len(result["rows"]) < 12:
                result["rows"].append(import_preview_row(missing, "missing", "Can be added to wishlist after import"))
    assign_deck_missing_item_keys(result["missing_items"])
    return result

def preview_deck_group_import(user_id, group_id, section_rows, included_sections=None, source="decklist", enrich_scryfall=True, merge=True, warnings=None):
    included = set(included_sections or DECK_IMPORT_DEFAULT_SECTIONS) | {DECK_IMPORT_MAIN_SECTION}
    import_warnings = list(warnings or [])
    import_warnings.extend(deck_import_exclusion_warnings(section_rows, included))
    items = deck_import_items_from_sections(section_rows, included)
    preview = deck_import_preview_from_items(
        user_id,
        group_id,
        items,
        source=source,
        enrich_scryfall=enrich_scryfall,
        merge=merge,
        warnings=import_warnings,
    )
    payload = {
        "sections": {
            section: [dict(item) for item in rows_for_section]
            for section, rows_for_section in section_rows.items()
            if rows_for_section
        },
        "included_sections": sorted(included),
        "warnings": list(warnings or []),
        "source": source,
        "enrich_scryfall": bool(enrich_scryfall),
        "merge": bool(merge),
    }
    batch_id = create_import_batch(user_id, "deck_group", source, "preview", preview, payload, group_id=group_id)
    preview["batch_id"] = batch_id
    update_import_batch(batch_id, summary=preview)
    return preview

def commit_deck_import_preview(user_id, group_id, batch_id):
    batch = import_batch_for_user(user_id, batch_id)
    if not batch or batch["import_type"] != "deck_group" or batch["status"] != "preview" or int(batch["group_id"] or 0) != int(group_id):
        raise ValueError("Deck import preview was not found. Please submit the deck list again.")
    payload = import_batch_payload(batch)
    sections = {section: [] for section in DECK_IMPORT_SECTION_LABELS}
    raw_sections = payload.get("sections", {})
    if not isinstance(raw_sections, dict):
        raise ValueError("Deck import preview was not found. Please submit the deck list again.")
    for section, raw_items in raw_sections.items():
        if not isinstance(raw_items, list):
            continue
        sections.setdefault(section, [])
        for item in raw_items:
            if isinstance(item, dict) and item.get("card_name"):
                sections[section].append(deck_import_item(
                    item.get("card_name", ""),
                    item.get("quantity", 1),
                    game=item.get("game", "mtg"),
                    set_name=item.get("set_name", ""),
                    set_code=item.get("set_code", ""),
                    collector_number=item.get("collector_number", ""),
                    finish=item.get("finish", "Regular"),
                    condition=item.get("condition", "NM"),
                    language=item.get("language", "English"),
                    scryfall_id=item.get("scryfall_id", ""),
                    notes=item.get("notes", ""),
                ))
    included_sections = set(payload.get("included_sections") or DECK_IMPORT_DEFAULT_SECTIONS)
    result = import_deck_group_sections(
        user_id,
        group_id,
        sections,
        included_sections=included_sections,
        source=payload.get("source", batch["source"] or "decklist"),
        enrich_scryfall=bool(payload.get("enrich_scryfall")),
        merge=bool(payload.get("merge")),
        warnings=payload.get("warnings", []),
        batch_id=batch["id"],
    )
    result["batch_id"] = batch["id"]
    update_import_batch(batch["id"], status="applied", summary=result, payload={})
    return result

def import_deck_group_items(user_id, group_id, items, source="decklist", enrich_scryfall=True, merge=True, warnings=None, batch_id=None):
    group = user_group(user_id, group_id)
    if not group or group["group_type"] != "deck":
        raise ValueError("Deck imports are only available for deck groups.")
    result = deck_import_result(source, len(items), warnings)

    lookup_cache = {}
    deck_cards = {}
    for item in items:
        import_item = item
        if enrich_scryfall and item["game"] == "mtg":
            import_item = apply_local_scryfall_data(item, lookup_cache)
            if import_item is not item:
                result["enriched"] += 1
            else:
                result["not_found"] += 1

        key = deck_import_collection_key(import_item)
        if key not in deck_cards:
            deck_cards[key] = dict(import_item)
            deck_cards[key]["quantity"] = 0
        deck_cards[key]["quantity"] = min(MAX_CARD_QUANTITY, deck_cards[key]["quantity"] + int(item["quantity"] or 0))

    for deck_item in deck_cards.values():
        remaining = max(0, int(deck_item["quantity"] or 0))
        owned_total = 0
        for collection_item in deck_import_collection_matches(user_id, deck_item):
            if remaining <= 0:
                break
            available = max(0, int(collection_item["quantity"] or 0))
            if available <= 0:
                continue
            group_quantity = min(available, remaining)
            existing_group_item = deck_group_item_row(group_id, collection_item["id"])
            previous_state = record_state(existing_group_item)
            add_collection_item_to_group(user_id, group_id, collection_item["id"], group_quantity)
            updated_group_item = deck_group_item_row(group_id, collection_item["id"])
            if updated_group_item:
                record_import_batch_item(
                    batch_id,
                    "group_collection_item",
                    "updated" if existing_group_item else "inserted",
                    "group_collection_items",
                    updated_group_item["id"],
                    previous_state,
                )
            result["grouped"] += group_quantity
            result["matched"] += group_quantity
            owned_total += group_quantity
            remaining -= group_quantity
        if remaining > 0:
            missing = deck_missing_item(deck_item, remaining, owned_total)
            result["missing"] += remaining
            result["missing_entries"] += 1
            result["missing_items"].append(missing)
            add_import_warning(result, f"{missing['card_name']}: missing {remaining} from your collection.")
    assign_deck_missing_item_keys(result["missing_items"])
    return result

def deck_import_collection_key(item):
    return (
        str(item.get("game") or "mtg").strip().lower(),
        str(item.get("card_name") or "").strip().lower(),
    )

def deck_import_collection_matches(user_id, item):
    return rows(
        """
        SELECT *
        FROM collection_items
        WHERE user_id = ?
            AND game = ?
            AND card_name = ? COLLATE NOCASE
            AND quantity > 0
        ORDER BY
            CASE
                WHEN ? != '' AND scryfall_id = ? THEN 0
                WHEN ? != '' AND collector_number != '' AND set_code = ? COLLATE NOCASE AND collector_number = ? COLLATE NOCASE THEN 1
                ELSE 2
            END,
            quantity DESC,
            set_name COLLATE NOCASE,
            collector_number COLLATE NOCASE
        """,
        (
            user_id,
            item.get("game", "mtg"),
            item.get("card_name", ""),
            item.get("scryfall_id", ""),
            item.get("scryfall_id", ""),
            item.get("set_code", ""),
            item.get("set_code", ""),
            item.get("collector_number", ""),
        ),
    )

def deck_missing_item(item, missing_quantity, owned_quantity=0):
    missing = dict(item)
    missing["quantity"] = int(missing_quantity)
    missing["owned_quantity"] = int(owned_quantity or 0)
    missing["desired_quantity"] = int(missing_quantity)
    return missing

def assign_deck_missing_item_keys(items):
    for index, item in enumerate(items):
        item["key"] = f"{index}:{item.get('game', 'mtg')}:{item.get('card_name', '').strip().lower()}"

def import_deck_group_sections(user_id, group_id, section_rows, included_sections=None, source="decklist", enrich_scryfall=True, merge=True, warnings=None, batch_id=None):
    included = set(included_sections or DECK_IMPORT_DEFAULT_SECTIONS) | {DECK_IMPORT_MAIN_SECTION}
    import_warnings = list(warnings or [])
    import_warnings.extend(deck_import_exclusion_warnings(section_rows, included))
    items = deck_import_items_from_sections(section_rows, included)
    return import_deck_group_items(
        user_id,
        group_id,
        items,
        source=source,
        enrich_scryfall=enrich_scryfall,
        merge=merge,
        warnings=import_warnings,
        batch_id=batch_id,
    )

def deck_import_preview_payload(source, section_rows, warnings=None, enrich_scryfall=True, merge=True, source_url=""):
    normalized_sections = {
        section: [dict(item) for item in items]
        for section, items in section_rows.items()
        if items
    }
    return {
        "source": source,
        "sections": normalized_sections,
        "warnings": list(warnings or []),
        "enrich_scryfall": bool(enrich_scryfall),
        "merge": bool(merge),
        "source_url": source_url,
    }

def encode_deck_import_payload(payload):
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")

def decode_deck_import_payload(encoded_payload):
    try:
        raw = base64.urlsafe_b64decode(str(encoded_payload or "").encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Deck import review expired. Please submit the deck list again.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("sections"), dict):
        raise ValueError("Deck import review expired. Please submit the deck list again.")
    sections = {section: [] for section in DECK_IMPORT_SECTION_LABELS}
    for section, items in payload.get("sections", {}).items():
        if not isinstance(items, list):
            continue
        sections.setdefault(section, [])
        for item in items:
            if isinstance(item, dict) and item.get("card_name"):
                sections[section].append(deck_import_item(
                    item.get("card_name", ""),
                    item.get("quantity", 1),
                    set_name=item.get("set_name", ""),
                    set_code=item.get("set_code", ""),
                    collector_number=item.get("collector_number", ""),
                    finish=item.get("finish", "Regular"),
                    condition=item.get("condition", "NM"),
                    language=item.get("language", "English"),
                    scryfall_id=item.get("scryfall_id", ""),
                    notes=item.get("notes", ""),
                ))
    return {
        "source": str(payload.get("source") or "decklist"),
        "sections": sections,
        "warnings": [str(warning) for warning in payload.get("warnings", []) if str(warning).strip()],
        "enrich_scryfall": bool(payload.get("enrich_scryfall")),
        "merge": bool(payload.get("merge")),
        "source_url": str(payload.get("source_url") or ""),
    }

def encode_deck_missing_wants_payload(items):
    payload_items = []
    for item in items or []:
        price_usd = normalize_price_usd(item.get("price_usd", ""))
        payload_items.append({
            "key": str(item.get("key") or ""),
            "game": str(item.get("game") or "mtg"),
            "card_name": str(item.get("card_name") or ""),
            "set_name": str(item.get("set_name") or ""),
            "set_code": str(item.get("set_code") or ""),
            "collector_number": str(item.get("collector_number") or ""),
            "finish": str(item.get("finish") or ""),
            "language": str(item.get("language") or ""),
            "scryfall_id": str(item.get("scryfall_id") or ""),
            "image_url": str(item.get("image_url") or ""),
            "mana_cost": str(item.get("mana_cost") or ""),
            "type_line": str(item.get("type_line") or ""),
            "oracle_text": str(item.get("oracle_text") or ""),
            "rarity": str(item.get("rarity") or ""),
            "colors": str(item.get("colors") or ""),
            "color_identity": str(item.get("color_identity") or ""),
            "scryfall_uri": str(item.get("scryfall_uri") or ""),
            "price_usd": price_usd,
            "price_source": "scryfall" if price_usd else "",
            "quantity": max(1, clamp_quantity(item.get("quantity") or item.get("desired_quantity"), 1)),
        })
    return encode_deck_import_payload({"items": payload_items})

def decode_deck_missing_wants_payload(encoded_payload):
    try:
        raw = base64.urlsafe_b64decode(str(encoded_payload or "").encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Missing-card prompt expired. Please import the deck again.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("Missing-card prompt expired. Please import the deck again.")
    items = []
    for item in payload["items"][:300]:
        if not isinstance(item, dict) or not str(item.get("card_name") or "").strip():
            continue
        clean = deck_import_item(
            item.get("card_name", ""),
            max(1, clamp_quantity(item.get("quantity"), 1)),
            game=item.get("game", "mtg"),
            set_name=item.get("set_name", ""),
            set_code=item.get("set_code", ""),
            collector_number=item.get("collector_number", ""),
            finish=item.get("finish", "Regular"),
            language=item.get("language", "English"),
            scryfall_id=item.get("scryfall_id", ""),
        )
        for field in SCRYFALL_COLLECTION_FIELDS:
            clean[field] = str(item.get(field) or "")[:5000]
        clean["price_usd"] = normalize_price_usd(item.get("price_usd", ""))
        clean["price_source"] = "scryfall" if clean["price_usd"] else ""
        clean["key"] = str(item.get("key") or "")
        items.append(clean)
    return items

def deck_missing_want_data(deck_group, item):
    data = {field: str(item.get(field) or "") for field in SCRYFALL_COLLECTION_FIELDS}
    price_usd = normalize_price_usd(item.get("price_usd", ""))
    data.update({
        "game": item.get("game", "mtg"),
        "card_name": item.get("card_name", ""),
        "set_name": item.get("set_name", ""),
        "set_code": item.get("set_code", ""),
        "collector_number": item.get("collector_number", ""),
        "desired_quantity": max(1, clamp_quantity(item.get("quantity"), 1)),
        "priority": "normal",
        "budget_cap_usd": "",
        "condition": "",
        "finish": item.get("finish", "") if item.get("finish") not in ("", "Regular") else "",
        "language": "",
        "price_usd": price_usd,
        "price_source": "scryfall" if price_usd else "",
        "preferred_printing_notes": "",
        "notes": f"Missing from deck: {deck_group['name']}",
        "is_public": 1,
        "lookup_on_save": "1" if item.get("scryfall_id") else "",
    })
    return data

def existing_group_want_match(user_id, wishlist_group_id, item):
    return row(
        """
        SELECT want_items.*
        FROM group_want_items
        JOIN want_items ON want_items.id = group_want_items.want_item_id
        WHERE group_want_items.group_id = ?
            AND want_items.user_id = ?
            AND want_items.game = ?
            AND want_items.card_name = ? COLLATE NOCASE
            AND want_items.set_code = ? COLLATE NOCASE
            AND want_items.collector_number = ? COLLATE NOCASE
        LIMIT 1
        """,
        (
            wishlist_group_id,
            user_id,
            item.get("game", "mtg"),
            item.get("card_name", ""),
            item.get("set_code", ""),
            item.get("collector_number", ""),
        ),
    )

def add_deck_missing_items_to_wishlist(user_id, deck_group_id, items, selected_keys, wishlist_group_id=0, new_group_name="", is_public=True):
    deck_group = user_group(user_id, deck_group_id)
    if not deck_group or deck_group["group_type"] != "deck":
        raise ValueError("Deck not found.")
    selected_keys = {str(key) for key in selected_keys if str(key).strip()}
    selected_items = [item for item in items if str(item.get("key") or "") in selected_keys]
    if not selected_items:
        raise ValueError("Choose at least one missing card to add.")
    try:
        wishlist_group_id = int(wishlist_group_id or 0)
    except (TypeError, ValueError):
        wishlist_group_id = 0
    wishlist_group = user_group(user_id, wishlist_group_id) if wishlist_group_id else None
    if wishlist_group and wishlist_group["group_type"] != "wishlist":
        raise ValueError("Choose a wishlist group.")
    if not wishlist_group:
        name = sanitize_text_input(new_group_name, max_length=80).strip() or f"{deck_group['name']} missing cards"
        wishlist_group_id = create_card_group(
            user_id,
            "wishlist",
            name,
            f"Cards missing from deck: {deck_group['name']}",
            is_public=is_public,
        )
        wishlist_group = user_group(user_id, wishlist_group_id)

    added = 0
    updated = 0
    for item in selected_items:
        desired_quantity = max(1, clamp_quantity(item.get("quantity"), 1))
        existing = existing_group_want_match(user_id, wishlist_group_id, item)
        if existing:
            execute(
                """
                UPDATE want_items
                SET desired_quantity = MAX(desired_quantity, ?), updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (desired_quantity, now_iso(), existing["id"], user_id),
            )
            updated += 1
            continue
        want_data = deck_missing_want_data(deck_group, item)
        want_data["visibility"] = row_value(wishlist_group, "default_item_visibility", VISIBILITY_MEMBERS)
        want_data["is_public"] = visibility_to_public_flag(want_data["visibility"])
        want_id = insert_want_item(user_id, want_data)
        add_want_item_to_group(user_id, wishlist_group_id, want_id)
        added += 1
    return {
        "wishlist_group_id": wishlist_group_id,
        "wishlist_group_name": wishlist_group["name"] if wishlist_group else "",
        "added": added,
        "updated": updated,
        "selected": len(selected_items),
    }

def import_deck_group_csv(user_id, group_id, csv_bytes, source="auto", enrich_scryfall=True, merge=True, field_mapping=None):
    section_rows, warnings = normalize_csv_rows_by_section(
        csv_bytes,
        default_game="mtg",
        default_trade_quantity=0,
        field_mapping=field_mapping,
        source=source,
    )
    return import_deck_group_sections(
        user_id,
        group_id,
        section_rows,
        source=source,
        enrich_scryfall=enrich_scryfall,
        merge=merge,
        warnings=warnings,
    )

def import_deck_group_text(user_id, group_id, deck_text, source="decklist", enrich_scryfall=True, merge=True):
    section_rows, warnings = deck_import_sections_from_text(deck_text)
    return import_deck_group_sections(
        user_id,
        group_id,
        section_rows,
        source=source,
        enrich_scryfall=enrich_scryfall,
        merge=merge,
        warnings=warnings,
    )

def import_deck_group_url(user_id, group_id, source_url, enrich_scryfall=True, merge=True):
    _, section_rows, warnings = deck_import_sections_from_url(source_url)
    result = import_deck_group_sections(
        user_id,
        group_id,
        section_rows,
        source="url",
        enrich_scryfall=enrich_scryfall,
        merge=merge,
        warnings=warnings,
    )
    result["source_url"] = source_url
    return result

__all__ = [
    'deck_group_item_row',
    'deck_import_result',
    'deck_import_preview_from_items',
    'preview_deck_group_import',
    'commit_deck_import_preview',
    'import_deck_group_items',
    'deck_import_collection_key',
    'deck_import_collection_matches',
    'deck_missing_item',
    'assign_deck_missing_item_keys',
    'import_deck_group_sections',
    'deck_import_preview_payload',
    'encode_deck_import_payload',
    'decode_deck_import_payload',
    'encode_deck_missing_wants_payload',
    'decode_deck_missing_wants_payload',
    'deck_missing_want_data',
    'existing_group_want_match',
    'add_deck_missing_items_to_wishlist',
    'import_deck_group_csv',
    'import_deck_group_text',
    'import_deck_group_url',
]
