"""python -m jobboards discover | crawl | stats   (BANK_DATABASE_URL names the bank)"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from jobboards.bank import Bank
from jobboards.crawl import GAPS, crawl
from jobboards.discover import from_bank, from_commoncrawl, from_directory, from_feeds
from jobboards.http import Http


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="jobboards")
    sub = parser.add_subparsers(dest="cmd", required=True)
    discover = sub.add_parser("discover", help="find boards (daily)")
    discover.add_argument("--no-commoncrawl", action="store_true")
    discover.add_argument("--cc-crawls", type=int, default=12, help="newest Common Crawl crawls to walk")
    discover.add_argument("--cc-budget", type=float, default=1200, help="seconds for Common Crawl this run")
    run = sub.add_parser("crawl", help="read the boards that are due (hourly)")
    run.add_argument("--budget", type=float, default=2400, help="seconds to keep starting boards")
    run.add_argument("--limit", type=int, default=50000, help="most boards to take this run")
    run.add_argument("--workers", type=int, default=16)
    sub.add_parser("stats", help="boards and open postings per platform")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    dsn = os.environ.get("BANK_DATABASE_URL")
    if not dsn:
        sys.exit("BANK_DATABASE_URL is not set")
    bank = Bank(dsn)
    http = Http(gaps={**GAPS, "index.commoncrawl.org": 2.0, "raw.githubusercontent.com": 0.2})
    try:
        if args.cmd == "discover":
            run_id = bank.start_run("discover")
            stats = {"directory": bank.add_boards(from_directory()), "feeds": bank.add_boards(from_feeds(http)),
                     "bank": bank.add_boards(from_bank(bank))}
            if not args.no_commoncrawl:
                stats["commoncrawl"] = from_commoncrawl(http, bank, crawls=args.cc_crawls, budget_s=args.cc_budget)
            stats["pruned_closed_postings"] = bank.prune()
            bank.finish_run(run_id, stats)
        elif args.cmd == "crawl":
            stats = crawl(bank, http, budget_s=args.budget, limit=args.limit, workers=args.workers)
        else:
            stats = bank.stats()
        print(json.dumps(stats, indent=1, default=str))
    finally:
        bank.close()


if __name__ == "__main__":
    main()
