"""python -m jobboards discover | boards | crawl | resolve | find | publish | stats   (BANK_DATABASE_URL names the bank)"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from jobboards.agent import find
from jobboards.bank import Bank
from jobboards.crawl import GAPS, crawl
from jobboards.discover import from_bank, from_commoncrawl, from_directory, from_feeds, from_homepages, jobright_companies, jobright_coverage
from jobboards.http import Http
from jobboards.resolve import resolve


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="jobboards")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("discover", help="find boards from the quick sources: directory, feeds, the bank's own links (daily)")
    boards = sub.add_parser("boards", help="find boards from employers' homepages and Common Crawl (its own runs, all day)")
    boards.add_argument("--homepage-budget", type=float, default=2400,
                        help="seconds for reading Jobright-listed companies' homepages this run")
    boards.add_argument("--cc-crawls", type=int, default=12, help="newest Common Crawl crawls to walk")
    boards.add_argument("--cc-budget", type=float, default=1200, help="seconds for Common Crawl this run")
    run = sub.add_parser("crawl", help="read the boards that are due (hourly)")
    run.add_argument("--budget", type=float, default=2400, help="seconds to keep starting boards")
    run.add_argument("--limit", type=int, default=50000, help="most boards to take this run")
    run.add_argument("--workers", type=int, default=16)
    run.add_argument("--shard", type=int, default=0, help="this crawler's share of the boards (0-based)")
    run.add_argument("--shards", type=int, default=1, help="how many crawlers split the boards")
    links = sub.add_parser("resolve", help="give homepage-only Jobright rows the employer's posting (its own hourly runs)")
    links.add_argument("--homepage-budget", type=float, default=600)
    finder = sub.add_parser("find", help="ask Claude for the postings of employers the crawl can't link (needs ANTHROPIC_API_KEY)")
    finder.add_argument("--max-companies", type=int, default=20, help="most employers asked this run")
    finder.add_argument("--daily-companies", type=int, default=int(os.environ.get("FINDER_DAILY_COMPANIES") or 60),
                        help="most employers asked in 24 hours (repo variable FINDER_DAILY_COMPANIES)")
    sub.add_parser("publish", help="put every open intern/new-grad posting in the bank on the site (catch-up)")
    sub.add_parser("stats", help="boards and open postings per platform")
    sub.add_parser("jobright-coverage", help="share of Jobright's roles at employers the bank reads")
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
            stats["pruned_closed_postings"] = bank.prune()
            bank.finish_run(run_id, stats)
        elif args.cmd == "boards":
            # Each homepage is read once a month and each Common Crawl index page once, so once
            # caught up a run is quick; most roles first, so the employers that matter go first.
            run_id = bank.start_run("boards")
            stats = {"homepages": from_homepages(http, bank, jobright_companies(http), budget_s=args.homepage_budget),
                     "commoncrawl": from_commoncrawl(http, bank, crawls=args.cc_crawls, budget_s=args.cc_budget)}
            bank.finish_run(run_id, stats)
        elif args.cmd == "crawl":
            stats = crawl(bank, http, budget_s=args.budget, limit=args.limit, workers=args.workers,
                          shard=args.shard, shards=args.shards)
        elif args.cmd == "resolve":
            stats = resolve(bank, http, homepage_budget_s=args.homepage_budget)
        elif args.cmd == "find":
            if os.environ.get("ANTHROPIC_API_KEY"):
                import anthropic  # only this lane needs it

                stats = find(bank, http, anthropic.Anthropic(), max_companies=args.max_companies,
                             daily_companies=args.daily_companies)
            else:
                stats = {"skipped": "ANTHROPIC_API_KEY is not set"}
        elif args.cmd == "publish":
            stats = {"job_bank_rows_written": bank.publish_open()}
        elif args.cmd == "jobright-coverage":
            stats = jobright_coverage(bank, jobright_companies(http))
        else:
            stats = bank.stats()
        print(json.dumps(stats, indent=1, default=str))
        if args.cmd == "crawl" and (stats.get("bank_error") or stats.get("touch_error")):
            sys.exit("Crawl finished with database/publishing failures; see run counts above")
    finally:
        bank.close()


if __name__ == "__main__":
    main()
