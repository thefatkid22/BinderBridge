"""Admin panel, audit log, invite, and dispute views.

This module is wired by binderbridge.views; shared app helpers are injected at runtime.
"""

def render_copyable_field(field_id, label, value, help_text="", button_label="Copy"):
    help_html = f'<span class="subtle">{e(help_text)}</span>' if help_text else ""
    return f"""
    <div class="copyable-field span-2">
        <label for="{e(field_id)}">{e(label)}
            <input id="{e(field_id)}" readonly value="{e(value)}" onclick="this.select()">
        </label>
        <div class="copyable-field-actions">
            <button class="button secondary small" type="button" data-copy-target="#{e(field_id)}" data-copy-label="{e(button_label)}">{e(button_label)}</button>
            {help_html}
        </div>
    </div>
    """


def render_staff_admin(user, notice=None, status="info", invite_result=None, recovery_result=None):
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
        invite_rows = invite_rows or render_empty_action_state(
            "No invites yet.",
            "Create an invite above when you are ready to bring in another member.",
            tag="li",
        )
        result_panel = ""
        if invite_result:
            result_panel = f"""
            <div class="invite-result span-2">
                <strong>Invite link</strong>
                {render_copyable_field("staff-invite-link", "Invite link", invite_result["link"], "Share this link directly with the intended user.", "Copy invite link")}
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


def render_admin(user, notice=None, status="info", invite_result=None, recovery_result=None, active_section=""):
    if user_role(user) not in (ROLE_OWNER, ROLE_ADMIN):
        return render_staff_admin(user, notice=notice, status=status, invite_result=invite_result, recovery_result=recovery_result)
    users = admin_user_list()
    active_user_count = sum(
        1
        for managed_user in users
        if not managed_user["is_banned"] and row_value(managed_user, "registration_status", "active") == "active"
    )
    elevated_user_count = sum(1 for managed_user in users if user_role(managed_user) in (ROLE_OWNER, ROLE_ADMIN))
    elevated_user_label = f"{elevated_user_count} admin/owner account{'s' if elevated_user_count != 1 else ''}"
    user_rows = "".join(render_admin_user_row(user, managed_user) for managed_user in users)
    trade_policy = trade_policy_settings()
    threshold = trade_policy["trusted_threshold"]
    fairness = trade_policy["fairness"]
    one_way_options = option_tags(ONE_WAY_TRADE_POLICY_OPTIONS, trade_policy["one_way_policy"])
    integration_policy = integration_access_settings()
    api_access_options = option_tags(INTEGRATION_ACCESS_POLICY_OPTIONS, integration_policy["api_policy"])
    webhook_access_options = option_tags(INTEGRATION_ACCESS_POLICY_OPTIONS, integration_policy["webhook_policy"])
    moderation_settings = registration_moderation_settings()
    approval_options = option_tags(REGISTRATION_APPROVAL_MODE_OPTIONS, moderation_settings["approval_mode"])
    invite_only_checked = checked(invite_only_registration_enabled())
    invite_mode_label = "Invite-only" if invite_only_registration_enabled() else "Open registration"
    review_count = pending_registration_count()
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
    invite_rows = invite_rows or render_empty_action_state(
        "No invites yet.",
        "Create an invite above when you are ready to bring in another member. Revoked links can be deleted from this list later.",
        tag="li",
    )
    pending_rows = "".join(render_registration_review_row(item) for item in pending_registration_rows())
    pending_rows = pending_rows or render_empty_action_state(
        "No pending account reviews.",
        "New signups that need approval will appear here with their risk signals.",
        tag="li",
    )
    invite_result_panel = ""
    if invite_result:
        invite_result_panel = f"""
        <div class="invite-result span-2">
            <strong>Invite link</strong>
            {render_copyable_field("admin-invite-link", "Invite link", invite_result["link"], f'Expires {invite_result["expires_at"][:10]}', "Copy invite link")}
        </div>
        """
    recovery_result_panel = ""
    if recovery_result and not recovery_result.get("sent"):
        recovery_result_panel = f"""
        <article class="panel form-grid compact-form span-2">
            <div class="span-2 panel-heading">
                <h2>Manual password recovery link</h2>
                <span class="status pending">Share securely</span>
            </div>
            <p class="muted compact span-2">This one-time link was shown because email delivery was unavailable. Share it directly with the intended user. It expires {e(recovery_result["expires_at"][:16].replace("T", " "))} UTC.</p>
            {render_copyable_field("admin-recovery-link", "Recovery link", recovery_result["link"], "Share this securely. It can only be used once.", "Copy recovery link")}
        </article>
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
    ) or render_empty_action_state(
        "No backups created yet.",
        "Create a backup from this tab before making major site changes.",
        tag="li",
    )
    recent_admin_logs = admin_audit_log_rows(limit=6)
    recent_admin_log_rows = "".join(render_admin_audit_log_item(item) for item in recent_admin_logs) or render_empty_action_state(
        "No admin actions logged yet.",
        "Policy, invite, account, and maintenance changes will be listed here.",
        tag="li",
    )
    open_dispute_count = open_trade_dispute_count()
    recent_disputes = trade_dispute_admin_rows({"status": ""}, limit=5)
    recent_dispute_rows = "".join(render_trade_dispute_summary_item(item) for item in recent_disputes) or render_empty_action_state(
        "No trade issues reported yet.",
        "Disputes and escalated trade problems will appear here when members report them.",
        tag="li",
    )
    onboarding_panel = render_admin_onboarding_checklist()
    setup_completion_banner = render_admin_setup_completion_banner()
    workspace_items = [
        ("#admin-overview", "Overview", "Setup and health"),
        ("#admin-policies", "Policies", "Trade and integrations"),
        ("#admin-access", "Access", "Registration and invites"),
        ("#admin-operations", "Operations", "Backups and logs"),
        ("#admin-users", "Users", "Roles, trust, and recovery"),
    ]
    active_attr = workspace_active_attr(active_section, [href.lstrip("#") for href, _text, _detail in workspace_items])
    content = f"""
    <section class="section-heading">
        <div>
            <p class="eyebrow">Admin</p>
            <h1>Admin control panel</h1>
            <p class="muted compact">Review site health first, then move into policy, access, operations, or user management.</p>
        </div>
    </section>
    <section class="settings-summary admin-summary" aria-label="Admin status summary">
        <article class="settings-summary-card">
            <span>Accounts</span>
            <strong>{e(active_user_count)} active</strong>
            <small>{e(len(users))} total, {e(elevated_user_label)}</small>
        </article>
        <article class="settings-summary-card">
            <span>Reviews</span>
            <strong>{e(review_count)} pending</strong>
            <small>{e(invite_mode_label)}</small>
        </article>
        <article class="settings-summary-card">
            <span>Trade issues</span>
            <strong>{e(open_dispute_count)} open</strong>
            <small>Queue and recent reports below</small>
        </article>
        <article class="settings-summary-card">
            <span>Backups</span>
            <strong>{e(auto_backup_mode)}</strong>
            <small>Last success: {e(auto_success_label)}</small>
        </article>
    </section>
    <section class="workspace-layout tabbed-workspace" data-workspace-tabs{active_attr}>
        {render_workspace_nav(workspace_items, label="Admin control panel", compact=True, vertical=True)}
        <div class="workspace-pane-stack">
    {recovery_result_panel}
    {setup_completion_banner}
    <section class="workspace-section" id="admin-overview">
        <div class="workspace-section-heading">
            <div><p class="eyebrow">Overview</p><h2>Site operations at a glance</h2><p class="muted compact">Open a focused dashboard for imports, card data, maintenance, or database work.</p></div>
        </div>
        <div class="admin-operation-grid">
            <a class="admin-operation-card" href="/admin/setup"><span class="pill">Setup</span><strong>First-run setup</strong><span>Walk through public URL, registration, backups, Scryfall, invites, and first import.</span></a>
            <a class="admin-operation-card" href="/admin/jobs"><span class="pill">Jobs</span><strong>Import and jobs</strong><span>Review imports, enrichment, price refreshes, and failed notifications.</span></a>
            <a class="admin-operation-card" href="/admin/collection-health"><span class="pill">Cards</span><strong>Collection health</strong><span>Find duplicates, invalid finishes, stale prices, and visibility coverage.</span></a>
            <a class="admin-operation-card" href="/admin/health"><span class="pill">Health</span><strong>Maintenance health</strong><span>See setup warnings, queued work, backup status, and failed delivery.</span></a>
            <a class="admin-operation-card" href="/admin/database"><span class="pill">Database</span><strong>Database maintenance</strong><span>Inspect storage, indexes, migrations, and maintenance history.</span></a>
        </div>
        {onboarding_panel}
    </section>
    <section class="workspace-section" id="admin-policies">
        <div class="workspace-section-heading">
            <div><p class="eyebrow">Policies</p><h2>Community rules and integrations</h2><p class="muted compact">Set trade safeguards and decide which user classes may use integrations.</p></div>
        </div>
        <div class="admin-settings-grid">
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
        </div>
    </section>
    <section class="workspace-section" id="admin-access">
        <div class="workspace-section-heading">
            <div><p class="eyebrow">Access</p><h2>Registration and invitations</h2><p class="muted compact">Choose how new members join and manage outstanding invitations.</p></div>
        </div>
        <div class="admin-settings-grid">
        <form class="panel form-grid compact-form registration-settings" method="post" action="/admin/registration-settings#admin-access">
            <div class="span-2 panel-heading">
                <h2>Registration</h2>
                <span class="pill">{e(invite_mode_label)}</span>
                <span class="pill">{e(moderation_settings["approval_mode_label"])}</span>
            </div>
            <label class="checkbox-line span-2">
                <input type="checkbox" name="invite_only_registration" value="1"{invite_only_checked}>
                Require an invite link for new accounts
            </label>
            <label class="span-2">Account approval
                <select name="registration_approval_mode">{approval_options}</select>
            </label>
            <label>Suspicious score threshold
                <input name="registration_risk_threshold" type="number" min="0" max="1000" step="1" value="{e(moderation_settings["risk_threshold"])}">
            </label>
            <p class="muted compact span-2">Suspicious mode sends matching signups to review instead of automatically denying them. Signals use hashed email, IP, network range, and user-agent values.</p>
            <div class="form-actions span-2">
                <button class="button primary" type="submit">Save registration settings</button>
            </div>
        </form>
        <article class="panel invite-settings span-2" id="admin-registration-review">
            <div class="panel-heading with-gap">
                <div>
                    <h2>Pending account review</h2>
                    <p class="muted compact">Approve real members, deny suspicious signups, and keep the risk signals visible for context.</p>
                </div>
                <span class="pill">{e(review_count)} pending</span>
            </div>
            <ul class="stack-list compact-stack">{pending_rows}</ul>
        </article>
        <article class="panel invite-settings" id="admin-invites">
            <form class="form-grid compact-form" method="post" action="/admin/invites#admin-access">
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
        </div>
    </section>
    <section class="workspace-section" id="admin-operations">
        <div class="workspace-section-heading">
            <div><p class="eyebrow">Operations</p><h2>Backups, audit trail, and trade issues</h2><p class="muted compact">Keep the site recoverable and review activity that needs administrator attention.</p></div>
        </div>
        <div class="admin-settings-grid">
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
                <button class="button danger" type="submit" data-confirm="Restore this backup? Current data will be replaced after a safety backup is created.">Restore backup</button>
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
        </div>
    </section>
    <section class="workspace-section" id="admin-users">
        <div class="workspace-section-heading">
            <div><p class="eyebrow">Users</p><h2>Roles, trust, and account recovery</h2><p class="muted compact">Review member activity and apply the least privilege needed for each account.</p></div>
            <span class="pill">{e(len(users))} accounts</span>
        </div>
        <div class="panel flush">
        <div class="table-wrap">
            <table class="admin-table responsive-card-table">
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
        </div>
    </section>
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
            <a class="button secondary small" href="/admin/setup">Open setup wizard</a>
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


def render_admin_setup_completion_banner():
    summary = admin_setup_summary()
    completed_at = summary.get("completed_at", "")
    if not completed_at:
        return ""
    completed_label = completed_at[:16].replace("T", " ")
    return f"""
    <section class="panel setup-completion-banner" aria-label="First-run setup status">
        <div class="panel-heading">
            <div>
                <p class="eyebrow">Setup complete</p>
                <h2>First-run setup complete</h2>
                <p class="muted compact">Marked complete {e(completed_label)}. Keep the health dashboard handy for ongoing operations.</p>
            </div>
            <span class="status accepted">Ready</span>
        </div>
        <div class="actions">
            <a class="button secondary" href="/admin/setup">Review setup wizard</a>
            <a class="button ghost" href="/admin/health">Open maintenance health</a>
        </div>
    </section>
    """


def render_setup_status(complete, label=None):
    return f'<span class="status {"accepted" if complete else "pending"}">{e(label or ("Complete" if complete else "Needs setup"))}</span>'


def render_admin_setup_wizard(user, notice=None, status="info", invite_result=None):
    summary = admin_setup_summary()
    checklist = summary["checklist"]
    complete_count = int(checklist["complete_count"])
    total = int(checklist["total"] or 1)
    progress = min(100, max(0, round((complete_count / total) * 100)))
    completed_at = summary.get("completed_at", "")
    setup_status = "Marked complete" if completed_at else "In progress"
    setup_status_class = "accepted" if completed_at else "pending"
    public_config = summary.get("public_base_url_config", "")
    public_saved = summary.get("public_base_url_saved", "")
    public_value = summary.get("public_base_url", "")
    public_locked = bool(public_config)
    public_detail = (
        "Managed by environment or config file."
        if public_locked
        else "Saved in BinderBridge settings."
        if public_saved
        else "Not set yet. BinderBridge will infer links from each request until this is saved."
    )
    public_form_controls = (
        f"""
        <input class="span-2" id="setup-public-base-url" readonly name="public_base_url" value="{e(public_value)}">
        <p class="muted compact span-2">This value is managed outside the UI. Change `BINDERBRIDGE_PUBLIC_BASE_URL` or `[server] public_base_url` in config to update it.</p>
        """
        if public_locked
        else f"""
        <input class="span-2" id="setup-public-base-url" name="public_base_url" type="url" maxlength="240" placeholder="https://cards.example.com" value="{e(public_value)}">
        <p class="muted compact span-2">Use the real site origin users open in their browser. For passkeys and public installs, use HTTPS.</p>
        <div class="form-actions span-2">
            <button class="button primary" type="submit">Save public URL</button>
            <button class="button ghost" name="intent" value="clear" type="submit">Clear</button>
        </div>
        """
    )
    moderation = summary.get("registration_moderation", {})
    approval_options = option_tags(REGISTRATION_APPROVAL_MODE_OPTIONS, moderation.get("approval_mode", DEFAULT_REGISTRATION_APPROVAL_MODE))
    invite_only = bool(summary.get("registration_invite_only"))
    registration_label = "Invite-only" if invite_only else "Open registration"
    smtp_configured = bool(summary.get("smtp_configured"))
    smtp_status = "Configured" if smtp_configured else "Not configured"
    backup = summary["backups"]["automatic"]
    backup_rows = "".join(
        f'<li><strong>{e(archive["name"])}</strong><span>{e(archive["size_label"])} - {e(archive["created_at"])}</span></li>'
        for archive in summary["backups"].get("archives", [])
    ) or render_empty_action_state(
        "No backup archives yet.",
        "Create a backup before opening the site to regular use.",
        tag="li",
    )
    scryfall = summary.get("scryfall_bulk", {})
    scryfall_count = int(scryfall.get("card_count", 0) or 0)
    scryfall_error = scryfall.get("error", "")
    scryfall_running = str(scryfall.get("status", "")).lower() in ("running", "queued")
    scryfall_complete = scryfall_count > 0 and not scryfall_error
    scryfall_status = "Synced" if scryfall_complete else "Running" if scryfall_running else "Error" if scryfall_error else "Not synced"
    docs_base = str(SOURCE_URL or "").rstrip("/")
    readme_config_url = f"{docs_base}/blob/HEAD/README.md#configuration"
    deployment_first_run_url = f"{docs_base}/blob/HEAD/docs/DEPLOYMENT.md#3-first-run-admin-checklist"
    deployment_config_url = f"{docs_base}/blob/HEAD/docs/DEPLOYMENT.md#configuration-files"
    deployment_https_url = f"{docs_base}/blob/HEAD/docs/DEPLOYMENT.md#reverse-proxy-and-https"
    deployment_backup_url = f"{docs_base}/blob/HEAD/docs/DEPLOYMENT.md#backups-and-restore-drills"
    setup_doc_links = f"""
    <div class="setup-doc-links" aria-label="Setup documentation links">
        <a class="button ghost small" href="{e(readme_config_url)}" target="_blank" rel="noreferrer">Configuration reference</a>
        <a class="button ghost small" href="{e(deployment_first_run_url)}" target="_blank" rel="noreferrer">Deployment first-run checklist</a>
        <a class="button ghost small" href="{e(deployment_https_url)}" target="_blank" rel="noreferrer">HTTPS and public URL</a>
        <a class="button ghost small" href="{e(deployment_backup_url)}" target="_blank" rel="noreferrer">Backup drills</a>
    </div>
    """
    invite_result_panel = ""
    if invite_result:
        invite_result_panel = f"""
        <div class="invite-result span-2">
            <strong>Manual invite link</strong>
            <p class="muted compact">Copy this link and send it directly if SMTP is not configured, or if you want to invite someone outside the automatic email flow.</p>
            {render_copyable_field("setup-invite-link", "Invite link", invite_result["link"], f'Expires {invite_result["expires_at"][:10]}', "Copy invite link")}
        </div>
        """
    checklist_rows = "".join(render_admin_onboarding_item(item) for item in checklist["items"])
    content = f"""
    <section class="section-heading">
        <div>
            <p class="eyebrow">Admin setup</p>
            <h1>First-run setup wizard</h1>
            <p class="muted compact">Bring a fresh BinderBridge site online with the important operations in one place.</p>
        </div>
        <div class="actions">
            <a class="button secondary" href="/admin">Back to admin</a>
            <a class="button secondary" href="/admin/health">Maintenance health</a>
        </div>
    </section>
    <section class="panel setup-wizard-overview">
        <div class="panel-heading">
            <div>
                <h2>Setup progress</h2>
                <p class="muted compact">{e(complete_count)} of {e(total)} setup checks complete.</p>
            </div>
            <span class="status {setup_status_class}">{e(setup_status)}</span>
        </div>
        <div class="onboarding-progress-row">
            <span>{e(progress)}% ready</span>
            <div class="onboarding-progress" aria-label="{e(complete_count)} of {e(total)} onboarding steps complete">
                <span style="width: {e(progress)}%"></span>
            </div>
        </div>
        <ul class="stack-list onboarding-list">{checklist_rows}</ul>
    </section>
    <section class="panel setup-recommendation">
        <div class="panel-heading">
            <div>
                <p class="eyebrow">Recommended defaults</p>
                <h2>Recommended defaults for small local groups</h2>
                <p class="muted compact">These defaults keep a small trusted group easy to run while leaving room to grow later.</p>
            </div>
            <span class="pill">Local group friendly</span>
        </div>
        <ul class="setup-recommendation-list">
            <li><strong>Registration:</strong> invite-only with suspicious-signup review at a 25 risk threshold.</li>
            <li><strong>Public URL:</strong> set the exact URL members use; use HTTPS for passkeys, password recovery, and internet-facing installs.</li>
            <li><strong>Backups:</strong> automatic every 24 hours, keep 14 backups for up to 30 days, and create one manual backup before upgrades.</li>
            <li><strong>Email:</strong> SMTP is optional for local groups because manual invite and recovery links still work.</li>
            <li><strong>Scryfall:</strong> run the bulk sync before large imports so card matching and finish checks stay fast.</li>
        </ul>
        {setup_doc_links}
    </section>
    <section class="setup-wizard-grid">
        <form class="panel form-grid compact-form setup-step-card" id="setup-public-url" method="post" action="/admin/setup/public-url">
            <div class="span-2 panel-heading">
                <div><h2>1. Public URL</h2><p class="muted compact">{e(public_detail)}</p></div>
                {render_setup_status(bool(public_value), "Configured" if public_value else "Missing")}
            </div>
            <label class="span-2" for="setup-public-base-url">Public base URL</label>
            {public_form_controls}
            <div class="setup-doc-links span-2">
                <a class="button ghost small" href="{e(deployment_https_url)}" target="_blank" rel="noreferrer">Public URL guidance</a>
                <a class="button ghost small" href="{e(readme_config_url)}" target="_blank" rel="noreferrer">Config reference</a>
            </div>
        </form>
        <form class="panel form-grid compact-form setup-step-card" id="setup-registration" method="post" action="/admin/setup/registration">
            <div class="span-2 panel-heading">
                <div><h2>2. Registration</h2><p class="muted compact">Pick the default path for new members.</p></div>
                <span class="pill">{e(registration_label)}</span>
            </div>
            <label class="checkbox-line span-2">
                <input type="checkbox" name="invite_only_registration" value="1"{checked(invite_only)}>
                Require an invite link for new accounts
            </label>
            <label class="span-2">Account approval
                <select name="registration_approval_mode">{approval_options}</select>
            </label>
            <label>Suspicious score threshold
                <input name="registration_risk_threshold" type="number" min="0" max="1000" step="1" value="{e(moderation.get("risk_threshold", DEFAULT_REGISTRATION_RISK_THRESHOLD))}">
            </label>
            <p class="muted compact span-2">Recommended for small local groups: enable invite-only registration, choose suspicious-signup review, and use a threshold around 25.</p>
            <div class="form-actions span-2"><button class="button primary" type="submit">Save registration policy</button></div>
        </form>
        <article class="panel setup-step-card" id="setup-smtp">
            <div class="panel-heading">
                <div><h2>3. Email delivery</h2><p class="muted compact">SMTP powers invite emails, password recovery email, and optional notification email.</p></div>
                {render_setup_status(smtp_configured, smtp_status)}
            </div>
            <p class="muted compact">SMTP is configured from environment variables or `binderbridge.ini`. Without SMTP, admins can still create manual invite and recovery links.</p>
            <div class="actions"><a class="button secondary" href="/admin/health">Open email health</a><a class="button ghost" href="{e(deployment_config_url)}" target="_blank" rel="noreferrer">SMTP config docs</a><a class="button ghost" href="/account/profile#account-notifications">Notification settings</a></div>
        </article>
        <form class="panel form-grid compact-form setup-step-card" id="setup-backups" method="post" action="/admin/setup/backup">
            <div class="span-2 panel-heading">
                <div><h2>4. Backups</h2><p class="muted compact">Create a first safety backup and choose automatic backup retention.</p></div>
                {render_setup_status(bool(summary["backups"].get("archives")), "Backed up" if summary["backups"].get("archives") else "No backups")}
            </div>
            <label class="checkbox-line span-2">
                <input type="checkbox" name="automatic_backup_enabled" value="1"{checked(backup["enabled"])}>
                Run scheduled backups
            </label>
            <label>Every hours
                <input name="automatic_backup_interval_hours" type="number" min="1" step="1" value="{e(backup["interval_hours"])}">
            </label>
            <label>Keep newest
                <input name="automatic_backup_retention_count" type="number" min="1" step="1" value="{e(backup["retention_count"])}">
            </label>
            <label>Delete older than days
                <input name="automatic_backup_retention_days" type="number" min="0" step="1" value="{e(backup["retention_days"])}">
            </label>
            <label class="checkbox-line">
                <input type="checkbox" name="run_backup_now" value="1">
                Create a backup now
            </label>
            <p class="muted compact span-2">Recommended for most small groups: run every 24 hours, keep 14 backups, and remove automatic backups older than 30 days.</p>
            <div class="form-actions span-2"><button class="button primary" type="submit">Save backup plan</button><a class="button ghost" href="/admin#admin-operations">Open backup tools</a><a class="button ghost" href="{e(deployment_backup_url)}" target="_blank" rel="noreferrer">Backup docs</a></div>
            <ul class="stack-list compact-stack span-2">{backup_rows}</ul>
        </form>
        <form class="panel setup-step-card" id="setup-scryfall" method="post" action="/admin/setup/scryfall">
            <div class="panel-heading">
                <div><h2>5. Scryfall data</h2><p class="muted compact">Local bulk data improves imports, lookup matching, finish checks, and price refreshes.</p></div>
                {render_setup_status(scryfall_complete, scryfall_status)}
            </div>
            <p class="muted compact">{e(scryfall_count)} cached card records. Last update: {e(str(scryfall.get("updated_at", "") or "Never")[:16].replace("T", " "))}.</p>
            {f'<p class="notice error compact">{e(scryfall_error)}</p>' if scryfall_error else ''}
            <div class="actions"><button class="button primary" type="submit"{' disabled' if scryfall_running else ''}>{e("Sync running" if scryfall_running else "Start Scryfall sync")}</button><a class="button ghost" href="/admin/jobs">Open jobs</a><a class="button ghost" href="{e(deployment_first_run_url)}" target="_blank" rel="noreferrer">First-run docs</a></div>
        </form>
        <form class="panel form-grid compact-form setup-step-card" id="setup-invite" method="post" action="/admin/invites">
            <input type="hidden" name="redirect_to" value="/admin/setup">
            <div class="span-2 panel-heading">
                <div><h2>6. First invite</h2><p class="muted compact">Create a first invite for another member or tester.</p></div>
                {render_setup_status(summary["invite_count"] > 0, f'{summary["invite_count"]} created')}
            </div>
            <label class="span-2">Recipient email
                <input required name="email" type="email" maxlength="254" autocomplete="email">
            </label>
            <p class="muted compact span-2">If SMTP is not configured, BinderBridge will show a copyable invite link.</p>
            <div class="form-actions span-2"><button class="button primary" type="submit">Create invite</button><a class="button ghost" href="/admin#admin-invites">Review invites</a></div>
            {invite_result_panel}
        </form>
        <article class="panel setup-step-card" id="setup-import">
            <div class="panel-heading">
                <div><h2>7. First collection import</h2><p class="muted compact">Import collection CSVs after backup and Scryfall setup are ready.</p></div>
                {render_setup_status(summary["import_count"] > 0, f'{summary["import_count"]} imports')}
            </div>
            <p class="muted compact">Use ManaBox, Archidekt, Deckbox, Moxfield, Dragon Shield, Delver Lens, or a custom CSV mapping preset.</p>
            <div class="actions"><a class="button primary" href="/import">Open import</a><a class="button ghost" href="/collection">Open collection</a></div>
        </article>
        <form class="panel setup-step-card" id="setup-complete" method="post" action="/admin/setup/complete">
            <div class="panel-heading">
                <div><h2>Finish setup</h2><p class="muted compact">Mark the wizard complete once the site is ready for your group.</p></div>
                {render_setup_status(bool(completed_at), "Complete" if completed_at else "Optional")}
            </div>
            <p class="muted compact">You can come back to this page any time. The admin health page continues to show operational warnings.</p>
            <div class="actions"><button class="button primary" type="submit">Mark setup complete</button><a class="button secondary" href="/admin">Return to admin</a></div>
        </form>
    </section>
    """
    return render_layout(user, "First-run setup", content, active="admin", notice=notice, status=status)


def health_time_label(value):
    text = str(value or "").strip()
    return text[:16].replace("T", " ") if text else "Never"


def health_status_class(value):
    status = str(value or "").strip().lower()
    if status in ("done", "idle", "sent", "none", "ok", "completed", "applied", "succeeded"):
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
    if status in ("done", "idle", "sent", "none", "ok", "completed", "enabled", "applied", "succeeded"):
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


def render_database_storage_chart(history):
    points = list(history or ())
    if not points:
        return render_empty_action_state(
            "No database storage snapshots yet.",
            "Storage snapshots will appear after maintenance health records database size.",
        )
    maximum = max(int(item["total_bytes"] or 0) for item in points) or 1
    bars = "".join(
        f"""
        <div class="storage-growth-point" title="{e(item["recorded_at"][:16].replace("T", " "))} - {e(bytes_label(item["total_bytes"]))} - {e(item["source"])}">
            <span class="storage-growth-bar" style="height: {e(max(4, round((int(item["total_bytes"] or 0) / maximum) * 100)))}%"></span>
        </div>
        """
        for item in points
    )
    first = int(points[0]["total_bytes"] or 0)
    latest = int(points[-1]["total_bytes"] or 0)
    difference = latest - first
    change_label = f"+{bytes_label(difference)}" if difference > 0 else f"-{bytes_label(abs(difference))}" if difference < 0 else "No change"
    return f"""
    <div class="storage-growth-chart" role="img" aria-label="Database storage growth across {e(len(points))} snapshots">
        {bars}
    </div>
    <div class="storage-growth-labels">
        <span>{e(points[0]["recorded_at"][:10])}</span>
        <strong>{e(change_label)}</strong>
        <span>{e(points[-1]["recorded_at"][:10])}</span>
    </div>
    """


def render_database_maintenance_run(item):
    action = str(item["action"] or "").upper()
    status_value = str(item["status"] or "")
    change = int(item["after_bytes"] or 0) - int(item["before_bytes"] or 0)
    change_label = f"+{bytes_label(change)}" if change > 0 else f"-{bytes_label(abs(change))}" if change < 0 else "No size change"
    return f"""
    <li>
        <div>
            <strong>{e(action)}</strong>
            <span>{e(item["completed_at"][:16].replace("T", " "))} - {e(item["duration_ms"])} ms - {e(change_label)}</span>
            <small>{e(item["details"])}</small>
        </div>
        <span class="status {health_status_class(status_value)}">{e(status_value.title())}</span>
    </li>
    """


def render_database_index_row(item):
    observations = item["observed_plans"]
    observation_html = "".join(f'<span class="pill">{e(label)}</span>' for label in observations)
    if not observation_html:
        observation_html = '<span class="muted compact">Not chosen by the current sample plans.</span>'
    flags = []
    if item["unique"]:
        flags.append("Unique")
    if item["partial"]:
        flags.append("Partial")
    flags_html = "".join(f'<span class="pill">{e(flag)}</span>' for flag in flags)
    return f"""
    <tr>
        <td data-label="Index">
            <strong>{e(item["name"])}</strong>
            <div class="status-row">{flags_html}</div>
        </td>
        <td data-label="Table"><strong>{e(item["table"])}</strong></td>
        <td data-label="Columns"><span>{e(item["columns_label"])}</span></td>
        <td data-label="Planner statistics"><span>{e(item["stat"] or "Run ANALYZE to populate statistics")}</span></td>
        <td data-label="Storage"><strong>{e(item["size_label"])}</strong><span class="subtle">{e(item["page_count"])} pages</span></td>
        <td data-label="Sample planner use"><div class="status-row">{observation_html}</div></td>
    </tr>
    """


def render_database_migration_row(item):
    applied_at = item["applied_at"][:16].replace("T", " ") if item["applied_at"] else "Not applied"
    return f"""
    <tr>
        <td data-label="Version"><strong>v{e(item["version"])}</strong></td>
        <td data-label="Migration"><strong>{e(item["description"])}</strong><span class="subtle">{e(item["function"])}</span></td>
        <td data-label="Status"><span class="status {health_status_class(item["status"])}">{e(item["status"].title())}</span></td>
        <td data-label="Recorded">{e(applied_at)}</td>
    </tr>
    """


def render_admin_database(user, notice=None, status="info"):
    dashboard = database_maintenance_dashboard()
    storage = dashboard["storage"]
    index_data = dashboard["indexes"]
    index_summary = index_data["summary"]
    migration_data = dashboard["migrations"]
    run_rows = "".join(render_database_maintenance_run(item) for item in dashboard["runs"])
    run_rows = run_rows or render_empty_action_state(
        "No manual database maintenance actions have been recorded yet.",
        "Analyze, vacuum, and checkpoint runs will appear here after they complete.",
        tag="li",
    )
    index_rows = "".join(render_database_index_row(item) for item in index_data["indexes"])
    index_rows = index_rows or '<tr><td class="empty-state" colspan="6">No application indexes found.</td></tr>'
    migration_rows = "".join(render_database_migration_row(item) for item in migration_data["migrations"])
    content = f"""
    <section class="section-heading">
        <div>
            <p class="eyebrow">Admin</p>
            <h1>Database maintenance</h1>
            <p class="muted">Inspect SQLite storage, refresh planner statistics, reclaim unused pages, and review schema history.</p>
        </div>
        <div class="actions">
            <a class="button secondary" href="/admin">Back to admin</a>
            <a class="button secondary" href="/admin/health">Maintenance health</a>
            <a class="button secondary" href="/admin/logs">Activity log</a>
        </div>
    </section>
    <section class="metric-grid">
        <article class="{health_card_class('metric', 'ok')}"><span>{e(storage["total_size_label"])}</span><p>database and WAL storage</p></article>
        <article class="{health_card_class('metric', 'warning' if storage["reusable_percent"] >= 20 else 'ok')}"><span>{e(storage["reusable_size_label"])}</span><p>reusable free pages ({e(storage["reusable_percent"])}%)</p></article>
        <article class="{health_card_class('metric', 'ok')}"><span>{e(index_summary["total"])}</span><p>application indexes</p></article>
        <article class="{health_card_class('metric', 'ok' if migration_data["current_version"] == migration_data["latest_version"] else 'warning')}"><span>v{e(migration_data["current_version"])}</span><p>schema version</p></article>
    </section>
    <section class="panel database-action-panel">
        <div class="panel-heading">
            <div><h2>Maintenance actions</h2><p class="muted compact">These operations run synchronously and are recorded in the admin activity log.</p></div>
            <span class="pill">SQLite {e(storage["journal_mode"].upper())}</span>
        </div>
        <div class="maintenance-grid">
            <form class="backup-action-card" method="post" action="/admin/database/analyze">
                <strong>Refresh planner statistics</strong>
                <span class="subtle">Runs ANALYZE and PRAGMA optimize so SQLite can make better index choices. Routine and low risk.</span>
                <button class="button primary" type="submit">Run ANALYZE</button>
            </form>
            <form class="backup-action-card" method="post" action="/admin/database/vacuum">
                <strong>Rebuild and compact database</strong>
                <span class="subtle">Runs quick_check, checkpoints WAL, then VACUUM. This needs temporary free disk space and may pause writes.</span>
                <a class="button ghost small" href="/admin">Create a backup first</a>
                <button class="button danger" type="submit" data-confirm="Run VACUUM now? This can take time and temporarily block database writes. Create a backup first.">Run VACUUM</button>
            </form>
            <form class="backup-action-card" method="post" action="/admin/database/snapshot">
                <strong>Record storage snapshot</strong>
                <span class="subtle">Adds a point to the storage-growth chart. The page also records at most one automatic point per day.</span>
                <button class="button secondary" type="submit">Record snapshot</button>
            </form>
        </div>
    </section>
    <section class="admin-settings-grid database-overview-grid">
        <article class="panel">
            <div class="panel-heading"><div><h2>Storage growth</h2><p class="muted compact">Database, WAL, and shared-memory files combined.</p></div><span class="pill">{e(len(dashboard["storage_history"]))} snapshots</span></div>
            {render_database_storage_chart(dashboard["storage_history"])}
        </article>
        <article class="panel">
            <div class="panel-heading"><h2>Current storage</h2><span class="pill">{e(storage["journal_mode"].upper())}</span></div>
            <div class="detail-grid">
                <span>Database file</span><strong>{e(storage["database_size_label"])}</strong>
                <span>WAL file</span><strong>{e(storage["wal_size_label"])}</strong>
                <span>Shared memory</span><strong>{e(storage["shm_size_label"])}</strong>
                <span>Page size</span><strong>{e(storage["page_size_label"])}</strong>
                <span>Allocated pages</span><strong>{e(storage["page_count"])}</strong>
                <span>Reusable pages</span><strong>{e(storage["freelist_count"])}</strong>
                <span>Auto vacuum mode</span><strong>{e(storage["auto_vacuum"])}</strong>
            </div>
        </article>
        <article class="panel span-2">
            <div class="panel-heading"><div><h2>Recent maintenance</h2><p class="muted compact">Manual ANALYZE, VACUUM, and storage snapshot actions.</p></div></div>
            <ul class="stack-list compact-stack">{run_rows}</ul>
        </article>
    </section>
    <section class="panel flush database-index-panel">
        <div class="panel-heading padded">
            <div>
                <h2>Index visibility</h2>
                <p class="muted compact">SQLite does not expose cumulative index-use counters. “Sample planner use” shows indexes chosen by BinderBridge's representative query plans; ANALYZE statistics and storage footprint provide the remaining visibility.</p>
            </div>
            <div class="status-row">
                <span class="pill">{e(index_summary["observed"])} observed in samples</span>
                <span class="pill">{e(index_summary["with_stats"])} with ANALYZE stats</span>
                <span class="pill">{e(index_summary["total_size_label"])} total</span>
            </div>
        </div>
        <div class="table-wrap">
            <table class="admin-table responsive-card-table database-index-table">
                <thead><tr><th>Index</th><th>Table</th><th>Columns</th><th>Planner statistics</th><th>Storage</th><th>Sample planner use</th></tr></thead>
                <tbody>{index_rows}</tbody>
            </table>
        </div>
    </section>
    <section class="panel flush database-migration-panel">
        <div class="panel-heading padded">
            <div><h2>Migration history</h2><p class="muted compact">Schema migrations run automatically during startup. Older applied versions were backfilled when migration-history tracking was introduced.</p></div>
            <span class="status {health_status_class('applied' if migration_data["current_version"] == migration_data["latest_version"] else 'pending')}">v{e(migration_data["current_version"])} of v{e(migration_data["latest_version"])}</span>
        </div>
        <div class="table-wrap">
            <table class="admin-table responsive-card-table database-migration-table">
                <thead><tr><th>Version</th><th>Migration</th><th>Status</th><th>Recorded</th></tr></thead>
                <tbody>{migration_rows}</tbody>
            </table>
        </div>
    </section>
    """
    return render_layout(user, "Database maintenance", content, active="admin", notice=notice, status=status)


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
    ) or render_empty_action_state(
        "No backups created yet.",
        "Use the backup tools to create a recovery point before major maintenance.",
        actions=(("/admin#admin-operations", "Open backup tools", "secondary"),),
        tag="li",
    )
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
    failed_rows = failed_rows or render_empty_action_state(
        "No failed email notifications.",
        "Recent email delivery failures will appear here with replay controls.",
        tag="li",
    )
    setup_warning_rows = "".join(render_setup_warning_item(item) for item in setup_warnings)
    setup_warning_rows = setup_warning_rows or render_empty_action_state(
        "No setup warnings right now.",
        "Configuration warnings from setup, email, backups, and Scryfall will appear here.",
        tag="li",
    )
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
            <a class="button secondary" href="/admin/database">Database maintenance</a>
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
                <a class="backup-action-card link-card" href="/admin/database">
                    <strong>Open database maintenance</strong>
                    <span class="subtle">Review storage growth, indexes, migration history, and run ANALYZE or VACUUM.</span>
                    <span class="button ghost small">Open database tools</span>
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
                    <p class="muted compact">Set a value to 0 to keep that data forever. Cleanup only removes inactive records: read notifications, terminal webhook deliveries, old audit logs, resolved dispute evidence, revoked API tokens, and finished invites.</p>
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
            <label>Revoked API tokens
                <input name="api_token_days" type="number" min="0" max="36500" step="1" value="{e(retention["api_token_days"])}">
                <span class="subtle">{e(retention_eligible["api_tokens"])} eligible; active tokens are protected.</span>
            </label>
            <label>Inactive invites
                <input name="invite_days" type="number" min="0" max="36500" step="1" value="{e(retention["invite_days"])}">
                <span class="subtle">{e(retention_eligible["registration_invites"])} eligible; pending invites are protected.</span>
            </label>
            <p class="muted compact span-2">Retention age is measured from creation time, webhook completion time, or dispute resolution time as appropriate. Last cleanup: {e(retention_last_run)}.</p>
            <div class="form-actions span-2">
                <button class="button primary" name="intent" value="save" type="submit">Save retention settings</button>
                <button class="button danger" name="intent" value="save_run" type="submit" data-confirm="Delete all records currently eligible under these retention settings? This cannot be undone.">Save and run cleanup</button>
            </div>
        </form>
        <article class="{health_card_class('panel', 'ok')}">
            <div class="panel-heading">
                <h2>Database</h2>
                <a class="button ghost small" href="/admin/database">Open tools</a>
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
    confirm_attr = f' data-confirm="{e(confirm)}"' if confirm else ""
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


def admin_job_background_row(job):
    status_value = row_value(job, "status", "")
    label = JOB_TYPE_LABELS.get(row_value(job, "job_type", ""), row_value(job, "job_type", "Background job").replace("_", " ").title())
    error = row_value(job, "last_error", "")
    progress = row_value(job, "progress_message", "")
    result = parse_job_json(row_value(job, "result_json", "{}"), {})
    result_line = f'<span class="subtle">Last result: {e(json.dumps(result, ensure_ascii=True, sort_keys=True)[:300])}</span>' if result else ""
    error_line = f'<p class="job-error">{e(error)}</p>' if error else ""
    actions = ""
    if status_value in ("failed", "cancelled"):
        actions += admin_job_retry_form("/admin/jobs/background/retry", "job_id", job["id"])
    if status_value in ("pending", "running"):
        actions += admin_job_retry_form(
            "/admin/jobs/background/cancel",
            "job_id",
            job["id"],
            "Cancel",
            "danger",
            "Cancel this background job? Running work may finish its current operation before stopping.",
        )
    return f"""
    <li class="job-row {health_severity_class(health_severity_for_status(status_value))}">
        <div class="job-main">
            <strong>{e(label)}</strong>
            <span class="subtle">Job #{e(job["id"])} - updated {e(admin_job_time_label(row_value(job, "updated_at", "")))}</span>
            <div class="job-meta">
                <span>{e(job["attempts"])} of {e(job["max_attempts"])} attempts</span>
                <span>Available {e(admin_job_time_label(row_value(job, "available_at", "")))}</span>
                <span>{e(progress or "No progress message")}</span>
            </div>
            {result_line}
            {error_line}
        </div>
        <div class="job-actions">
            <span class="status {health_status_class(status_value)}">{e(admin_job_status_label(status_value))}</span>
            {actions}
        </div>
    </li>
    """


def render_admin_jobs(user, notice=None, status="info"):
    dashboard = maintenance_job_dashboard()
    metrics = dashboard["metrics"]
    price_refresh = dashboard["scryfall_price_refresh"]
    import_rows = "".join(admin_job_import_row(batch) for batch in dashboard["imports"])
    import_rows = import_rows or render_empty_action_state(
        "No import batches have been recorded yet.",
        "CSV and deck imports will appear here after members preview or apply them.",
        tag="li",
    )
    scryfall_rows = "".join(admin_job_scryfall_row(job) for job in dashboard["scryfall_jobs"])
    scryfall_rows = scryfall_rows or render_empty_action_state(
        "No Scryfall enrichment jobs need attention.",
        "Failed or queued card enrichment jobs will appear here when they need admin review.",
        tag="li",
    )
    price_rows = "".join(admin_job_price_row(job) for job in dashboard["price_jobs"])
    price_rows = price_rows or render_empty_action_state(
        "No queued price refresh jobs need attention.",
        "Provider price refresh jobs will appear here if they queue, fail, or need a retry.",
        tag="li",
    )
    notification_rows = "".join(admin_job_notification_row(item) for item in dashboard["failed_notifications"])
    notification_rows = notification_rows or render_empty_action_state(
        "No failed notification emails.",
        "Failed email deliveries will appear here with retry controls.",
        tag="li",
    )
    background_rows = "".join(admin_job_background_row(item) for item in dashboard["background_jobs"])
    background_rows = background_rows or render_empty_action_state(
        "No durable background jobs have been recorded.",
        "Scheduled backups, webhook delivery, notifications, and Scryfall work will be listed here.",
        tag="li",
    )
    runner = dashboard["job_runner"]
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
        <article class="metric">
            <span>{e(metrics["background_attention"])}</span>
            <p>runner jobs needing attention</p>
        </article>
    </section>
    <section class="job-dashboard-grid">
        <article class="panel job-dashboard-card span-2">
            <div class="panel-heading">
                <div>
                    <h2>Durable background runner</h2>
                    <p class="muted compact">Leased, retryable orchestration for Scryfall, backups, notifications, and webhooks.</p>
                </div>
                <div class="status-row">
                    <span class="pill">{e(runner.get("mode", "unknown"))} mode</span>
                    <span class="pill">{e(runner.get("lease_seconds", ""))}s lease</span>
                    {render_health_status_counts(dashboard["background_counts"])}
                </div>
            </div>
            <ul class="stack-list job-list">{background_rows}</ul>
        </article>
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
        <td data-label="Issue">
            <strong>#{e(item["id"])}</strong>
            <span class="subtle">{e(item["created_at"][:16].replace("T", " "))}</span>
            {escalation_badge}
        </td>
        <td data-label="Trade">
            <a href="/trades/{item["trade_id"]}">Trade #{e(item["trade_id"])}</a>
            <span class="subtle">{e(trade_dispute_user_label(item, "proposer"))} with {e(trade_dispute_user_label(item, "recipient"))}</span>
        </td>
        <td data-label="Status">
            <span class="status {e(trade_dispute_status_class(item["status"]))}">{e(trade_dispute_status_label(item["status"]))}</span>
            <span class="subtle">{e(trade_dispute_category_label(item["category"]))}</span>
        </td>
        <td data-label="Report">
            <strong>{e(trade_dispute_user_label(item, "reporter"))}</strong>
            <p class="compact">{e(item["body"])}</p>
            <div class="evidence-block admin-evidence-block">
                <strong>Evidence</strong>
                {evidence_html}
            </div>
            {resolved_line}
        </td>
        <td data-label="Admin review">
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
            <table class="admin-table responsive-card-table admin-dispute-table">
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
        <form method="post" action="/admin/invites/{invite["id"]}/revoke#admin-access">
            <button class="button ghost small" type="submit">Revoke</button>
        </form>
        """
        if row_value(invite, "status", "") == "pending"
        else ""
    )
    delete_button = (
        f"""
        <form method="post" action="/admin/invites/{invite["id"]}/delete#admin-access">
            <button class="button danger small" type="submit" data-confirm="Delete this invite record?">Delete</button>
        </form>
        """
        if row_value(invite, "status", "") != "pending"
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
            {delete_button}
        </div>
    </li>
    """


def render_registration_review_row(item):
    reasons = registration_attempt_reasons(item)
    reason_text = ", ".join(
        f"{reason.get('label', 'Signal')} (+{reason.get('points', 0)})"
        for reason in reasons
        if isinstance(reason, dict)
    ) or "No specific risk signals; review required by policy."
    invite_line = ""
    if row_value(item, "invite_id", None):
        inviter = row_value(item, "inviter_name", "") or row_value(item, "inviter_username", "")
        invite_suffix = f" from {inviter}" if inviter else ""
        invite_line = f'<span class="subtle">Invite #{e(row_value(item, "invite_id", ""))}{e(invite_suffix)}</span>'
    email_domain = row_value(item, "email_domain", "")
    email_line = f'<span class="subtle">Email domain: {e(email_domain)}</span>' if email_domain else ""
    created = row_value(item, "attempt_created_at", "") or row_value(item, "created_at", "")
    return f"""
    <li class="invite-row registration-review-row">
        <div>
            <strong>{e(item["display_name"])}</strong>
            <span class="subtle">@{e(item["username"])} - requested {e(created[:16].replace("T", " "))}</span>
            {email_line}
            {invite_line}
            <span class="subtle">{e(reason_text)}</span>
        </div>
        <div class="invite-actions">
            <span class="status pending">Risk {e(row_value(item, "risk_score", 0))}</span>
            <form method="post" action="/admin/registration-review/{item["id"]}/approve#admin-access">
                <input name="note" placeholder="Review note">
                <button class="button primary small" type="submit">Approve</button>
            </form>
            <form method="post" action="/admin/registration-review/{item["id"]}/deny#admin-access" data-confirm="Deny this account registration?">
                <input name="note" placeholder="Denial note">
                <button class="button danger small" type="submit">Deny</button>
            </form>
        </div>
    </li>
    """


def render_admin_user_row(admin_user, managed_user):
    status_parts = []
    managed_role = user_role(managed_user)
    role_class = "accepted" if managed_role in (ROLE_OWNER, ROLE_ADMIN) else "pending" if managed_role in (ROLE_MODERATOR, ROLE_ORGANIZER) else ""
    status_parts.append(f'<span class="status {role_class}">{e(role_label(managed_role))}</span>')
    registration_status = row_value(managed_user, "registration_status", "active")
    if managed_user["is_banned"]:
        status_parts.append('<span class="status declined">Banned</span>')
    elif registration_status == "pending":
        status_parts.append('<span class="status pending">Pending review</span>')
    elif registration_status == "denied":
        status_parts.append('<span class="status declined">Denied</span>')
    else:
        status_parts.append('<span class="status accepted">Active</span>')
    if two_factor_enabled(managed_user):
        status_parts.append('<span class="status accepted">2FA on</span>')
    elif row_value(managed_user, "totp_secret", ""):
        status_parts.append('<span class="status pending">2FA setup</span>')
    if int(row_value(managed_user, "pending_recovery_count", 0) or 0):
        status_parts.append('<span class="status pending">Recovery requested</span>')
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
        <div class="admin-control-group">
            <span class="admin-control-title">Role</span>
            <form class="inline-admin-form role-form" method="post" action="/admin/user/{managed_user["id"]}/role">
                <label>Role<select name="role">{options}</select></label>
                <button class="button secondary small" type="submit">Change role</button>
            </form>
        </div>
        """
    moderation_controls = ""
    if can_moderate:
        moderation_controls = f"""
        <div class="admin-control-group">
            <span class="admin-control-title">Moderation</span>
            <form class="inline-admin-form" method="post" action="/admin/user/{managed_user["id"]}/ban">
                <input type="hidden" name="action" value="{ban_action}">
                <input name="reason" placeholder="Ban reason" value="{e(ban_reason)}" {'disabled' if managed_user["is_banned"] else ""}>
                <button class="button {'secondary' if managed_user["is_banned"] else 'danger'} small" type="submit">{ban_label}</button>
            </form>
        </div>
        <div class="admin-control-group">
            <span class="admin-control-title">Trust</span>
            <form class="inline-admin-form trust-form" method="post" action="/admin/user/{managed_user["id"]}/trust">
                <button class="button secondary small" name="action" value="{primary_trust_action}" type="submit">{primary_trust_label}</button>
                <button class="button ghost small" name="action" value="{secondary_trust_action}" type="submit">{secondary_trust_label}</button>
            </form>
        </div>
        <div class="admin-control-group">
            <span class="admin-control-title">Notes</span>
            <form class="inline-admin-form notes-form" method="post" action="/admin/user/{managed_user["id"]}/notes">
                <textarea name="admin_notes" rows="2" placeholder="Staff notes">{e(managed_user["admin_notes"])}</textarea>
                <button class="button secondary small" type="submit">Save notes</button>
            </form>
        </div>
        """
    security_controls = ""
    if can_manage_user:
        security_controls = f"""
        <div class="admin-control-group">
            <span class="admin-control-title">Security</span>
            <form class="inline-admin-form" method="post" action="/admin/user/{managed_user["id"]}/password">
                <input required name="current_password" type="password" autocomplete="current-password" placeholder="Your admin password">
                <button class="button secondary small" type="submit" data-confirm="Issue a one-time password recovery link and sign this user out?">Issue reset link</button>
            </form>
            <form class="inline-admin-form role-form" method="post" action="/admin/user/{managed_user["id"]}/2fa">
                <button class="button secondary small" type="submit" data-confirm="Reset two-factor authentication for this user? They will need to set it up again.">Reset 2FA</button>
            </form>
        </div>
        """
    controls = moderation_controls + security_controls + role_form
    if not controls:
        controls = '<span class="muted compact">No actions available for this role.</span>'
    return f"""
    <tr>
        <td data-label="User">
            <strong>{e(managed_user["display_name"])}</strong>
            <span class="subtle">@{e(managed_user["username"])}</span>
            <span class="subtle">{e(managed_user["email"] or "No email")}</span>
            {self_note}
        </td>
        <td data-label="Status">
            <div class="status-stack">{''.join(status_parts)}</div>
            {f'<span class="subtle">{e(ban_reason)}</span>' if ban_reason else ""}
            <span class="subtle">{e(trust_detail)}</span>
        </td>
        <td data-label="Activity">
            <span class="subtle">{e(managed_user["collection_count"])} collection entries</span>
            <span class="subtle">{e(managed_user["want_count"])} wants</span>
            <span class="subtle">{e(managed_user["trade_count"])} trades</span>
            <span class="subtle">{e(managed_user["completed_trade_count"])} completed trades</span>
            <span class="subtle">Joined {e(managed_user["created_at"][:10])}</span>
        </td>
        <td data-label="Controls">
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
