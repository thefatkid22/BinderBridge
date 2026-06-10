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
                stale_trade_reminder_days = ?, updated_at = ?
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
                now_iso(),
                user_id,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("That username is already taken.") from exc


def change_user_password(user_id, current_password, new_password, confirm_password, keep_session_token=None):
    found = row("SELECT * FROM users WHERE id = ?", (user_id,))
    if not found or not verify_password(current_password, found["password_hash"]):
        raise ValueError("Current password is incorrect.")
    if len(new_password) < 8:
        raise ValueError("New password must be at least 8 characters.")
    if new_password != confirm_password:
        raise ValueError("New password and confirmation do not match.")
    with db() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (hash_password(new_password), now_iso(), user_id),
        )
        if keep_session_token:
            conn.execute("DELETE FROM sessions WHERE user_id = ? AND token != ?", (user_id, keep_session_token))
        else:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


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
            (SELECT COUNT(*) FROM trades WHERE status = 'completed' AND (proposer_id = users.id OR recipient_id = users.id)) AS completed_trade_count
        FROM users
        ORDER BY is_banned DESC,
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
    execute(
        """
        UPDATE users
        SET is_banned = ?, ban_reason = ?, banned_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (1 if should_ban else 0, sanitize_text_input(reason, max_length=1000).strip() if should_ban else "", now_iso() if should_ban else "", now_iso(), target_user_id),
    )
    if should_ban:
        execute("DELETE FROM sessions WHERE user_id = ?", (target_user_id,))
    log_admin_action(
        admin_user_id,
        "user_banned" if should_ban else "user_unbanned",
        target_user_id,
        "user",
        admin_audit_user_label(target),
        sanitize_text_input(reason, max_length=1000).strip() if should_ban else "",
    )


def admin_reset_user_password(admin_user_id, target_user_id, new_password, confirm_password):
    target = row("SELECT * FROM users WHERE id = ?", (target_user_id,))
    if not target:
        raise ValueError("User not found.")
    require_user_management(admin_user_id, target, CAP_MANAGE_USERS)
    if len(new_password) < 8:
        raise ValueError("New password must be at least 8 characters.")
    if new_password != confirm_password:
        raise ValueError("New password and confirmation do not match.")
    with db() as conn:
        conn.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?", (hash_password(new_password), now_iso(), target_user_id))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (target_user_id,))
        log_admin_action(
            admin_user_id,
            "password_reset",
            target_user_id,
            "user",
            admin_audit_user_label(target),
            "Password reset and active sessions cleared.",
            conn=conn,
        )


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
    "require_admin",
    "require_user_management",
    "admin_user_list",
    "admin_set_user_ban",
    "admin_reset_user_password",
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
    "accept_registration_invite",
]
