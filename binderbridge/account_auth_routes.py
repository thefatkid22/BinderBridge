"""Authentication, registration, and recovery HTTP route handlers."""

from http import HTTPStatus


def session_cookie_is_secure(self):
    if SESSION_COOKIE_SECURE:
        return True
    try:
        return urlparse(self.public_base_url()).scheme.lower() == "https"
    except (AttributeError, TypeError, ValueError):
        return False


def session_cookie_header(self, token="", max_age=None):
    cookie = SimpleCookie()
    cookie[SESSION_COOKIE] = str(token or "")
    morsel = cookie[SESSION_COOKIE]
    morsel["path"] = "/"
    morsel["httponly"] = True
    morsel["samesite"] = "Lax"
    morsel["max-age"] = str(
        SESSION_TTL_SECONDS if max_age is None else max(0, int(max_age))
    )
    if not token:
        morsel["expires"] = "Thu, 01 Jan 1970 00:00:00 GMT"
    if session_cookie_is_secure(self):
        morsel["secure"] = True
    return morsel.OutputString()


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
    if row_value(found, "registration_status", "active") == REGISTRATION_STATUS_PENDING:
        return self.html(render_login(notice="This account is waiting for administrator approval.", status="warning"), HTTPStatus.FORBIDDEN)
    if row_value(found, "registration_status", "active") == REGISTRATION_STATUS_DENIED:
        return self.html(render_login(notice="This account registration was not approved. Contact an administrator if this was unexpected.", status="error"), HTTPStatus.FORBIDDEN)
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
        self.send_header("Set-Cookie", self.session_cookie_header(token))
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
    user_count_before = row("SELECT COUNT(*) AS count FROM users")["count"]
    assessment = registration_risk_assessment(
        username,
        display_name or username,
        email,
        invite,
        self.client_ip(),
        self.headers.get("User-Agent", ""),
    )
    registration_status = registration_status_for_new_account(user_count_before, assessment["score"])
    try:
        user_id = create_user(
            username,
            password,
            display_name or username,
            email=email,
            registration_status=registration_status,
        )
        record_registration_attempt(
            user_id,
            username,
            display_name or username,
            email,
            invite,
            self.client_ip(),
            self.headers.get("User-Agent", ""),
            assessment,
            registration_status,
        )
        if invite:
            accept_registration_invite(invite_token, user_id)
    except sqlite3.IntegrityError:
        return self.html(render_register(invite_token=invite_token, invite=invite, invite_required=invite_required, notice="That username is already taken.", status="error"), HTTPStatus.CONFLICT)
    except ValueError as exc:
        return self.html(render_register(invite_token=invite_token, invite=invite, invite_required=invite_required, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    if registration_status == REGISTRATION_STATUS_PENDING:
        notify_registration_review_needed(user_id, assessment)
        return self.html(
            render_login(
                notice="Account created. An administrator needs to approve it before you can sign in.",
                status="info",
            )
        )
    token, expires_at = create_session(user_id)
    self.redirect_with_session("/", token, expires_at)

def password_recovery(self, method, user):
    if user:
        return self.redirect("/account")
    if method == "GET":
        return self.html(render_password_recovery())
    if method != "POST":
        return self.redirect("/login")
    form = self.read_form()
    identifier = form.get("identifier", [""])[0]
    self.enforce_rate_limit(
        "password_recovery",
        self.rate_limit_key("password-recovery"),
        "Too many recovery requests. Try again later.",
    )
    request_password_recovery(identifier, self.public_base_url())
    notice = (
        "If an account matches, a one-time password reset link has been emailed. "
        "If email delivery is unavailable, an administrator has been notified."
        if email_delivery_configured()
        else "If an account matches, an administrator has been notified and can provide a one-time password reset link."
    )
    return self.html(render_password_recovery(notice=notice))

def password_reset(self, method, user, query=None):
    query = query or {}
    token = query.get("token", [""])[0]
    if method == "GET":
        return self.html(render_password_reset(token, valid=bool(password_reset_from_token(token))))
    if method != "POST":
        return self.redirect("/login")
    form = self.read_form()
    token = form.get("token", [""])[0]
    self.enforce_rate_limit(
        "password_reset",
        self.rate_limit_key("password-reset"),
        "Too many password reset attempts. Try again shortly.",
    )
    try:
        complete_password_reset(
            token,
            form.get("new_password", [""])[0],
            form.get("confirm_password", [""])[0],
        )
    except ValueError as exc:
        return self.html(
            render_password_reset(token, valid=bool(password_reset_from_token(token)), notice=str(exc), status="error"),
            HTTPStatus.BAD_REQUEST,
        )
    return self.html(
        render_login(
            notice="Password reset complete. Sign in with your new password. Two-factor authentication remains enabled.",
        )
    )

def redirect_with_session(self, location, token, expires_at):
    self.send_response(HTTPStatus.SEE_OTHER)
    self.send_header("Location", safe_local_redirect_path(location, default="/"))
    self.send_header("Set-Cookie", self.session_cookie_header(token))
    self.send_security_headers()
    self.end_headers()

def logout(self):
    cookie = SimpleCookie(self.headers.get("Cookie"))
    token = cookie.get(SESSION_COOKIE)
    delete_session(token.value if token else None)
    self.send_response(HTTPStatus.SEE_OTHER)
    self.send_header("Location", "/login")
    self.send_header("Set-Cookie", self.session_cookie_header(max_age=0))
    self.send_security_headers()
    self.end_headers()

def public_base_url(self):
    configured = configured_public_base_url() if globals().get("configured_public_base_url") else config_str("BINDERBRIDGE_PUBLIC_BASE_URL", "PUBLIC_BASE_URL", default="", section="server", key="public_base_url")
    if configured:
        return configured.rstrip("/")
    host = sanitize_text_input(self.headers.get("Host", ""), max_length=120).strip()
    if not re.match(r"^[A-Za-z0-9.\-:\[\]]+$", host):
        host = f"{HOST}:{PORT}"
    proto = ""
    if TRUST_PROXY_HEADERS:
        proto = sanitize_text_input(self.headers.get("X-Forwarded-Proto", ""), max_length=20).strip().lower()
    if proto not in ("http", "https"):
        proto = "http"
    return f"{proto}://{host}"

ACCOUNT_AUTH_ROUTE_METHODS = (
    "session_cookie_is_secure",
    "session_cookie_header",
    "login",
    "login_two_factor",
    "passkey_request_context",
    "passkey_json",
    "login_passkey_options",
    "login_passkey_complete",
    "register",
    "password_recovery",
    "password_reset",
    "redirect_with_session",
    "logout",
    "public_base_url",
)

__all__ = ["ACCOUNT_AUTH_ROUTE_METHODS", *ACCOUNT_AUTH_ROUTE_METHODS]
