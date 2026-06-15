"""Verify that a database created by a published BinderBridge tag upgrades cleanly."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FROM_TAG = "v0.1.0-alpha.1"

OLD_RELEASE_FIXTURE = """
import json
import app

app.init_db()
user_id = app.create_user("upgradefixture", "password123", "Upgrade Fixture")
app.import_collection_csv(
    user_id,
    b"Name,Quantity,Trade\\nPublished Release Sentinel,2,1\\n",
    enrich_scryfall=False,
)
print(json.dumps({"version": app.APP_VERSION, "schema": app.CURRENT_SCHEMA_VERSION}))
"""

CURRENT_RELEASE_UPGRADE = """
import json
import sqlite3
import app

app.init_db()
item = app.row(
    "SELECT card_name, quantity, quantity_for_trade FROM collection_items WHERE card_name = ?",
    ("Published Release Sentinel",),
)
version = app.row("SELECT value FROM app_settings WHERE key = ?", (app.SCHEMA_VERSION_KEY,))
history = app.rows("SELECT version FROM schema_migration_history ORDER BY version")
connection = sqlite3.connect(app.DB_PATH)
quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
foreign_key_issues = connection.execute("PRAGMA foreign_key_check").fetchall()
connection.close()

assert item["quantity"] == 2
assert item["quantity_for_trade"] == 1
assert int(version["value"]) == app.CURRENT_SCHEMA_VERSION
assert quick_check == "ok"
assert not foreign_key_issues
print(json.dumps({
    "version": app.APP_VERSION,
    "schema": int(version["value"]),
    "migration_history": len(history),
    "preserved_card": dict(item),
    "quick_check": quick_check,
    "foreign_key_issues": len(foreign_key_issues),
}))
"""


def run_python(code, cwd, env):
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    output = result.stdout.strip().splitlines()
    if not output:
        raise RuntimeError(f"Upgrade smoke subprocess produced no output in {cwd}.")
    return json.loads(output[-1])


def main():
    from_tag = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BINDERBRIDGE_UPGRADE_FROM_TAG", DEFAULT_FROM_TAG)
    subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/tags/{from_tag}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        archive_path = temp_path / "source.zip"
        old_root = temp_path / from_tag
        data_path = temp_path / "data"
        subprocess.run(
            ["git", "archive", "--format=zip", f"--output={archive_path}", from_tag],
            cwd=ROOT,
            check=True,
        )
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(old_root)
        env = dict(os.environ)
        env["BINDERBRIDGE_DATA"] = str(data_path)
        old_release = run_python(OLD_RELEASE_FIXTURE, old_root, env)
        current_release = run_python(CURRENT_RELEASE_UPGRADE, ROOT, env)

    print(json.dumps({
        "from_tag": from_tag,
        "from_release": old_release,
        "to_release": current_release,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
