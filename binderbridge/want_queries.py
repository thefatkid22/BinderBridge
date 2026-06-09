"""Wanted-card SQL query helpers.

Shared app helpers are injected at runtime by the app facade.
"""

def want_trade_matches(user_id, want, limit=3):
    condition_preference = row_value(want, "condition", "")
    finish_preference = row_value(want, "finish", "")
    language_preference = row_value(want, "language", "")
    budget_cap_usd = normalize_price_usd(row_value(want, "budget_cap_usd", ""))
    base_params = [
        user_id,
        want["game"],
        want["scryfall_id"],
        want["scryfall_id"],
        want["card_name"],
        want["set_code"],
        want["set_code"],
        want["collector_number"],
        want["collector_number"],
        condition_preference,
        condition_preference,
        finish_preference,
        finish_preference,
        language_preference,
        language_preference,
    ]
    params = [budget_cap_usd, budget_cap_usd, *base_params]
    matches = rows(
        f"""
        SELECT
            users.id AS owner_id,
            users.display_name,
            users.username,
            COALESCE(SUM(collection_items.quantity_for_trade), 0) AS total_quantity,
            COALESCE(SUM(
                CASE
                    WHEN ? != '' AND collection_items.price_usd != ''
                        AND CAST(collection_items.price_usd AS REAL) <= CAST(? AS REAL)
                    THEN collection_items.quantity_for_trade
                    ELSE 0
                END
            ), 0) AS within_budget_quantity,
            COUNT(collection_items.id) AS entry_count
        FROM collection_items
        JOIN users ON users.id = collection_items.user_id
        WHERE collection_items.user_id != ?
            AND users.is_banned = 0
            AND collection_items.quantity_for_trade > 0
            AND collection_items.is_public = 1
            AND collection_items.game = ?
            AND (
                (? != '' AND collection_items.scryfall_id = ?)
                OR (
                    collection_items.card_name = ? COLLATE NOCASE
                    AND (? = '' OR collection_items.set_code = '' OR collection_items.set_code = ?)
                    AND (? = '' OR collection_items.collector_number = '' OR collection_items.collector_number = ?)
                )
            )
            AND (? = '' OR instr(',' || ? || ',', ',' || COALESCE(collection_items.condition, '') || ',') > 0)
            AND (? = '' OR instr(',' || ? || ',', ',' || COALESCE(collection_items.finish, '') || ',') > 0)
            AND (? = '' OR instr(',' || ? || ',', ',' || COALESCE(collection_items.language, '') || ',') > 0)
        GROUP BY users.id
        ORDER BY within_budget_quantity DESC, total_quantity DESC, users.display_name COLLATE NOCASE
        LIMIT {int(limit)}
        """,
        params,
    )
    totals = row(
        """
        SELECT
            COUNT(DISTINCT users.id) AS user_count,
            COALESCE(SUM(collection_items.quantity_for_trade), 0) AS total_quantity,
            COALESCE(SUM(
                CASE
                    WHEN ? != '' AND collection_items.price_usd != ''
                        AND CAST(collection_items.price_usd AS REAL) <= CAST(? AS REAL)
                    THEN collection_items.quantity_for_trade
                    ELSE 0
                END
            ), 0) AS within_budget_quantity
        FROM collection_items
        JOIN users ON users.id = collection_items.user_id
        WHERE collection_items.user_id != ?
            AND users.is_banned = 0
            AND collection_items.quantity_for_trade > 0
            AND collection_items.is_public = 1
            AND collection_items.game = ?
            AND (
                (? != '' AND collection_items.scryfall_id = ?)
                OR (
                    collection_items.card_name = ? COLLATE NOCASE
                    AND (? = '' OR collection_items.set_code = '' OR collection_items.set_code = ?)
                    AND (? = '' OR collection_items.collector_number = '' OR collection_items.collector_number = ?)
                )
            )
            AND (? = '' OR instr(',' || ? || ',', ',' || COALESCE(collection_items.condition, '') || ',') > 0)
            AND (? = '' OR instr(',' || ? || ',', ',' || COALESCE(collection_items.finish, '') || ',') > 0)
            AND (? = '' OR instr(',' || ? || ',', ',' || COALESCE(collection_items.language, '') || ',') > 0)
        """,
        params,
    )
    return {
        "matches": matches,
        "user_count": totals["user_count"],
        "total_quantity": totals["total_quantity"],
        "within_budget_quantity": totals["within_budget_quantity"],
    }



def want_rows_for_user(user_id, order_clause):
    return rows(
        f"""
        SELECT *
        FROM want_items
        WHERE user_id = ?
        ORDER BY {order_clause}
        """,
        (user_id,),
    )

__all__ = [
    'want_trade_matches',
    'want_rows_for_user',
]
