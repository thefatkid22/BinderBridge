import base64
import csv
import hashlib
import hmac
import html
import io
import ipaddress
import json
import os
import re
import secrets
import smtplib
import sys
import sqlite3
import types
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import default as email_policy
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

from binderbridge.config import config_bool, config_int, config_str
from binderbridge.version import APP_NAME, APP_VERSION, DEFAULT_SOURCE_URL


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(config_str("BINDERBRIDGE_DATA", default=str(BASE_DIR / "data"), section="app", key="data_dir"))
DB_PATH = DATA_DIR / "binderbridge.sqlite3"
STATIC_DIR = BASE_DIR / "static"
HOST = config_str("BINDERBRIDGE_HOST", "HOST", default="127.0.0.1", section="server", key="host")
PORT = config_int("BINDERBRIDGE_PORT", "PORT", default=8000, section="server", key="port")
TRUST_PROXY_HEADERS = config_bool(
    "BINDERBRIDGE_TRUST_PROXY_HEADERS",
    "TRUST_PROXY_HEADERS",
    default=False,
    section="server",
    key="trust_proxy_headers",
)
SOURCE_URL = config_str("BINDERBRIDGE_SOURCE_URL", default=DEFAULT_SOURCE_URL, section="app", key="source_url")
SESSION_COOKIE = "binderbridge_session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 14
PBKDF2_ITERATIONS = 310_000
CSRF_FIELD_NAME = "_csrf_token"
CSRF_FORM_RE = re.compile(r"(<form\b(?=[^>]*\bmethod\s*=\s*['\"]?post['\"]?)[^>]*>)", re.IGNORECASE)
MAX_REQUEST_BODY_BYTES = max(64_000, config_int("BINDERBRIDGE_MAX_REQUEST_BODY_BYTES", "MAX_REQUEST_BODY_BYTES", default=15 * 1024 * 1024, section="limits", key="max_request_body_bytes"))
MAX_UPLOAD_BYTES = max(64_000, config_int("BINDERBRIDGE_MAX_UPLOAD_BYTES", "MAX_UPLOAD_BYTES", default=10 * 1024 * 1024, section="limits", key="max_upload_bytes"))
MAX_FORM_FIELDS = max(100, config_int("BINDERBRIDGE_MAX_FORM_FIELDS", "MAX_FORM_FIELDS", default=2000, section="limits", key="max_form_fields"))
MAX_FORM_VALUE_LENGTH = max(10_000, config_int("BINDERBRIDGE_MAX_FORM_VALUE_LENGTH", "MAX_FORM_VALUE_LENGTH", default=500_000, section="limits", key="max_form_value_length"))
MAX_CSV_ROWS = max(100, config_int("BINDERBRIDGE_MAX_CSV_ROWS", "MAX_CSV_ROWS", default=25000, section="limits", key="max_csv_rows"))
MAX_CARD_QUANTITY = max(1, config_int("BINDERBRIDGE_MAX_CARD_QUANTITY", "MAX_CARD_QUANTITY", default=100000, section="limits", key="max_card_quantity"))


_FEATURE_MODULES = []


def _feature_module_targets(module):
    return (module, *getattr(module, "__binderbridge_feature_modules__", ()))


def _feature_shared_globals(source_globals):
    return {
        name: value
        for name, value in source_globals.items()
        if not (name.startswith("__") and name.endswith("__"))
    }


def _sync_feature_module(module, source_globals):
    module.__dict__.update(_feature_shared_globals(source_globals))
    for target in getattr(module, "__binderbridge_feature_modules__", ()):
        target.__dict__.update(_feature_shared_globals(source_globals))
        target.__dict__.update(_feature_shared_globals(module.__dict__))


def _install_feature_module(module):
    _sync_feature_module(module, globals())
    for name in module.__all__:
        globals()[name] = getattr(module, name)
    _FEATURE_MODULES.append(module)


def _wire_feature_modules():
    for module in _FEATURE_MODULES:
        _sync_feature_module(module, globals())


class _AppModule(types.ModuleType):
    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        for module in self.__dict__.get("_FEATURE_MODULES", []):
            for target in _feature_module_targets(module):
                target.__dict__[name] = value


from binderbridge import roles as _roles
_install_feature_module(_roles)
from binderbridge import ui_helpers as _ui_helpers
_install_feature_module(_ui_helpers)
from binderbridge import rate_limits as _rate_limits
_install_feature_module(_rate_limits)
from binderbridge import privacy as _privacy
_install_feature_module(_privacy)
from binderbridge import accounts as _accounts
_install_feature_module(_accounts)
from binderbridge import registration_moderation as _registration_moderation
_install_feature_module(_registration_moderation)
from binderbridge import groups as _groups
_install_feature_module(_groups)
from binderbridge import maintenance as _maintenance
_install_feature_module(_maintenance)
from binderbridge import cleanup as _cleanup
_install_feature_module(_cleanup)
from binderbridge import collection_health as _collection_health
_install_feature_module(_collection_health)
from binderbridge import import_profiles as _import_profiles
_install_feature_module(_import_profiles)



from binderbridge import import_mapping as _import_mapping
_install_feature_module(_import_mapping)


class RequestTooLargeError(ValueError):
    pass


class CsrfError(ValueError):
    pass


class RateLimitError(ValueError):
    pass


def csrf_token_for_session(session_token):
    token = str(session_token or "").strip()
    if not token:
        return ""
    return hmac.new(token.encode("utf-8"), b"binderbridge-csrf-v1", hashlib.sha256).hexdigest()


def inject_csrf_tokens(html_body, session_token):
    csrf_token = csrf_token_for_session(session_token)
    if not csrf_token or "<form" not in html_body.lower():
        return html_body
    hidden = f'<input type="hidden" name="{CSRF_FIELD_NAME}" value="{csrf_token}">'
    return CSRF_FORM_RE.sub(lambda match: match.group(1) + hidden, html_body)


def csrf_form_valid(form, session_token):
    provided = ""
    values = form.get(CSRF_FIELD_NAME, []) if form else []
    if values:
        provided = str(values[0] or "")
    expected = csrf_token_for_session(session_token)
    return bool(expected and provided and hmac.compare_digest(provided, expected))


def request_content_length(headers):
    try:
        length = int(headers.get("Content-Length", "0") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid request body length.") from exc
    if length < 0:
        raise ValueError("Invalid request body length.")
    if length > MAX_REQUEST_BODY_BYTES:
        raise RequestTooLargeError("Request body is too large.")
    return length


def sanitize_text_input(value, max_length=MAX_FORM_VALUE_LENGTH):
    text = "" if value is None else str(value)
    cleaned = []
    for char in text.replace("\x00", ""):
        codepoint = ord(char)
        if codepoint < 32 and char not in ("\n", "\r", "\t"):
            continue
        if codepoint == 127:
            continue
        cleaned.append(char)
        if len(cleaned) >= max_length:
            break
    return "".join(cleaned)


def safe_log_text(value, encoding=None):
    text = "" if value is None else str(value)
    escaped = []
    for char in text:
        codepoint = ord(char)
        if codepoint < 32 or 127 <= codepoint <= 159:
            escaped.append(f"\\x{codepoint:02x}")
        else:
            escaped.append(char)
    clean_text = "".join(escaped)
    encoding = encoding or getattr(sys.stderr, "encoding", None) or "utf-8"
    try:
        return clean_text.encode(encoding, errors="backslashreplace").decode(encoding, errors="replace")
    except LookupError:
        return clean_text.encode("ascii", errors="backslashreplace").decode("ascii")


def write_log_message(message, stream=None):
    stream = stream or sys.stderr
    safe_message = safe_log_text(message, getattr(stream, "encoding", None))
    try:
        stream.write(f"{safe_message}\n")
        stream.flush()
    except (AttributeError, UnicodeEncodeError):
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            buffer.write(f"{safe_message}\n".encode("ascii", errors="backslashreplace"))
            buffer.flush()


def sanitize_form_values(form):
    sanitized = {}
    for key, values in dict(form or {}).items():
        clean_key = sanitize_text_input(key, max_length=200)
        if not clean_key:
            continue
        if not isinstance(values, (list, tuple)):
            values = [values]
        sanitized.setdefault(clean_key, []).extend(
            sanitize_text_input(value) for value in values
        )
    return sanitized


def safe_local_redirect_path(value, default="/", allowed_prefix=None):
    text = sanitize_text_input(value, max_length=2000).strip()
    if not text:
        return default
    parsed = urlparse(text)
    if parsed.scheme or parsed.netloc or not text.startswith("/") or text.startswith("//") or "\\" in text:
        return default
    if allowed_prefix and parsed.path != allowed_prefix and not parsed.path.startswith(f"{allowed_prefix}/"):
        return default
    return text


FLASH_NOTICE_PARAM = "_notice"
FLASH_NOTICE_STATUS_PARAM = "_notice_status"
FLASH_NOTICE_STATUSES = {"info", "success", "error", "warning"}


def query_without_notice_params(query):
    clean = {}
    for key, values in (query or {}).items():
        if key in (FLASH_NOTICE_PARAM, FLASH_NOTICE_STATUS_PARAM):
            continue
        clean[key] = values if isinstance(values, list) else [values]
    return clean


def clean_flash_notice(notice, status="success"):
    notice = sanitize_text_input(notice, max_length=400).strip()
    if not notice:
        return "", "info"
    status = sanitize_text_input(status, max_length=20).strip().lower()
    if status not in FLASH_NOTICE_STATUSES:
        status = "info"
    return notice, status


def set_session_flash(token, notice, status="success"):
    notice, status = clean_flash_notice(notice, status)
    if not token or not notice:
        return 0
    with db() as conn:
        cursor = conn.execute(
            "UPDATE sessions SET flash_notice = ?, flash_status = ? WHERE token = ?",
            (notice, status, token),
        )
        return cursor.rowcount


def consume_session_flash(token):
    if not token:
        return "", "info"
    with db() as conn:
        found = conn.execute(
            "SELECT flash_notice, flash_status FROM sessions WHERE token = ?",
            (token,),
        ).fetchone()
        if not found:
            return "", "info"
        notice, status = clean_flash_notice(found["flash_notice"], found["flash_status"])
        if notice:
            conn.execute(
                "UPDATE sessions SET flash_notice = '', flash_status = '' WHERE token = ?",
                (token,),
            )
        return notice, status


def count_phrase(count, singular, plural=None):
    count = int(count or 0)
    label = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {label}"


def safe_download_filename(filename, default="download"):
    text = sanitize_text_input(filename, max_length=180).strip()
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip(".-")
    return text or default


from binderbridge import deck_imports as _deck_imports
_install_feature_module(_deck_imports)

from binderbridge import scryfall_client as _scryfall_client
_install_feature_module(_scryfall_client)
from binderbridge import scryfall_jobs as _scryfall_jobs
_install_feature_module(_scryfall_jobs)
from binderbridge import background_jobs as _background_jobs
_install_feature_module(_background_jobs)


def render_scryfall_result_picker(
    scryfall_results,
    button_label="Use selected card",
    intent="use_scryfall",
    title="Scryfall matches",
    multiple=False,
):
    if not scryfall_results:
        return ""
    options = []
    for index, card in enumerate(scryfall_results):
        image = f'<img class="scryfall-result-image" src="{e(card["image_url"])}" alt="">' if card.get("image_url") else '<span class="scryfall-result-image placeholder"></span>'
        price = f' - ${e(card["price_usd"])}' if card.get("price_usd") else ""
        input_type = "checkbox" if multiple else "radio"
        input_name = "selected_scryfall_ids" if multiple else "selected_scryfall_id"
        checked_attr = "" if multiple else (" checked" if index == 0 else "")
        options.append(
            f"""
            <label class="scryfall-result-card">
                <input type="{input_type}" name="{input_name}" value="{e(card["scryfall_id"])}"{checked_attr} data-scryfall-option>
                {image}
                <span>
                    <strong>{e(card["card_name"])}</strong>
                    <small>{e(card["set_name"])} ({e(card["set_code"])}) #{e(card["collector_number"])}{price}</small>
                    <small>{e(card["type_line"] or card["rarity"])}</small>
                </span>
            </label>
            """
        )
    select_all = ""
    script = ""
    if multiple:
        select_all = """
        <label class="checkbox-line scryfall-select-all">
            <input type="checkbox" data-scryfall-select-all>
            Select all shown
        </label>
        """
        script = """
        <script>
            (function () {
                document.querySelectorAll("[data-scryfall-select-all]").forEach(function (toggle) {
                    var section = toggle.closest(".scryfall-results");
                    if (!section) return;
                    var options = Array.prototype.slice.call(section.querySelectorAll("[data-scryfall-option]"));
                    function syncToggle() {
                        toggle.checked = options.length > 0 && options.every(function (option) { return option.checked; });
                        toggle.indeterminate = options.some(function (option) { return option.checked; }) && !toggle.checked;
                    }
                    toggle.addEventListener("change", function () {
                        options.forEach(function (option) { option.checked = toggle.checked; });
                    });
                    options.forEach(function (option) {
                        option.addEventListener("change", syncToggle);
                    });
                    syncToggle();
                });
            })();
        </script>
        """
    return f"""
    <section class="scryfall-results span-2">
        <div class="panel-heading">
            <h2>{e(title)}</h2>
            <span class="muted">{len(scryfall_results)} shown</span>
        </div>
        {select_all}
        <div class="scryfall-result-grid">
            {''.join(options)}
        </div>
        <div class="form-actions">
            <button class="button primary" name="intent" value="{e(intent)}" type="submit">{e(button_label)}</button>
        </div>
    </section>
    {script}
    """


def render_scryfall_preview(item):
    if not (item.get("scryfall_id") or item.get("image_url") or item.get("type_line")):
        return ""
    image = f'<img class="lookup-preview-image" src="{e(item["image_url"])}" alt="">' if item.get("image_url") else '<span class="lookup-preview-image placeholder"></span>'
    scryfall_link = f'<a href="{e(item["scryfall_uri"])}" target="_blank" rel="noreferrer">Open on Scryfall</a>' if item.get("scryfall_uri") else ""
    return f"""
    <div class="lookup-preview span-2">
        {image}
        <div>
            <strong>{e(item["card_name"])}</strong>
            <span>{e(item.get("type_line") or "Scryfall match loaded")}</span>
            <span>{e(item.get("set_code") or "Set")} {e("#" + item["collector_number"] if item.get("collector_number") else "")} {e(item.get("rarity", ""))}</span>
            {scryfall_link}
        </div>
    </div>
    """

from binderbridge import collection_service as _collection_service
_install_feature_module(_collection_service)
from binderbridge import api as _api
_install_feature_module(_api)

from binderbridge import import_batches as _import_batches
_install_feature_module(_import_batches)
from binderbridge import collection_imports as _collection_imports
_install_feature_module(_collection_imports)
from binderbridge import deck_import_service as _deck_import_service
_install_feature_module(_deck_import_service)

from binderbridge import job_runner as _job_runner
_install_feature_module(_job_runner)
from binderbridge import saved_searches as _saved_searches
_install_feature_module(_saved_searches)


from binderbridge import views as _views
_install_feature_module(_views)
from binderbridge import exports as _exports
_install_feature_module(_exports)

# Additional trade service functions live in binderbridge.trade_service.

class App(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}/{APP_VERSION}"

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def dispatch(self, method):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        user = None
        self._csrf_required = False
        self._request_path = path
        self._request_method = method
        try:
            query = sanitize_form_values(parse_qs(parsed.query, keep_blank_values=True, max_num_fields=MAX_FORM_FIELDS))
            user = self.current_user()
            self._csrf_required = bool(user and method == "POST" and not path.startswith("/api/"))
            if path.startswith("/static/"):
                return self.static_file(path)
            if path.startswith("/api/"):
                return self.api_dispatch(method, path, query)
            if path == "/login":
                return self.login(method, user)
            if path == "/login/2fa":
                return self.login_two_factor(method, user)
            if path == "/login/passkey/options":
                return self.login_passkey_options(method, user, query)
            if path == "/login/passkey":
                return self.login_passkey_complete(method, user)
            if path == "/register":
                return self.register(method, user, query)
            if path == "/password/forgot":
                return self.password_recovery(method, user)
            if path == "/password/reset":
                return self.password_reset(method, user, query)
            if path.startswith("/share/") and method == "GET":
                return self.shared_group_page(path)
            if path == "/logout" and method == "POST":
                if self._csrf_required:
                    self.parse_request_body()
                return self.logout()
            if not user:
                return self.redirect("/login")
            if method in ("POST", "PUT", "PATCH", "DELETE") and not user_can_mutate_path(user, path):
                content = """
                <section class="panel centered-state">
                    <h1>Read-only account</h1>
                    <p class="muted">Your account can browse BinderBridge, but it cannot change collections, wishlists, groups, trades, or integrations.</p>
                    <a class="button primary" href="/browse">Browse cards</a>
                </section>
                """
                return self.html(render_layout(user, "Read-only account", content), HTTPStatus.FORBIDDEN)
            if self._csrf_required:
                self.parse_request_body()
            if path == "/":
                return self.html(render_dashboard(user))
            if path == "/account":
                return self.html(render_account(user))
            if path == "/account/export" and method == "GET":
                return self.account_export(user)
            if path == "/account/profile" and method == "POST":
                return self.account_profile(user)
            if path == "/account/password" and method == "POST":
                return self.account_password(user)
            if path == "/account/2fa/start" and method == "POST":
                return self.account_two_factor_start(user)
            if path == "/account/2fa/enable" and method == "POST":
                return self.account_two_factor_enable(user)
            if path == "/account/2fa/disable" and method == "POST":
                return self.account_two_factor_disable(user)
            if path == "/account/2fa/recovery-codes" and method == "POST":
                return self.account_two_factor_recovery_codes(user)
            if path == "/account/passkeys/register/options" and method == "GET":
                return self.account_passkey_register_options(user)
            if path == "/account/passkeys/register" and method == "POST":
                return self.account_passkey_register(user)
            if path.startswith("/account/passkeys/") and path.endswith("/delete") and method == "POST":
                return self.account_passkey_delete(user, path)
            if path == "/account/api-tokens" and method == "POST":
                return self.account_api_token_create(user)
            if path.startswith("/account/api-tokens/") and path.endswith("/revoke") and method == "POST":
                return self.account_api_token_revoke(user, path)
            if path.startswith("/account/api-tokens/") and path.endswith("/delete") and method == "POST":
                return self.account_api_token_delete(user, path)
            if path == "/account/webhooks" and method == "POST":
                return self.account_webhook_create(user)
            if path.startswith("/account/webhooks/") and path.endswith("/delete") and method == "POST":
                return self.account_webhook_delete(user, path)
            if path.startswith("/account/webhooks/") and path.endswith("/test") and method == "POST":
                return self.account_webhook_test(user, path)
            if path == "/saved-searches" and method == "POST":
                return self.saved_search_create(user)
            if path.startswith("/saved-searches/") and path.endswith("/delete") and method == "POST":
                return self.saved_search_delete(user, path)
            if path == "/cleanup":
                return self.cleanup_page(user)
            if path == "/cleanup/collection" and method == "POST":
                return self.cleanup_collection(user)
            if path == "/cleanup/wants" and method == "POST":
                return self.cleanup_wants(user)
            if path == "/cleanup/audit":
                page_query = query_without_notice_params(query)
                notice, notice_status = self.consume_flash_notice()
                return self.condition_finish_audit_page(user, page_query, notice=notice, status=notice_status)
            if path == "/cleanup/audit/update" and method == "POST":
                return self.condition_finish_audit_update(user)
            if path == "/cleanup/audit/update-all" and method == "POST":
                return self.condition_finish_audit_update_all(user)
            if path == "/cleanup/audit/normalize" and method == "POST":
                return self.condition_finish_audit_normalize(user)
            if path == "/cleanup/audit/normalize-all" and method == "POST":
                return self.condition_finish_audit_normalize_all(user)
            if path == "/cleanup/audit/scryfall" and method == "POST":
                return self.condition_finish_audit_scryfall(user)
            if path == "/cleanup/audit/scryfall-all" and method == "POST":
                return self.condition_finish_audit_scryfall_all(user)
            if path == "/cleanup/audit/scryfall-delete" and method == "POST":
                return self.condition_finish_audit_scryfall_delete(user)
            if path == "/cleanup/audit/wishlist-scryfall" and method == "POST":
                return self.condition_finish_audit_want_scryfall(user)
            if path == "/cleanup/audit/wishlist-scryfall-all" and method == "POST":
                return self.condition_finish_audit_want_scryfall_all(user)
            if path == "/cleanup/audit/wishlist-scryfall-delete" and method == "POST":
                return self.condition_finish_audit_want_scryfall_delete(user)
            if path == "/admin":
                return self.admin_page(user)
            if path == "/admin/setup":
                return self.admin_setup_page(user)
            if path == "/admin/setup/public-url" and method == "POST":
                return self.admin_setup_public_url(user)
            if path == "/admin/setup/registration" and method == "POST":
                return self.admin_setup_registration(user)
            if path == "/admin/setup/backup" and method == "POST":
                return self.admin_setup_backup(user)
            if path == "/admin/setup/scryfall" and method == "POST":
                return self.admin_setup_scryfall_sync(user)
            if path == "/admin/setup/complete" and method == "POST":
                return self.admin_setup_complete(user)
            if path == "/admin/health":
                return self.admin_health_page(user)
            if path == "/admin/collection-health":
                return self.admin_collection_health_page(user)
            if path == "/admin/database":
                return self.admin_database_page(user)
            if path == "/admin/database/analyze" and method == "POST":
                return self.admin_database_analyze(user)
            if path == "/admin/database/vacuum" and method == "POST":
                return self.admin_database_vacuum(user)
            if path == "/admin/database/snapshot" and method == "POST":
                return self.admin_database_snapshot(user)
            if path == "/admin/health/jobs/retry" and method == "POST":
                return self.admin_health_retry_jobs(user)
            if path == "/admin/health/notifications/replay" and method == "POST":
                return self.admin_health_replay_notifications(user)
            if path == "/admin/health/backups/check" and method == "POST":
                return self.admin_health_check_backups(user)
            if path == "/admin/health/scryfall/sync" and method == "POST":
                return self.admin_health_scryfall_sync(user)
            if path == "/admin/health/retention" and method == "POST":
                return self.admin_health_retention(user)
            if path == "/admin/jobs":
                return self.admin_jobs_page(user)
            if path == "/admin/logs":
                return self.admin_logs_page(user, query)
            if path == "/admin/disputes":
                return self.admin_disputes_page(user, query)
            if path.startswith("/admin/disputes/") and path.endswith("/update") and method == "POST":
                return self.admin_dispute_update(user, path)
            if path == "/admin/jobs/scryfall/retry" and method == "POST":
                return self.admin_job_retry_scryfall(user)
            if path == "/admin/jobs/prices/retry" and method == "POST":
                return self.admin_job_retry_price(user)
            if path == "/admin/jobs/scryfall-prices/retry" and method == "POST":
                return self.admin_job_retry_scryfall_prices(user)
            if path == "/admin/jobs/notifications/retry" and method == "POST":
                return self.admin_job_retry_notification(user)
            if path == "/admin/jobs/background/retry" and method == "POST":
                return self.admin_job_retry_background(user)
            if path == "/admin/jobs/background/cancel" and method == "POST":
                return self.admin_job_cancel_background(user)
            if path.startswith("/admin/jobs/imports/") and path.endswith("/undo") and method == "POST":
                return self.admin_job_undo_import(user, path)
            if path == "/admin/trade-policy" and method == "POST":
                return self.admin_trade_policy_settings(user)
            if path == "/admin/integration-policy" and method == "POST":
                return self.admin_integration_policy_settings(user)
            if path == "/admin/trust-settings" and method == "POST":
                return self.admin_trust_settings(user)
            if path == "/admin/trade-fairness" and method == "POST":
                return self.admin_trade_fairness_settings(user)
            if path == "/admin/registration-settings" and method == "POST":
                return self.admin_registration_settings(user)
            if path.startswith("/admin/registration-review/") and method == "POST":
                return self.admin_registration_review(user, path)
            if path == "/admin/invites" and method == "POST":
                return self.admin_invite_create(user)
            if path.startswith("/admin/invites/") and path.endswith("/revoke") and method == "POST":
                return self.admin_invite_revoke(user, path)
            if path.startswith("/admin/invites/") and path.endswith("/delete") and method == "POST":
                return self.admin_invite_delete(user, path)
            if path == "/admin/backups/create" and method == "POST":
                return self.admin_backup_create(user)
            if path == "/admin/backups/settings" and method == "POST":
                return self.admin_backup_settings(user)
            if path == "/admin/backups/run" and method == "POST":
                return self.admin_backup_run(user)
            if path == "/admin/backups/restore" and method == "POST":
                return self.admin_backup_restore(user)
            if path.startswith("/admin/user/") and method == "POST":
                return self.admin_user_action(user, path)
            if path == "/collection":
                page_query = query_without_notice_params(query)
                notice, notice_status = self.consume_flash_notice()
                return self.html(render_collection(user, page_query, notice=notice, status=notice_status))
            if path == "/collection/stats":
                return self.html(render_collection_statistics(user))
            if path == "/collection/export" and method == "GET":
                return self.collection_export(user, query)
            if path == "/collection/bulk-update" and method == "POST":
                return self.collection_bulk_update(user)
            if path == "/collection/update-all" and method == "POST":
                return self.collection_update_all(user)
            if path == "/collection/bulk-delete" and method == "POST":
                return self.collection_bulk_delete(user)
            if path == "/collection/delete-all" and method == "POST":
                return self.collection_delete_all(user)
            if path == "/collection/bulk-group" and method == "POST":
                return self.collection_bulk_group(user)
            if path == "/collection/group-all" and method == "POST":
                return self.collection_group_all(user)
            if path == "/collection/new":
                return self.collection_new(method, user)
            if path.startswith("/collection/photos/") and method == "GET":
                return self.collection_photo(user, path)
            if path.startswith("/collection/"):
                return self.collection_item(method, user, path)
            if path == "/import/scryfall-sync" and method == "POST":
                return self.import_scryfall_sync(user)
            if path == "/prices/refresh" and method == "POST":
                return self.prices_refresh(user)
            if path.startswith("/imports/") and path.endswith("/undo") and method == "POST":
                return self.import_undo(user, path)
            if path == "/import/presets" and method == "POST":
                return self.csv_import_mapping_preset_create(user)
            if path.startswith("/import/presets/") and path.endswith("/delete") and method == "POST":
                return self.csv_import_mapping_preset_delete(user, path)
            if path == "/import":
                return self.collection_import(method, user)
            if path == "/wants":
                page_query = query_without_notice_params(query)
                notice, notice_status = self.consume_flash_notice()
                return self.html(render_wants(user, query=page_query, notice=notice, status=notice_status))
            if path == "/wants/export" and method == "GET":
                return self.wants_export(user)
            if path == "/wants/bulk-update" and method == "POST":
                return self.want_bulk_update(user)
            if path == "/wants/update-all" and method == "POST":
                return self.want_update_all(user)
            if path == "/wants/bulk-delete" and method == "POST":
                return self.want_bulk_delete(user)
            if path == "/wants/delete-all" and method == "POST":
                return self.want_delete_all(user)
            if path == "/wants/bulk-group" and method == "POST":
                return self.want_bulk_group(user)
            if path == "/wants/group-all" and method == "POST":
                return self.want_group_all(user)
            if path == "/wants/new":
                return self.want_new(user) if method == "POST" else self.redirect("/wants")
            if path.startswith("/wants/") and "/share-links" in path:
                return self.want_share_link(method, user, path)
            if path.startswith("/wants/") and path.endswith("/edit"):
                return self.want_edit(method, user, path)
            if path.startswith("/wants/") and path.endswith("/delete") and method == "POST":
                return self.want_delete(user, path)
            if path == "/groups":
                return self.groups_page(method, user, query)
            if path.startswith("/groups/"):
                return self.group_action(method, user, path, query)
            if path == "/browse":
                return self.html(render_browse(user, query))
            if path == "/members":
                return self.redirect("/browse")
            if path.startswith("/members/"):
                return self.member_detail(user, path, query)
            if path == "/notifications":
                page_query = query_without_notice_params(query)
                notice, notice_status = self.consume_flash_notice()
                return self.html(render_notifications(user, query=page_query, notice=notice, status=notice_status))
            if path == "/notifications/read-all" and method == "POST":
                form = self.read_form()
                marked = unread_notification_count(user["id"])
                mark_all_notifications_read(user["id"])
                redirect_to = workspace_redirect_path("/notifications", form, ("notification-inbox", "notification-cleanup"), default="notification-inbox")
                self.flash_notice(f"Marked {count_phrase(marked, 'notification')} read.")
                return self.redirect(redirect_to)
            if path == "/notifications/delete-read" and method == "POST":
                form = self.read_form()
                deleted = delete_read_notifications(user["id"])
                redirect_to = workspace_redirect_path("/notifications", form, ("notification-inbox", "notification-cleanup"), default="notification-inbox")
                self.flash_notice(f"Deleted {count_phrase(deleted, 'read notification')}.")
                return self.redirect(redirect_to)
            if path == "/notifications/delete-all" and method == "POST":
                form = self.read_form()
                deleted = delete_all_notifications(user["id"])
                redirect_to = workspace_redirect_path("/notifications", form, ("notification-inbox", "notification-cleanup"), default="notification-cleanup")
                self.flash_notice(f"Deleted {count_phrase(deleted, 'notification')}.")
                return self.redirect(redirect_to)
            if path.startswith("/notifications/") and path.endswith("/read") and method == "POST":
                return self.notification_action(user, path)
            if path.startswith("/notifications/") and path.endswith("/delete") and method == "POST":
                return self.notification_action(user, path)
            if path == "/trades":
                return self.html(render_trades(user, query=query))
            if path == "/trades/matches":
                return self.html(render_trade_matchmaking(user, query))
            if path == "/trades/new":
                return self.trade_new(method, user, query)
            if path.startswith("/trades/"):
                return self.trade_action(method, user, path)
            return self.not_found(user)
        except RequestTooLargeError as exc:
            content = f"""
            <section class="panel centered-state">
                <h1>Request too large</h1>
                <p class="muted">{e(exc)}</p>
                <a class="button primary" href="/">Go home</a>
            </section>
            """
            return self.html(render_layout(user, "Request too large", content), HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        except CsrfError as exc:
            content = f"""
            <section class="panel centered-state">
                <h1>Security check failed</h1>
                <p class="muted">{e(exc)}</p>
                <a class="button primary" href="/">Go home</a>
            </section>
            """
            return self.html(render_layout(user, "Security check failed", content), HTTPStatus.FORBIDDEN)
        except RateLimitError as exc:
            content = f"""
            <section class="panel centered-state">
                <h1>Slow down a moment</h1>
                <p class="muted">{e(exc)}</p>
                <a class="button primary" href="/">Go home</a>
            </section>
            """
            return self.html(render_layout(user, "Rate limited", content), HTTPStatus.TOO_MANY_REQUESTS)
        except Exception as exc:
            return self.error_page(user, exc)

    def current_user(self):
        cookie = SimpleCookie(self.headers.get("Cookie"))
        token = cookie.get(SESSION_COOKIE)
        return get_user_by_session(token.value) if token else None

    def current_session_token(self):
        cookie = SimpleCookie(self.headers.get("Cookie"))
        token = cookie.get(SESSION_COOKIE)
        return token.value if token else None

    def flash_notice(self, notice, status="success"):
        return set_session_flash(self.current_session_token(), notice, status)

    def consume_flash_notice(self):
        return consume_session_flash(self.current_session_token())

    def client_ip(self):
        if TRUST_PROXY_HEADERS:
            forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
            if forwarded:
                return forwarded
        try:
            return self.client_address[0]
        except (AttributeError, TypeError, IndexError):
            return ""

    def rate_limit_key(self, bucket, extra=""):
        return f"{self.client_ip()}:{extra}" if extra else self.client_ip()

    def enforce_rate_limit(self, bucket, key=None, message="Too many requests. Try again shortly."):
        if not rate_limit_allowed(bucket, key if key is not None else self.rate_limit_key(bucket)):
            raise RateLimitError(message)

    def validate_csrf_form(self, form):
        if not getattr(self, "_csrf_required", False):
            return
        if not csrf_form_valid(form, self.current_session_token()):
            raise CsrfError("Refresh the page and try again.")
        form.pop(CSRF_FIELD_NAME, None)

    def parse_request_body(self):
        if getattr(self, "_body_parsed", False):
            return
        self._body_parsed = True
        self._cached_form = {}
        self._cached_files = {}
        content_type = self.headers.get("Content-Type", "")
        length = request_content_length(self.headers)
        if "multipart/form-data" in content_type:
            body = self.rfile.read(length)
            message = BytesParser(policy=email_policy).parsebytes(
                b"Content-Type: " + content_type.encode("utf-8") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
            )
            fields = {}
            files = {}
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if not name:
                    continue
                name = sanitize_text_input(name, max_length=200)
                if not name:
                    continue
                payload = part.get_payload(decode=True) or b""
                filename = part.get_filename()
                if filename:
                    if len(payload) > MAX_UPLOAD_BYTES:
                        raise RequestTooLargeError("Uploaded file is too large.")
                    files[name] = {
                        "filename": safe_download_filename(filename, default="upload"),
                        "content": payload,
                        "content_type": part.get_content_type(),
                    }
                else:
                    charset = part.get_content_charset() or "utf-8"
                    fields.setdefault(name, []).append(payload.decode(charset, errors="replace"))
            form = sanitize_form_values(fields)
            self._cached_files = files
        else:
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            form = sanitize_form_values(parse_qs(raw, keep_blank_values=True, max_num_fields=MAX_FORM_FIELDS))
        self.validate_csrf_form(form)
        self._cached_form = form

    def read_form(self):
        self.parse_request_body()
        return self._cached_form

    def read_multipart_form(self):
        self.parse_request_body()
        return self._cached_form, self._cached_files

    def html(self, body, status=HTTPStatus.OK, headers=None):
        body = inject_csrf_tokens(body, self.current_session_token())
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_security_headers()
        if headers:
            for key, value in headers:
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(encoded)

    def binary(self, data, content_type, filename, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{safe_download_filename(filename)}"')
        self.send_security_headers()
        self.end_headers()
        self.wfile.write(data)

    def inline_binary(self, data, content_type, filename, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'inline; filename="{safe_download_filename(filename)}"')
        self.send_security_headers()
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", safe_local_redirect_path(location, default="/"))
        self.send_security_headers()
        self.end_headers()

    def send_security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'; "
            "img-src 'self' data: https://cards.scryfall.io https://*.scryfall.io; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'",
        )

    def static_file(self, path):
        safe_name = unquote(path.removeprefix("/static/")).replace("/", os.sep).replace("\\", os.sep)
        file_path = (STATIC_DIR / safe_name).resolve()
        static_root = STATIC_DIR.resolve()
        try:
            file_path.relative_to(static_root)
        except ValueError:
            return self.send_error(HTTPStatus.NOT_FOUND)
        if not file_path.exists():
            return self.send_error(HTTPStatus.NOT_FOUND)
        content_type = "text/css" if file_path.suffix == ".css" else "text/plain"
        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_security_headers()
        self.end_headers()
        self.wfile.write(data)















































    def not_found(self, user=None):
        content = """
        <section class="panel centered-state">
            <h1>Not found</h1>
            <p class="muted">That page is not available.</p>
            <a class="button primary" href="/">Go home</a>
        </section>
        """
        self.html(render_layout(user, "Not found", content), HTTPStatus.NOT_FOUND)

    def error_page(self, user, exc):
        content = f"""
        <section class="panel centered-state">
            <h1>Something broke</h1>
            <p class="muted">{e(exc)}</p>
            <a class="button primary" href="/">Go home</a>
        </section>
        """
        self.html(render_layout(user, "Error", content, notice="The app hit an unexpected error.", status="error"), HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, format, *args):
        write_log_message(f"{self.address_string()} - {format % args}")

from binderbridge import account_routes as _account_routes
_install_feature_module(_account_routes)
for _account_route_name in _account_routes.ACCOUNT_ROUTE_METHODS:
    setattr(App, _account_route_name, globals()[_account_route_name])

from binderbridge import group_routes as _group_routes
_install_feature_module(_group_routes)
for _group_route_name in _group_routes.GROUP_ROUTE_METHODS:
    setattr(App, _group_route_name, globals()[_group_route_name])

from binderbridge import collection_routes as _collection_routes
_install_feature_module(_collection_routes)
for _collection_route_name in _collection_routes.COLLECTION_ROUTE_METHODS:
    setattr(App, _collection_route_name, globals()[_collection_route_name])

for _api_route_name in _api.API_ROUTE_METHODS:
    setattr(App, _api_route_name, globals()[_api_route_name])

from binderbridge import trade_service as _trade_service
_install_feature_module(_trade_service)
from binderbridge import saved_search_routes as _saved_search_routes
_install_feature_module(_saved_search_routes)
for _saved_search_route_name in _saved_search_routes.SAVED_SEARCH_ROUTE_METHODS:
    setattr(App, _saved_search_route_name, globals()[_saved_search_route_name])
from binderbridge import trade_routes as _trade_routes
_install_feature_module(_trade_routes)
for _trade_route_name in _trade_routes.TRADE_ROUTE_METHODS:
    setattr(App, _trade_route_name, globals()[_trade_route_name])

from binderbridge import admin_routes as _admin_routes
_install_feature_module(_admin_routes)
for _admin_route_name in _admin_routes.__all__:
    setattr(App, _admin_route_name, globals()[_admin_route_name])

from binderbridge import demo_data as _demo_data
_install_feature_module(_demo_data)


def main():
    init_db()
    seed_demo_data()
    start_background_job_runner()
    server = ThreadingHTTPServer((HOST, PORT), App)
    write_log_message(f"{APP_NAME} running at http://{HOST}:{PORT}", stream=sys.stdout)
    write_log_message(f"Database: {DB_PATH}", stream=sys.stdout)
    server.serve_forever()


_wire_feature_modules()
sys.modules[__name__].__class__ = _AppModule

if __name__ == "__main__":
    main()
