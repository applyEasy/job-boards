# job-boards

Finds employers' job boards on its own and keeps every posting on them in a
bank (Postgres `bank_*` tables). applyFinch's Backend reads the bank's changes,
keeps the roles it wants and shows them. This repo never filters: every
posting on a board is stored.

- **Find** (`discover`, daily): boards come only from public links on an exact
  job-platform host (never a guessed slug): the pinned LastRound ATS directory,
  the public GitHub job lists, Common Crawl's index (resumable page by page),
  and links inside postings already in the bank.
- **Read** (`crawl`, hourly): the boards that are due, one worker per API host,
  politely spaced. A board whose listing hasn't changed writes nothing. New,
  edited and relisted postings are written; postings a *complete* listing
  dropped are closed. Busy boards are read every 6 h, quiet ones back off to
  48 h, empty ones weekly; a board failing for 7 days is dead (its postings
  close) and is retried monthly.
- **Platforms**: Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Oracle
  Recruiting Cloud, iCIMS. Posting ids are job_bank's own `source_id`s
  (`gh-<slug>-<id>`, `wd-<tenant>-<slug_req>`, ...).

## Setup (once)

1. Apply `sql/20261014_boards_bank.sql` to the Supabase database (SQL editor
   or `psql`). It creates the tables (RLS on, closed to anon/authenticated)
   and a `job_boards_crawler` role that can touch only them.
2. Give the role a password: `alter role job_boards_crawler login password '<openssl rand -hex 24>';`
3. Add the repo secret `BANK_DATABASE_URL`:
   `postgresql://job_boards_crawler.<project-ref>:<password>@<session-pooler-host>:5432/postgres`
   (Dashboard > Connect > Session pooler shows the host).
4. Actions > crawl > Run workflow: `discover` once, then `crawl`.
   After that the schedule runs it.

Rollback: disable the workflow; `sql/20261014_boards_bank_ROLLBACK.sql` drops
the tables and the role.

## Run locally

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
export BANK_DATABASE_URL=postgresql://...
.venv/bin/python -m jobboards discover --no-commoncrawl
.venv/bin/python -m jobboards crawl --limit 50 --budget 120
.venv/bin/python -m jobboards stats
```

Options: `discover --cc-crawls 12 --cc-budget 1200`, `crawl --budget 2400 --limit 50000 --workers 16`.
Each run writes a `bank_runs` row with its counts.

## Tests

```bash
.venv/bin/pytest -q                                   # unit tests
BANK_TEST_DATABASE_URL=postgresql://postgres@localhost:5432/scratch .venv/bin/pytest -q   # + bank tests (wipes that DB's bank_* tables)
```

## Data

`jobboards/data/lastround-ats-company-directory-2026-08.csv` is LastRound AI's
ATS directory, CC BY 4.0 (licence beside it), from
[fyrosofttech/lastroundai-hiring-data](https://github.com/fyrosofttech/lastroundai-hiring-data)
at commit `2816bfadd65b7bbec23701dd036bcc7496c729db`.
