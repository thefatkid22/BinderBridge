"""Dashboard and notification center views.

This module is wired by binderbridge.views; shared app helpers are injected at runtime.
"""

def render_dashboard(user, notice=None, status="info"):
    summary = get_collection_summary(user["id"])
    pending = rows(
        """
        SELECT trades.*, proposer.display_name AS proposer_name, recipient.display_name AS recipient_name,
            (
                SELECT COUNT(*)
                FROM user_notifications
                WHERE user_notifications.user_id = ?
                    AND user_notifications.related_trade_id = trades.id
                    AND user_notifications.is_read = 0
                    AND user_notifications.kind IN ('trade_offer', 'trade_counter', 'trade_comment', 'trade_status', 'trade_reminder', 'trade_dispute', 'trade_feedback')
            ) AS unread_trade_notifications
        FROM trades
        JOIN users proposer ON proposer.id = trades.proposer_id
        JOIN users recipient ON recipient.id = trades.recipient_id
        WHERE (trades.proposer_id = ? OR trades.recipient_id = ?) AND trades.status = 'pending'
        ORDER BY trades.updated_at DESC
        LIMIT 5
        """,
        (user["id"], user["id"], user["id"]),
    )
    recent_for_trade = rows(
        """
        SELECT *
        FROM collection_items
        WHERE user_id = ? AND quantity_for_trade > 0
        ORDER BY updated_at DESC
        LIMIT 5
        """,
        (user["id"],),
    )
    pending_html = render_trade_table(user, pending, compact=True) if pending else '<div class="empty-state">No pending trades yet.</div>'
    recent_html = "".join(
        f"""
        <li>
            <strong>{e(item["card_name"])}</strong>
            <span>{e(item["quantity_for_trade"])} available - {e(item["condition"])} - {e(item["finish"])}</span>
        </li>
        """
        for item in recent_for_trade
    ) or '<li class="muted">Mark cards for trade from your collection.</li>'
    notifications = notification_rows(user["id"], limit=4)
    notification_html = "".join(
        f"""
        <li class="{'dashboard-notification-unread' if not item['is_read'] else ''}">
            <strong>{'<span class="unread-dot" aria-label="Unread"></span>' if not item['is_read'] else ''}{e(item["title"])}</strong>
            <span>{e(notification_kind_label(item["kind"]))} - {e(item["created_at"][:16].replace("T", " "))}</span>
            {f'<a href="{e(item["url"])}">Open</a>' if item["url"] else ''}
        </li>
        """
        for item in notifications
    ) or '<li class="muted">Trade updates, price changes, and import notices will appear here.</li>'

    content = f"""
    <section class="section-heading">
        <div>
            <p class="eyebrow">Dashboard</p>
            <h1>Your trade room</h1>
        </div>
        <div class="actions">
            <a class="button primary" href="/collection/new">Add card</a>
            <a class="button secondary" href="/browse">Find trades</a>
        </div>
    </section>

    <section class="metric-grid">
        <article class="metric"><span>{summary["total_cards"]}</span><p>Total cards</p></article>
        <article class="metric"><span>{summary["unique_cards"]}</span><p>Unique entries</p></article>
        <article class="metric"><span>{summary["trade_cards"]}</span><p>For trade</p></article>
        <article class="metric"><span>{summary["wants_count"]}</span><p>Wanted cards</p></article>
    </section>

    <section class="content-grid">
        <article class="panel">
            <div class="panel-heading">
                <h2>Pending trades</h2>
                <a href="/trades">View all</a>
            </div>
            {pending_html}
        </article>
        <article class="panel">
            <div class="panel-heading">
                <h2>Recently tradeable</h2>
                <a href="/collection">Manage</a>
            </div>
            <ul class="stack-list">{recent_html}</ul>
        </article>
    </section>
    <section class="panel">
        <div class="panel-heading">
            <h2>Recent notifications</h2>
            <a href="/notifications">Open inbox</a>
        </div>
        <ul class="stack-list">{notification_html}</ul>
    </section>
    """
    return render_layout(user, "Dashboard", content, active="dashboard", notice=notice, status=status)


def notification_kind_label(kind):
    return {
        "price_alert": "Price alert",
        "price_refresh": "Price refresh",
        "scryfall_import": "Import lookup",
        "watchlist_alert": "Watchlist",
        "trade_offer": "Trade offer",
        "trade_counter": "Counter offer",
        "trade_comment": "Comment",
        "trade_dispute": "Trade issue",
        "trade_feedback": "Feedback",
        "trade_status": "Trade status",
        "trade_reminder": "Trade reminder",
    }.get(kind, "Notification")


def render_notifications(user, notice=None, status="info"):
    notifications = notification_rows(user["id"])
    unread_count = unread_notification_count(user["id"])
    read_count = sum(1 for item in notifications if item["is_read"])
    if notifications:
        notification_items = "".join(
            f"""
            <li class="notification-item {'unread' if not item["is_read"] else 'read'}">
                <div>
                    {f'<span class="pill unread-indicator">Unread</span>' if not item["is_read"] else ''}
                    <span class="pill">{e(notification_kind_label(item["kind"]))}</span>
                    <span class="subtle">{e(item["created_at"][:16].replace("T", " "))}</span>
                    {f'<span class="pill email-status {e(row_value(item, "email_status", ""))}">Email {e(row_value(item, "email_status", ""))}</span>' if row_value(item, "email_status", "") else ''}
                </div>
                <h2>{e(item["title"])}</h2>
                <p>{e(item["body"])}</p>
                {f'<p class="muted compact">Email issue: {e(row_value(item, "email_error", ""))}</p>' if row_value(item, "email_error", "") else ''}
                <div class="actions">
                    {f'<a class="button secondary small" href="{e(item["url"])}">Open</a>' if item["url"] else ''}
                    {f'<form method="post" action="/notifications/{item["id"]}/read"><button class="button ghost small" type="submit">Mark read</button></form>' if not item["is_read"] else ''}
                    <form method="post" action="/notifications/{item["id"]}/delete">
                        <button class="button danger small" type="submit">Delete</button>
                    </form>
                </div>
            </li>
            """
            for item in notifications
        )
    else:
        notification_items = '<li class="empty-state compact-empty">No notifications yet.</li>'

    recent_changes = price_history_summary(user["id"])
    if recent_changes:
        change_items = "".join(
            f"""
            <li>
                <strong>{e(card_price_history_label(change["card_name"], change["set_name"], change["collector_number"]))}</strong>
                <span>{e(change["observed_at"][:10])} - ${e(change["previous_price_usd"])} to ${e(change["price_usd"])} ({e(signed_price_text(change["change_amount"]))})</span>
            </li>
            """
            for change in recent_changes
        )
    else:
        change_items = '<li class="muted">Price changes will appear after a Scryfall refresh finds a new value.</li>'

    mark_all = (
        """
        <form method="post" action="/notifications/read-all">
            <button class="button secondary" type="submit">Mark all read</button>
        </form>
        """
        if unread_count
        else ""
    )
    delete_read = (
        """
        <form method="post" action="/notifications/delete-read">
            <button class="button secondary" type="submit">Delete read</button>
        </form>
        """
        if read_count
        else ""
    )
    delete_all = (
        f"""
        <form method="post" action="/notifications/delete-all">
            <button class="button danger" type="submit" onclick="return confirm('Delete all {len(notifications)} notifications?')">Delete all</button>
        </form>
        """
        if notifications
        else ""
    )
    content = f"""
    <section class="section-heading">
        <div>
            <p class="eyebrow">Notifications</p>
            <h1>Activity and alerts</h1>
        </div>
        <div class="actions">
            {mark_all}
            {delete_read}
            {delete_all}
        </div>
    </section>
    <section class="content-grid notifications-grid">
        <article class="panel">
            <div class="panel-heading">
                <h2>Inbox</h2>
                <span class="pill">{e(unread_count)} unread</span>
            </div>
            <ol class="notification-list">{notification_items}</ol>
        </article>
        <article class="panel">
            <div class="panel-heading">
                <h2>Recent value changes</h2>
            </div>
            <ul class="stack-list">{change_items}</ul>
        </article>
    </section>
    """
    return render_layout(user, "Notifications", content, active="dashboard", notice=notice, status=status)


__all__ = ['render_dashboard', 'notification_kind_label', 'render_notifications']
