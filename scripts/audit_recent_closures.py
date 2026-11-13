"""Read-only audit of one production crawl's posting closures."""

import argparse
import json
import os
from datetime import datetime, timedelta

import psycopg


def utc_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamps must include a UTC offset")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=utc_time)
    parser.add_argument("--end", required=True, type=utc_time)
    args = parser.parse_args()
    if not args.start < args.end <= args.start + timedelta(hours=2):
        parser.error("audit window must be positive and at most two hours")

    with psycopg.connect(os.environ["BANK_DATABASE_URL"], autocommit=True, connect_timeout=15) as conn:
        conn.execute("BEGIN READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '45s'")
        columns = conn.execute(
            """select column_name, data_type, is_nullable
               from information_schema.columns
               where table_schema = 'public' and table_name = 'bank_postings'
                 and column_name in ('last_listed_at', 'gone_first_seen_at')
               order by column_name"""
        ).fetchall()
        by_provider = conn.execute(
            """select b.provider, count(*) as closed, count(distinct p.board_id) as boards
               from public.bank_postings p join public.bank_boards b on b.id = p.board_id
               where p.closed_at >= %s and p.closed_at < %s
               group by b.provider order by closed desc""",
            (args.start, args.end),
        ).fetchall()
        top_boards = conn.execute(
            """select p.board_id, b.provider, b.status, count(*) as closed
               from public.bank_postings p join public.bank_boards b on b.id = p.board_id
               where p.closed_at >= %s and p.closed_at < %s
               group by p.board_id, b.provider, b.status
               order by closed desc limit 20""",
            (args.start, args.end),
        ).fetchall()
        runs = conn.execute(
            """select id, started_at, finished_at,
                      coalesce((stats->>'postings_closed')::integer, 0) as closed
               from public.bank_runs where kind = 'crawl'
                 and started_at >= %s and started_at < %s
               order by started_at""",
            (args.start, args.end),
        ).fetchall()
        conn.execute("ROLLBACK")

    print(json.dumps({
        "window": [args.start.isoformat(), args.end.isoformat()],
        "columns": [dict(zip(("name", "type", "nullable"), row)) for row in columns],
        "by_provider": [dict(zip(("provider", "closed", "boards"), row)) for row in by_provider],
        "top_boards": [dict(zip(("board_id", "provider", "status", "closed"), row)) for row in top_boards],
        "runs": [dict(zip(("id", "started_at", "finished_at", "closed"), row)) for row in runs],
    }, default=str, indent=2))


if __name__ == "__main__":
    main()
