# job-boards

Finds employers' job boards on its own and keeps every posting on them in a
bank (Postgres `bank_*` tables). The bank never filters: every posting on a
board is stored. The intern and new-grad ones go onto applyfinch.com (the
`job_bank` table) in the same write, so a new posting is live as soon as its
board is read.

- **Find** (`discover` daily, `boards` all day): boards come only from public links on an exact
  job-platform host (never a guessed slug). `discover`: the pinned LastRound ATS directory,
  the public GitHub job lists and links inside postings already in the bank. `boards`
  (its own runs): employer homepages from the public job lists, most roles first, each
  once a month, and Common Crawl's index (resumable page by page).
- **Read** (`crawl`, hourly): the boards that are due, split across 4 crawlers
  running side by side (each takes whole hosts, `--shard/--shards`), one worker per API host,
  politely spaced. A board whose listing hasn't changed writes nothing. New,
  edited and relisted postings are written; postings a *complete* listing
  dropped are closed. Busy boards are read every hour, quiet ones back off to
  48 h, empty ones weekly; a board failing for 7 days is dead (its postings
  close) and is retried monthly.
- **Publish** (inside every crawl write): new and edited intern/new-grad
  postings are upserted into `job_bank`, closed ones taken down
  (`jobboards/publish.py` holds a copy of Backend's title rules). Each crawl
  also marks the live rows the bank still holds as seen, once a day each, so
  Backend's 7-day stale sweep leaves them up.
- **Link** (`resolve`, its own hourly runs): a Jobright row on applyfinch.com
  that opens only the employer's homepage gets the employer's posting from
  the bank (same role by title words, kind, year and city), saved in
  `job_bank_jobright_links` for Backend's read model. An employer with no
  board yet gets its homepage read for one.
- **Ask** (`find`, its own runs, needs `ANTHROPIC_API_KEY`): for employers the
  crawl can't link, Claude searches the web once a month per employer for its
  careers board and postings. A board it names is added for the crawl; any
  other link is saved only once its page shows the role and the employer,
  and checked again every 3 days. Model `claude-haiku-5-5` (about $0.05 an
  employer, mostly web searches); repo variables `FINDER_MODEL` and
  `FINDER_DAILY_COMPANIES` (default 60) change the model and the daily cap.
- Each kind of run has its own queue; the heartbeat keeps every lane going.
- **Platforms**: Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Oracle
  Recruiting Cloud, iCIMS, Phenom, Jibe, and SuccessFactors. Posting ids are job_bank's own `source_id`s
  (`gh-<slug>-<id>`, `wd-<tenant>-<slug_req>`, ...).

## Setup (once)

1. Apply `sql/20261014_boards_bank.sql` to the Supabase database (SQL editor
   or `psql`). It creates the tables (RLS on, closed to anon/authenticated)
   and a `job_boards_crawler` role that can touch only them.
   Then `sql/20261014_bank_publish.sql` (the functions that write `job_bank`),
   `sql/20261015_jobright_links.sql` (resolve) and `sql/20261016_agent_finder.sql` (find).
2. Give the role a password: `alter role job_boards_crawler login password '<openssl rand -hex 24>';`
3. Add the repo secret `BANK_DATABASE_URL`:
   `postgresql://job_boards_crawler.<project-ref>:<password>@<session-pooler-host>:5432/postgres`
   (Dashboard > Connect > Session pooler shows the host). For `find`, also
   `ANTHROPIC_API_KEY`; without it that lane does nothing.
4. Actions > crawl > Run workflow: `discover` once, then `crawl`, then
   `publish` once (puts postings stored before publishing existed on the site).
   After that the schedule runs it.

Rollback: disable the workflow; first `sql/20261014_bank_publish_ROLLBACK.sql`
stops the `job_bank` writes, `sql/20261014_boards_bank_ROLLBACK.sql` drops
the tables and the role.

## Jobright coverage gate

`.github/workflows/jobright-coverage.yml` measures the share of Jobright-listed
roles whose employer has an active board with open postings in the bank. It runs
daily and after a trusted push to `main`, validates the report, and keeps the
credential-free JSON as a 30-day artifact. The database URL remains only in the
workflow environment; pull requests never receive it.

This report is read-only evidence, not permission to turn Jobright off. Review
`top_missing` and confirm direct posting twins before changing Backend's
Jobright source setting.

## Run locally

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
export BANK_DATABASE_URL=postgresql://...
.venv/bin/python -m jobboards discover
.venv/bin/python -m jobboards boards --homepage-budget 60 --cc-budget 0
.venv/bin/python -m jobboards crawl --limit 50 --budget 120
.venv/bin/python -m jobboards resolve
ANTHROPIC_API_KEY=... .venv/bin/python -m jobboards find --max-companies 2
.venv/bin/python -m jobboards publish
.venv/bin/python -m jobboards stats
```

Options: `boards --homepage-budget 2400 --cc-crawls 12 --cc-budget 1200`, `crawl --budget 2400 --limit 50000 --workers 16 --shard 0 --shards 1`,
`resolve --homepage-budget 600`, `find --max-companies 20 --daily-companies 60` (`FINDER_MODEL`, default `claude-haiku-5-5`).
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
