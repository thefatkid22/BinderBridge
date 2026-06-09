"""Member directory, profile, and reputation views.

This module is wired by binderbridge.views; shared app helpers are injected at runtime.
"""

def render_members(user, query, notice=None, status="info"):
    q = query.get("q", [""])[0].strip()
    where = ["users.id != ?"]
    params = [user["id"]]
    where.append("users.is_banned = 0")
    if q:
        where.append("(users.display_name LIKE ? OR users.username LIKE ? OR collection_items.card_name LIKE ?)")
        term = f"%{q}%"
        params.extend([term, term, term])
    members = rows(
        f"""
        SELECT
            users.id,
            users.username,
            users.display_name,
            users.bio,
            COUNT(collection_items.id) AS unique_cards,
            COALESCE(SUM(collection_items.quantity_for_trade), 0) AS trade_cards
        FROM users
        LEFT JOIN collection_items ON collection_items.user_id = users.id AND collection_items.quantity_for_trade > 0 AND collection_items.is_public = 1
        WHERE {' AND '.join(where)}
        GROUP BY users.id
        ORDER BY users.display_name COLLATE NOCASE
        """,
        params,
    )
    cards = "".join(
        f"""
        <article class="member-card">
            <div>
                <h2>{e(member["display_name"])}</h2>
                <p class="muted">@{e(member["username"])}</p>
            </div>
            <div class="member-stats">
                <span><strong>{e(member["trade_cards"])}</strong> trade cards</span>
                <span><strong>{e(member["unique_cards"])}</strong> entries</span>
            </div>
            <a class="button secondary full" href="/members/{member["id"]}">Open binder</a>
        </article>
        """
        for member in members
    ) or '<div class="empty-state">No members match that search.</div>'
    member_datalists = render_datalist("member-search-suggestions", member_search_suggestions(user["id"]))
    content = f"""
    <section class="section-heading">
        <div>
            <p class="eyebrow">Members</p>
            <h1>Browse trade binders</h1>
        </div>
    </section>
    <form class="filter-bar" method="get" action="/members">
        <label class="search-field">Search
            <input name="q" value="{e(q)}" placeholder="Member or card" list="member-search-suggestions">
        </label>
        <button class="button secondary" type="submit">Search</button>
        {member_datalists}
    </form>
    <section class="member-grid">{cards}</section>
    """
    return render_layout(user, "Members", content, active="browse", notice=notice, status=status)


def feedback_rating_label(rating):
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        rating = 0
    return f"{rating}/5"


def feedback_count_label(count):
    count = int(count or 0)
    return f"{count} feedback" if count == 1 else f"{count} feedback entries"


def render_reputation_summary(summary, feedback_rows):
    feedback_count = int(summary["feedback_count"])
    completed_count = int(summary["completed_trade_count"])
    if feedback_count:
        average_label = f"{summary['average_rating']:.1f}/5"
        positive_label = f"{summary['positive_count']} positive"
    else:
        average_label = "No feedback yet"
        positive_label = "No ratings"
    recent_items = "".join(
        f"""
        <li>
            <div>
                <strong>{e(feedback_rating_label(feedback["rating"]))}</strong>
                <span>{e(feedback["updated_at"][:10])} from {e(feedback["reviewer_name"])}</span>
            </div>
            {f'<p>{e(feedback["body"])}</p>' if feedback["body"] else '<p class="muted compact">No written comment.</p>'}
        </li>
        """
        for feedback in feedback_rows
    ) or '<li class="muted compact">Completed trades can receive feedback from either participant.</li>'
    return f"""
    <div class="reputation-summary">
        <div>
            <strong>{e(average_label)}</strong>
            <span>{e(feedback_count_label(feedback_count))}</span>
        </div>
        <div>
            <strong>{e(completed_count)}</strong>
            <span>completed trades</span>
        </div>
        <div>
            <strong>{e(positive_label)}</strong>
            <span>rated 4 or higher</span>
        </div>
    </div>
    <ul class="feedback-list compact-feedback-list">{recent_items}</ul>
    """


def public_profile_stats(member_id):
    trade = row(
        """
        SELECT
            COUNT(*) AS unique_trade_cards,
            COALESCE(SUM(quantity_for_trade), 0) AS available_trade_quantity,
            COALESCE(SUM(CAST(COALESCE(NULLIF(price_usd, ''), '0') AS REAL) * quantity_for_trade), 0) AS trade_value
        FROM collection_items
        WHERE user_id = ? AND quantity_for_trade > 0 AND is_public = 1
        """,
        (member_id,),
    )
    wants = row(
        """
        SELECT COUNT(*) AS count, COALESCE(SUM(desired_quantity), 0) AS desired_quantity
        FROM want_items
        WHERE user_id = ? AND is_public = 1
        """,
        (member_id,),
    )
    groups = row(
        """
        SELECT COUNT(*) AS count
        FROM card_groups
        WHERE user_id = ? AND is_public = 1
        """,
        (member_id,),
    )
    return {
        "unique_trade_cards": int(row_value(trade, "unique_trade_cards", 0) or 0),
        "available_trade_quantity": int(row_value(trade, "available_trade_quantity", 0) or 0),
        "trade_value": float(row_value(trade, "trade_value", 0) or 0),
        "wants_count": int(row_value(wants, "count", 0) or 0),
        "desired_quantity": int(row_value(wants, "desired_quantity", 0) or 0),
        "groups_count": int(row_value(groups, "count", 0) or 0),
    }


def render_public_trade_card(member, item):
    thumb = f'<img class="card-thumb" src="{e(item["image_url"])}" alt="">' if item["image_url"] else '<span class="card-thumb placeholder"></span>'
    scryfall_link = f'<a class="subtle" href="{e(item["scryfall_uri"])}" target="_blank" rel="noreferrer">Scryfall</a>' if item["scryfall_uri"] else ""
    type_line = f'<span class="subtle">{e(item["type_line"])}</span>' if item["type_line"] else ""
    return f"""
    <article class="public-card-row">
        <div class="card-cell">
            {thumb}
            <div>
                <strong>{e(item["card_name"])}</strong>
                {type_line}
                <span class="subtle">{e(game_label(item["game"]))} - {e(item["set_name"] or "Any set")} {e("(" + item["set_code"] + ")" if item["set_code"] else "")} {e("#" + item["collector_number"] if item["collector_number"] else "")}</span>
                {scryfall_link}
            </div>
        </div>
        <div class="public-card-meta">
            <span class="pill">{e(item["condition"])}</span>
            <span class="pill">{e(item["finish"])}</span>
            <span class="pill">{e(item["language"])}</span>
            {price_pill(item)}
        </div>
        <form class="inline-trade-form public-profile-trade-form" method="get" action="/trades/new">
            <input type="hidden" name="recipient_id" value="{e(member["id"])}">
            <label class="mini-input trade-request-quantity">Qty
                <input type="number" min="1" max="{e(item["quantity_for_trade"])}" name="request_{e(item["id"])}" value="1" aria-label="Quantity to request for {e(item["card_name"])}">
            </label>
            <button class="button primary small" type="submit">Propose trade</button>
        </form>
        <div class="public-card-availability">
            <strong>{e(item["quantity_for_trade"])}</strong>
            <span>available</span>
        </div>
    </article>
    """


def render_public_want_profile_item(want):
    scryfall_link = f'<a href="{e(want["scryfall_uri"])}" target="_blank" rel="noreferrer">Scryfall</a>' if want["scryfall_uri"] else ""
    budget_cap = normalize_price_usd(row_value(want, "budget_cap_usd", ""))
    printing_note = row_value(want, "preferred_printing_notes", "")
    preference_parts = [
        want["set_name"] or "Any set",
        want["condition"] or "Any condition",
        want["finish"] or "Any finish",
        want["language"] or "Any language",
    ]
    return f"""
    <li class="public-want-row">
        <div>
            <strong>{e(want["card_name"])}</strong>
            <span>{e(game_label(want["game"]))} - wants {e(want["desired_quantity"])}</span>
            <span class="subtle">{e(want_priority_label(row_value(want, "priority", "normal")))} priority{f' - up to ${e(budget_cap)} each' if budget_cap else ''}</span>
            <span class="subtle">{e(" - ".join(part for part in preference_parts if part))}</span>
            {f'<span class="subtle"><strong>Preferred printing:</strong> {e(printing_note)}</span>' if printing_note else ''}
            {scryfall_link}
        </div>
        {price_pill(want)}
    </li>
    """


def render_public_group_profile_card(member, group):
    description = f'<p>{e(group["description"])}</p>' if group["description"] else ""
    return f"""
    <article class="public-group-card">
        <div>
            <strong><a href="/members/{member["id"]}/groups/{group["id"]}">{e(group["name"])}</a></strong>
            <span class="subtle">{e(group_type_label(group["group_type"]))} - {e(group_count_label(group))}</span>
            {description}
        </div>
        <a class="button secondary small" href="/members/{member["id"]}/groups/{group["id"]}">Open</a>
    </article>
    """


def render_member_detail(user, member_id, query=None, notice=None, status="info"):
    query = query or {}
    member = row("SELECT * FROM users WHERE id = ?", (member_id,))
    if not member or member["id"] == user["id"] or member["is_banned"]:
        return None
    total_count = row(
        """
        SELECT COUNT(*) AS count
        FROM collection_items
        WHERE user_id = ? AND quantity_for_trade > 0 AND is_public = 1
        """,
        (member_id,),
    )["count"]
    page, per_page, page_count, offset = pagination_state(query, total_count)
    current_sort, current_dir = sort_state(query, CARD_SORT_OPTIONS)
    order_clause = sort_order_clause(
        query,
        CARD_SORT_OPTIONS,
        COLLECTION_SORT_SQL,
        fallback=("card_name COLLATE NOCASE", "set_name COLLATE NOCASE"),
    )
    collection = rows(
        f"""
        SELECT *
        FROM collection_items
        WHERE user_id = ? AND quantity_for_trade > 0 AND is_public = 1
        ORDER BY {order_clause}
        LIMIT ? OFFSET ?
        """,
        (member_id, per_page, offset),
    )
    stats = public_profile_stats(member_id)
    wants = rows(
        """
        SELECT *
        FROM want_items
        WHERE user_id = ? AND is_public = 1
        ORDER BY card_name COLLATE NOCASE, set_name COLLATE NOCASE
        """,
        (member_id,),
    )
    public_groups = public_member_group_rows(member_id)
    collection_rows = "".join(render_public_trade_card(member, item) for item in collection)
    collection_list = f'<div class="public-card-list">{collection_rows}</div>' if collection else '<div class="empty-state">This member has no trade cards listed.</div>'
    pagination = render_pagination(f"/members/{member_id}", query, total_count, page, per_page, page_count)
    want_list = "".join(render_public_want_profile_item(want) for want in wants) or '<li class="muted">No public wants listed.</li>'
    group_list = "".join(render_public_group_profile_card(member, group) for group in public_groups) or '<div class="empty-state compact-empty">No public groups listed.</div>'
    profile_details = []
    if member["bio"]:
        profile_details.append(f'<p>{e(member["bio"])}</p>')
    if member["public_email"] and member["email"]:
        profile_details.append(f'<p><strong>Email:</strong> <a href="mailto:{e(member["email"])}">{e(member["email"])}</a></p>')
    profile_block = "".join(profile_details) or '<p class="muted">No profile details yet.</p>'
    reputation_block = render_reputation_summary(reputation_summary(member_id), recent_feedback_for_user(member_id, limit=3))
    content = f"""
    <section class="section-heading">
        <div>
            <p class="eyebrow">Public profile</p>
            <h1>{e(member["display_name"])}</h1>
            <p class="lead">@{e(member["username"])}</p>
        </div>
        <div class="actions">
            <a class="button primary" href="/trades/new?recipient_id={member["id"]}">Propose trade</a>
            <a class="button secondary" href="/browse">Back to browse</a>
        </div>
    </section>
    <section class="public-profile-summary">
        <article class="panel public-profile-card">
            <div class="panel-heading">
                <h2>Profile</h2>
            </div>
            <div class="profile-detail">{profile_block}</div>
        </article>
        <article class="metric">
            <span>{e(stats["available_trade_quantity"])}</span>
            <p>cards available for trade</p>
        </article>
        <article class="metric">
            <span>{e(stats["wants_count"])}</span>
            <p>public wanted cards</p>
        </article>
        <article class="metric">
            <span>{e(stats["groups_count"])}</span>
            <p>public binders and lists</p>
        </article>
    </section>
    <section class="public-profile-grid">
        <article class="panel public-profile-main">
            <div class="panel-heading padded">
                <div>
                    <h2>Trade availability</h2>
                    <p class="muted compact">{e(stats["unique_trade_cards"])} public entr{'y' if stats["unique_trade_cards"] == 1 else 'ies'} - {e(f"${stats['trade_value']:.2f}")} listed value</p>
                </div>
                <a class="button primary small" href="/trades/new?recipient_id={member["id"]}">Propose trade</a>
            </div>
            {render_sort_bar(f"/members/{member_id}", query, CARD_SORT_OPTIONS, current_sort, current_dir)}
            {collection_list}
            {pagination}
        </article>
        <article class="panel">
            <div class="panel-heading">
                <h2>Reputation</h2>
            </div>
            {reputation_block}
            <div class="panel-heading with-gap">
                <h2>Public binders and lists</h2>
            </div>
            <div class="public-group-list">{group_list}</div>
            <div class="panel-heading with-gap">
                <h2>Wishlist</h2>
                <a class="button secondary small" href="/trades/new?recipient_id={member["id"]}">Offer cards</a>
            </div>
            <ul class="stack-list">{want_list}</ul>
        </article>
    </section>
    """
    return render_layout(user, member["display_name"], content, active="browse", notice=notice, status=status)


__all__ = ['render_members', 'feedback_rating_label', 'feedback_count_label', 'render_reputation_summary', 'public_profile_stats', 'render_public_trade_card', 'render_public_want_profile_item', 'render_public_group_profile_card', 'render_member_detail']
