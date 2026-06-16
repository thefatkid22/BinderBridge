"""Registration moderation, approval queue, and privacy-safe evasion signals."""

import hashlib
import hmac
import ipaddress
import json
import secrets


REGISTRATION_APPROVAL_MODE_KEY = "registration_approval_mode"
REGISTRATION_RISK_THRESHOLD_KEY = "registration_risk_threshold"
REGISTRATION_SIGNAL_SECRET_KEY = "registration_signal_secret"
DEFAULT_REGISTRATION_APPROVAL_MODE = "off"
DEFAULT_REGISTRATION_RISK_THRESHOLD = 50
REGISTRATION_APPROVAL_MODE_OPTIONS = (
    ("off", "No approval queue"),
    ("all", "Require approval for every new account"),
    ("suspicious", "Require approval only for suspicious signups"),
)
REGISTRATION_STATUS_ACTIVE = "active"
REGISTRATION_STATUS_PENDING = "pending"
REGISTRATION_STATUS_DENIED = "denied"
REGISTRATION_STATUSES = {
    REGISTRATION_STATUS_ACTIVE,
    REGISTRATION_STATUS_PENDING,
    REGISTRATION_STATUS_DENIED,
}


def normalize_registration_approval_mode(value):
    mode = sanitize_text_input(value, max_length=40).strip().lower()
    valid = {key for key, _label in REGISTRATION_APPROVAL_MODE_OPTIONS}
    if mode not in valid:
        raise ValueError("Choose a valid registration approval mode.")
    return mode


def normalize_registration_risk_threshold(value):
    try:
        threshold = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("Risk threshold must be a whole number.") from exc
    if threshold < 0 or threshold > 1000:
        raise ValueError("Risk threshold must be between 0 and 1000.")
    return threshold


def registration_approval_mode():
    try:
        return normalize_registration_approval_mode(
            get_setting(REGISTRATION_APPROVAL_MODE_KEY, DEFAULT_REGISTRATION_APPROVAL_MODE)
        )
    except ValueError:
        return DEFAULT_REGISTRATION_APPROVAL_MODE


def registration_risk_threshold():
    try:
        return normalize_registration_risk_threshold(
            get_setting(REGISTRATION_RISK_THRESHOLD_KEY, DEFAULT_REGISTRATION_RISK_THRESHOLD)
        )
    except ValueError:
        return DEFAULT_REGISTRATION_RISK_THRESHOLD


def registration_moderation_settings():
    mode = registration_approval_mode()
    threshold = registration_risk_threshold()
    return {
        "approval_mode": mode,
        "approval_mode_label": dict(REGISTRATION_APPROVAL_MODE_OPTIONS).get(mode, "No approval queue"),
        "risk_threshold": threshold,
    }


def set_registration_moderation_settings(approval_mode, risk_threshold):
    mode = normalize_registration_approval_mode(approval_mode)
    threshold = normalize_registration_risk_threshold(risk_threshold)
    set_setting(REGISTRATION_APPROVAL_MODE_KEY, mode)
    set_setting(REGISTRATION_RISK_THRESHOLD_KEY, str(threshold))
    return registration_moderation_settings()


def registration_signal_secret():
    secret = get_setting(REGISTRATION_SIGNAL_SECRET_KEY, "")
    if secret:
        return secret
    secret = secrets.token_urlsafe(32)
    set_setting(REGISTRATION_SIGNAL_SECRET_KEY, secret)
    return secret


def registration_signal_hash(value):
    clean = sanitize_text_input(value, max_length=500).strip().lower()
    if not clean:
        return ""
    return hmac.new(
        registration_signal_secret().encode("utf-8"),
        clean.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def registration_email_domain(email):
    clean = sanitize_text_input(email, max_length=254).strip().lower()
    if "@" not in clean:
        return ""
    domain = clean.rsplit("@", 1)[1].strip(". ")
    return domain[:120]


def registration_ip_subnet(value):
    clean = sanitize_text_input(value, max_length=120).strip()
    if not clean:
        return ""
    try:
        ip = ipaddress.ip_address(clean)
    except ValueError:
        return ""
    prefix = 24 if ip.version == 4 else 64
    return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False))


def registration_signal_values(email, request_ip, user_agent):
    email = sanitize_text_input(email, max_length=254).strip().lower()
    request_ip = sanitize_text_input(request_ip, max_length=120).strip()
    user_agent = sanitize_text_input(user_agent, max_length=500).strip()
    subnet = registration_ip_subnet(request_ip)
    return {
        "email_domain": registration_email_domain(email),
        "email_hash": registration_signal_hash(email),
        "ip_hash": registration_signal_hash(request_ip),
        "subnet_hash": registration_signal_hash(subnet),
        "user_agent_hash": registration_signal_hash(user_agent),
    }


def _risk_reason(reasons, code, label, points):
    reasons.append({"code": code, "label": label, "points": int(points)})


def registration_risk_assessment(username, display_name, email, invite=None, request_ip="", user_agent=""):
    clean_username = sanitize_text_input(username, max_length=40).strip()
    clean_display = sanitize_text_input(display_name, max_length=80).strip()
    clean_email = sanitize_text_input(email, max_length=254).strip().lower()
    signals = registration_signal_values(clean_email, request_ip, user_agent)
    reasons = []

    banned_match = row(
        """
        SELECT id FROM users
        WHERE email = ? COLLATE NOCASE
            AND email != ''
            AND (is_banned = 1 OR registration_status = 'denied')
        LIMIT 1
        """,
        (clean_email,),
    ) if clean_email else None
    if banned_match:
        _risk_reason(reasons, "email_match", "Email matches a banned or denied account", 80)

    username_match = row(
        """
        SELECT id FROM users
        WHERE username = ? COLLATE NOCASE
            AND (is_banned = 1 OR registration_status = 'denied')
        LIMIT 1
        """,
        (clean_username,),
    ) if clean_username else None
    if username_match:
        _risk_reason(reasons, "username_match", "Username matches a banned or denied account", 60)

    display_match = row(
        """
        SELECT id FROM users
        WHERE display_name = ? COLLATE NOCASE
            AND (is_banned = 1 OR registration_status = 'denied')
        LIMIT 1
        """,
        (clean_display,),
    ) if clean_display else None
    if display_match:
        _risk_reason(reasons, "display_match", "Display name matches a banned or denied account", 25)

    if signals["ip_hash"]:
        ip_match = row(
            """
            SELECT registration_attempts.id
            FROM registration_attempts
            LEFT JOIN users ON users.id = registration_attempts.user_id
            WHERE registration_attempts.ip_hash = ?
                AND (
                    registration_attempts.status IN ('denied', 'banned')
                    OR users.is_banned = 1
                    OR users.registration_status = 'denied'
                )
            LIMIT 1
            """,
            (signals["ip_hash"],),
        )
        if ip_match:
            _risk_reason(reasons, "ip_match", "Signup IP matches a denied or banned account", 50)

    if signals["subnet_hash"]:
        subnet_match = row(
            """
            SELECT registration_attempts.id
            FROM registration_attempts
            LEFT JOIN users ON users.id = registration_attempts.user_id
            WHERE registration_attempts.subnet_hash = ?
                AND (
                    registration_attempts.status IN ('denied', 'banned')
                    OR users.is_banned = 1
                    OR users.registration_status = 'denied'
                )
            LIMIT 1
            """,
            (signals["subnet_hash"],),
        )
        if subnet_match:
            _risk_reason(reasons, "subnet_match", "Signup network range has prior denied or banned activity", 20)

    if invite and row_value(invite, "created_by_user_id", None):
        inviter = row("SELECT * FROM users WHERE id = ?", (invite["created_by_user_id"],))
        if inviter and (row_value(inviter, "is_banned", 0) or row_value(inviter, "registration_status", "active") == "denied"):
            _risk_reason(reasons, "inviter_banned", "Invite was created by a banned or denied account", 60)
        prior_bad_invites = row(
            """
            SELECT COUNT(*) AS count
            FROM registration_invites
            JOIN users accepted ON accepted.id = registration_invites.accepted_by_user_id
            WHERE registration_invites.created_by_user_id = ?
                AND (accepted.is_banned = 1 OR accepted.registration_status = 'denied')
            """,
            (invite["created_by_user_id"],),
        )
        if prior_bad_invites and int(prior_bad_invites["count"] or 0) >= 2:
            _risk_reason(reasons, "inviter_pattern", "Inviter has multiple accepted accounts later banned or denied", 15)

    score = min(1000, sum(int(reason["points"]) for reason in reasons))
    return {"score": score, "reasons": reasons, "signals": signals}


def registration_status_for_new_account(user_count_before, risk_score):
    if int(user_count_before or 0) <= 0:
        return REGISTRATION_STATUS_ACTIVE
    mode = registration_approval_mode()
    if mode == "all":
        return REGISTRATION_STATUS_PENDING
    if mode == "suspicious" and int(risk_score or 0) >= registration_risk_threshold():
        return REGISTRATION_STATUS_PENDING
    return REGISTRATION_STATUS_ACTIVE


def record_registration_attempt(user_id, username, display_name, email, invite, request_ip, user_agent, assessment, status):
    status = sanitize_text_input(status, max_length=40).strip().lower() or REGISTRATION_STATUS_ACTIVE
    if status not in REGISTRATION_STATUSES:
        status = REGISTRATION_STATUS_ACTIVE
    signals = assessment.get("signals") or {}
    timestamp = now_iso()
    with db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO registration_attempts
                (user_id, invite_id, inviter_user_id, username, display_name, email_domain,
                 email_hash, ip_hash, subnet_hash, user_agent_hash, risk_score, risk_reasons_json,
                 status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(user_id),
                row_value(invite, "id", None),
                row_value(invite, "created_by_user_id", None),
                sanitize_text_input(username, max_length=40).strip(),
                sanitize_text_input(display_name, max_length=80).strip(),
                signals.get("email_domain", ""),
                signals.get("email_hash", ""),
                signals.get("ip_hash", ""),
                signals.get("subnet_hash", ""),
                signals.get("user_agent_hash", ""),
                int(assessment.get("score", 0) or 0),
                json.dumps(assessment.get("reasons", []), ensure_ascii=True, separators=(",", ":")),
                status,
                timestamp,
            ),
        )
        return cursor.lastrowid


def notify_registration_review_needed(user_id, assessment):
    user = row("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user:
        return 0
    title = "Account awaiting approval"
    reason_count = len(assessment.get("reasons", []))
    body = (
        f"{user['display_name']} (@{user['username']}) is waiting for registration review. "
        f"Risk score {int(assessment.get('score', 0) or 0)} with {reason_count} signal{'s' if reason_count != 1 else ''}."
    )
    admins = rows("SELECT id FROM users WHERE role IN ('owner', 'admin', 'moderator') AND is_banned = 0 AND registration_status = 'active'")
    for admin in admins:
        create_notification(admin["id"], "admin_notice", title, body, "/admin#admin-registration-review")
    return len(admins)


def pending_registration_rows(limit=20):
    return rows(
        """
        SELECT
            users.*,
            registration_attempts.id AS attempt_id,
            registration_attempts.invite_id,
            registration_attempts.inviter_user_id,
            registration_attempts.email_domain,
            registration_attempts.risk_score,
            registration_attempts.risk_reasons_json,
            registration_attempts.created_at AS attempt_created_at,
            inviter.display_name AS inviter_name,
            inviter.username AS inviter_username
        FROM users
        LEFT JOIN registration_attempts
            ON registration_attempts.id = (
                SELECT latest.id
                FROM registration_attempts latest
                WHERE latest.user_id = users.id
                ORDER BY latest.created_at DESC, latest.id DESC
                LIMIT 1
            )
        LEFT JOIN users inviter ON inviter.id = registration_attempts.inviter_user_id
        WHERE users.registration_status = 'pending' AND users.is_banned = 0
        ORDER BY registration_attempts.risk_score DESC, users.created_at ASC, users.id ASC
        LIMIT ?
        """,
        (int(limit),),
    )


def pending_registration_count():
    found = row("SELECT COUNT(*) AS count FROM users WHERE registration_status = 'pending' AND is_banned = 0")
    return int(found["count"] or 0) if found else 0


def registration_attempt_reasons(item):
    try:
        reasons = json.loads(str(row_value(item, "risk_reasons_json", "[]") or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        reasons = []
    return reasons if isinstance(reasons, list) else []


def admin_review_registration(admin_user_id, target_user_id, decision, note=""):
    target = row("SELECT * FROM users WHERE id = ?", (target_user_id,))
    if not target:
        raise ValueError("Account not found.")
    require_user_management(admin_user_id, target, CAP_MODERATE_USERS)
    if row_value(target, "registration_status", "active") != REGISTRATION_STATUS_PENDING:
        raise ValueError("Only pending accounts can be reviewed.")
    decision = sanitize_text_input(decision, max_length=20).strip().lower()
    if decision not in ("approve", "deny"):
        raise ValueError("Choose approve or deny.")
    timestamp = now_iso()
    clean_note = sanitize_text_input(note, max_length=1000).strip()
    approved = decision == "approve"
    with db() as conn:
        conn.execute(
            """
            UPDATE users
            SET registration_status = ?, registration_review_note = ?,
                registration_reviewed_by_user_id = ?, registration_reviewed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                REGISTRATION_STATUS_ACTIVE if approved else REGISTRATION_STATUS_DENIED,
                clean_note,
                int(admin_user_id),
                timestamp,
                timestamp,
                int(target_user_id),
            ),
        )
        conn.execute(
            """
            UPDATE registration_attempts
            SET status = ?, decision_note = ?, reviewed_by_user_id = ?, reviewed_at = ?
            WHERE user_id = ?
            """,
            (
                REGISTRATION_STATUS_ACTIVE if approved else REGISTRATION_STATUS_DENIED,
                clean_note,
                int(admin_user_id),
                timestamp,
                int(target_user_id),
            ),
        )
        if not approved:
            revoke_user_access_for_moderation(conn, target_user_id, revoke_invites=False)
            conn.execute(
                """
                UPDATE registration_attempts
                SET status = ?, decision_note = ?, reviewed_by_user_id = ?, reviewed_at = ?
                WHERE user_id = ?
                """,
                (
                    REGISTRATION_STATUS_DENIED,
                    clean_note,
                    int(admin_user_id),
                    timestamp,
                    int(target_user_id),
                ),
            )
        else:
            create_notification(
                target_user_id,
                "admin_notice",
                "Account approved",
                "Your account has been approved. You can now use BinderBridge.",
                "/",
                conn=conn,
            )
        log_admin_action(
            admin_user_id,
            "registration_approved" if approved else "registration_denied",
            target_user_id,
            "user",
            admin_audit_user_label(target),
            clean_note,
            conn=conn,
        )
    return row("SELECT * FROM users WHERE id = ?", (target_user_id,))


def revoke_user_access_for_moderation(conn, user_id, revoke_invites=True):
    timestamp = now_iso()
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (int(user_id),))
    conn.execute("DELETE FROM two_factor_challenges WHERE user_id = ?", (int(user_id),))
    conn.execute("DELETE FROM passkey_challenges WHERE user_id = ?", (int(user_id),))
    conn.execute(
        "UPDATE api_tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at = ''",
        (timestamp, int(user_id)),
    )
    conn.execute(
        "UPDATE webhook_endpoints SET is_active = 0, updated_at = ? WHERE user_id = ?",
        (timestamp, int(user_id)),
    )
    if revoke_invites:
        conn.execute(
            """
            UPDATE registration_invites
            SET status = 'revoked', updated_at = ?
            WHERE created_by_user_id = ? AND status = 'pending'
            """,
            (timestamp, int(user_id)),
        )
    conn.execute(
        "UPDATE registration_attempts SET status = 'banned' WHERE user_id = ?",
        (int(user_id),),
    )


__all__ = [
    "REGISTRATION_APPROVAL_MODE_KEY",
    "REGISTRATION_RISK_THRESHOLD_KEY",
    "REGISTRATION_SIGNAL_SECRET_KEY",
    "DEFAULT_REGISTRATION_APPROVAL_MODE",
    "DEFAULT_REGISTRATION_RISK_THRESHOLD",
    "REGISTRATION_APPROVAL_MODE_OPTIONS",
    "REGISTRATION_STATUS_ACTIVE",
    "REGISTRATION_STATUS_PENDING",
    "REGISTRATION_STATUS_DENIED",
    "REGISTRATION_STATUSES",
    "normalize_registration_approval_mode",
    "normalize_registration_risk_threshold",
    "registration_approval_mode",
    "registration_risk_threshold",
    "registration_moderation_settings",
    "set_registration_moderation_settings",
    "registration_signal_secret",
    "registration_signal_hash",
    "registration_email_domain",
    "registration_ip_subnet",
    "registration_signal_values",
    "registration_risk_assessment",
    "registration_status_for_new_account",
    "record_registration_attempt",
    "notify_registration_review_needed",
    "pending_registration_rows",
    "pending_registration_count",
    "registration_attempt_reasons",
    "admin_review_registration",
    "revoke_user_access_for_moderation",
]
