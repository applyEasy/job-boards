"""Compare one large Lever closure batch with the employer's current API list."""

import argparse
import json
import os
from datetime import datetime, timedelta

import psycopg
import requests


BOARD_KEY = "aogarciaagency"
BOARD_ID = f"lever:{BOARD_KEY}"
API_URL = f"https://api.lever.co/v0/postings/{BOARD_KEY}?mode=json"


def utc_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamps must include an offset")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=utc_time)
    parser.add_argument("--end", required=True, type=utc_time)
    args = parser.parse_args()
    if not args.start < args.end <= args.start + timedelta(hours=2):
        parser.error("audit window must be positive and at most two hours")

    with psycopg.connect(os.environ["BANK_DATABASE_URL"], autocommit=True, connect_timeout=15) as conn:
        conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '45s'")
        rows = conn.execute(
            """select id from public.bank_postings
               where board_id = %s and closed_at >= %s and closed_at < %s""",
            (BOARD_ID, args.start, args.end),
        ).fetchall()
        open_count = conn.execute(
            "select count(*) from public.bank_postings where board_id = %s and closed_at is null",
            (BOARD_ID,),
        ).fetchone()[0]
        conn.execute("ROLLBACK")

    response = requests.get(API_URL, timeout=45)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        raise RuntimeError("Lever API did not return a listing")
    live_ids = {
        f"lever-{BOARD_KEY}-{item['id']}"
        for item in data
        if isinstance(item, dict) and item.get("id") and str(item.get("text") or "").strip()
    }
    closed_ids = {row[0] for row in rows}
    print(json.dumps({
        "board": BOARD_ID,
        "window": [args.start.isoformat(), args.end.isoformat()],
        "current_api_postings": len(live_ids),
        "bank_open_postings": open_count,
        "currently_closed_from_window": len(closed_ids),
        "closed_ids_in_current_api": len(closed_ids & live_ids),
        "limitation": "The API is read after the crawl; a role can reappear between reads.",
    }, indent=2))


if __name__ == "__main__":
    main()
