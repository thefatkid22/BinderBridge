"""Extracted BinderBridge feature code.

The app facade injects shared helpers/constants into this module at import time
so the legacy app.py public API remains compatible during the split.
"""

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from binderbridge import trade_queries as _trade_queries

__binderbridge_feature_modules__ = (_trade_queries,)

from binderbridge.trade_queries import *


def validate_collection_form(form):
    card_name = sanitize_text_input(form.get("card_name", [""])[0], max_length=160).strip()
    game = form.get("game", ["mtg"])[0].strip() or "mtg"
    set_name = sanitize_text_input(form.get("set_name", [""])[0], max_length=120).strip()
    set_code = sanitize_text_input(form.get("set_code", [""])[0], max_length=20).strip().upper()
    collector_number = sanitize_text_input(form.get("collector_number", [""])[0], max_length=40).strip()
    finish = form.get("finish", ["Regular"])[0].strip() or "Regular"
    condition = form.get("condition", ["NM"])[0].strip() or "NM"
    condition_notes = sanitize_text_input(form.get("condition_notes", [""])[0], max_length=1000).strip()
    language = form.get("language", ["English"])[0].strip() or "English"
    notes = sanitize_text_input(form.get("notes", [""])[0], max_length=1000).strip()
    if not str(form.get("quantity", ["1"])[0] or "").strip().lstrip("+-").isdigit():
        raise ValueError("Quantities must be numbers.")
    if not str(form.get("quantity_for_trade", ["0"])[0] or "").strip().lstrip("+-").isdigit():
        raise ValueError("Quantities must be numbers.")
    quantity = clamp_quantity(form.get("quantity", ["1"])[0], 1)
    quantity_for_trade = clamp_quantity(form.get("quantity_for_trade", ["0"])[0], 0)
    if not card_name:
        raise ValueError("Card name is required.")
    if quantity_for_trade > quantity:
        raise ValueError("Quantity for trade cannot be higher than quantity owned.")
    if game not in dict(CARD_GAMES):
        game = "other"
    if condition not in CONDITION_OPTIONS:
        condition = "NM"
    data = {
        "game": game,
        "card_name": card_name,
        "set_name": set_name,
        "set_code": set_code,
        "collector_number": collector_number,
        "finish": finish,
        "condition": condition,
        "condition_notes": condition_notes,
        "language": language,
        "quantity": quantity,
        "quantity_for_trade": quantity_for_trade,
        "notes": notes,
        "visibility": form_visibility(form),
    }
    for field in SCRYFALL_COLLECTION_FIELDS:
        data[field] = sanitize_text_input(form.get(field, [""])[0], max_length=5000).strip()
    data["price_usd"] = normalize_price_usd(data.get("price_usd", ""))
    data["price_source"] = "scryfall" if data["price_usd"] else ""
    for field in PRICE_PROVIDER_ID_FIELDS.values():
        data[field] = sanitize_text_input(form.get(field, [""])[0], max_length=80).strip()
    data["lookup_on_save"] = "1" if form.get("lookup_on_save", [""])[0] == "1" else ""
    data["scryfall_finish_override"] = "1" if form.get("scryfall_finish_override", [""])[0] == "1" else ""
    data["selected_scryfall_id"] = sanitize_text_input(form.get("selected_scryfall_id", [""])[0], max_length=80).strip()
    return data


def parse_trade_quantities(form, prefix, owner_id, price_basis="", viewer_id=None):
    chosen = []
    for key, values in form.items():
        if not key.startswith(f"{prefix}_"):
            continue
        try:
            item_id = int(key.split("_", 1)[1])
            quantity = clamp_quantity(values[0], 0)
        except (TypeError, ValueError):
            continue
        if quantity <= 0:
            continue
        where = "id = ? AND user_id = ? AND quantity_for_trade >= ?"
        params = [item_id, owner_id, quantity]
        item = row(f"SELECT * FROM collection_items WHERE {where}", params)
        viewer = row("SELECT * FROM users WHERE id = ?", (viewer_id,)) if viewer_id is not None else None
        if item and (viewer_id is None or int(viewer_id) == int(owner_id) or can_view_record(viewer, owner_id, item)):
            chosen.append((apply_trade_price_basis(item, price_basis), quantity))
    return chosen


def parse_counter_trade_id(data):
    try:
        counter_trade_id = int(data.get("counter_trade_id", ["0"])[0] or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, counter_trade_id)








def render_counter_trade(user, trade_id):
    source_trade = counter_render_source_trade(user["id"], trade_id)
    if not source_trade:
        return None
    return render_new_trade(
        user,
        source_trade["proposer_id"],
        counter_trade_query(source_trade, user["id"]),
        notice=f"Countering Trade #{trade_id}. Adjust the cards, then review your counter offer.",
    )






def trade_actor_name(trade, actor_id):
    if actor_id == row_value(trade, "proposer_id"):
        return row_value(trade, "proposer_name", "The proposer")
    if actor_id == row_value(trade, "recipient_id"):
        return row_value(trade, "recipient_name", "The recipient")
    return "A user"


def trade_other_user_id(trade, actor_id):
    if actor_id == row_value(trade, "proposer_id"):
        return row_value(trade, "recipient_id")
    if actor_id == row_value(trade, "recipient_id"):
        return row_value(trade, "proposer_id")
    return None


def notify_trade_status_change(conn, trade_id, actor_id, status_value, note=""):
    trade = trade_with_names_conn(conn, trade_id)
    if not trade:
        return
    target_id = trade_other_user_id(trade, actor_id)
    if not target_id:
        return
    actor = trade_actor_name(trade, actor_id)
    label = TRADE_STATUS_LABELS.get(status_value, status_value.title())
    body = f"{actor} marked Trade #{trade_id} as {label.lower()}."
    note = sanitize_text_input(note, max_length=180).strip()
    if note:
        body += f" Note: {note[:180]}"
    create_notification(
        target_id,
        "trade_status",
        f"Trade #{trade_id} {label.lower()}",
        body,
        f"/trades/{trade_id}",
        trade_id,
        conn=conn,
    )


def add_trade_comment(trade_id, user_id, body):
    body = sanitize_text_input(body, max_length=2000).strip()
    if not body:
        raise ValueError("Comment cannot be empty.")
    trade = row(
        "SELECT * FROM trades WHERE id = ? AND (proposer_id = ? OR recipient_id = ?)",
        (trade_id, user_id, user_id),
    )
    if not trade:
        raise ValueError("Trade not found.")
    created_at = now_iso()
    with db() as conn:
        conn.execute(
            "INSERT INTO trade_comments (trade_id, user_id, body, created_at) VALUES (?, ?, ?, ?)",
            (trade_id, user_id, body, created_at),
        )
        conn.execute("UPDATE trades SET updated_at = ? WHERE id = ?", (created_at, trade_id))
        trade_with_names = trade_with_names_conn(conn, trade_id)
        target_id = trade_other_user_id(trade_with_names, user_id)
        if target_id:
            actor = trade_actor_name(trade_with_names, user_id)
            snippet = body.replace("\r", " ").replace("\n", " ")[:180]
            create_notification(
                target_id,
                "trade_comment",
                f"New comment on Trade #{trade_id}",
                f"{actor}: {snippet}",
                f"/trades/{trade_id}",
                trade_id,
                conn=conn,
            )
    send_pending_trade_notification_emails()










def submit_trade_feedback(trade_id, reviewer_id, rating, body=""):
    try:
        rating = int(rating)
    except (TypeError, ValueError) as exc:
        raise ValueError("Choose a rating from 1 to 5.") from exc
    if rating < 1 or rating > 5:
        raise ValueError("Choose a rating from 1 to 5.")
    body = sanitize_text_input(body, max_length=1200).strip()
    timestamp = now_iso()
    with db() as conn:
        trade = conn.execute(
            """
            SELECT trades.*, proposer.display_name AS proposer_name, recipient.display_name AS recipient_name
            FROM trades
            JOIN users proposer ON proposer.id = trades.proposer_id
            JOIN users recipient ON recipient.id = trades.recipient_id
            WHERE trades.id = ?
                AND trades.status = 'completed'
                AND (trades.proposer_id = ? OR trades.recipient_id = ?)
            """,
            (trade_id, reviewer_id, reviewer_id),
        ).fetchone()
        if not trade:
            raise ValueError("Feedback can only be left on completed trades you participated in.")
        reviewee_id = trade_other_user_id(trade, reviewer_id)
        if not reviewee_id:
            raise ValueError("Feedback can only be left for the other trade participant.")
        existing = conn.execute(
            "SELECT * FROM trade_feedback WHERE trade_id = ? AND reviewer_id = ?",
            (trade_id, reviewer_id),
        ).fetchone()
        if existing:
            conn.execute(
                """
                UPDATE trade_feedback
                SET rating = ?, body = ?, updated_at = ?
                WHERE id = ?
                """,
                (rating, body, timestamp, existing["id"]),
            )
            feedback_id = existing["id"]
        else:
            cursor = conn.execute(
                """
                INSERT INTO trade_feedback
                    (trade_id, reviewer_id, reviewee_id, rating, body, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (trade_id, reviewer_id, reviewee_id, rating, body, timestamp, timestamp),
            )
            feedback_id = cursor.lastrowid
            reviewer_name = trade_actor_name(trade, reviewer_id)
            create_notification(
                reviewee_id,
                "trade_feedback",
                f"Feedback received for Trade #{trade_id}",
                f"{reviewer_name} rated the trade {rating}/5.",
                f"/trades/{trade_id}",
                trade_id,
                conn=conn,
            )
    return feedback_id


def trade_recommendation_match_rows(owner_id, want_user_id, viewer_id, selected_ids=None, public_wants=True, limit=8):
    selected_ids = {int(item_id) for item_id in (selected_ids or set()) if str(item_id).strip().isdigit()}
    where = [
        "collection_items.user_id = ?",
        "want_items.user_id = ?",
        "collection_items.quantity_for_trade > 0",
        "collection_items.game = want_items.game",
        """
        (
            (want_items.scryfall_id != '' AND collection_items.scryfall_id = want_items.scryfall_id)
            OR (
                collection_items.card_name = want_items.card_name COLLATE NOCASE
                AND (want_items.set_code = '' OR collection_items.set_code = '' OR collection_items.set_code = want_items.set_code COLLATE NOCASE)
                AND (want_items.collector_number = '' OR collection_items.collector_number = '' OR collection_items.collector_number = want_items.collector_number)
            )
        )
        """,
        "(want_items.condition = '' OR instr(',' || want_items.condition || ',', ',' || COALESCE(collection_items.condition, '') || ',') > 0)",
        "(want_items.finish = '' OR instr(',' || want_items.finish || ',', ',' || COALESCE(collection_items.finish, '') || ',') > 0)",
        "(want_items.language = '' OR instr(',' || want_items.language || ',', ',' || COALESCE(collection_items.language, '') || ',') > 0)",
    ]
    params = [owner_id, want_user_id]
    if int(viewer_id) != int(owner_id):
        clause, privacy_params = visibility_sql_for_user_id(viewer_id, "collection_items.visibility", "collection_items.user_id")
        where.append(clause)
        params.extend(privacy_params)
    if public_wants:
        clause, privacy_params = visibility_sql_for_user_id(viewer_id, "want_items.visibility", "want_items.user_id")
        where.append(clause)
        params.extend(privacy_params)
    if selected_ids:
        placeholders = ",".join("?" for _ in selected_ids)
        where.append(f"collection_items.id NOT IN ({placeholders})")
        params.extend(sorted(selected_ids))
    return rows(
        f"""
        SELECT
            collection_items.*,
            COUNT(want_items.id) AS matched_want_count,
            MAX(want_items.desired_quantity) AS matched_desired_quantity,
            MAX(CASE want_items.priority WHEN 'urgent' THEN 4 WHEN 'high' THEN 3 WHEN 'normal' THEN 2 WHEN 'low' THEN 1 ELSE 2 END) AS matched_want_priority_rank,
            MAX(
                CASE
                    WHEN want_items.budget_cap_usd != '' AND collection_items.price_usd != ''
                        AND CAST(collection_items.price_usd AS REAL) <= CAST(want_items.budget_cap_usd AS REAL)
                    THEN 1 ELSE 0
                END
            ) AS matched_within_budget
        FROM collection_items
        JOIN want_items ON {' AND '.join(where)}
        GROUP BY collection_items.id
        ORDER BY
            matched_want_priority_rank DESC,
            matched_within_budget DESC,
            matched_want_count DESC,
            COALESCE(CAST(NULLIF(collection_items.price_usd, '') AS REAL), 0) DESC,
            collection_items.card_name COLLATE NOCASE,
            collection_items.set_name COLLATE NOCASE
        LIMIT ?
        """,
        [*params, max(1, min(int(limit or 8), 20))],
    )


def trade_recommendation_available_rows(owner_id, viewer_id, selected_ids=None, limit=50):
    selected_ids = {int(item_id) for item_id in (selected_ids or set()) if str(item_id).strip().isdigit()}
    where = ["user_id = ?", "quantity_for_trade > 0"]
    params = [owner_id]
    if int(viewer_id) != int(owner_id):
        clause, privacy_params = visibility_sql_for_user_id(viewer_id, "visibility", "user_id")
        where.append(clause)
        params.extend(privacy_params)
    if selected_ids:
        placeholders = ",".join("?" for _ in selected_ids)
        where.append(f"id NOT IN ({placeholders})")
        params.extend(sorted(selected_ids))
    return rows(
        f"""
        SELECT *
        FROM collection_items
        WHERE {' AND '.join(where)}
        ORDER BY
            COALESCE(CAST(NULLIF(price_usd, '') AS REAL), 0) DESC,
            card_name COLLATE NOCASE,
            set_name COLLATE NOCASE
        LIMIT ?
        """,
        [*params, max(1, min(int(limit or 50), 100))],
    )


def trade_recommendation_quantity(item, target_cents=None):
    available = max(1, int(row_value(item, "quantity_for_trade", 1) or 1))
    unit_cents = price_to_cents(normalize_price_usd(row_value(item, "price_usd", "")))
    if not target_cents or not unit_cents:
        return 1
    needed = max(1, int((abs(int(target_cents)) + unit_cents - 1) // unit_cents))
    return min(available, needed)


def trade_recommendation_card(item, side, reason, quantity=1, target_cents=None):
    item = dict(item)
    item["display_price_usd"] = normalize_price_usd(row_value(item, "display_price_usd", row_value(item, "price_usd", "")))
    item["display_price_source"] = "scryfall" if item["display_price_usd"] else ""
    unit_cents = price_to_cents(item["display_price_usd"])
    quantity = max(1, min(int(quantity or 1), int(row_value(item, "quantity_for_trade", 1) or 1)))
    score = abs(abs(int(target_cents or 0)) - (unit_cents * quantity)) if target_cents else 0
    return {
        "side": side,
        "item": item,
        "quantity": quantity,
        "reason": reason,
        "unit_cents": unit_cents,
        "value_cents": unit_cents * quantity,
        "score": score,
    }


def trade_value_balance_recommendations(user_id, recipient_id, selected_offer, selected_request, selected_offer_ids, selected_request_ids, price_basis="", limit=4):
    offer_value = trade_entries_value_cents(selected_offer)
    request_value = trade_entries_value_cents(selected_request)
    gap = request_value - offer_value
    if abs(gap) <= max(100, round(max(offer_value, request_value) * 0.05)):
        return {"side": "", "gap_cents": gap, "cards": []}
    if gap > 0:
        side = "offer"
        owner_id = user_id
        selected_ids = selected_offer_ids
        reason = "Helps balance the higher request value"
    else:
        side = "request"
        owner_id = recipient_id
        selected_ids = selected_request_ids
        reason = "Helps balance the higher offer value"
    cards = []
    for item in trade_recommendation_available_rows(owner_id, user_id, selected_ids, limit=60):
        priced = apply_trade_price_basis(item, price_basis)
        quantity = trade_recommendation_quantity(priced, gap)
        cards.append(trade_recommendation_card(priced, side, reason, quantity=quantity, target_cents=gap))
    cards = sorted(cards, key=lambda card: (0 if card["unit_cents"] else 1, card["score"], -card["value_cents"], row_value(card["item"], "card_name", "").lower()))
    return {"side": side, "gap_cents": gap, "cards": cards[:max(1, min(int(limit or 4), 10))]}


def trade_recommendations_for_pair(user_id, recipient_id, selected_offer, selected_request, selected_quantities=None, price_basis="", limit=4):
    selected_quantities = selected_quantities or {}
    selected_offer_ids = set((selected_quantities.get("offer") or {}).keys())
    selected_request_ids = set((selected_quantities.get("request") or {}).keys())
    offer_wishlist = [
        trade_recommendation_card(
            apply_trade_price_basis(item, price_basis),
            "offer",
            "Matches their wishlist",
            quantity=trade_recommendation_quantity(item),
        )
        for item in trade_recommendation_match_rows(
            user_id,
            recipient_id,
            user_id,
            selected_offer_ids,
            public_wants=True,
            limit=limit,
        )
    ]
    request_wishlist = [
        trade_recommendation_card(
            apply_trade_price_basis(item, price_basis),
            "request",
            "Matches your wishlist",
            quantity=trade_recommendation_quantity(item),
        )
        for item in trade_recommendation_match_rows(
            recipient_id,
            user_id,
            user_id,
            selected_request_ids,
            public_wants=False,
            limit=limit,
        )
    ]
    balance = trade_value_balance_recommendations(
        user_id,
        recipient_id,
        selected_offer,
        selected_request,
        selected_offer_ids,
        selected_request_ids,
        price_basis=price_basis,
        limit=limit,
    )
    return {
        "offer_wishlist": offer_wishlist,
        "request_wishlist": request_wishlist,
        "balance": balance,
    }


def trade_dispute_category_label(category):
    return dict(TRADE_DISPUTE_CATEGORY_OPTIONS).get(str(category or ""), "Other issue")


def trade_dispute_status_label(status):
    return dict(TRADE_DISPUTE_STATUS_OPTIONS).get(str(status or ""), "Open")


def trade_dispute_status_class(status):
    return {
        "open": "pending",
        "reviewing": "pending",
        "resolved": "accepted",
        "dismissed": "declined",
    }.get(str(status or ""), "pending")


def normalize_trade_dispute_category(category):
    category = sanitize_text_input(category, max_length=40).strip()
    if category not in dict(TRADE_DISPUTE_CATEGORY_OPTIONS):
        category = "other"
    return category


def normalize_trade_dispute_status(status):
    status = sanitize_text_input(status, max_length=40).strip()
    if status not in dict(TRADE_DISPUTE_STATUS_OPTIONS):
        raise ValueError("Choose a valid issue status.")
    return status


DISPUTE_EVIDENCE_MAX_BYTES = 5 * 1024 * 1024
DISPUTE_EVIDENCE_ALLOWED_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "application/pdf",
    "text/plain",
}
DISPUTE_EVIDENCE_EXTENSION_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".log": "text/plain",
}


def dispute_evidence_type_matches(content_type, content):
    if content_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if content_type == "image/gif":
        return content.startswith((b"GIF87a", b"GIF89a"))
    if content_type == "image/webp":
        return len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP"
    if content_type == "application/pdf":
        return content.startswith(b"%PDF-")
    if content_type == "text/plain":
        try:
            content.decode("utf-8")
            return True
        except UnicodeDecodeError:
            try:
                content.decode("utf-16")
                return True
            except UnicodeDecodeError:
                return False
    return False


def normalize_dispute_evidence_upload(upload, note=""):
    if not upload:
        return None
    content = upload.get("content") or b""
    if isinstance(content, str):
        content = content.encode("utf-8")
    if not content:
        return None
    if len(content) > DISPUTE_EVIDENCE_MAX_BYTES:
        raise ValueError("Evidence attachments must be 5 MB or smaller.")
    filename = safe_download_filename(upload.get("filename") or "evidence", default="evidence")
    extension_type = DISPUTE_EVIDENCE_EXTENSION_TYPES.get(Path(filename).suffix.lower())
    content_type = sanitize_text_input(upload.get("content_type") or "", max_length=120).strip().lower().split(";", 1)[0]
    if content_type in ("", "application/octet-stream") and extension_type:
        content_type = extension_type
    if content_type not in DISPUTE_EVIDENCE_ALLOWED_TYPES and extension_type in DISPUTE_EVIDENCE_ALLOWED_TYPES:
        content_type = extension_type
    if content_type not in DISPUTE_EVIDENCE_ALLOWED_TYPES:
        raise ValueError("Evidence must be a PNG, JPG, GIF, WebP, PDF, or plain text file.")
    if not dispute_evidence_type_matches(content_type, content):
        raise ValueError("Evidence file contents do not match an allowed file type.")
    return {
        "original_filename": filename,
        "content_type": content_type,
        "file_size": len(content),
        "checksum_sha256": hashlib.sha256(content).hexdigest(),
        "note": sanitize_text_input(note, max_length=500).strip(),
        "content": content,
    }


def insert_trade_dispute_evidence_conn(conn, dispute_id, uploader_id, upload, note=""):
    evidence = normalize_dispute_evidence_upload(upload, note)
    if not evidence:
        return None
    cursor = conn.execute(
        """
        INSERT INTO trade_dispute_evidence
            (dispute_id, uploaded_by_user_id, original_filename, content_type, file_size, checksum_sha256, note, content, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            dispute_id,
            uploader_id,
            evidence["original_filename"],
            evidence["content_type"],
            evidence["file_size"],
            evidence["checksum_sha256"],
            evidence["note"],
            evidence["content"],
            now_iso(),
        ),
    )
    return cursor.lastrowid


def add_trade_dispute_evidence(dispute_id, uploader_id, upload, note="", trade_id=None):
    with db() as conn:
        uploader = conn.execute(
            "SELECT id, role, is_admin, is_banned FROM users WHERE id = ?",
            (uploader_id,),
        ).fetchone()
        if not uploader or uploader["is_banned"]:
            raise ValueError("Trade issue not found.")
        dispute = conn.execute(
            """
            SELECT
                trade_disputes.*,
                trades.proposer_id,
                trades.recipient_id,
                proposer.display_name AS proposer_name,
                recipient.display_name AS recipient_name
            FROM trade_disputes
            JOIN trades ON trades.id = trade_disputes.trade_id
            JOIN users proposer ON proposer.id = trades.proposer_id
            JOIN users recipient ON recipient.id = trades.recipient_id
            WHERE trade_disputes.id = ?
            """,
            (dispute_id,),
        ).fetchone()
        if not dispute or (trade_id is not None and int(dispute["trade_id"]) != int(trade_id)) or (not user_has_capability(uploader, CAP_MODERATE_DISPUTES) and uploader_id not in (dispute["proposer_id"], dispute["recipient_id"])):
            raise ValueError("Trade issue not found.")
        evidence_id = insert_trade_dispute_evidence_conn(conn, dispute_id, uploader_id, upload, note)
        if not evidence_id:
            raise ValueError("Choose an evidence file before uploading.")
        actor = trade_actor_name(dispute, uploader_id)
        for admin in conn.execute("SELECT id FROM users WHERE role IN ('owner', 'admin', 'moderator') AND is_banned = 0").fetchall():
            create_notification(
                admin["id"],
                "trade_dispute",
                f"Evidence added to Trade #{dispute['trade_id']}",
                f"{actor} added evidence to issue #{dispute_id}.",
                f"/admin/disputes?status={dispute['status']}&q={dispute['trade_id']}",
                dispute["trade_id"],
                conn=conn,
            )
    return evidence_id


def create_trade_dispute(trade_id, reporter_id, category, body, evidence_upload=None, evidence_note=""):
    category = normalize_trade_dispute_category(category)
    body = sanitize_text_input(body, max_length=2000).strip()
    if not body:
        raise ValueError("Describe the trade issue before reporting it.")
    timestamp = now_iso()
    with db() as conn:
        trade = conn.execute(
            """
            SELECT trades.*, proposer.display_name AS proposer_name, recipient.display_name AS recipient_name
            FROM trades
            JOIN users proposer ON proposer.id = trades.proposer_id
            JOIN users recipient ON recipient.id = trades.recipient_id
            WHERE trades.id = ? AND (trades.proposer_id = ? OR trades.recipient_id = ?)
            """,
            (trade_id, reporter_id, reporter_id),
        ).fetchone()
        if not trade:
            raise ValueError("Trade not found.")
        cursor = conn.execute(
            """
            INSERT INTO trade_disputes
                (trade_id, reporter_id, category, status, body, created_at, updated_at)
            VALUES (?, ?, ?, 'open', ?, ?, ?)
            """,
            (trade_id, reporter_id, category, body, timestamp, timestamp),
        )
        dispute_id = cursor.lastrowid
        evidence_id = insert_trade_dispute_evidence_conn(conn, dispute_id, reporter_id, evidence_upload, evidence_note)
        reporter_name = trade_actor_name(trade, reporter_id)
        snippet = body.replace("\r", " ").replace("\n", " ")[:180]
        evidence_line = " Evidence attached." if evidence_id else ""
        for admin in conn.execute("SELECT id FROM users WHERE role IN ('owner', 'admin', 'moderator') AND is_banned = 0").fetchall():
            create_notification(
                admin["id"],
                "trade_dispute",
                f"Trade #{trade_id} issue reported",
                f"{reporter_name} reported {trade_dispute_category_label(category).lower()}: {snippet}{evidence_line}",
                f"/admin/disputes?status=open&q={trade_id}",
                trade_id,
                conn=conn,
            )
    return dispute_id


def prune_trade_dispute_evidence(retention_days=None):
    days = dispute_evidence_retention_days() if retention_days is None else normalize_policy_days(
        retention_days,
        "Evidence retention",
        minimum=0,
    )
    if days <= 0:
        return {"deleted": 0, "retention_days": 0, "cutoff": ""}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()
    with db() as conn:
        cursor = conn.execute(
            """
            DELETE FROM trade_dispute_evidence
            WHERE dispute_id IN (
                SELECT id
                FROM trade_disputes
                WHERE status IN ('resolved', 'dismissed')
                    AND COALESCE(NULLIF(resolved_at, ''), NULLIF(updated_at, ''), created_at) < ?
            )
            """,
            (cutoff,),
        )
        deleted = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
    return {"deleted": deleted, "retention_days": days, "cutoff": cutoff}


def trade_dispute_filter_value(filters, key):
    value = (filters or {}).get(key, "")
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return sanitize_text_input(value, max_length=120).strip()


def trade_dispute_admin_filters(filters=None):
    q = trade_dispute_filter_value(filters, "q")
    status = trade_dispute_filter_value(filters, "status")
    category = trade_dispute_filter_value(filters, "category")
    if status and status not in dict(TRADE_DISPUTE_STATUS_OPTIONS):
        status = ""
    if category and category not in dict(TRADE_DISPUTE_CATEGORY_OPTIONS):
        category = ""
    return {"q": q, "status": status, "category": category}










def update_trade_dispute_admin(dispute_id, admin_user_id, status_value, admin_note="", request_ip="", user_agent="", resolution_note=""):
    status_value = normalize_trade_dispute_status(status_value)
    admin_note = sanitize_text_input(admin_note, max_length=2000).strip()
    resolution_note = sanitize_text_input(resolution_note, max_length=2000).strip()
    timestamp = now_iso()
    with db() as conn:
        admin = conn.execute("SELECT * FROM users WHERE id = ? AND is_banned = 0", (admin_user_id,)).fetchone()
        if not user_has_capability(admin, CAP_MODERATE_DISPUTES):
            raise ValueError("Moderator access is required.")
        dispute = conn.execute(
            """
            SELECT
                trade_disputes.*,
                trades.proposer_id,
                trades.recipient_id
            FROM trade_disputes
            JOIN trades ON trades.id = trade_disputes.trade_id
            WHERE trade_disputes.id = ?
            """,
            (dispute_id,),
        ).fetchone()
        if not dispute:
            raise ValueError("Trade issue not found.")
        resolved_by = admin_user_id if status_value in ("resolved", "dismissed") else None
        resolved_at = timestamp if resolved_by else ""
        conn.execute(
            """
            UPDATE trade_disputes
            SET status = ?,
                admin_note = ?,
                resolution_note = ?,
                resolved_by_user_id = ?,
                resolved_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (status_value, admin_note, resolution_note, resolved_by, resolved_at, timestamp, dispute_id),
        )
        label = trade_dispute_status_label(status_value).lower()
        note_line = f" Admin note: {admin_note[:180]}" if admin_note else ""
        notified = set()
        for target_id in (dispute["reporter_id"], dispute["proposer_id"], dispute["recipient_id"]):
            if not target_id or target_id in notified:
                continue
            notified.add(target_id)
            create_notification(
                target_id,
                "trade_dispute",
                f"Trade #{dispute['trade_id']} issue {label}",
                f"An admin marked issue #{dispute_id} as {label}.{note_line}",
                f"/trades/{dispute['trade_id']}",
                dispute["trade_id"],
                conn=conn,
            )
        log_admin_action(
            admin_user_id,
            "trade_dispute_updated",
            dispute["reporter_id"],
            "trade_dispute",
            f"Trade #{dispute['trade_id']} issue #{dispute_id}",
            f"Status set to {trade_dispute_status_label(status_value)}." + (f" Resolution note: {resolution_note[:180]}" if resolution_note else ""),
            request_ip,
            user_agent,
            conn=conn,
        )
    return dispute_id




def update_trade_response(trade_id, user_id, decision, response_note="", fairness_acknowledged=False):
    if decision not in ("accepted", "declined"):
        raise ValueError("Choose accept or decline.")
    timestamp = now_iso()
    with db() as conn:
        trade = conn.execute(
            "SELECT * FROM trades WHERE id = ? AND recipient_id = ? AND status = 'pending'",
            (trade_id, user_id),
        ).fetchone()
        if not trade:
            raise ValueError("Trade not found.")
        if decision == "accepted":
            offered, requested = trade_fairness_entries_for_trade_conn(conn, trade_id)
            validate_trade_fairness_for_send(offered, requested, fairness_acknowledged)
        conn.execute(
            "UPDATE trades SET status = ?, response_note = ?, updated_at = ? WHERE id = ?",
            (decision, sanitize_text_input(response_note, max_length=1200).strip(), timestamp, trade_id),
        )
        notify_trade_status_change(conn, trade_id, user_id, decision, response_note)
    send_pending_trade_notification_emails()


def cancel_trade_offer(trade_id, user_id):
    timestamp = now_iso()
    with db() as conn:
        trade = conn.execute(
            "SELECT * FROM trades WHERE id = ? AND proposer_id = ? AND status = 'pending'",
            (trade_id, user_id),
        ).fetchone()
        if not trade:
            raise ValueError("Trade not found.")
        conn.execute("UPDATE trades SET status = 'cancelled', updated_at = ? WHERE id = ?", (timestamp, trade_id))
        notify_trade_status_change(conn, trade_id, user_id, "cancelled")
    send_pending_trade_notification_emails()


def insert_trade_item_record(conn, trade_id, owner_id, item, quantity, side):
    cursor = conn.execute(
        """
        INSERT INTO trade_items
            (trade_id, owner_id, collection_item_id, card_name, set_name, quantity, condition, condition_notes, finish, price_usd, price_source, side)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade_id,
            owner_id,
            item["id"],
            item["card_name"],
            item["set_name"],
            quantity,
            item["condition"],
            row_value(item, "condition_notes", ""),
            item["finish"],
            trade_item_price_usd(item),
            trade_item_price_source(item),
            side,
        ),
    )
    copy_collection_item_photos_to_trade_item_conn(conn, item["id"], cursor.lastrowid)
    return cursor.lastrowid


def add_trade_item(trade_id, owner_id, item, quantity, side):
    with db() as conn:
        insert_trade_item_record(conn, trade_id, owner_id, item, quantity, side)


def create_trade_offer(proposer_id, recipient_id, proposer_note, offered, requested, counter_trade_id=0, price_source_preference=""):
    timestamp = now_iso()
    price_source_preference = normalize_price_basis(price_source_preference)
    validate_trade_fairness_for_creation(offered, requested)
    with db() as conn:
        counter_source = None
        if counter_trade_id:
            counter_source = conn.execute(
                """
                SELECT *
                FROM trades
                WHERE id = ?
                    AND proposer_id = ?
                    AND recipient_id = ?
                    AND status = 'pending'
                """,
                (counter_trade_id, recipient_id, proposer_id),
            ).fetchone()
            if not counter_source:
                raise ValueError("The original trade is no longer available to counter.")
        cursor = conn.execute(
            """
            INSERT INTO trades
                (proposer_id, recipient_id, proposer_note, price_source_preference, countered_from_trade_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (proposer_id, recipient_id, sanitize_text_input(proposer_note, max_length=1200).strip(), price_source_preference, counter_trade_id or None, timestamp, timestamp),
        )
        trade_id = cursor.lastrowid
        for item, quantity in offered:
            insert_trade_item_record(conn, trade_id, proposer_id, item, quantity, "offered")
        for item, quantity in requested:
            insert_trade_item_record(conn, trade_id, recipient_id, item, quantity, "requested")
        if counter_source:
            conn.execute(
                """
                UPDATE trades
                SET status = 'countered', counter_trade_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (trade_id, timestamp, counter_trade_id),
            )
        trade = trade_with_names_conn(conn, trade_id)
        if counter_source:
            create_notification(
                recipient_id,
                "trade_counter",
                f"New counter offer: Trade #{trade_id}",
                f"{trade['proposer_name']} sent a counter offer for Trade #{counter_trade_id}.",
                f"/trades/{trade_id}",
                trade_id,
                conn=conn,
            )
        else:
            create_notification(
                recipient_id,
                "trade_offer",
                f"New trade offer: Trade #{trade_id}",
                f"{trade['proposer_name']} sent you a trade offer.",
                f"/trades/{trade_id}",
                trade_id,
                conn=conn,
            )
    send_pending_trade_notification_emails()
    return trade_id


def copy_trade_item_photos_to_collection_item_conn(conn, trade_item_id, collection_item_id):
    existing_checksums = {
        item["checksum_sha256"]
        for item in conn.execute(
            "SELECT checksum_sha256 FROM collection_item_photos WHERE collection_item_id = ?",
            (collection_item_id,),
        ).fetchall()
    }
    remaining = max(0, CARD_PHOTO_MAX_COUNT - len(existing_checksums))
    if not remaining:
        return 0
    copied = 0
    photos = conn.execute(
        "SELECT * FROM trade_item_photos WHERE trade_item_id = ? ORDER BY created_at, id",
        (trade_item_id,),
    ).fetchall()
    for photo in photos:
        if photo["checksum_sha256"] in existing_checksums:
            continue
        conn.execute(
            """
            INSERT INTO collection_item_photos
                (collection_item_id, original_filename, content_type, file_size, checksum_sha256, caption, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                collection_item_id,
                photo["original_filename"],
                photo["content_type"],
                photo["file_size"],
                photo["checksum_sha256"],
                photo["caption"],
                photo["content"],
                now_iso(),
            ),
        )
        existing_checksums.add(photo["checksum_sha256"])
        copied += 1
        if copied >= remaining:
            break
    return copied


def complete_trade(trade_id, completed_by_user_id=None):
    with db() as conn:
        trade = conn.execute("SELECT * FROM trades WHERE id = ? AND status = 'accepted'", (trade_id,)).fetchone()
        if not trade:
            raise ValueError("Only accepted trades can be completed.")
        trade_items = conn.execute("SELECT * FROM trade_items WHERE trade_id = ?", (trade_id,)).fetchall()
        if not trade_items:
            raise ValueError("This trade has no cards selected.")

        for trade_item in trade_items:
            source = conn.execute(
                "SELECT * FROM collection_items WHERE id = ? AND user_id = ?",
                (trade_item["collection_item_id"], trade_item["owner_id"]),
            ).fetchone()
            if not source:
                raise ValueError(f"{trade_item['card_name']} is no longer in the owner's collection.")
            if source["quantity"] < trade_item["quantity"]:
                raise ValueError(f"{source['card_name']} no longer has enough quantity to complete this trade.")

            recipient_id = trade["recipient_id"] if trade_item["side"] == "offered" else trade["proposer_id"]
            new_source_quantity = source["quantity"] - trade_item["quantity"]
            new_trade_quantity = min(max(0, source["quantity_for_trade"] - trade_item["quantity"]), new_source_quantity)

            if new_source_quantity == 0:
                conn.execute("DELETE FROM collection_items WHERE id = ?", (source["id"],))
            else:
                conn.execute(
                    """
                    UPDATE collection_items
                    SET quantity = ?, quantity_for_trade = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (new_source_quantity, new_trade_quantity, now_iso(), source["id"]),
                )

            existing = conn.execute(
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
                    recipient_id,
                    source["game"],
                    source["card_name"],
                    source["set_name"],
                    source["set_code"],
                    source["collector_number"],
                    source["finish"],
                    source["condition"],
                    source["language"],
                ),
            ).fetchone()
            if existing:
                old_price = row_value(existing, "price_usd", "")
                conn.execute(
                    """
                    UPDATE collection_items
                    SET quantity = quantity + ?,
                        condition_notes = COALESCE(NULLIF(condition_notes, ''), ?),
                        price_usd = COALESCE(NULLIF(price_usd, ''), ?),
                        price_source = COALESCE(NULLIF(price_source, ''), ?),
                        tcgplayer_product_id = COALESCE(NULLIF(tcgplayer_product_id, ''), ?),
                        cardmarket_product_id = COALESCE(NULLIF(cardmarket_product_id, ''), ?),
                        cardkingdom_sku = COALESCE(NULLIF(cardkingdom_sku, ''), ?),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        trade_item["quantity"],
                        row_value(trade_item, "condition_notes", ""),
                        row_value(source, "price_usd", ""),
                        "scryfall" if row_value(source, "price_usd", "") else "",
                        row_value(source, "tcgplayer_product_id", ""),
                        row_value(source, "cardmarket_product_id", ""),
                        row_value(source, "cardkingdom_sku", ""),
                        now_iso(),
                        existing["id"],
                    ),
                )
                copy_trade_item_photos_to_collection_item_conn(conn, trade_item["id"], existing["id"])
                new_price = old_price or row_value(source, "price_usd", "")
                record_price_history_for_item(existing["id"], recipient_id, existing, old_price, new_price, conn=conn)
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO collection_items
                        (user_id, game, card_name, set_name, set_code, collector_number, finish, condition, condition_notes, language,
                         quantity, quantity_for_trade, scryfall_id, image_url, mana_cost, type_line, oracle_text,
                         rarity, colors, color_identity, scryfall_uri, price_usd, price_source, tcgplayer_product_id,
                         cardmarket_product_id, cardkingdom_sku, notes, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        recipient_id,
                        source["game"],
                        source["card_name"],
                        source["set_name"],
                        source["set_code"],
                        source["collector_number"],
                        source["finish"],
                        source["condition"],
                        row_value(trade_item, "condition_notes", ""),
                        source["language"],
                        trade_item["quantity"],
                        source["scryfall_id"],
                        source["image_url"],
                        source["mana_cost"],
                        source["type_line"],
                        source["oracle_text"],
                        source["rarity"],
                        source["colors"],
                        source["color_identity"],
                        source["scryfall_uri"],
                        source["price_usd"],
                        "scryfall" if row_value(source, "price_usd", "") else "",
                        row_value(source, "tcgplayer_product_id", ""),
                        row_value(source, "cardmarket_product_id", ""),
                        row_value(source, "cardkingdom_sku", ""),
                        f"Received from trade #{trade_id}",
                        now_iso(),
                        now_iso(),
                    ),
                )
                copy_trade_item_photos_to_collection_item_conn(conn, trade_item["id"], cursor.lastrowid)
                new_item = dict(source)
                new_item["id"] = cursor.lastrowid
                record_price_history_for_item(cursor.lastrowid, recipient_id, new_item, "", row_value(source, "price_usd", ""), conn=conn)

        conn.execute("UPDATE trades SET status = 'completed', updated_at = ? WHERE id = ?", (now_iso(), trade_id))
        if completed_by_user_id:
            notify_trade_status_change(conn, trade_id, completed_by_user_id, "completed")
        else:
            for target_id in (trade["proposer_id"], trade["recipient_id"]):
                create_notification(
                    target_id,
                    "trade_status",
                    f"Trade #{trade_id} completed",
                    f"Trade #{trade_id} was marked complete.",
                    f"/trades/{trade_id}",
                    trade_id,
                    conn=conn,
                )
    send_pending_trade_notification_emails()


__all__ = [
    "validate_collection_form",
    "parse_trade_quantities",
    "parse_counter_trade_id",
    "counter_source_trade_for",
    "linked_trade_for_user",
    "counter_trade_query",
    "render_counter_trade",
    "trade_comment_rows",
    "trade_with_names_conn",
    "trade_actor_name",
    "trade_other_user_id",
    "notify_trade_status_change",
    "add_trade_comment",
    "trade_feedback_rows",
    "trade_feedback_for_user",
    "reputation_summary",
    "recent_feedback_for_user",
    "submit_trade_feedback",
    "trade_recommendation_match_rows",
    "trade_recommendation_available_rows",
    "trade_recommendation_quantity",
    "trade_recommendation_card",
    "trade_value_balance_recommendations",
    "trade_recommendations_for_pair",
    "trade_dispute_category_label",
    "trade_dispute_status_label",
    "trade_dispute_status_class",
    "normalize_trade_dispute_category",
    "normalize_trade_dispute_status",
    "DISPUTE_EVIDENCE_MAX_BYTES",
    "normalize_dispute_evidence_upload",
    "insert_trade_dispute_evidence_conn",
    "add_trade_dispute_evidence",
    "trade_dispute_rows",
    "trade_dispute_evidence_rows",
    "trade_dispute_evidence_for_user",
    "trade_item_photo_rows",
    "trade_item_photo_for_user",
    "create_trade_dispute",
    "prune_trade_dispute_evidence",
    "trade_dispute_filter_value",
    "trade_dispute_admin_filters",
    "trade_dispute_admin_where",
    "trade_dispute_admin_count",
    "open_trade_dispute_count",
    "trade_dispute_trend_cutoff",
    "trade_dispute_repeat_issue_trends",
    "trade_dispute_category_trends",
    "trade_dispute_admin_rows",
    "update_trade_dispute_admin",
    "trade_fairness_entries_for_trade_conn",
    "update_trade_response",
    "cancel_trade_offer",
    "insert_trade_item_record",
    "add_trade_item",
    "create_trade_offer",
    "copy_trade_item_photos_to_collection_item_conn",
    "complete_trade",
]
