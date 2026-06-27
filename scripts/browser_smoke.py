"""Headless browser smoke tests for BinderBridge.

This script starts a temporary BinderBridge server, drives a real Chromium
browser with Playwright, and verifies high-traffic fresh-install flows. It is
kept outside ``unittest discover`` because it downloads/runs browser tooling.
"""

from __future__ import annotations

import contextlib
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # pragma: no cover - exercised by local environments.
    print(
        "Playwright is required for browser smoke tests.\n"
        "Install it with:\n"
        "  python -m pip install playwright\n"
        "  python -m playwright install chromium",
        file=sys.stderr,
    )
    raise SystemExit(2)


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "app.py"
SMOKE_PASSWORD = "password123"
SCREENSHOT_DIR_ENV = "BINDERBRIDGE_BROWSER_SCREENSHOT_DIR"
VISUAL_VIEWPORTS = (
    ("desktop", {"width": 1440, "height": 1000}),
    ("mobile", {"width": 390, "height": 900}),
)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_server(base_url: str, process: subprocess.Popen, timeout: float = 25.0) -> None:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        if process.poll() is not None:
            break
        try:
            with urlopen(f"{base_url}/login", timeout=1.0) as response:
                if response.status == 200:
                    return
        except URLError as exc:
            last_error = str(exc)
        except OSError as exc:
            last_error = str(exc)
        time.sleep(0.2)
    raise RuntimeError(f"BinderBridge did not start at {base_url}: {last_error}")


def terminate_server(process: subprocess.Popen) -> tuple[str, str]:
    if process.poll() is None:
        process.terminate()
        try:
            return process.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
    return process.communicate(timeout=8)


def configure_app_for_fixture(data_dir: Path):
    os.environ["BINDERBRIDGE_DATA"] = str(data_dir)
    import app

    app.DATA_DIR = data_dir
    app.DB_PATH = data_dir / "binderbridge.sqlite3"
    app.init_db()
    return app


def seed_trade_partner(app) -> tuple[int, int]:
    owner = app.get_user_by_username("owner")
    if not owner:
        raise RuntimeError("Owner user was not created by the browser registration flow.")
    bob_id = app.create_user("bob", SMOKE_PASSWORD, "Bob Trader", email="bob@example.test")
    app.upsert_collection_item(
        bob_id,
        {
            "card_name": "Lightning Bolt",
            "set_name": "Magic 2011",
            "set_code": "M11",
            "collector_number": "149",
            "quantity": 2,
            "quantity_for_trade": 1,
            "condition": "NM",
            "finish": "Regular",
            "language": "English",
            "type_line": "Instant",
            "price_usd": "1.25",
            "is_public": 1,
        },
        merge=False,
    )
    return int(owner["id"]), int(bob_id)


def seed_visual_workspace_data(app, owner_id: int, bob_id: int) -> dict[str, int]:
    owner_card = app.row(
        "SELECT * FROM collection_items WHERE user_id = ? AND card_name = ? ORDER BY id DESC LIMIT 1",
        (owner_id, "Sol Ring"),
    )
    owner_want = app.row(
        "SELECT * FROM want_items WHERE user_id = ? AND card_name = ? ORDER BY id DESC LIMIT 1",
        (owner_id, "Lightning Bolt"),
    )
    bob_card = app.row(
        "SELECT * FROM collection_items WHERE user_id = ? AND card_name = ? ORDER BY id DESC LIMIT 1",
        (bob_id, "Lightning Bolt"),
    )
    if not owner_card or not owner_want or not bob_card:
        raise RuntimeError("Visual workspace fixture data was not created.")

    deck_group_id = app.create_card_group(owner_id, "deck", "Browser Smoke Deck", "Visual coverage deck")
    app.add_collection_item_to_group(owner_id, deck_group_id, int(owner_card["id"]), 1)
    wishlist_group_id = app.create_card_group(owner_id, "wishlist", "Browser Smoke Wants", "Visual coverage wishlist")
    app.add_want_item_to_group(owner_id, wishlist_group_id, int(owner_want["id"]))
    app.create_notification(
        owner_id,
        "admin_notice",
        "Visual check notice",
        "This seeded notice keeps the notification workspace populated during browser visual checks.",
        "/admin",
    )
    return {
        "deck_group_id": int(deck_group_id),
        "wishlist_group_id": int(wishlist_group_id),
        "owner_card_id": int(owner_card["id"]),
        "bob_card_id": int(bob_card["id"]),
    }


def register_owner(page, base_url: str) -> None:
    page.goto(f"{base_url}/register")
    expect(page.get_by_role("heading", name=re.compile("Create account", re.I))).to_be_visible()
    page.get_by_label("Display name").fill("Owner User")
    page.get_by_label("Username").fill("owner")
    page.get_by_label("Email").fill("owner@example.test")
    page.get_by_label("Password").fill(SMOKE_PASSWORD)
    page.get_by_role("button", name="Create account").click()
    page.wait_for_url(f"{base_url}/")
    expect(page.get_by_role("link", name=re.compile("My Cards|Collection", re.I))).to_be_visible()


def complete_setup_basics(page, base_url: str) -> None:
    page.goto(f"{base_url}/admin/setup")
    expect(page.get_by_role("heading", name="First-run setup wizard")).to_be_visible()
    expect(page.get_by_text("Recommended defaults for small local groups")).to_be_visible()
    public_url = page.locator("#setup-public-base-url")
    if public_url.is_editable():
        public_url.fill(base_url)
        page.locator("#setup-public-url").get_by_role("button", name="Save public URL").click()
        expect(page.get_by_text(re.compile("Public base URL saved", re.I))).to_be_visible()
    page.goto(f"{base_url}/admin/health")
    expect(page.get_by_role("heading", name=re.compile("Maintenance health", re.I))).to_be_visible()


def open_workspace_tab(page, href: str, selector: str) -> None:
    page.locator(f'a[href="{href}"]').first.click()
    expect(page.locator(selector)).to_be_visible()


def screenshot_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-").lower()
    return cleaned or "page"


def screenshot_root(tmp_path: Path) -> Path:
    configured = os.environ.get(SCREENSHOT_DIR_ENV, "").strip()
    root = Path(configured) if configured else tmp_path / "browser-smoke-screenshots"
    root.mkdir(parents=True, exist_ok=True)
    return root


def assert_visual_layout(page, label: str) -> None:
    issues = page.evaluate(
        """
        () => {
            const issues = [];
            const tolerance = 2;
            const selectors = [
                ".page-shell > .section-heading",
                ".page-shell > .settings-summary",
                ".page-shell > .workspace-layout",
                ".page-shell > .content-grid",
                ".page-shell > .panel",
                ".page-shell > form",
                ".workspace-layout > .workspace-side-nav",
                ".workspace-layout > .workspace-pane-stack",
                ".workspace-pane-stack > .workspace-section:not([hidden])",
                ".workspace-section:not([hidden]) > .panel",
                ".workspace-section:not([hidden]) > .content-grid",
                ".workspace-section:not([hidden]) > form",
                ".workspace-section:not([hidden]) > .metric-grid",
                ".workspace-section:not([hidden]) > .admin-settings-grid",
                ".metric-grid > .metric",
                ".settings-summary > *",
                ".admin-settings-grid > *",
                ".content-grid > *",
                ".group-list > *",
                ".stack-list > li",
                ".notification-list > li",
                ".trade-summary-grid > *",
                ".trade-detail-grid > *",
                ".duplicate-grid > *",
                ".workspace-side-nav > .workspace-nav-link"
            ];

            function visible(element) {
                if (!element || element.hidden) return false;
                const style = window.getComputedStyle(element);
                if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) return false;
                const rect = element.getBoundingClientRect();
                return rect.width > tolerance && rect.height > tolerance;
            }

            function rect(element) {
                const box = element.getBoundingClientRect();
                return {left: box.left, right: box.right, top: box.top, bottom: box.bottom, width: box.width, height: box.height};
            }

            function overlap(a, b) {
                const x = Math.min(a.right, b.right) - Math.max(a.left, b.left);
                const y = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
                return x > tolerance && y > tolerance;
            }

            const scrollWidth = Math.max(document.documentElement.scrollWidth, document.body ? document.body.scrollWidth : 0);
            if (scrollWidth - window.innerWidth > tolerance) {
                issues.push(`page has horizontal overflow: ${scrollWidth}px content in ${window.innerWidth}px viewport`);
            }

            const seen = new Set();
            selectors.forEach((selector) => {
                const nodes = Array.from(document.querySelectorAll(selector)).filter(visible);
                for (let i = 0; i < nodes.length; i += 1) {
                    for (let j = i + 1; j < nodes.length; j += 1) {
                        const first = nodes[i];
                        const second = nodes[j];
                        if (first.parentElement !== second.parentElement) continue;
                        if (first.contains(second) || second.contains(first)) continue;
                        const key = `${selector}|${i}|${j}`;
                        if (seen.has(key)) continue;
                        seen.add(key);
                        if (overlap(rect(first), rect(second))) {
                            const firstClass = String(first.getAttribute("class") || "(no-class)");
                            const secondClass = String(second.getAttribute("class") || "(no-class)");
                            issues.push(`${selector} siblings overlap: ${first.tagName.toLowerCase()}.${firstClass} and ${second.tagName.toLowerCase()}.${secondClass}`);
                        }
                    }
                }
            });

            document.querySelectorAll("[data-workspace-tabs]").forEach((workspace, index) => {
                const links = Array.from(workspace.querySelectorAll(".workspace-nav-link[href^='#']")).filter(visible);
                const sections = links.map((link) => {
                    const id = decodeURIComponent(String(link.getAttribute("href") || "").slice(1));
                    return document.getElementById(id);
                }).filter((section) => section && section.classList.contains("workspace-section") && workspace.contains(section));
                const activeLinks = links.filter((link) => link.classList.contains("active"));
                const activeSections = sections.filter((section) => !section.hidden && visible(section));
                if (activeLinks.length !== 1) {
                    issues.push(`workspace ${index + 1} has ${activeLinks.length} active nav links`);
                }
                if (activeSections.length !== 1) {
                    issues.push(`workspace ${index + 1} has ${activeSections.length} visible active sections`);
                }
                const nav = workspace.querySelector(".workspace-side-nav");
                const section = activeSections[0];
                if (visible(nav) && section && overlap(rect(nav), rect(section))) {
                    issues.push(`workspace ${index + 1} side navigation overlaps active section #${section.id}`);
                }
            });

            return issues;
        }
        """
    )
    if issues:
        details = "\n".join(f"  - {issue}" for issue in issues)
        raise RuntimeError(f"Visual layout regression on {label}:\n{details}")


def capture_visual_state(page, screenshot_dir: Path, viewport_name: str, target_name: str) -> None:
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(100)
    path = screenshot_dir / f"{viewport_name}-{screenshot_name(target_name)}.png"
    page.screenshot(path=str(path), full_page=True, animations="disabled")


def verify_visual_target(page, base_url: str, screenshot_dir: Path, viewport_name: str, target: dict[str, object]) -> None:
    page.goto(f"{base_url}{target['path']}")
    expect(page.locator(".page-shell")).to_be_visible()
    assert_visual_layout(page, f"{viewport_name}/{target['name']}")
    capture_visual_state(page, screenshot_dir, viewport_name, str(target["name"]))
    for tab in target.get("tabs", ()):
        href, selector, tab_name = tab
        open_workspace_tab(page, href, selector)
        label = f"{target['name']}-{tab_name}"
        assert_visual_layout(page, f"{viewport_name}/{label}")
        capture_visual_state(page, screenshot_dir, viewport_name, label)


def visual_targets(fixture: dict[str, int], trade_id: int, bob_id: int) -> list[dict[str, object]]:
    trade_builder_query = urlencode(
        {
            "recipient_id": bob_id,
            f"offer_{fixture['owner_card_id']}": 1,
            f"request_{fixture['bob_card_id']}": 1,
            "offer_per_page": 10,
            "request_per_page": 10,
        }
    )
    return [
        {"name": "collection", "path": "/collection"},
        {"name": "wishlist", "path": "/wants"},
        {"name": "groups-cards", "path": "/groups"},
        {"name": "groups-wishlist", "path": "/groups?type=wishlist"},
        {
            "name": "account-profile",
            "path": "/account",
            "tabs": (
                ("#account-notifications", "#account-notifications", "notifications"),
                ("#account-security", "#account-security", "security"),
                ("#account-integrations", "#account-integrations", "integrations"),
                ("#account-data", "#account-data", "data"),
            ),
        },
        {
            "name": "admin-overview",
            "path": "/admin",
            "tabs": (
                ("#admin-policies", "#admin-policies", "policies"),
                ("#admin-access", "#admin-access", "access"),
                ("#admin-operations", "#admin-operations", "operations"),
                ("#admin-users", "#admin-users", "users"),
            ),
        },
        {
            "name": "notifications-inbox",
            "path": "/notifications",
            "tabs": (
                ("#notification-values", "#notification-values", "values"),
                ("#notification-cleanup", "#notification-cleanup", "cleanup"),
            ),
        },
        {
            "name": "cleanup-collection",
            "path": "/cleanup",
            "tabs": (("#cleanup-wants", "#cleanup-wants", "wants"),),
        },
        {
            "name": "audit-results",
            "path": "/cleanup/audit",
            "tabs": (("#audit-summary", "#audit-summary", "summary"),),
        },
        {
            "name": "trade-builder-selected",
            "path": f"/trades/new?{trade_builder_query}",
            "tabs": (
                ("#trade-recommendations", "#trade-recommendations", "recommendations"),
                ("#trade-offer", "#trade-offer", "offer"),
                ("#trade-request", "#trade-request", "request"),
                ("#trade-message", "#trade-message", "message"),
            ),
        },
        {
            "name": "trade-detail-response",
            "path": f"/trades/{trade_id}",
            "tabs": (
                ("#trade-cards", "#trade-cards", "cards"),
                ("#trade-issues", "#trade-issues", "issues"),
                ("#trade-feedback", "#trade-feedback", "feedback"),
                ("#trade-comments", "#trade-comments", "comments"),
            ),
        },
        {
            "name": "group-detail-cards",
            "path": f"/groups/{fixture['deck_group_id']}",
            "tabs": (
                ("#group-sharing", "#group-sharing", "sharing"),
                ("#group-import", "#group-import", "import"),
                ("#group-danger", "#group-danger", "danger"),
            ),
        },
        {
            "name": "wishlist-group-detail-cards",
            "path": f"/groups/{fixture['wishlist_group_id']}",
            "tabs": (
                ("#group-sharing", "#group-sharing", "sharing"),
                ("#group-danger", "#group-danger", "danger"),
            ),
        },
    ]


def verify_visual_regression_pages(page, base_url: str, screenshot_dir: Path, fixture: dict[str, int], trade_id: int, bob_id: int) -> None:
    for viewport_name, viewport in VISUAL_VIEWPORTS:
        page.set_viewport_size(viewport)
        for target in visual_targets(fixture, trade_id, bob_id):
            verify_visual_target(page, base_url, screenshot_dir, viewport_name, target)


def verify_account_workspace(page, base_url: str) -> None:
    page.goto(f"{base_url}/account")
    expect(page.get_by_role("heading", name="Account settings")).to_be_visible()
    open_workspace_tab(page, "#account-notifications", "#account-notifications")
    expect(page.get_by_role("heading", name="Notification preferences")).to_be_visible()
    open_workspace_tab(page, "#account-security", "#account-security")
    expect(page.get_by_role("heading", name="Sign-in protection")).to_be_visible()
    open_workspace_tab(page, "#account-integrations", "#account-integrations")
    expect(page.get_by_role("heading", name="API and webhooks")).to_be_visible()


def verify_cleanup_workspace(page, base_url: str) -> None:
    page.goto(f"{base_url}/cleanup")
    expect(page.get_by_role("heading", name="Duplicate cleanup")).to_be_visible()
    open_workspace_tab(page, "#cleanup-wants", "#cleanup-wants")
    expect(page.locator("#cleanup-wants").get_by_role("heading", name="Wanted-card duplicates", exact=True)).to_be_visible()

    page.goto(f"{base_url}/cleanup/audit")
    expect(page.get_by_role("heading", name=re.compile("Condition .* finish audit", re.I))).to_be_visible()
    open_workspace_tab(page, "#audit-summary", "#audit-summary")
    expect(page.locator("#audit-summary")).to_contain_text("cards need audit")


def import_owner_collection(page, base_url: str, tmp_path: Path) -> None:
    csv_path = tmp_path / "owner-collection.csv"
    csv_path.write_text(
        "Name,Quantity,Trade Qty,Set,Set Code,Collector Number,Finish,Condition,Language\n"
        "Sol Ring,2,1,Commander Masters,CMM,703,Regular,NM,English\n",
        encoding="utf-8",
    )
    page.goto(f"{base_url}/import")
    page.get_by_label("CSV file").set_input_files(str(csv_path))
    page.get_by_label("Default trade qty").fill("1")
    page.get_by_label("Scryfall lookup").uncheck()
    page.get_by_role("button", name="Preview CSV").click()
    expect(page.get_by_role("heading", name="Import preview")).to_be_visible()
    expect(page.get_by_text("Will insert")).to_be_visible()
    page.get_by_role("button", name="Import these rows").click()
    expect(page.get_by_text(re.compile("Imported 1 collection rows?", re.I))).to_be_visible()

    page.goto(f"{base_url}/collection?q=Sol")
    expect(page.get_by_role("heading", name=re.compile("Your cards", re.I))).to_be_visible()
    expect(page.get_by_text("Sol Ring")).to_be_visible()


def add_owner_want(page, base_url: str) -> None:
    page.goto(f"{base_url}/wants")
    add_form = page.locator("#add-want form")
    add_form.get_by_label("Card name").fill("Lightning Bolt")
    add_form.get_by_label("Desired qty").fill("1")
    add_form.get_by_label("Scryfall lookup on save").uncheck()
    add_form.get_by_role("button", name="Add want").click()
    expect(page.get_by_text("Lightning Bolt")).to_be_visible()
    expect(page.get_by_text("Available for trade")).to_be_visible()


def propose_trade_from_browse(page, base_url: str) -> int:
    page.goto(f"{base_url}/browse?q=Lightning")
    expect(page.get_by_text("Lightning Bolt")).to_be_visible()
    page.locator("tr", has_text="Lightning Bolt").get_by_role("button", name="Propose trade").click()
    page.wait_for_url(re.compile(r"/trades/new\?.*recipient_id="))
    expect(page.get_by_role("heading", name=re.compile("Offer to Bob Trader", re.I))).to_be_visible()
    expect(page.locator("#trade-selected")).to_contain_text("Lightning Bolt")
    expect(page.locator(".trade-builder-status")).to_contain_text("You request 1")

    open_workspace_tab(page, "#trade-offer", "#trade-offer")
    page.locator('input[data-side="offer"][data-card-name="Sol Ring"]').fill("1")
    expect(page.locator("#trade-selected")).to_contain_text("Sol Ring")
    expect(page.locator(".trade-builder-status")).to_contain_text("You offer 1")
    open_workspace_tab(page, "#trade-message", "#trade-message")
    page.get_by_label("Message").fill("Browser smoke trade request.")
    page.get_by_role("button", name="Review trade").click()
    expect(page.get_by_role("heading", name=re.compile("Confirm with Bob Trader", re.I))).to_be_visible()
    fairness_ack = page.locator('input[name="fairness_ack"]')
    if fairness_ack.count():
        fairness_ack.check()
    page.get_by_role("button", name="Send trade").click()
    page.wait_for_url(re.compile(r"/trades/\d+$"))
    expect(page.get_by_text("Browser smoke trade request.")).to_be_visible()
    match = re.search(r"/trades/(\d+)$", page.url)
    if not match:
        raise RuntimeError(f"Expected trade detail URL, got {page.url}")
    return int(match.group(1))


def login(page, base_url: str, username: str) -> None:
    page.goto(f"{base_url}/login")
    page.get_by_label("Username").fill(username)
    page.get_by_label("Password").fill(SMOKE_PASSWORD)
    page.get_by_role("button", name="Sign in", exact=True).click()
    page.wait_for_url(f"{base_url}/")


def verify_recipient_notifications(browser, base_url: str, trade_id: int) -> None:
    context = browser.new_context()
    page = context.new_page()
    try:
        login(page, base_url, "bob")
        page.goto(f"{base_url}/notifications")
        expect(page.get_by_role("heading", name=re.compile("Notifications|Activity", re.I))).to_be_visible()
        expect(page.get_by_text(f"Trade #{trade_id}")).to_be_visible()
        open_workspace_tab(page, "#notification-cleanup", "#notification-cleanup")
        expect(page.get_by_role("heading", name="Inbox cleanup")).to_be_visible()
        open_workspace_tab(page, "#notification-inbox", "#notification-inbox")
        expect(page.get_by_text(f"Trade #{trade_id}")).to_be_visible()
    finally:
        context.close()


def run_smoke() -> None:
    port = find_free_port()
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="binderbridge-browser-smoke-") as tmp:
        screenshot_dir = screenshot_root(Path(tmp))
        data_dir = Path(tmp) / "data"
        data_dir.mkdir()
        app = configure_app_for_fixture(data_dir)
        env = os.environ.copy()
        env.update(
            {
                "BINDERBRIDGE_DATA": str(data_dir),
                "BINDERBRIDGE_HOST": "127.0.0.1",
                "BINDERBRIDGE_PORT": str(port),
            }
        )
        process = subprocess.Popen(
            [sys.executable, str(APP_PATH)],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="backslashreplace",
        )
        try:
            wait_for_server(base_url, process)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                context = browser.new_context(base_url=base_url, viewport={"width": 1440, "height": 1000})
                page = context.new_page()
                try:
                    register_owner(page, base_url)
                    complete_setup_basics(page, base_url)
                    verify_account_workspace(page, base_url)
                    owner_id, bob_id = seed_trade_partner(app)
                    import_owner_collection(page, base_url, Path(tmp))
                    verify_cleanup_workspace(page, base_url)
                    add_owner_want(page, base_url)
                    visual_fixture = seed_visual_workspace_data(app, owner_id, bob_id)
                    trade_id = propose_trade_from_browse(page, base_url)
                    verify_visual_regression_pages(page, base_url, screenshot_dir, visual_fixture, trade_id, bob_id)
                    verify_recipient_notifications(browser, base_url, trade_id)
                finally:
                    context.close()
                    browser.close()
        except (AssertionError, PlaywrightError, RuntimeError) as exc:
            stdout, stderr = terminate_server(process)
            print(f"Browser smoke failed: {exc}", file=sys.stderr)
            if stdout:
                print("\n--- BinderBridge stdout ---", file=sys.stderr)
                print(stdout[-4000:], file=sys.stderr)
            if stderr:
                print("\n--- BinderBridge stderr ---", file=sys.stderr)
                print(stderr[-4000:], file=sys.stderr)
            raise SystemExit(1) from exc
        else:
            stdout, stderr = terminate_server(process)
            if stderr.strip():
                print("--- BinderBridge stderr ---", file=sys.stderr)
                print(stderr[-4000:], file=sys.stderr)
            print(f"Browser smoke screenshots saved to {screenshot_dir}")
            print("Browser smoke passed.")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        run_smoke()
