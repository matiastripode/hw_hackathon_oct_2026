"""Explicit reset for fictional hackathon demo data."""

import argparse

from app import db


def main():
    parser = argparse.ArgumentParser(description="Reset the fictional handoff board demo.")
    parser.add_argument("--reset", action="store_true", help="Delete board entries/handoffs and restore the demo")
    parser.add_argument("--db", default=str(db.DEFAULT_DB), help="SQLite database path")
    args = parser.parse_args()
    if not args.reset:
        parser.error("Pass --reset to confirm replacement of the existing board data.")
    db.reset_demo(args.db)
    print(f"Fictional demo restored in {args.db}")


if __name__ == "__main__":
    main()
