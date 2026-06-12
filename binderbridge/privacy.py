"""Central privacy policies and private share links for BinderBridge."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone


VISIBILITY_PRIVATE = "private"
VISIBILITY_TRUSTED = "trusted"
VISIBILITY_MEMBERS = "members"
VISIBILITY_LINK = "link"

VISIBILITY_OPTIONS = (
    (VISIBILITY_MEMBERS, "All members"),
    (VISIBILITY_TRUSTED, "Trusted members"),
    (VISIBILITY_LINK, "Share-link only"),
    (VISIBILITY_PRIVATE, "Private"),
)
VISIBILITY_LABELS = dict(VISIBILITY_OPTIONS)

VALUE_VISIBILITY_OPTIONS = (
    (VISIBILITY_MEMBERS, "All members"),
    (VISIBILITY_TRUSTED, "Trusted members"),
    (VISIBILITY_PRIVATE, "Only me"),
)
VALUE_VISIBILITY_LABELS = dict(VALUE_VISIBILITY_OPTIONS)

SHARE_TOKEN_PREFIX = "bbshare_"


def privacy_record_value(record, key, default=None):
    if record is None:
        return default
    try:
        return record[key]
    except (KeyError, IndexError, TypeError):
        return default


def normalize_visibility(value, default=VISIBILITY_MEMBERS, allow_link=True):
    normalized = str(value or "").strip().lower().replace("-", "_")
    allowed = set(VISIBILITY_LABELS)
    if not allow_link:
        allowed.discard(VISIBILITY_LINK)
    return normalized if normalized in allowed else default


def normalize_value_visibility(value, default=VISIBILITY_MEMBERS):
    normalized = normalize_visibility(value, default=default, allow_link=False)
    return normalized if normalized in VALUE_VISIBILITY_LABELS else default


def record_visibility(record):
    value = privacy_record_value(record, "visibility", "")
    if value:
        return normalize_visibility(value)
    return VISIBILITY_MEMBERS if int(privacy_record_value(record, "is_public", 1) or 0) else VISIBILITY_PRIVATE


def visibility_label(record_or_visibility):
    visibility = (
        normalize_visibility(record_or_visibility)
        if isinstance(record_or_visibility, str)
        else record_visibility(record_or_visibility)
    )
    return VISIBILITY_LABELS.get(visibility, "All members")


def visibility_to_public_flag(visibility):
    return 1 if normalize_visibility(visibility) == VISIBILITY_MEMBERS else 0


def form_visibility(form, default=VISIBILITY_MEMBERS, name="visibility"):
    if name in form:
        return normalize_visibility(form.get(name, [default])[0], default=default)
    legacy = str(form.get("is_public", [""])[0] or "").strip().lower()
    if legacy:
        return VISIBILITY_MEMBERS if legacy in ("1", "true", "on", "yes") else VISIBILITY_PRIVATE
    if form.get("_visibility_present", [""])[0] == "1":
        return VISIBILITY_PRIVATE
    return normalize_visibility(default)


def viewer_is_trusted(viewer):
    return bool(viewer and is_trusted_user(viewer))


def can_view_visibility(viewer, owner_id, visibility, via_share=False):
    if viewer and int(privacy_record_value(viewer, "id", 0) or 0) == int(owner_id or 0):
        return True
    visibility = normalize_visibility(visibility)
    if via_share:
        return True
    if visibility == VISIBILITY_MEMBERS:
        return bool(viewer)
    if visibility == VISIBILITY_TRUSTED:
        return viewer_is_trusted(viewer)
    return False


def can_view_record(viewer, owner_id, record, via_share=False):
    return can_view_visibility(viewer, owner_id, record_visibility(record), via_share=via_share)


def visibility_sql_for_user(viewer, visibility_column="visibility", owner_column="user_id"):
    viewer_id = int(privacy_record_value(viewer, "id", 0) or 0)
    if not viewer_id:
        return f"{visibility_column} = ?", [VISIBILITY_MEMBERS]
    clauses = [f"{owner_column} = ?", f"{visibility_column} = ?"]
    params = [viewer_id, VISIBILITY_MEMBERS]
    if viewer_is_trusted(viewer):
        clauses.append(f"{visibility_column} = ?")
        params.append(VISIBILITY_TRUSTED)
    return f"({' OR '.join(clauses)})", params


def visibility_sql_for_user_id(viewer_id, visibility_column="visibility", owner_column="user_id"):
    viewer = row("SELECT * FROM users WHERE id = ?", (viewer_id,)) if viewer_id else None
    return visibility_sql_for_user(viewer, visibility_column, owner_column)


def can_view_collection_values(viewer, owner, group=None, share_link=None):
    if share_link is not None:
        return bool(int(privacy_record_value(share_link, "show_values", 0) or 0))
    if group is not None and not int(privacy_record_value(group, "show_values", 1) or 0):
        return False
    owner_id = int(privacy_record_value(owner, "owner_id", privacy_record_value(owner, "id", 0)) or 0)
    if viewer and int(privacy_record_value(viewer, "id", 0) or 0) == owner_id:
        return True
    setting = normalize_value_visibility(
        privacy_record_value(owner, "owner_value_visibility", privacy_record_value(owner, "collection_value_visibility", VISIBILITY_MEMBERS))
    )
    return can_view_visibility(viewer, owner_id, setting)


def visible_price_pill(viewer, owner, item, group=None, share_link=None):
    return price_pill(item) if can_view_collection_values(viewer, owner, group=group, share_link=share_link) else ""


def share_token_hash(token):
    return hashlib.sha256(str(token or "").strip().encode("utf-8")).hexdigest()


def create_share_link(user_id, target_type, target_id, default_label, label="", expires_days=0, show_values=False, show_photos=True):
    clean_label = sanitize_text_input(label, max_length=80).strip() or default_label
    try:
        expires_days = max(0, min(3650, int(expires_days or 0)))
    except (TypeError, ValueError) as exc:
        raise ValueError("Expiration must be a number of days.") from exc
    token = SHARE_TOKEN_PREFIX + secrets.token_urlsafe(32)
    expires_at = ""
    if expires_days:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=expires_days)).replace(microsecond=0).isoformat()
    share_id = execute(
        """
        INSERT INTO privacy_share_links
            (user_id, target_type, target_id, token_hash, token_hint, label, show_values, show_photos,
             expires_at, revoked_at, last_accessed_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', ?)
        """,
        (
            user_id,
            target_type,
            target_id,
            share_token_hash(token),
            token[-8:],
            clean_label,
            1 if show_values else 0,
            1 if show_photos else 0,
            expires_at,
            now_iso(),
        ),
    )
    return token, row("SELECT * FROM privacy_share_links WHERE id = ?", (share_id,))


def create_group_share_link(user_id, group_id, label="", expires_days=0, show_values=False, show_photos=True):
    group = user_group(user_id, group_id)
    if not group:
        raise ValueError("Group not found.")
    return create_share_link(
        user_id, "group", group_id, f"{group['name']} share", label, expires_days, show_values, show_photos
    )


def create_collection_share_link(user_id, collection_item_id, label="", expires_days=0, show_values=False, show_photos=True):
    item = row("SELECT * FROM collection_items WHERE id = ? AND user_id = ?", (collection_item_id, user_id))
    if not item:
        raise ValueError("Collection card not found.")
    return create_share_link(
        user_id, "collection", collection_item_id, f"{item['card_name']} share", label, expires_days, show_values, show_photos
    )


def create_want_share_link(user_id, want_id, label="", expires_days=0, show_values=False):
    want = row("SELECT * FROM want_items WHERE id = ? AND user_id = ?", (want_id, user_id))
    if not want:
        raise ValueError("Wanted card not found.")
    if record_visibility(want) != VISIBILITY_LINK:
        raise ValueError("Set this wanted card to Share-link only before creating a private link.")
    return create_share_link(
        user_id, "want", want_id, f"{want['card_name']} want share", label, expires_days, show_values, False
    )


def share_link_rows(user_id, target_type, target_id):
    return rows(
        """
        SELECT *
        FROM privacy_share_links
        WHERE user_id = ? AND target_type = ? AND target_id = ?
        ORDER BY created_at DESC, id DESC
        """,
        (user_id, target_type, target_id),
    )


def group_share_link_rows(user_id, group_id):
    return share_link_rows(user_id, "group", group_id)


def collection_share_link_rows(user_id, collection_item_id):
    return share_link_rows(user_id, "collection", collection_item_id)


def want_share_link_rows(user_id, want_id):
    return share_link_rows(user_id, "want", want_id)


def revoke_share_link(user_id, target_type, target_id, share_id):
    with db() as conn:
        cursor = conn.execute(
            """
            UPDATE privacy_share_links
            SET revoked_at = ?
            WHERE id = ? AND user_id = ? AND target_type = ? AND target_id = ? AND revoked_at = ''
            """,
            (now_iso(), share_id, user_id, target_type, target_id),
        )
        return cursor.rowcount


def revoke_group_share_link(user_id, group_id, share_id):
    return revoke_share_link(user_id, "group", group_id, share_id)


def revoke_collection_share_link(user_id, collection_item_id, share_id):
    return revoke_share_link(user_id, "collection", collection_item_id, share_id)


def revoke_want_share_link(user_id, want_id, share_id):
    return revoke_share_link(user_id, "want", want_id, share_id)


def share_link_from_token(token, touch=True):
    token = str(token or "").strip()
    if not token.startswith(SHARE_TOKEN_PREFIX):
        return None
    link = row(
        """
        SELECT privacy_share_links.*, users.display_name AS owner_name, users.username AS owner_username
        FROM privacy_share_links
        JOIN users ON users.id = privacy_share_links.user_id
        WHERE privacy_share_links.token_hash = ?
            AND privacy_share_links.revoked_at = ''
            AND users.is_banned = 0
        """,
        (share_token_hash(token),),
    )
    if not link:
        return None
    expires_at = str(privacy_record_value(link, "expires_at", "") or "")
    if expires_at and expires_at <= now_iso():
        return None
    result = dict(link)
    if link["target_type"] == "group":
        target = row(
            """
            SELECT name AS group_name, description AS group_description, group_type,
                show_values AS group_show_values, show_photos AS group_show_photos
            FROM card_groups
            WHERE id = ? AND user_id = ?
            """,
            (link["target_id"], link["user_id"]),
        )
        if not target:
            return None
        result.update(dict(target))
    elif link["target_type"] == "collection":
        target = row(
            "SELECT * FROM collection_items WHERE id = ? AND user_id = ?",
            (link["target_id"], link["user_id"]),
        )
        if not target:
            return None
        result.update({f"item_{key}": target[key] for key in target.keys()})
    elif link["target_type"] == "want":
        target = row(
            "SELECT * FROM want_items WHERE id = ? AND user_id = ?",
            (link["target_id"], link["user_id"]),
        )
        if not target or record_visibility(target) != VISIBILITY_LINK:
            return None
        result.update({f"want_{key}": target[key] for key in target.keys()})
    else:
        return None
    if touch:
        execute("UPDATE privacy_share_links SET last_accessed_at = ? WHERE id = ?", (now_iso(), link["id"]))
    return result


def share_link_allows_photos(link):
    return bool(int(privacy_record_value(link, "show_photos", 0) or 0))


__all__ = [
    "VISIBILITY_PRIVATE",
    "VISIBILITY_TRUSTED",
    "VISIBILITY_MEMBERS",
    "VISIBILITY_LINK",
    "VISIBILITY_OPTIONS",
    "VISIBILITY_LABELS",
    "VALUE_VISIBILITY_OPTIONS",
    "VALUE_VISIBILITY_LABELS",
    "SHARE_TOKEN_PREFIX",
    "normalize_visibility",
    "normalize_value_visibility",
    "record_visibility",
    "visibility_label",
    "visibility_to_public_flag",
    "form_visibility",
    "viewer_is_trusted",
    "can_view_visibility",
    "can_view_record",
    "visibility_sql_for_user",
    "visibility_sql_for_user_id",
    "can_view_collection_values",
    "visible_price_pill",
    "share_token_hash",
    "create_share_link",
    "create_group_share_link",
    "create_collection_share_link",
    "create_want_share_link",
    "share_link_rows",
    "group_share_link_rows",
    "collection_share_link_rows",
    "want_share_link_rows",
    "revoke_share_link",
    "revoke_group_share_link",
    "revoke_collection_share_link",
    "revoke_want_share_link",
    "share_link_from_token",
    "share_link_allows_photos",
]
