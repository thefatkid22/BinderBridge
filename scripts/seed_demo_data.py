"""Seed BinderBridge demo data into a development database."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(
        description="Seed a rich BinderBridge demo dataset for local evaluation.",
    )
    parser.add_argument(
        "--reset-demo",
        action="store_true",
        help="Delete the known demo users first, then seed them again.",
    )
    parser.add_argument(
        "--allow-existing",
        action="store_true",
        help="Allow seeding into a database that already has non-demo users. Use only for disposable/dev databases.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the seed result as JSON.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    app.init_db()
    result = app.seed_demo_data(
        enabled=True,
        reset=args.reset_demo,
        allow_existing=args.allow_existing or args.reset_demo,
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("seeded") else 1

    if result.get("seeded"):
        print("Demo data seeded.")
        print(f"Database: {app.DB_PATH}")
        print("Accounts:")
        for username, password in result["accounts"].items():
            print(f"  {username} / {password}")
        print(
            "Summary: "
            f"{result['collection_items']} collection cards, "
            f"{result['wants']} wants, "
            f"{result['groups']} groups, "
            f"{result['trades']} trades, "
            f"{result['disputes']} dispute."
        )
        return 0

    reason = result.get("reason", "unknown")
    if reason == "existing_users":
        print(
            "Demo data was not seeded because this database already has users. "
            "Use --allow-existing for a disposable/dev database, or --reset-demo to refresh known demo users.",
            file=sys.stderr,
        )
    elif reason == "demo_exists":
        print(
            "Demo users already exist. Use --reset-demo to recreate the demo dataset.",
            file=sys.stderr,
        )
    elif reason == "disabled":
        print("Demo seeding is disabled.", file=sys.stderr)
    else:
        print(f"Demo data was not seeded: {reason}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
