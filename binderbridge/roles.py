"""User roles, hierarchy, and capability checks for BinderBridge."""

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MODERATOR = "moderator"
ROLE_ORGANIZER = "organizer"
ROLE_MEMBER = "member"
ROLE_READ_ONLY = "read_only"

ROLE_OPTIONS = (
    (ROLE_OWNER, "Site Owner"),
    (ROLE_ADMIN, "Admin"),
    (ROLE_MODERATOR, "Moderator"),
    (ROLE_ORGANIZER, "Organizer"),
    (ROLE_MEMBER, "Member"),
    (ROLE_READ_ONLY, "Read-only"),
)
ROLE_LABELS = dict(ROLE_OPTIONS)
ROLE_LEVELS = {
    ROLE_READ_ONLY: 10,
    ROLE_MEMBER: 20,
    ROLE_ORGANIZER: 30,
    ROLE_MODERATOR: 35,
    ROLE_ADMIN: 40,
    ROLE_OWNER: 50,
}

CAP_ACCESS_ADMIN = "access_admin"
CAP_MANAGE_ROLES = "manage_roles"
CAP_MANAGE_USERS = "manage_users"
CAP_MODERATE_USERS = "moderate_users"
CAP_MODERATE_DISPUTES = "moderate_disputes"
CAP_VIEW_AUDIT_LOG = "view_audit_log"
CAP_MANAGE_INVITES = "manage_invites"
CAP_MANAGE_SETTINGS = "manage_settings"
CAP_MANAGE_MAINTENANCE = "manage_maintenance"
CAP_MANAGE_BACKUPS = "manage_backups"
CAP_WRITE_CONTENT = "write_content"
CAP_TRADE = "trade"

ROLE_CAPABILITIES = {
    ROLE_OWNER: {
        CAP_ACCESS_ADMIN,
        CAP_MANAGE_ROLES,
        CAP_MANAGE_USERS,
        CAP_MODERATE_USERS,
        CAP_MODERATE_DISPUTES,
        CAP_VIEW_AUDIT_LOG,
        CAP_MANAGE_INVITES,
        CAP_MANAGE_SETTINGS,
        CAP_MANAGE_MAINTENANCE,
        CAP_MANAGE_BACKUPS,
        CAP_WRITE_CONTENT,
        CAP_TRADE,
    },
    ROLE_ADMIN: {
        CAP_ACCESS_ADMIN,
        CAP_MANAGE_ROLES,
        CAP_MANAGE_USERS,
        CAP_MODERATE_USERS,
        CAP_MODERATE_DISPUTES,
        CAP_VIEW_AUDIT_LOG,
        CAP_MANAGE_INVITES,
        CAP_MANAGE_SETTINGS,
        CAP_MANAGE_MAINTENANCE,
        CAP_MANAGE_BACKUPS,
        CAP_WRITE_CONTENT,
        CAP_TRADE,
    },
    ROLE_MODERATOR: {
        CAP_ACCESS_ADMIN,
        CAP_MODERATE_USERS,
        CAP_MODERATE_DISPUTES,
        CAP_VIEW_AUDIT_LOG,
        CAP_WRITE_CONTENT,
        CAP_TRADE,
    },
    ROLE_ORGANIZER: {
        CAP_ACCESS_ADMIN,
        CAP_MANAGE_INVITES,
        CAP_WRITE_CONTENT,
        CAP_TRADE,
    },
    ROLE_MEMBER: {CAP_WRITE_CONTENT, CAP_TRADE},
    ROLE_READ_ONLY: set(),
}


def record_value(record, key, default=None):
    if record is None:
        return default
    try:
        return record[key]
    except (KeyError, IndexError, TypeError):
        return default


def normalize_user_role(value, is_admin=False):
    role = str(value or "").strip().lower().replace("-", "_")
    if role in ROLE_LABELS:
        return role
    return ROLE_ADMIN if is_admin else ROLE_MEMBER


def user_role(user):
    return normalize_user_role(record_value(user, "role", ""), bool(record_value(user, "is_admin", 0)))


def role_label(role_or_user):
    role = user_role(role_or_user) if not isinstance(role_or_user, str) else normalize_user_role(role_or_user)
    return ROLE_LABELS.get(role, "Member")


def role_level(role_or_user):
    role = user_role(role_or_user) if not isinstance(role_or_user, str) else normalize_user_role(role_or_user)
    return ROLE_LEVELS.get(role, ROLE_LEVELS[ROLE_MEMBER])


def user_has_capability(user, capability):
    if not user or record_value(user, "is_banned", 0):
        return False
    if record_value(user, "registration_status", "active") != "active":
        return False
    return capability in ROLE_CAPABILITIES.get(user_role(user), set())


def require_capability(user, capability):
    return user_has_capability(user, capability)


def user_can_manage_target(actor, target, capability=CAP_MODERATE_USERS):
    if not user_has_capability(actor, capability) or not target:
        return False
    if int(record_value(actor, "id", 0) or 0) == int(record_value(target, "id", 0) or 0):
        return False
    return role_level(actor) > role_level(target)


def assignable_roles_for_user(actor):
    role = user_role(actor)
    if role == ROLE_OWNER:
        return ROLE_OPTIONS
    if role == ROLE_ADMIN:
        return tuple(item for item in ROLE_OPTIONS if ROLE_LEVELS[item[0]] < ROLE_LEVELS[ROLE_ADMIN])
    return ()


def user_can_assign_role(actor, target, new_role):
    new_role = normalize_user_role(new_role)
    if not user_has_capability(actor, CAP_MANAGE_ROLES) or not target:
        return False
    if int(record_value(actor, "id", 0) or 0) == int(record_value(target, "id", 0) or 0):
        return False
    if user_role(actor) != ROLE_OWNER and not user_can_manage_target(actor, target, CAP_MANAGE_ROLES):
        return False
    return new_role in {key for key, _label in assignable_roles_for_user(actor)}


def role_sync_is_admin(role):
    return 1 if normalize_user_role(role) in (ROLE_OWNER, ROLE_ADMIN) else 0


def user_can_write_content(user):
    return user_has_capability(user, CAP_WRITE_CONTENT)


def user_can_trade(user):
    return user_has_capability(user, CAP_TRADE)


def user_can_mutate_path(user, path):
    if user_can_write_content(user):
        return True
    path = str(path or "")
    return path == "/logout" or path in (
        "/account/profile",
        "/account/password",
        "/account/2fa/start",
        "/account/2fa/enable",
        "/account/2fa/disable",
        "/account/2fa/recovery-codes",
        "/account/passkeys/register",
        "/notifications/read-all",
        "/notifications/delete-read",
        "/notifications/delete-all",
        "/saved-searches",
    ) or path.startswith("/account/passkeys/") or path.startswith("/notifications/") or path.startswith("/saved-searches/")


__all__ = [
    "ROLE_OWNER",
    "ROLE_ADMIN",
    "ROLE_MODERATOR",
    "ROLE_ORGANIZER",
    "ROLE_MEMBER",
    "ROLE_READ_ONLY",
    "ROLE_OPTIONS",
    "ROLE_LABELS",
    "ROLE_LEVELS",
    "ROLE_CAPABILITIES",
    "CAP_ACCESS_ADMIN",
    "CAP_MANAGE_ROLES",
    "CAP_MANAGE_USERS",
    "CAP_MODERATE_USERS",
    "CAP_MODERATE_DISPUTES",
    "CAP_VIEW_AUDIT_LOG",
    "CAP_MANAGE_INVITES",
    "CAP_MANAGE_SETTINGS",
    "CAP_MANAGE_MAINTENANCE",
    "CAP_MANAGE_BACKUPS",
    "CAP_WRITE_CONTENT",
    "CAP_TRADE",
    "normalize_user_role",
    "user_role",
    "role_label",
    "role_level",
    "user_has_capability",
    "require_capability",
    "user_can_manage_target",
    "assignable_roles_for_user",
    "user_can_assign_role",
    "role_sync_is_admin",
    "user_can_write_content",
    "user_can_trade",
    "user_can_mutate_path",
]
