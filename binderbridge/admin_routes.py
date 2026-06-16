"""Admin HTTP route handlers for BinderBridge."""


def admin_request_ip(self):
    forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
    if forwarded:
        return forwarded
    try:
        return self.client_address[0]
    except (AttributeError, TypeError, IndexError):
        return ""


def admin_user_agent(self):
    return self.headers.get("User-Agent", "")


def admin_page(self, user, notice=None, status="info"):
    if not require_capability(user, CAP_ACCESS_ADMIN):
        return self.not_found(user)
    return self.html(render_admin(user, notice=notice, status=status))


def admin_logs_page(self, user, query):
    if not require_capability(user, CAP_VIEW_AUDIT_LOG):
        return self.not_found(user)
    return self.html(render_admin_logs(user, query))


def admin_health_page(self, user):
    if not require_capability(user, CAP_MANAGE_MAINTENANCE):
        return self.not_found(user)
    return self.html(render_admin_health(user))


def admin_collection_health_page(self, user):
    if not require_capability(user, CAP_MANAGE_MAINTENANCE):
        return self.not_found(user)
    return self.html(render_admin_collection_health(user))


def admin_database_page(self, user):
    if not require_capability(user, CAP_MANAGE_MAINTENANCE):
        return self.not_found(user)
    return self.html(render_admin_database(user))


def admin_database_maintenance_action(self, user, action):
    if not require_capability(user, CAP_MANAGE_MAINTENANCE):
        return self.not_found(user)
    try:
        result = run_database_maintenance(action)
    except ValueError as exc:
        return self.html(render_admin_database(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    action_label = action.upper()
    details = (
        f"{result['details']} Before {result['before']['total_size_label']}; "
        f"after {result['after']['total_size_label']}; duration {result['duration_ms']} ms."
    )
    log_admin_action(
        user["id"],
        f"database_{action}_completed",
        None,
        "database",
        action_label,
        details,
        admin_request_ip(self),
        admin_user_agent(self),
    )
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],)) or user
    notice = f"{action_label} completed in {result['duration_ms']} ms."
    if action == "vacuum":
        notice += f" Reclaimed {result['saved_size_label']}."
    return self.html(render_admin_database(updated, notice=notice))


def admin_database_analyze(self, user):
    return admin_database_maintenance_action(self, user, "analyze")


def admin_database_vacuum(self, user):
    return admin_database_maintenance_action(self, user, "vacuum")


def admin_database_snapshot(self, user):
    if not require_capability(user, CAP_MANAGE_MAINTENANCE):
        return self.not_found(user)
    snapshot = run_database_storage_snapshot()
    log_admin_action(
        user["id"],
        "database_storage_snapshot_recorded",
        None,
        "database",
        "Storage snapshot",
        f"Recorded database storage snapshot at {bytes_label(snapshot['total_bytes'])}.",
        admin_request_ip(self),
        admin_user_agent(self),
    )
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],)) or user
    return self.html(render_admin_database(updated, notice="Database storage snapshot recorded."))


def admin_health_retry_jobs(self, user):
    if not require_capability(user, CAP_MANAGE_MAINTENANCE):
        return self.not_found(user)
    result = retry_recoverable_maintenance_jobs()
    if result["scryfall_jobs"]:
        start_scryfall_enrichment_worker()
    if result["price_jobs"]:
        start_price_refresh_worker()
    log_admin_action(
        user["id"],
        "maintenance_jobs_retried",
        None,
        "maintenance",
        "Recoverable jobs",
        f"Retried {result['scryfall_jobs']} Scryfall job(s) and {result['price_jobs']} price job(s).",
        admin_request_ip(self),
        admin_user_agent(self),
    )
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],)) or user
    if result["total"]:
        notice = f"Retried {result['total']} recoverable job{'s' if result['total'] != 1 else ''}."
        return self.html(render_admin_health(updated, notice=notice))
    return self.html(render_admin_health(updated, notice="No recoverable jobs needed retrying.", status="warning"))


def admin_health_replay_notifications(self, user):
    if not require_capability(user, CAP_MANAGE_MAINTENANCE):
        return self.not_found(user)
    try:
        result = replay_failed_notification_emails(limit=50)
    except ValueError as exc:
        return self.html(render_admin_health(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    log_admin_action(
        user["id"],
        "notification_emails_replayed",
        None,
        "maintenance",
        "Failed notification emails",
        f"Queued {result['queued']} failed notification email(s). Sent {result['sent']}, failed {result['failed']}, skipped {result['skipped']}.",
        admin_request_ip(self),
        admin_user_agent(self),
    )
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],)) or user
    notice = f"Replayed failed notification emails. Queued {result['queued']}; sent {result['sent']}; failed {result['failed']}; skipped {result['skipped']}."
    return self.html(render_admin_health(updated, notice=notice, status="warning" if result["failed"] else "info"))


def admin_health_check_backups(self, user):
    if not require_capability(user, CAP_MANAGE_BACKUPS):
        return self.not_found(user)
    result = run_backup_integrity_check()
    log_admin_action(
        user["id"],
        "backup_integrity_checked",
        None,
        "backup",
        "Backup integrity",
        result["message"],
        admin_request_ip(self),
        admin_user_agent(self),
    )
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],)) or user
    status = "info" if result["status"] == "ok" else "warning" if result["status"] == "warning" else "error"
    return self.html(render_admin_health(updated, notice=result["message"], status=status))


def admin_health_scryfall_sync(self, user):
    if not require_capability(user, CAP_MANAGE_MAINTENANCE):
        return self.not_found(user)
    form = self.read_form()
    started = start_scryfall_bulk_sync()
    notice = "Scryfall bulk data sync started." if started else "Scryfall bulk data sync is already running."
    log_admin_action(
        user["id"],
        "scryfall_bulk_sync_started",
        None,
        "scryfall",
        "Scryfall bulk data",
        notice,
        admin_request_ip(self),
        admin_user_agent(self),
    )
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],)) or user
    if form.get("redirect_to", [""])[0] == "/admin":
        return self.html(render_admin(updated, notice=notice))
    return self.html(render_admin_health(updated, notice=notice))


def admin_health_retention(self, user):
    if not require_capability(user, CAP_MANAGE_MAINTENANCE):
        return self.not_found(user)
    form = self.read_form()
    try:
        settings = set_data_retention_settings(
            form.get("notification_days", [""])[0],
            form.get("admin_log_days", [""])[0],
            form.get("webhook_days", [""])[0],
            form.get("evidence_days", [""])[0],
        )
        result = prune_data_retention_records(settings) if form.get("intent", ["save"])[0] == "save_run" else None
    except ValueError as exc:
        return self.html(render_admin_health(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    details = (
        f"Read notifications {settings['notification_days']} day(s); admin logs {settings['admin_log_days']} day(s); "
        f"webhook deliveries {settings['webhook_days']} day(s); resolved dispute evidence {settings['evidence_days']} day(s)."
    )
    log_admin_action(
        user["id"],
        "data_retention_pruned" if result else "data_retention_updated",
        None,
        "maintenance",
        "Data retention",
        (
            f"{details} Deleted {result['notifications']} notification(s), {result['admin_logs']} log(s), "
            f"{result['webhook_deliveries']} webhook delivery record(s), and {result['dispute_evidence']} evidence attachment(s)."
            if result
            else details
        ),
        admin_request_ip(self),
        admin_user_agent(self),
    )
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],)) or user
    notice = "Data retention settings saved."
    if result:
        notice += f" Cleanup removed {result['total']} eligible record{'s' if result['total'] != 1 else ''}."
    return self.html(render_admin_health(updated, notice=notice))


def admin_jobs_page(self, user, notice=None, status="info"):
    if not require_capability(user, CAP_MANAGE_MAINTENANCE):
        return self.not_found(user)
    return self.html(render_admin_jobs(user, notice=notice, status=status))


def admin_jobs_retry_response(self, user, form, notice, status="info", http_status=None):
    http_status = http_status or HTTPStatus.OK
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],)) or user
    if form.get("redirect_to", [""])[0] == "/admin/health":
        return self.html(render_admin_health(updated, notice=notice, status=status), http_status)
    return self.html(render_admin_jobs(updated, notice=notice, status=status), http_status)


def admin_disputes_page(self, user, query):
    if not require_capability(user, CAP_MODERATE_DISPUTES):
        return self.not_found(user)
    return self.html(render_admin_trade_disputes(user, query))


def admin_dispute_update(self, user, path):
    if not require_capability(user, CAP_MODERATE_DISPUTES):
        return self.not_found(user)
    parts = path.strip("/").split("/")
    try:
        dispute_id = int(parts[2])
    except (ValueError, IndexError):
        return self.not_found(user)
    form = self.read_form()
    try:
        update_trade_dispute_admin(
            dispute_id,
            user["id"],
            form.get("status", [""])[0],
            form.get("admin_note", [""])[0],
            admin_request_ip(self),
            admin_user_agent(self),
            resolution_note=form.get("resolution_note", [""])[0],
        )
    except ValueError as exc:
        return self.html(render_admin_trade_disputes(user, {}, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],)) or user
    return self.html(render_admin_trade_disputes(updated, {}, notice="Trade issue updated."))


def admin_trust_settings(self, user):
    if not require_capability(user, CAP_MANAGE_SETTINGS):
        return self.not_found(user)
    form = self.read_form()
    try:
        threshold = set_trusted_trade_threshold(form.get("trusted_trade_threshold", [""])[0])
    except ValueError as exc:
        return self.html(render_admin(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    log_admin_action(
        user["id"],
        "trusted_threshold_updated",
        None,
        "setting",
        "Trusted trades",
        f"Completed-trade threshold set to {threshold}.",
        admin_request_ip(self),
        admin_user_agent(self),
    )
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    return self.html(render_admin(updated, notice=f"Trusted trade threshold set to {threshold}."))


def admin_trade_fairness_settings(self, user):
    if not require_capability(user, CAP_MANAGE_SETTINGS):
        return self.not_found(user)
    form = self.read_form()
    try:
        settings = set_trade_fairness_settings(
            form.get("fairness_warn_percent", [""])[0],
            form.get("fairness_block_percent", [""])[0],
        )
    except ValueError as exc:
        return self.html(render_admin(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    log_admin_action(
        user["id"],
        "trade_fairness_updated",
        None,
        "setting",
        "Trade fairness",
        f"Warning threshold {settings['warn_percent']}%; block threshold {settings['block_percent']}%.",
        admin_request_ip(self),
        admin_user_agent(self),
    )
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    block_label = f"{settings['block_percent']}%" if settings["block_enabled"] else "off"
    notice = f"Trade fairness rules saved. Warning at {settings['warn_percent']}%, block at {block_label}."
    return self.html(render_admin(updated, notice=notice))


def admin_trade_policy_settings(self, user):
    if not require_capability(user, CAP_MANAGE_SETTINGS):
        return self.not_found(user)
    form = self.read_form()
    try:
        settings = set_trade_policy_settings(
            form.get("one_way_trade_policy", [""])[0],
            form.get("trusted_trade_threshold", [""])[0],
            form.get("fairness_warn_percent", [""])[0],
            form.get("fairness_block_percent", [""])[0],
            form.get("dispute_escalation_days", [""])[0],
            dispute_evidence_retention_days(),
        )
    except ValueError as exc:
        return self.html(render_admin(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    fairness = settings["fairness"]
    block_label = f"{fairness['block_percent']}%" if fairness["block_enabled"] else "off"
    details = (
        f"One-way trades: {settings['one_way_policy_label']}; trust threshold {settings['trusted_threshold']}; "
        f"fairness warning {fairness['warn_percent']}%, block {block_label}; "
        f"dispute escalation {settings['dispute_escalation_days']} day(s)."
    )
    log_admin_action(
        user["id"],
        "trade_policy_updated",
        None,
        "setting",
        "Trade policy",
        details,
        admin_request_ip(self),
        admin_user_agent(self),
    )
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    notice = "Trade policy settings saved."
    return self.html(render_admin(updated, notice=notice))


def admin_integration_policy_settings(self, user):
    if not require_capability(user, CAP_MANAGE_SETTINGS):
        return self.not_found(user)
    form = self.read_form()
    try:
        settings = set_integration_access_settings(
            form.get("api_access_policy", [""])[0],
            form.get("webhook_access_policy", [""])[0],
        )
    except ValueError as exc:
        return self.html(render_admin(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    details = (
        f"API tokens: {settings['api_policy_label']}; "
        f"webhooks: {settings['webhook_policy_label']}."
    )
    log_admin_action(
        user["id"],
        "integration_policy_updated",
        None,
        "setting",
        "Integration access",
        details,
        admin_request_ip(self),
        admin_user_agent(self),
    )
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    return self.html(render_admin(updated, notice="Integration access settings saved."))


def admin_registration_settings(self, user):
    if not require_capability(user, CAP_MANAGE_SETTINGS):
        return self.not_found(user)
    form = self.read_form()
    enabled = form.get("invite_only_registration", [""])[0] == "1"
    try:
        moderation = set_registration_moderation_settings(
            form.get("registration_approval_mode", [DEFAULT_REGISTRATION_APPROVAL_MODE])[0],
            form.get("registration_risk_threshold", [DEFAULT_REGISTRATION_RISK_THRESHOLD])[0],
        )
    except ValueError as exc:
        return self.html(render_admin(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    invite_only = set_invite_only_registration(enabled)
    log_admin_action(
        user["id"],
        "registration_mode_updated",
        None,
        "setting",
        "Registration",
        (
            ("Invite-only registration enabled. " if invite_only else "Open registration enabled. ")
            + f"Approval mode: {moderation['approval_mode_label']}; risk threshold {moderation['risk_threshold']}."
        ),
        admin_request_ip(self),
        admin_user_agent(self),
    )
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    mode = "invite-only" if enabled else "open"
    return self.html(render_admin(updated, notice=f"Registration mode set to {mode}. Approval mode: {moderation['approval_mode_label']}."))


def admin_registration_review(self, user, path):
    if not require_capability(user, CAP_MODERATE_USERS):
        return self.not_found(user)
    parts = path.strip("/").split("/")
    try:
        target_user_id = int(parts[2])
        decision = parts[3]
    except (ValueError, IndexError):
        return self.not_found(user)
    form = self.read_form()
    try:
        reviewed = admin_review_registration(
            user["id"],
            target_user_id,
            decision,
            form.get("note", [""])[0],
        )
    except ValueError as exc:
        return self.html(render_admin(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    notice = (
        f"Registration approved for {reviewed['display_name']}."
        if decision == "approve"
        else f"Registration denied for {reviewed['display_name']}."
    )
    return self.html(render_admin(updated, notice=notice, status="info" if decision == "approve" else "warning"))


def admin_invite_create(self, user):
    if not require_capability(user, CAP_MANAGE_INVITES):
        return self.not_found(user)
    form = self.read_form()
    try:
        invite_result = create_registration_invite(
            user["id"],
            form.get("email", [""])[0],
            self.public_base_url(),
        )
    except ValueError as exc:
        return self.html(render_admin(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    notice = f"Invite created for {invite_result['email']}. {invite_result['email_status']}"
    return self.html(render_admin(updated, notice=notice, invite_result=invite_result))


def admin_invite_revoke(self, user, path):
    if not require_capability(user, CAP_MANAGE_INVITES):
        return self.not_found(user)
    parts = path.strip("/").split("/")
    try:
        invite_id = int(parts[2])
    except (ValueError, IndexError):
        return self.not_found(user)
    try:
        revoke_registration_invite(user["id"], invite_id)
    except ValueError as exc:
        return self.html(render_admin(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    return self.html(render_admin(updated, notice="Invite revoked."))


def admin_backup_create(self, user):
    if not require_capability(user, CAP_MANAGE_BACKUPS):
        return self.not_found(user)
    try:
        archive_path = create_backup_archive(user["id"])
    except ValueError as exc:
        return self.html(render_admin(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    log_admin_action(
        user["id"],
        "backup_created",
        None,
        "backup",
        archive_path.name,
        "Manual backup downloaded from admin panel.",
        admin_request_ip(self),
        admin_user_agent(self),
    )
    return self.binary(archive_path.read_bytes(), "application/zip", archive_path.name)


def admin_backup_settings(self, user):
    if not require_capability(user, CAP_MANAGE_BACKUPS):
        return self.not_found(user)
    form = self.read_form()
    try:
        settings = set_automatic_backup_settings(
            form.get("automatic_backup_enabled", [""])[0] == "1",
            form.get("automatic_backup_interval_hours", [""])[0],
            form.get("automatic_backup_retention_count", [""])[0],
            form.get("automatic_backup_retention_days", [""])[0],
        )
        pruned = prune_backup_archives(settings["retention_count"], settings["retention_days"])
    except ValueError as exc:
        return self.html(render_admin(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    mode = "enabled" if settings["enabled"] else "paused"
    notice = f"Automatic backups {mode}. Retention cleanup removed {len(pruned['deleted'])} archive{'s' if len(pruned['deleted']) != 1 else ''}."
    if pruned["failed"]:
        notice += f" {len(pruned['failed'])} archive cleanup error{'s' if len(pruned['failed']) != 1 else ''}."
    log_admin_action(
        user["id"],
        "backup_settings_updated",
        None,
        "backup",
        "Automatic backups",
        f"Automatic backups {mode}; every {settings['interval_hours']} hour(s); keep {settings['retention_count']}; retention {settings['retention_days']} day(s).",
        admin_request_ip(self),
        admin_user_agent(self),
    )
    return self.html(render_admin(updated, notice=notice, status="warning" if pruned["failed"] else "info"))


def admin_backup_run(self, user):
    if not require_capability(user, CAP_MANAGE_BACKUPS):
        return self.not_found(user)
    result = run_automatic_backup_once(force=True)
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    if not result.get("success"):
        return self.html(render_admin(updated, notice=result.get("error", "Automatic backup failed."), status="error"), HTTPStatus.BAD_REQUEST)
    pruned = result.get("pruned", {})
    notice = f"Automatic backup created: {result['archive']}."
    if pruned.get("deleted"):
        notice += f" Removed {len(pruned['deleted'])} old automatic backup{'s' if len(pruned['deleted']) != 1 else ''}."
    log_admin_action(
        user["id"],
        "backup_run",
        None,
        "backup",
        result["archive"],
        f"Forced automatic backup run. Removed {len(pruned.get('deleted', []))} old archive(s).",
        admin_request_ip(self),
        admin_user_agent(self),
    )
    return self.html(render_admin(updated, notice=notice))


def admin_backup_restore(self, user):
    if not require_capability(user, CAP_MANAGE_BACKUPS):
        return self.not_found(user)
    fields, files = self.read_multipart_form()
    confirmation = fields.get("confirmation", [""])[0].strip()
    if confirmation != "RESTORE":
        return self.html(
            render_admin(user, notice='Type "RESTORE" to confirm the restore.', status="error"),
            HTTPStatus.BAD_REQUEST,
        )
    try:
        result = restore_backup_upload(files.get("backup_file"), user["id"])
    except ValueError as exc:
        return self.html(render_admin(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],)) or user
    notice = f"Backup restored. Pre-restore safety backup saved as {result['pre_restore_backup_name']}."
    return self.html(render_admin(updated, notice=notice))


def admin_job_retry_scryfall(self, user):
    if not require_capability(user, CAP_MANAGE_MAINTENANCE):
        return self.not_found(user)
    form = self.read_form()
    try:
        job = retry_scryfall_enrichment_job(form.get("job_id", [""])[0])
        start_scryfall_enrichment_worker()
    except ValueError as exc:
        return admin_jobs_retry_response(self, user, form, str(exc), status="error", http_status=HTTPStatus.BAD_REQUEST)
    log_admin_action(
        user["id"],
        "scryfall_job_retried",
        job.get("user_id"),
        "scryfall_job",
        f"Job #{job['id']}",
        f"Retried Scryfall enrichment for {job.get('card_name', 'card')}.",
        admin_request_ip(self),
        admin_user_agent(self),
    )
    return admin_jobs_retry_response(self, user, form, f"Scryfall enrichment job #{job['id']} queued for retry.")


def admin_job_retry_price(self, user):
    if not require_capability(user, CAP_MANAGE_MAINTENANCE):
        return self.not_found(user)
    form = self.read_form()
    try:
        job = retry_price_refresh_job(form.get("job_id", [""])[0])
        start_price_refresh_worker()
    except ValueError as exc:
        return admin_jobs_retry_response(self, user, form, str(exc), status="error", http_status=HTTPStatus.BAD_REQUEST)
    log_admin_action(
        user["id"],
        "price_job_retried",
        job.get("user_id"),
        "price_job",
        f"Job #{job['id']}",
        f"Retried {job.get('provider', 'price')} refresh for collection item #{job.get('collection_item_id', '')}.",
        admin_request_ip(self),
        admin_user_agent(self),
    )
    return admin_jobs_retry_response(self, user, form, f"Price refresh job #{job['id']} queued for retry.")


def admin_job_retry_scryfall_prices(self, user):
    if not require_capability(user, CAP_MANAGE_MAINTENANCE):
        return self.not_found(user)
    result = retry_scryfall_price_refresh_async()
    log_admin_action(
        user["id"],
        "scryfall_price_retry",
        None,
        "price_refresh",
        "Scryfall prices",
        result["message"],
        admin_request_ip(self),
        admin_user_agent(self),
    )
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],)) or user
    return self.html(render_admin_jobs(updated, notice=result["message"], status="info" if result["started"] else "warning"))


def admin_job_retry_notification(self, user):
    if not require_capability(user, CAP_MANAGE_MAINTENANCE):
        return self.not_found(user)
    form = self.read_form()
    try:
        notification = retry_failed_notification_email(form.get("notification_id", [""])[0])
    except ValueError as exc:
        return admin_jobs_retry_response(self, user, form, str(exc), status="error", http_status=HTTPStatus.BAD_REQUEST)
    result = send_pending_trade_notification_emails(user_id=notification["user_id"], limit=5)
    log_admin_action(
        user["id"],
        "notification_email_retried",
        notification.get("user_id"),
        "notification",
        f"Notification #{notification['id']}",
        f"Retried failed notification email. Sent {result['sent']}, failed {result['failed']}, skipped {result['skipped']}.",
        admin_request_ip(self),
        admin_user_agent(self),
    )
    notice = f"Notification email retry processed. Sent {result['sent']}, failed {result['failed']}, skipped {result['skipped']}."
    return admin_jobs_retry_response(self, user, form, notice, status="warning" if result["failed"] else "info")


def admin_job_retry_background(self, user):
    if not require_capability(user, CAP_MANAGE_MAINTENANCE):
        return self.not_found(user)
    form = self.read_form()
    try:
        job = retry_background_job(form.get("job_id", [""])[0])
    except ValueError as exc:
        return admin_jobs_retry_response(self, user, form, str(exc), status="error", http_status=HTTPStatus.BAD_REQUEST)
    log_admin_action(
        user["id"],
        "background_job_retried",
        None,
        "background_job",
        f"Job #{job['id']}",
        f"Retried {job.get('job_type', 'background job')}.",
        admin_request_ip(self),
        admin_user_agent(self),
    )
    return admin_jobs_retry_response(self, user, form, f"Background job #{job['id']} queued for retry.")


def admin_job_cancel_background(self, user):
    if not require_capability(user, CAP_MANAGE_MAINTENANCE):
        return self.not_found(user)
    form = self.read_form()
    try:
        job = cancel_background_job(form.get("job_id", [""])[0])
    except ValueError as exc:
        return admin_jobs_retry_response(self, user, form, str(exc), status="error", http_status=HTTPStatus.BAD_REQUEST)
    log_admin_action(
        user["id"],
        "background_job_cancelled",
        None,
        "background_job",
        f"Job #{job['id']}",
        f"Cancelled {job.get('job_type', 'background job')}.",
        admin_request_ip(self),
        admin_user_agent(self),
    )
    return admin_jobs_retry_response(self, user, form, f"Background job #{job['id']} cancelled.", status="warning")


def admin_job_undo_import(self, user, path):
    if not require_capability(user, CAP_MANAGE_MAINTENANCE):
        return self.not_found(user)
    parts = path.strip("/").split("/")
    try:
        batch_id = int(parts[3])
    except (ValueError, IndexError):
        return self.not_found(user)
    try:
        result = admin_undo_import_batch(batch_id)
    except ValueError as exc:
        return self.html(render_admin_jobs(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    target_user_id = row("SELECT user_id FROM import_batches WHERE id = ?", (batch_id,))
    log_admin_action(
        user["id"],
        "import_batch_undone",
        target_user_id["user_id"] if target_user_id else None,
        "import_batch",
        f"Batch #{batch_id}",
        f"Admin undo reverted {result['undone_items']} imported item(s).",
        admin_request_ip(self),
        admin_user_agent(self),
    )
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],)) or user
    return self.html(render_admin_jobs(updated, notice=f"Import batch #{batch_id} undone. Reverted {result['undone_items']} item(s)."))


def admin_user_action(self, user, path):
    if not require_capability(user, CAP_ACCESS_ADMIN):
        return self.not_found(user)
    parts = path.strip("/").split("/")
    if len(parts) != 4:
        return self.not_found(user)
    try:
        target_user_id = int(parts[2])
    except ValueError:
        return self.not_found(user)
    action = parts[3]
    form = self.read_form()
    recovery_result = None
    try:
        if action == "ban":
            if not require_capability(user, CAP_MODERATE_USERS):
                return self.not_found(user)
            requested = form.get("action", ["ban"])[0]
            if requested == "unban":
                admin_set_user_ban(user["id"], target_user_id, False)
                notice = "User unbanned."
            else:
                admin_set_user_ban(user["id"], target_user_id, True, form.get("reason", [""])[0])
                notice = "User banned and active sessions cleared."
        elif action == "password":
            if not require_capability(user, CAP_MANAGE_USERS):
                return self.not_found(user)
            self.enforce_rate_limit(
                "password_reset",
                self.rate_limit_key("admin-password-reset", str(user["id"])),
                "Too many administrator password recovery attempts. Try again shortly.",
            )
            recovery_result = admin_issue_user_password_recovery(
                user["id"],
                target_user_id,
                form.get("current_password", [""])[0],
                self.public_base_url(),
            )
            notice = (
                "Password recovery link emailed and active sessions cleared."
                if recovery_result["sent"]
                else "Manual password recovery link created and active sessions cleared."
            )
        elif action == "2fa":
            if not require_capability(user, CAP_MANAGE_USERS):
                return self.not_found(user)
            admin_reset_user_two_factor(user["id"], target_user_id)
            notice = "Two-factor authentication reset and active sessions cleared."
        elif action == "role":
            if not require_capability(user, CAP_MANAGE_ROLES):
                return self.not_found(user)
            requested = form.get("role", [""])[0]
            if not requested:
                legacy_action = form.get("action", [""])[0]
                requested = ROLE_ADMIN if legacy_action == "make_admin" else ROLE_MEMBER if legacy_action == "remove_admin" else ""
            admin_set_user_role(user["id"], target_user_id, requested)
            notice = f"Role changed to {role_label(requested)}."
        elif action == "notes":
            if not require_capability(user, CAP_MODERATE_USERS):
                return self.not_found(user)
            admin_update_notes(target_user_id, form.get("admin_notes", [""])[0], user["id"])
            notice = "Admin notes saved."
        elif action == "trust":
            if not require_capability(user, CAP_MODERATE_USERS):
                return self.not_found(user)
            requested = form.get("action", [""])[0]
            admin_set_user_trust(target_user_id, requested, user["id"])
            if requested == "trust":
                notice = "Trusted status granted."
            elif requested == "revoke":
                notice = "Trusted status revoked."
            else:
                notice = "Trusted status reset to automatic earning."
        else:
            return self.not_found(user)
    except ValueError as exc:
        return self.html(render_admin(user, notice=str(exc), status="error"), HTTPStatus.BAD_REQUEST)
    updated = row("SELECT * FROM users WHERE id = ?", (user["id"],))
    return self.html(render_admin(updated, notice=notice, recovery_result=recovery_result))


__all__ = [
    "admin_request_ip",
    "admin_user_agent",
    "admin_page",
    "admin_logs_page",
    "admin_health_page",
    "admin_collection_health_page",
    "admin_database_page",
    "admin_database_maintenance_action",
    "admin_database_analyze",
    "admin_database_vacuum",
    "admin_database_snapshot",
    "admin_health_retry_jobs",
    "admin_health_replay_notifications",
    "admin_health_check_backups",
    "admin_health_scryfall_sync",
    "admin_health_retention",
    "admin_jobs_page",
    "admin_jobs_retry_response",
    "admin_disputes_page",
    "admin_dispute_update",
    "admin_trust_settings",
    "admin_trade_fairness_settings",
    "admin_trade_policy_settings",
    "admin_integration_policy_settings",
    "admin_registration_settings",
    "admin_registration_review",
    "admin_invite_create",
    "admin_invite_revoke",
    "admin_backup_create",
    "admin_backup_settings",
    "admin_backup_run",
    "admin_backup_restore",
    "admin_job_retry_scryfall",
    "admin_job_retry_price",
    "admin_job_retry_scryfall_prices",
    "admin_job_retry_notification",
    "admin_job_retry_background",
    "admin_job_cancel_background",
    "admin_job_undo_import",
    "admin_user_action",
]
