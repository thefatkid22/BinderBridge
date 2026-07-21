"""Signed-in account settings HTTP route handlers."""

from http import HTTPStatus

def account_profile(self, user):
    form = self.read_form()
    active_section = workspace_section_from_form(form, ("account-profile", "account-notifications"), default="account-profile")
    if not verify_password(form.get("current_password", [""])[0], user["password_hash"]):
        return self.html(render_account(user, notice="Current password is incorrect.", status="error", active_section=active_section), HTTPStatus.BAD_REQUEST)
    preferred_price_source = form.get("preferred_price_source", [""])[0]
    notify_trade_offer_enabled = form.get("notify_trade_offer_enabled", [""])[0] == "1"
    notify_trade_comment_enabled = form.get("notify_trade_comment_enabled", [""])[0] == "1"
    notify_trade_counter_enabled = form.get("notify_trade_counter_enabled", [""])[0] == "1"
    notify_trade_status_enabled = form.get("notify_trade_status_enabled", [""])[0] == "1"
    notify_import_complete_enabled = form.get("notify_import_complete_enabled", [""])[0] == "1"
    notify_admin_notice_enabled = form.get("notify_admin_notice_enabled", [""])[0] == "1"
    if email_delivery_configured():
        email_trade_notifications_enabled = form.get("email_trade_notifications_enabled", [""])[0] == "1"
        email_trade_offer_enabled = form.get("email_trade_offer_enabled", [""])[0] == "1"
        email_trade_comment_enabled = form.get("email_trade_comment_enabled", [""])[0] == "1"
        email_trade_counter_enabled = form.get("email_trade_counter_enabled", [""])[0] == "1"
        email_trade_status_enabled = form.get("email_trade_status_enabled", [""])[0] == "1"
        email_price_alert_enabled = form.get("email_price_alert_enabled", [""])[0] == "1"
        email_import_complete_enabled = form.get("email_import_complete_enabled", [""])[0] == "1"
        email_admin_notice_enabled = form.get("email_admin_notice_enabled", [""])[0] == "1"
        email_digest_frequency = form.get("email_digest_frequency", ["immediate"])[0]
        email_digest_time = form.get("email_digest_time", ["09:00"])[0]
        email_digest_weekday = form.get("email_digest_weekday", ["0"])[0]
        notification_timezone = form.get("notification_timezone", ["UTC"])[0]
        quiet_hours_enabled = form.get("quiet_hours_enabled", [""])[0] == "1"
        quiet_hours_start = form.get("quiet_hours_start", ["22:00"])[0]
        quiet_hours_end = form.get("quiet_hours_end", ["07:00"])[0]
    else:
        email_trade_notifications_enabled = bool(row_value(user, "email_trade_notifications_enabled", 0))
        email_trade_offer_enabled = bool(row_value(user, "email_trade_offer_enabled", 1))
        email_trade_comment_enabled = bool(row_value(user, "email_trade_comment_enabled", 1))
        email_trade_counter_enabled = bool(row_value(user, "email_trade_counter_enabled", 1))
        email_trade_status_enabled = bool(row_value(user, "email_trade_status_enabled", 1))
        email_price_alert_enabled = bool(row_value(user, "email_price_alert_enabled", 0))
        email_import_complete_enabled = bool(row_value(user, "email_import_complete_enabled", 0))
        email_admin_notice_enabled = bool(row_value(user, "email_admin_notice_enabled", 0))
        email_digest_frequency = row_value(user, "email_digest_frequency", "immediate")
        email_digest_time = row_value(user, "email_digest_time", "09:00")
        email_digest_weekday = row_value(user, "email_digest_weekday", 0)
        notification_timezone = row_value(user, "notification_timezone", "UTC")
        quiet_hours_enabled = bool(row_value(user, "quiet_hours_enabled", 0))
        quiet_hours_start = row_value(user, "quiet_hours_start", "22:00")
        quiet_hours_end = row_value(user, "quiet_hours_end", "07:00")
    try:
        update_user_profile(
            user["id"],
            form.get("username", [""])[0],
            form.get("display_name", [""])[0],
            form.get("email", [""])[0],
            form.get("bio", [""])[0],
            form.get("public_email", [""])[0] == "1",
            preferred_price_source,
            form.get("price_alerts_enabled", [""])[0] == "1",
            form.get("price_alert_threshold_percent", ["0"])[0],
            form.get("watchlist_alerts_enabled", [""])[0] == "1",
            email_trade_notifications_enabled,
            email_trade_offer_enabled,
            email_trade_comment_enabled,
            email_trade_counter_enabled,
            email_trade_status_enabled,
            notify_trade_offer_enabled,
            notify_trade_comment_enabled,
            notify_trade_counter_enabled,
            notify_trade_status_enabled,
            notify_import_complete_enabled,
            notify_admin_notice_enabled,
            email_price_alert_enabled,
            email_import_complete_enabled,
            email_admin_notice_enabled,
            email_digest_frequency,
            email_digest_time,
            email_digest_weekday,
            notification_timezone,
            quiet_hours_enabled,
            quiet_hours_start,
            quiet_hours_end,
            form.get("stale_trade_reminder_days", ["3"])[0],
            form.get("collection_value_visibility", [VISIBILITY_MEMBERS])[0],
        )
    except ValueError as exc:
        return self.html(render_account(user, notice=str(exc), status="error", active_section=active_section), HTTPStatus.BAD_REQUEST)
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    notice = "Profile updated."
    price_basis = normalize_price_basis(preferred_price_source)
    if price_basis in PRICE_PROVIDER_KEYS:
        applied = apply_cached_provider_prices([user["id"]], price_basis)
        queued = schedule_price_refresh_jobs(user["id"], provider=price_basis, force=False)
        if queued:
            start_price_refresh_worker()
            notice += f" Applied {applied} cached {price_provider_label(price_basis)} prices and queued {queued} batched price updates."
        elif applied:
            notice += f" Applied {applied} cached {price_provider_label(price_basis)} prices."
        elif not price_provider_ready(price_basis):
            notice += f" {price_provider_label(price_basis)} is not configured yet."
    return self.html(render_account(updated, notice=notice, active_section=active_section))

def account_password(self, user):
    form = self.read_form()
    try:
        change_user_password(
            user["id"],
            form.get("current_password", [""])[0],
            form.get("new_password", [""])[0],
            form.get("confirm_password", [""])[0],
            keep_session_token=self.current_session_token(),
        )
    except ValueError as exc:
        return self.html(render_account(user, notice=str(exc), status="error", active_section="account-security"), HTTPStatus.BAD_REQUEST)
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    return self.html(render_account(updated, notice="Password changed. Other sessions were signed out.", active_section="account-security"))

def account_two_factor_start(self, user):
    form = self.read_form()
    if not verify_password(form.get("current_password", [""])[0], user["password_hash"]):
        return self.html(render_account(user, notice="Current password is incorrect.", status="error", active_section="account-security"), HTTPStatus.BAD_REQUEST)
    start_user_totp_setup(user["id"])
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    return self.html(render_account(updated, notice="Two-factor setup started. Add the setup key to your authenticator app, then enter the code.", active_section="account-security"))

def account_two_factor_enable(self, user):
    form = self.read_form()
    if not verify_password(form.get("current_password", [""])[0], user["password_hash"]):
        return self.html(render_account(user, notice="Current password is incorrect.", status="error", active_section="account-security"), HTTPStatus.BAD_REQUEST)
    try:
        recovery_codes = enable_user_totp(user["id"], form.get("two_factor_code", [""])[0])
    except ValueError as exc:
        updated = row("SELECT * FROM users WHERE id = ?", (user["id"],)) or user
        return self.html(render_account(updated, notice=str(exc), status="error", active_section="account-security"), HTTPStatus.BAD_REQUEST)
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    return self.html(render_account(updated, notice="Two-factor authentication enabled. Save your recovery codes now.", recovery_codes=recovery_codes, active_section="account-security"))

def account_two_factor_disable(self, user):
    form = self.read_form()
    if not verify_password(form.get("current_password", [""])[0], user["password_hash"]):
        return self.html(render_account(user, notice="Current password is incorrect.", status="error", active_section="account-security"), HTTPStatus.BAD_REQUEST)
    disable_user_totp(user["id"])
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    return self.html(render_account(updated, notice="Two-factor authentication disabled.", active_section="account-security"))

def account_two_factor_recovery_codes(self, user):
    form = self.read_form()
    if not verify_password(form.get("current_password", [""])[0], user["password_hash"]):
        return self.html(render_account(user, notice="Current password is incorrect.", status="error", active_section="account-security"), HTTPStatus.BAD_REQUEST)
    try:
        recovery_codes = regenerate_user_totp_recovery_codes(user["id"])
    except ValueError as exc:
        return self.html(render_account(user, notice=str(exc), status="error", active_section="account-security"), HTTPStatus.BAD_REQUEST)
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    return self.html(render_account(updated, notice="New recovery codes generated. Save them now.", recovery_codes=recovery_codes, active_section="account-security"))

def account_passkey_register_options(self, user):
    try:
        rp_id, origin = passkey_request_context(self)
        return self.passkey_json(passkey_registration_options(user, rp_id, origin))
    except ValueError as exc:
        return self.passkey_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

def account_passkey_register(self, user):
    form = self.read_form()
    if not verify_password(form.get("current_password", [""])[0], user["password_hash"]):
        return self.passkey_json({"error": "Current password is incorrect."}, HTTPStatus.UNAUTHORIZED)
    try:
        credential_id = complete_passkey_registration(
            user["id"],
            form.get("token", [""])[0],
            form.get("credential", [""])[0],
            form.get("nickname", [""])[0],
        )
    except ValueError as exc:
        return self.passkey_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
    return self.passkey_json({"ok": True, "credential_id": credential_id})

def account_passkey_delete(self, user, path):
    parts = path.strip("/").split("/")
    try:
        credential_id = int(parts[2])
    except (ValueError, IndexError):
        return self.redirect("/account")
    form = self.read_form()
    if not verify_password(form.get("current_password", [""])[0], user["password_hash"]):
        return self.html(render_account(user, notice="Current password is incorrect.", status="error", active_section="account-security"), HTTPStatus.BAD_REQUEST)
    deleted = delete_passkey_credential(user["id"], credential_id)
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],)) or user
    notice = "Passkey removed." if deleted else "Passkey not found."
    return self.html(render_account(updated, notice=notice, status="info" if deleted else "warning", active_section="account-security"))

def account_export(self, user):
    filename, data = export_account_json(user["id"])
    return self.binary(data, "application/json; charset=utf-8", filename)

ACCOUNT_SETTINGS_ROUTE_METHODS = (
    "account_profile",
    "account_password",
    "account_two_factor_start",
    "account_two_factor_enable",
    "account_two_factor_disable",
    "account_two_factor_recovery_codes",
    "account_passkey_register_options",
    "account_passkey_register",
    "account_passkey_delete",
    "account_export",
)

__all__ = ["ACCOUNT_SETTINGS_ROUTE_METHODS", *ACCOUNT_SETTINGS_ROUTE_METHODS]
