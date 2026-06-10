"""Layout, authentication, and account views.

This module is wired by binderbridge.views; shared app helpers are injected at runtime.
"""

def render_subnav(items, active_key, label="Section"):
    links = "".join(
        f'<a class="section-tab {"active" if key == active_key else ""}" href="{href}">{e(text)}</a>'
        for key, href, text in items
    )
    return f'<nav class="section-tabs" aria-label="{e(label)}">{links}</nav>'


def render_cards_subnav(active_key):
    return render_subnav(
        [
            ("collection", "/collection", "Collection"),
            ("stats", "/collection/stats", "Stats"),
            ("groups", "/groups", "Decks & Binders"),
            ("import", "/import", "Import"),
        ],
        active_key,
        label="My Cards",
    )


def render_wishlist_subnav(active_key):
    return render_subnav(
        [
            ("wants", "/wants", "Wanted Cards"),
            ("groups", "/groups?type=wishlist", "Wishlist Groups"),
        ],
        active_key,
        label="Wishlist",
    )


def group_listing_url(group):
    return "/groups?type=wishlist" if group and group["group_type"] == "wishlist" else "/groups"


def render_layout(user, title, content, active="dashboard", notice=None, status="info"):
    auth_links = ""
    if user:
        unread_count = unread_notification_count(user["id"])
        dashboard_label = "Dashboard"
        if unread_count:
            dashboard_label += f'<span class="nav-badge">{e(unread_count)}</span>'
        trade_unread_count = unread_trade_notification_count(user["id"])
        trades_label = "Trades"
        if trade_unread_count:
            trades_label += f'<span class="nav-badge trade-nav-badge">{e(trade_unread_count)}</span>'
        nav_items = [
            ("dashboard", "/", dashboard_label),
            ("cards", "/collection", "My Cards"),
            ("wants", "/wants", "Wishlist"),
            ("browse", "/browse", "Browse"),
            ("trades", "/trades", trades_label),
            ("account", "/account", "Account"),
        ]
        if user_has_capability(user, CAP_ACCESS_ADMIN):
            staff_label = "Admin" if user_role(user) in (ROLE_OWNER, ROLE_ADMIN) else "Staff"
            nav_items.append(("admin", "/admin", staff_label))
        nav = "".join(
            f'<a class="nav-link {"active" if active == key else ""}" href="{href}">{label}</a>'
            for key, href, label in nav_items
        )
        auth_links = f"""
            <nav class="app-nav" aria-label="Primary">{nav}</nav>
            <button class="theme-toggle" id="theme-toggle" type="button" aria-pressed="false" aria-label="Switch to light mode">
                <span class="theme-toggle-track" aria-hidden="true"><span class="theme-toggle-knob"></span></span>
                <span class="theme-toggle-label">Light mode</span>
            </button>
            <div class="user-chip">
                <a href="/account">{e(user["display_name"])}</a>
                <span class="pill">{e(role_label(user))}</span>
                <form method="post" action="/logout"><button class="button ghost small" type="submit">Sign out</button></form>
            </div>
        """
    else:
        auth_links = """
            <nav class="app-nav" aria-label="Primary">
                <a class="nav-link active" href="/login">Sign in</a>
                <a class="nav-link" href="/register">Create account</a>
            </nav>
            <button class="theme-toggle" id="theme-toggle" type="button" aria-pressed="false" aria-label="Switch to light mode">
                <span class="theme-toggle-track" aria-hidden="true"><span class="theme-toggle-knob"></span></span>
                <span class="theme-toggle-label">Light mode</span>
            </button>
        """

    flash = f'<div class="notice {status}">{e(notice)}</div>' if notice else ""
    body_class = "auth-page" if not user else "app-page"
    return f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{e(title)} - {APP_NAME}</title>
    <script>
        (function () {{
            try {{
                if (localStorage.getItem("binderbridge_theme") === "light") {{
                    document.documentElement.dataset.theme = "light";
                }}
            }} catch (error) {{}}
        }})();
    </script>
    <link rel="stylesheet" href="/static/style.css">
</head>
<body class="{body_class}">
    <header class="topbar">
        <a class="brand" href="/">
            <span class="brand-mark">BB</span>
            <span>{APP_NAME}</span>
        </a>
        {auth_links}
    </header>
    <main class="page-shell">
        {flash}
        {content}
    </main>
    <script>
        (function () {{
            var button = document.getElementById("theme-toggle");
            if (!button) return;
            var label = button.querySelector(".theme-toggle-label");

            function currentTheme() {{
                return document.documentElement.dataset.theme === "light" ? "light" : "dark";
            }}

            function applyTheme(theme) {{
                var light = theme === "light";
                if (light) {{
                    document.documentElement.dataset.theme = "light";
                }} else {{
                    document.documentElement.removeAttribute("data-theme");
                }}
                button.setAttribute("aria-pressed", light ? "true" : "false");
                button.setAttribute("aria-label", light ? "Switch to dark mode" : "Switch to light mode");
                if (label) {{
                    label.textContent = light ? "Dark mode" : "Light mode";
                }}
                try {{
                    localStorage.setItem("binderbridge_theme", theme);
                }} catch (error) {{}}
            }}

            applyTheme(currentTheme());
            button.addEventListener("click", function () {{
                applyTheme(currentTheme() === "light" ? "dark" : "light");
            }});
        }})();
    </script>
</body>
</html>"""


def render_login(user=None, notice=None, status="info"):
    content = """
    <section class="auth-grid">
        <div class="auth-copy">
            <p class="eyebrow">Self-hosted collection trading</p>
            <h1>Trade from binders people can actually browse.</h1>
            <p class="lead">Track collections, publish trade stock, keep want lists, and negotiate trades with other users on your own server.</p>
        </div>
        <form class="panel auth-panel" method="post" action="/login">
            <h2>Sign in</h2>
            <label>Username
                <input required name="username" autocomplete="username">
            </label>
            <label>Password
                <input required name="password" type="password" autocomplete="current-password">
            </label>
            <button class="button primary full" type="submit">Sign in</button>
            <div class="auth-divider"><span>or</span></div>
            <button class="button secondary full" type="button" id="passkey-login-button">Sign in with passkey</button>
            <p class="muted compact" id="passkey-login-status"></p>
            <p class="muted compact">New here? <a href="/register">Create an account</a>.</p>
        </form>
    </section>
    <script>
        (function () {
            var button = document.getElementById("passkey-login-button");
            var status = document.getElementById("passkey-login-status");
            if (!button) return;

            function setStatus(message) {
                if (status) status.textContent = message || "";
            }

            function b64ToBuffer(value) {
                var text = String(value || "").replace(/-/g, "+").replace(/_/g, "/");
                while (text.length % 4) text += "=";
                var binary = atob(text);
                var bytes = new Uint8Array(binary.length);
                for (var i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
                return bytes.buffer;
            }

            function bufferToB64(buffer) {
                var bytes = new Uint8Array(buffer || []);
                var binary = "";
                for (var i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
                return btoa(binary).replace(/\\+/g, "-").replace(/\\//g, "_").replace(/=+$/g, "");
            }

            function assertionPayload(credential) {
                return {
                    id: credential.id,
                    rawId: bufferToB64(credential.rawId),
                    type: credential.type,
                    response: {
                        clientDataJSON: bufferToB64(credential.response.clientDataJSON),
                        authenticatorData: bufferToB64(credential.response.authenticatorData),
                        signature: bufferToB64(credential.response.signature),
                        userHandle: credential.response.userHandle ? bufferToB64(credential.response.userHandle) : ""
                    }
                };
            }

            button.addEventListener("click", async function () {
                try {
                    if (!window.PublicKeyCredential || !navigator.credentials) {
                        setStatus("This browser does not support passkeys.");
                        return;
                    }
                    var usernameInput = document.querySelector('input[name="username"]');
                    var username = usernameInput ? usernameInput.value.trim() : "";
                    if (!username) {
                        setStatus("Enter your username first.");
                        if (usernameInput) usernameInput.focus();
                        return;
                    }
                    setStatus("Requesting passkey...");
                    var optionsResponse = await fetch("/login/passkey/options?username=" + encodeURIComponent(username), { credentials: "same-origin" });
                    var options = await optionsResponse.json();
                    if (!optionsResponse.ok) throw new Error(options.error || "Passkey sign-in could not start.");
                    options.publicKey.challenge = b64ToBuffer(options.publicKey.challenge);
                    (options.publicKey.allowCredentials || []).forEach(function (item) {
                        item.id = b64ToBuffer(item.id);
                    });
                    var credential = await navigator.credentials.get({ publicKey: options.publicKey });
                    var form = new URLSearchParams();
                    form.set("token", options.token);
                    form.set("credential", JSON.stringify(assertionPayload(credential)));
                    var resultResponse = await fetch("/login/passkey", {
                        method: "POST",
                        credentials: "same-origin",
                        headers: { "Content-Type": "application/x-www-form-urlencoded" },
                        body: form.toString()
                    });
                    var result = await resultResponse.json();
                    if (!resultResponse.ok) throw new Error(result.error || "Passkey sign-in failed.");
                    window.location.href = result.redirect || "/";
                } catch (error) {
                    setStatus(error && error.message ? error.message : "Passkey sign-in failed.");
                }
            });
        })();
    </script>
    """
    return render_layout(user, "Sign in", content, active="login", notice=notice, status=status)


def render_two_factor_login(challenge_token, notice=None, status="info"):
    content = f"""
    <section class="auth-grid">
        <div class="auth-copy">
            <p class="eyebrow">Two-factor authentication</p>
            <h1>Enter your authenticator code.</h1>
            <p class="lead">Use the six-digit code from your authenticator app, or one of your saved recovery codes.</p>
        </div>
        <form class="panel auth-panel" method="post" action="/login/2fa">
            <input type="hidden" name="challenge_token" value="{e(challenge_token)}">
            <h2>Security check</h2>
            <label>Authenticator or recovery code
                <input required name="two_factor_code" inputmode="numeric" autocomplete="one-time-code" autofocus>
            </label>
            <button class="button primary full" type="submit">Verify and sign in</button>
            <p class="muted compact"><a href="/login">Back to password sign in</a></p>
        </form>
    </section>
    """
    return render_layout(None, "Two-factor authentication", content, active="login", notice=notice, status=status)


def render_register(user=None, notice=None, status="info", invite_token="", invite=None, invite_required=False):
    invite_email = row_value(invite, "email", "") if invite else ""
    invite_hidden = f'<input type="hidden" name="invite_token" value="{e(invite_token)}">' if invite_token else ""
    email_field = (
        f"""
            <label>Email
                <input required readonly name="email" type="email" maxlength="254" value="{e(invite_email)}">
            </label>
        """
        if invite_email
        else """
            <label>Email
                <input name="email" type="email" maxlength="254" autocomplete="email">
            </label>
        """
    )
    invite_note = f'<p class="muted compact">Invite accepted for {e(invite_email)}.</p>' if invite_email else ""
    if invite_required and not invite:
        form_panel = """
        <article class="panel auth-panel">
            <h2>Invite required</h2>
            <p class="muted">Registration is currently invite-only. Use the invite link from an administrator to create an account.</p>
            <a class="button primary full" href="/login">Sign in</a>
        </article>
        """
    else:
        form_panel = f"""
        <form class="panel auth-panel" method="post" action="/register">
            {invite_hidden}
            <h2>Create account</h2>
            {invite_note}
            <label>Display name
                <input required name="display_name" maxlength="80" autocomplete="name">
            </label>
            <label>Username
                <input required name="username" maxlength="40" pattern="[A-Za-z0-9_\\-]{{3,40}}" autocomplete="username">
            </label>
            {email_field}
            <label>Password
                <input required name="password" type="password" minlength="8" autocomplete="new-password">
            </label>
            <button class="button primary full" type="submit">Create account</button>
            <p class="muted compact">Already have one? <a href="/login">Sign in</a>.</p>
        </form>
        """
    content = f"""
    <section class="auth-grid">
        <div class="auth-copy">
            <p class="eyebrow">BinderBridge</p>
            <h1>Start a shared trade community.</h1>
            <p class="lead">The MVP is tuned for Magic: The Gathering and leaves the door open for other card games later.</p>
        </div>
        {form_panel}
    </section>
    """
    return render_layout(user, "Create account", content, active="register", notice=notice, status=status)


def render_recovery_code_panel(recovery_codes):
    if not recovery_codes:
        return ""
    code_rows = "".join(f"<li><code>{e(code)}</code></li>" for code in recovery_codes)
    return f"""
    <div class="recovery-code-panel span-2">
        <strong>Save these recovery codes now.</strong>
        <p class="muted compact">Each code works once if you lose access to your authenticator app. They will not be shown again.</p>
        <ul class="recovery-code-list">{code_rows}</ul>
    </div>
    """


def render_two_factor_account_panel(user, recovery_codes=None):
    enabled = two_factor_enabled(user)
    setup = user_totp_setup_details(user)
    status_label = "Enabled" if enabled else "Setup started" if setup else "Off"
    status_class = "accepted" if enabled else "pending" if setup else "declined"
    recovery_panel = render_recovery_code_panel(recovery_codes)
    if enabled:
        enabled_at = row_value(user, "totp_enabled_at", "")
        enabled_detail = f"Enabled {e(enabled_at[:10])}" if enabled_at else "Enabled for this account"
        controls = f"""
            <p class="muted compact span-2">{enabled_detail}. You will need an authenticator code or recovery code after entering your password.</p>
            {recovery_panel}
            <form class="embedded-security-form span-2" method="post" action="/account/2fa/recovery-codes">
                <label>Current password
                    <input required name="current_password" type="password" autocomplete="current-password">
                </label>
                <button class="button secondary" type="submit">Generate new recovery codes</button>
            </form>
            <form class="embedded-security-form span-2" method="post" action="/account/2fa/disable">
                <label>Current password
                    <input required name="current_password" type="password" autocomplete="current-password">
                </label>
                <button class="button danger" type="submit" onclick="return confirm('Turn off two-factor authentication for this account?')">Disable 2FA</button>
            </form>
        """
    elif setup:
        controls = f"""
            <p class="muted compact span-2">Add this account to your authenticator app, then enter the six-digit code it shows.</p>
            <div class="totp-qr-panel span-2">
                {setup["qr_svg"]}
                <div>
                    <strong>Scan with your authenticator app</strong>
                    <p class="muted compact">Use the QR code first. If your app cannot scan it, use the manual setup key below.</p>
                </div>
            </div>
            <label class="span-2">Manual setup key
                <input readonly value="{e(setup["formatted_secret"])}" onclick="this.select()">
            </label>
            <label class="span-2">Authenticator URI
                <input readonly value="{e(setup["otpauth_uri"])}" onclick="this.select()">
            </label>
            <form class="embedded-security-form span-2" method="post" action="/account/2fa/enable">
                <label>Authenticator code
                    <input required name="two_factor_code" inputmode="numeric" autocomplete="one-time-code">
                </label>
                <label>Current password
                    <input required name="current_password" type="password" autocomplete="current-password">
                </label>
                <button class="button primary" type="submit">Enable 2FA</button>
            </form>
            <form class="embedded-security-form span-2" method="post" action="/account/2fa/start">
                <label>Current password
                    <input required name="current_password" type="password" autocomplete="current-password">
                </label>
                <button class="button secondary" type="submit">Generate a new setup key</button>
            </form>
        """
    else:
        controls = """
            <p class="muted compact span-2">Protect your login with a six-digit code from an authenticator app. Recovery codes are generated after setup.</p>
            <form class="embedded-security-form span-2" method="post" action="/account/2fa/start">
                <label>Current password
                    <input required name="current_password" type="password" autocomplete="current-password">
                </label>
                <button class="button primary" type="submit">Start 2FA setup</button>
            </form>
        """
    return f"""
        <article class="panel form-grid compact-form security-panel">
            <div class="span-2 panel-heading">
                <h2>Two-factor authentication</h2>
                <span class="status {status_class}">{e(status_label)}</span>
            </div>
            {controls}
        </article>
    """


def render_passkey_account_panel(user):
    credentials = passkey_existing_credentials(user["id"])

    def time_label(value):
        text = str(value or "").strip()
        return text[:16].replace("T", " ") if text else "Never"

    credential_rows = "".join(
        f"""
        <li class="passkey-row">
            <div>
                <strong>{e(row_value(credential, "nickname", "") or "Passkey")}</strong>
                <span class="subtle">Added {e(time_label(row_value(credential, "created_at", "")))} - Last used {e(time_label(row_value(credential, "last_used_at", "")))}</span>
            </div>
            <form class="inline-admin-form" method="post" action="/account/passkeys/{credential["id"]}/delete">
                <input required name="current_password" type="password" autocomplete="current-password" placeholder="Current password">
                <button class="button danger small" type="submit" onclick="return confirm('Remove this passkey from your account?')">Remove</button>
            </form>
        </li>
        """
        for credential in credentials
    ) or '<li class="muted">No passkeys registered yet.</li>'
    setup_status = f"{len(credentials)} registered" if credentials else "Not set up"
    setup_class = "accepted" if credentials else "declined"
    script = """
    <script>
        (function () {
            var form = document.getElementById("passkey-register-form");
            var button = document.getElementById("passkey-register-button");
            var status = document.getElementById("passkey-register-status");
            if (!form || !button) return;

            function setStatus(message) {
                if (status) status.textContent = message || "";
            }

            function b64ToBuffer(value) {
                var text = String(value || "").replace(/-/g, "+").replace(/_/g, "/");
                while (text.length % 4) text += "=";
                var binary = atob(text);
                var bytes = new Uint8Array(binary.length);
                for (var i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
                return bytes.buffer;
            }

            function bufferToB64(buffer) {
                var bytes = new Uint8Array(buffer || []);
                var binary = "";
                for (var i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
                return btoa(binary).replace(/\\+/g, "-").replace(/\\//g, "_").replace(/=+$/g, "");
            }

            function credentialPayload(credential) {
                var transports = [];
                if (credential.response && typeof credential.response.getTransports === "function") {
                    transports = credential.response.getTransports();
                }
                return {
                    id: credential.id,
                    rawId: bufferToB64(credential.rawId),
                    type: credential.type,
                    transports: transports,
                    response: {
                        clientDataJSON: bufferToB64(credential.response.clientDataJSON),
                        attestationObject: bufferToB64(credential.response.attestationObject)
                    }
                };
            }

            button.addEventListener("click", async function () {
                try {
                    if (!window.PublicKeyCredential || !navigator.credentials) {
                        setStatus("This browser does not support passkeys.");
                        return;
                    }
                    var currentPassword = form.querySelector('input[name="current_password"]');
                    if (!currentPassword || !currentPassword.value) {
                        setStatus("Enter your current password first.");
                        if (currentPassword) currentPassword.focus();
                        return;
                    }
                    setStatus("Creating passkey...");
                    var optionsResponse = await fetch("/account/passkeys/register/options", { credentials: "same-origin" });
                    var options = await optionsResponse.json();
                    if (!optionsResponse.ok) throw new Error(options.error || "Passkey setup could not start.");
                    options.publicKey.challenge = b64ToBuffer(options.publicKey.challenge);
                    options.publicKey.user.id = b64ToBuffer(options.publicKey.user.id);
                    (options.publicKey.excludeCredentials || []).forEach(function (item) {
                        item.id = b64ToBuffer(item.id);
                    });
                    var credential = await navigator.credentials.create({ publicKey: options.publicKey });
                    var body = new URLSearchParams();
                    var csrf = form.querySelector('input[name="_csrf_token"]');
                    if (csrf) body.set("_csrf_token", csrf.value);
                    body.set("token", options.token);
                    body.set("nickname", (form.querySelector('input[name="nickname"]') || {}).value || "");
                    body.set("current_password", currentPassword.value);
                    body.set("credential", JSON.stringify(credentialPayload(credential)));
                    var resultResponse = await fetch("/account/passkeys/register", {
                        method: "POST",
                        credentials: "same-origin",
                        headers: { "Content-Type": "application/x-www-form-urlencoded" },
                        body: body.toString()
                    });
                    var result = await resultResponse.json();
                    if (!resultResponse.ok) throw new Error(result.error || "Passkey setup failed.");
                    setStatus("Passkey added.");
                    window.location.reload();
                } catch (error) {
                    setStatus(error && error.message ? error.message : "Passkey setup failed.");
                }
            });
        })();
    </script>
    """
    return f"""
        <article class="panel form-grid compact-form security-panel passkey-panel">
            <div class="span-2 panel-heading">
                <h2>Passkeys</h2>
                <span class="status {setup_class}">{e(setup_status)}</span>
            </div>
            <p class="muted compact span-2">Use a device passkey for passwordless sign-in. Passkeys require browser support and user verification such as a PIN, fingerprint, or face unlock.</p>
            <form class="embedded-security-form span-2" method="post" action="/account/passkeys/register" id="passkey-register-form">
                <label>Passkey name
                    <input name="nickname" maxlength="80" placeholder="Laptop, phone, or security key">
                </label>
                <label>Current password
                    <input required name="current_password" type="password" autocomplete="current-password">
                </label>
                <button class="button primary" type="button" id="passkey-register-button">Add passkey</button>
            </form>
            <p class="muted compact span-2" id="passkey-register-status"></p>
            <div class="span-2 panel-heading with-gap">
                <h2>Registered passkeys</h2>
            </div>
            <ul class="stack-list compact-stack span-2 passkey-list">{credential_rows}</ul>
        </article>
        {script}
    """


def render_account(user, notice=None, status="info", recovery_codes=None):
    public_email_checked = checked(bool(user["public_email"]))
    price_alert_checked = checked(bool(row_value(user, "price_alerts_enabled", 1)))
    price_alert_threshold = row_value(user, "price_alert_threshold_percent", "0") or "0"
    watchlist_alert_checked = checked(bool(row_value(user, "watchlist_alerts_enabled", 1)))
    notify_trade_offer_checked = checked(bool(row_value(user, "notify_trade_offer_enabled", 1)))
    notify_trade_comment_checked = checked(bool(row_value(user, "notify_trade_comment_enabled", 1)))
    notify_trade_counter_checked = checked(bool(row_value(user, "notify_trade_counter_enabled", 1)))
    notify_trade_status_checked = checked(bool(row_value(user, "notify_trade_status_enabled", 1)))
    notify_import_complete_checked = checked(bool(row_value(user, "notify_import_complete_enabled", 1)))
    notify_admin_notice_checked = checked(bool(row_value(user, "notify_admin_notice_enabled", 1)))
    stale_trade_reminder_days = row_value(user, "stale_trade_reminder_days", 3)
    trade_email_controls = ""
    if email_delivery_configured():
        trade_email_checked = checked(bool(row_value(user, "email_trade_notifications_enabled", 0)))
        trade_offer_email_checked = checked(bool(row_value(user, "email_trade_offer_enabled", 1)))
        trade_comment_email_checked = checked(bool(row_value(user, "email_trade_comment_enabled", 1)))
        trade_counter_email_checked = checked(bool(row_value(user, "email_trade_counter_enabled", 1)))
        trade_status_email_checked = checked(bool(row_value(user, "email_trade_status_enabled", 1)))
        price_alert_email_checked = checked(bool(row_value(user, "email_price_alert_enabled", 0)))
        import_complete_email_checked = checked(bool(row_value(user, "email_import_complete_enabled", 0)))
        admin_notice_email_checked = checked(bool(row_value(user, "email_admin_notice_enabled", 0)))
        email_digest_frequency = row_value(user, "email_digest_frequency", "immediate") or "immediate"
        email_digest_time = row_value(user, "email_digest_time", "09:00") or "09:00"
        email_digest_weekday = int(row_value(user, "email_digest_weekday", 0) or 0)
        notification_timezone = row_value(user, "notification_timezone", "UTC") or "UTC"
        quiet_hours_checked = checked(bool(row_value(user, "quiet_hours_enabled", 0)))
        quiet_hours_start = row_value(user, "quiet_hours_start", "22:00") or "22:00"
        quiet_hours_end = row_value(user, "quiet_hours_end", "07:00") or "07:00"
        digest_frequency_options = "".join(
            f'<option value="{e(value)}"{" selected" if value == email_digest_frequency else ""}>{e(label)}</option>'
            for value, label in EMAIL_DIGEST_FREQUENCY_LABELS.items()
        )
        digest_weekday_options = "".join(
            f'<option value="{index}"{" selected" if index == email_digest_weekday else ""}>{e(label)}</option>'
            for index, label in enumerate(EMAIL_DIGEST_WEEKDAY_LABELS)
        )
        trade_email_controls = f"""
            <div class="span-2 panel-heading with-gap">
                <h2>Email notifications</h2>
                <span class="pill">SMTP configured</span>
            </div>
            <label class="checkbox-line span-2">
                <input type="checkbox" name="email_trade_notifications_enabled" value="1"{trade_email_checked}>
                Email unread notifications for enabled categories to my account email
            </label>
            <fieldset class="preference-checks span-2">
                <legend>Email me about</legend>
                <div class="preference-option-grid">
                    <label class="checkbox-line preference-option">
                        <input type="checkbox" name="email_trade_offer_enabled" value="1"{trade_offer_email_checked}>
                        Trade offers
                    </label>
                    <label class="checkbox-line preference-option">
                        <input type="checkbox" name="email_trade_comment_enabled" value="1"{trade_comment_email_checked}>
                        Comments
                    </label>
                    <label class="checkbox-line preference-option">
                        <input type="checkbox" name="email_trade_counter_enabled" value="1"{trade_counter_email_checked}>
                        Counter offers
                    </label>
                    <label class="checkbox-line preference-option">
                        <input type="checkbox" name="email_trade_status_enabled" value="1"{trade_status_email_checked}>
                        Status changes
                    </label>
                    <label class="checkbox-line preference-option">
                        <input type="checkbox" name="email_price_alert_enabled" value="1"{price_alert_email_checked}>
                        Price alerts
                    </label>
                    <label class="checkbox-line preference-option">
                        <input type="checkbox" name="email_import_complete_enabled" value="1"{import_complete_email_checked}>
                        Import completion
                    </label>
                    <label class="checkbox-line preference-option">
                        <input type="checkbox" name="email_admin_notice_enabled" value="1"{admin_notice_email_checked}>
                        Admin notices
                    </label>
                </div>
            </fieldset>
            <div class="span-2 panel-heading with-gap">
                <h2>Delivery schedule</h2>
            </div>
            <label>Email delivery
                <select name="email_digest_frequency">{digest_frequency_options}</select>
            </label>
            <label>Digest delivery time
                <input name="email_digest_time" type="time" value="{e(email_digest_time)}">
            </label>
            <label>Weekly digest day
                <select name="email_digest_weekday">{digest_weekday_options}</select>
            </label>
            <label>Timezone
                <input name="notification_timezone" maxlength="80" list="notification-timezones" value="{e(notification_timezone)}" placeholder="America/Chicago">
                <datalist id="notification-timezones">
                    <option value="UTC"><option value="America/New_York"><option value="America/Chicago"><option value="America/Denver"><option value="America/Los_Angeles">
                </datalist>
            </label>
            <label class="checkbox-line span-2">
                <input type="checkbox" name="quiet_hours_enabled" value="1"{quiet_hours_checked}>
                Defer notification emails during quiet hours
            </label>
            <label>Quiet hours start
                <input name="quiet_hours_start" type="time" value="{e(quiet_hours_start)}">
            </label>
            <label>Quiet hours end
                <input name="quiet_hours_end" type="time" value="{e(quiet_hours_end)}">
            </label>
            <p class="muted compact span-2">In-app alerts appear immediately. Quiet hours and digests only change email delivery, and unread notifications remain queued until their scheduled delivery window.</p>
        """
    role_notice = (
        '<div class="notice warning">This is a read-only account. You can browse the site and manage account security, but cannot change cards, wishlists, groups, trades, or integrations.</div>'
        if user_role(user) == ROLE_READ_ONLY
        else ""
    )
    integration_panel = "" if user_role(user) == ROLE_READ_ONLY else render_api_access_panel(user)
    content = f"""
    <section class="section-heading">
        <div>
            <p class="eyebrow">Account</p>
            <h1>Control panel</h1>
            <p class="muted compact">Role: <strong>{e(role_label(user))}</strong></p>
        </div>
    </section>
    {role_notice}
    <section class="content-grid account-grid">
        <form class="panel form-grid compact-form" method="post" action="/account/profile">
            <div class="span-2 panel-heading">
                <h2>Profile</h2>
            </div>
            <label>Display name
                <input required name="display_name" maxlength="80" value="{e(user["display_name"])}">
            </label>
            <label>Username
                <input required name="username" maxlength="40" pattern="[A-Za-z0-9_\\-]{{3,40}}" value="{e(user["username"])}">
            </label>
            <label class="span-2">Email
                <input name="email" type="email" maxlength="254" value="{e(user["email"])}">
            </label>
            <label class="checkbox-line span-2">
                <input type="checkbox" name="public_email" value="1"{public_email_checked}>
                Show email on my member profile
            </label>
            <label class="span-2">Bio
                <textarea name="bio" rows="5" maxlength="1000">{e(user["bio"])}</textarea>
            </label>
            <div class="span-2 panel-heading with-gap">
                <h2>Notification preferences</h2>
            </div>
            <fieldset class="preference-checks span-2">
                <legend>In-app notifications</legend>
                <div class="preference-option-grid">
                    <label class="checkbox-line preference-option">
                        <input type="checkbox" name="notify_trade_offer_enabled" value="1"{notify_trade_offer_checked}>
                        Trade offers
                    </label>
                    <label class="checkbox-line preference-option">
                        <input type="checkbox" name="notify_trade_comment_enabled" value="1"{notify_trade_comment_checked}>
                        Comments
                    </label>
                    <label class="checkbox-line preference-option">
                        <input type="checkbox" name="notify_trade_counter_enabled" value="1"{notify_trade_counter_checked}>
                        Counter offers
                    </label>
                    <label class="checkbox-line preference-option">
                        <input type="checkbox" name="notify_trade_status_enabled" value="1"{notify_trade_status_checked}>
                        Trade status
                    </label>
                    <label class="checkbox-line preference-option">
                        <input type="checkbox" name="price_alerts_enabled" value="1"{price_alert_checked}>
                        Price alerts
                    </label>
                    <label class="checkbox-line preference-option">
                        <input type="checkbox" name="watchlist_alerts_enabled" value="1"{watchlist_alert_checked}>
                        Watchlist alerts
                    </label>
                    <label class="checkbox-line preference-option">
                        <input type="checkbox" name="notify_import_complete_enabled" value="1"{notify_import_complete_checked}>
                        Import completion
                    </label>
                    <label class="checkbox-line preference-option">
                        <input type="checkbox" name="notify_admin_notice_enabled" value="1"{notify_admin_notice_checked}>
                        Admin notices
                    </label>
                </div>
            </fieldset>
            <label class="span-2">Minimum change percent
                <input name="price_alert_threshold_percent" type="number" min="0" step="0.1" value="{e(price_alert_threshold)}">
            </label>
            <label class="span-2">Remind me about pending trade offers after
                <input name="stale_trade_reminder_days" type="number" min="0" max="90" step="1" value="{e(stale_trade_reminder_days)}">
            </label>
            <p class="muted compact span-2">Price alerts use the minimum change percent. Watchlist alerts trigger when another user lists a wanted card for trade. Set pending trade reminders to 0 to turn them off.</p>
            {trade_email_controls}
            <label class="span-2">Current password
                <input required name="current_password" type="password" autocomplete="current-password">
            </label>
            <div class="form-actions span-2">
                <button class="button primary" type="submit">Save profile</button>
            </div>
        </form>

        <form class="panel form-grid compact-form" method="post" action="/account/password">
            <div class="span-2 panel-heading">
                <h2>Password</h2>
            </div>
            <label class="span-2">Current password
                <input required name="current_password" type="password" autocomplete="current-password">
            </label>
            <label class="span-2">New password
                <input required name="new_password" type="password" minlength="8" autocomplete="new-password">
            </label>
            <label class="span-2">Confirm new password
                <input required name="confirm_password" type="password" minlength="8" autocomplete="new-password">
            </label>
            <div class="form-actions span-2">
                <button class="button primary" type="submit">Change password</button>
            </div>
            <p class="muted compact span-2">Changing your password signs out other active sessions.</p>
        </form>

        {render_two_factor_account_panel(user, recovery_codes=recovery_codes)}

        {render_passkey_account_panel(user)}

        {integration_panel}

        <article class="panel export-panel">
            <div class="panel-heading">
                <h2>Account export</h2>
                <span class="pill">JSON</span>
            </div>
            <p class="muted">Download your collection, wants, groups, trades, notifications, and price history in one portable account file.</p>
            <div class="form-actions">
                <a class="button secondary" href="/account/export">Download account data</a>
                <a class="button secondary" href="/cleanup">Open cleanup tools</a>
                <a class="button secondary" href="/cleanup/audit">Audit condition/finish</a>
            </div>
        </article>
    </section>
    """
    return render_layout(user, "Account", content, active="account", notice=notice, status=status)


__all__ = ['render_subnav', 'render_cards_subnav', 'render_wishlist_subnav', 'group_listing_url', 'render_layout', 'render_login', 'render_two_factor_login', 'render_register', 'render_recovery_code_panel', 'render_two_factor_account_panel', 'render_passkey_account_panel', 'render_account']
