"""The AI posting finder: Claude looks for an employer's own postings on the web.

resolve.py links a Jobright row to its posting only when the crawler reads that
employer's board. Many big employers' boards can't be found from their homepage
(IBM, Amazon, Lockheed Martin, SpaceX on 10/15), so their rows keep opening the
homepage. For each such employer, once a month, this asks Claude (web search, at
most 5 searches) for the employer's careers board and each listed role's posting.

Nothing Claude returns is used until code has checked it:
- a link on a platform the crawler reads (Greenhouse, Workday, ...) becomes a board
  (evidence `agent:<domain>`); the crawl reads it and resolve links the rows, by
  its own title rules;
- any other link is saved only if the page loads (robots.txt allowing), isn't a job
  board's, and shows the role's title and the employer's name. Those links are
  checked again every 3 days and dropped once the page stops showing the role.

Runs as its own lane (`python -m jobboards find`) with a daily cap on employers
asked. Cost: one request per employer with up to 5 web searches ($10 per 1,000)
plus the tokens, roughly $0.05-0.30 an employer on claude-opus-5-5 (set
FINDER_MODEL to change it).
"""

from __future__ import annotations

import html as html_lib
import logging
import os
import re
from collections import Counter
from datetime import date
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from jobboards.boards import Board, board_from_url
from jobboards.discover import _company_boards, _company_key
from jobboards.readers import _robots_allows, _robots_rules
from jobboards.resolve import _SAME, board_index, group_rows, read_boards, title_words

log = logging.getLogger("jobboards.agent")

MODEL = os.environ.get("FINDER_MODEL") or "claude-opus-5-5"
ROLES_PER_COMPANY = 10
MAX_SEARCHES = 5
MAX_TURNS = 3

# Sites that list other companies' jobs: never the posting (Backend's read_rules.JOB_BOARD_HOSTS and more).
_JOB_BOARDS = frozenset(("jobright linkedin indeed ziprecruiter simplify joinhandshake handshake glassdoor adzuna "
                         "builtin dice wellfound monster careerbuilder simplyhired talent jooble lensa jobgether "
                         "ycombinator ripplematch levels").split())

REPORT = {
    "name": "report",
    "description": "Report what you found. Call it once, at the end.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "careers_url": {"type": "string",
                            "description": "The employer's own careers site or job board listing its jobs; \"\" if none."},
            "postings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "integer", "description": "The role's number in the list."},
                        "url": {"type": "string", "description": "The role's posting on the employer's own site or "
                                                                 "applicant tracking system; \"\" if not found."},
                    },
                    "required": ["role", "url"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["careers_url", "postings"],
        "additionalProperties": False,
    },
}

PROMPT = """Employer: {company}
Homepage: {homepage}
Open roles another site lists for this employer:
{roles}

Find this employer's own careers site (the page or job board where it lists its open jobs), and for each role, \
the URL of that role's posting on the employer's own careers site or applicant tracking system (Greenhouse, Lever, \
Workday, iCIMS, Oracle, SuccessFactors, Ashby, SmartRecruiters and the like). Never give a job board or aggregator \
(LinkedIn, Indeed, Glassdoor, ZipRecruiter, Jobright, Simplify, Handshake, Built In, Dice, Wellfound, Monster). \
Give "" for a role you can't find rather than a guess. Then call the report tool once with what you found."""


def is_job_board(url: Optional[str]) -> bool:
    host = (urlsplit(url or "").hostname or "").lower()
    labels = host.split(".")
    return any(label in _JOB_BOARDS for label in labels[-3:-1])


def ask(client: Any, company: str, homepage: str, roles: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Claude's report for one employer: {careers_url, postings: [{role, url}]}, or None (a refusal, or no report)."""
    listed = "\n".join(f"{n}. {r['title']} ({', '.join((r.get('locations') or [])[:3]) or 'place not given'})"
                       for n, r in enumerate(roles, 1))
    first = {"role": "user", "content": PROMPT.format(company=company, homepage=homepage or "unknown", roles=listed)}
    messages: List[Dict[str, Any]] = [first]
    for _ in range(MAX_TURNS):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "medium"},
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": MAX_SEARCHES}, REPORT],
            messages=messages,
        )
        report = next((b for b in response.content if b.type == "tool_use" and b.name == "report"), None)
        if report is not None:
            return dict(report.input)
        if response.stop_reason == "refusal":
            return None
        if response.stop_reason == "pause_turn":  # a long search turn: send it back to continue
            messages = [first, {"role": "assistant", "content": response.content}]
            continue
        messages = messages + [{"role": "assistant", "content": response.content},
                               {"role": "user", "content": "Call the report tool now with what you found."}]
    return None


def verify(http: Any, url: str, title: str, company: str, robots: Optional[dict] = None) -> Optional[str]:
    """The page's address if it is this employer's posting of this role, else None."""
    robots = {} if robots is None else robots
    try:
        parts = urlsplit(url or "")
        if parts.scheme not in ("http", "https") or not parts.hostname or is_job_board(url):
            return None
        root = f"{parts.scheme}://{parts.netloc}"
        if root not in robots:
            reply = http.get(f"{root}/robots.txt")
            robots[root] = _robots_rules(reply.text) if reply.status_code == 200 else []
        if not _robots_allows(robots[root], parts.path or "/"):
            return None
        reply = http.get(url)
    except Exception:  # unreachable, too large, throttled: not checked, so not used
        return None
    final = reply.url or url
    if reply.status_code != 200 or is_job_board(final):
        return None
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", reply.text[:2_000_000])).lower()
    words, key = title_words(title), _company_key(company)
    page = frozenset(_SAME.get(w, w) for w in re.findall(r"[a-z0-9+#]+", text))
    shows_role = len(words) >= 2 and len(words & page) >= 0.8 * len(words)
    shows_employer = bool(key) and key in re.sub(r"[^a-z0-9]", "", text)
    return final if shows_role and shows_employer else None


def recheck(bank: Any, http: Any, limit: int = 500) -> int:
    """Its links not checked for 3 days, checked again; a page that no longer shows the role loses its link."""
    rows, robots = bank.jobright_agent_links(limit), {}
    dropped = [r for r in rows if not verify(http, r["url"], r["title"], r["company_name"], robots)]
    bank.save_jobright_links([(r["job_id"], None, None, None) for r in dropped])
    bank.touch_jobright_links([r["job_id"] for r in rows if r not in dropped])
    return len(dropped)


def find(bank: Any, http: Any, client: Any, max_companies: int = 20, daily_companies: int = 60,
         month: Optional[str] = None, limit: int = 5000) -> Dict[str, int]:
    """Ask Claude about up to `max_companies` employers whose homepage-only rows the crawl can't link,
    most rows first, never past `daily_companies` in 24 hours."""
    month = month or date.today().strftime("%Y-%m")
    run_id, stats = bank.start_run("agent"), Counter(companies_asked=0)
    try:
        stats["links_dropped"] = recheck(bank, http)
        left = min(max_companies, daily_companies - bank.agent_companies_today())
        if left <= 0:
            stats["daily_cap_reached"] = 1
            return dict(stats)
        index, done = board_index(bank), bank.done_items("agent")
        employers = sorted(((key, group) for key, group in group_rows(bank.jobright_homepage_only(limit)).items()
                            if not read_boards(index, *key) and f"{key[1] or key[0]}|{month}" not in done),
                           key=lambda item: -len(item[1]))
        robots: dict = {}
        for (name, domain), group in employers[:left]:
            company, homepage = group[0]["company_name"], group[0]["company_website"]
            roles = group[:ROLES_PER_COMPANY]
            try:
                report = ask(client, company, homepage, roles)
            except Exception as exc:  # one employer's failed request never stops the others
                log.warning("finder: %s could not be asked: %s", company, exc)
                stats["errors"] += 1
                continue
            stats["companies_asked"] += 1
            bank.mark_done("agent", f"{domain or name}|{month}")
            if not report:
                stats["no_report"] += 1
                continue
            evidence = f"agent:{domain or name}"
            urls = [report.get("careers_url") or "", *[(p.get("url") or "") for p in report.get("postings") or []]]
            boards = [(Board(b.provider, b.key, company), url, evidence) for url in urls if (b := board_from_url(url))]
            careers = report.get("careers_url") or ""
            if careers and not board_from_url(careers) and not is_job_board(careers):
                boards += [(Board(b.provider, b.key, company), page, evidence) for b, page in _company_boards(http, careers)]
            stats["boards_found"] += bank.add_boards(boards)
            saves = []
            for posting in report.get("postings") or []:
                url, n = posting.get("url") or "", posting.get("role")
                if not url or board_from_url(url):  # nothing, or a board the crawl now reads
                    continue
                role = roles[n - 1] if isinstance(n, int) and 0 < n <= len(roles) else None
                checked = role and verify(http, url, role["title"], company, robots)
                if checked:
                    saves.append((role["job_id"], None, checked, 1.0))
                    stats["linked"] += 1
                else:
                    stats["rejected"] += 1
            bank.save_jobright_links(saves)
        return dict(stats)
    finally:
        bank.finish_run(run_id, dict(stats))
