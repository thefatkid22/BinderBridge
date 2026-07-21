"""Account profile and password services.

The app facade injects shared helpers/constants into this module at import time.
"""

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

__all__ = [
    "validate_username",
    "validate_email",
    "update_user_profile",
    "change_user_password",
]
