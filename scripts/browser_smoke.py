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

    page.locator('a[href="#trade-offer"]').click()
    page.locator('input[data-side="offer"][data-card-name="Sol Ring"]').fill("1")
    expect(page.locator("#trade-selected")).to_contain_text("Sol Ring")
    page.locator('a[href="#trade-message"]').click()
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
    finally:
        context.close()


def run_smoke() -> None:
    port = find_free_port()
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="binderbridge-browser-smoke-") as tmp:
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
                    seed_trade_partner(app)
                    import_owner_collection(page, base_url, Path(tmp))
                    add_owner_want(page, base_url)
                    trade_id = propose_trade_from_browse(page, base_url)
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
            print("Browser smoke passed.")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        run_smoke()
