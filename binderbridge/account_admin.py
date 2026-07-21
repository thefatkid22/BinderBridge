"""Administrative account-management services.

The app facade injects shared helpers/constants into this module at import time.
"""

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

__all__ = [
    "require_admin",
    "admin_user_list",
    "require_user_management",
    "admin_set_user_ban",
    "admin_issue_user_password_recovery",
    "admin_reset_user_two_factor",
    "admin_set_user_role",
    "admin_update_notes",
]
