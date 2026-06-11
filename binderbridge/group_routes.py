"""Group, deck, wishlist, and member HTTP route handlers.

These functions are attached to app.App by app.py after the class is defined.
Shared helpers are injected by the app facade at import time.
"""

def group_export(self, user, group):
    filename, data = export_group_csv(user["id"], group["id"])
    return self.binary(data, "text/csv; charset=utf-8", filename)

def groups_page(self, method, user, query=None):
    view = normalize_group_view(query_value(query or {}, "type"))
    if method == "GET":
        return self.html(render_groups(user, view=view))
    form = self.read_form()
    view = normalize_group_view(form.get("group_view", [view])[0])
    try:
        group_id = create_card_group(
            user["id"],
            form.get("group_type", ["deck"])[0],
            form.get("name", [""])[0],
            form.get("description", [""])[0],
            visibility=form_visibility(form),
        )
    except ValueError as exc:
        return self.html(render_groups(user, notice=str(exc), status="error", view=view), HTTPStatus.BAD_REQUEST)
    return self.redirect(f"/groups/{group_id}")

def group_action(self, method, user, path, query=None):
    parts = path.strip("/").split("/")
    try:
        group_id = int(parts[1])
    except (ValueError, IndexError):
        return self.not_found(user)
    group = user_group(user["id"], group_id)
    if not group:
        return self.not_found(user)
    if len(parts) == 2 and method == "GET":
        page = render_group_detail(user, group_id, query=query)
        if not page:
            return self.not_found(user)
        return self.html(page)
    if len(parts) == 3 and parts[2] == "delete" and method == "POST":
        redirect_to = group_listing_url(group)
        delete_card_group(user["id"], group_id)
        return self.redirect(redirect_to)
    if len(parts) == 3 and parts[2] == "visibility" and method == "POST":
        form = self.read_form()
        update_card_group_visibility(user["id"], group_id, form_visibility(form))
        return self.redirect(f"/groups/{group_id}")
    if len(parts) == 3 and parts[2] == "sharing" and method == "POST":
        form = self.read_form()
        update_card_group_sharing_defaults(
            user["id"],
            group_id,
            form_visibility(form),
            form.get("default_item_visibility", [VISIBILITY_MEMBERS])[0],
            form.get("show_values", [""])[0] == "1",
            form.get("show_photos", [""])[0] == "1",
        )
        return self.html(render_group_detail(user, group_id, notice="Sharing defaults updated. Existing card visibility was not changed."))
    if len(parts) == 3 and parts[2] == "share-links" and method == "POST":
        form = self.read_form()
        try:
            token, link = create_group_share_link(
                user["id"],
                group_id,
                form.get("label", [""])[0],
                form.get("expires_days", ["0"])[0],
                form.get("show_values", [""])[0] == "1",
                form.get("show_photos", [""])[0] == "1",
            )
        except ValueError as exc:
            return self.html(render_group_detail(user, group_id, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
        share_result = {
            "url": f"{self.public_base_url()}/share/{token}",
            "link": link,
        }
        return self.html(render_group_detail(user, group_id, notice="Private share link created. Copy it now; the token is not stored.", share_result=share_result))
    if len(parts) == 5 and parts[2] == "share-links" and parts[4] == "revoke" and method == "POST":
        try:
            share_id = int(parts[3])
        except ValueError:
            return self.not_found(user)
        revoke_group_share_link(user["id"], group_id, share_id)
        return self.html(render_group_detail(user, group_id, notice="Share link revoked."))
    if len(parts) == 3 and parts[2] == "export" and method == "GET":
        return self.group_export(user, group)
    if len(parts) == 3 and parts[2] == "import" and method == "POST":
        return self.group_deck_import(user, group)
    if len(parts) == 3 and parts[2] == "missing-wants" and method == "POST":
        return self.group_deck_missing_wants(user, group)
    if len(parts) == 3 and parts[2] == "add" and method == "POST":
        form = self.read_form()
        try:
            if group["group_type"] == "wishlist":
                add_want_item_to_group(user["id"], group_id, int(form.get("want_item_id", ["0"])[0] or 0))
            else:
                add_collection_item_to_group(
                    user["id"],
                    group_id,
                    int(form.get("collection_item_id", ["0"])[0] or 0),
                    form.get("quantity", ["1"])[0],
                )
        except (ValueError, TypeError) as exc:
            return self.html(render_group_detail(user, group_id, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
        return self.redirect(f"/groups/{group_id}")
    if len(parts) == 5 and parts[2] == "items" and parts[4] == "delete" and method == "POST":
        try:
            group_item_id = int(parts[3])
        except ValueError:
            return self.not_found(user)
        remove_group_item(user["id"], group_id, group_item_id)
        return self.redirect(f"/groups/{group_id}")
    return self.not_found(user)

def group_deck_import(self, user, group):
    fields, files = self.read_multipart_form()
    intent = fields.get("intent", [""])[0]
    if intent == "commit_preview":
        try:
            result = commit_deck_import_preview(user["id"], group["id"], fields.get("batch_id", ["0"])[0])
        except ValueError as exc:
            page = render_group_detail(user, group["id"], notice=str(exc), status="error")
            return self.html(page, HTTPStatus.BAD_REQUEST)
        if result.get("queued"):
            start_scryfall_enrichment_worker()
        notice = (
            f"Imported {result['grouped']} owned deck card{'s' if result['grouped'] != 1 else ''}. "
            f"{result['missing']} card{'s' if result['missing'] != 1 else ''} missing from your collection."
        )
        return self.html(render_group_detail(user, group["id"], notice=notice, import_result=result))
    if intent == "confirm_import":
        try:
            payload = decode_deck_import_payload(fields.get("payload", [""])[0])
            selected_sections = {
                section
                for section in fields.get("include_section", [])
                if section in DECK_IMPORT_SECTION_LABELS
            }
            preview = preview_deck_group_import(
                user["id"],
                group["id"],
                payload["sections"],
                included_sections=selected_sections,
                source=payload["source"],
                enrich_scryfall=payload["enrich_scryfall"],
                merge=payload["merge"],
                warnings=payload["warnings"],
            )
        except ValueError as exc:
            page = render_group_detail(user, group["id"], notice=str(exc), status="error")
            return self.html(page, HTTPStatus.BAD_REQUEST)
        return self.html(render_group_detail(user, group["id"], notice="Deck import preview ready. Review the matches before importing.", import_review=preview))

    source = fields.get("source", ["decklist"])[0]
    if source not in dict(DECK_IMPORT_SOURCE_OPTIONS):
        source = "decklist"
    enrich_scryfall = fields.get("enrich_scryfall", [""])[0] == "1"
    merge_duplicates = fields.get("merge_duplicates", [""])[0] == "1"
    field_mapping = csv_import_mapping_for_user(
        user["id"],
        fields.get("mapping_preset_id", ["0"])[0],
        is_admin=bool(user["is_admin"]),
        import_target="deck",
    )
    deck_url = fields.get("deck_url", [""])[0].strip()
    deck_text = fields.get("deck_text", [""])[0].strip()
    upload = files.get("deck_file")
    try:
        if deck_url:
            _, section_rows, warnings = deck_import_sections_from_url(deck_url)
            result_source = "url"
            source_url = deck_url
        elif deck_text:
            section_rows, warnings = deck_import_sections_from_text(deck_text)
            result_source = "decklist"
            source_url = ""
        elif upload and upload["content"]:
            filename = str(upload.get("filename") or "").lower()
            content_type = str(upload.get("content_type") or "").lower()
            use_decklist_parser = source == "decklist" or filename.endswith((".txt", ".dec")) or "text/plain" in content_type
            if use_decklist_parser and not filename.endswith(".csv"):
                section_rows, warnings = deck_import_sections_from_text(decode_csv(upload["content"]))
                result_source = "decklist"
                source_url = ""
            else:
                result_source = source if source in dict(CSV_SOURCE_OPTIONS) else "auto"
                section_rows, warnings = normalize_csv_rows_by_section(
                    upload["content"],
                    default_game="mtg",
                    default_trade_quantity=0,
                    field_mapping=field_mapping,
                )
                source_url = ""
        else:
            raise ValueError("Paste a deck list, enter a deck-list URL, or choose a CSV/text file.")

        if deck_import_sections_need_review(section_rows):
            review = deck_import_preview_payload(
                result_source,
                section_rows,
                warnings=warnings,
                enrich_scryfall=enrich_scryfall,
                merge=merge_duplicates,
                source_url=source_url,
            )
            notice = "Extra deck sections detected. Choose which ones to include before importing."
            return self.html(render_group_detail(user, group["id"], notice=notice, import_review=review))

        preview = preview_deck_group_import(
            user["id"],
            group["id"],
            section_rows,
            source=result_source,
            enrich_scryfall=enrich_scryfall,
            merge=merge_duplicates,
            warnings=warnings,
        )
    except ValueError as exc:
        page = render_group_detail(user, group["id"], notice=str(exc), status="error")
        return self.html(page, HTTPStatus.BAD_REQUEST)
    return self.html(render_group_detail(user, group["id"], notice="Deck import preview ready. Review the matches before importing.", import_review=preview))

def group_deck_missing_wants(self, user, group):
    if group["group_type"] != "deck":
        return self.not_found(user)
    form = self.read_form()
    try:
        items = decode_deck_missing_wants_payload(form.get("payload", [""])[0])
        result = add_deck_missing_items_to_wishlist(
            user["id"],
            group["id"],
            items,
            form.get("missing_key", []),
            form.get("wishlist_group_id", ["0"])[0],
            form.get("new_group_name", [""])[0],
            form.get("wishlist_is_public", [""])[0] == "1",
        )
    except ValueError as exc:
        return self.html(render_group_detail(user, group["id"], notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    notice = (
        f"Added {result['added']} missing wanted card{'s' if result['added'] != 1 else ''}"
        f" to {result['wishlist_group_name']}."
    )
    if result["updated"]:
        notice += f" Updated {result['updated']} existing want{'s' if result['updated'] != 1 else ''}."
    return self.html(render_group_detail(user, result["wishlist_group_id"], notice=notice))

def member_detail(self, user, path, query=None):
    parts = path.strip("/").split("/")
    try:
        member_id = int(parts[1])
    except (ValueError, IndexError):
        return self.not_found(user)
    if len(parts) == 4 and parts[2] == "groups":
        try:
            group_id = int(parts[3])
        except ValueError:
            return self.not_found(user)
        page = render_public_group_detail(user, member_id, group_id, query=query)
    elif len(parts) == 2:
        page = render_member_detail(user, member_id, query)
    else:
        return self.not_found(user)
    if not page:
        return self.not_found(user)
    self.html(page)

def shared_group_page(self, path):
    parts = path.strip("/").split("/")
    if len(parts) < 2:
        return self.not_found(None)
    token = parts[1]
    link = share_link_from_token(token)
    if not link:
        return self.not_found(None)
    if len(parts) == 2:
        return self.html(render_shared_group(link, token))
    if len(parts) == 4 and parts[2] == "photos":
        try:
            photo_id = int(parts[3])
        except ValueError:
            return self.not_found(None)
        if not share_link_allows_photos(link):
            return self.not_found(None)
        photo = row(
            """
            SELECT collection_item_photos.*
            FROM collection_item_photos
            JOIN collection_items ON collection_items.id = collection_item_photos.collection_item_id
            JOIN group_collection_items ON group_collection_items.collection_item_id = collection_items.id
            WHERE collection_item_photos.id = ? AND group_collection_items.group_id = ?
            """,
            (photo_id, link["target_id"]),
        )
        if not photo:
            return self.not_found(None)
        return self.inline_binary(photo["content"], photo["content_type"], photo["original_filename"])
    return self.not_found(None)


GROUP_ROUTE_METHODS = ('group_export', 'groups_page', 'group_action', 'group_deck_import', 'group_deck_missing_wants', 'member_detail', 'shared_group_page')

__all__ = [
    "GROUP_ROUTE_METHODS",
    *GROUP_ROUTE_METHODS,
]
