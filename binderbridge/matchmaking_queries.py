"""Trade matchmaking candidate SQL query helpers.

Shared app helpers are injected at runtime by the app facade.
"""

def trade_matchmaking_candidate_rows(user_id):
    collection_clause, collection_params = visibility_sql_for_user_id(
        user_id, "collection_items.visibility", "collection_items.user_id"
    )
    want_clause, want_params = visibility_sql_for_user_id(
        user_id, "want_items.visibility", "want_items.user_id"
    )
    they_have_rows = rows(
        f"""
        SELECT
            users.id AS member_id,
            users.username,
            users.display_name,
            want_items.id AS want_id,
            want_items.desired_quantity,
            want_items.priority AS want_priority,
            want_items.budget_cap_usd AS want_budget_cap_usd,
            want_items.preferred_printing_notes AS want_preferred_printing_notes,
            collection_items.id AS collection_item_id,
            collection_items.card_name,
            collection_items.set_name,
            collection_items.set_code,
            collection_items.collector_number,
            collection_items.condition,
            collection_items.finish,
            collection_items.language,
            collection_items.price_usd,
            collection_items.image_url,
            collection_items.type_line,
            collection_items.quantity_for_trade
        FROM want_items
        JOIN collection_items ON collection_items.user_id != want_items.user_id
        JOIN users ON users.id = collection_items.user_id
        WHERE want_items.user_id = ?
            AND collection_items.user_id != ?
            AND users.is_banned = 0
            AND collection_items.quantity_for_trade > 0
            AND {collection_clause}
            AND collection_items.game = want_items.game
            AND (
                (want_items.scryfall_id != '' AND collection_items.scryfall_id = want_items.scryfall_id)
                OR (
                    collection_items.card_name = want_items.card_name COLLATE NOCASE
                    AND (want_items.set_code = '' OR collection_items.set_code = '' OR collection_items.set_code = want_items.set_code COLLATE NOCASE)
                    AND (want_items.collector_number = '' OR collection_items.collector_number = '' OR collection_items.collector_number = want_items.collector_number)
                )
            )
            AND (want_items.condition = '' OR instr(',' || want_items.condition || ',', ',' || COALESCE(collection_items.condition, '') || ',') > 0)
            AND (want_items.finish = '' OR instr(',' || want_items.finish || ',', ',' || COALESCE(collection_items.finish, '') || ',') > 0)
            AND (want_items.language = '' OR instr(',' || want_items.language || ',', ',' || COALESCE(collection_items.language, '') || ',') > 0)
        ORDER BY
            CASE want_items.priority WHEN 'urgent' THEN 4 WHEN 'high' THEN 3 WHEN 'normal' THEN 2 WHEN 'low' THEN 1 ELSE 2 END DESC,
            CASE
                WHEN want_items.budget_cap_usd != '' AND collection_items.price_usd != ''
                    AND CAST(collection_items.price_usd AS REAL) <= CAST(want_items.budget_cap_usd AS REAL)
                THEN 0 ELSE 1
            END,
            users.display_name COLLATE NOCASE,
            collection_items.card_name COLLATE NOCASE
        """,
        [user_id, user_id, *collection_params],
    )
    they_want_rows = rows(
        f"""
        SELECT
            users.id AS member_id,
            users.username,
            users.display_name,
            want_items.id AS want_id,
            want_items.desired_quantity,
            want_items.priority AS want_priority,
            want_items.budget_cap_usd AS want_budget_cap_usd,
            want_items.preferred_printing_notes AS want_preferred_printing_notes,
            collection_items.id AS collection_item_id,
            collection_items.card_name,
            collection_items.set_name,
            collection_items.set_code,
            collection_items.collector_number,
            collection_items.condition,
            collection_items.finish,
            collection_items.language,
            collection_items.price_usd,
            collection_items.image_url,
            collection_items.type_line,
            collection_items.quantity_for_trade
        FROM want_items
        JOIN users ON users.id = want_items.user_id
        JOIN collection_items ON collection_items.user_id = ?
        WHERE want_items.user_id != ?
            AND {want_clause}
            AND users.is_banned = 0
            AND collection_items.quantity_for_trade > 0
            AND collection_items.game = want_items.game
            AND (
                (want_items.scryfall_id != '' AND collection_items.scryfall_id = want_items.scryfall_id)
                OR (
                    collection_items.card_name = want_items.card_name COLLATE NOCASE
                    AND (want_items.set_code = '' OR collection_items.set_code = '' OR collection_items.set_code = want_items.set_code COLLATE NOCASE)
                    AND (want_items.collector_number = '' OR collection_items.collector_number = '' OR collection_items.collector_number = want_items.collector_number)
                )
            )
            AND (want_items.condition = '' OR instr(',' || want_items.condition || ',', ',' || COALESCE(collection_items.condition, '') || ',') > 0)
            AND (want_items.finish = '' OR instr(',' || want_items.finish || ',', ',' || COALESCE(collection_items.finish, '') || ',') > 0)
            AND (want_items.language = '' OR instr(',' || want_items.language || ',', ',' || COALESCE(collection_items.language, '') || ',') > 0)
        ORDER BY
            CASE want_items.priority WHEN 'urgent' THEN 4 WHEN 'high' THEN 3 WHEN 'normal' THEN 2 WHEN 'low' THEN 1 ELSE 2 END DESC,
            CASE
                WHEN want_items.budget_cap_usd != '' AND collection_items.price_usd != ''
                    AND CAST(collection_items.price_usd AS REAL) <= CAST(want_items.budget_cap_usd AS REAL)
                THEN 0 ELSE 1
            END,
            users.display_name COLLATE NOCASE,
            collection_items.card_name COLLATE NOCASE
        """,
        [user_id, user_id, *want_params],
    )
    return they_have_rows, they_want_rows


__all__ = [
    'trade_matchmaking_candidate_rows',
]
