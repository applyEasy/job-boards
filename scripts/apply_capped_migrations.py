"""Apply the two catalog-only capped-board safety migrations.

This entry point is intentionally narrow. It is run manually by the
apply-capped-migrations workflow, which shares the crawler's concurrency group.
"""

import os
from pathlib import Path

import psycopg


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = (
    ROOT / "sql/20261112_last_listed_at.sql",
    ROOT / "sql/20261113_posting_gone_confirmation.sql",
)


def main() -> None:
    dsn = os.environ["BANK_DATABASE_URL"]
    with psycopg.connect(dsn, autocommit=True, connect_timeout=15) as conn:
        for migration in MIGRATIONS:
            conn.execute(migration.read_text(), prepare=False)
            print(f"applied {migration.name}")
        rows = conn.execute(
            """select column_name, data_type, is_nullable
               from information_schema.columns
               where table_schema = 'public' and table_name = 'bank_postings'
                 and column_name in ('last_listed_at', 'gone_first_seen_at')"""
        ).fetchall()
    observed = {name: (data_type, is_nullable) for name, data_type, is_nullable in rows}
    expected = {"last_listed_at", "gone_first_seen_at"}
    if set(observed) != expected or any(
        kind != "timestamp with time zone" or nullable != "YES"
        for kind, nullable in observed.values()
    ):
        raise RuntimeError("capped-board columns missing, non-nullable, or wrong type after migration")
    print("verified both nullable capped-board timestamp columns")


if __name__ == "__main__":
    main()
