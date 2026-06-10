"""Collection, import, cleanup, and wishlist HTTP route handlers.

These functions are attached to app.App by app.py after the class is defined.
Shared helpers are injected by the app facade at import time.
"""

def collection_export(self, user, query):
    filename, data = export_collection_csv(user["id"], query)
    return self.binary(data, "text/csv; charset=utf-8", filename)

def wants_export(self, user):
    filename, data = export_wants_csv(user["id"])
    return self.binary(data, "text/csv; charset=utf-8", filename)

def cleanup_page(self, user, notice=None, status="info"):
    return self.html(render_cleanup(user, notice=notice, status=status))

def cleanup_collection(self, user):
    form = self.read_form()
    result = cleanup_collection_duplicates(user["id"], form.get("group_key", []))
    notice = f"Merged {result['merged']} duplicate collection row{'s' if result['merged'] != 1 else ''} across {result['groups']} group{'s' if result['groups'] != 1 else ''}."
    return self.cleanup_page(user, notice=notice)

def cleanup_wants(self, user):
    form = self.read_form()
    result = cleanup_want_duplicates(user["id"], form.get("group_key", []))
    notice = f"Merged {result['merged']} duplicate wanted-card row{'s' if result['merged'] != 1 else ''} across {result['groups']} group{'s' if result['groups'] != 1 else ''}."
    return self.cleanup_page(user, notice=notice)

def condition_finish_audit_page(self, user, query=None, notice=None, status="info"):
    return self.html(render_condition_finish_audit(user, query or {}, notice=notice, status=status))

def condition_finish_audit_query_from_form(self, form):
    redirect_to = safe_local_redirect_path(
        form.get("redirect_to", ["/cleanup/audit"])[0],
        default="/cleanup/audit",
        allowed_prefix="/cleanup/audit",
    )
    return sanitize_form_values(parse_qs(urlparse(redirect_to).query, keep_blank_values=True, max_num_fields=MAX_FORM_FIELDS))

def condition_finish_audit_update(self, user):
    form = self.read_form()
    query = self.condition_finish_audit_query_from_form(form)
    try:
        condition, finish = parse_condition_finish_audit_update(form)
    except ValueError as exc:
        return self.condition_finish_audit_page(user, query, notice=str(exc), status="error")
    updated = update_collection_condition_finish_by_ids(user["id"], form.get("item_id", []), condition, finish)
    notice = f"Updated {updated} selected card{'s' if updated != 1 else ''}."
    return self.condition_finish_audit_page(user, query, notice=notice)

def condition_finish_audit_update_all(self, user):
    form = self.read_form()
    query = self.condition_finish_audit_query_from_form(form)
    filters = condition_finish_audit_filter_values(form)
    try:
        condition, finish = parse_condition_finish_audit_update(form)
    except ValueError as exc:
        return self.condition_finish_audit_page(user, query, notice=str(exc), status="error")
    updated = update_collection_condition_finish_matching(user["id"], filters, condition, finish)
    notice = f"Updated {updated} matching card{'s' if updated != 1 else ''}."
    return self.condition_finish_audit_page(user, query, notice=notice)

def condition_finish_audit_normalize(self, user):
    form = self.read_form()
    query = self.condition_finish_audit_query_from_form(form)
    updated = normalize_collection_condition_finish_by_ids(user["id"], form.get("item_id", []))
    notice = f"Normalized {updated} selected card{'s' if updated != 1 else ''}."
    return self.condition_finish_audit_page(user, query, notice=notice)

def condition_finish_audit_normalize_all(self, user):
    form = self.read_form()
    query = self.condition_finish_audit_query_from_form(form)
    filters = condition_finish_audit_filter_values(form)
    updated = normalize_collection_condition_finish_matching(user["id"], filters)
    notice = f"Normalized {updated} matching card{'s' if updated != 1 else ''}."
    return self.condition_finish_audit_page(user, query, notice=notice)

def collection_import(self, method, user):
    if method == "GET":
        return self.html(render_import(user))
    fields, files = self.read_multipart_form()
    intent = fields.get("intent", ["preview"])[0]
    if intent == "commit_preview":
        try:
            result = commit_collection_import_preview(user["id"], fields.get("batch_id", ["0"])[0])
        except ValueError as exc:
            return self.html(render_import(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
        if result.get("queued"):
            start_scryfall_enrichment_worker()
        notice = f"Imported {result['inserted'] + result['updated']} collection rows."
        if result.get("queued"):
            notice += f" {result['queued']} rows queued for background Scryfall enrichment."
        return self.html(render_import(user, result=result, notice=notice))
    upload = files.get("csv_file")
    if not upload or not upload["content"]:
        return self.html(render_import(user, notice="Choose a CSV file to import.", status="error"), HTTPStatus.BAD_REQUEST)
    source = fields.get("source", ["auto"])[0]
    default_game = fields.get("game", ["mtg"])[0]
    if source not in dict(CSV_SOURCE_OPTIONS):
        source = "auto"
    if default_game not in dict(CARD_GAMES):
        default_game = "mtg"
    default_trade_quantity = clamp_quantity(fields.get("default_trade_quantity", ["0"])[0], 0)
    enrich_scryfall = fields.get("enrich_scryfall", [""])[0] == "1"
    merge_duplicates = fields.get("merge_duplicates", [""])[0] == "1"
    allow_scryfall_finish_mismatch = fields.get("scryfall_finish_override", [""])[0] == "1"
    field_mapping = csv_import_mapping_for_user(
        user["id"],
        fields.get("mapping_preset_id", ["0"])[0],
        is_admin=bool(user["is_admin"]),
        import_target="collection",
    )
    try:
        if intent == "import_now":
            result = import_collection_csv(
                user["id"],
                upload["content"],
                source=source,
                default_game=default_game,
                default_trade_quantity=default_trade_quantity,
                enrich_scryfall=enrich_scryfall,
                merge=merge_duplicates,
                allow_scryfall_finish_mismatch=allow_scryfall_finish_mismatch,
                field_mapping=field_mapping,
            )
        else:
            preview = preview_collection_import_csv(
                user["id"],
                upload["content"],
                source=source,
                default_game=default_game,
                default_trade_quantity=default_trade_quantity,
                enrich_scryfall=enrich_scryfall,
                merge=merge_duplicates,
                allow_scryfall_finish_mismatch=allow_scryfall_finish_mismatch,
                field_mapping=field_mapping,
            )
            return self.html(render_import(user, preview=preview, notice="Preview ready. Review the counts before importing."))
    except ValueError as exc:
        return self.html(render_import(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    if result.get("queued"):
        start_scryfall_enrichment_worker()
    notice = f"Imported {result['inserted'] + result['updated']} collection rows."
    if result.get("queued"):
        notice += f" {result['queued']} rows queued for background Scryfall enrichment."
    return self.html(render_import(user, result=result, notice=notice))

def csv_import_mapping_preset_create(self, user):
    form = self.read_form()
    try:
        preset = save_csv_import_mapping_preset(
            user["id"],
            form.get("name", [""])[0],
            csv_import_mapping_from_form(form),
            import_target=form.get("import_target", ["collection"])[0],
            is_shared=form.get("is_shared", [""])[0] == "1",
            is_admin=bool(user["is_admin"]),
        )
    except ValueError as exc:
        return self.html(render_import(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    target_label = "deck" if row_value(preset, "import_target", "collection") == "deck" else "collection"
    return self.html(render_import(user, notice=f"Saved {target_label} mapping preset."))

def csv_import_mapping_preset_delete(self, user, path):
    parts = path.strip("/").split("/")
    try:
        preset_id = int(parts[2])
    except (IndexError, ValueError):
        return self.not_found(user)
    try:
        delete_csv_import_mapping_preset(user["id"], preset_id, is_admin=bool(user["is_admin"]))
    except ValueError as exc:
        return self.html(render_import(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    return self.html(render_import(user, notice="Deleted mapping preset."))

def import_undo(self, user, path):
    try:
        batch_id = int(path.strip("/").split("/")[1])
    except (ValueError, IndexError):
        return self.not_found(user)
    form = self.read_form()
    redirect_to = form.get("redirect_to", ["/import"])[0]
    if not (redirect_to.startswith("/import") or redirect_to.startswith("/groups/")):
        redirect_to = "/import"
    try:
        result = undo_import_batch(user["id"], batch_id)
    except ValueError as exc:
        if redirect_to.startswith("/groups/"):
            parts = redirect_to.strip("/").split("/")
            try:
                group_id = int(parts[1])
            except (ValueError, IndexError):
                return self.html(render_import(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
            return self.html(render_group_detail(user, group_id, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
        return self.html(render_import(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    notice = f"Undid import batch #{result['batch_id']} ({result['undone_items']} change{'s' if result['undone_items'] != 1 else ''} reverted)."
    if redirect_to.startswith("/groups/"):
        parts = redirect_to.strip("/").split("/")
        try:
            group_id = int(parts[1])
        except (ValueError, IndexError):
            return self.redirect("/import")
        page = render_group_detail(user, group_id, notice=notice)
        if not page:
            return self.redirect("/import")
        return self.html(page)
    return self.html(render_import(user, notice=notice))

def import_scryfall_sync(self, user):
    if not require_admin(user):
        return self.not_found(user)
    started = start_scryfall_bulk_sync()
    notice = "Local Scryfall data sync started. Imports will use it as soon as it finishes." if started else "Local Scryfall data sync is already running."
    return self.html(render_import(user, notice=notice))

def prices_refresh(self, user):
    if not require_admin(user):
        return self.not_found(user)
    result = refresh_all_scryfall_prices(sync_bulk=False, notify_users=True)
    notice = (
        f"Refreshed Scryfall prices for {result['priced']} of {result['checked']} MTG entries. "
        f"{result['changed']} value change{'s' if result['changed'] != 1 else ''} found."
    )
    if result["missing"]:
        notice += f" {result['missing']} entries did not have a local Scryfall price yet."
    return self.html(render_import(user, notice=notice))

def collection_bulk_delete(self, user):
    form = self.read_form()
    redirect_to = safe_local_redirect_path(form.get("redirect_to", ["/collection"])[0], default="/collection", allowed_prefix="/collection")
    bulk_delete_collection_items(user["id"], form.get("item_id", []))
    self.redirect(redirect_to)

def collection_bulk_update(self, user):
    form = self.read_form()
    redirect_to = safe_local_redirect_path(form.get("redirect_to", ["/collection"])[0], default="/collection", allowed_prefix="/collection")
    try:
        quantity, quantity_for_trade, is_public = parse_bulk_collection_update(form)
    except ValueError as exc:
        return self.html(render_collection(user, {}, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    update_collection_items_by_ids(user["id"], form.get("item_id", []), quantity, quantity_for_trade, is_public)
    self.redirect(redirect_to)

def collection_update_all(self, user):
    form = self.read_form()
    redirect_to = safe_local_redirect_path(form.get("redirect_to", ["/collection"])[0], default="/collection", allowed_prefix="/collection")
    filters = collection_filter_values(form)
    try:
        quantity, quantity_for_trade, is_public = parse_bulk_collection_update(form)
    except ValueError as exc:
        return self.html(render_collection(user, {}, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    update_collection_items_matching(user["id"], filters, quantity=quantity, quantity_for_trade=quantity_for_trade, is_public=is_public)
    self.redirect(redirect_to)

def collection_delete_all(self, user):
    form = self.read_form()
    redirect_to = safe_local_redirect_path(form.get("redirect_to", ["/collection"])[0], default="/collection", allowed_prefix="/collection")
    filters = collection_filter_values(form)
    delete_collection_items_matching(user["id"], filters)
    self.redirect(redirect_to)

def collection_new(self, method, user):
    if method == "GET":
        return self.html(render_collection_form(user))
    form = self.read_form()
    try:
        data = validate_collection_form(form)
    except ValueError as exc:
        return self.html(render_collection_form(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    intent = form.get("intent", ["save"])[0]
    if intent in ("lookup", "choose_scryfall_card", "use_scryfall") or should_request_scryfall_selection(data):
        self.enforce_rate_limit(
            "scryfall_lookup",
            f"user:{user['id']}",
            "Too many Scryfall lookup requests. Try again shortly.",
        )
    if intent == "lookup":
        try:
            candidates = manual_scryfall_candidates(data)
            data["lookup_on_save"] = "1"
        except (ValueError, ScryfallError) as exc:
            return self.html(render_collection_form(user, data, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
        return self.html(render_collection_form(
            user,
            data,
            notice=f"Found {len(candidates)} card matches. Choose the card, then pick a printing.",
            scryfall_results=candidates,
            scryfall_picker_intent="choose_scryfall_card",
            scryfall_picker_label="Show variations",
            scryfall_picker_title="Scryfall card matches",
        ))
    if intent == "choose_scryfall_card":
        try:
            selected_card, variants = manual_scryfall_variants(data["selected_scryfall_id"])
            data = apply_selected_card_name(data, selected_card)
        except (ValueError, ScryfallError) as exc:
            return self.html(render_collection_form(user, data, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
        return self.html(render_collection_form(
            user,
            data,
            notice=f"Found {len(variants)} printings and variants. Choose the exact one.",
            scryfall_results=variants,
            scryfall_picker_intent="use_scryfall",
            scryfall_picker_label="Use selected printing",
            scryfall_picker_title="Printings and variants",
        ))
    if intent == "use_scryfall":
        try:
            data = apply_scryfall_data(data, selected_scryfall_card_data(data["selected_scryfall_id"]))
            data["lookup_on_save"] = "1"
        except (ValueError, ScryfallError) as exc:
            return self.html(render_collection_form(user, data, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
        return self.html(render_collection_form(user, data, notice="Scryfall card selected. Review the details, then save."))
    if should_request_scryfall_selection(data):
        try:
            if data["set_code"] and data["collector_number"]:
                data = enrich_collection_data_from_scryfall(data)
            else:
                candidates = manual_scryfall_candidates(data)
                if len(candidates) == 1:
                    selected_card, variants = manual_scryfall_variants(candidates[0]["scryfall_id"])
                    data = apply_selected_card_name(data, selected_card)
                    data["selected_scryfall_id"] = candidates[0]["scryfall_id"]
                    if len(variants) == 1:
                        data = apply_scryfall_data(data, variants[0])
                    else:
                        return self.html(render_collection_form(
                            user,
                            data,
                            notice="Choose the exact printing before saving.",
                            scryfall_results=variants,
                            scryfall_picker_intent="use_scryfall",
                            scryfall_picker_label="Use selected printing",
                            scryfall_picker_title="Printings and variants",
                        ))
                else:
                    return self.html(render_collection_form(
                        user,
                        data,
                        notice="Choose the card before saving.",
                        scryfall_results=candidates,
                        scryfall_picker_intent="choose_scryfall_card",
                        scryfall_picker_label="Show variations",
                        scryfall_picker_title="Scryfall card matches",
                    ))
        except (ValueError, ScryfallError) as exc:
            return self.html(render_collection_form(user, data, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    try:
        ensure_scryfall_finish_allowed(data, allow_override=data.get("scryfall_finish_override") == "1")
    except ValueError as exc:
        return self.html(render_collection_form(user, data, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    upsert_collection_item(user["id"], data, merge=False)
    self.redirect("/collection")

def collection_item(self, method, user, path):
    parts = path.strip("/").split("/")
    if len(parts) < 3:
        return self.not_found(user)
    try:
        item_id = int(parts[1])
    except ValueError:
        return self.not_found(user)
    item = row("SELECT * FROM collection_items WHERE id = ? AND user_id = ?", (item_id, user["id"]))
    if not item:
        return self.not_found(user)
    action = parts[2]
    if action == "photos" and len(parts) == 3 and method == "POST":
        form, files = self.read_multipart_form()
        try:
            add_collection_item_photo(
                user["id"],
                item_id,
                files.get("card_photo"),
                form.get("caption", [""])[0],
            )
        except ValueError as exc:
            return self.html(render_collection_form(user, item, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
        return self.html(render_collection_form(user, item, notice="Card photo added."))
    if action == "photos" and len(parts) == 5 and parts[4] == "delete" and method == "POST":
        try:
            photo_id = int(parts[3])
        except ValueError:
            return self.not_found(user)
        if not delete_collection_item_photo(user["id"], item_id, photo_id):
            return self.not_found(user)
        return self.redirect(f"/collection/{item_id}/edit")
    if len(parts) != 3:
        return self.not_found(user)
    if action == "edit":
        if method == "GET":
            return self.html(render_collection_form(user, item))
        form = self.read_form()
        try:
            data = validate_collection_form(form)
        except ValueError as exc:
            return self.html(render_collection_form(user, item, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
        data["id"] = item_id
        intent = form.get("intent", ["save"])[0]
        if intent in ("lookup", "choose_scryfall_card", "use_scryfall") or should_request_scryfall_selection(data):
            self.enforce_rate_limit(
                "scryfall_lookup",
                f"user:{user['id']}",
                "Too many Scryfall lookup requests. Try again shortly.",
            )
        if intent == "lookup":
            try:
                candidates = manual_scryfall_candidates(data)
                data["lookup_on_save"] = "1"
            except (ValueError, ScryfallError) as exc:
                return self.html(render_collection_form(user, data, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
            return self.html(render_collection_form(
                user,
                data,
                notice=f"Found {len(candidates)} card matches. Choose the card, then pick a printing.",
                scryfall_results=candidates,
                scryfall_picker_intent="choose_scryfall_card",
                scryfall_picker_label="Show variations",
                scryfall_picker_title="Scryfall card matches",
            ))
        if intent == "choose_scryfall_card":
            try:
                selected_card, variants = manual_scryfall_variants(data["selected_scryfall_id"])
                data = apply_selected_card_name(data, selected_card)
                data["id"] = item_id
            except (ValueError, ScryfallError) as exc:
                return self.html(render_collection_form(user, data, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
            return self.html(render_collection_form(
                user,
                data,
                notice=f"Found {len(variants)} printings and variants. Choose the exact one.",
                scryfall_results=variants,
                scryfall_picker_intent="use_scryfall",
                scryfall_picker_label="Use selected printing",
                scryfall_picker_title="Printings and variants",
            ))
        if intent == "use_scryfall":
            try:
                data = apply_scryfall_data(data, selected_scryfall_card_data(data["selected_scryfall_id"]))
                data["id"] = item_id
                data["lookup_on_save"] = "1"
            except (ValueError, ScryfallError) as exc:
                return self.html(render_collection_form(user, data, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
            return self.html(render_collection_form(user, data, notice="Scryfall card selected. Review the details, then save."))
        if should_request_scryfall_selection(data):
            try:
                if data["set_code"] and data["collector_number"]:
                    data = enrich_collection_data_from_scryfall(data)
                    data["id"] = item_id
                else:
                    candidates = manual_scryfall_candidates(data)
                    if len(candidates) == 1:
                        selected_card, variants = manual_scryfall_variants(candidates[0]["scryfall_id"])
                        data = apply_selected_card_name(data, selected_card)
                        data["id"] = item_id
                        if len(variants) == 1:
                            data = apply_scryfall_data(data, variants[0])
                            data["id"] = item_id
                        else:
                            return self.html(render_collection_form(
                                user,
                                data,
                                notice="Choose the exact printing before saving.",
                                scryfall_results=variants,
                                scryfall_picker_intent="use_scryfall",
                                scryfall_picker_label="Use selected printing",
                                scryfall_picker_title="Printings and variants",
                            ))
                    else:
                        return self.html(render_collection_form(
                            user,
                            data,
                            notice="Choose the card before saving.",
                            scryfall_results=candidates,
                            scryfall_picker_intent="choose_scryfall_card",
                            scryfall_picker_label="Show variations",
                            scryfall_picker_title="Scryfall card matches",
                        ))
            except (ValueError, ScryfallError) as exc:
                return self.html(render_collection_form(user, data, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
        try:
            ensure_scryfall_finish_allowed(data, allow_override=data.get("scryfall_finish_override") == "1")
        except ValueError as exc:
            return self.html(render_collection_form(user, data, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
        update_collection_item(user["id"], item_id, data)
        return self.redirect("/collection")
    if action == "delete" and method == "POST":
        execute("DELETE FROM collection_items WHERE id = ? AND user_id = ?", (item_id, user["id"]))
        return self.redirect("/collection")
    return self.not_found(user)

def collection_photo(self, user, path):
    try:
        photo_id = int(path.strip("/").split("/")[2])
    except (ValueError, IndexError):
        return self.not_found(user)
    photo = collection_item_photo_for_user(photo_id, user["id"])
    if not photo:
        return self.not_found(user)
    return self.inline_binary(photo["content"], photo["content_type"], photo["original_filename"])

def want_new(self, user):
    form = self.read_form()
    try:
        data = validate_want_form(form)
    except ValueError as exc:
        return self.html(render_wants(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    intent = form.get("intent", ["save"])[0]
    if intent in ("lookup", "choose_scryfall_card", "add_scryfall_wants", "use_scryfall") or should_request_scryfall_selection(data):
        self.enforce_rate_limit(
            "scryfall_lookup",
            f"user:{user['id']}",
            "Too many Scryfall lookup requests. Try again shortly.",
        )
    if intent == "lookup":
        try:
            candidates = manual_scryfall_candidates(data)
            data["lookup_on_save"] = "1"
        except (ValueError, ScryfallError) as exc:
            return self.html(render_wants(user, data, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
        return self.html(render_wants(
            user,
            data,
            scryfall_results=candidates,
            notice=f"Found {len(candidates)} card matches. Choose the card, then pick a printing.",
            scryfall_picker_intent="choose_scryfall_card",
            scryfall_picker_label="Show variations",
            scryfall_picker_title="Scryfall card matches",
        ))
    if intent == "choose_scryfall_card":
        try:
            selected_card, variants = manual_scryfall_variants(data["selected_scryfall_id"])
            data = apply_selected_card_name(data, selected_card)
        except (ValueError, ScryfallError) as exc:
            return self.html(render_wants(user, data, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
        return self.html(render_wants(
            user,
            data,
            scryfall_results=variants,
            notice=f"Found {len(variants)} printings and variants. Choose one or more to add.",
            scryfall_picker_intent="add_scryfall_wants",
            scryfall_picker_label="Add selected wants",
            scryfall_picker_title="Printings and variants",
            scryfall_picker_multiple=True,
        ))
    if intent == "add_scryfall_wants":
        try:
            inserted = insert_selected_want_items(user["id"], data, form.get("selected_scryfall_ids", []))
        except (ValueError, ScryfallError) as exc:
            variants = []
            if data.get("selected_scryfall_id"):
                try:
                    selected_card, variants = manual_scryfall_variants(data["selected_scryfall_id"])
                    data = apply_selected_card_name(data, selected_card)
                except (ValueError, ScryfallError):
                    variants = []
            return self.html(render_wants(
                user,
                data,
                scryfall_results=variants,
                notice=str(exc),
                status="error",
                scryfall_picker_intent="add_scryfall_wants",
                scryfall_picker_label="Add selected wants",
                scryfall_picker_title="Printings and variants",
                scryfall_picker_multiple=True,
            ), HTTPStatus.BAD_REQUEST)
        return self.html(render_wants(
            user,
            notice=f"Added {inserted} wanted printing{'s' if inserted != 1 else ''}.",
        ))
    if intent == "use_scryfall":
        try:
            data = apply_scryfall_data(data, selected_scryfall_card_data(data["selected_scryfall_id"]))
            data["lookup_on_save"] = "1"
        except (ValueError, ScryfallError) as exc:
            return self.html(render_wants(user, data, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
        return self.html(render_wants(user, data, notice="Scryfall card selected. Review the details, then add the want."))
    if should_request_scryfall_selection(data):
        try:
            if data["set_code"] and data["collector_number"]:
                data = enrich_collection_data_from_scryfall(data)
            else:
                candidates = manual_scryfall_candidates(data)
                if len(candidates) == 1:
                    selected_card, variants = manual_scryfall_variants(candidates[0]["scryfall_id"])
                    data = apply_selected_card_name(data, selected_card)
                    if len(variants) == 1:
                        data = apply_scryfall_data(data, variants[0])
                    else:
                        return self.html(render_wants(
                            user,
                            data,
                            scryfall_results=variants,
                            notice="Choose one or more printings before adding this want.",
                            scryfall_picker_intent="add_scryfall_wants",
                            scryfall_picker_label="Add selected wants",
                            scryfall_picker_title="Printings and variants",
                            scryfall_picker_multiple=True,
                        ))
                else:
                    return self.html(render_wants(
                        user,
                        data,
                        scryfall_results=candidates,
                        notice="Choose the card before adding this want.",
                        scryfall_picker_intent="choose_scryfall_card",
                        scryfall_picker_label="Show variations",
                        scryfall_picker_title="Scryfall card matches",
                    ))
        except (ValueError, ScryfallError) as exc:
            return self.html(render_wants(user, data, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    try:
        ensure_scryfall_finish_allowed(
            data,
            allow_override=data.get("scryfall_finish_override") == "1",
            finish_values=data.get("finish", ""),
        )
    except ValueError as exc:
        return self.html(render_wants(user, data, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    insert_want_item(user["id"], data)
    self.redirect("/wants")

def want_edit(self, method, user, path):
    try:
        want_id = int(path.strip("/").split("/")[1])
    except (ValueError, IndexError):
        return self.not_found(user)
    found = row("SELECT * FROM want_items WHERE id = ? AND user_id = ?", (want_id, user["id"]))
    if not found:
        return self.not_found(user)
    if method == "GET":
        return self.html(render_wants(user, found, edit_want_id=want_id))
    if method != "POST":
        return self.not_found(user)
    form = self.read_form()
    try:
        data = validate_want_form(form)
    except ValueError as exc:
        return self.html(render_wants(user, found, notice=str(exc), status="error", edit_want_id=want_id), HTTPStatus.BAD_REQUEST)
    intent = form.get("intent", ["save"])[0]
    if intent in ("lookup", "choose_scryfall_card", "use_scryfall") or should_request_scryfall_selection(data):
        self.enforce_rate_limit(
            "scryfall_lookup",
            f"user:{user['id']}",
            "Too many Scryfall lookup requests. Try again shortly.",
        )
    if intent == "lookup":
        try:
            candidates = manual_scryfall_candidates(data)
            data["lookup_on_save"] = "1"
        except (ValueError, ScryfallError) as exc:
            return self.html(render_wants(user, data, notice=str(exc), status="error", edit_want_id=want_id), HTTPStatus.BAD_REQUEST)
        return self.html(render_wants(
            user,
            data,
            scryfall_results=candidates,
            notice=f"Found {len(candidates)} card matches. Choose the card, then pick a printing.",
            scryfall_picker_intent="choose_scryfall_card",
            scryfall_picker_label="Show variations",
            scryfall_picker_title="Scryfall card matches",
            edit_want_id=want_id,
        ))
    if intent == "choose_scryfall_card":
        try:
            selected_card, variants = manual_scryfall_variants(data["selected_scryfall_id"])
            data = apply_selected_card_name(data, selected_card)
        except (ValueError, ScryfallError) as exc:
            return self.html(render_wants(user, data, notice=str(exc), status="error", edit_want_id=want_id), HTTPStatus.BAD_REQUEST)
        return self.html(render_wants(
            user,
            data,
            scryfall_results=variants,
            notice=f"Found {len(variants)} printings and variants. Choose the exact one.",
            scryfall_picker_intent="use_scryfall",
            scryfall_picker_label="Use selected want",
            scryfall_picker_title="Printings and variants",
            edit_want_id=want_id,
        ))
    if intent == "use_scryfall":
        try:
            data = apply_scryfall_data(data, selected_scryfall_card_data(data["selected_scryfall_id"]))
            data["lookup_on_save"] = "1"
        except (ValueError, ScryfallError) as exc:
            return self.html(render_wants(user, data, notice=str(exc), status="error", edit_want_id=want_id), HTTPStatus.BAD_REQUEST)
        return self.html(render_wants(user, data, notice="Scryfall card selected. Review the details, then save the want.", edit_want_id=want_id))
    if should_request_scryfall_selection(data):
        try:
            if data["set_code"] and data["collector_number"]:
                data = enrich_collection_data_from_scryfall(data)
            else:
                candidates = manual_scryfall_candidates(data)
                if len(candidates) == 1:
                    selected_card, variants = manual_scryfall_variants(candidates[0]["scryfall_id"])
                    data = apply_selected_card_name(data, selected_card)
                    if len(variants) == 1:
                        data = apply_scryfall_data(data, variants[0])
                    else:
                        return self.html(render_wants(
                            user,
                            data,
                            scryfall_results=variants,
                            notice="Choose the exact printing before saving this want.",
                            scryfall_picker_intent="use_scryfall",
                            scryfall_picker_label="Use selected want",
                            scryfall_picker_title="Printings and variants",
                            edit_want_id=want_id,
                        ))
                else:
                    return self.html(render_wants(
                        user,
                        data,
                        scryfall_results=candidates,
                        notice="Choose the card before saving this want.",
                        scryfall_picker_intent="choose_scryfall_card",
                        scryfall_picker_label="Show variations",
                        scryfall_picker_title="Scryfall card matches",
                        edit_want_id=want_id,
                    ))
        except (ValueError, ScryfallError) as exc:
            return self.html(render_wants(user, data, notice=str(exc), status="error", edit_want_id=want_id), HTTPStatus.BAD_REQUEST)
    try:
        ensure_scryfall_finish_allowed(
            data,
            allow_override=data.get("scryfall_finish_override") == "1",
            finish_values=data.get("finish", ""),
        )
    except ValueError as exc:
        return self.html(render_wants(user, data, notice=str(exc), status="error", edit_want_id=want_id), HTTPStatus.BAD_REQUEST)
    if not update_want_item(user["id"], want_id, data):
        return self.not_found(user)
    self.redirect("/wants")

def want_delete(self, user, path):
    try:
        want_id = int(path.strip("/").split("/")[1])
    except (ValueError, IndexError):
        return self.not_found(user)
    execute("DELETE FROM want_items WHERE id = ? AND user_id = ?", (want_id, user["id"]))
    self.redirect("/wants")

COLLECTION_ROUTE_METHODS = ('collection_export', 'wants_export', 'cleanup_page', 'cleanup_collection', 'cleanup_wants', 'condition_finish_audit_page', 'condition_finish_audit_query_from_form', 'condition_finish_audit_update', 'condition_finish_audit_update_all', 'condition_finish_audit_normalize', 'condition_finish_audit_normalize_all', 'collection_import', 'csv_import_mapping_preset_create', 'csv_import_mapping_preset_delete', 'import_undo', 'import_scryfall_sync', 'prices_refresh', 'collection_bulk_delete', 'collection_bulk_update', 'collection_update_all', 'collection_delete_all', 'collection_new', 'collection_item', 'collection_photo', 'want_new', 'want_edit', 'want_delete')

__all__ = [
    "COLLECTION_ROUTE_METHODS",
    *COLLECTION_ROUTE_METHODS,
]
