"""Trade, feedback, dispute, and selected-card SQL query helpers.

Shared app helpers are injected at runtime by the app facade.
"""

from datetime import datetime, timedelta, timezone


def counter_source_trade_for(user_id, recipient_id, counter_trade_id):
    if not counter_trade_id:
        return None
    return row(
        """
        SELECT trades.*, proposer.display_name AS proposer_name, recipient.display_name AS recipient_name
        FROM trades
        JOIN users proposer ON proposer.id = trades.proposer_id
        JOIN users recipient ON recipient.id = trades.recipient_id
        WHERE trades.id = ?
            AND trades.proposer_id = ?
            AND trades.recipient_id = ?
            AND trades.status = 'pending'
        """,
        (counter_trade_id, recipient_id, user_id),
    )

def linked_trade_for_user(trade_id, user_id):
    if not trade_id:
        return None
    return row(
        """
        SELECT trades.*, proposer.display_name AS proposer_name, recipient.display_name AS recipient_name
        FROM trades
        JOIN users proposer ON proposer.id = trades.proposer_id
        JOIN users recipient ON recipient.id = trades.recipient_id
        WHERE trades.id = ? AND (trades.proposer_id = ? OR trades.recipient_id = ?)
        """,
        (trade_id, user_id, user_id),
    )

def counter_trade_query(source_trade, user_id):
    query = {
        "recipient_id": [str(source_trade["proposer_id"])],
        "counter_trade_id": [str(source_trade["id"])],
    }
    if row_value(source_trade, "price_source_preference", ""):
        query["price_source_preference"] = [row_value(source_trade, "price_source_preference", "")]
    source_items = rows("SELECT * FROM trade_items WHERE trade_id = ?", (source_trade["id"],))
    for item in source_items:
        collection_item_id = row_value(item, "collection_item_id")
        if not collection_item_id:
            continue
        prefix = "offer" if item["owner_id"] == user_id else "request"
        query[f"{prefix}_{collection_item_id}"] = [str(item["quantity"])]
    return query

def trade_comment_rows(trade_id):
    return rows(
        """
        SELECT trade_comments.*, users.display_name
        FROM trade_comments
        JOIN users ON users.id = trade_comments.user_id
        WHERE trade_comments.trade_id = ?
        ORDER BY trade_comments.created_at ASC, trade_comments.id ASC
        """,
        (trade_id,),
    )

def trade_with_names_conn(conn, trade_id):
    return conn.execute(
        """
        SELECT trades.*, proposer.display_name AS proposer_name, recipient.display_name AS recipient_name
        FROM trades
        JOIN users proposer ON proposer.id = trades.proposer_id
        JOIN users recipient ON recipient.id = trades.recipient_id
        WHERE trades.id = ?
        """,
        (trade_id,),
    ).fetchone()

def trade_feedback_rows(trade_id):
    return rows(
        """
        SELECT
            trade_feedback.*,
            reviewer.display_name AS reviewer_name,
            reviewee.display_name AS reviewee_name
        FROM trade_feedback
        JOIN users reviewer ON reviewer.id = trade_feedback.reviewer_id
        JOIN users reviewee ON reviewee.id = trade_feedback.reviewee_id
        WHERE trade_feedback.trade_id = ?
        ORDER BY trade_feedback.updated_at ASC, trade_feedback.id ASC
        """,
        (trade_id,),
    )

def trade_feedback_for_user(trade_id, user_id):
    return row(
        """
        SELECT *
        FROM trade_feedback
        WHERE trade_id = ? AND reviewer_id = ?
        """,
        (trade_id, user_id),
    )

def reputation_summary(user_id):
    summary = row(
        """
        SELECT
            COUNT(*) AS feedback_count,
            COALESCE(AVG(rating), 0) AS average_rating,
            SUM(CASE WHEN rating >= 4 THEN 1 ELSE 0 END) AS positive_count
        FROM trade_feedback
        WHERE reviewee_id = ?
        """,
        (user_id,),
    )
    feedback_count = int(row_value(summary, "feedback_count", 0) or 0)
    positive_count = int(row_value(summary, "positive_count", 0) or 0)
    return {
        "feedback_count": feedback_count,
        "average_rating": float(row_value(summary, "average_rating", 0) or 0),
        "positive_count": positive_count,
        "completed_trade_count": completed_trade_count(user_id),
    }

def recent_feedback_for_user(user_id, limit=4):
    return rows(
        """
        SELECT
            trade_feedback.*,
            reviewer.display_name AS reviewer_name
        FROM trade_feedback
        JOIN users reviewer ON reviewer.id = trade_feedback.reviewer_id
        WHERE trade_feedback.reviewee_id = ?
        ORDER BY COALESCE(NULLIF(trade_feedback.updated_at, ''), trade_feedback.created_at) DESC,
            trade_feedback.id DESC
        LIMIT ?
        """,
        (user_id, limit),
    )

def trade_dispute_rows(trade_id):
    return rows(
        """
        SELECT
            trade_disputes.*,
            reporter.display_name AS reporter_name,
            reporter.username AS reporter_username,
            resolver.display_name AS resolver_name,
            resolver.username AS resolver_username
        FROM trade_disputes
        LEFT JOIN users reporter ON reporter.id = trade_disputes.reporter_id
        LEFT JOIN users resolver ON resolver.id = trade_disputes.resolved_by_user_id
        WHERE trade_disputes.trade_id = ?
        ORDER BY
            CASE trade_disputes.status
                WHEN 'open' THEN 1
                WHEN 'reviewing' THEN 2
                WHEN 'resolved' THEN 3
                WHEN 'dismissed' THEN 4
                ELSE 9
            END,
            trade_disputes.created_at DESC,
            trade_disputes.id DESC
        """,
        (trade_id,),
    )

TRADE_DISPUTE_TEXT_PREVIEW_CHARS = 4000


def trade_dispute_evidence_rows(dispute_id):
    return rows(
        """
        SELECT
            trade_dispute_evidence.id,
            trade_dispute_evidence.dispute_id,
            trade_dispute_evidence.uploaded_by_user_id,
            trade_dispute_evidence.original_filename,
            trade_dispute_evidence.content_type,
            trade_dispute_evidence.file_size,
            trade_dispute_evidence.checksum_sha256,
            trade_dispute_evidence.note,
            trade_dispute_evidence.created_at,
            uploader.display_name AS uploader_name,
            uploader.username AS uploader_username
        FROM trade_dispute_evidence
        LEFT JOIN users uploader ON uploader.id = trade_dispute_evidence.uploaded_by_user_id
        WHERE trade_dispute_evidence.dispute_id = ?
        ORDER BY trade_dispute_evidence.created_at, trade_dispute_evidence.id
        """,
        (dispute_id,),
    )

def trade_dispute_evidence_text_preview(evidence_id, dispute_id, max_chars=TRADE_DISPUTE_TEXT_PREVIEW_CHARS):
    found = row(
        """
        SELECT content, file_size
        FROM trade_dispute_evidence
        WHERE id = ? AND dispute_id = ? AND content_type = 'text/plain'
        """,
        (evidence_id, dispute_id),
    )
    if not found:
        return None
    content = found["content"] or b""
    if isinstance(content, str):
        text = content
    else:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = content.decode("utf-16", errors="replace")
    text = sanitize_text_input(text, max_length=max_chars + 1).strip()
    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars].rstrip()
    return {"text": text, "truncated": truncated}

def trade_dispute_evidence_for_user(evidence_id, user_id, is_admin=False):
    return row(
        """
        SELECT
            trade_dispute_evidence.*,
            trade_disputes.trade_id,
            trades.proposer_id,
            trades.recipient_id
        FROM trade_dispute_evidence
        JOIN trade_disputes ON trade_disputes.id = trade_dispute_evidence.dispute_id
        JOIN trades ON trades.id = trade_disputes.trade_id
        WHERE trade_dispute_evidence.id = ?
            AND (
                ? = 1
                OR trades.proposer_id = ?
                OR trades.recipient_id = ?
            )
        """,
        (evidence_id, 1 if is_admin else 0, user_id, user_id),
    )

def trade_dispute_admin_where(filters=None):
    filters = trade_dispute_admin_filters(filters)
    where = []
    params = []
    if filters["q"]:
        term = f"%{filters['q']}%"
        where.append(
            """
            (
                CAST(trade_disputes.id AS TEXT) LIKE ?
                OR CAST(trade_disputes.trade_id AS TEXT) LIKE ?
                OR trade_disputes.body LIKE ?
                OR trade_disputes.admin_note LIKE ?
                OR reporter.username LIKE ?
                OR reporter.display_name LIKE ?
                OR proposer.username LIKE ?
                OR proposer.display_name LIKE ?
                OR recipient.username LIKE ?
                OR recipient.display_name LIKE ?
            )
            """
        )
        params.extend([term, term, term, term, term, term, term, term, term, term])
    if filters["status"]:
        where.append("trade_disputes.status = ?")
        params.append(filters["status"])
    if filters["category"]:
        where.append("trade_disputes.category = ?")
        params.append(filters["category"])
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    return where_sql, params

def trade_dispute_admin_count(filters=None):
    where_sql, params = trade_dispute_admin_where(filters)
    found = row(
        f"""
        SELECT COUNT(*) AS count
        FROM trade_disputes
        JOIN trades ON trades.id = trade_disputes.trade_id
        LEFT JOIN users reporter ON reporter.id = trade_disputes.reporter_id
        LEFT JOIN users proposer ON proposer.id = trades.proposer_id
        LEFT JOIN users recipient ON recipient.id = trades.recipient_id
        {where_sql}
        """,
        params,
    )
    return found["count"] if found else 0

def open_trade_dispute_count():
    found = row("SELECT COUNT(*) AS count FROM trade_disputes WHERE status IN ('open', 'reviewing')")
    return found["count"] if found else 0

def trade_dispute_trend_cutoff(days):
    days = max(1, int(days or 90))
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()

def trade_dispute_repeat_issue_trends(limit=6, days=90):
    limit = max(1, min(int(limit or 6), 20))
    cutoff = trade_dispute_trend_cutoff(days)
    return rows(
        """
        SELECT
            reported.id AS reported_user_id,
            reported.display_name AS reported_name,
            reported.username AS reported_username,
            COUNT(*) AS issue_count,
            SUM(CASE WHEN trade_disputes.status IN ('open', 'reviewing') THEN 1 ELSE 0 END) AS active_count,
            MAX(trade_disputes.created_at) AS latest_at,
            GROUP_CONCAT(DISTINCT trade_disputes.category) AS categories
        FROM trade_disputes
        JOIN trades ON trades.id = trade_disputes.trade_id
        LEFT JOIN users reported ON reported.id = CASE
            WHEN trade_disputes.reporter_id = trades.proposer_id THEN trades.recipient_id
            WHEN trade_disputes.reporter_id = trades.recipient_id THEN trades.proposer_id
            ELSE NULL
        END
        WHERE trade_disputes.created_at >= ?
            AND reported.id IS NOT NULL
        GROUP BY reported.id
        HAVING COUNT(*) >= 2
        ORDER BY issue_count DESC, active_count DESC, latest_at DESC
        LIMIT ?
        """,
        (cutoff, limit),
    )

def trade_dispute_category_trends(limit=6, days=90):
    limit = max(1, min(int(limit or 6), 20))
    cutoff = trade_dispute_trend_cutoff(days)
    return rows(
        """
        SELECT
            category,
            COUNT(*) AS issue_count,
            SUM(CASE WHEN status IN ('open', 'reviewing') THEN 1 ELSE 0 END) AS active_count,
            COUNT(DISTINCT trade_id) AS trade_count,
            MAX(created_at) AS latest_at
        FROM trade_disputes
        WHERE created_at >= ?
        GROUP BY category
        ORDER BY issue_count DESC, active_count DESC, latest_at DESC
        LIMIT ?
        """,
        (cutoff, limit),
    )

def trade_dispute_admin_rows(filters=None, limit=25, offset=0):
    limit = max(1, min(int(limit or 25), 100))
    offset = max(0, int(offset or 0))
    where_sql, params = trade_dispute_admin_where(filters)
    return rows(
        f"""
        SELECT
            trade_disputes.*,
            reporter.display_name AS reporter_name,
            reporter.username AS reporter_username,
            proposer.display_name AS proposer_name,
            proposer.username AS proposer_username,
            recipient.display_name AS recipient_name,
            recipient.username AS recipient_username,
            resolver.display_name AS resolver_name,
            resolver.username AS resolver_username
        FROM trade_disputes
        JOIN trades ON trades.id = trade_disputes.trade_id
        LEFT JOIN users reporter ON reporter.id = trade_disputes.reporter_id
        LEFT JOIN users proposer ON proposer.id = trades.proposer_id
        LEFT JOIN users recipient ON recipient.id = trades.recipient_id
        LEFT JOIN users resolver ON resolver.id = trade_disputes.resolved_by_user_id
        {where_sql}
        ORDER BY
            CASE trade_disputes.status
                WHEN 'open' THEN 1
                WHEN 'reviewing' THEN 2
                WHEN 'resolved' THEN 3
                WHEN 'dismissed' THEN 4
                ELSE 9
            END,
            trade_disputes.updated_at DESC,
            trade_disputes.id DESC
        LIMIT ? OFFSET ?
        """,
        params + [limit, offset],
    )

def trade_fairness_entries_for_trade_conn(conn, trade_id):
    offered = conn.execute(
        """
        SELECT
            trade_items.*,
            COALESCE(NULLIF(trade_items.price_usd, ''), collection_items.price_usd, '') AS display_price_usd,
            COALESCE(NULLIF(trade_items.price_source, ''), collection_items.price_source, '') AS display_price_source
        FROM trade_items
        LEFT JOIN collection_items ON collection_items.id = trade_items.collection_item_id
        WHERE trade_items.trade_id = ? AND trade_items.side = 'offered'
        """,
        (trade_id,),
    ).fetchall()
    requested = conn.execute(
        """
        SELECT
            trade_items.*,
            COALESCE(NULLIF(trade_items.price_usd, ''), collection_items.price_usd, '') AS display_price_usd,
            COALESCE(NULLIF(trade_items.price_source, ''), collection_items.price_source, '') AS display_price_source
        FROM trade_items
        LEFT JOIN collection_items ON collection_items.id = trade_items.collection_item_id
        WHERE trade_items.trade_id = ? AND trade_items.side = 'requested'
        """,
        (trade_id,),
    ).fetchall()
    return [(item, item["quantity"]) for item in offered], [(item, item["quantity"]) for item in requested]



def trade_rows_for_user(user_id):
    return rows(
        """
        SELECT trades.*, proposer.display_name AS proposer_name, recipient.display_name AS recipient_name
        FROM trades
        JOIN users proposer ON proposer.id = trades.proposer_id
        JOIN users recipient ON recipient.id = trades.recipient_id
        WHERE trades.proposer_id = ? OR trades.recipient_id = ?
        ORDER BY trades.updated_at DESC
        """,
        (user_id, user_id),
    )


def trade_detail_for_user(trade_id, user_id):
    return row(
        """
        SELECT trades.*, proposer.display_name AS proposer_name, recipient.display_name AS recipient_name
        FROM trades
        JOIN users proposer ON proposer.id = trades.proposer_id
        JOIN users recipient ON recipient.id = trades.recipient_id
        WHERE trades.id = ? AND (trades.proposer_id = ? OR trades.recipient_id = ?)
        """,
        (trade_id, user_id, user_id),
    )


def trade_item_rows(trade_id, side):
    return rows(
        """
        SELECT
            trade_items.*,
            COALESCE(NULLIF(trade_items.price_usd, ''), collection_items.price_usd, '') AS display_price_usd,
            COALESCE(NULLIF(trade_items.price_source, ''), collection_items.price_source, '') AS display_price_source
        FROM trade_items
        LEFT JOIN collection_items ON collection_items.id = trade_items.collection_item_id
        WHERE trade_items.trade_id = ? AND trade_items.side = ?
        ORDER BY trade_items.card_name
        """,
        (trade_id, side),
    )


def trade_selected_item_rows(owner_id, item_ids, viewer_id=None):
    if not item_ids:
        return []
    placeholders = ",".join("?" for _ in item_ids)
    visibility_sql = "AND is_public = 1" if viewer_id is not None and int(viewer_id) != int(owner_id) else ""
    return rows(
        f"""
        SELECT *
        FROM collection_items
        WHERE user_id = ? AND id IN ({placeholders}) {visibility_sql}
        ORDER BY card_name COLLATE NOCASE, set_name COLLATE NOCASE, collector_number COLLATE NOCASE
        """,
        [owner_id, *item_ids],
    )


def counter_render_source_trade(user_id, trade_id):
    return row(
        """
        SELECT trades.*, proposer.display_name AS proposer_name, recipient.display_name AS recipient_name
        FROM trades
        JOIN users proposer ON proposer.id = trades.proposer_id
        JOIN users recipient ON recipient.id = trades.recipient_id
        WHERE trades.id = ? AND trades.recipient_id = ? AND trades.status = 'pending'
        """,
        (trade_id, user_id),
    )

__all__ = [
    'counter_source_trade_for',
    'linked_trade_for_user',
    'counter_trade_query',
    'trade_comment_rows',
    'trade_with_names_conn',
    'trade_feedback_rows',
    'trade_feedback_for_user',
    'reputation_summary',
    'recent_feedback_for_user',
    'trade_dispute_rows',
    'trade_dispute_evidence_rows',
    'trade_dispute_evidence_text_preview',
    'trade_dispute_evidence_for_user',
    'trade_dispute_admin_where',
    'trade_dispute_admin_count',
    'open_trade_dispute_count',
    'trade_dispute_trend_cutoff',
    'trade_dispute_repeat_issue_trends',
    'trade_dispute_category_trends',
    'trade_dispute_admin_rows',
    'trade_fairness_entries_for_trade_conn',
    'trade_rows_for_user',
    'trade_detail_for_user',
    'trade_item_rows',
    'trade_selected_item_rows',
    'counter_render_source_trade',
]
