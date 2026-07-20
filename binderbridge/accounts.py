"""Extracted BinderBridge feature code.

The app facade injects shared helpers/constants into this module at import time
so the legacy app.py public API remains compatible during the split.
"""

def validate_trade_sides(user, offered, requested):
    if not offered and not requested:
        raise ValueError("Choose at least one card for the trade.")
    if not offered or not requested:
        policy = one_way_trade_policy()
        if user_can_propose_one_way_trade(user):
            return
        if policy == "disabled":
            raise ValueError("One-directional trades are disabled by site policy. Select cards on both sides.")
        if policy == "admins":
            raise ValueError("One-directional trades can only be proposed by admins.")
        if policy == "anyone":
            raise ValueError("One-directional trades can only be proposed by active users.")
        raise ValueError("One-directional trades can only be proposed by trusted users.")


def validate_username(username):
    username = sanitize_text_input(username, max_length=40).strip()
    if len(username) < 3 or len(username) > 40 or not username.replace("_", "").replace("-", "").isalnum():
        raise ValueError("Use 3-40 letters, numbers, underscores, or hyphens for username.")
    return username


def validate_email(email):
    email = sanitize_text_input(email, max_length=254).strip()
    if not email:
        return ""
    if len(email) > 254 or "@" not in email or email.startswith("@") or email.endswith("@"):
        raise ValueError("Enter a valid email address.")
    local, domain = email.rsplit("@", 1)
    if not local or "." not in domain or any(ch.isspace() for ch in email):
        raise ValueError("Enter a valid email address.")
    return email


def update_user_profile(
    user_id,
    username,
    display_name,
    email,
    bio,
    public_email,
    preferred_price_source="",
    price_alerts_enabled=True,
    price_alert_threshold_percent="0",
    watchlist_alerts_enabled=True,
    email_trade_notifications_enabled=False,
    email_trade_offer_enabled=True,
    email_trade_comment_enabled=True,
    email_trade_counter_enabled=True,
    email_trade_status_enabled=True,
    notify_trade_offer_enabled=True,
    notify_trade_comment_enabled=True,
    notify_trade_counter_enabled=True,
    notify_trade_status_enabled=True,
    notify_import_complete_enabled=True,
    notify_admin_notice_enabled=True,
    email_price_alert_enabled=False,
    email_import_complete_enabled=False,
    email_admin_notice_enabled=False,
    email_digest_frequency="immediate",
    email_digest_time="09:00",
    email_digest_weekday=0,
    notification_timezone="UTC",
    quiet_hours_enabled=False,
    quiet_hours_start="22:00",
    quiet_hours_end="07:00",
    stale_trade_reminder_days=3,
    collection_value_visibility="members",
):
    username = validate_username(username)
    display_name = sanitize_text_input(display_name, max_length=80).strip()
    if not display_name:
        raise ValueError("Display name is required.")
    email = validate_email(email)
    bio = sanitize_text_input(bio, max_length=1000).strip()
    preferred_price_source = normalize_price_basis(preferred_price_source)
    price_alert_threshold_percent = normalize_price_alert_threshold(price_alert_threshold_percent)
    email_digest_frequency = normalize_email_digest_frequency(email_digest_frequency)
    email_digest_time = normalize_notification_time(email_digest_time, "Digest delivery time")
    email_digest_weekday = normalize_email_digest_weekday(email_digest_weekday)
    notification_timezone = normalize_notification_timezone(notification_timezone)
    quiet_hours_start = normalize_notification_time(quiet_hours_start, "Quiet hours start")
    quiet_hours_end = normalize_notification_time(quiet_hours_end, "Quiet hours end")
    stale_trade_reminder_days = normalize_stale_trade_reminder_days(stale_trade_reminder_days)
    collection_value_visibility = normalize_value_visibility(collection_value_visibility)
    try:
        execute(
            """
            UPDATE users
            SET username = ?, display_name = ?, email = ?, bio = ?, public_email = ?,
                preferred_price_source = ?, price_alerts_enabled = ?, price_alert_threshold_percent = ?,
                watchlist_alerts_enabled = ?, notify_trade_offer_enabled = ?,
                notify_trade_comment_enabled = ?, notify_trade_counter_enabled = ?,
                notify_trade_status_enabled = ?, notify_import_complete_enabled = ?,
                notify_admin_notice_enabled = ?, email_trade_notifications_enabled = ?,
                email_trade_offer_enabled = ?, email_trade_comment_enabled = ?,
                email_trade_counter_enabled = ?, email_trade_status_enabled = ?,
                email_price_alert_enabled = ?, email_import_complete_enabled = ?,
                email_admin_notice_enabled = ?, email_digest_frequency = ?,
                email_digest_time = ?, email_digest_weekday = ?, notification_timezone = ?,
                quiet_hours_enabled = ?, quiet_hours_start = ?, quiet_hours_end = ?,
                stale_trade_reminder_days = ?, collection_value_visibility = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                username,
                display_name,
                email,
                bio,
                1 if public_email else 0,
                preferred_price_source,
                1 if price_alerts_enabled else 0,
                price_alert_threshold_percent,
                1 if watchlist_alerts_enabled else 0,
                1 if notify_trade_offer_enabled else 0,
                1 if notify_trade_comment_enabled else 0,
                1 if notify_trade_counter_enabled else 0,
                1 if notify_trade_status_enabled else 0,
                1 if notify_import_complete_enabled else 0,
                1 if notify_admin_notice_enabled else 0,
                1 if email_trade_notifications_enabled else 0,
                1 if email_trade_offer_enabled else 0,
                1 if email_trade_comment_enabled else 0,
                1 if email_trade_counter_enabled else 0,
                1 if email_trade_status_enabled else 0,
                1 if email_price_alert_enabled else 0,
                1 if email_import_complete_enabled else 0,
                1 if email_admin_notice_enabled else 0,
                email_digest_frequency,
                email_digest_time,
                email_digest_weekday,
                notification_timezone,
                1 if quiet_hours_enabled else 0,
                quiet_hours_start,
                quiet_hours_end,
                stale_trade_reminder_days,
                collection_value_visibility,
                now_iso(),
                user_id,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("That username is already taken.") from exc


def change_user_password(
    user_id,
    current_password,
    new_password,
    confirm_password,
    keep_session_token=None,
    keep_api_token_id=None,
):
    found = row("SELECT * FROM users WHERE id = ?", (user_id,))
    if not found or not verify_password(current_password, found["password_hash"]):
        raise ValueError("Current password is incorrect.")
    if len(new_password) < 8:
        raise ValueError("New password must be at least 8 characters.")
    if new_password != confirm_password:
        raise ValueError("New password and confirmation do not match.")
    if verify_password(new_password, found["password_hash"]):
        raise ValueError("New password must be different from the current password.")
    timestamp = now_iso()
    with db() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (hash_password(new_password), timestamp, user_id),
        )
        if keep_session_token:
            browser_sessions = conn.execute(
                "DELETE FROM sessions WHERE user_id = ? AND token != ?",
                (user_id, keep_session_token),
            ).rowcount
        else:
            browser_sessions = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,)).rowcount
        if keep_api_token_id:
            android_sessions = conn.execute(
                """
                UPDATE api_tokens
                SET revoked_at = ?
                WHERE user_id = ?
                  AND credential_kind = 'android_session'
                  AND revoked_at = ''
                  AND id != ?
                """,
                (timestamp, user_id, keep_api_token_id),
            ).rowcount
        else:
            android_sessions = conn.execute(
                """
                UPDATE api_tokens
                SET revoked_at = ?
                WHERE user_id = ?
                  AND credential_kind = 'android_session'
                  AND revoked_at = ''
                """,
                (timestamp, user_id),
            ).rowcount
        conn.execute("DELETE FROM two_factor_challenges WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM passkey_challenges WHERE user_id = ?", (user_id,))
    return {
        "browser_sessions_revoked": browser_sessions,
        "android_sessions_revoked": android_sessions,
    }


def password_reset_token_hash(token):
    clean_token = sanitize_text_input(token, max_length=200).strip()
    return hashlib.sha256(clean_token.encode("utf-8")).hexdigest() if clean_token else ""


def password_reset_url(token, base_url=""):
    clean_token = sanitize_text_input(token, max_length=200).strip()
    base = sanitize_text_input(base_url, max_length=500).strip().rstrip("/")
    path = f"/password/reset?token={clean_token}"
    return f"{base}{path}" if base else path


def password_recovery_user(identifier):
    clean_identifier = sanitize_text_input(identifier, max_length=254).strip()
    if not clean_identifier:
        return None
    found = row(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE AND is_banned = 0",
        (clean_identifier,),
    )
    if found:
        return found
    matches = rows(
        "SELECT * FROM users WHERE email = ? COLLATE NOCASE AND email != '' AND is_banned = 0 ORDER BY id",
        (clean_identifier,),
    )
    return matches[0] if len(matches) == 1 else None


def expire_password_reset_tokens(conn=None):
    timestamp = now_iso()
    query = """
        UPDATE password_reset_tokens
        SET revoked_at = ?
        WHERE used_at = '' AND revoked_at = '' AND expires_at <= ?
    """
    if conn is not None:
        conn.execute(query, (timestamp, timestamp))
        return
    execute(query, (timestamp, timestamp))


def create_password_reset_token(user_id, base_url="", created_by_user_id=None, delivery_method="manual", clear_sessions=False):
    user = row("SELECT * FROM users WHERE id = ? AND is_banned = 0", (user_id,))
    if not user:
        raise ValueError("User not found.")
    token = secrets.token_urlsafe(32)
    token_hash = password_reset_token_hash(token)
    timestamp = now_iso()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=PASSWORD_RESET_EXPIRY_MINUTES)
    ).replace(microsecond=0).isoformat()
    with db() as conn:
        expire_password_reset_tokens(conn)
        conn.execute(
            """
            UPDATE password_reset_tokens
            SET revoked_at = ?
            WHERE user_id = ? AND used_at = '' AND revoked_at = ''
            """,
            (timestamp, user_id),
        )
        cursor = conn.execute(
            """
            INSERT INTO password_reset_tokens
                (user_id, token_hash, token_hint, created_by_user_id, delivery_method, expires_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                token_hash,
                token[:8],
                created_by_user_id,
                sanitize_text_input(delivery_method, max_length=20).strip() or "manual",
                expires_at,
                timestamp,
            ),
        )
        conn.execute(
            """
            UPDATE password_recovery_requests
            SET status = 'issued', handled_by_user_id = ?, handled_at = ?
            WHERE user_id = ? AND status = 'pending'
            """,
            (created_by_user_id, timestamp, user_id),
        )
        if clear_sessions:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM two_factor_challenges WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM passkey_challenges WHERE user_id = ?", (user_id,))
    return {
        "id": cursor.lastrowid,
        "user_id": user_id,
        "token": token,
        "link": password_reset_url(token, base_url),
        "expires_at": expires_at,
        "delivery_method": delivery_method,
    }


def mark_password_reset_token_sent(token_id):
    execute(
        "UPDATE password_reset_tokens SET sent_at = ?, delivery_method = 'email' WHERE id = ?",
        (now_iso(), int(token_id)),
    )


def revoke_password_reset_token(token_id):
    execute(
        "UPDATE password_reset_tokens SET revoked_at = ? WHERE id = ? AND used_at = ''",
        (now_iso(), int(token_id)),
    )


def password_reset_from_token(token):
    token_hash = password_reset_token_hash(token)
    if not token_hash:
        return None
    expire_password_reset_tokens()
    return row(
        """
        SELECT password_reset_tokens.*, users.username, users.display_name, users.email, users.password_hash
        FROM password_reset_tokens
        JOIN users ON users.id = password_reset_tokens.user_id
        WHERE password_reset_tokens.token_hash = ?
            AND password_reset_tokens.used_at = ''
            AND password_reset_tokens.revoked_at = ''
            AND password_reset_tokens.expires_at > ?
            AND users.is_banned = 0
        """,
        (token_hash, now_iso()),
    )


def send_password_recovery_email(user, reset_link):
    return send_email_message(
        row_value(user, "email", ""),
        f"Reset your {APP_NAME} password",
        "\n".join(
            [
                f"A password reset was requested for your {APP_NAME} account.",
                "",
                "Reset your password using this one-time link:",
                reset_link,
                "",
                f"This link expires in {PASSWORD_RESET_EXPIRY_MINUTES} minutes.",
                "If you did not request this reset, you can ignore this email.",
                "Two-factor authentication remains enabled after a password reset.",
            ]
        ),
    )


def create_pending_password_recovery_request(user_id):
    timestamp = now_iso()
    with db() as conn:
        existing = conn.execute(
            """
            SELECT id FROM password_recovery_requests
            WHERE user_id = ? AND status = 'pending'
            ORDER BY requested_at DESC LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        if existing:
            return existing["id"], False
        cursor = conn.execute(
            """
            INSERT INTO password_recovery_requests (user_id, status, requested_at)
            VALUES (?, 'pending', ?)
            """,
            (user_id, timestamp),
        )
        admins = conn.execute(
            "SELECT id FROM users WHERE role IN ('owner', 'admin') AND is_banned = 0"
        ).fetchall()
        for admin in admins:
            create_notification(
                admin["id"],
                "admin_notice",
                "Password recovery assistance requested",
                "A user needs an administrator-issued password recovery link.",
                "/admin",
                conn=conn,
            )
        return cursor.lastrowid, True


def request_password_recovery(identifier, base_url=""):
    user = password_recovery_user(identifier)
    result = {"matched": bool(user), "delivery": "none", "sent": False}
    if not user:
        return result
    if email_delivery_configured() and row_value(user, "email", ""):
        reset = create_password_reset_token(user["id"], base_url, delivery_method="email")
        sent, message = send_password_recovery_email(user, reset["link"])
        if sent:
            mark_password_reset_token_sent(reset["id"])
            result.update({"delivery": "email", "sent": True, "email_status": message})
            return result
        revoke_password_reset_token(reset["id"])
        result["email_status"] = message
    create_pending_password_recovery_request(user["id"])
    result["delivery"] = "admin"
    return result


def complete_password_reset(token, new_password, confirm_password):
    reset = password_reset_from_token(token)
    if not reset:
        raise ValueError("That password reset link is invalid, expired, or already used.")
    if len(new_password) < 8:
        raise ValueError("New password must be at least 8 characters.")
    if new_password != confirm_password:
        raise ValueError("New password and confirmation do not match.")
    if verify_password(new_password, reset["password_hash"]):
        raise ValueError("New password must be different from the current password.")
    timestamp = now_iso()
    with db() as conn:
        consumed = conn.execute(
            """
            UPDATE password_reset_tokens
            SET used_at = ?
            WHERE id = ? AND used_at = '' AND revoked_at = '' AND expires_at > ?
            """,
            (timestamp, reset["id"], timestamp),
        )
        if consumed.rowcount != 1:
            raise ValueError("That password reset link is invalid, expired, or already used.")
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (hash_password(new_password), timestamp, reset["user_id"]),
        )
        conn.execute(
            """
            UPDATE password_reset_tokens
            SET revoked_at = ?
            WHERE user_id = ? AND id != ? AND used_at = '' AND revoked_at = ''
            """,
            (timestamp, reset["user_id"], reset["id"]),
        )
        conn.execute(
            """
            UPDATE password_recovery_requests
            SET status = 'completed', handled_at = ?
            WHERE user_id = ? AND status IN ('pending', 'issued')
            """,
            (timestamp, reset["user_id"]),
        )
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (reset["user_id"],))
        conn.execute(
            """
            UPDATE api_tokens
            SET revoked_at = ?
            WHERE user_id = ? AND credential_kind = 'android_session' AND revoked_at = ''
            """,
            (timestamp, reset["user_id"]),
        )
        conn.execute("DELETE FROM two_factor_challenges WHERE user_id = ?", (reset["user_id"],))
        conn.execute("DELETE FROM passkey_challenges WHERE user_id = ?", (reset["user_id"],))
        if reset["created_by_user_id"]:
            log_admin_action(
                reset["created_by_user_id"],
                "password_recovery_completed",
                reset["user_id"],
                "user",
                f"{reset['display_name']} (@{reset['username']})",
                "Administrator-issued password recovery link was completed. Active sessions were cleared.",
                conn=conn,
            )
    return reset["user_id"]


def require_admin(user):
    return require_capability(user, CAP_ACCESS_ADMIN)


def admin_user_list():
    return rows(
        """
        SELECT
            users.*,
            (SELECT COUNT(*) FROM collection_items WHERE user_id = users.id) AS collection_count,
            (SELECT COUNT(*) FROM want_items WHERE user_id = users.id) AS want_count,
            (SELECT COUNT(*) FROM trades WHERE proposer_id = users.id OR recipient_id = users.id) AS trade_count,
            (SELECT COUNT(*) FROM trades WHERE status = 'completed' AND (proposer_id = users.id OR recipient_id = users.id)) AS completed_trade_count,
            (SELECT COUNT(*) FROM password_recovery_requests WHERE user_id = users.id AND status = 'pending') AS pending_recovery_count,
            (SELECT MAX(requested_at) FROM password_recovery_requests WHERE user_id = users.id AND status = 'pending') AS recovery_requested_at
        FROM users
        ORDER BY is_banned DESC,
            CASE registration_status WHEN 'pending' THEN 3 WHEN 'denied' THEN 2 ELSE 1 END DESC,
            CASE role
                WHEN 'owner' THEN 50 WHEN 'admin' THEN 40 WHEN 'moderator' THEN 35
                WHEN 'organizer' THEN 30 WHEN 'member' THEN 20 ELSE 10
            END DESC,
            display_name COLLATE NOCASE
        """
    )


def require_user_management(actor_user_id, target, capability):
    actor = row("SELECT * FROM users WHERE id = ?", (actor_user_id,))
    if actor and target and int(actor["id"]) == int(target["id"]):
        raise ValueError("You cannot manage your own account from the staff panel.")
    if not user_can_manage_target(actor, target, capability):
        raise ValueError("Your role cannot manage that account.")
    return actor


def admin_set_user_ban(admin_user_id, target_user_id, should_ban, reason=""):
    target = row("SELECT * FROM users WHERE id = ?", (target_user_id,))
    if not target:
        raise ValueError("User not found.")
    require_user_management(admin_user_id, target, CAP_MODERATE_USERS)
    timestamp = now_iso()
    clean_reason = sanitize_text_input(reason, max_length=1000).strip() if should_ban else ""
    with db() as conn:
        conn.execute(
            """
            UPDATE users
            SET is_banned = ?, ban_reason = ?, banned_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (1 if should_ban else 0, clean_reason, timestamp if should_ban else "", timestamp, target_user_id),
        )
        if should_ban:
            revoke_user_access_for_moderation(conn, target_user_id, revoke_invites=True)
    log_admin_action(
        admin_user_id,
        "user_banned" if should_ban else "user_unbanned",
        target_user_id,
        "user",
        admin_audit_user_label(target),
        clean_reason,
    )


def admin_issue_user_password_recovery(admin_user_id, target_user_id, admin_password, base_url=""):
    target = row("SELECT * FROM users WHERE id = ?", (target_user_id,))
    if not target:
        raise ValueError("User not found.")
    actor = require_user_management(admin_user_id, target, CAP_MANAGE_USERS)
    if not verify_password(admin_password, actor["password_hash"]):
        raise ValueError("Your current password is incorrect.")
    delivery_method = "email" if email_delivery_configured() and row_value(target, "email", "") else "manual"
    result = create_password_reset_token(
        target_user_id,
        base_url,
        created_by_user_id=admin_user_id,
        delivery_method=delivery_method,
        clear_sessions=True,
    )
    result["sent"] = False
    result["email_status"] = ""
    if delivery_method == "email":
        sent, message = send_password_recovery_email(target, result["link"])
        result["sent"] = sent
        result["email_status"] = message
        if sent:
            mark_password_reset_token_sent(result["id"])
    log_admin_action(
        admin_user_id,
        "password_recovery_issued",
        target_user_id,
        "user",
        admin_audit_user_label(target),
        (
            "Password recovery link emailed and active sessions cleared."
            if result["sent"]
            else "Manual password recovery link issued and active sessions cleared."
        ),
    )
    return result


def admin_reset_user_two_factor(admin_user_id, target_user_id):
    target = row("SELECT * FROM users WHERE id = ?", (target_user_id,))
    if not target:
        raise ValueError("User not found.")
    require_user_management(admin_user_id, target, CAP_MANAGE_USERS)
    with db() as conn:
        conn.execute(
            """
            UPDATE users
            SET totp_secret = '', totp_enabled = 0, totp_recovery_codes = '', totp_enabled_at = '', updated_at = ?
            WHERE id = ?
            """,
            (now_iso(), target_user_id),
        )
        conn.execute("DELETE FROM two_factor_challenges WHERE user_id = ?", (target_user_id,))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (target_user_id,))
        log_admin_action(
            admin_user_id,
            "two_factor_reset",
            target_user_id,
            "user",
            admin_audit_user_label(target),
            "Two-factor authentication reset and active sessions cleared.",
            conn=conn,
        )


def admin_set_user_role(admin_user_id, target_user_id, role):
    actor = row("SELECT * FROM users WHERE id = ?", (admin_user_id,))
    target = row("SELECT * FROM users WHERE id = ?", (target_user_id,))
    if not target:
        raise ValueError("User not found.")
    if isinstance(role, bool):
        role = ROLE_ADMIN if role else ROLE_MEMBER
    role = str(role or "").strip().lower().replace("-", "_")
    if role not in ROLE_LABELS:
        raise ValueError("Choose a valid user role.")
    if not user_can_assign_role(actor, target, role):
        raise ValueError("Your role cannot assign that role to this account.")
    if user_role(target) == ROLE_OWNER and role != ROLE_OWNER:
        owner_count = row("SELECT COUNT(*) AS count FROM users WHERE role = 'owner'")["count"]
        if owner_count <= 1:
            raise ValueError("At least one owner account is required.")
    previous_role = user_role(target)
    execute(
        "UPDATE users SET role = ?, is_admin = ?, updated_at = ? WHERE id = ?",
        (role, role_sync_is_admin(role), now_iso(), target_user_id),
    )
    log_admin_action(
        admin_user_id,
        "admin_granted" if role == ROLE_ADMIN else "admin_removed" if previous_role == ROLE_ADMIN else "user_role_updated",
        target_user_id,
        "user",
        admin_audit_user_label(target),
        f"Role changed from {role_label(previous_role)} to {role_label(role)}.",
    )


def admin_update_notes(target_user_id, notes, admin_user_id=None):
    target = row("SELECT * FROM users WHERE id = ?", (target_user_id,))
    if not target:
        raise ValueError("User not found.")
    if admin_user_id:
        require_user_management(admin_user_id, target, CAP_MODERATE_USERS)
    execute("UPDATE users SET admin_notes = ?, updated_at = ? WHERE id = ?", (sanitize_text_input(notes, max_length=2000).strip(), now_iso(), target_user_id))
    if admin_user_id:
        log_admin_action(
            admin_user_id,
            "admin_notes_updated",
            target_user_id,
            "user",
            admin_audit_user_label(target),
            "Private admin notes updated.",
        )


def invite_only_registration_enabled():
    return get_setting(INVITE_ONLY_REGISTRATION_KEY, "0") == "1"


def registration_requires_invite():
    user_count = row("SELECT COUNT(*) AS count FROM users")["count"]
    return bool(user_count and invite_only_registration_enabled())


def set_invite_only_registration(enabled):
    set_setting(INVITE_ONLY_REGISTRATION_KEY, "1" if enabled else "0")
    return invite_only_registration_enabled()


def registration_invite_token_hash(token):
    token = sanitize_text_input(token, max_length=200).strip()
    if not token:
        return ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def registration_invite_url(token, base_url=""):
    token = sanitize_text_input(token, max_length=200).strip()
    base = sanitize_text_input(base_url, max_length=500).strip().rstrip("/")
    path = f"/register?invite={token}"
    return f"{base}{path}" if base else path


def expire_registration_invites(conn=None):
    timestamp = now_iso()
    query = """
        UPDATE registration_invites
        SET status = 'expired', updated_at = ?
        WHERE status = 'pending' AND expires_at <= ?
    """
    if conn is not None:
        conn.execute(query, (timestamp, timestamp))
        return
    execute(query, (timestamp, timestamp))


def registration_invite_from_token(token, email=""):
    token_hash = registration_invite_token_hash(token)
    if not token_hash:
        return None
    expire_registration_invites()
    invite = row(
        """
        SELECT registration_invites.*, creator.display_name AS created_by_name, accepted.display_name AS accepted_by_name
        FROM registration_invites
        LEFT JOIN users creator ON creator.id = registration_invites.created_by_user_id
        LEFT JOIN users accepted ON accepted.id = registration_invites.accepted_by_user_id
        WHERE registration_invites.token_hash = ?
        """,
        (token_hash,),
    )
    if not invite or invite["status"] != "pending":
        return None
    expected_email = validate_email(email) if email else ""
    if expected_email and invite["email"].lower() != expected_email.lower():
        return None
    return invite


def smtp_bool(*names, default=False):
    return config_bool(*names, default=default)


def smtp_invites_configured():
    return bool(config_str("BINDERBRIDGE_SMTP_HOST", "SMTP_HOST", default="", section="smtp", key="host"))


def send_registration_invite_email(email, invite_link):
    host = config_str("BINDERBRIDGE_SMTP_HOST", "SMTP_HOST", default="", section="smtp", key="host")
    if not host:
        return False, "SMTP is not configured. Copy the invite link and send it manually."
    port = config_int("BINDERBRIDGE_SMTP_PORT", "SMTP_PORT", default=587, section="smtp", key="port")
    username = config_str("BINDERBRIDGE_SMTP_USERNAME", "SMTP_USERNAME", default="", section="smtp", key="username")
    password = config_str("BINDERBRIDGE_SMTP_PASSWORD", "SMTP_PASSWORD", default="", section="smtp", key="password")
    from_address = config_str("BINDERBRIDGE_SMTP_FROM", "SMTP_FROM", default=username or "noreply@localhost", section="smtp", key="from_address")
    use_ssl = config_bool("BINDERBRIDGE_SMTP_SSL", "SMTP_SSL", default=False, section="smtp", key="ssl")
    use_starttls = config_bool("BINDERBRIDGE_SMTP_TLS", "SMTP_TLS", default=not use_ssl, section="smtp", key="tls")
    message = EmailMessage()
    message["Subject"] = f"You're invited to {APP_NAME}"
    message["From"] = from_address
    message["To"] = email
    message.set_content(
        "\n".join(
            [
                f"You have been invited to join {APP_NAME}.",
                "",
                "Create your account here:",
                invite_link,
                "",
                f"This invite expires in {REGISTRATION_INVITE_EXPIRY_DAYS} day{'s' if REGISTRATION_INVITE_EXPIRY_DAYS != 1 else ''}.",
            ]
        )
    )
    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
        with server:
            if use_starttls and not use_ssl:
                server.starttls()
            if username or password:
                server.login(username, password)
            server.send_message(message)
    except Exception as exc:
        return False, f"Invite was created, but email could not be sent: {exc}"
    return True, "Invite email sent."


def create_registration_invite(admin_user_id, email, base_url=""):
    email = validate_email(email)
    if not email:
        raise ValueError("Email is required.")
    existing_user = row("SELECT id FROM users WHERE email = ? COLLATE NOCASE", (email,))
    if existing_user:
        raise ValueError("That email is already attached to an account.")
    token = secrets.token_urlsafe(32)
    token_hash = registration_invite_token_hash(token)
    timestamp = now_iso()
    expires_at = (datetime.now(timezone.utc) + timedelta(days=REGISTRATION_INVITE_EXPIRY_DAYS)).replace(microsecond=0).isoformat()
    invite_link = registration_invite_url(token, base_url)
    sent, email_status = send_registration_invite_email(email, invite_link)
    invite_id = execute(
        """
        INSERT INTO registration_invites
            (email, token_hash, token_hint, created_by_user_id, status, sent_at, expires_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?)
        """,
        (email, token_hash, token[:8], admin_user_id, timestamp if sent else "", expires_at, timestamp, timestamp),
    )
    log_admin_action(
        admin_user_id,
        "invite_created",
        None,
        "invite",
        email,
        "Invite email sent." if sent else "Invite created for manual delivery.",
    )
    return {
        "id": invite_id,
        "email": email,
        "token": token,
        "link": invite_link,
        "sent": sent,
        "email_status": email_status,
        "expires_at": expires_at,
    }


def registration_invite_rows(limit=20):
    expire_registration_invites()
    return rows(
        """
        SELECT registration_invites.*, creator.display_name AS created_by_name, accepted.display_name AS accepted_by_name
        FROM registration_invites
        LEFT JOIN users creator ON creator.id = registration_invites.created_by_user_id
        LEFT JOIN users accepted ON accepted.id = registration_invites.accepted_by_user_id
        ORDER BY registration_invites.created_at DESC, registration_invites.id DESC
        LIMIT ?
        """,
        (limit,),
    )


def revoke_registration_invite(admin_user_id, invite_id):
    timestamp = now_iso()
    with db() as conn:
        invite = conn.execute("SELECT * FROM registration_invites WHERE id = ?", (invite_id,)).fetchone()
        if not invite:
            raise ValueError("Invite not found.")
        if invite["status"] != "pending":
            raise ValueError("Only pending invites can be revoked.")
        conn.execute(
            "UPDATE registration_invites SET status = 'revoked', updated_at = ? WHERE id = ?",
            (timestamp, invite_id),
        )
        log_admin_action(
            admin_user_id,
            "invite_revoked",
            None,
            "invite",
            invite["email"],
            f"Invite #{invite_id} revoked.",
            conn=conn,
        )
    return True


def delete_registration_invite(admin_user_id, invite_id):
    try:
        invite_id = int(invite_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invite not found.") from exc
    with db() as conn:
        invite = conn.execute("SELECT * FROM registration_invites WHERE id = ?", (invite_id,)).fetchone()
        if not invite:
            raise ValueError("Invite not found.")
        if invite["status"] == "pending":
            raise ValueError("Pending invites can be revoked, but not deleted.")
        conn.execute("DELETE FROM registration_invites WHERE id = ?", (invite_id,))
        log_admin_action(
            admin_user_id,
            "invite_deleted",
            None,
            "invite",
            invite["email"],
            f"Invite #{invite_id} ({invite['status']}) deleted.",
            conn=conn,
        )
    return True


def accept_registration_invite(token, user_id):
    token_hash = registration_invite_token_hash(token)
    if not token_hash:
        raise ValueError("Invite token is required.")
    timestamp = now_iso()
    with db() as conn:
        expire_registration_invites(conn)
        invite = conn.execute(
            "SELECT * FROM registration_invites WHERE token_hash = ?",
            (token_hash,),
        ).fetchone()
        if not invite or invite["status"] != "pending":
            raise ValueError("Invite is no longer available.")
        conn.execute(
            """
            UPDATE registration_invites
            SET status = 'accepted', accepted_by_user_id = ?, accepted_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (user_id, timestamp, timestamp, invite["id"]),
        )
    return True


__all__ = [
    "validate_trade_sides",
    "validate_username",
    "validate_email",
    "update_user_profile",
    "change_user_password",
    "password_reset_token_hash",
    "password_reset_url",
    "password_recovery_user",
    "expire_password_reset_tokens",
    "create_password_reset_token",
    "mark_password_reset_token_sent",
    "revoke_password_reset_token",
    "password_reset_from_token",
    "send_password_recovery_email",
    "create_pending_password_recovery_request",
    "request_password_recovery",
    "complete_password_reset",
    "require_admin",
    "require_user_management",
    "admin_user_list",
    "admin_set_user_ban",
    "admin_issue_user_password_recovery",
    "admin_reset_user_two_factor",
    "admin_set_user_role",
    "admin_update_notes",
    "invite_only_registration_enabled",
    "registration_requires_invite",
    "set_invite_only_registration",
    "registration_invite_token_hash",
    "registration_invite_url",
    "expire_registration_invites",
    "registration_invite_from_token",
    "smtp_invites_configured",
    "send_registration_invite_email",
    "create_registration_invite",
    "registration_invite_rows",
    "revoke_registration_invite",
    "delete_registration_invite",
    "accept_registration_invite",
]
