"""Password recovery services.

The app facade injects shared helpers/constants into this module at import time.
"""

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

__all__ = [
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
]
