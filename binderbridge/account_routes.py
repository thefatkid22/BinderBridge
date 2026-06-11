"""Account, authentication, and profile HTTP route handlers.

These functions are attached to app.App by app.py after the class is defined.
Shared helpers are injected by the app facade at import time.
"""

from http import HTTPStatus

def login(self, method, user):
    if user:
        return self.redirect("/")
    if method == "GET":
        return self.html(render_login())
    form = self.read_form()
    username = form.get("username", [""])[0].strip()
    password = form.get("password", [""])[0]
    self.enforce_rate_limit(
        "login",
        self.rate_limit_key("login", username.lower()),
        "Too many sign-in attempts. Try again shortly.",
    )
    found = get_user_by_username(username)
    if not found or not verify_password(password, found["password_hash"]):
        return self.html(render_login(notice="That username and password did not match.", status="error"), HTTPStatus.UNAUTHORIZED)
    if found["is_banned"]:
        return self.html(render_login(notice="This account has been banned. Contact an administrator.", status="error"), HTTPStatus.FORBIDDEN)
    if two_factor_enabled(found):
        challenge_token, _ = create_two_factor_challenge(found["id"])
        return self.html(render_two_factor_login(challenge_token))
    token, expires_at = create_session(found["id"])
    self.redirect_with_session("/", token, expires_at)

def login_two_factor(self, method, user):
    if user:
        return self.redirect("/")
    if method != "POST":
        return self.redirect("/login")
    form = self.read_form()
    challenge_token = form.get("challenge_token", [""])[0]
    try:
        verified_user, method_used = complete_two_factor_login(challenge_token, form.get("two_factor_code", [""])[0])
    except ValueError as exc:
        return self.html(render_two_factor_login(challenge_token, notice=str(exc), status="error"), HTTPStatus.UNAUTHORIZED)
    token, expires_at = create_session(verified_user["id"])
    self.redirect_with_session("/", token, expires_at)

def passkey_request_context(self):
    base_url = self.public_base_url()
    parsed = urlparse(base_url)
    scheme = parsed.scheme or "http"
    netloc = parsed.netloc or f"{HOST}:{PORT}"
    origin = f"{scheme}://{netloc}"
    rp_id = passkey_clean_rp_id(parsed.hostname or netloc)
    return rp_id, origin

def passkey_json(self, payload, status=HTTPStatus.OK, session=None):
    data = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    self.send_response(status)
    self.send_header("Content-Type", "application/json; charset=utf-8")
    self.send_header("Content-Length", str(len(data)))
    if session:
        token, expires_at = session
        self.send_header(
            "Set-Cookie",
            f"{SESSION_COOKIE}={token}; Max-Age={SESSION_TTL_SECONDS}; Path=/; HttpOnly; SameSite=Lax",
        )
    self.send_security_headers()
    self.end_headers()
    self.wfile.write(data)

def login_passkey_options(self, method, user, query):
    if user:
        return self.passkey_json({"error": "You are already signed in."}, HTTPStatus.BAD_REQUEST)
    if method != "GET":
        return self.passkey_json({"error": "Passkey options must be requested with GET."}, HTTPStatus.METHOD_NOT_ALLOWED)
    username = (query or {}).get("username", [""])[0].strip()
    self.enforce_rate_limit(
        "login",
        self.rate_limit_key("passkey-login", username.lower()),
        "Too many passkey sign-in attempts. Try again shortly.",
    )
    try:
        rp_id, origin = passkey_request_context(self)
        options = passkey_authentication_options(username, rp_id, origin)
    except ValueError as exc:
        return self.passkey_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
    return self.passkey_json(options)

def login_passkey_complete(self, method, user):
    if user:
        return self.passkey_json({"error": "You are already signed in."}, HTTPStatus.BAD_REQUEST)
    if method != "POST":
        return self.passkey_json({"error": "Passkey sign-in must be submitted with POST."}, HTTPStatus.METHOD_NOT_ALLOWED)
    form = self.read_form()
    try:
        verified_user, credential = complete_passkey_authentication(
            form.get("token", [""])[0],
            form.get("credential", [""])[0],
        )
    except ValueError as exc:
        return self.passkey_json({"error": str(exc)}, HTTPStatus.UNAUTHORIZED)
    token, expires_at = create_session(verified_user["id"])
    return self.passkey_json({"ok": True, "redirect": "/"}, session=(token, expires_at))

def register(self, method, user, query=None):
    if user:
        return self.redirect("/")
    query = query or {}
    invite_required = registration_requires_invite()
    if method == "GET":
        invite_token = query.get("invite", [""])[0]
        invite = registration_invite_from_token(invite_token) if invite_token else None
        notice = None
        status = "info"
        if invite_token and not invite:
            notice = "That invite link is invalid, expired, or already used."
            status = "error"
        return self.html(render_register(invite_token=invite_token, invite=invite, invite_required=invite_required, notice=notice, status=status))
    form = self.read_form()
    self.enforce_rate_limit(
        "register",
        self.rate_limit_key("register"),
        "Too many registration attempts. Try again later.",
    )
    invite_token = form.get("invite_token", [""])[0]
    invite = registration_invite_from_token(invite_token) if invite_token else None
    if invite_required and not invite:
        return self.html(
            render_register(invite_token=invite_token, invite_required=invite_required, notice="Registration is invite-only. Use a valid invite link.", status="error"),
            HTTPStatus.BAD_REQUEST,
        )
    if invite_token and not invite:
        return self.html(
            render_register(invite_token=invite_token, invite_required=invite_required, notice="That invite link is invalid, expired, or already used.", status="error"),
            HTTPStatus.BAD_REQUEST,
        )
    display_name = form.get("display_name", [""])[0].strip()
    username = form.get("username", [""])[0].strip()
    password = form.get("password", [""])[0]
    email = invite["email"] if invite else form.get("email", [""])[0]
    try:
        username = validate_username(username)
        email = validate_email(email)
    except ValueError as exc:
        return self.html(render_register(invite_token=invite_token, invite=invite, invite_required=invite_required, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    if len(password) < 8:
        return self.html(render_register(invite_token=invite_token, invite=invite, invite_required=invite_required, notice="Password must be at least 8 characters.", status="error"), HTTPStatus.BAD_REQUEST)
    try:
        user_id = create_user(username, password, display_name or username, email=email)
        if invite:
            accept_registration_invite(invite_token, user_id)
    except sqlite3.IntegrityError:
        return self.html(render_register(invite_token=invite_token, invite=invite, invite_required=invite_required, notice="That username is already taken.", status="error"), HTTPStatus.CONFLICT)
    except ValueError as exc:
        return self.html(render_register(invite_token=invite_token, invite=invite, invite_required=invite_required, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    token, expires_at = create_session(user_id)
    self.redirect_with_session("/", token, expires_at)

def redirect_with_session(self, location, token, expires_at):
    self.send_response(HTTPStatus.SEE_OTHER)
    self.send_header("Location", safe_local_redirect_path(location, default="/"))
    self.send_header(
        "Set-Cookie",
        f"{SESSION_COOKIE}={token}; Max-Age={SESSION_TTL_SECONDS}; Path=/; HttpOnly; SameSite=Lax",
    )
    self.send_security_headers()
    self.end_headers()

def logout(self):
    cookie = SimpleCookie(self.headers.get("Cookie"))
    token = cookie.get(SESSION_COOKIE)
    delete_session(token.value if token else None)
    self.send_response(HTTPStatus.SEE_OTHER)
    self.send_header("Location", "/login")
    self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax")
    self.send_security_headers()
    self.end_headers()

def public_base_url(self):
    configured = config_str("BINDERBRIDGE_PUBLIC_BASE_URL", "PUBLIC_BASE_URL", default="", section="server", key="public_base_url")
    if configured:
        return configured.rstrip("/")
    host = sanitize_text_input(self.headers.get("Host", ""), max_length=120).strip()
    if not re.match(r"^[A-Za-z0-9.\-:\[\]]+$", host):
        host = f"{HOST}:{PORT}"
    proto = sanitize_text_input(self.headers.get("X-Forwarded-Proto", ""), max_length=20).strip().lower()
    if proto not in ("http", "https"):
        proto = "http"
    return f"{proto}://{host}"

def account_profile(self, user):
    form = self.read_form()
    if not verify_password(form.get("current_password", [""])[0], user["password_hash"]):
        return self.html(render_account(user, notice="Current password is incorrect.", status="error"), HTTPStatus.BAD_REQUEST)
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
        return self.html(render_account(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
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
    return self.html(render_account(updated, notice=notice))

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
        return self.html(render_account(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    return self.html(render_account(updated, notice="Password changed. Other sessions were signed out."))

def account_two_factor_start(self, user):
    form = self.read_form()
    if not verify_password(form.get("current_password", [""])[0], user["password_hash"]):
        return self.html(render_account(user, notice="Current password is incorrect.", status="error"), HTTPStatus.BAD_REQUEST)
    start_user_totp_setup(user["id"])
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    return self.html(render_account(updated, notice="Two-factor setup started. Add the setup key to your authenticator app, then enter the code."))

def account_two_factor_enable(self, user):
    form = self.read_form()
    if not verify_password(form.get("current_password", [""])[0], user["password_hash"]):
        return self.html(render_account(user, notice="Current password is incorrect.", status="error"), HTTPStatus.BAD_REQUEST)
    try:
        recovery_codes = enable_user_totp(user["id"], form.get("two_factor_code", [""])[0])
    except ValueError as exc:
        updated = row("SELECT * FROM users WHERE id = ?", (user["id"],)) or user
        return self.html(render_account(updated, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    return self.html(render_account(updated, notice="Two-factor authentication enabled. Save your recovery codes now.", recovery_codes=recovery_codes))

def account_two_factor_disable(self, user):
    form = self.read_form()
    if not verify_password(form.get("current_password", [""])[0], user["password_hash"]):
        return self.html(render_account(user, notice="Current password is incorrect.", status="error"), HTTPStatus.BAD_REQUEST)
    disable_user_totp(user["id"])
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    return self.html(render_account(updated, notice="Two-factor authentication disabled."))

def account_two_factor_recovery_codes(self, user):
    form = self.read_form()
    if not verify_password(form.get("current_password", [""])[0], user["password_hash"]):
        return self.html(render_account(user, notice="Current password is incorrect.", status="error"), HTTPStatus.BAD_REQUEST)
    try:
        recovery_codes = regenerate_user_totp_recovery_codes(user["id"])
    except ValueError as exc:
        return self.html(render_account(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    return self.html(render_account(updated, notice="New recovery codes generated. Save them now.", recovery_codes=recovery_codes))

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
        return self.html(render_account(user, notice="Current password is incorrect.", status="error"), HTTPStatus.BAD_REQUEST)
    deleted = delete_passkey_credential(user["id"], credential_id)
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],)) or user
    notice = "Passkey removed." if deleted else "Passkey not found."
    return self.html(render_account(updated, notice=notice, status="info" if deleted else "warning"))

def account_export(self, user):
    filename, data = export_account_json(user["id"])
    return self.binary(data, "application/json; charset=utf-8", filename)

ACCOUNT_ROUTE_METHODS = (
    'login',
    'login_two_factor',
    'passkey_request_context',
    'passkey_json',
    'login_passkey_options',
    'login_passkey_complete',
    'register',
    'redirect_with_session',
    'logout',
    'public_base_url',
    'account_profile',
    'account_password',
    'account_two_factor_start',
    'account_two_factor_enable',
    'account_two_factor_disable',
    'account_two_factor_recovery_codes',
    'account_passkey_register_options',
    'account_passkey_register',
    'account_passkey_delete',
    'account_export',
)

__all__ = [
    "ACCOUNT_ROUTE_METHODS",
    *ACCOUNT_ROUTE_METHODS,
]
