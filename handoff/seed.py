import argparse

from handoff.db import database_path, reset_demo_database


def main():
    parser = argparse.ArgumentParser(description="Reset the database to fictional demo data.")
    parser.add_argument("--reset", action="store_true", help="Permanently discard all existing records.")
    args = parser.parse_args()
    if not args.reset:
        parser.error("Pass --reset to discard existing records and load demo data.")
    reset_demo_database(database_path())


if __name__ == "__main__":
    main()
