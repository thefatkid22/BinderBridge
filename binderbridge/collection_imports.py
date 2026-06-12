"""Collection CSV preview and import orchestration for BinderBridge."""

def collection_import_existing_match(user_id, data, merge=True):
    if not merge:
        return None
    values = collection_item_values(data)
    return row(
        """
        SELECT *
        FROM collection_items
        WHERE user_id = ?
            AND game = ?
            AND card_name = ? COLLATE NOCASE
            AND set_name = ?
            AND set_code = ?
            AND collector_number = ?
            AND finish = ?
            AND condition = ?
            AND language = ?
        """,
        (
            user_id,
            values["game"],
            values["card_name"],
            values["set_name"],
            values["set_code"],
            values["collector_number"],
            values["finish"],
            values["condition"],
            values["language"],
        ),
    )

def import_preview_row(import_item, action, note=""):
    return {
        "action": action,
        "card_name": import_item.get("card_name", ""),
        "set_name": import_item.get("set_name", ""),
        "set_code": import_item.get("set_code", ""),
        "collector_number": import_item.get("collector_number", ""),
        "quantity": int(import_item.get("quantity") or 0),
        "quantity_for_trade": int(import_item.get("quantity_for_trade") or 0),
        "finish": import_item.get("finish", ""),
        "condition": import_item.get("condition", ""),
        "note": note,
    }

def collection_import_preview_from_items(
    user_id,
    items,
    warnings=None,
    source="auto",
    default_game="mtg",
    default_trade_quantity=0,
    enrich_scryfall=False,
    merge=True,
    allow_scryfall_finish_mismatch=False,
):
    result = {
        "source": source,
        "total_rows": len(items),
        "inserted": 0,
        "updated": 0,
        "enriched": 0,
        "queued": 0,
        "not_found": 0,
        "skipped": 0,
        "warning_count": 0,
        "warnings": [],
        "rows": [],
        "preview": True,
        "default_game": default_game,
        "default_trade_quantity": default_trade_quantity,
        "enrich_scryfall": bool(enrich_scryfall),
        "merge": bool(merge),
        "allow_scryfall_finish_mismatch": bool(allow_scryfall_finish_mismatch),
    }
    for warning in warnings or []:
        add_import_warning(result, warning)

    lookup_cache = {}
    for item in items:
        import_item = item
        queued = False
        if enrich_scryfall and item["game"] == "mtg":
            import_item = apply_local_scryfall_data(item, lookup_cache)
            if import_item is not item:
                result["enriched"] += 1
            else:
                queued = True
                result["queued"] += 1
        mismatch_message = scryfall_finish_check_message(import_item)
        if mismatch_message and not allow_scryfall_finish_mismatch:
            result["skipped"] += 1
            add_import_warning(result, f"{mismatch_message} Row would be skipped. Enable the Scryfall finish override to import it anyway.")
            if len(result["rows"]) < 12:
                result["rows"].append(import_preview_row(import_item, "skipped", "Finish mismatch"))
            continue
        if mismatch_message:
            add_import_warning(result, f"{mismatch_message} Override would allow this row.")
        if collection_import_existing_match(user_id, import_item, merge=merge):
            result["updated"] += 1
            action = "update"
        else:
            result["inserted"] += 1
            action = "insert"
        if len(result["rows"]) < 12:
            result["rows"].append(import_preview_row(import_item, action, "Queued for Scryfall lookup" if queued else ""))
    return result

def preview_collection_import_csv(
    user_id,
    csv_bytes,
    source="auto",
    default_game="mtg",
    default_trade_quantity=0,
    enrich_scryfall=False,
    merge=True,
    allow_scryfall_finish_mismatch=False,
    field_mapping=None,
):
    items, warnings = normalize_csv_rows(
        csv_bytes,
        default_game,
        default_trade_quantity,
        field_mapping=field_mapping,
        source=source,
        target="collection",
    )
    preview = collection_import_preview_from_items(
        user_id,
        items,
        warnings=warnings,
        source=source,
        default_game=default_game,
        default_trade_quantity=default_trade_quantity,
        enrich_scryfall=enrich_scryfall,
        merge=merge,
        allow_scryfall_finish_mismatch=allow_scryfall_finish_mismatch,
    )
    payload = {
        "items": items,
        "warnings": warnings,
        "source": source,
        "default_game": default_game,
        "default_trade_quantity": default_trade_quantity,
        "enrich_scryfall": bool(enrich_scryfall),
        "merge": bool(merge),
        "allow_scryfall_finish_mismatch": bool(allow_scryfall_finish_mismatch),
    }
    batch_id = create_import_batch(user_id, "collection_csv", source, "preview", preview, payload)
    preview["batch_id"] = batch_id
    update_import_batch(batch_id, summary=preview)
    return preview

def collection_import_result(source, total_rows, warnings=None):
    result = {
        "source": source,
        "total_rows": total_rows,
        "inserted": 0,
        "updated": 0,
        "enriched": 0,
        "queued": 0,
        "not_found": 0,
        "skipped": 0,
        "warning_count": 0,
        "warnings": [],
    }
    for warning in warnings or []:
        add_import_warning(result, warning)
    return result

def import_collection_items(
    user_id,
    items,
    warnings=None,
    source="auto",
    enrich_scryfall=False,
    merge=True,
    allow_scryfall_finish_mismatch=False,
    batch_id=None,
):
    result = collection_import_result(source, len(items), warnings)
    lookup_cache = {}
    for item in items:
        import_item = item
        queue_item = False
        if enrich_scryfall and item["game"] == "mtg":
            import_item = apply_local_scryfall_data(item, lookup_cache)
            if import_item is not item:
                result["enriched"] += 1
            else:
                queue_item = True
        mismatch_message = scryfall_finish_check_message(import_item)
        if mismatch_message:
            if allow_scryfall_finish_mismatch:
                add_import_warning(result, f"{mismatch_message} Override allowed this row.")
            else:
                result["skipped"] += 1
                add_import_warning(result, f"{mismatch_message} Row skipped. Enable the Scryfall finish override to import it anyway.")
                continue

        existing = collection_import_existing_match(user_id, import_item, merge=merge)
        previous_state = record_state(existing)
        action, item_id = upsert_collection_item(user_id, import_item, merge=merge, return_id=True)
        result["inserted" if action == "inserted" else "updated"] += 1
        record_import_batch_item(batch_id, "collection_item", action, "collection_items", item_id, previous_state)
        if queue_item and enqueue_scryfall_enrichment(item_id, user_id, item):
            result["queued"] += 1
    return result

def commit_collection_import_preview(user_id, batch_id):
    batch = import_batch_for_user(user_id, batch_id)
    if not batch or batch["import_type"] != "collection_csv" or batch["status"] != "preview":
        raise ValueError("Import preview was not found. Please upload the CSV again.")
    payload = import_batch_payload(batch)
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("Import preview was not found. Please upload the CSV again.")
    result = import_collection_items(
        user_id,
        items[:MAX_CSV_ROWS],
        warnings=payload.get("warnings", []),
        source=payload.get("source", batch["source"] or "auto"),
        enrich_scryfall=bool(payload.get("enrich_scryfall")),
        merge=bool(payload.get("merge")),
        allow_scryfall_finish_mismatch=bool(payload.get("allow_scryfall_finish_mismatch")),
        batch_id=batch["id"],
    )
    result["batch_id"] = batch["id"]
    update_import_batch(batch["id"], status="applied", summary=result, payload={})
    return result

def import_collection_csv(
    user_id,
    csv_bytes,
    source="auto",
    default_game="mtg",
    default_trade_quantity=0,
    enrich_scryfall=False,
    merge=True,
    allow_scryfall_finish_mismatch=False,
    field_mapping=None,
):
    items, warnings = normalize_csv_rows(
        csv_bytes,
        default_game,
        default_trade_quantity,
        field_mapping=field_mapping,
        source=source,
        target="collection",
    )
    batch_id = create_import_batch(
        user_id,
        "collection_csv",
        source,
        "applied",
        {"total_rows": len(items), "source": source},
        {},
    )
    result = import_collection_items(
        user_id,
        items,
        warnings=warnings,
        source=source,
        enrich_scryfall=enrich_scryfall,
        merge=merge,
        allow_scryfall_finish_mismatch=allow_scryfall_finish_mismatch,
        batch_id=batch_id,
    )
    result["batch_id"] = batch_id
    update_import_batch(batch_id, summary=result)
    return result

__all__ = [
    'collection_import_existing_match',
    'import_preview_row',
    'collection_import_preview_from_items',
    'preview_collection_import_csv',
    'collection_import_result',
    'import_collection_items',
    'commit_collection_import_preview',
    'import_collection_csv',
]
