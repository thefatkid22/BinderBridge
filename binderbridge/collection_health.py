"""Site-wide collection health metrics for administrators."""

from collections import defaultdict
from datetime import datetime, timedelta, timezone


COLLECTION_HEALTH_STALE_MULTIPLIER = 2


def collection_health_price_cutoff(reference_time=None):
    reference_time = reference_time or datetime.now(timezone.utc)
    hours = max(1, int(SCRYFALL_PRICE_REFRESH_INTERVAL_HOURS)) * COLLECTION_HEALTH_STALE_MULTIPLIER
    return (reference_time - timedelta(hours=hours)).replace(microsecond=0).isoformat()


def collection_health_missing_scryfall(item):
    if str(row_value(item, "game", "") or "").lower() != "mtg":
        return False
    return not (
        str(row_value(item, "scryfall_id", "") or "").strip()
        and str(row_value(item, "scryfall_uri", "") or "").strip()
        and str(row_value(item, "type_line", "") or "").strip()
    )


def collection_health_stale_price(item, cutoff):
    if str(row_value(item, "game", "") or "").lower() != "mtg":
        return False
    if not str(row_value(item, "scryfall_id", "") or "").strip():
        return False
    refreshed_at = str(row_value(item, "price_refreshed_at", "") or "").strip()
    return not refreshed_at or refreshed_at < cutoff


def collection_health_invalid_finish(item, conn):
    details = condition_finish_audit_details(item, conn=conn)
    return any(
        details.get(key)
        for key in ("finish_missing", "finish_invalid", "normalize_finish", "scryfall_finish_mismatch")
    )


def collection_health_duplicate_ids(items):
    grouped = defaultdict(list)
    for item in items:
        grouped[item_duplicate_key(item, COLLECTION_DUPLICATE_FIELDS)].append(item)
    duplicate_ids = set()
    for group_items in grouped.values():
        if len(group_items) > 1:
            duplicate_ids.update(int(item["id"]) for item in group_items[1:])
    return duplicate_ids


def collection_health_severity(user_health):
    if not int(user_health.get("affected_cards", 0) or 0):
        return "ok"
    if int(user_health.get("invalid_finishes", 0) or 0) or int(user_health.get("duplicate_rows", 0) or 0):
        return "error"
    return "warning"


def collection_health_dashboard(reference_time=None):
    cutoff = collection_health_price_cutoff(reference_time)
    users = rows(
        """
        SELECT id, username, display_name, role, is_banned, collection_value_visibility
        FROM users
        ORDER BY display_name COLLATE NOCASE, username COLLATE NOCASE
        """
    )
    items = rows(
        """
        SELECT *
        FROM collection_items
        ORDER BY user_id, card_name COLLATE NOCASE, set_name COLLATE NOCASE, collector_number COLLATE NOCASE, id
        """
    )
    items_by_user = defaultdict(list)
    for item in items:
        items_by_user[int(item["user_id"])].append(item)

    summary = {
        "total_cards": len(items),
        "healthy_cards": 0,
        "affected_cards": 0,
        "duplicate_rows": 0,
        "missing_scryfall": 0,
        "invalid_finishes": 0,
        "stale_prices": 0,
        "users_with_cards": 0,
        "users_needing_attention": 0,
        "visibility": {key: 0 for key in VISIBILITY_LABELS},
        "value_visibility": {key: 0 for key in VALUE_VISIBILITY_LABELS},
    }
    user_rows = []
    with db() as conn:
        for user in users:
            user_items = items_by_user.get(int(user["id"]), [])
            value_visibility = str(row_value(user, "collection_value_visibility", VISIBILITY_MEMBERS) or VISIBILITY_MEMBERS)
            if value_visibility not in summary["value_visibility"]:
                value_visibility = VISIBILITY_MEMBERS
            summary["value_visibility"][value_visibility] += 1
            if not user_items:
                continue

            duplicate_ids = collection_health_duplicate_ids(user_items)
            missing_ids = {
                int(item["id"])
                for item in user_items
                if collection_health_missing_scryfall(item)
            }
            invalid_finish_ids = {
                int(item["id"])
                for item in user_items
                if collection_health_invalid_finish(item, conn)
            }
            stale_price_ids = {
                int(item["id"])
                for item in user_items
                if collection_health_stale_price(item, cutoff)
            }
            affected_ids = duplicate_ids | missing_ids | invalid_finish_ids | stale_price_ids
            visibility = {key: 0 for key in VISIBILITY_LABELS}
            for item in user_items:
                item_visibility = str(row_value(item, "visibility", VISIBILITY_MEMBERS) or VISIBILITY_MEMBERS)
                if item_visibility not in visibility:
                    item_visibility = VISIBILITY_MEMBERS
                visibility[item_visibility] += 1
                summary["visibility"][item_visibility] += 1

            health = {
                "user_id": int(user["id"]),
                "username": user["username"],
                "display_name": user["display_name"],
                "role": user["role"],
                "is_banned": bool(user["is_banned"]),
                "collection_value_visibility": value_visibility,
                "total_cards": len(user_items),
                "healthy_cards": len(user_items) - len(affected_ids),
                "affected_cards": len(affected_ids),
                "duplicate_rows": len(duplicate_ids),
                "missing_scryfall": len(missing_ids),
                "invalid_finishes": len(invalid_finish_ids),
                "stale_prices": len(stale_price_ids),
                "visibility": visibility,
            }
            health["health_percent"] = int((health["healthy_cards"] / health["total_cards"]) * 100) if health["total_cards"] else 100
            health["issue_total"] = (
                health["duplicate_rows"]
                + health["missing_scryfall"]
                + health["invalid_finishes"]
                + health["stale_prices"]
            )
            health["severity"] = collection_health_severity(health)
            user_rows.append(health)
            summary["users_with_cards"] += 1
            summary["users_needing_attention"] += 1 if affected_ids else 0
            summary["healthy_cards"] += health["healthy_cards"]
            summary["affected_cards"] += health["affected_cards"]
            summary["duplicate_rows"] += health["duplicate_rows"]
            summary["missing_scryfall"] += health["missing_scryfall"]
            summary["invalid_finishes"] += health["invalid_finishes"]
            summary["stale_prices"] += health["stale_prices"]

    summary["health_percent"] = (
        int((summary["healthy_cards"] / summary["total_cards"]) * 100)
        if summary["total_cards"]
        else 100
    )
    user_rows.sort(
        key=lambda item: (
            0 if item["affected_cards"] else 1,
            -item["affected_cards"],
            str(item["display_name"]).lower(),
        )
    )
    return {
        "summary": summary,
        "users": user_rows,
        "price_stale_after_hours": max(1, int(SCRYFALL_PRICE_REFRESH_INTERVAL_HOURS)) * COLLECTION_HEALTH_STALE_MULTIPLIER,
        "price_cutoff": cutoff,
    }


__all__ = [
    "COLLECTION_HEALTH_STALE_MULTIPLIER",
    "collection_health_price_cutoff",
    "collection_health_missing_scryfall",
    "collection_health_stale_price",
    "collection_health_invalid_finish",
    "collection_health_duplicate_ids",
    "collection_health_severity",
    "collection_health_dashboard",
]
