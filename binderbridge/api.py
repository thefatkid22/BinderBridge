"""API tokens and webhook integrations for BinderBridge."""

import hashlib
import hmac
import json
import secrets
import threading
import time
from http import HTTPStatus
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from binderbridge.collection_queries import browse_filter_users, trade_picker_count, trade_picker_rows, trade_picker_where
from binderbridge.collection_service import collection_item_photo_count
from binderbridge.config import config_bool, config_float, config_int
from binderbridge.formatting import TRADE_STATUS_LABELS
from binderbridge.groups import (
    add_collection_item_to_group,
    collection_group_item_count,
    collection_group_items,
    create_card_group,
    delete_card_group,
    group_summary_rows,
    group_type_label,
    normalize_group_type,
    remove_group_item,
    update_card_group,
    update_group_collection_item_quantities,
    user_group,
)
from binderbridge.privacy import can_view_collection_values
from binderbridge.trade_queries import (
    trade_comment_rows,
    trade_count_for_user,
    trade_detail_for_user,
    trade_item_photo_rows,
    trade_list_filter_values,
    trade_page_rows,
)
from binderbridge.trade_service import (
    add_trade_comment,
    cancel_trade_offer,
    complete_trade,
    create_trade_offer,
    parse_trade_quantities,
    update_trade_response,
)


API_TOKEN_PREFIX = "bbapi_"
API_TOKEN_SCOPES = ("read", "write")
API_PAGE_SIZE_MAX = max(1, config_int("BINDERBRIDGE_API_PAGE_SIZE_MAX", default=250, section="api", key="page_size_max"))
API_ACCESS_POLICY_KEY = "api_access_policy"
WEBHOOK_ACCESS_POLICY_KEY = "webhook_access_policy"
DEFAULT_INTEGRATION_ACCESS_POLICY = "all"
INTEGRATION_ACCESS_POLICY_OPTIONS = (
    ("all", "All active users"),
    ("trusted", "Trusted users and admins"),
    ("admins", "Admins only"),
    ("disabled", "Disabled"),
)
WEBHOOK_TIMEOUT_SECONDS = max(1.0, config_float("BINDERBRIDGE_WEBHOOK_TIMEOUT_SECONDS", default=5.0, section="webhooks", key="timeout_seconds"))
WEBHOOK_DELIVERY_INTERVAL_SECONDS = max(5.0, config_float("BINDERBRIDGE_WEBHOOK_DELIVERY_INTERVAL_SECONDS", default=30.0, section="webhooks", key="delivery_interval_seconds"))
WEBHOOK_DELIVERY_BATCH_SIZE = max(1, config_int("BINDERBRIDGE_WEBHOOK_DELIVERY_BATCH_SIZE", default=20, section="webhooks", key="delivery_batch_size"))
WEBHOOK_DELIVERY_WORKER_ENABLED = config_bool("BINDERBRIDGE_WEBHOOK_WORKER_ENABLED", default=True, section="webhooks", key="worker_enabled")
WEBHOOK_EVENT_OPTIONS = (
    ("notification.created", "All in-app notifications"),
    ("trade.offer", "Trade offers and counter offers"),
    ("trade.comment", "Trade comments"),
    ("trade.status", "Trade status changes"),
    ("trade.feedback", "Trade feedback received"),
    ("trade.dispute", "Trade issue updates"),
    ("watchlist.match", "Wishlist/watchlist matches"),
    ("price.updated", "Scheduled price refresh completion"),
    ("price.alert", "Price movement alerts"),
    ("import.completed", "Background import lookup completion"),
    ("backup.failed", "Automatic backup failures"),
)
WEBHOOK_EVENT_LABELS = dict(WEBHOOK_EVENT_OPTIONS)
NOTIFICATION_WEBHOOK_EVENTS = {
    "trade_offer": "trade.offer",
    "trade_counter": "trade.offer",
    "trade_comment": "trade.comment",
    "trade_status": "trade.status",
    "trade_feedback": "trade.feedback",
    "trade_dispute": "trade.dispute",
    "watchlist_alert": "watchlist.match",
    "price_refresh": "price.updated",
    "price_alert": "price.alert",
    "scryfall_import": "import.completed",
    "backup_failure": "backup.failed",
}

_webhook_worker_lock = threading.Lock()
_webhook_worker_started = False


def normalize_integration_access_policy(value):
    policy = str(value or "").strip().lower()
    allowed = {key for key, _label in INTEGRATION_ACCESS_POLICY_OPTIONS}
    if policy not in allowed:
        raise ValueError("Choose a valid integration access policy.")
    return policy


def integration_policy_label(policy):
    return dict(INTEGRATION_ACCESS_POLICY_OPTIONS).get(policy, dict(INTEGRATION_ACCESS_POLICY_OPTIONS)[DEFAULT_INTEGRATION_ACCESS_POLICY])


def api_access_policy():
    try:
        return normalize_integration_access_policy(get_setting(API_ACCESS_POLICY_KEY, DEFAULT_INTEGRATION_ACCESS_POLICY))
    except ValueError:
        return DEFAULT_INTEGRATION_ACCESS_POLICY


def webhook_access_policy():
    try:
        return normalize_integration_access_policy(get_setting(WEBHOOK_ACCESS_POLICY_KEY, DEFAULT_INTEGRATION_ACCESS_POLICY))
    except ValueError:
        return DEFAULT_INTEGRATION_ACCESS_POLICY


def integration_access_settings():
    api_policy = api_access_policy()
    webhook_policy = webhook_access_policy()
    return {
        "api_policy": api_policy,
        "api_policy_label": integration_policy_label(api_policy),
        "webhook_policy": webhook_policy,
        "webhook_policy_label": integration_policy_label(webhook_policy),
    }


def set_integration_access_settings(api_policy_value, webhook_policy_value):
    api_policy = normalize_integration_access_policy(api_policy_value)
    webhook_policy = normalize_integration_access_policy(webhook_policy_value)
    set_setting(API_ACCESS_POLICY_KEY, api_policy)
    set_setting(WEBHOOK_ACCESS_POLICY_KEY, webhook_policy)
    return integration_access_settings()


def user_matches_integration_policy(user, policy):
    if not user or row_value(user, "is_banned", 0) or row_value(user, "registration_status", "active") != "active":
        return False
    policy = normalize_integration_access_policy(policy)
    if policy == "disabled":
        return False
    if policy == "all":
        return True
    if policy == "admins":
        return bool(row_value(user, "is_admin", 0))
    return bool(row_value(user, "is_admin", 0)) or is_trusted_user(user)


def user_can_use_api(user):
    return user_matches_integration_policy(user, api_access_policy())


def user_can_use_webhooks(user):
    return user_can_write_content(user) and user_matches_integration_policy(user, webhook_access_policy())


def integration_access_error(feature):
    return f"{feature} access is not enabled for your account."


def api_token_hash(token):
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def normalize_api_token_scopes(values):
    if isinstance(values, str):
        values = values.split(",")
    scopes = []
    for value in values or []:
        scope = sanitize_text_input(value, max_length=40).strip().lower()
        if scope in API_TOKEN_SCOPES and scope not in scopes:
            scopes.append(scope)
    return scopes or ["read"]


def api_token_has_scope(token_row, scope):
    scopes = set(normalize_api_token_scopes(row_value(token_row, "scopes", "read")))
    return scope in scopes


def create_api_token(user_id, name, scopes=None, expires_at=""):
    user = row("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user_can_use_api(user):
        raise ValueError(integration_access_error("API"))
    clean_name = sanitize_text_input(name, max_length=80).strip() or "API token"
    clean_scopes = normalize_api_token_scopes(scopes or ["read"])
    if not user_can_write_content(user):
        clean_scopes = ["read"]
    expires_at = sanitize_text_input(expires_at, max_length=40).strip()
    token = API_TOKEN_PREFIX + secrets.token_urlsafe(32)
    timestamp = now_iso()
    token_id = execute(
        """
        INSERT INTO api_tokens
            (user_id, name, token_hash, token_hint, scopes, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            clean_name,
            api_token_hash(token),
            token[-8:],
            ",".join(clean_scopes),
            expires_at,
            timestamp,
        ),
    )
    return {"id": token_id, "token": token, "name": clean_name, "scopes": clean_scopes, "expires_at": expires_at}


def revoke_api_token(user_id, token_id):
    try:
        token_id = int(token_id)
    except (TypeError, ValueError):
        return 0
    with db() as conn:
        cursor = conn.execute(
            """
            UPDATE api_tokens
            SET revoked_at = ?
            WHERE id = ? AND user_id = ? AND revoked_at = ''
            """,
            (now_iso(), token_id, user_id),
        )
        return cursor.rowcount


def delete_revoked_api_token(user_id, token_id):
    try:
        token_id = int(token_id)
    except (TypeError, ValueError):
        return 0
    with db() as conn:
        cursor = conn.execute(
            "DELETE FROM api_tokens WHERE id = ? AND user_id = ? AND revoked_at != ''",
            (token_id, user_id),
        )
        return cursor.rowcount


def api_token_rows(user_id):
    return rows(
        """
        SELECT *
        FROM api_tokens
        WHERE user_id = ?
        ORDER BY revoked_at = '' DESC, created_at DESC, id DESC
        """,
        (user_id,),
    )


def get_user_by_api_token(token):
    token = str(token or "").strip()
    if not token.startswith(API_TOKEN_PREFIX):
        return None, None
    found = row(
        """
        SELECT api_tokens.*, users.username, users.display_name, users.email, users.role,
            users.registration_status, users.is_admin, users.is_banned
        FROM api_tokens
        JOIN users ON users.id = api_tokens.user_id
        WHERE api_tokens.token_hash = ?
        """,
        (api_token_hash(token),),
    )
    if (
        not found
        or row_value(found, "revoked_at", "")
        or int(row_value(found, "is_banned", 0) or 0)
        or row_value(found, "registration_status", "active") != "active"
    ):
        return None, None
    expires_at = row_value(found, "expires_at", "")
    if expires_at and expires_at <= now_iso():
        return None, None
    user = row("SELECT * FROM users WHERE id = ?", (found["user_id"],))
    if not user_can_use_api(user):
        return None, None
    execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (now_iso(), found["id"]))
    return user, found


def normalize_webhook_events(values):
    if isinstance(values, str):
        values = values.split(",")
    valid = {event for event, _label in WEBHOOK_EVENT_OPTIONS}
    events = []
    for value in values or []:
        event = sanitize_text_input(value, max_length=80).strip()
        if event == "all":
            return ["all"]
        if event in valid and event not in events:
            events.append(event)
    return events or ["notification.created"]


def validate_webhook_url(url):
    clean_url = sanitize_text_input(url, max_length=500).strip()
    parsed = urlparse(clean_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Webhook URL must be an http or https URL.")
    if parsed.username or parsed.password:
        raise ValueError("Webhook URL cannot include embedded credentials.")
    return clean_url


def create_webhook_endpoint(user_id, name, url, event_types=None, secret=""):
    user = row("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user_can_use_webhooks(user):
        raise ValueError(integration_access_error("Webhook"))
    clean_name = sanitize_text_input(name, max_length=80).strip() or "Webhook"
    clean_url = validate_webhook_url(url)
    clean_events = normalize_webhook_events(event_types)
    clean_secret = sanitize_text_input(secret, max_length=160).strip() or secrets.token_urlsafe(32)
    timestamp = now_iso()
    webhook_id = execute(
        """
        INSERT INTO webhook_endpoints
            (user_id, name, url, secret, event_types, is_active, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (user_id, clean_name, clean_url, clean_secret, ",".join(clean_events), timestamp, timestamp),
    )
    return {"id": webhook_id, "name": clean_name, "url": clean_url, "secret": clean_secret, "event_types": clean_events}


def delete_webhook_endpoint(user_id, webhook_id):
    try:
        webhook_id = int(webhook_id)
    except (TypeError, ValueError):
        return 0
    with db() as conn:
        cursor = conn.execute("DELETE FROM webhook_endpoints WHERE id = ? AND user_id = ?", (webhook_id, user_id))
        return cursor.rowcount


def webhook_endpoint_rows(user_id):
    return rows(
        """
        SELECT *
        FROM webhook_endpoints
        WHERE user_id = ?
        ORDER BY is_active DESC, created_at DESC, id DESC
        """,
        (user_id,),
    )


def webhook_events_match(event_types, event_type):
    configured = set(normalize_webhook_events(event_types))
    return "all" in configured or event_type in configured


def webhook_payload(user_id, event_type, payload):
    return {
        "version": "2026-05-29",
        "event": event_type,
        "event_id": secrets.token_urlsafe(18),
        "user_id": int(user_id),
        "created_at": now_iso(),
        "data": payload or {},
    }


def queue_user_webhook_event(user_id, event_type, payload=None, conn=None):
    clean_event = sanitize_text_input(event_type, max_length=80).strip()
    if clean_event not in WEBHOOK_EVENT_LABELS:
        return 0
    user = row("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user_can_use_webhooks(user):
        return 0
    body = webhook_payload(user_id, clean_event, payload or {})
    payload_json = json.dumps(body, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    timestamp = now_iso()

    def run(active_conn):
        endpoints = active_conn.execute(
            """
            SELECT *
            FROM webhook_endpoints
            WHERE user_id = ? AND is_active = 1
            ORDER BY id
            """,
            (user_id,),
        ).fetchall()
        queued = 0
        for endpoint in endpoints:
            if not webhook_events_match(row_value(endpoint, "event_types", ""), clean_event):
                continue
            active_conn.execute(
                """
                INSERT INTO webhook_deliveries
                    (webhook_id, user_id, event_type, payload_json, status, created_at)
                VALUES (?, ?, ?, ?, 'pending', ?)
                """,
                (endpoint["id"], user_id, clean_event, payload_json, timestamp),
            )
            queued += 1
        return queued

    if conn is not None:
        queued = run(conn)
        if queued:
            start_webhook_delivery_worker(conn=conn)
        return queued
    with db() as active_conn:
        queued = run(active_conn)
    if queued:
        start_webhook_delivery_worker()
    return queued


def queue_notification_webhooks(user_id, notification_id, kind, title, body="", url="", related_trade_id=None, conn=None):
    notification = {
        "id": int(notification_id),
        "kind": sanitize_text_input(kind, max_length=60).strip(),
        "title": sanitize_text_input(title, max_length=160).strip(),
        "body": sanitize_text_input(body, max_length=800).strip(),
        "url": safe_local_redirect_path(url, default="") if url else "",
        "related_trade_id": related_trade_id,
    }
    queued = queue_user_webhook_event(user_id, "notification.created", {"notification": notification}, conn=conn)
    specific_event = NOTIFICATION_WEBHOOK_EVENTS.get(notification["kind"])
    if specific_event:
        queued += queue_user_webhook_event(user_id, specific_event, {"notification": notification}, conn=conn)
    return queued


def send_webhook_http_request(endpoint, delivery):
    payload = row_value(delivery, "payload_json", "{}").encode("utf-8")
    signature = hmac.new(str(row_value(endpoint, "secret", "")).encode("utf-8"), payload, hashlib.sha256).hexdigest()
    request = Request(
        row_value(endpoint, "url", ""),
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": f"{APP_NAME}/webhook",
            "X-BinderBridge-Event": row_value(delivery, "event_type", ""),
            "X-BinderBridge-Delivery": str(row_value(delivery, "id", "")),
            "X-BinderBridge-Signature": f"sha256={signature}",
        },
        method="POST",
    )
    with urlopen(request, timeout=WEBHOOK_TIMEOUT_SECONDS) as response:
        response_body = response.read(500).decode("utf-8", errors="replace")
        return response.status, response_body


def mark_webhook_delivery_result(conn, endpoint_id, delivery_id, ok, http_status=0, response_body="", error=""):
    timestamp = now_iso()
    status = "sent" if ok else "failed"
    conn.execute(
        """
        UPDATE webhook_deliveries
        SET status = ?, http_status = ?, response_body = ?, error = ?, attempts = attempts + 1, completed_at = ?
        WHERE id = ?
        """,
        (
            status,
            int(http_status or 0),
            sanitize_text_input(response_body, max_length=500).strip(),
            sanitize_text_input(error, max_length=500).strip(),
            timestamp,
            delivery_id,
        ),
    )
    if ok:
        conn.execute(
            "UPDATE webhook_endpoints SET last_success_at = ?, last_error = '', updated_at = ? WHERE id = ?",
            (timestamp, timestamp, endpoint_id),
        )
    else:
        conn.execute(
            "UPDATE webhook_endpoints SET last_failure_at = ?, last_error = ?, updated_at = ? WHERE id = ?",
            (timestamp, sanitize_text_input(error or response_body, max_length=500).strip(), timestamp, endpoint_id),
        )


def send_pending_webhook_deliveries(user_id=None, limit=None):
    limit = int(limit or WEBHOOK_DELIVERY_BATCH_SIZE)
    sent = failed = 0
    with db() as conn:
        where = ["webhook_deliveries.status = 'pending'", "webhook_endpoints.is_active = 1"]
        params = []
        if user_id:
            where.append("webhook_deliveries.user_id = ?")
            params.append(int(user_id))
        params.append(limit)
        deliveries = conn.execute(
            f"""
            SELECT
                webhook_deliveries.*,
                webhook_endpoints.url,
                webhook_endpoints.secret,
                webhook_endpoints.is_active
            FROM webhook_deliveries
            JOIN webhook_endpoints ON webhook_endpoints.id = webhook_deliveries.webhook_id
            WHERE {' AND '.join(where)}
            ORDER BY webhook_deliveries.created_at ASC, webhook_deliveries.id ASC
            LIMIT ?
            """,
            params,
        ).fetchall()
        for delivery in deliveries:
            owner = conn.execute("SELECT * FROM users WHERE id = ?", (delivery["user_id"],)).fetchone()
            if not user_can_use_webhooks(owner):
                mark_webhook_delivery_result(
                    conn,
                    delivery["webhook_id"],
                    delivery["id"],
                    False,
                    error=integration_access_error("Webhook"),
                )
                failed += 1
                continue
            try:
                status, response_body = send_webhook_http_request(delivery, delivery)
                ok = 200 <= int(status) < 300
                mark_webhook_delivery_result(
                    conn,
                    delivery["webhook_id"],
                    delivery["id"],
                    ok,
                    http_status=status,
                    response_body=response_body,
                    error="" if ok else f"Webhook returned HTTP {status}",
                )
                if ok:
                    sent += 1
                else:
                    failed += 1
            except HTTPError as exc:
                response_body = ""
                try:
                    response_body = exc.read(500).decode("utf-8", errors="replace")
                except Exception:
                    response_body = ""
                mark_webhook_delivery_result(
                    conn,
                    delivery["webhook_id"],
                    delivery["id"],
                    False,
                    http_status=exc.code,
                    response_body=response_body,
                    error=f"Webhook returned HTTP {exc.code}",
                )
                failed += 1
            except (URLError, TimeoutError, OSError, ValueError) as exc:
                mark_webhook_delivery_result(
                    conn,
                    delivery["webhook_id"],
                    delivery["id"],
                    False,
                    error=str(exc),
                )
                failed += 1
    return {"sent": sent, "failed": failed}


def webhook_delivery_rows(user_id, limit=8):
    return rows(
        """
        SELECT webhook_deliveries.*, webhook_endpoints.name AS webhook_name
        FROM webhook_deliveries
        JOIN webhook_endpoints ON webhook_endpoints.id = webhook_deliveries.webhook_id
        WHERE webhook_deliveries.user_id = ?
        ORDER BY webhook_deliveries.created_at DESC, webhook_deliveries.id DESC
        LIMIT ?
        """,
        (user_id, int(limit)),
    )


def start_webhook_delivery_worker(conn=None):
    if not WEBHOOK_DELIVERY_WORKER_ENABLED:
        return False
    enqueue = globals().get("enqueue_background_job")
    if enqueue:
        _job_id, created = enqueue(
            "webhook_delivery",
            unique_key="system:webhook-delivery",
            max_attempts=10,
            conn=conn,
        )
        expedite = globals().get("expedite_background_job")
        if expedite:
            expedite("system:webhook-delivery", conn=conn)
        return created
    return False


def render_api_access_panel(user):
    api_allowed = user_can_use_api(user)
    webhook_allowed = user_can_use_webhooks(user)
    write_scope_disabled = " disabled" if not user_can_write_content(user) else ""
    if not api_allowed and not webhook_allowed:
        return ""
    api_section = ""
    if api_allowed:
        token_rows = api_token_rows(user["id"])
        token_items = "".join(render_api_token_row(token) for token in token_rows) or render_empty_action_state(
            "No API tokens yet.",
            "Create a scoped bearer token above when you are ready to connect scripts or external tools.",
            tag="li",
        )
        api_section = f"""
            <div class="panel-heading">
                <h2>API access</h2>
                <span class="pill">Bearer tokens</span>
            </div>
            <form class="form-grid compact-form embedded-form" method="post" action="/account/api-tokens#account-integrations">
                <label>Token name
                    <input required name="name" maxlength="80" placeholder="Collection sync script">
                </label>
                <fieldset class="preference-checks">
                    <legend>Scopes</legend>
                    <div class="preference-option-grid compact-preferences">
                        <label class="checkbox-line preference-option">
                            <input type="checkbox" name="scope" value="read" checked>
                            Read
                        </label>
                        <label class="checkbox-line preference-option">
                            <input type="checkbox" name="scope" value="write"{write_scope_disabled}>
                            Write
                        </label>
                    </div>
                </fieldset>
                <label class="span-2">Current password
                    <input required name="current_password" type="password" autocomplete="current-password">
                </label>
                <div class="form-actions span-2">
                    <button class="button primary" type="submit">Create API token</button>
                </div>
            </form>
            <ul class="stack-list compact-stack api-token-list">{token_items}</ul>
        """
    webhook_section = ""
    if webhook_allowed:
        webhook_rows = webhook_endpoint_rows(user["id"])
        delivery_rows = webhook_delivery_rows(user["id"])
        webhook_items = "".join(render_webhook_endpoint_row(webhook) for webhook in webhook_rows) or render_empty_action_state(
            "No webhooks configured yet.",
            "Add an endpoint above to receive signed BinderBridge events in another app.",
            tag="li",
        )
        delivery_items = "".join(render_webhook_delivery_row(delivery) for delivery in delivery_rows) or render_empty_action_state(
            "No webhook deliveries yet.",
            "Deliveries will appear here after a subscribed notification event is queued.",
            actions=(("/notifications#notification-inbox", "Open notifications", "ghost"),),
            tag="li",
        )
        event_checks = "".join(
            f"""
            <label class="checkbox-line preference-option">
                <input type="checkbox" name="event_type" value="{e(event)}"{" checked" if event == "notification.created" else ""}>
                {e(label)}
            </label>
            """
            for event, label in WEBHOOK_EVENT_OPTIONS
        )
        webhook_section = f"""
            <div class="panel-heading with-gap">
                <h2>Webhooks</h2>
                <span class="pill">Signed JSON</span>
            </div>
            <form class="form-grid compact-form embedded-form" method="post" action="/account/webhooks#account-integrations">
                <label>Name
                    <input required name="name" maxlength="80" placeholder="Discord bridge">
                </label>
                <label>Endpoint URL
                    <input required name="url" type="url" maxlength="500" placeholder="https://example.com/binderbridge/webhook">
                </label>
                <fieldset class="preference-checks span-2">
                    <legend>Events</legend>
                    <div class="preference-option-grid webhook-event-grid">{event_checks}</div>
                </fieldset>
                <label class="span-2">Signing secret
                    <input name="secret" maxlength="160" placeholder="Leave blank to generate one">
                </label>
                <label class="span-2">Current password
                    <input required name="current_password" type="password" autocomplete="current-password">
                </label>
                <div class="form-actions span-2">
                    <button class="button primary" type="submit">Add webhook</button>
                </div>
            </form>
            <ul class="stack-list compact-stack webhook-list">{webhook_items}</ul>

            <div class="panel-heading with-gap">
                <h2>Recent deliveries</h2>
            </div>
            <ul class="stack-list compact-stack webhook-delivery-list">{delivery_items}</ul>
        """
    return f"""
        <article class="panel api-access-panel span-2">
            {api_section}
            {webhook_section}
        </article>
    """


def render_api_token_row(token):
    status = "Revoked" if row_value(token, "revoked_at", "") else "Active"
    status_class = "declined" if row_value(token, "revoked_at", "") else "accepted"
    last_used = row_value(token, "last_used_at", "")[:16].replace("T", " ") if row_value(token, "last_used_at", "") else "Never used"
    revoke = (
        f"""
        <form method="post" action="/account/api-tokens/{token["id"]}/revoke#account-integrations">
            <button class="button ghost small" type="submit">Revoke</button>
        </form>
        """
        if not row_value(token, "revoked_at", "")
        else ""
    )
    delete = (
        f"""
        <form method="post" action="/account/api-tokens/{token["id"]}/delete#account-integrations">
            <button class="button danger small" type="submit" data-confirm="Delete this revoked API token record?">Delete</button>
        </form>
        """
        if row_value(token, "revoked_at", "")
        else ""
    )
    return f"""
    <li class="api-token-row">
        <div>
            <strong>{e(token["name"])}</strong>
            <span class="subtle">Scopes: {e(row_value(token, "scopes", "read"))} - token ending {e(row_value(token, "token_hint", ""))}</span>
            <span class="subtle">Last used: {e(last_used)}</span>
        </div>
        <div class="inline-actions">
            <span class="status {status_class}">{e(status)}</span>
            {revoke}
            {delete}
        </div>
    </li>
    """


def render_webhook_endpoint_row(webhook):
    event_labels = ", ".join(
        WEBHOOK_EVENT_LABELS.get(event, event)
        for event in normalize_webhook_events(row_value(webhook, "event_types", ""))
    )
    last_success = row_value(webhook, "last_success_at", "")[:16].replace("T", " ") if row_value(webhook, "last_success_at", "") else "No successful deliveries yet"
    last_error = row_value(webhook, "last_error", "")
    error_line = f'<span class="subtle">Last error: {e(last_error)}</span>' if last_error else ""
    return f"""
    <li class="webhook-row">
        <div>
            <strong>{e(webhook["name"])}</strong>
            <span class="subtle">{e(row_value(webhook, "url", ""))}</span>
            <span class="subtle">{e(event_labels)}</span>
            <span class="subtle">Last success: {e(last_success)}</span>
            {error_line}
        </div>
        <div class="inline-actions">
            <form method="post" action="/account/webhooks/{webhook["id"]}/test#account-integrations">
                <button class="button secondary small" type="submit">Send test</button>
            </form>
            <form method="post" action="/account/webhooks/{webhook["id"]}/delete#account-integrations">
                <button class="button ghost small" type="submit">Delete</button>
            </form>
        </div>
    </li>
    """


def render_webhook_delivery_row(delivery):
    status_class = "accepted" if delivery["status"] == "sent" else "declined" if delivery["status"] == "failed" else "pending"
    status_text = row_value(delivery, "status", "pending").title()
    detail = row_value(delivery, "error", "") or row_value(delivery, "response_body", "")
    detail_line = f'<span class="subtle">{e(detail)}</span>' if detail else ""
    return f"""
    <li>
        <div>
            <strong>{e(row_value(delivery, "webhook_name", "Webhook"))}</strong>
            <span class="subtle">{e(row_value(delivery, "event_type", ""))} - {e(row_value(delivery, "created_at", "")[:16].replace("T", " "))}</span>
            {detail_line}
        </div>
        <span class="status {status_class}">{e(status_text)}</span>
    </li>
    """


def api_pagination(query):
    page = max(1, query_int(query, "page", 1))
    per_page = max(1, min(query_int(query, "per_page", 100), API_PAGE_SIZE_MAX))
    return page, per_page, (page - 1) * per_page


def api_row_dict(item, fields):
    return {field: row_value(item, field, "") for field in fields}


COLLECTION_API_FIELDS = (
    "id", "game", "card_name", "set_name", "set_code", "collector_number", "finish", "condition",
    "condition_notes", "language", "quantity", "quantity_for_trade", "scryfall_id", "image_url", "mana_cost", "type_line",
    "oracle_text", "rarity", "colors", "color_identity", "scryfall_uri", "price_usd", "price_source",
    "price_refreshed_at", "price_status", "notes", "is_public", "visibility", "created_at", "updated_at",
)
WANT_API_FIELDS = (
    "id", "game", "card_name", "set_name", "set_code", "collector_number", "desired_quantity",
    "priority", "budget_cap_usd", "condition", "finish", "language", "scryfall_id", "image_url", "mana_cost", "type_line",
    "oracle_text", "rarity", "colors", "color_identity", "scryfall_uri", "price_usd", "price_source",
    "preferred_printing_notes", "notes", "is_public", "visibility", "created_at", "updated_at",
)
GROUP_API_FIELDS = (
    "id", "group_type", "name", "description", "is_public", "visibility", "default_item_visibility",
    "show_values", "show_photos", "collection_quantity", "collection_entries", "want_entries", "created_at", "updated_at",
)


def api_collection_item_dict(item):
    data = api_row_dict(item, COLLECTION_API_FIELDS)
    for key in ("id", "quantity", "quantity_for_trade", "is_public"):
        data[key] = int(data[key] or 0)
    data["photo_count"] = collection_item_photo_count(data["id"])
    return data


def api_want_item_dict(item):
    data = api_row_dict(item, WANT_API_FIELDS)
    for key in ("id", "desired_quantity", "is_public"):
        data[key] = int(data[key] or 0)
    return data


def api_group_dict(group):
    data = api_row_dict(group, GROUP_API_FIELDS)
    for key in ("id", "is_public", "show_values", "show_photos", "collection_quantity", "collection_entries", "want_entries"):
        data[key] = int(data[key] or 0)
    data["group_type_label"] = group_type_label(data["group_type"])
    return data


def api_group_collection_item_dict(item):
    data = api_collection_item_dict(item)
    data["group_item_id"] = int(row_value(item, "group_item_id", 0) or 0)
    data["group_quantity"] = int(row_value(item, "group_quantity", 0) or 0)
    return data


def api_bool_value(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if not text:
        return default
    return text not in ("0", "false", "no", "off")


def api_group_type_filter(query):
    group_type = query_value(query or {}, "type").strip().lower()
    if group_type in ("collection", "collections", "deck-binder", "deck_binder", "deck-binders", "deck_binders"):
        return "collection"
    if group_type:
        return normalize_group_type(group_type)
    return ""


def api_group_item_filters(query):
    return {
        "q": query_value(query or {}, "q"),
        "game": query_value(query or {}, "game"),
        "condition": query_value(query or {}, "condition"),
        "finish": query_value(query or {}, "finish"),
    }


def api_payload_to_form(payload):
    form = {}
    for key, value in dict(payload or {}).items():
        clean_key = sanitize_text_input(key, max_length=120).strip()
        if not clean_key:
            continue
        if isinstance(value, list):
            form[clean_key] = [sanitize_text_input(item, max_length=MAX_FORM_VALUE_LENGTH) for item in value]
        elif isinstance(value, bool):
            form[clean_key] = ["1" if value else "0"]
        else:
            form[clean_key] = [sanitize_text_input(value, max_length=MAX_FORM_VALUE_LENGTH)]
    return form


def api_extract_bearer_token(headers):
    auth = headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return headers.get("X-BinderBridge-Token", "").strip()


def integration_request_ip(self):
    if hasattr(self, "client_ip"):
        return self.client_ip()
    try:
        return self.client_address[0]
    except (AttributeError, TypeError, IndexError):
        return ""


def integration_user_agent(self):
    return self.headers.get("User-Agent", "")


def log_integration_action(self, user_id, action, target_label, details="", target_type="integration"):
    return log_admin_action(
        user_id,
        action,
        user_id,
        target_type,
        target_label,
        details,
        integration_request_ip(self),
        integration_user_agent(self),
    )


def account_api_token_create(self, user):
    form = self.read_form()
    self.enforce_rate_limit(
        "integration_admin",
        f"user:{user['id']}",
        "Too many integration-management requests. Try again shortly.",
    )
    if not user_can_use_api(user):
        return self.html(render_account(user, notice=integration_access_error("API"), status="error", active_section="account-integrations"), HTTPStatus.FORBIDDEN)
    if not verify_password(form.get("current_password", [""])[0], user["password_hash"]):
        return self.html(render_account(user, notice="Current password is required to create an API token.", status="error", active_section="account-integrations"), HTTPStatus.UNAUTHORIZED)
    try:
        token = create_api_token(user["id"], form.get("name", ["API token"])[0], form.get("scope", ["read"]))
    except ValueError as exc:
        return self.html(render_account(user, notice=str(exc), status="error", active_section="account-integrations"), HTTPStatus.FORBIDDEN)
    log_integration_action(
        self,
        user["id"],
        "api_token_created",
        token["name"],
        f"Scopes: {','.join(token['scopes'])}. Token ending {token['token'][-8:]}.",
        "api_token",
    )
    refreshed = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    notice = f"API token created. Copy it now; it will not be shown again: {token['token']}"
    return self.html(render_account(refreshed, notice=notice, status="info", active_section="account-integrations"))


def account_api_token_revoke(self, user, path):
    self.enforce_rate_limit(
        "integration_admin",
        f"user:{user['id']}",
        "Too many integration-management requests. Try again shortly.",
    )
    try:
        token_id = int(path.strip("/").split("/")[2])
    except (IndexError, ValueError):
        return self.not_found(user)
    token = row("SELECT * FROM api_tokens WHERE id = ? AND user_id = ?", (token_id, user["id"]))
    revoked = revoke_api_token(user["id"], token_id)
    if revoked and token:
        log_integration_action(
            self,
            user["id"],
            "api_token_revoked",
            row_value(token, "name", "API token"),
            f"Token ending {row_value(token, 'token_hint', '')}.",
            "api_token",
        )
    refreshed = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    notice = "API token revoked." if revoked else "API token was not found."
    status = "info" if revoked else "error"
    return self.html(render_account(refreshed, notice=notice, status=status, active_section="account-integrations"), HTTPStatus.OK if revoked else HTTPStatus.NOT_FOUND)


def account_api_token_delete(self, user, path):
    self.enforce_rate_limit(
        "integration_admin",
        f"user:{user['id']}",
        "Too many integration-management requests. Try again shortly.",
    )
    try:
        token_id = int(path.strip("/").split("/")[2])
    except (IndexError, ValueError):
        return self.not_found(user)
    token = row("SELECT * FROM api_tokens WHERE id = ? AND user_id = ?", (token_id, user["id"]))
    deleted = delete_revoked_api_token(user["id"], token_id)
    if deleted and token:
        log_integration_action(
            self,
            user["id"],
            "api_token_deleted",
            row_value(token, "name", "API token"),
            f"Deleted revoked token ending {row_value(token, 'token_hint', '')}.",
            "api_token",
        )
    refreshed = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    notice = "Revoked API token deleted." if deleted else "Only revoked API tokens can be deleted."
    status = "info" if deleted else "error"
    return self.html(render_account(refreshed, notice=notice, status=status, active_section="account-integrations"), HTTPStatus.OK if deleted else HTTPStatus.BAD_REQUEST)


def account_webhook_create(self, user):
    form = self.read_form()
    self.enforce_rate_limit(
        "integration_admin",
        f"user:{user['id']}",
        "Too many integration-management requests. Try again shortly.",
    )
    if not user_can_use_webhooks(user):
        return self.html(render_account(user, notice=integration_access_error("Webhook"), status="error", active_section="account-integrations"), HTTPStatus.FORBIDDEN)
    if not verify_password(form.get("current_password", [""])[0], user["password_hash"]):
        return self.html(render_account(user, notice="Current password is required to add a webhook.", status="error", active_section="account-integrations"), HTTPStatus.UNAUTHORIZED)
    try:
        webhook = create_webhook_endpoint(
            user["id"],
            form.get("name", ["Webhook"])[0],
            form.get("url", [""])[0],
            form.get("event_type", ["notification.created"]),
            form.get("secret", [""])[0],
        )
    except ValueError as exc:
        return self.html(render_account(user, notice=str(exc), status="error", active_section="account-integrations"), HTTPStatus.BAD_REQUEST)
    log_integration_action(
        self,
        user["id"],
        "webhook_created",
        webhook["name"],
        f"URL: {webhook['url']}. Events: {','.join(webhook['event_types'])}.",
        "webhook",
    )
    notice = f"Webhook added. Signing secret: {webhook['secret']}"
    refreshed = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    return self.html(render_account(refreshed, notice=notice, status="info", active_section="account-integrations"))


def account_webhook_delete(self, user, path):
    self.enforce_rate_limit(
        "integration_admin",
        f"user:{user['id']}",
        "Too many integration-management requests. Try again shortly.",
    )
    try:
        webhook_id = int(path.strip("/").split("/")[2])
    except (IndexError, ValueError):
        return self.not_found(user)
    webhook = row("SELECT * FROM webhook_endpoints WHERE id = ? AND user_id = ?", (webhook_id, user["id"]))
    deleted = delete_webhook_endpoint(user["id"], webhook_id)
    if deleted and webhook:
        log_integration_action(
            self,
            user["id"],
            "webhook_deleted",
            row_value(webhook, "name", "Webhook"),
            f"URL: {row_value(webhook, 'url', '')}.",
            "webhook",
        )
    refreshed = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    notice = "Webhook deleted." if deleted else "Webhook was not found."
    status = "info" if deleted else "error"
    return self.html(render_account(refreshed, notice=notice, status=status, active_section="account-integrations"), HTTPStatus.OK if deleted else HTTPStatus.NOT_FOUND)


def account_webhook_test(self, user, path):
    self.enforce_rate_limit(
        "integration_admin",
        f"user:{user['id']}",
        "Too many integration-management requests. Try again shortly.",
    )
    if not user_can_use_webhooks(user):
        return self.html(render_account(user, notice=integration_access_error("Webhook"), status="error", active_section="account-integrations"), HTTPStatus.FORBIDDEN)
    try:
        webhook_id = int(path.strip("/").split("/")[2])
    except (IndexError, ValueError):
        return self.not_found(user)
    found = row("SELECT * FROM webhook_endpoints WHERE id = ? AND user_id = ?", (webhook_id, user["id"]))
    if not found:
        return self.not_found(user)
    payload_json = json.dumps(
        webhook_payload(
            user["id"],
            "notification.created",
            {"notification": {"kind": "test", "title": "Test webhook", "body": "BinderBridge webhook test delivery."}},
        ),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    execute(
        """
        INSERT INTO webhook_deliveries
            (webhook_id, user_id, event_type, payload_json, status, created_at)
        VALUES (?, ?, 'notification.created', ?, 'pending', ?)
        """,
        (webhook_id, user["id"], payload_json, now_iso()),
    )
    result = send_pending_webhook_deliveries(user_id=user["id"], limit=WEBHOOK_DELIVERY_BATCH_SIZE)
    log_integration_action(
        self,
        user["id"],
        "webhook_tested",
        row_value(found, "name", "Webhook"),
        f"Sent {result['sent']}; failed {result['failed']}.",
        "webhook",
    )
    refreshed = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    return self.html(render_account(refreshed, notice=f"Webhook test queued. Sent {result['sent']}, failed {result['failed']}.", status="info", active_section="account-integrations"))


def api_json(self, payload, status=HTTPStatus.OK):
    data = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    self.send_response(status)
    self.send_header("Content-Type", "application/json; charset=utf-8")
    self.send_header("Content-Length", str(len(data)))
    self.send_security_headers()
    self.end_headers()
    self.wfile.write(data)


def api_read_json(self):
    length = request_content_length(self.headers)
    if not length:
        return {}
    raw = self.rfile.read(length).decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Request body must be valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON request body must be an object.")
    return payload


def api_error(self, message, status=HTTPStatus.BAD_REQUEST):
    return self.api_json({"error": sanitize_text_input(message, max_length=300).strip()}, status)


def api_authenticate(self, required_scope="read"):
    token = api_extract_bearer_token(self.headers)
    user, token_row = get_user_by_api_token(token)
    if not user:
        if not rate_limit_allowed("api_auth_failed", integration_request_ip(self)):
            self.api_error("Too many failed API authentication attempts. Try again shortly.", HTTPStatus.TOO_MANY_REQUESTS)
            return None, None, True
        token_hint = token[-8:] if token else "missing"
        log_admin_action(
            None,
            "api_auth_failed",
            None,
            "api",
            f"Token {token_hint}",
            f"Failed API authentication for {self.command} {getattr(self, '_request_path', '')}.",
            integration_request_ip(self),
            integration_user_agent(self),
        )
        self.api_error("A valid API bearer token is required.", HTTPStatus.UNAUTHORIZED)
        return None, None, True
    if required_scope and not api_token_has_scope(token_row, required_scope):
        self.api_error(f"This token does not include the {required_scope} scope.", HTTPStatus.FORBIDDEN)
        return None, None, True
    return user, token_row, None


def api_collection_list(self, user, query):
    filters = collection_filter_values(query)
    where, params = collection_where(user["id"], filters)
    page, per_page, offset = api_pagination(query)
    total = row(f"SELECT COUNT(*) AS count FROM collection_items WHERE {' AND '.join(where)}", params)["count"]
    items = rows(
        f"""
        SELECT *
        FROM collection_items
        WHERE {' AND '.join(where)}
        ORDER BY card_name COLLATE NOCASE, set_name COLLATE NOCASE, collector_number COLLATE NOCASE
        LIMIT ? OFFSET ?
        """,
        [*params, per_page, offset],
    )
    return self.api_json({
        "data": [api_collection_item_dict(item) for item in items],
        "pagination": {"page": page, "per_page": per_page, "total": int(total)},
    })


def api_collection_create(self, user):
    payload = self.api_read_json()
    form = api_payload_to_form(payload)
    data = validate_collection_form(form)
    if data["game"] == "mtg" and form.get("lookup_on_save", [""])[0] == "1":
        if not rate_limit_allowed("scryfall_lookup", f"api:user:{user['id']}"):
            return self.api_error("Too many Scryfall lookup requests. Try again shortly.", HTTPStatus.TOO_MANY_REQUESTS)
        try:
            data = enrich_collection_data_from_scryfall(data)
        except ScryfallError as exc:
            return self.api_error(str(exc), HTTPStatus.BAD_REQUEST)
    merge_value = payload.get("merge", True)
    merge = str(merge_value).strip().lower() not in ("0", "false", "no", "off")
    action, item_id = upsert_collection_item(user["id"], data, merge=merge, return_id=True)
    item = row("SELECT * FROM collection_items WHERE id = ? AND user_id = ?", (item_id, user["id"]))
    log_integration_action(
        self,
        user["id"],
        "api_write",
        f"Collection item #{item_id}",
        f"{action.title()} collection item via API: {item['card_name']}.",
        "api",
    )
    return self.api_json({"status": action, "data": api_collection_item_dict(item)}, HTTPStatus.CREATED if action == "inserted" else HTTPStatus.OK)


def api_collection_import(self, user):
    payload = self.api_read_json()
    raw_items = payload.get("items", payload.get("rows", []))
    if not isinstance(raw_items, list) or not raw_items:
        return self.api_error("Import items are required.", HTTPStatus.BAD_REQUEST)
    if len(raw_items) > API_PAGE_SIZE_MAX:
        return self.api_error(f"Import is limited to {API_PAGE_SIZE_MAX} rows per request.", HTTPStatus.BAD_REQUEST)
    try:
        group_id = int(payload.get("group_id", 0) or 0)
    except (TypeError, ValueError):
        return self.api_error("Group id must be a number.", HTTPStatus.BAD_REQUEST)
    if group_id:
        group = user_group(user["id"], group_id)
        if not group or row_value(group, "group_type", "") == "wishlist":
            return self.api_error("Deck or binder group not found.", HTTPStatus.BAD_REQUEST)

    result_rows = []
    summary = {
        "inserted": 0,
        "updated": 0,
        "failed": 0,
        "grouped": 0,
        "group_failed": 0,
    }
    for index, item_payload in enumerate(raw_items, start=1):
        if not isinstance(item_payload, dict):
            summary["failed"] += 1
            result_rows.append({"ok": False, "index": index, "error": "Import row must be an object."})
            continue
        try:
            form = api_payload_to_form(item_payload)
            data = validate_collection_form(form)
            if data["game"] == "mtg" and form.get("lookup_on_save", [""])[0] == "1":
                if not rate_limit_allowed("scryfall_lookup", f"api:user:{user['id']}"):
                    raise ValueError("Too many Scryfall lookup requests. Try again shortly.")
                try:
                    data = enrich_collection_data_from_scryfall(data)
                except ScryfallError as exc:
                    raise ValueError(str(exc)) from exc
            merge_value = item_payload.get("merge", True)
            merge = str(merge_value).strip().lower() not in ("0", "false", "no", "off")
            action, item_id = upsert_collection_item(user["id"], data, merge=merge, return_id=True)
            item = row("SELECT * FROM collection_items WHERE id = ? AND user_id = ?", (item_id, user["id"]))
            summary["updated" if action == "updated" else "inserted"] += 1
            row_result = {
                "ok": True,
                "index": index,
                "status": action,
                "data": api_collection_item_dict(item),
            }
            if group_id:
                try:
                    add_collection_item_to_group(user["id"], group_id, item_id, item_payload.get("group_quantity", item_payload.get("quantity", 1)))
                    group_item = row(
                        """
                        SELECT
                            group_collection_items.id AS group_item_id,
                            group_collection_items.quantity AS group_quantity,
                            collection_items.*
                        FROM group_collection_items
                        JOIN collection_items ON collection_items.id = group_collection_items.collection_item_id
                        WHERE group_collection_items.group_id = ? AND group_collection_items.collection_item_id = ? AND collection_items.user_id = ?
                        """,
                        (group_id, item_id, user["id"]),
                    )
                    row_result["group_status"] = "added"
                    row_result["group"] = api_group_collection_item_dict(group_item)
                    summary["grouped"] += 1
                except ValueError as exc:
                    row_result["group_status"] = "failed"
                    row_result["group_error"] = str(exc)
                    summary["group_failed"] += 1
            result_rows.append(row_result)
        except ValueError as exc:
            summary["failed"] += 1
            result_rows.append({"ok": False, "index": index, "error": str(exc)})
    log_integration_action(
        self,
        user["id"],
        "api_write",
        "Collection import",
        f"Imported {summary['inserted']} new, updated {summary['updated']}, failed {summary['failed']} via API batch import.",
        "api",
    )
    return self.api_json({"data": result_rows, "summary": summary})


def api_collection_detail(self, user, item_id):
    item = row("SELECT * FROM collection_items WHERE id = ? AND user_id = ?", (item_id, user["id"]))
    if not item:
        return self.api_error("Collection item not found.", HTTPStatus.NOT_FOUND)
    return self.api_json({"data": api_collection_item_dict(item)})


def api_collection_update(self, user, item_id):
    existing = row("SELECT * FROM collection_items WHERE id = ? AND user_id = ?", (item_id, user["id"]))
    if not existing:
        return self.api_error("Collection item not found.", HTTPStatus.NOT_FOUND)
    payload = self.api_read_json()
    merged = dict(existing)
    merged.update(payload)
    form = api_payload_to_form(merged)
    data = validate_collection_form(form)
    update_collection_item(user["id"], item_id, data)
    item = row("SELECT * FROM collection_items WHERE id = ? AND user_id = ?", (item_id, user["id"]))
    log_integration_action(
        self,
        user["id"],
        "api_write",
        f"Collection item #{item_id}",
        f"Updated collection item via API: {item['card_name']}.",
        "api",
    )
    return self.api_json({"data": api_collection_item_dict(item)})


def api_collection_delete(self, user, item_id):
    deleted = bulk_delete_collection_items(user["id"], [item_id])
    if not deleted:
        return self.api_error("Collection item not found.", HTTPStatus.NOT_FOUND)
    log_integration_action(
        self,
        user["id"],
        "api_write",
        f"Collection item #{item_id}",
        "Deleted collection item via API.",
        "api",
    )
    return self.api_json({"deleted": deleted})


def api_groups_list(self, user, query):
    type_filter = api_group_type_filter(query)
    q = query_value(query, "q").lower()
    page, per_page, offset = api_pagination(query)
    group_rows = []
    for group in group_summary_rows(user["id"]):
        group_type = row_value(group, "group_type", "")
        if type_filter == "collection" and group_type not in ("deck", "binder"):
            continue
        if type_filter and type_filter != "collection" and group_type != type_filter:
            continue
        if q and q not in row_value(group, "name", "").lower() and q not in row_value(group, "description", "").lower():
            continue
        group_rows.append(group)
    total = len(group_rows)
    paged = group_rows[offset:offset + per_page]
    return self.api_json({
        "data": [api_group_dict(group) for group in paged],
        "pagination": {"page": page, "per_page": per_page, "total": total},
    })


def api_group_create(self, user):
    payload = self.api_read_json()
    group_type = payload.get("group_type", payload.get("type", "deck"))
    visibility = payload.get("visibility")
    is_public = api_bool_value(payload.get("is_public"), default=True)
    group_id = create_card_group(
        user["id"],
        group_type,
        payload.get("name", ""),
        payload.get("description", ""),
        is_public=is_public,
        visibility=visibility,
    )
    group = user_group(user["id"], group_id)
    log_integration_action(
        self,
        user["id"],
        "api_write",
        f"Group #{group_id}",
        f"Created {group_type_label(group['group_type']).lower()} group via API: {group['name']}.",
        "api",
    )
    return self.api_json({"data": api_group_dict(group)}, HTTPStatus.CREATED)


def api_group_detail(self, user, group_id, query=None):
    group = user_group(user["id"], group_id)
    if not group:
        return self.api_error("Group not found.", HTTPStatus.NOT_FOUND)
    page, per_page, offset = api_pagination(query or {})
    data = api_group_dict(group)
    if row_value(group, "group_type", "") == "wishlist":
        counts = row(
            "SELECT COUNT(*) AS entries FROM group_want_items WHERE group_id = ?",
            (group_id,),
        )
        data["want_entries"] = int(row_value(counts, "entries", 0) or 0)
        return self.api_json({
            "data": data,
            "items": [],
            "pagination": {"page": page, "per_page": per_page, "total": 0},
        })
    filters = api_group_item_filters(query or {})
    total = collection_group_item_count(group_id, filters)
    counts = row(
        """
        SELECT COALESCE(SUM(quantity), 0) AS quantity, COUNT(*) AS entries
        FROM group_collection_items
        WHERE group_id = ?
        """,
        (group_id,),
    )
    data["collection_quantity"] = int(row_value(counts, "quantity", 0) or 0)
    data["collection_entries"] = int(row_value(counts, "entries", 0) or 0)
    items = collection_group_items(group_id, filters=filters, limit=per_page, offset=offset)
    return self.api_json({
        "data": data,
        "items": [api_group_collection_item_dict(item) for item in items],
        "pagination": {"page": page, "per_page": per_page, "total": int(total)},
    })


def api_group_update(self, user, group_id):
    existing = user_group(user["id"], group_id)
    if not existing:
        return self.api_error("Group not found.", HTTPStatus.NOT_FOUND)
    payload = self.api_read_json()
    visibility = None
    if "visibility" in payload:
        visibility = payload.get("visibility")
    elif "is_public" in payload:
        visibility = api_bool_value(payload.get("is_public"), default=bool(row_value(existing, "is_public", 1)))
    updated = update_card_group(
        user["id"],
        group_id,
        name=payload.get("name") if "name" in payload else None,
        description=payload.get("description") if "description" in payload else None,
        visibility=visibility,
    )
    if not updated:
        return self.api_error("Group not found.", HTTPStatus.NOT_FOUND)
    group = user_group(user["id"], group_id)
    log_integration_action(
        self,
        user["id"],
        "api_write",
        f"Group #{group_id}",
        f"Updated group via API: {group['name']}.",
        "api",
    )
    return self.api_json({"data": api_group_dict(group)})


def api_group_delete(self, user, group_id):
    group = user_group(user["id"], group_id)
    deleted = delete_card_group(user["id"], group_id)
    if not deleted:
        return self.api_error("Group not found.", HTTPStatus.NOT_FOUND)
    log_integration_action(
        self,
        user["id"],
        "api_write",
        f"Group #{group_id}",
        f"Deleted group via API: {row_value(group, 'name', 'group')}.",
        "api",
    )
    return self.api_json({"deleted": deleted})


def api_group_add_collection_item(self, user, group_id):
    payload = self.api_read_json()
    try:
        collection_item_id = int(payload.get("collection_item_id", 0))
    except (TypeError, ValueError):
        return self.api_error("Collection item id must be a number.", HTTPStatus.BAD_REQUEST)
    quantity = payload.get("quantity", 1)
    add_collection_item_to_group(user["id"], group_id, collection_item_id, quantity)
    group_item = row(
        """
        SELECT
            group_collection_items.id AS group_item_id,
            group_collection_items.quantity AS group_quantity,
            collection_items.*
        FROM group_collection_items
        JOIN collection_items ON collection_items.id = group_collection_items.collection_item_id
        WHERE group_collection_items.group_id = ? AND group_collection_items.collection_item_id = ? AND collection_items.user_id = ?
        """,
        (group_id, collection_item_id, user["id"]),
    )
    if not group_item:
        return self.api_error("Group card was not added.", HTTPStatus.BAD_REQUEST)
    log_integration_action(
        self,
        user["id"],
        "api_write",
        f"Group #{group_id}",
        f"Added collection item #{collection_item_id} to group via API.",
        "api",
    )
    return self.api_json({"data": api_group_collection_item_dict(group_item)}, HTTPStatus.CREATED)


def api_group_update_collection_item(self, user, group_id, group_item_id):
    payload = self.api_read_json()
    updated = update_group_collection_item_quantities(user["id"], group_id, [group_item_id], payload.get("quantity", 1))
    if not updated:
        return self.api_error("Group card not found.", HTTPStatus.NOT_FOUND)
    group_item = row(
        """
        SELECT
            group_collection_items.id AS group_item_id,
            group_collection_items.quantity AS group_quantity,
            collection_items.*
        FROM group_collection_items
        JOIN collection_items ON collection_items.id = group_collection_items.collection_item_id
        WHERE group_collection_items.group_id = ? AND group_collection_items.id = ? AND collection_items.user_id = ?
        """,
        (group_id, group_item_id, user["id"]),
    )
    return self.api_json({"data": api_group_collection_item_dict(group_item)})


def api_group_remove_collection_item(self, user, group_id, group_item_id):
    deleted = remove_group_item(user["id"], group_id, group_item_id)
    if not deleted:
        return self.api_error("Group card not found.", HTTPStatus.NOT_FOUND)
    log_integration_action(
        self,
        user["id"],
        "api_write",
        f"Group #{group_id}",
        f"Removed group item #{group_item_id} via API.",
        "api",
    )
    return self.api_json({"deleted": deleted})


def api_wants_list(self, user, query):
    page, per_page, offset = api_pagination(query)
    q = query_value(query, "q")
    where = ["user_id = ?"]
    params = [user["id"]]
    if q:
        where.append("(card_name LIKE ? OR type_line LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    total = row(f"SELECT COUNT(*) AS count FROM want_items WHERE {' AND '.join(where)}", params)["count"]
    items = rows(
        f"""
        SELECT *
        FROM want_items
        WHERE {' AND '.join(where)}
        ORDER BY card_name COLLATE NOCASE, set_name COLLATE NOCASE, collector_number COLLATE NOCASE
        LIMIT ? OFFSET ?
        """,
        [*params, per_page, offset],
    )
    return self.api_json({
        "data": [api_want_item_dict(item) for item in items],
        "pagination": {"page": page, "per_page": per_page, "total": int(total)},
    })


def api_want_create(self, user):
    payload = self.api_read_json()
    data = validate_want_form(api_payload_to_form(payload))
    want_id = insert_want_item(user["id"], data)
    item = row("SELECT * FROM want_items WHERE id = ? AND user_id = ?", (want_id, user["id"]))
    log_integration_action(
        self,
        user["id"],
        "api_write",
        f"Want item #{want_id}",
        f"Created wanted card via API: {item['card_name']}.",
        "api",
    )
    return self.api_json({"data": api_want_item_dict(item)}, HTTPStatus.CREATED)


def api_want_detail(self, user, want_id):
    item = row("SELECT * FROM want_items WHERE id = ? AND user_id = ?", (want_id, user["id"]))
    if not item:
        return self.api_error("Want item not found.", HTTPStatus.NOT_FOUND)
    return self.api_json({"data": api_want_item_dict(item)})


def api_want_update(self, user, want_id):
    existing = row("SELECT * FROM want_items WHERE id = ? AND user_id = ?", (want_id, user["id"]))
    if not existing:
        return self.api_error("Want item not found.", HTTPStatus.NOT_FOUND)
    payload = self.api_read_json()
    merged = dict(existing)
    merged.update(payload)
    data = validate_want_form(api_payload_to_form(merged))
    updated = update_want_item(user["id"], want_id, data)
    if not updated:
        return self.api_error("Want item not found.", HTTPStatus.NOT_FOUND)
    item = row("SELECT * FROM want_items WHERE id = ? AND user_id = ?", (want_id, user["id"]))
    log_integration_action(
        self,
        user["id"],
        "api_write",
        f"Want item #{want_id}",
        f"Updated wanted card via API: {item['card_name']}.",
        "api",
    )
    return self.api_json({"data": api_want_item_dict(item)})


def api_want_delete(self, user, want_id):
    with db() as conn:
        found = conn.execute("SELECT * FROM want_items WHERE id = ? AND user_id = ?", (want_id, user["id"])).fetchone()
        if not found:
            return self.api_error("Want item not found.", HTTPStatus.NOT_FOUND)
        cursor = conn.execute("DELETE FROM want_items WHERE id = ? AND user_id = ?", (want_id, user["id"]))
        deleted = cursor.rowcount
    log_integration_action(
        self,
        user["id"],
        "api_write",
        f"Want item #{want_id}",
        "Deleted wanted card via API.",
        "api",
    )
    return self.api_json({"deleted": deleted})


def api_card_search(self, user, query):
    card_name = query_value(query, "q")
    set_code = query_value(query, "set_code")
    if not card_name:
        return self.api_error("Card name is required.", HTTPStatus.BAD_REQUEST)
    if not rate_limit_allowed("scryfall_lookup", f"api:user:{user['id']}"):
        return self.api_error("Too many Scryfall lookup requests. Try again shortly.", HTTPStatus.TOO_MANY_REQUESTS)
    limit = max(1, min(query_int(query, "limit", 8), 20))
    try:
        matches = search_scryfall_cards(card_name, set_code=set_code, limit=limit)
    except ScryfallError as exc:
        return self.api_error(str(exc), HTTPStatus.BAD_REQUEST)
    return self.api_json({"data": matches})


def api_trade_dict(trade, include_comments=False, viewer_id=None):
    trade_items = rows("SELECT * FROM trade_items WHERE trade_id = ? ORDER BY side, card_name", (trade["id"],))
    viewer_id = int(viewer_id or 0)
    proposer_id = int(trade["proposer_id"])
    recipient_id = int(trade["recipient_id"])
    viewer_role = ""
    if viewer_id == proposer_id:
        viewer_role = "proposer"
    elif viewer_id == recipient_id:
        viewer_role = "recipient"
    status_value = trade["status"]
    is_pending = status_value == "pending"
    counter_trade_id = row_value(trade, "counter_trade_id")
    countered_from_trade_id = row_value(trade, "countered_from_trade_id")
    data = {
        "id": int(trade["id"]),
        "status": status_value,
        "status_label": TRADE_STATUS_LABELS.get(status_value, status_value.title()),
        "proposer": {"id": proposer_id, "display_name": row_value(trade, "proposer_name", "")},
        "recipient": {"id": recipient_id, "display_name": row_value(trade, "recipient_name", "")},
        "viewer": {
            "id": viewer_id,
            "role": viewer_role,
            "direction": "outgoing" if viewer_role == "proposer" else ("incoming" if viewer_role == "recipient" else ""),
            "needs_action": bool(is_pending and viewer_role == "recipient"),
            "can_accept": bool(is_pending and viewer_role == "recipient"),
            "can_decline": bool(is_pending and viewer_role == "recipient"),
            "can_cancel": bool(is_pending and viewer_role == "proposer"),
            "can_complete": bool(status_value == "accepted" and viewer_role in ("proposer", "recipient")),
            "can_counter": bool(is_pending and viewer_role == "recipient" and not counter_trade_id),
        },
        "countered_from_trade_id": int(countered_from_trade_id or 0),
        "counter_trade_id": int(counter_trade_id or 0),
        "proposer_note": row_value(trade, "proposer_note", ""),
        "response_note": row_value(trade, "response_note", ""),
        "price_source_preference": row_value(trade, "price_source_preference", "scryfall"),
        "unread_trade_notifications": int(row_value(trade, "unread_trade_notifications", 0) or 0),
        "created_at": trade["created_at"],
        "updated_at": trade["updated_at"],
        "items": [
            {
                "side": item["side"],
                "owner_id": int(item["owner_id"]),
                "collection_item_id": row_value(item, "collection_item_id"),
                "card_name": item["card_name"],
                "set_name": row_value(item, "set_name", ""),
                "quantity": int(item["quantity"]),
                "condition": row_value(item, "condition", ""),
                "condition_notes": row_value(item, "condition_notes", ""),
                "finish": row_value(item, "finish", ""),
                "photo_count": len(trade_item_photo_rows(item["id"])),
                "price_usd": row_value(item, "price_usd", ""),
                "price_source": row_value(item, "price_source", ""),
            }
            for item in trade_items
        ],
    }
    if include_comments:
        data["comments"] = [
            {
                "id": int(comment["id"]),
                "user_id": int(comment["user_id"]),
                "display_name": row_value(comment, "display_name", ""),
                "body": comment["body"],
                "created_at": comment["created_at"],
            }
            for comment in trade_comment_rows(trade["id"])
        ]
    return data


def api_trade_partner_dict(user, partner):
    where, params = trade_picker_where(partner["id"], {}, viewer_id=user["id"])
    return {
        "id": int(partner["id"]),
        "username": row_value(partner, "username", ""),
        "display_name": row_value(partner, "display_name", ""),
        "available_entries": int(trade_picker_count(where, params)),
    }


def api_trade_partners_list(self, user, query):
    q = sanitize_text_input(str(query.get("q", [""])[0] or ""), max_length=120).strip().lower()
    page, per_page, offset = api_pagination(query)
    partners = browse_filter_users(user["id"])
    if q:
        partners = [
            partner
            for partner in partners
            if q in row_value(partner, "display_name", "").lower()
            or q in row_value(partner, "username", "").lower()
        ]
    total = len(partners)
    page_items = partners[offset:offset + per_page]
    return self.api_json({
        "data": [api_trade_partner_dict(user, partner) for partner in page_items],
        "pagination": {"page": page, "per_page": per_page, "total": int(total)},
    })


def api_trade_card_dict(user, owner, item):
    data = api_collection_item_dict(item)
    data["owner"] = {
        "id": int(owner["id"]),
        "username": row_value(owner, "username", ""),
        "display_name": row_value(owner, "display_name", ""),
    }
    if not can_view_collection_values(user, owner):
        data["price_usd"] = ""
        data["price_source"] = ""
    return data


def api_trade_cards_list(self, user, query):
    owner_id = query_int(query, "owner_id", 0)
    if owner_id <= 0:
        owner_id = int(user["id"])
    owner = row("SELECT * FROM users WHERE id = ?", (owner_id,))
    if not owner or int(row_value(owner, "is_banned", 0) or 0):
        return self.api_error("Trade partner not found.", HTTPStatus.NOT_FOUND)
    q = sanitize_text_input(str(query.get("q", [""])[0] or ""), max_length=160).strip()
    filters = {"q": q}
    where, params = trade_picker_where(owner_id, filters, viewer_id=user["id"])
    page, per_page, offset = api_pagination(query)
    total = trade_picker_count(where, params)
    items = trade_picker_rows(
        where,
        params,
        "card_name COLLATE NOCASE, set_name COLLATE NOCASE, collector_number COLLATE NOCASE",
        per_page,
        offset,
    )
    return self.api_json({
        "data": [api_trade_card_dict(user, owner, item) for item in items],
        "owner": {"id": int(owner["id"]), "username": owner["username"], "display_name": owner["display_name"]},
        "pagination": {"page": page, "per_page": per_page, "total": int(total)},
    })


def api_trade_selection_form(payload):
    form = {}
    for payload_key, form_prefix in (("offered", "offer"), ("requested", "request")):
        selections = payload.get(payload_key, [])
        if not isinstance(selections, list):
            raise ValueError(f"{payload_key} must be a list.")
        quantities = {}
        for selection in selections:
            if not isinstance(selection, dict):
                raise ValueError(f"{payload_key} entries must be objects.")
            try:
                item_id = int(selection.get("collection_item_id", selection.get("id", 0)) or 0)
                quantity = int(selection.get("quantity", 0) or 0)
            except (TypeError, ValueError) as exc:
                raise ValueError("Trade selections must include numeric collection item ids and quantities.") from exc
            if item_id <= 0 or quantity <= 0:
                continue
            quantities[item_id] = quantities.get(item_id, 0) + quantity
        for item_id, quantity in quantities.items():
            form[f"{form_prefix}_{item_id}"] = [str(quantity)]
    return form


def api_trade_create(self, user):
    payload = self.api_read_json()
    try:
        recipient_id = int(payload.get("recipient_id", 0) or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("recipient_id must be a number.") from exc
    if recipient_id <= 0:
        raise ValueError("Choose a trade partner.")
    if recipient_id == int(user["id"]):
        raise ValueError("Choose another member to trade with.")
    recipient = row("SELECT * FROM users WHERE id = ?", (recipient_id,))
    if (
        not recipient
        or int(row_value(recipient, "is_banned", 0) or 0)
        or row_value(recipient, "registration_status", "active") != "active"
    ):
        return self.api_error("Trade partner not found.", HTTPStatus.NOT_FOUND)
    try:
        counter_trade_id = int(payload.get("counter_trade_id", 0) or 0)
    except (TypeError, ValueError):
        counter_trade_id = 0
    proposer_note = sanitize_text_input(str(payload.get("proposer_note", "")), max_length=1200).strip()
    form = api_trade_selection_form(payload)
    offered = parse_trade_quantities(form, "offer", user["id"], "scryfall", viewer_id=user["id"])
    requested = parse_trade_quantities(form, "request", recipient_id, "scryfall", viewer_id=user["id"])
    trade_id = create_trade_offer(
        user["id"],
        recipient_id,
        proposer_note,
        offered,
        requested,
        counter_trade_id,
        "scryfall",
    )
    trade = trade_detail_for_user(trade_id, user["id"])
    status = HTTPStatus.OK if counter_trade_id else HTTPStatus.CREATED
    return self.api_json({"data": api_trade_dict(trade, include_comments=True, viewer_id=user["id"])}, status)


def api_trades_list(self, user, query):
    filters = trade_list_filter_values(query)
    page, per_page, offset = api_pagination(query)
    total = trade_count_for_user(user["id"], filters)
    trade_rows = trade_page_rows(user["id"], filters, per_page, offset)
    return self.api_json({
        "data": [api_trade_dict(trade, viewer_id=user["id"]) for trade in trade_rows],
        "filters": filters,
        "metrics": {
            "needs_action": int(trade_count_for_user(user["id"], {"direction": "needs_action"})),
        },
        "pagination": {"page": page, "per_page": per_page, "total": int(total)},
    })


def api_trade_detail(self, user, trade_id):
    trade = trade_detail_for_user(trade_id, user["id"])
    if not trade:
        return self.api_error("Trade not found.", HTTPStatus.NOT_FOUND)
    return self.api_json({"data": api_trade_dict(trade, include_comments=True, viewer_id=user["id"])})


def api_trade_action(self, user, trade_id, action):
    payload = self.api_read_json()
    response_note = sanitize_text_input(str(payload.get("response_note", "")), max_length=1200).strip()
    fairness_acknowledged = bool(payload.get("fairness_acknowledged", False) or payload.get("fairness_ack", False))
    if action == "accept":
        update_trade_response(trade_id, user["id"], "accepted", response_note, fairness_acknowledged)
    elif action == "decline":
        update_trade_response(trade_id, user["id"], "declined", response_note, False)
    elif action == "cancel":
        cancel_trade_offer(trade_id, user["id"])
    elif action == "complete":
        complete_trade(trade_id, completed_by_user_id=user["id"])
    else:
        return self.api_error("Trade action not found.", HTTPStatus.NOT_FOUND)
    trade = trade_detail_for_user(trade_id, user["id"])
    if not trade:
        return self.api_error("Trade not found.", HTTPStatus.NOT_FOUND)
    return self.api_json({"data": api_trade_dict(trade, include_comments=True, viewer_id=user["id"]), "action": action})


def api_trade_comment_create(self, user, trade_id):
    payload = self.api_read_json()
    body = sanitize_text_input(
        str(payload.get("body", payload.get("comment", payload.get("message", "")))),
        max_length=2000,
    ).strip()
    add_trade_comment(trade_id, user["id"], body)
    trade = trade_detail_for_user(trade_id, user["id"])
    if not trade:
        return self.api_error("Trade not found.", HTTPStatus.NOT_FOUND)
    return self.api_json({"data": api_trade_dict(trade, include_comments=True, viewer_id=user["id"])}, HTTPStatus.CREATED)


def api_notification_dict(item):
    return {
        "id": int(item["id"]),
        "kind": item["kind"],
        "title": item["title"],
        "body": item["body"],
        "url": item["url"],
        "related_trade_id": row_value(item, "related_trade_id"),
        "is_read": bool(item["is_read"]),
        "created_at": item["created_at"],
    }


def api_notifications_list(self, user, query):
    page, per_page, offset = api_pagination(query)
    total = row("SELECT COUNT(*) AS count FROM user_notifications WHERE user_id = ?", (user["id"],))["count"]
    items = rows(
        """
        SELECT *
        FROM user_notifications
        WHERE user_id = ?
        ORDER BY is_read ASC, created_at DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        (user["id"], per_page, offset),
    )
    return self.api_json({
        "data": [api_notification_dict(item) for item in items],
        "pagination": {"page": page, "per_page": per_page, "total": int(total)},
    })


def api_notification_detail(self, user, notification_id):
    item = row("SELECT * FROM user_notifications WHERE id = ? AND user_id = ?", (notification_id, user["id"]))
    if not item:
        return self.api_error("Notification not found.", HTTPStatus.NOT_FOUND)
    return self.api_json({"data": api_notification_dict(item)})


def api_notification_mark_read(self, user, notification_id):
    item = row("SELECT * FROM user_notifications WHERE id = ? AND user_id = ?", (notification_id, user["id"]))
    if not item:
        return self.api_error("Notification not found.", HTTPStatus.NOT_FOUND)
    mark_notification_read(user["id"], notification_id)
    refreshed = row("SELECT * FROM user_notifications WHERE id = ? AND user_id = ?", (notification_id, user["id"]))
    return self.api_json({"data": api_notification_dict(refreshed)})


def api_notifications_mark_all_read(self, user):
    mark_all_notifications_read(user["id"])
    return self.api_json({"read_all": True})


def api_notification_delete(self, user, notification_id):
    deleted = delete_notification(user["id"], notification_id)
    if not deleted:
        return self.api_error("Notification not found.", HTTPStatus.NOT_FOUND)
    return self.api_json({"deleted": deleted})


def api_dispatch(self, method, path, query):
    if path == "/api/v1/health" and method == "GET":
        if not rate_limit_allowed("api_health", integration_request_ip(self)):
            return self.api_error("Too many API health requests. Try again shortly.", HTTPStatus.TOO_MANY_REQUESTS)
        return self.api_json({"ok": True, "app": APP_NAME, "version": APP_VERSION})
    required_scope = "write" if method in ("POST", "PUT", "PATCH", "DELETE") else "read"
    user, token_row, error = self.api_authenticate(required_scope)
    if error:
        return None
    if required_scope == "write" and not user_can_write_content(user):
        return self.api_error("This account is read-only.", HTTPStatus.FORBIDDEN)
    if required_scope == "read" and not rate_limit_allowed("api_read", f"user:{user['id']}"):
        return self.api_error("Too many API read requests. Try again shortly.", HTTPStatus.TOO_MANY_REQUESTS)
    if required_scope == "write" and not rate_limit_allowed("api_write", f"user:{user['id']}"):
        return self.api_error("Too many API write requests. Try again shortly.", HTTPStatus.TOO_MANY_REQUESTS)
    try:
        if path == "/api/v1/me" and method == "GET":
            return self.api_json({
                "data": {
                    "id": int(user["id"]),
                    "username": user["username"],
                    "display_name": user["display_name"],
                    "email": user["email"],
                    "role": user_role(user),
                    "is_admin": bool(user["is_admin"]),
                    "api_token": {
                        "name": row_value(token_row, "name", "API token"),
                        "scopes": normalize_api_token_scopes(row_value(token_row, "scopes", "read")),
                        "token_hint": row_value(token_row, "token_hint", ""),
                        "expires_at": row_value(token_row, "expires_at", ""),
                    },
                }
            })
        if path == "/api/v1/collection":
            if method == "GET":
                return self.api_collection_list(user, query)
            if method == "POST":
                return self.api_collection_create(user)
        if path == "/api/v1/collection/import" and method == "POST":
            return self.api_collection_import(user)
        if path.startswith("/api/v1/collection/"):
            try:
                item_id = int(path.rsplit("/", 1)[1])
            except ValueError:
                return self.api_error("Collection item id must be a number.", HTTPStatus.NOT_FOUND)
            if method == "GET":
                return self.api_collection_detail(user, item_id)
            if method in ("POST", "PUT", "PATCH"):
                return self.api_collection_update(user, item_id)
            if method == "DELETE":
                return self.api_collection_delete(user, item_id)
        if path == "/api/v1/groups":
            if method == "GET":
                return self.api_groups_list(user, query)
            if method == "POST":
                return self.api_group_create(user)
        if path.startswith("/api/v1/groups/"):
            parts = path.strip("/").split("/")
            try:
                group_id = int(parts[3])
            except (ValueError, IndexError):
                return self.api_error("Group id must be a number.", HTTPStatus.NOT_FOUND)
            action = parts[4] if len(parts) > 4 else ""
            if len(parts) > 6:
                return self.api_error("API endpoint not found.", HTTPStatus.NOT_FOUND)
            if not action:
                if method == "GET":
                    return self.api_group_detail(user, group_id, query)
                if method in ("POST", "PUT", "PATCH"):
                    return self.api_group_update(user, group_id)
                if method == "DELETE":
                    return self.api_group_delete(user, group_id)
            if action == "collection-items":
                if len(parts) == 5 and method == "POST":
                    return self.api_group_add_collection_item(user, group_id)
                try:
                    group_item_id = int(parts[5])
                except (ValueError, IndexError):
                    return self.api_error("Group item id must be a number.", HTTPStatus.NOT_FOUND)
                if method in ("POST", "PUT", "PATCH"):
                    return self.api_group_update_collection_item(user, group_id, group_item_id)
                if method == "DELETE":
                    return self.api_group_remove_collection_item(user, group_id, group_item_id)
        if path == "/api/v1/wants":
            if method == "GET":
                return self.api_wants_list(user, query)
            if method == "POST":
                return self.api_want_create(user)
        if path.startswith("/api/v1/wants/"):
            try:
                want_id = int(path.rsplit("/", 1)[1])
            except ValueError:
                return self.api_error("Want item id must be a number.", HTTPStatus.NOT_FOUND)
            if method == "GET":
                return self.api_want_detail(user, want_id)
            if method in ("POST", "PUT", "PATCH"):
                return self.api_want_update(user, want_id)
            if method == "DELETE":
                return self.api_want_delete(user, want_id)
        if path == "/api/v1/cards/search" and method == "GET":
            return self.api_card_search(user, query)
        if path == "/api/v1/trade-partners" and method == "GET":
            return self.api_trade_partners_list(user, query)
        if path == "/api/v1/trade-cards" and method == "GET":
            return self.api_trade_cards_list(user, query)
        if path == "/api/v1/trades":
            if method == "GET":
                return self.api_trades_list(user, query)
            if method == "POST":
                return self.api_trade_create(user)
        if path.startswith("/api/v1/trades/"):
            parts = path.strip("/").split("/")
            try:
                trade_id = int(parts[3])
            except (ValueError, IndexError):
                return self.api_error("Trade id must be a number.", HTTPStatus.NOT_FOUND)
            action = parts[4] if len(parts) > 4 else ""
            if len(parts) > 5:
                return self.api_error("API endpoint not found.", HTTPStatus.NOT_FOUND)
            if not action and method == "GET":
                return self.api_trade_detail(user, trade_id)
            if action in ("accept", "decline", "cancel", "complete") and method in ("POST", "PUT", "PATCH"):
                return self.api_trade_action(user, trade_id, action)
            if action == "comments" and method == "POST":
                return self.api_trade_comment_create(user, trade_id)
        if path == "/api/v1/notifications" and method == "GET":
            return self.api_notifications_list(user, query)
        if path == "/api/v1/notifications/read-all" and method == "POST":
            return self.api_notifications_mark_all_read(user)
        if path.startswith("/api/v1/notifications/"):
            parts = path.strip("/").split("/")
            try:
                notification_id = int(parts[3])
            except (ValueError, IndexError):
                return self.api_error("Notification id must be a number.", HTTPStatus.NOT_FOUND)
            action = parts[4] if len(parts) > 4 else ""
            if not action and method == "GET":
                return self.api_notification_detail(user, notification_id)
            if action == "read" and method in ("POST", "PUT", "PATCH"):
                return self.api_notification_mark_read(user, notification_id)
            if not action and method == "DELETE":
                return self.api_notification_delete(user, notification_id)
    except ValueError as exc:
        return self.api_error(str(exc), HTTPStatus.BAD_REQUEST)
    return self.api_error("API endpoint not found.", HTTPStatus.NOT_FOUND)


API_ROUTE_METHODS = (
    "api_json",
    "api_read_json",
    "api_error",
    "api_authenticate",
    "api_collection_list",
    "api_collection_create",
    "api_collection_import",
    "api_collection_detail",
    "api_collection_update",
    "api_collection_delete",
    "api_groups_list",
    "api_group_create",
    "api_group_detail",
    "api_group_update",
    "api_group_delete",
    "api_group_add_collection_item",
    "api_group_update_collection_item",
    "api_group_remove_collection_item",
    "api_wants_list",
    "api_want_create",
    "api_want_detail",
    "api_want_update",
    "api_want_delete",
    "api_card_search",
    "api_trade_dict",
    "api_trade_partner_dict",
    "api_trade_partners_list",
    "api_trade_card_dict",
    "api_trade_cards_list",
    "api_trade_selection_form",
    "api_trade_create",
    "api_trades_list",
    "api_trade_detail",
    "api_trade_action",
    "api_trade_comment_create",
    "api_notification_dict",
    "api_notifications_list",
    "api_notification_detail",
    "api_notification_mark_read",
    "api_notifications_mark_all_read",
    "api_notification_delete",
    "api_dispatch",
    "account_api_token_create",
    "account_api_token_revoke",
    "account_api_token_delete",
    "account_webhook_create",
    "account_webhook_delete",
    "account_webhook_test",
)


__all__ = [
    "API_TOKEN_PREFIX",
    "API_TOKEN_SCOPES",
    "API_ACCESS_POLICY_KEY",
    "WEBHOOK_ACCESS_POLICY_KEY",
    "DEFAULT_INTEGRATION_ACCESS_POLICY",
    "INTEGRATION_ACCESS_POLICY_OPTIONS",
    "WEBHOOK_EVENT_OPTIONS",
    "WEBHOOK_EVENT_LABELS",
    "NOTIFICATION_WEBHOOK_EVENTS",
    "normalize_integration_access_policy",
    "integration_policy_label",
    "api_access_policy",
    "webhook_access_policy",
    "integration_access_settings",
    "set_integration_access_settings",
    "user_matches_integration_policy",
    "user_can_use_api",
    "user_can_use_webhooks",
    "integration_access_error",
    "api_token_hash",
    "normalize_api_token_scopes",
    "api_token_has_scope",
    "create_api_token",
    "revoke_api_token",
    "delete_revoked_api_token",
    "api_token_rows",
    "get_user_by_api_token",
    "normalize_webhook_events",
    "validate_webhook_url",
    "create_webhook_endpoint",
    "delete_webhook_endpoint",
    "webhook_endpoint_rows",
    "webhook_events_match",
    "webhook_payload",
    "queue_user_webhook_event",
    "queue_notification_webhooks",
    "send_webhook_http_request",
    "mark_webhook_delivery_result",
    "send_pending_webhook_deliveries",
    "webhook_delivery_rows",
    "start_webhook_delivery_worker",
    "render_api_access_panel",
    "render_api_token_row",
    "render_webhook_endpoint_row",
    "render_webhook_delivery_row",
    "api_pagination",
    "api_row_dict",
    "api_collection_item_dict",
    "api_want_item_dict",
    "api_group_dict",
    "api_group_collection_item_dict",
    "api_bool_value",
    "api_group_type_filter",
    "api_group_item_filters",
    "api_payload_to_form",
    "api_extract_bearer_token",
    "integration_request_ip",
    "integration_user_agent",
    "log_integration_action",
    "api_trade_dict",
    "API_ROUTE_METHODS",
    *API_ROUTE_METHODS,
]
