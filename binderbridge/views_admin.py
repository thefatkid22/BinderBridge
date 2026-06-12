"""Admin panel, audit log, invite, and dispute views.

This module is wired by binderbridge.views; shared app helpers are injected at runtime.
"""

def render_staff_admin(user, notice=None, status="info", invite_result=None):
    can_moderate = user_has_capability(user, CAP_MODERATE_USERS)
    can_disputes = user_has_capability(user, CAP_MODERATE_DISPUTES)
    can_logs = user_has_capability(user, CAP_VIEW_AUDIT_LOG)
    can_invites = user_has_capability(user, CAP_MANAGE_INVITES)
    action_links = "".join(
        link
        for enabled, link in (
            (can_disputes, '<a class="button secondary" href="/admin/disputes">Trade issue queue</a>'),
            (can_logs, '<a class="button secondary" href="/admin/logs">Activity log</a>'),
        )
        if enabled
    )
    invite_panel = ""
    if can_invites:
        invite_rows = "".join(render_registration_invite_row(invite) for invite in registration_invite_rows())
        invite_rows = invite_rows or '<li class="muted">No invites yet.</li>'
        result_panel = ""
        if invite_result:
            result_panel = f"""
            <div class="invite-result span-2">
                <strong>Invite link</strong>
                <input readonly value="{e(invite_result["link"])}" onclick="this.select()">
            </div>
            """
        invite_panel = f"""
        <article class="panel invite-settings">
            <form class="form-grid compact-form" method="post" action="/admin/invites">
                <div class="span-2 panel-heading"><h2>Create invite</h2><span class="pill">{e(role_label(user))}</span></div>
                <label class="span-2">Recipient email
                    <input required name="email" type="email" maxlength="254" autocomplete="email">
                </label>
                <div class="form-actions span-2"><button class="button primary" type="submit">Create invite</button></div>
                {result_panel}
            </form>
            <div class="panel-heading with-gap"><h2>Recent invites</h2></div>
            <ul class="stack-list compact-stack">{invite_rows}</ul>
        </article>
        """
    user_panel = ""
    if can_moderate:
        user_rows = "".join(render_admin_user_row(user, managed_user) for managed_user in admin_user_list())
        user_panel = f"""
        <section class="panel flush">
            <div class="table-wrap">
                <table class="admin-table responsive-card-table">
                    <thead><tr><th>User</th><th>Status</th><th>Activity</th><th>Controls</th></tr></thead>
                    <tbody>{user_rows}</tbody>
                </table>
            </div>
        </section>
        """
    content = f"""
    <section class="section-heading">
        <div>
            <p class="eyebrow">Staff</p>
            <h1>{e(role_label(user))} control panel</h1>
            <p class="muted">Your tools are limited to the responsibilities assigned to this role.</p>
        </div>
        <div class="actions">{action_links}</div>
    </section>
    {invite_panel}
    {user_panel}
    """
    return render_layout(user, "Staff", content, active="admin", notice=notice, status=status)


def render_admin(user, notice=None, status="info", invite_result=None):
    if user_role(user) not in (ROLE_OWNER, ROLE_ADMIN):
        return render_staff_admin(user, notice=notice, status=status, invite_result=invite_result)
    users = admin_user_list()
    user_rows = "".join(render_admin_user_row(user, managed_user) for managed_user in users)
    trade_policy = trade_policy_settings()
    threshold = trade_policy["trusted_threshold"]
    fairness = trade_policy["fairness"]
    one_way_options = option_tags(ONE_WAY_TRADE_POLICY_OPTIONS, trade_policy["one_way_policy"])
    integration_policy = integration_access_settings()
    api_access_options = option_tags(INTEGRATION_ACCESS_POLICY_OPTIONS, integration_policy["api_policy"])
    webhook_access_options = option_tags(INTEGRATION_ACCESS_POLICY_OPTIONS, integration_policy["webhook_policy"])
    invite_only_checked = checked(invite_only_registration_enabled())
    invite_mode_label = "Invite-only" if invite_only_registration_enabled() else "Open registration"
    smtp_configured = smtp_invites_configured()
    smtp_status = "SMTP configured" if smtp_configured else "Manual link fallback"
    invite_panel_title = "Email invites" if smtp_configured else "Invite links"
    invite_label = "Invite email" if smtp_configured else "Recipient email"
    invite_button_label = "Create and email invite" if smtp_configured else "Create invite link"
    invite_help = (
        "SMTP is configured, so BinderBridge will email the invite link automatically."
        if smtp_configured
        else "SMTP is not configured, so BinderBridge will create a copyable invite link for manual email."
    )
    invite_rows = "".join(render_registration_invite_row(invite) for invite in registration_invite_rows())
    invite_rows = invite_rows or '<li class="muted">No invites yet.</li>'
    invite_result_panel = ""
    if invite_result:
        invite_result_panel = f"""
        <div class="invite-result span-2">
            <strong>Invite link</strong>
            <input readonly value="{e(invite_result["link"])}" onclick="this.select()">
            <span class="subtle">Expires {e(invite_result["expires_at"][:10])}</span>
        </div>
        """
    backups = backup_status()
    auto_backup = backups["automatic"]
    auto_backup_checked = checked(auto_backup["enabled"])
    auto_backup_mode = "Enabled" if auto_backup["enabled"] else "Paused"
    auto_next = auto_backup["next_run"]
    auto_next_label = "Paused" if not auto_backup["enabled"] else "Due now" if auto_next == "due" else auto_next[:16].replace("T", " ") if auto_next else "Not scheduled"
    auto_success_label = auto_backup["last_success"][:16].replace("T", " ") if auto_backup["last_success"] else "Never"
    auto_error = f'<p class="notice error compact">{e(auto_backup["last_error"])}</p>' if auto_backup["last_error"] else ""
    backup_rows = "".join(
        f"""
        <li>
            <strong>{e(archive["name"])}</strong>
            <span>{e(archive["size_label"])} - {e(archive["created_at"])}</span>
        </li>
        """
        for archive in backups["archives"]
    ) or '<li class="muted">No backups created yet.</li>'
    recent_admin_logs = admin_audit_log_rows(limit=6)
    recent_admin_log_rows = "".join(render_admin_audit_log_item(item) for item in recent_admin_logs) or '<li class="muted">No admin actions logged yet.</li>'
    open_dispute_count = open_trade_dispute_count()
    recent_disputes = trade_dispute_admin_rows({"status": ""}, limit=5)
    recent_dispute_rows = "".join(render_trade_dispute_summary_item(item) for item in recent_disputes) or '<li class="muted">No trade issues reported yet.</li>'
    onboarding_panel = render_admin_onboarding_checklist()
    content = f"""
    <section class="section-heading">
        <div>
            <p class="eyebrow">Admin</p>
            <h1>User control panel</h1>
        </div>
        <div class="actions">
            <a class="button secondary" href="/admin/jobs">Import and jobs</a>
            <a class="button secondary" href="/admin/collection-health">Collection health</a>
            <a class="button secondary" href="/admin/health">Maintenance health</a>
        </div>
    </section>
    {onboarding_panel}
    <section class="admin-settings-grid">
        <form class="panel form-grid compact-form trade-policy-settings span-2" method="post" action="/admin/trade-policy">
            <div class="span-2 panel-heading">
                <h2>Trade policy</h2>
                <span class="pill">{e(trade_policy["one_way_policy_label"])}</span>
            </div>
            <label>One-way trades
                <select name="one_way_trade_policy">{one_way_options}</select>
            </label>
            <label>Completed trades to earn trust
                <input name="trusted_trade_threshold" type="number" min="1" step="1" value="{threshold}">
            </label>
            <label>Warning threshold %
                <input name="fairness_warn_percent" type="number" min="0" step="0.1" value="{e(fairness["warn_percent"])}">
            </label>
            <label>Block threshold %
                <input name="fairness_block_percent" type="number" min="0" step="0.1" value="{e(fairness["block_percent"])}">
            </label>
            <label>Dispute escalation after days
                <input name="dispute_escalation_days" type="number" min="1" step="1" value="{e(trade_policy["dispute_escalation_days"])}">
            </label>
            <p class="muted span-2 compact">Set block threshold to 0 to warn only. Unpriced cards count as $0.00. Evidence retention is managed from Maintenance Health.</p>
            <div class="form-actions span-2">
                <button class="button primary" type="submit">Save trade policy</button>
            </div>
        </form>
        <form class="panel form-grid compact-form integration-policy-settings span-2" method="post" action="/admin/integration-policy">
            <div class="span-2 panel-heading">
                <h2>Integration access</h2>
                <span class="pill">API: {e(integration_policy["api_policy_label"])}</span>
                <span class="pill">Webhooks: {e(integration_policy["webhook_policy_label"])}</span>
            </div>
            <label>API tokens
                <select name="api_access_policy">{api_access_options}</select>
            </label>
            <label>Webhooks
                <select name="webhook_access_policy">{webhook_access_options}</select>
            </label>
            <p class="muted span-2 compact">Controls who can see account integration tools, create new tokens or webhooks, use API tokens, and receive webhook deliveries.</p>
            <div class="form-actions span-2">
                <button class="button primary" type="submit">Save integration access</button>
            </div>
        </form>
        <form class="panel form-grid compact-form registration-settings" method="post" action="/admin/registration-settings">
            <div class="span-2 panel-heading">
                <h2>Registration</h2>
                <span class="pill">{e(invite_mode_label)}</span>
            </div>
            <label class="checkbox-line span-2">
                <input type="checkbox" name="invite_only_registration" value="1"{invite_only_checked}>
                Require an invite link for new accounts
            </label>
            <div class="form-actions span-2">
                <button class="button primary" type="submit">Save registration mode</button>
            </div>
        </form>
        <article class="panel invite-settings" id="admin-invites">
            <form class="form-grid compact-form" method="post" action="/admin/invites">
                <div class="span-2 panel-heading">
                    <h2>{e(invite_panel_title)}</h2>
                    <span class="pill">{e(smtp_status)}</span>
                </div>
                <label class="span-2">{e(invite_label)}
                    <input required name="email" type="email" maxlength="254" autocomplete="email">
                </label>
                <p class="muted compact span-2">{e(invite_help)}</p>
                <div class="form-actions span-2">
                    <button class="button primary" type="submit">{e(invite_button_label)}</button>
                </div>
                {invite_result_panel}
            </form>
            <div class="panel-heading with-gap">
                <h2>Recent invites</h2>
            </div>
            <ul class="stack-list compact-stack">{invite_rows}</ul>
        </article>
        <article class="panel maintenance-panel">
            <div class="panel-heading">
                <h2>Backup and restore</h2>
                <span class="pill">SQLite</span>
            </div>
            <div class="maintenance-grid">
                <form class="backup-action-card" method="post" action="/admin/backups/create">
                    <strong>Download backup</strong>
                    <span class="subtle">Database size: {e(backups["database_size_label"])}</span>
                    <button class="button primary" type="submit">Download backup</button>
                </form>
                <form class="backup-action-card" method="post" action="/admin/backups/settings">
                    <strong>Automatic backups</strong>
                    <span class="status {'accepted' if auto_backup["enabled"] else 'pending'}">{e(auto_backup_mode)}</span>
                    <label class="checkbox-line">
                        <input type="checkbox" name="automatic_backup_enabled" value="1"{auto_backup_checked}>
                        Run scheduled backups
                    </label>
                    <label>Every hours
                        <input name="automatic_backup_interval_hours" type="number" min="1" step="1" value="{e(auto_backup["interval_hours"])}">
                    </label>
                    <label>Keep newest
                        <input name="automatic_backup_retention_count" type="number" min="1" step="1" value="{e(auto_backup["retention_count"])}">
                    </label>
                    <label>Delete older than days
                        <input name="automatic_backup_retention_days" type="number" min="0" step="1" value="{e(auto_backup["retention_days"])}">
                    </label>
                    <span class="subtle">Last success: {e(auto_success_label)}</span>
                    <span class="subtle">Next run: {e(auto_next_label)}</span>
                    {auto_error}
                    <button class="button primary" type="submit">Save schedule</button>
                </form>
                <form class="backup-action-card" method="post" action="/admin/backups/run">
                    <strong>Run scheduled backup now</strong>
                    <span class="subtle">Creates an automatic backup and applies retention cleanup.</span>
                    <button class="button secondary" type="submit">Run now</button>
                </form>
                <form class="backup-action-card" method="post" action="/admin/backups/restore" enctype="multipart/form-data">
                    <strong>Restore backup</strong>
                    <span class="subtle">Accepts BinderBridge backup zips or SQLite database files.</span>
                    <input required type="file" name="backup_file" accept=".zip,.sqlite3,.sqlite,.db,application/zip">
                    <label>Confirm restore
                        <input required name="confirmation" placeholder="Type RESTORE">
                    </label>
                    <button class="button danger" type="submit" onclick="return confirm('Restore this backup? Current data will be replaced after a safety backup is created.')">Restore backup</button>
                </form>
            </div>
            <div class="panel-heading with-gap">
                <h2>Recent backups</h2>
                <span class="muted compact">{e(backups["backup_dir"])}</span>
            </div>
            <ul class="stack-list">{backup_rows}</ul>
        </article>
        <article class="panel admin-log-panel">
            <div class="panel-heading">
                <h2>Admin activity log</h2>
                <a class="button secondary small" href="/admin/logs">Open full log</a>
            </div>
            <ul class="stack-list compact-stack">{recent_admin_log_rows}</ul>
        </article>
        <article class="panel admin-dispute-panel">
            <div class="panel-heading">
                <h2>Trade issue queue</h2>
                <span class="pill">{e(open_dispute_count)} open</span>
                <a class="button secondary small" href="/admin/disputes">Review issues</a>
            </div>
            <ul class="stack-list compact-stack">{recent_dispute_rows}</ul>
        </article>
    </section>
    <section class="panel flush">
        <div class="table-wrap">
            <table class="admin-table">
                <thead>
                    <tr>
                        <th>User</th>
                        <th>Status</th>
                        <th>Activity</th>
                        <th>Controls</th>
                    </tr>
                </thead>
                <tbody>{user_rows}</tbody>
            </table>
        </div>
    </section>
    """
    return render_layout(user, "Admin", content, active="admin", notice=notice, status=status)


def render_admin_onboarding_action(item):
    label = item.get("action_label", "Open")
    action_url = item.get("action_url", "#")
    disabled = " disabled" if item.get("disabled") else ""
    if item.get("action_method") == "post":
        return f"""
        <form class="onboarding-action" method="post" action="{e(action_url)}">
            <input type="hidden" name="redirect_to" value="/admin">
            <button class="button secondary small" type="submit"{disabled}>{e(label)}</button>
        </form>
        """
    return f'<a class="button ghost small" href="{e(action_url)}">{e(label)}</a>'


def render_admin_onboarding_item(item):
    complete = bool(item.get("complete"))
    status_class = "accepted" if complete else "pending"
    if str(item.get("status_label", "")).lower() == "error":
        status_class = "declined"
    return f"""
    <li class="onboarding-row {'complete' if complete else 'incomplete'}">
        <div class="onboarding-marker" aria-hidden="true">{e("Done" if complete else "Next")}</div>
        <div class="onboarding-main">
            <strong>{e(item.get("title", "Onboarding step"))}</strong>
            <span>{e(item.get("detail", ""))}</span>
        </div>
        <div class="onboarding-side">
            <span class="status {status_class}">{e(item.get("status_label", "Pending"))}</span>
            {render_admin_onboarding_action(item)}
        </div>
    </li>
    """


def render_admin_onboarding_checklist():
    checklist = admin_onboarding_checklist()
    rows_html = "".join(render_admin_onboarding_item(item) for item in checklist["items"])
    complete_count = int(checklist["complete_count"])
    total = int(checklist["total"] or 1)
    progress = min(100, max(0, round((complete_count / total) * 100)))
    done_label = "Complete" if checklist["is_complete"] else "Getting started"
    return f"""
    <section class="panel onboarding-panel">
        <div class="panel-heading">
            <div>
                <h2>Onboarding checklist</h2>
                <p class="muted compact">A short setup path for new admins bringing a fresh BinderBridge site online.</p>
            </div>
            <span class="status {'accepted' if checklist["is_complete"] else 'pending'}">{e(done_label)}</span>
        </div>
        <div class="onboarding-progress-row">
            <span>{e(complete_count)} of {e(total)} complete</span>
            <div class="onboarding-progress" aria-label="{e(complete_count)} of {e(total)} onboarding steps complete">
                <span style="width: {e(progress)}%"></span>
            </div>
        </div>
        <ul class="stack-list onboarding-list">{rows_html}</ul>
    </section>
    """


def health_time_label(value):
    text = str(value or "").strip()
    return text[:16].replace("T", " ") if text else "Never"


def health_status_class(value):
    status = str(value or "").strip().lower()
    if status in ("done", "idle", "sent", "none", "ok", "completed"):
        return "accepted"
    if status in ("failed", "error", "disabled", "not_found"):
        return "declined"
    return "pending"


def health_severity_for_status(value):
    status = str(value or "").strip().lower()
    if status in ("failed", "error", "disabled", "not_found"):
        return "error"
    if status in ("pending", "processing", "queued", "warning", "paused"):
        return "warning"
    if status in ("done", "idle", "sent", "none", "ok", "completed", "enabled"):
        return "ok"
    return "info"


def health_severity_class(value):
    severity = str(value or "info").strip().lower()
    if severity not in ("ok", "info", "warning", "error"):
        severity = health_severity_for_status(severity)
    return f"severity-{severity}"


def health_count_statuses(counts, *statuses):
    wanted = {str(status) for status in statuses}
    total = 0
    for item in counts or ():
        if str(item["status"]) in wanted:
            total += int(item["count"] or 0)
    return total


def health_severity_for_counts(counts):
    if health_count_statuses(counts, "failed", "error", "disabled", "not_found"):
        return "error"
    if health_count_statuses(counts, "pending", "processing", "queued", "warning"):
        return "warning"
    return "ok"


def health_card_class(base, severity):
    return f'{base} health-severity-card {health_severity_class(severity)}'


def render_health_status_counts(counts):
    if not counts:
        return '<span class="muted compact">No queued records.</span>'
    return "".join(
        f'<span class="status {health_status_class(item["status"])}">{e(item["status"])}: {e(item["count"])}</span>'
        for item in counts
    )


def render_failed_notification_row(notification):
    error = row_value(notification, "email_error", "") or "No error message stored."
    return f"""
    <li>
        <div>
            <strong>{e(row_value(notification, "title", "Notification"))}</strong>
            <span class="subtle">{e(row_value(notification, "display_name", ""))} (@{e(row_value(notification, "username", ""))}) - {e(health_time_label(row_value(notification, "created_at", "")))}</span>
            <span class="subtle">{e(error)}</span>
        </div>
        <a class="button ghost small" href="{e(row_value(notification, "url", "/notifications") or "/notifications")}">Open</a>
    </li>
    """


def health_attention_setup_rows(setup_warnings):
    rows = []
    for warning in setup_warnings:
        severity = str(warning.get("severity", "info") or "info").lower()
        if severity not in ("error", "warning"):
            continue
        rows.append(
            f"""
            <li class="job-row attention-row {health_severity_class(severity)}">
                <div class="job-main">
                    <strong>{e(warning.get("title", "Setup warning"))}</strong>
                    <span class="subtle">{e(warning.get("detail", ""))}</span>
                </div>
                <div class="job-actions">
                    <span class="status {health_status_class(severity)}">{e(severity.title())}</span>
                    {f'<a class="button ghost small" href="{e(warning["action_url"])}">{e(warning.get("action_label", "Open"))}</a>' if warning.get("action_url") else ''}
                </div>
            </li>
            """
        )
    return "".join(rows)


def render_health_attention_panel(dashboard, setup_warnings):
    retry_statuses = tuple(globals().get("JOB_DASHBOARD_RETRY_STATUSES", ("failed", "not_found", "processing")))
    scryfall_jobs = [job for job in dashboard["scryfall_jobs"] if row_value(job, "status", "") in retry_statuses]
    price_jobs = [job for job in dashboard["price_jobs"] if row_value(job, "status", "") in retry_statuses]
    failed_notifications = dashboard["failed_notifications"]
    setup_rows = health_attention_setup_rows(setup_warnings)
    scryfall_rows = "".join(admin_job_scryfall_row(job, return_to="/admin/health") for job in scryfall_jobs[:4])
    price_rows = "".join(admin_job_price_row(job, return_to="/admin/health") for job in price_jobs[:4])
    notification_rows = "".join(admin_job_notification_row(item, return_to="/admin/health") for item in failed_notifications[:4])
    setup_count = setup_rows.count('class="job-row')
    total_items = len(scryfall_jobs) + len(price_jobs) + len(failed_notifications) + setup_count
    if not total_items:
        return f"""
        <section class="{health_card_class('panel health-attention-panel', 'ok')}">
            <div class="panel-heading">
                <div>
                    <h2>Needs attention</h2>
                    <p class="muted compact">No failed jobs or warning-level setup issues are waiting right now.</p>
                </div>
                <span class="status accepted">Healthy</span>
            </div>
        </section>
        """
    setup_section = f"""
        <div class="attention-group">
            <div class="attention-group-heading">
                <h3>Setup warnings</h3>
                <span class="pill">{e(setup_count)}</span>
            </div>
            <ul class="stack-list job-list">{setup_rows}</ul>
        </div>
    """ if setup_rows else ""
    scryfall_section = f"""
        <div class="attention-group">
            <div class="attention-group-heading">
                <h3>Scryfall jobs</h3>
                <span class="pill">{e(len(scryfall_jobs))}</span>
            </div>
            <ul class="stack-list job-list">{scryfall_rows}</ul>
        </div>
    """ if scryfall_rows else ""
    price_section = f"""
        <div class="attention-group">
            <div class="attention-group-heading">
                <h3>Price jobs</h3>
                <span class="pill">{e(len(price_jobs))}</span>
            </div>
            <ul class="stack-list job-list">{price_rows}</ul>
        </div>
    """ if price_rows else ""
    notification_section = f"""
        <div class="attention-group">
            <div class="attention-group-heading">
                <h3>Failed notification emails</h3>
                <span class="pill">{e(len(failed_notifications))}</span>
            </div>
            <ul class="stack-list job-list">{notification_rows}</ul>
        </div>
    """ if notification_rows else ""
    return f"""
    <section class="{health_card_class('panel health-attention-panel', 'error' if failed_notifications or scryfall_jobs or price_jobs else 'warning')}">
        <div class="panel-heading">
            <div>
                <h2>Needs attention</h2>
                <p class="muted compact">Failed or recoverable work is grouped here with direct retry actions.</p>
            </div>
            <div class="status-row">
                <span class="status declined">{e(total_items)} item{'s' if total_items != 1 else ''}</span>
                <a class="button ghost small" href="/admin/jobs">Open full dashboard</a>
            </div>
        </div>
        <div class="attention-grid">
            {setup_section}
            {scryfall_section}
            {price_section}
            {notification_section}
        </div>
    </section>
    """


def render_setup_warning_item(warning):
    severity = str(warning.get("severity", "info") or "info").lower()
    status_class = "declined" if severity == "error" else "pending" if severity == "warning" else "accepted"
    action = ""
    if warning.get("action_url") and warning.get("action_label"):
        action = f'<a class="button ghost small" href="{e(warning["action_url"])}">{e(warning["action_label"])}</a>'
    return f"""
    <li class="setup-warning-row">
        <div>
            <span class="status {status_class}">{e(severity.title())}</span>
            <strong>{e(warning.get("title", "Setup warning"))}</strong>
            <span class="subtle">{e(warning.get("detail", ""))}</span>
        </div>
        {action}
    </li>
    """


def render_collection_health_issue(label, count, severe=False):
    status_class = "declined" if count and severe else "pending" if count else "accepted"
    return f'<span class="status {status_class}">{e(label)}: {e(count)}</span>'


def render_collection_health_visibility(counts):
    return "".join(
        f'<span class="pill">{e(VISIBILITY_LABELS[key])}: {e(counts.get(key, 0))}</span>'
        for key in VISIBILITY_LABELS
    )


def render_collection_health_user_row(item):
    issue_statuses = "".join(
        (
            render_collection_health_issue("Duplicates", item["duplicate_rows"], severe=True),
            render_collection_health_issue("Missing Scryfall", item["missing_scryfall"]),
            render_collection_health_issue("Invalid finishes", item["invalid_finishes"], severe=True),
            render_collection_health_issue("Stale prices", item["stale_prices"]),
        )
    )
    banned = '<span class="status declined">Banned</span>' if item["is_banned"] else ""
    return f"""
    <tr class="job-row {health_severity_class(item["severity"])}">
        <td data-label="User"><strong>{e(item["display_name"])}</strong><span class="subtle">@{e(item["username"])} - {e(role_label(item["role"]))}</span>{banned}</td>
        <td data-label="Health">
            <div class="collection-health-meter">
                <strong>{e(item["health_percent"])}% healthy</strong>
                <progress max="100" value="{e(item["health_percent"])}">{e(item["health_percent"])}%</progress>
                <span class="subtle">{e(item["affected_cards"])} of {e(item["total_cards"])} entries need attention</span>
            </div>
        </td>
        <td data-label="Issues"><div class="status-stack health-issue-stack">{issue_statuses}</div></td>
        <td data-label="Sharing"><div class="status-row">{render_collection_health_visibility(item["visibility"])}</div><span class="subtle">Values: {e(VALUE_VISIBILITY_LABELS.get(item["collection_value_visibility"], "All members"))}</span></td>
        <td data-label="Actions" class="table-actions"><a class="button secondary small" href="/members/{e(item["user_id"])}">Open profile</a></td>
    </tr>
    """


def render_admin_collection_health(user, notice=None, status="info"):
    dashboard = collection_health_dashboard()
    summary = dashboard["summary"]
    user_rows = "".join(render_collection_health_user_row(item) for item in dashboard["users"])
    user_rows = user_rows or '<tr><td class="empty-state" colspan="5">No collection cards have been added yet.</td></tr>'
    visibility_metrics = "".join(
        f'<article class="metric"><span>{e(summary["visibility"][key])}</span><p>{e(label)}</p></article>'
        for key, label in VISIBILITY_OPTIONS
    )
    value_visibility_metrics = "".join(
        f'<span class="pill">{e(label)}: {e(summary["value_visibility"][key])}</span>'
        for key, label in VALUE_VISIBILITY_OPTIONS
    )
    content = f"""
    <section class="section-heading">
        <div><p class="eyebrow">Admin</p><h1>Collection health</h1><p class="muted">Review collection quality, Scryfall coverage, price freshness, and sharing choices across the site.</p></div>
        <div class="actions"><a class="button secondary" href="/admin">Back to admin</a><a class="button secondary" href="/admin/jobs">Import and jobs</a><a class="button secondary" href="/admin/health">Maintenance health</a></div>
    </section>
    <section class="metric-grid collection-health-summary">
        <article class="{health_card_class('metric', 'ok' if summary["health_percent"] == 100 else 'warning')}"><span>{e(summary["health_percent"])}%</span><p>healthy collection entries</p></article>
        <article class="{health_card_class('metric', 'error' if summary["duplicate_rows"] else 'ok')}"><span>{e(summary["duplicate_rows"])}</span><p>duplicate extra rows</p></article>
        <article class="{health_card_class('metric', 'warning' if summary["missing_scryfall"] else 'ok')}"><span>{e(summary["missing_scryfall"])}</span><p>missing Scryfall data</p></article>
        <article class="{health_card_class('metric', 'error' if summary["invalid_finishes"] else 'ok')}"><span>{e(summary["invalid_finishes"])}</span><p>invalid finishes</p></article>
        <article class="{health_card_class('metric', 'warning' if summary["stale_prices"] else 'ok')}"><span>{e(summary["stale_prices"])}</span><p>stale prices</p></article>
        <article class="{health_card_class('metric', 'warning' if summary["users_needing_attention"] else 'ok')}"><span>{e(summary["users_needing_attention"])}</span><p>users needing attention</p></article>
    </section>
    <section class="admin-settings-grid">
        <article class="panel span-2">
            <div class="panel-heading"><div><h2>Public/private coverage</h2><p class="muted compact">Collection item visibility and user-level collection value sharing choices.</p></div><span class="pill">{e(summary["total_cards"])} entries</span></div>
            <div class="metric-grid compact-stats privacy-coverage-grid">{visibility_metrics}</div><div class="status-row">{value_visibility_metrics}</div>
        </article>
        <article class="panel span-2">
            <div class="panel-heading"><div><h2>Health definitions</h2><p class="muted compact">Issues can overlap; the health percentage counts each affected card only once.</p></div><a class="button ghost small" href="/admin/jobs">Open jobs</a></div>
            <div class="detail-grid">
                <span>Duplicates</span><strong>Extra exact-match collection rows that can be merged by their owner.</strong>
                <span>Missing Scryfall</span><strong>MTG entries missing a Scryfall ID, URL, or canonical type line.</strong>
                <span>Invalid finishes</span><strong>Missing, malformed, non-canonical, or printing-incompatible finish values.</strong>
                <span>Stale prices</span><strong>Identified MTG printings not refreshed within {e(dashboard["price_stale_after_hours"])} hours.</strong>
            </div>
        </article>
    </section>
    <section class="panel flush collection-health-users">
        <div class="panel-heading padded"><div><h2>Health by user</h2><p class="muted compact">Users with the most affected cards appear first.</p></div><span class="pill">{e(summary["users_with_cards"])} collector{'s' if summary["users_with_cards"] != 1 else ''}</span></div>
        <div class="table-wrap"><table class="admin-table responsive-card-table"><thead><tr><th>User</th><th>Health</th><th>Issues</th><th>Sharing</th><th>Actions</th></tr></thead><tbody>{user_rows}</tbody></table></div>
    </section>
    """
    return render_layout(user, "Collection health", content, active="admin", notice=notice, status=status)


def render_admin_health(user, notice=None, status="info"):
    health = maintenance_health_status()
    dashboard = maintenance_job_dashboard(limit=6)
    database = health["database"]
    retention = health["retention"]
    retention_eligible = retention["eligible"]
    backups = health["backups"]
    auto_backup = backups["automatic"]
    backup_integrity = backups["integrity"]
    scryfall_bulk = health["scryfall"]["bulk"]
    scryfall_prices = health["scryfall"]["prices"]
    email = health["email"]
    notifications = health["notifications"]
    setup_warnings = health.get("setup_warnings", [])
    failed_email_count = int(notifications["failed_email_count"] or 0)
    pending_email_count = int(notifications["pending_email_count"] or 0)
    backup_severity = (
        "error"
        if auto_backup["last_error"] or backup_integrity.get("last_status") == "failed"
        else "warning"
        if not backups["archives"] or not auto_backup["enabled"] or backup_integrity.get("last_status") == "warning"
        else "ok"
    )
    scryfall_severity = (
        "error"
        if scryfall_bulk.get("status") in ("error", "failed") or scryfall_bulk.get("error") or scryfall_prices.get("status") in ("error", "failed") or scryfall_prices.get("error")
        else "warning"
        if int(dashboard["metrics"].get("scryfall_attention", 0) or 0)
        else "ok"
    )
    email_severity = "error" if failed_email_count else "warning" if pending_email_count else "info" if not email["configured"] else "ok"
    jobs_severity = (
        "error"
        if int(dashboard["metrics"].get("scryfall_attention", 0) or 0) or int(dashboard["metrics"].get("price_attention", 0) or 0) or failed_email_count
        else "warning"
        if health_count_statuses(health["job_counts"].get("scryfall"), "pending", "processing") or health_count_statuses(health["job_counts"].get("prices"), "pending", "processing")
        else "ok"
    )
    setup_severity = "error" if any(item.get("severity") == "error" for item in setup_warnings) else "warning" if any(item.get("severity") == "warning" for item in setup_warnings) else "ok"
    attention_panel = render_health_attention_panel(dashboard, setup_warnings)
    db_metrics = "".join(
        f'<article class="metric"><span>{e(count)}</span><p>{e(label)}</p></article>'
        for label, count in database["counts"]
    )
    archive_rows = "".join(
        f"""
        <li>
            <strong>{e(archive["name"])}</strong>
            <span class="subtle">{e(archive["size_label"])} - {e(archive["created_at"])}</span>
        </li>
        """
        for archive in backups["archives"]
    ) or '<li class="muted">No backups created yet.</li>'
    job_rows = "".join(
        f"""
        <article class="health-job-row {health_severity_class(health_severity_for_counts(job["counts"]))}">
            <strong>{e(job["label"])}</strong>
            <div class="status-row">{render_health_status_counts(job["counts"])}</div>
        </article>
        """
        for job in health["jobs"]
    )
    failed_rows = "".join(render_failed_notification_row(item) for item in notifications["recent_failed"])
    failed_rows = failed_rows or '<li class="muted">No failed email notifications.</li>'
    setup_warning_rows = "".join(render_setup_warning_item(item) for item in setup_warnings)
    setup_warning_rows = setup_warning_rows or '<li class="muted">No setup warnings right now.</li>'
    email_status = "Configured" if email["configured"] else "Not configured"
    email_status_class = "accepted" if email["configured"] else "pending"
    email_replay_disabled = "" if email["configured"] else " disabled"
    backup_integrity_label = (
        f'{backup_integrity["last_status"].title()} - {backup_integrity["checked"]} checked'
        if backup_integrity.get("last_status")
        else "Not checked"
    )
    backup_integrity_message = backup_integrity.get("last_message", "")
    smtp_detail = (
        f'{email["host"]}:{email["port"]}'
        if email["host"]
        else "SMTP host is not set; invite links and in-app notifications still work."
    )
    backup_error = f'<p class="notice error compact">{e(auto_backup["last_error"])}</p>' if auto_backup["last_error"] else ""
    bulk_error = f'<p class="notice error compact">{e(scryfall_bulk.get("error", ""))}</p>' if scryfall_bulk.get("error") else ""
    price_error = f'<p class="notice error compact">{e(scryfall_prices.get("error", ""))}</p>' if scryfall_prices.get("error") else ""
    retention_last_run = health_time_label(retention["last_run"]) if retention["last_run"] else "Never"
    content = f"""
    <section class="section-heading">
        <div>
            <p class="eyebrow">Admin</p>
            <h1>Maintenance health</h1>
        </div>
        <div class="actions">
            <a class="button secondary" href="/admin">Back to admin</a>
            <a class="button secondary" href="/admin/collection-health">Collection health</a>
            <a class="button secondary" href="/admin/logs">Activity log</a>
        </div>
    </section>
    {attention_panel}
    <section class="metric-grid">
        <article class="{health_card_class('metric', 'ok')}">
            <span>{e(database["size_label"])}</span>
            <p>database size</p>
        </article>
        <article class="{health_card_class('metric', backup_severity)}">
            <span>{e(len(backups["archives"]))}</span>
            <p>backup archives</p>
        </article>
        <article class="{health_card_class('metric', 'warning' if pending_email_count else 'ok')}">
            <span>{e(notifications["pending_email_count"])}</span>
            <p>pending emails</p>
        </article>
        <article class="{health_card_class('metric', 'error' if failed_email_count else 'ok')}">
            <span>{e(notifications["failed_email_count"])}</span>
            <p>failed emails</p>
        </article>
    </section>
    <section class="admin-settings-grid">
        <article class="panel span-2">
            <div class="panel-heading">
                <div>
                    <h2>Maintenance actions</h2>
                    <p class="muted compact">Run recovery checks directly from the health page.</p>
                </div>
                <span class="pill">{e(len(setup_warnings))} warning{'s' if len(setup_warnings) != 1 else ''}</span>
            </div>
            <div class="maintenance-grid">
                <form class="backup-action-card" method="post" action="/admin/health/jobs/retry">
                    <strong>Retry recoverable jobs</strong>
                    <span class="subtle">Queues failed, not-found, or interrupted Scryfall and price jobs.</span>
                    <button class="button secondary" type="submit">Retry jobs</button>
                </form>
                <form class="backup-action-card" method="post" action="/admin/health/notifications/replay">
                    <strong>Replay failed notification emails</strong>
                    <span class="subtle">Moves failed unread trade emails back through the SMTP sender.</span>
                    <button class="button secondary" type="submit"{email_replay_disabled}>Replay emails</button>
                </form>
                <form class="backup-action-card" method="post" action="/admin/health/backups/check">
                    <strong>Check backup integrity</strong>
                    <span class="subtle">Verifies backup ZIPs and the embedded SQLite database.</span>
                    <button class="button secondary" type="submit">Check backups</button>
                </form>
                <a class="backup-action-card link-card" href="/admin/jobs">
                    <strong>Open job dashboard</strong>
                    <span class="subtle">Review individual imports, lookups, price refreshes, and failed emails.</span>
                    <span class="button ghost small">Open jobs</span>
                </a>
            </div>
        </article>
        <article class="{health_card_class('panel span-2', setup_severity)}">
            <div class="panel-heading">
                <h2>Setup warnings</h2>
                <span class="status {health_status_class("failed" if any(item.get("severity") == "error" for item in setup_warnings) else "pending" if setup_warnings else "ok")}">{e(len(setup_warnings))} item{'s' if len(setup_warnings) != 1 else ''}</span>
            </div>
            <ul class="stack-list compact-stack">{setup_warning_rows}</ul>
        </article>
        <form class="panel form-grid compact-form span-2" method="post" action="/admin/health/retention">
            <div class="span-2 panel-heading">
                <div>
                    <h2>Data retention</h2>
                    <p class="muted compact">Set a value to 0 to keep that data forever. Cleanup only removes read notifications, terminal webhook deliveries, old audit logs, and evidence from resolved or dismissed disputes.</p>
                </div>
                <span class="pill">{e(retention_eligible["total"])} eligible</span>
            </div>
            <label>Read notifications
                <input name="notification_days" type="number" min="0" max="36500" step="1" value="{e(retention["notification_days"])}">
                <span class="subtle">{e(retention_eligible["notifications"])} eligible; unread notifications are protected.</span>
            </label>
            <label>Admin audit logs
                <input name="admin_log_days" type="number" min="0" max="36500" step="1" value="{e(retention["admin_log_days"])}">
                <span class="subtle">{e(retention_eligible["admin_logs"])} eligible.</span>
            </label>
            <label>Webhook delivery records
                <input name="webhook_days" type="number" min="0" max="36500" step="1" value="{e(retention["webhook_days"])}">
                <span class="subtle">{e(retention_eligible["webhook_deliveries"])} eligible; pending deliveries are protected.</span>
            </label>
            <label>Resolved dispute evidence
                <input name="evidence_days" type="number" min="0" max="36500" step="1" value="{e(retention["evidence_days"])}">
                <span class="subtle">{e(retention_eligible["dispute_evidence"])} eligible; open dispute evidence is protected.</span>
            </label>
            <p class="muted compact span-2">Retention age is measured from creation time, webhook completion time, or dispute resolution time as appropriate. Last cleanup: {e(retention_last_run)}.</p>
            <div class="form-actions span-2">
                <button class="button primary" name="intent" value="save" type="submit">Save retention settings</button>
                <button class="button danger" name="intent" value="save_run" type="submit" onclick="return confirm('Delete all records currently eligible under these retention settings? This cannot be undone.')">Save and run cleanup</button>
            </div>
        </form>
        <article class="{health_card_class('panel', 'ok')}">
            <div class="panel-heading">
                <h2>Database</h2>
                <span class="pill">SQLite</span>
            </div>
            <p class="muted compact">{e(database["path"])}</p>
            <div class="metric-grid compact-stats">{db_metrics}</div>
        </article>
        <article class="{health_card_class('panel', backup_severity)}">
            <div class="panel-heading">
                <h2>Backup status</h2>
                <span class="status {health_status_class("enabled" if auto_backup["enabled"] else "paused")}">{e("Enabled" if auto_backup["enabled"] else "Paused")}</span>
            </div>
            <div class="detail-grid">
                <span>Last success</span><strong>{e(health_time_label(auto_backup["last_success"]))}</strong>
                <span>Next run</span><strong>{e("Due now" if auto_backup["next_run"] == "due" else health_time_label(auto_backup["next_run"]))}</strong>
                <span>Retention</span><strong>{e(auto_backup["retention_count"])} archives, {e(auto_backup["retention_days"])} days</strong>
                <span>Integrity check</span><strong>{e(backup_integrity_label)}</strong>
            </div>
            {backup_error}
            {f'<p class="muted compact">{e(backup_integrity_message)}</p>' if backup_integrity_message else ''}
            <ul class="stack-list compact-stack">{archive_rows}</ul>
        </article>
        <article class="{health_card_class('panel', scryfall_severity)}">
            <div class="panel-heading">
                <h2>Scryfall refresh</h2>
                <span class="status {health_status_class(scryfall_prices.get("status", ""))}">{e(scryfall_prices.get("status", "unknown"))}</span>
            </div>
            <div class="detail-grid">
                <span>Bulk cards</span><strong>{e(scryfall_bulk.get("card_count", 0))}</strong>
                <span>Bulk status</span><strong>{e(scryfall_bulk.get("status", "unknown"))}</strong>
                <span>Bulk updated</span><strong>{e(health_time_label(scryfall_bulk.get("updated_at", "")))}</strong>
                <span>Price updated</span><strong>{e(health_time_label(scryfall_prices.get("updated_at", "")))}</strong>
                <span>Auto price refresh</span><strong>{e("On" if scryfall_prices.get("auto") else "Off")}</strong>
                <span>Interval</span><strong>{e(scryfall_prices.get("interval_hours", ""))} hours</strong>
            </div>
            {bulk_error}
            {price_error}
        </article>
        <article class="{health_card_class('panel', email_severity)}">
            <div class="panel-heading">
                <h2>Email configuration</h2>
                <span class="status {email_status_class}">{e(email_status)}</span>
            </div>
            <div class="detail-grid">
                <span>SMTP</span><strong>{e(smtp_detail)}</strong>
                <span>From</span><strong>{e(email["from_address"] or "Default sender")}</strong>
                <span>Username</span><strong>{e("Set" if email["username_set"] else "Not set")}</strong>
                <span>Password</span><strong>{e("Set" if email["password_set"] else "Not set")}</strong>
                <span>TLS</span><strong>{e("SSL" if email["use_ssl"] else "STARTTLS" if email["use_starttls"] else "Off")}</strong>
            </div>
        </article>
        <article class="{health_card_class('panel', jobs_severity)}">
            <div class="panel-heading">
                <h2>Queued jobs</h2>
            </div>
            <div class="health-job-list">{job_rows}</div>
        </article>
        <article class="{health_card_class('panel', 'error' if failed_email_count else 'ok')}">
            <div class="panel-heading">
                <h2>Failed notifications</h2>
                <span class="status {health_status_class("failed" if notifications["failed_email_count"] else "ok")}">{e(notifications["failed_email_count"])} failed</span>
            </div>
            <ul class="stack-list compact-stack">{failed_rows}</ul>
        </article>
    </section>
    """
    return render_layout(user, "Maintenance health", content, active="admin", notice=notice, status=status)


def admin_job_user_label(item):
    display_name = row_value(item, "display_name", "")
    username = row_value(item, "username", "")
    if display_name and username:
        return f"{display_name} (@{username})"
    if display_name:
        return display_name
    if username:
        return f"@{username}"
    return "Deleted user"


def admin_job_time_label(value):
    text = str(value or "").strip()
    return text[:16].replace("T", " ") if text else "Never"


def admin_job_status_label(value):
    text = str(value or "").strip().replace("_", " ")
    return text.title() if text else "Unknown"


def admin_job_retry_form(action, field_name, field_value, label="Retry", button_class="secondary", confirm="", return_to=""):
    confirm_attr = f' onclick="return confirm(\'{e(confirm)}\')"' if confirm else ""
    return_input = f'<input type="hidden" name="redirect_to" value="{e(return_to)}">' if return_to else ""
    return f"""
    <form method="post" action="{e(action)}">
        <input type="hidden" name="{e(field_name)}" value="{e(field_value)}">
        {return_input}
        <button class="button {e(button_class)} small" type="submit"{confirm_attr}>{e(label)}</button>
    </form>
    """


def admin_job_import_target(batch):
    group_name = row_value(batch, "group_name", "")
    if group_name:
        return f'{group_name} {row_value(batch, "group_type", "group")}'
    import_type = row_value(batch, "import_type", "").replace("_", " ")
    return import_type.title() if import_type else "Import"


def admin_job_import_row(batch):
    summary_func = globals().get("import_batch_summary")
    summary = summary_func(batch) if summary_func else {}
    inserted = int(summary.get("inserted", 0) or 0)
    updated = int(summary.get("updated", 0) or 0)
    grouped = int(summary.get("grouped", 0) or 0)
    queued = int(summary.get("queued", 0) or 0)
    skipped = int(summary.get("skipped", 0) or 0)
    item_count = int(row_value(batch, "item_count", 0) or 0)
    status_value = row_value(batch, "status", "")
    undo_form = ""
    if status_value == "applied":
        undo_form = admin_job_retry_form(
            f'/admin/jobs/imports/{batch["id"]}/undo',
            "batch_id",
            batch["id"],
            "Undo",
            "danger",
            "Undo this import batch? Imported rows and group links from this batch will be reverted.",
        )
    return f"""
    <li class="job-row">
        <div class="job-main">
            <strong>Batch #{e(batch["id"])} - {e(row_value(batch, "source", "") or admin_job_import_target(batch))}</strong>
            <span class="subtle">{e(admin_job_user_label(batch))} - {e(admin_job_time_label(row_value(batch, "updated_at", "")))}</span>
            <div class="job-meta">
                <span>{e(admin_job_import_target(batch))}</span>
                <span>{e(item_count)} recorded item{'s' if item_count != 1 else ''}</span>
                <span>{e(inserted)} inserted</span>
                <span>{e(updated)} updated</span>
                <span>{e(grouped)} grouped</span>
                <span>{e(queued)} queued</span>
                <span>{e(skipped)} skipped</span>
            </div>
        </div>
        <div class="job-actions">
            <span class="status {health_status_class(status_value)}">{e(admin_job_status_label(status_value))}</span>
            {undo_form}
        </div>
    </li>
    """


def admin_job_scryfall_row(job, return_to=""):
    status_value = row_value(job, "status", "")
    error = row_value(job, "last_error", "")
    retry_form = ""
    if status_value in JOB_DASHBOARD_RETRY_STATUSES:
        retry_form = admin_job_retry_form("/admin/jobs/scryfall/retry", "job_id", job["id"], return_to=return_to)
    error_line = f'<p class="job-error">{e(error)}</p>' if error else ""
    print_detail = " ".join(
        part for part in (
            row_value(job, "item_set_code", "") or row_value(job, "set_code", ""),
            row_value(job, "item_collector_number", "") or row_value(job, "collector_number", ""),
        ) if part
    )
    return f"""
    <li class="job-row {health_severity_class(health_severity_for_status(status_value))}">
        <div class="job-main">
            <strong>{e(row_value(job, "collection_card_name", "") or row_value(job, "card_name", "Card lookup"))}</strong>
            <span class="subtle">{e(admin_job_user_label(job))} - updated {e(admin_job_time_label(row_value(job, "updated_at", "")))}</span>
            <div class="job-meta">
                <span>Job #{e(job["id"])}</span>
                <span>{e(print_detail or "No exact printing")}</span>
                <span>{e(job["attempts"])} attempt{'s' if int(job["attempts"] or 0) != 1 else ''}</span>
                <span>Available {e(admin_job_time_label(row_value(job, "available_at", "")))}</span>
            </div>
            {error_line}
        </div>
        <div class="job-actions">
            <span class="status {health_status_class(status_value)}">{e(admin_job_status_label(status_value))}</span>
            {retry_form}
        </div>
    </li>
    """


def admin_job_price_row(job, return_to=""):
    status_value = row_value(job, "status", "")
    error = row_value(job, "last_error", "")
    provider = row_value(job, "provider", "")
    active_providers = tuple(globals().get("PRICE_PROVIDER_KEYS", ()))
    retry_form = ""
    if provider in active_providers and status_value in JOB_DASHBOARD_RETRY_STATUSES:
        retry_form = admin_job_retry_form("/admin/jobs/prices/retry", "job_id", job["id"], return_to=return_to)
    elif provider not in active_providers:
        retry_form = '<span class="muted compact">Provider retired</span>'
    error_line = f'<p class="job-error">{e(error)}</p>' if error else ""
    provider_label_func = globals().get("price_provider_label")
    provider_label = provider_label_func(provider) if provider_label_func else provider.title()
    print_detail = " ".join(part for part in (row_value(job, "set_code", ""), row_value(job, "collector_number", "")) if part)
    return f"""
    <li class="job-row {health_severity_class(health_severity_for_status(status_value))}">
        <div class="job-main">
            <strong>{e(row_value(job, "card_name", "Price refresh"))}</strong>
            <span class="subtle">{e(admin_job_user_label(job))} - updated {e(admin_job_time_label(row_value(job, "updated_at", "")))}</span>
            <div class="job-meta">
                <span>Job #{e(job["id"])}</span>
                <span>{e(provider_label)}</span>
                <span>{e(print_detail or "No exact printing")}</span>
                <span>{e(job["attempts"])} attempt{'s' if int(job["attempts"] or 0) != 1 else ''}</span>
            </div>
            {error_line}
        </div>
        <div class="job-actions">
            <span class="status {health_status_class(status_value)}">{e(admin_job_status_label(status_value))}</span>
            {retry_form}
        </div>
    </li>
    """


def admin_job_notification_row(notification, return_to=""):
    error = row_value(notification, "email_error", "") or "No error message stored."
    return f"""
    <li class="job-row severity-error">
        <div class="job-main">
            <strong>{e(row_value(notification, "title", "Notification email"))}</strong>
            <span class="subtle">{e(admin_job_user_label(notification))} - created {e(admin_job_time_label(row_value(notification, "created_at", "")))}</span>
            <div class="job-meta">
                <span>Notification #{e(notification["id"])}</span>
                <span>{e(row_value(notification, "kind", "notification").replace("_", " "))}</span>
                <span>{e(row_value(notification, "email", "No email"))}</span>
            </div>
            <p class="job-error">{e(error)}</p>
        </div>
        <div class="job-actions">
            <span class="status declined">Failed</span>
            {admin_job_retry_form("/admin/jobs/notifications/retry", "notification_id", notification["id"], return_to=return_to)}
        </div>
    </li>
    """


def render_admin_jobs(user, notice=None, status="info"):
    dashboard = maintenance_job_dashboard()
    metrics = dashboard["metrics"]
    price_refresh = dashboard["scryfall_price_refresh"]
    import_rows = "".join(admin_job_import_row(batch) for batch in dashboard["imports"])
    import_rows = import_rows or '<li class="empty-state compact-empty">No import batches have been recorded yet.</li>'
    scryfall_rows = "".join(admin_job_scryfall_row(job) for job in dashboard["scryfall_jobs"])
    scryfall_rows = scryfall_rows or '<li class="empty-state compact-empty">No Scryfall enrichment jobs need attention.</li>'
    price_rows = "".join(admin_job_price_row(job) for job in dashboard["price_jobs"])
    price_rows = price_rows or '<li class="empty-state compact-empty">No queued price refresh jobs need attention.</li>'
    notification_rows = "".join(admin_job_notification_row(item) for item in dashboard["failed_notifications"])
    notification_rows = notification_rows or '<li class="empty-state compact-empty">No failed notification emails.</li>'
    price_error = f'<p class="notice error compact">{e(price_refresh.get("error", ""))}</p>' if price_refresh.get("error") else ""
    price_retry_disabled = " disabled" if price_refresh.get("status") in ("running", "queued") else ""
    content = f"""
    <section class="section-heading">
        <div>
            <p class="eyebrow">Admin</p>
            <h1>Import and job dashboard</h1>
        </div>
        <div class="actions">
            <a class="button secondary" href="/admin">Back to admin</a>
            <a class="button secondary" href="/admin/health">Maintenance health</a>
        </div>
    </section>
    <section class="metric-grid">
        <article class="metric">
            <span>{e(metrics["recent_imports"])}</span>
            <p>import batches</p>
        </article>
        <article class="metric">
            <span>{e(metrics["scryfall_attention"])}</span>
            <p>Scryfall jobs needing attention</p>
        </article>
        <article class="metric">
            <span>{e(metrics["price_attention"])}</span>
            <p>queued price jobs needing attention</p>
        </article>
        <article class="metric">
            <span>{e(metrics["failed_emails"])}</span>
            <p>failed notification emails</p>
        </article>
    </section>
    <section class="job-dashboard-grid">
        <article class="panel job-dashboard-card">
            <div class="panel-heading">
                <div>
                    <h2>CSV and deck imports</h2>
                    <p class="muted compact">Recent preview, applied, and undone batches with admin undo for applied imports.</p>
                </div>
                <div class="status-row">{render_health_status_counts(dashboard["import_counts"])}</div>
            </div>
            <ul class="stack-list job-list">{import_rows}</ul>
        </article>
        <article class="panel job-dashboard-card">
            <div class="panel-heading">
                <div>
                    <h2>Scryfall enrichment</h2>
                    <p class="muted compact">Background card lookups queued during import or add-card flows.</p>
                </div>
                <div class="status-row">{render_health_status_counts(dashboard["scryfall_counts"])}</div>
            </div>
            <ul class="stack-list job-list">{scryfall_rows}</ul>
        </article>
        <article class="panel job-dashboard-card">
            <div class="panel-heading">
                <div>
                    <h2>Scryfall price refresh</h2>
                    <p class="muted compact">Automatic collection price refresh status and recovery.</p>
                </div>
                <form method="post" action="/admin/jobs/scryfall-prices/retry">
                    <button class="button secondary small" type="submit"{price_retry_disabled}>Retry now</button>
                </form>
            </div>
            <div class="detail-grid">
                <span>Status</span><strong>{e(admin_job_status_label(price_refresh.get("status", "unknown")))}</strong>
                <span>Updated</span><strong>{e(admin_job_time_label(price_refresh.get("updated_at", "")))}</strong>
                <span>Schedule</span><strong>{e("On" if price_refresh.get("auto") else "Off")}, every {e(price_refresh.get("interval_hours", ""))} hours</strong>
            </div>
            {price_error}
            <div class="panel-heading with-gap">
                <div>
                    <h2>Queued price jobs</h2>
                    <p class="muted compact">Legacy or future batched provider jobs with retry state.</p>
                </div>
                <div class="status-row">{render_health_status_counts(dashboard["price_counts"])}</div>
            </div>
            <ul class="stack-list job-list">{price_rows}</ul>
        </article>
        <article class="panel job-dashboard-card">
            <div class="panel-heading">
                <div>
                    <h2>Failed notification emails</h2>
                    <p class="muted compact">Unread trade notification emails that failed SMTP delivery.</p>
                </div>
                <div class="status-row">{render_health_status_counts(dashboard["email_counts"])}</div>
            </div>
            <ul class="stack-list job-list">{notification_rows}</ul>
        </article>
    </section>
    """
    return render_layout(user, "Import and jobs", content, active="admin", notice=notice, status=status)


def admin_audit_log_display_user(item, prefix):
    display_name = row_value(item, f"{prefix}_display_name", "")
    username = row_value(item, f"{prefix}_username", "")
    if display_name and username:
        return f"{display_name} (@{username})"
    if display_name:
        return display_name
    if username:
        return f"@{username}"
    return "Deleted user"


def admin_audit_log_target_label(item):
    return row_value(item, "target_label", "") or admin_audit_log_display_user(item, "target")


def admin_audit_log_time_label(item):
    created_at = row_value(item, "created_at", "")
    return created_at[:16].replace("T", " ") if created_at else ""


def render_admin_audit_log_item(item):
    target = admin_audit_log_target_label(item)
    details = row_value(item, "details", "")
    details_line = f'<span class="subtle">{e(details)}</span>' if details else ""
    return f"""
    <li>
        <div>
            <strong>{e(admin_audit_action_label(item["action"]))}</strong>
            <span class="subtle">{e(admin_audit_log_display_user(item, "admin"))} - {e(admin_audit_log_time_label(item))}</span>
            <span class="subtle">Target: {e(target)}</span>
            {details_line}
        </div>
    </li>
    """


def render_admin_audit_log_table_row(item):
    details = row_value(item, "details", "")
    request_ip = row_value(item, "request_ip", "")
    request_line = f'<span class="subtle">IP: {e(request_ip)}</span>' if request_ip else ""
    return f"""
    <tr>
        <td data-label="Time">{e(admin_audit_log_time_label(item))}</td>
        <td data-label="Admin">{e(admin_audit_log_display_user(item, "admin"))}</td>
        <td data-label="Action">
            <strong>{e(admin_audit_action_label(item["action"]))}</strong>
            <span class="subtle">{e(item["action"])}</span>
        </td>
        <td data-label="Target">
            <span>{e(admin_audit_log_target_label(item))}</span>
            <span class="subtle">{e(row_value(item, "target_type", ""))}</span>
        </td>
        <td data-label="Details">
            <span>{e(details)}</span>
            {request_line}
        </td>
    </tr>
    """


def trade_dispute_user_label(item, prefix):
    display_name = row_value(item, f"{prefix}_name", "")
    username = row_value(item, f"{prefix}_username", "")
    if display_name and username:
        return f"{display_name} (@{username})"
    if display_name:
        return display_name
    if username:
        return f"@{username}"
    return "Deleted user"


def trade_dispute_evidence_size_label(size):
    try:
        size = max(0, int(size or 0))
    except (TypeError, ValueError):
        size = 0
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} bytes"


def trade_dispute_evidence_admin_preview(dispute, evidence):
    content_type = row_value(evidence, "content_type", "")
    evidence_url = f'/trades/{dispute["trade_id"]}/disputes/{dispute["id"]}/evidence/{evidence["id"]}'
    if content_type in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        return f"""
        <details class="evidence-preview" open>
            <summary>Image preview</summary>
            <a class="evidence-image-link" href="{evidence_url}">
                <img class="evidence-preview-image" src="{evidence_url}" alt="{e(row_value(evidence, "original_filename", "Evidence image"))}">
            </a>
        </details>
        """
    if content_type == "text/plain":
        preview = trade_dispute_evidence_text_preview(evidence["id"], dispute["id"])
        if not preview or not preview["text"]:
            return ""
        truncated = '<span class="subtle">Preview truncated.</span>' if preview["truncated"] else ""
        return f"""
        <details class="evidence-preview" open>
            <summary>Text preview</summary>
            <pre class="evidence-text-preview">{e(preview["text"])}</pre>
            {truncated}
        </details>
        """
    return ""


def render_trade_dispute_evidence_admin_list(item):
    evidence_rows = trade_dispute_evidence_rows(item["id"])
    if not evidence_rows:
        return '<p class="muted compact">No evidence attachments.</p>'
    items = "".join(
        f"""
        <li>
            <a href="/trades/{item["trade_id"]}/disputes/{item["id"]}/evidence/{evidence["id"]}">{e(evidence["original_filename"])}</a>
            <span class="subtle">{e(trade_dispute_evidence_size_label(evidence["file_size"]))} - {e(trade_dispute_user_label(evidence, "uploader"))} - {e(evidence["created_at"][:16].replace("T", " "))}</span>
            {f'<span class="subtle">{e(row_value(evidence, "note", ""))}</span>' if row_value(evidence, "note", "") else ""}
            {trade_dispute_evidence_admin_preview(item, evidence)}
        </li>
        """
        for evidence in evidence_rows
    )
    return f'<ul class="stack-list compact-stack evidence-list admin-evidence-list">{items}</ul>'


def render_trade_dispute_summary_item(item):
    escalation = trade_dispute_escalation_status(item)
    escalation_badge = (
        f'<span class="status pending">Needs attention</span><span class="subtle">Open {e(escalation["age_days"])} day(s)</span>'
        if escalation["escalated"]
        else ""
    )
    return f"""
    <li>
        <div>
            <strong>Trade #{e(item["trade_id"])} - {e(trade_dispute_category_label(item["category"]))}</strong>
            <span class="subtle">Reported by {e(trade_dispute_user_label(item, "reporter"))} - {e(item["created_at"][:16].replace("T", " "))}</span>
            <span class="status {e(trade_dispute_status_class(item["status"]))}">{e(trade_dispute_status_label(item["status"]))}</span>
            {escalation_badge}
        </div>
    </li>
    """


def render_trade_dispute_admin_row(item):
    status_options = option_tags(TRADE_DISPUTE_STATUS_OPTIONS, item["status"])
    admin_note = row_value(item, "admin_note", "")
    resolution_note = row_value(item, "resolution_note", "")
    resolver = trade_dispute_user_label(item, "resolver") if row_value(item, "resolver_name", "") or row_value(item, "resolver_username", "") else ""
    resolved_line = f'<span class="subtle">Resolved by {e(resolver)} on {e(row_value(item, "resolved_at", "")[:16].replace("T", " "))}</span>' if resolver and row_value(item, "resolved_at", "") else ""
    evidence_html = render_trade_dispute_evidence_admin_list(item)
    escalation = trade_dispute_escalation_status(item)
    escalation_badge = ""
    if escalation["escalated"]:
        escalation_badge = f"""
            <span class="status pending">Needs attention</span>
            <span class="subtle">Open {e(escalation["age_days"])} day(s); policy escalates after {e(escalation["threshold_days"])}.</span>
        """
    return f"""
    <tr>
        <td>
            <strong>#{e(item["id"])}</strong>
            <span class="subtle">{e(item["created_at"][:16].replace("T", " "))}</span>
            {escalation_badge}
        </td>
        <td>
            <a href="/trades/{item["trade_id"]}">Trade #{e(item["trade_id"])}</a>
            <span class="subtle">{e(trade_dispute_user_label(item, "proposer"))} with {e(trade_dispute_user_label(item, "recipient"))}</span>
        </td>
        <td>
            <span class="status {e(trade_dispute_status_class(item["status"]))}">{e(trade_dispute_status_label(item["status"]))}</span>
            <span class="subtle">{e(trade_dispute_category_label(item["category"]))}</span>
        </td>
        <td>
            <strong>{e(trade_dispute_user_label(item, "reporter"))}</strong>
            <p class="compact">{e(item["body"])}</p>
            <div class="evidence-block admin-evidence-block">
                <strong>Evidence</strong>
                {evidence_html}
            </div>
            {resolved_line}
        </td>
        <td>
            <form class="admin-dispute-form" method="post" action="/admin/disputes/{item["id"]}/update">
                <label>Status
                    <select name="status">{status_options}</select>
                </label>
                <label>Admin response
                    <textarea name="admin_note" rows="3" maxlength="2000" placeholder="Optional response visible to trade participants">{e(admin_note)}</textarea>
                </label>
                <label>Resolution notes
                    <textarea name="resolution_note" rows="3" maxlength="2000" placeholder="Internal notes about the decision, evidence, or follow-up">{e(resolution_note)}</textarea>
                </label>
                <button class="button primary small" type="submit">Save</button>
            </form>
        </td>
    </tr>
    """


def render_trade_dispute_trend_panel():
    repeat_rows = trade_dispute_repeat_issue_trends(limit=6)
    category_rows = trade_dispute_category_trends(limit=6)
    if repeat_rows:
        repeat_items = "".join(
            f"""
            <li>
                <div>
                    <strong>{e(trade_dispute_user_label(item, "reported"))}</strong>
                    <span class="subtle">{e(item["issue_count"])} reports in 90 days - {e(item["active_count"])} active</span>
                    <span class="subtle">Latest: {e(row_value(item, "latest_at", "")[:16].replace("T", " "))}</span>
                    <span class="subtle">Types: {e(row_value(item, "categories", "").replace(",", ", "))}</span>
                </div>
            </li>
            """
            for item in repeat_rows
        )
    else:
        repeat_items = '<li class="muted compact">No users have multiple issue reports in the last 90 days.</li>'
    if category_rows:
        category_items = "".join(
            f"""
            <li>
                <div>
                    <strong>{e(trade_dispute_category_label(item["category"]))}</strong>
                    <span class="subtle">{e(item["issue_count"])} reports across {e(item["trade_count"])} trades - {e(item["active_count"])} active</span>
                    <span class="subtle">Latest: {e(row_value(item, "latest_at", "")[:16].replace("T", " "))}</span>
                </div>
            </li>
            """
            for item in category_rows
        )
    else:
        category_items = '<li class="muted compact">No issue category trends yet.</li>'
    return f"""
    <section class="content-grid dispute-trend-grid">
        <article class="panel dispute-trend-card">
            <div class="panel-heading">
                <h2>Repeat issue trends</h2>
                <span class="pill">90 days</span>
            </div>
            <ul class="stack-list compact-stack">{repeat_items}</ul>
        </article>
        <article class="panel dispute-trend-card">
            <div class="panel-heading">
                <h2>Issue type trends</h2>
                <span class="pill">90 days</span>
            </div>
            <ul class="stack-list compact-stack">{category_items}</ul>
        </article>
    </section>
    """


def render_admin_trade_disputes(user, query, notice=None, status="info"):
    filters = trade_dispute_admin_filters({
        "q": query_value(query, "q"),
        "status": query_value(query, "status"),
        "category": query_value(query, "category"),
    })
    total_count = trade_dispute_admin_count(filters)
    page, per_page, page_count, offset = pagination_state(query, total_count)
    dispute_rows = trade_dispute_admin_rows(filters, per_page, offset)
    pagination = render_pagination("/admin/disputes", query, total_count, page, per_page, page_count)
    status_options = '<option value="">All statuses</option>' + option_tags(TRADE_DISPUTE_STATUS_OPTIONS, filters["status"])
    category_options = '<option value="">All issue types</option>' + option_tags(TRADE_DISPUTE_CATEGORY_OPTIONS, filters["category"])
    table_rows = "".join(render_trade_dispute_admin_row(item) for item in dispute_rows)
    if not table_rows:
        table_rows = '<tr><td colspan="5" class="empty-state compact-empty">No trade issues matched those filters.</td></tr>'
    trend_panel = render_trade_dispute_trend_panel()
    content = f"""
    <section class="section-heading">
        <div>
            <p class="eyebrow">Admin</p>
            <h1>Trade issue queue</h1>
        </div>
        <a class="button secondary" href="/admin">Back to admin</a>
    </section>
    {trend_panel}
    <form class="panel form-grid compact-form" method="get" action="/admin/disputes">
        <label>Search issues
            <input name="q" value="{e(filters["q"])}" placeholder="Trade, user, issue text, or admin response">
        </label>
        <label>Status
            <select name="status">{status_options}</select>
        </label>
        <label>Issue type
            <select name="category">{category_options}</select>
        </label>
        <div class="form-actions">
            <button class="button primary" type="submit">Filter issues</button>
            <a class="button ghost" href="/admin/disputes">Clear</a>
        </div>
    </form>
    <section class="panel flush">
        <div class="table-wrap">
            <table class="admin-table">
                <thead>
                    <tr>
                        <th>Issue</th>
                        <th>Trade</th>
                        <th>Status</th>
                        <th>Report</th>
                        <th>Admin review</th>
                    </tr>
                </thead>
                <tbody>{table_rows}</tbody>
            </table>
        </div>
        {pagination}
    </section>
    """
    return render_layout(user, "Trade issue queue", content, active="admin", notice=notice, status=status)


def render_admin_logs(user, query, notice=None, status="info"):
    filters = admin_audit_log_filters({
        "q": query_value(query, "q"),
        "action": query_value(query, "action"),
    })
    total_count = admin_audit_log_count(filters)
    page, per_page, page_count, offset = pagination_state(query, total_count)
    log_rows = admin_audit_log_rows(filters, per_page, offset)
    pagination = render_pagination("/admin/logs", query, total_count, page, per_page, page_count)
    action_options = '<option value="">All actions</option>' + "".join(
        f'<option value="{e(action)}"{selected(filters["action"], action)}>{e(label)}</option>'
        for action, label in ADMIN_AUDIT_ACTION_OPTIONS
    )
    table_rows = "".join(render_admin_audit_log_table_row(item) for item in log_rows)
    if not table_rows:
        table_rows = '<tr><td colspan="5" class="empty-state compact-empty">No admin activity matched those filters.</td></tr>'
    content = f"""
    <section class="section-heading">
        <div>
            <p class="eyebrow">Admin</p>
            <h1>Activity log</h1>
        </div>
        <a class="button secondary" href="/admin">Back to admin</a>
    </section>
    <form class="panel form-grid compact-form" method="get" action="/admin/logs">
        <label>Search logs
            <input name="q" value="{e(filters["q"])}" placeholder="Admin, target, action, or details">
        </label>
        <label>Action
            <select name="action">{action_options}</select>
        </label>
        <div class="form-actions">
            <button class="button primary" type="submit">Filter logs</button>
            <a class="button ghost" href="/admin/logs">Clear</a>
        </div>
    </form>
    <section class="panel flush">
        <div class="table-wrap">
            <table class="admin-table responsive-card-table admin-log-table">
                <thead>
                    <tr>
                        <th>Time</th>
                        <th>Admin</th>
                        <th>Action</th>
                        <th>Target</th>
                        <th>Details</th>
                    </tr>
                </thead>
                <tbody>{table_rows}</tbody>
            </table>
        </div>
        {pagination}
    </section>
    """
    return render_layout(user, "Admin activity log", content, active="admin", notice=notice, status=status)


def render_registration_invite_row(invite):
    status_class = {
        "pending": "pending",
        "accepted": "accepted",
        "revoked": "declined",
        "expired": "declined",
    }.get(row_value(invite, "status", ""), "pending")
    sent = "Sent" if row_value(invite, "sent_at", "") else "Not emailed"
    accepted = row_value(invite, "accepted_by_name", "")
    accepted_line = f'<span class="subtle">Accepted by {e(accepted)} on {e(row_value(invite, "accepted_at", "")[:10])}</span>' if accepted else ""
    revoke_button = (
        f"""
        <form method="post" action="/admin/invites/{invite["id"]}/revoke">
            <button class="button ghost small" type="submit">Revoke</button>
        </form>
        """
        if row_value(invite, "status", "") == "pending"
        else ""
    )
    return f"""
    <li class="invite-row">
        <div>
            <strong>{e(invite["email"])}</strong>
            <span class="subtle">{e(sent)} - expires {e(invite["expires_at"][:10])} - token {e(row_value(invite, "token_hint", ""))}</span>
            {accepted_line}
        </div>
        <div class="inline-actions">
            <span class="status {status_class}">{e(row_value(invite, "status", "pending").title())}</span>
            {revoke_button}
        </div>
    </li>
    """


def render_admin_user_row(admin_user, managed_user):
    status_parts = []
    managed_role = user_role(managed_user)
    role_class = "accepted" if managed_role in (ROLE_OWNER, ROLE_ADMIN) else "pending" if managed_role in (ROLE_MODERATOR, ROLE_ORGANIZER) else ""
    status_parts.append(f'<span class="status {role_class}">{e(role_label(managed_role))}</span>')
    if managed_user["is_banned"]:
        status_parts.append('<span class="status declined">Banned</span>')
    else:
        status_parts.append('<span class="status accepted">Active</span>')
    if two_factor_enabled(managed_user):
        status_parts.append('<span class="status accepted">2FA on</span>')
    elif row_value(managed_user, "totp_secret", ""):
        status_parts.append('<span class="status pending">2FA setup</span>')
    trust_state, trust_label, trust_detail = trusted_status_details(managed_user)
    trust_class = "accepted" if trust_state in ("trusted", "earned") else "declined" if trust_state == "revoked" else "pending"
    status_parts.append(f'<span class="status {trust_class}">{e(trust_label)}</span>')
    ban_action = "unban" if managed_user["is_banned"] else "ban"
    ban_label = "Unban" if managed_user["is_banned"] else "Ban"
    trust_override = int(managed_user["trusted_override"] or 0)
    if trust_override == 1:
        primary_trust_action, primary_trust_label = "revoke", "Revoke trust"
        secondary_trust_action, secondary_trust_label = "reset", "Use auto trust"
    elif trust_override == -1:
        primary_trust_action, primary_trust_label = "trust", "Trust user"
        secondary_trust_action, secondary_trust_label = "reset", "Allow earning"
    else:
        primary_trust_action, primary_trust_label = "trust", "Trust user"
        secondary_trust_action, secondary_trust_label = "revoke", "Revoke trust"
    disable_self = managed_user["id"] == admin_user["id"]
    self_note = '<span class="muted compact">This is you.</span>' if disable_self else ""
    ban_reason = managed_user["ban_reason"] if managed_user["ban_reason"] else ""
    can_moderate = user_can_manage_target(admin_user, managed_user, CAP_MODERATE_USERS)
    can_manage_user = user_can_manage_target(admin_user, managed_user, CAP_MANAGE_USERS)
    role_options = assignable_roles_for_user(admin_user)
    can_change_role = bool(role_options) and (
        user_role(admin_user) == ROLE_OWNER or user_can_manage_target(admin_user, managed_user, CAP_MANAGE_ROLES)
    ) and not disable_self
    role_form = ""
    if can_change_role:
        options = "".join(
            f'<option value="{e(role)}"{selected(managed_role, role)}>{e(label)}</option>'
            for role, label in role_options
        )
        role_form = f"""
        <form class="inline-admin-form role-form" method="post" action="/admin/user/{managed_user["id"]}/role">
            <label>Role<select name="role">{options}</select></label>
            <button class="button secondary small" type="submit">Change role</button>
        </form>
        """
    moderation_controls = ""
    if can_moderate:
        moderation_controls = f"""
        <form class="inline-admin-form" method="post" action="/admin/user/{managed_user["id"]}/ban">
            <input type="hidden" name="action" value="{ban_action}">
            <input name="reason" placeholder="Ban reason" value="{e(ban_reason)}" {'disabled' if managed_user["is_banned"] else ""}>
            <button class="button {'secondary' if managed_user["is_banned"] else 'danger'} small" type="submit">{ban_label}</button>
        </form>
        <form class="inline-admin-form trust-form" method="post" action="/admin/user/{managed_user["id"]}/trust">
            <button class="button secondary small" name="action" value="{primary_trust_action}" type="submit">{primary_trust_label}</button>
            <button class="button ghost small" name="action" value="{secondary_trust_action}" type="submit">{secondary_trust_label}</button>
        </form>
        <form class="inline-admin-form notes-form" method="post" action="/admin/user/{managed_user["id"]}/notes">
            <textarea name="admin_notes" rows="2" placeholder="Staff notes">{e(managed_user["admin_notes"])}</textarea>
            <button class="button secondary small" type="submit">Save notes</button>
        </form>
        """
    security_controls = ""
    if can_manage_user:
        security_controls = f"""
        <form class="inline-admin-form" method="post" action="/admin/user/{managed_user["id"]}/password">
            <input name="new_password" type="password" minlength="8" placeholder="New password">
            <input name="confirm_password" type="password" minlength="8" placeholder="Confirm">
            <button class="button secondary small" type="submit">Reset password</button>
        </form>
        <form class="inline-admin-form role-form" method="post" action="/admin/user/{managed_user["id"]}/2fa">
            <button class="button secondary small" type="submit" onclick="return confirm('Reset two-factor authentication for this user? They will need to set it up again.')">Reset 2FA</button>
        </form>
        """
    controls = moderation_controls + security_controls + role_form
    if not controls:
        controls = '<span class="muted compact">No actions available for this role.</span>'
    return f"""
    <tr>
        <td>
            <strong>{e(managed_user["display_name"])}</strong>
            <span class="subtle">@{e(managed_user["username"])}</span>
            <span class="subtle">{e(managed_user["email"] or "No email")}</span>
            {self_note}
        </td>
        <td>
            <div class="status-stack">{''.join(status_parts)}</div>
            {f'<span class="subtle">{e(ban_reason)}</span>' if ban_reason else ""}
            <span class="subtle">{e(trust_detail)}</span>
        </td>
        <td>
            <span class="subtle">{e(managed_user["collection_count"])} collection entries</span>
            <span class="subtle">{e(managed_user["want_count"])} wants</span>
            <span class="subtle">{e(managed_user["trade_count"])} trades</span>
            <span class="subtle">{e(managed_user["completed_trade_count"])} completed trades</span>
            <span class="subtle">Joined {e(managed_user["created_at"][:10])}</span>
        </td>
        <td>
            <div class="admin-controls">
                {controls}
            </div>
        </td>
    </tr>
    """


__all__ = ['render_staff_admin', 'render_admin', 'render_admin_onboarding_action', 'render_admin_onboarding_item', 'render_admin_onboarding_checklist', 'health_time_label', 'health_status_class', 'render_health_status_counts', 'render_failed_notification_row', 'render_admin_health', 'render_admin_collection_health', 'admin_job_user_label', 'admin_job_time_label', 'admin_job_status_label', 'admin_job_retry_form', 'admin_job_import_target', 'admin_job_import_row', 'admin_job_scryfall_row', 'admin_job_price_row', 'admin_job_notification_row', 'render_admin_jobs', 'admin_audit_log_display_user', 'admin_audit_log_target_label', 'render_admin_audit_log_time_label', 'render_admin_dispute_summary_item', 'render_trade_dispute_admin_row', 'render_admin_trade_disputes', 'render_admin_logs', 'render_registration_invite_row', 'render_admin_user_row']
__all__ = [
    name
    for name, value in globals().items()
    if callable(value)
    and (
        name.startswith(("render_", "admin_job_", "admin_audit_log_", "trade_dispute_"))
        or name in ("health_time_label", "health_status_class")
    )
]
