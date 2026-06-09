"""User-owned export helpers for BinderBridge."""

import csv
import io
import json
import re
from datetime import datetime, timezone


COLLECTION_EXPORT_FIELDS = [
    "game",
    "card_name",
    "quantity",
    "quantity_for_trade",
    "set_name",
    "set_code",
    "collector_number",
    "finish",
    "condition",
    "language",
    "scryfall_id",
    "type_line",
    "rarity",
    "colors",
    "color_identity",
    "price_usd",
    "price_source",
    "scryfall_uri",
    "notes",
    "is_public",
    "created_at",
    "updated_at",
]

WANT_EXPORT_FIELDS = [
    "game",
    "card_name",
    "desired_quantity",
    "set_name",
    "set_code",
    "collector_number",
    "condition",
    "finish",
    "language",
    "scryfall_id",
    "type_line",
    "rarity",
    "colors",
    "color_identity",
    "price_usd",
    "price_source",
    "scryfall_uri",
    "notes",
    "is_public",
    "created_at",
    "updated_at",
]

GROUP_COLLECTION_EXPORT_FIELDS = [
    "group_name",
    "group_type",
    "group_quantity",
    "collection_item_id",
    *COLLECTION_EXPORT_FIELDS,
]

GROUP_WANT_EXPORT_FIELDS = [
    "group_name",
    "group_type",
    "want_item_id",
    *WANT_EXPORT_FIELDS,
]

ACCOUNT_EXPORT_USER_FIELDS = [
    "id",
    "username",
    "email",
    "display_name",
    "bio",
    "public_email",
    "preferred_price_source",
    "price_alerts_enabled",
    "price_alert_threshold_percent",
    "watchlist_alerts_enabled",
    "notify_trade_offer_enabled",
    "notify_trade_comment_enabled",
    "notify_trade_counter_enabled",
    "notify_trade_status_enabled",
    "notify_import_complete_enabled",
    "notify_admin_notice_enabled",
    "email_trade_notifications_enabled",
    "email_trade_offer_enabled",
    "email_trade_comment_enabled",
    "email_trade_counter_enabled",
    "email_trade_status_enabled",
    "email_price_alert_enabled",
    "email_import_complete_enabled",
    "email_admin_notice_enabled",
    "email_digest_frequency",
    "email_digest_time",
    "email_digest_weekday",
    "notification_timezone",
    "quiet_hours_enabled",
    "quiet_hours_start",
    "quiet_hours_end",
    "stale_trade_reminder_days",
    "created_at",
    "updated_at",
]


def export_stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def safe_export_name(value, fallback="export"):
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "").strip()).strip("-._")
    return (cleaned or fallback).lower()[:80]


def record_dict(record):
    return dict(record) if record is not None else {}


def csv_export_bytes(fieldnames, records):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for record in records:
        data = record_dict(record)
        writer.writerow({field: data.get(field, "") for field in fieldnames})
    return output.getvalue().encode("utf-8-sig")


def json_export_bytes(data):
    return json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")


def export_collection_rows(user_id, query=None):
    filters = collection_filter_values(query or {})
    where, params = collection_where(user_id, filters)
    return rows(
        f"""
        SELECT *
        FROM collection_items
        WHERE {' AND '.join(where)}
        ORDER BY card_name COLLATE NOCASE, set_name COLLATE NOCASE, collector_number COLLATE NOCASE
        """,
        params,
    )


def export_collection_csv(user_id, query=None):
    data = csv_export_bytes(COLLECTION_EXPORT_FIELDS, export_collection_rows(user_id, query))
    return f"binderbridge-collection-{export_stamp()}.csv", data


def export_wants_rows(user_id):
    return rows(
        """
        SELECT *
        FROM want_items
        WHERE user_id = ?
        ORDER BY card_name COLLATE NOCASE, set_name COLLATE NOCASE, collector_number COLLATE NOCASE
        """,
        (user_id,),
    )


def export_wants_csv(user_id):
    data = csv_export_bytes(WANT_EXPORT_FIELDS, export_wants_rows(user_id))
    return f"binderbridge-wants-{export_stamp()}.csv", data


def export_group_collection_rows(group):
    items = collection_group_items(group["id"])
    records = []
    for item in items:
        data = record_dict(item)
        data["group_name"] = group["name"]
        data["group_type"] = group["group_type"]
        data["collection_item_id"] = item["id"]
        records.append(data)
    return records


def export_group_want_rows(group):
    items = wishlist_group_items(group["id"])
    records = []
    for item in items:
        data = record_dict(item)
        data["group_name"] = group["name"]
        data["group_type"] = group["group_type"]
        data["want_item_id"] = item["id"]
        records.append(data)
    return records


def export_group_csv(user_id, group_id):
    group = user_group(user_id, group_id)
    if not group:
        raise ValueError("Group not found.")
    if group["group_type"] == "wishlist":
        fieldnames = GROUP_WANT_EXPORT_FIELDS
        records = export_group_want_rows(group)
    else:
        fieldnames = GROUP_COLLECTION_EXPORT_FIELDS
        records = export_group_collection_rows(group)
    filename = f"binderbridge-{safe_export_name(group['group_type'])}-{safe_export_name(group['name'], 'group')}-{export_stamp()}.csv"
    return filename, csv_export_bytes(fieldnames, records)


def account_group_exports(user_id):
    groups = rows(
        """
        SELECT *
        FROM card_groups
        WHERE user_id = ?
        ORDER BY group_type, name COLLATE NOCASE
        """,
        (user_id,),
    )
    exported = []
    for group in groups:
        data = record_dict(group)
        if group["group_type"] == "wishlist":
            data["items"] = [record_dict(item) for item in export_group_want_rows(group)]
        else:
            data["items"] = [record_dict(item) for item in export_group_collection_rows(group)]
        exported.append(data)
    return exported


def account_trade_exports(user_id):
    trades = rows(
        """
        SELECT *
        FROM trades
        WHERE proposer_id = ? OR recipient_id = ?
        ORDER BY updated_at DESC, id DESC
        """,
        (user_id, user_id),
    )
    exported = []
    for trade in trades:
        data = record_dict(trade)
        data["items"] = [
            record_dict(item)
            for item in rows("SELECT * FROM trade_items WHERE trade_id = ? ORDER BY side, card_name", (trade["id"],))
        ]
        data["comments"] = [
            record_dict(comment)
            for comment in rows("SELECT * FROM trade_comments WHERE trade_id = ? ORDER BY created_at, id", (trade["id"],))
        ]
        data["feedback"] = [
            record_dict(feedback)
            for feedback in rows("SELECT * FROM trade_feedback WHERE trade_id = ? ORDER BY updated_at, id", (trade["id"],))
        ]
        data["disputes"] = [
            record_dict(dispute)
            for dispute in rows("SELECT * FROM trade_disputes WHERE trade_id = ? ORDER BY created_at, id", (trade["id"],))
        ]
        exported.append(data)
    return exported


def export_account_data(user_id):
    user = row("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user:
        raise ValueError("Account not found.")
    account = {field: row_value(user, field, "") for field in ACCOUNT_EXPORT_USER_FIELDS}
    return {
        "format": "binderbridge-account-export",
        "format_version": 1,
        "exported_at": now_iso(),
        "account": account,
        "collection": [record_dict(item) for item in export_collection_rows(user_id)],
        "wants": [record_dict(item) for item in export_wants_rows(user_id)],
        "groups": account_group_exports(user_id),
        "trades": account_trade_exports(user_id),
        "notifications": [
            record_dict(item)
            for item in rows("SELECT * FROM user_notifications WHERE user_id = ? ORDER BY created_at DESC, id DESC", (user_id,))
        ],
        "price_history": [
            record_dict(item)
            for item in rows("SELECT * FROM price_history WHERE user_id = ? ORDER BY observed_at DESC, id DESC", (user_id,))
        ],
    }


def export_account_json(user_id):
    user = row("SELECT username FROM users WHERE id = ?", (user_id,))
    filename_user = safe_export_name(user["username"] if user else "account", "account")
    return f"binderbridge-account-{filename_user}-{export_stamp()}.json", json_export_bytes(export_account_data(user_id))


__all__ = [
    "COLLECTION_EXPORT_FIELDS",
    "WANT_EXPORT_FIELDS",
    "GROUP_COLLECTION_EXPORT_FIELDS",
    "GROUP_WANT_EXPORT_FIELDS",
    "ACCOUNT_EXPORT_USER_FIELDS",
    "export_stamp",
    "safe_export_name",
    "record_dict",
    "csv_export_bytes",
    "json_export_bytes",
    "export_collection_rows",
    "export_collection_csv",
    "export_wants_rows",
    "export_wants_csv",
    "export_group_collection_rows",
    "export_group_want_rows",
    "export_group_csv",
    "account_group_exports",
    "account_trade_exports",
    "export_account_data",
    "export_account_json",
]
