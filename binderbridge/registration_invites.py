"""Registration invite services.

The app facade injects shared helpers/constants into this module at import time.
"""

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
