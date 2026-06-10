"""Trade and notification HTTP route handlers.

These functions are attached to app.App by app.py after the class is defined.
Shared helpers are injected by the app facade at import time.
"""

import sqlite3
from http import HTTPStatus


def database_locked(exc):
    message = str(exc or "").lower()
    return "database is locked" in message or "database table is locked" in message


def render_trade_database_busy(self, user, trade_id):
    return self.html(
        render_trade_detail(
            user,
            trade_id,
            notice="The database is busy finishing another update. Wait a moment, then try attaching the evidence again.",
            status="error",
        ),
        HTTPStatus.SERVICE_UNAVAILABLE,
    )


def trade_new(self, method, user, query):
    if method == "GET":
        try:
            recipient_id = int(query.get("recipient_id", ["0"])[0] or 0)
        except (TypeError, ValueError):
            recipient_id = 0
        recipient = row("SELECT * FROM users WHERE id = ?", (recipient_id,))
        notice = None
        status = "info"
        proposer_note = query.get("proposer_note", [""])[0].strip()
        if recipient and recipient["id"] != user["id"]:
            price_basis = trade_price_basis_for(user, query)
            result = prepare_price_basis_for_users([user["id"], recipient_id], price_basis, force=False)
            if result.get("queued"):
                start_price_refresh_worker()
            notice = price_basis_update_notice(result)
            if result.get("provider") in PRICE_PROVIDER_KEYS and not result.get("configured", True):
                status = "error"
        page = render_new_trade(user, recipient_id, query, proposer_note=proposer_note, notice=notice, status=status)
        if not page:
            return self.not_found(user)
        return self.html(page)
    form = self.read_form()
    try:
        recipient_id = int(form.get("recipient_id", ["0"])[0])
    except ValueError:
        return self.not_found(user)
    recipient = row("SELECT * FROM users WHERE id = ?", (recipient_id,))
    if not recipient or recipient["id"] == user["id"]:
        return self.not_found(user)
    counter_trade_id = parse_counter_trade_id(form)
    if counter_trade_id and not counter_source_trade_for(user["id"], recipient_id, counter_trade_id):
        return self.not_found(user)
    intent = form.get("intent", ["review"])[0]
    selected_quantities = trade_selected_quantities_from_form(form)
    proposer_note = form.get("proposer_note", [""])[0].strip()
    price_basis = trade_price_basis_for(user, form)
    basis_result = prepare_price_basis_for_users([user["id"], recipient_id], price_basis, force=False)
    if basis_result.get("queued"):
        start_price_refresh_worker()
    basis_notice = price_basis_update_notice(basis_result)
    basis_status = "error" if basis_result.get("provider") in PRICE_PROVIDER_KEYS and not basis_result.get("configured", True) else "info"
    if intent == "edit":
        return self.html(render_new_trade(
            user,
            recipient_id,
            form,
            selected_quantities=selected_quantities,
            proposer_note=proposer_note,
            notice=basis_notice or "Make your changes, then review the trade again.",
            status=basis_status,
        ))
    offered = parse_trade_quantities(form, "offer", user["id"], price_basis, viewer_id=user["id"])
    requested = parse_trade_quantities(form, "request", recipient_id, price_basis, viewer_id=user["id"])
    try:
        validate_trade_sides(user, offered, requested)
    except ValueError as exc:
        return self.html(render_new_trade(
            user,
            recipient_id,
            form,
            selected_quantities=selected_quantities,
            proposer_note=proposer_note,
            notice=str(exc),
            status="error",
        ), HTTPStatus.BAD_REQUEST)
    if intent != "send":
        page = render_trade_review(user, recipient_id, form, offered, requested)
        if not page:
            return self.not_found(user)
        return self.html(page)
    try:
        validate_trade_fairness_for_send(offered, requested, form.get("fairness_ack", [""])[0] == "1")
    except ValueError as exc:
        page = render_trade_review(user, recipient_id, form, offered, requested, notice=str(exc), status="error")
        if not page:
            return self.not_found(user)
        return self.html(page, HTTPStatus.BAD_REQUEST)
    try:
        trade_id = create_trade_offer(user["id"], recipient_id, proposer_note, offered, requested, counter_trade_id, price_basis)
    except ValueError as exc:
        return self.html(render_new_trade(
            user,
            recipient_id,
            form,
            selected_quantities=selected_quantities,
            proposer_note=proposer_note,
            notice=str(exc),
            status="error",
        ), HTTPStatus.BAD_REQUEST)
    self.redirect(f"/trades/{trade_id}")

def notification_action(self, user, path):
    parts = path.strip("/").split("/")
    try:
        notification_id = int(parts[1])
    except (ValueError, IndexError):
        return self.not_found(user)
    action = parts[2] if len(parts) > 2 else ""
    if action == "read":
        mark_notification_read(user["id"], notification_id)
    elif action == "delete":
        delete_notification(user["id"], notification_id)
    else:
        return self.not_found(user)
    return self.redirect("/notifications")

def trade_action(self, method, user, path):
    parts = path.strip("/").split("/")
    try:
        trade_id = int(parts[1])
    except (ValueError, IndexError):
        return self.not_found(user)
    if len(parts) == 2 and method == "GET":
        page = render_trade_detail(user, trade_id)
        if not page:
            return self.not_found(user)
        return self.html(page)
    if len(parts) == 6 and parts[2] == "disputes" and parts[4] == "evidence" and method == "GET":
        try:
            dispute_id = int(parts[3])
            evidence_id = int(parts[5])
        except (ValueError, IndexError):
            return self.not_found(user)
        return self.trade_dispute_evidence_download(user, trade_id, dispute_id, evidence_id)
    if len(parts) == 4 and parts[2] == "photos" and method == "GET":
        try:
            photo_id = int(parts[3])
        except (ValueError, IndexError):
            return self.not_found(user)
        return self.trade_item_photo_download(user, trade_id, photo_id)
    trade = row("SELECT * FROM trades WHERE id = ? AND (proposer_id = ? OR recipient_id = ?)", (trade_id, user["id"], user["id"]))
    if not trade:
        return self.not_found(user)
    if len(parts) == 3 and parts[2] == "counter" and method == "GET":
        page = render_counter_trade(user, trade_id)
        if not page:
            return self.not_found(user)
        return self.html(page)
    if len(parts) == 3 and parts[2] == "comments" and method == "POST":
        form = self.read_form()
        try:
            add_trade_comment(trade_id, user["id"], form.get("body", [""])[0])
        except ValueError as exc:
            return self.html(render_trade_detail(user, trade_id, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
        return self.redirect(f"/trades/{trade_id}")
    if len(parts) == 3 and parts[2] == "disputes" and method == "POST":
        form, files = self.read_multipart_form()
        try:
            create_trade_dispute(
                trade_id,
                user["id"],
                form.get("category", ["other"])[0],
                form.get("body", [""])[0],
                files.get("evidence_file"),
                form.get("evidence_note", [""])[0],
            )
        except ValueError as exc:
            return self.html(render_trade_detail(user, trade_id, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
        except sqlite3.OperationalError as exc:
            if database_locked(exc):
                return render_trade_database_busy(self, user, trade_id)
            raise
        return self.html(render_trade_detail(user, trade_id, notice="Issue report sent to the admins."))
    if len(parts) == 5 and parts[2] == "disputes" and parts[4] == "evidence" and method == "POST":
        try:
            dispute_id = int(parts[3])
        except (ValueError, IndexError):
            return self.not_found(user)
        form, files = self.read_multipart_form()
        try:
            add_trade_dispute_evidence(
                dispute_id,
                user["id"],
                files.get("evidence_file"),
                form.get("evidence_note", [""])[0],
                trade_id=trade_id,
            )
        except ValueError as exc:
            return self.html(render_trade_detail(user, trade_id, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
        except sqlite3.OperationalError as exc:
            if database_locked(exc):
                return render_trade_database_busy(self, user, trade_id)
            raise
        return self.redirect(f"/trades/{trade_id}")
    if len(parts) == 3 and parts[2] == "feedback" and method == "POST":
        form = self.read_form()
        try:
            submit_trade_feedback(
                trade_id,
                user["id"],
                form.get("rating", [""])[0],
                form.get("body", [""])[0],
            )
        except ValueError as exc:
            return self.html(render_trade_detail(user, trade_id, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
        return self.redirect(f"/trades/{trade_id}")
    if len(parts) == 3 and parts[2] == "respond" and method == "POST":
        if trade["recipient_id"] != user["id"] or trade["status"] != "pending":
            return self.not_found(user)
        form = self.read_form()
        decision = form.get("decision", [""])[0]
        if decision not in ("accepted", "declined"):
            return self.not_found(user)
        try:
            update_trade_response(
                trade_id,
                user["id"],
                decision,
                form.get("response_note", [""])[0],
                form.get("fairness_ack", [""])[0] == "1",
            )
        except ValueError as exc:
            return self.html(render_trade_detail(user, trade_id, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
        return self.redirect(f"/trades/{trade_id}")
    if len(parts) == 3 and parts[2] == "cancel" and method == "POST":
        if trade["proposer_id"] != user["id"] or trade["status"] != "pending":
            return self.not_found(user)
        try:
            cancel_trade_offer(trade_id, user["id"])
        except ValueError:
            return self.not_found(user)
        return self.redirect(f"/trades/{trade_id}")
    if len(parts) == 3 and parts[2] == "complete" and method == "POST":
        if trade["status"] != "accepted":
            return self.not_found(user)
        try:
            complete_trade(trade_id, completed_by_user_id=user["id"])
        except ValueError as exc:
            return self.html(render_trade_detail(user, trade_id, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
        return self.redirect(f"/trades/{trade_id}")
    return self.not_found(user)

def trade_dispute_evidence_download(self, user, trade_id, dispute_id, evidence_id):
    evidence = trade_dispute_evidence_for_user(evidence_id, user["id"], bool(row_value(user, "is_admin", 0)))
    if not evidence or int(evidence["trade_id"]) != int(trade_id) or int(evidence["dispute_id"]) != int(dispute_id):
        return self.not_found(user)
    return self.binary(evidence["content"], evidence["content_type"], evidence["original_filename"])

def trade_item_photo_download(self, user, trade_id, photo_id):
    photo = trade_item_photo_for_user(photo_id, user["id"])
    if not photo or int(photo["trade_id"]) != int(trade_id):
        return self.not_found(user)
    return self.inline_binary(photo["content"], photo["content_type"], photo["original_filename"])

TRADE_ROUTE_METHODS = ('trade_new', 'notification_action', 'trade_action', 'trade_dispute_evidence_download', 'trade_item_photo_download')

__all__ = [
    "TRADE_ROUTE_METHODS",
    *TRADE_ROUTE_METHODS,
]
