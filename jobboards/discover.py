"""Find boards. Every source yields (Board, evidence_url, evidence_source):
a public link on an exact ATS host, never a guessed slug."""

from __future__ import annotations

import csv
import json
import logging
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple
from urllib.parse import urlencode, urljoin, urlsplit

from jobboards.boards import Board, board_from_url
from jobboards.readers import _robots_allows, _robots_rules

log = logging.getLogger("jobboards.discover")
Found = Tuple[Board, str, str]

DIRECTORY = Path(__file__).parent / "data" / "lastround-ats-company-directory-2026-08.csv"
_DIRECTORY_URL = {"greenhouse": "https://boards.greenhouse.io/{}", "lever": "https://jobs.lever.co/{}",
                  "ashby": "https://jobs.ashbyhq.com/{}"}

# The public GitHub job lists Backend already reads.
FEEDS = {
    "simplify_internships": "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
    "simplify_newgrad": "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
    "speedyapply_intern": "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/README.md",
    "speedyapply_newgrad": "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/NEW_GRAD_USA.md",
}
_URL = re.compile(r"""https?://[^\s<>"'()\[\]|]+""")

CC_INDEX = "https://index.commoncrawl.org"
# CDX URL pattern -> optional CDX filter (keeps the noisy hosts to job pages).
CC_PATTERNS: Dict[str, Optional[str]] = {
    "boards.greenhouse.io/*": None,
    "job-boards.greenhouse.io/*": None,
    "jobs.lever.co/*": None,
    "jobs.ashbyhq.com/*": None,
    "jobs.smartrecruiters.com/*": None,
    "*.myworkdayjobs.com": None,
    "*.icims.com": "~url:.*/jobs/.*",
    "*.oraclecloud.com": "~url:.*CandidateExperience.*",
}


def from_directory(path: Path = DIRECTORY) -> Iterator[Found]:
    """The pinned LastRound ATS directory (CC BY 4.0, see data/*LICENSE*)."""
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            template = _DIRECTORY_URL.get((row.get("ats_vendor") or "").strip().lower())
            url = template.format(row.get("board_slug", "").strip()) if template else ""
            board = board_from_url(url)
            if board:
                yield Board(board.provider, board.key, (row.get("company_name") or "").strip()), url, \
                    "directory:lastround-2026-08"


def from_feeds(http: Any) -> Iterator[Found]:
    for name, feed_url in FEEDS.items():
        try:
            reply = http.get(feed_url)
            if reply.status_code != 200:
                log.warning("feed %s answered %d", name, reply.status_code)
                continue
            text = reply.text
        except Exception as exc:
            log.warning("feed %s failed: %s", name, exc)
            continue
        items = []
        if feed_url.endswith(".json"):
            try:
                data = json.loads(text)
                items = [(str(i.get("url") or ""), str(i.get("company_name") or "")) for i in data if isinstance(i, dict)]
            except ValueError:
                pass
        items = items or [(url, "") for url in _URL.findall(text)]
        for url, company in items:
            board = board_from_url(url)
            if board:
                yield Board(board.provider, board.key, company), url, f"feed:{name}"


def from_bank(bank: Any) -> Iterator[Found]:
    """Links inside recently changed postings that point at another board."""
    for row in bank.query("select distinct coalesce(apply_url, url) as link from bank_postings "
                          "where changed_at > now() - interval '2 days'"):
        board = board_from_url(row["link"] or "")
        if board:
            yield board, row["link"], "bank"


def _cc_get(http: Any, crawl: str, params: Dict[str, Any]) -> Optional[str]:
    try:
        reply = http.get(f"{CC_INDEX}/{crawl}-index?{urlencode(params)}")
    except Exception as exc:
        log.info("commoncrawl %s %s: %s", crawl, params.get("page", "pages"), exc)
        return None
    return reply.text if reply.status_code == 200 else None


def from_commoncrawl(http: Any, bank: Any, crawls: int = 12, budget_s: float = 1200) -> int:
    """Walk the CDX index of the newest `crawls` crawls, one URL pattern at a time,
    saving each page's boards as it goes. Finished pages are recorded in the bank
    so a day that stops part way (the index often answers 502) resumes there."""
    deadline = time.monotonic() + budget_s
    try:
        crawl_ids = [c["id"] for c in http.get(f"{CC_INDEX}/collinfo.json").json()][:crawls]
    except Exception as exc:
        log.warning("commoncrawl crawl list unavailable: %s", exc)
        return 0
    done, added = bank.done_items("commoncrawl"), 0
    for crawl in crawl_ids:
        for pattern, cdx_filter in CC_PATTERNS.items():
            if f"{crawl}|{pattern}|*" in done:
                continue
            base = {"url": pattern, **({"filter": cdx_filter} if cdx_filter else {})}
            meta = _cc_get(http, crawl, {**base, "output": "json", "showNumPages": "true"})
            try:
                pages = int(json.loads(meta)["pages"]) if meta else None
            except (ValueError, KeyError, TypeError):
                pages = None
            if pages is None:
                continue
            finished = 0
            for page in range(pages):
                item = f"{crawl}|{pattern}|{page}"
                if item in done:
                    finished += 1
                    continue
                if time.monotonic() > deadline:
                    return added
                body = _cc_get(http, crawl, {**base, "output": "text", "fl": "url", "page": page})
                if body is None:
                    continue  # tomorrow
                first: Dict[str, Found] = {}
                for url in body.split():
                    board = board_from_url(url)
                    if board and board.id not in first:
                        first[board.id] = (board, url, f"commoncrawl:{crawl}")
                added += bank.add_boards(first.values())
                bank.mark_done("commoncrawl", item)
                finished += 1
            if finished == pages:
                bank.mark_done("commoncrawl", f"{crawl}|{pattern}|*")
    return added


# --- boards from employers' own homepages (the companies Jobright's lists name) ---

_HREF_URL = re.compile(r"""https?://[^\s"'<>\\)]+""", re.I)
_CAREER_HREF = re.compile(r"""href=["']([^"'#]*(?:career|/jobs|join-us|work-with-us)[^"'#]*)["']""", re.I)
_JOBS_SITE = re.compile(r"""href=["'](https://(?:jobs|careers?)\.[a-z0-9.-]+/?[^"'#]*)["']""", re.I)
_PHENOM = re.compile(r"phenompeople|cdn\.phenom|widgetApiEndpoint", re.I)
_PHENOM_API = re.compile(r'"widgetApiEndpoint"\s*:\s*"https://([^/"]+)/widgets"')
_SF_SITE = re.compile(r'jobTitle-link|rmkcdn\.successfactors|class="jobLocation"', re.I)
PAGES_PER_COMPANY = 8


def boards_on_page(url: str, html: str) -> list:
    """Every board a page names: links to a known platform, and the careers
    platform the page itself runs on (Phenom, Jibe, SuccessFactors)."""
    found = {}
    for link in _HREF_URL.findall(f"{url} {html}"):
        board = board_from_url(link)
        if board:
            found.setdefault(board.id, board)
    host = (urlsplit(url).hostname or "").lower()
    if html and host:
        if _PHENOM.search(html):
            api = _PHENOM_API.search(html)
            segs = [s for s in urlsplit(url).path.split("/") if s]
            path = f"{segs[0]}/{segs[1]}" if len(segs) >= 2 and len(segs[0]) == 2 and len(segs[1]) == 2 else "us/en"
            board = Board("phenom", f"{(api.group(1) if api else host).lower()}/{path.lower()}")
            found.setdefault(board.id, board)
        if len(re.findall("jibe", html, re.I)) >= 5:
            found.setdefault(f"jibe:{host}", Board("jibe", host))
        if _SF_SITE.search(html) and "successfactors.com" not in host:
            found.setdefault(f"successfactors:{host}", Board("successfactors", host))
    return list(found.values())


def _company_boards(http: Any, homepage: str) -> list:
    """(board, page it was named on) from a homepage and up to a few of its careers pages; robots.txt honoured."""
    rules: dict = {}

    def get(url: str) -> Tuple[str, str]:
        parts = urlsplit(url)
        root = f"{parts.scheme}://{parts.netloc}"
        if root not in rules:
            try:
                robots = http.get(f"{root}/robots.txt")
                rules[root] = None if robots.status_code == 429 or robots.status_code >= 500 else (
                    _robots_rules(robots.text) if robots.status_code == 200 else [])
            except Exception:
                rules[root] = None
        if rules[root] is None or not _robots_allows(rules[root], parts.path or "/"):
            return url, ""
        try:
            reply = http.get(url)
        except Exception:
            return url, ""
        return (reply.url or url), (reply.text[:800_000] if reply.status_code == 200 else "")

    final, html = get(homepage)
    boards = boards_on_page(final, html)
    if boards:
        return [(b, final) for b in boards]
    domain = (urlsplit(final).hostname or "").removeprefix("www.")
    queue = list(dict.fromkeys(urljoin(final, h) for h in _CAREER_HREF.findall(html)))[:3]
    queue += [f"https://careers.{domain}", f"https://jobs.{domain}"] if domain else []
    seen: set = set()
    while queue and len(seen) < PAGES_PER_COMPANY:
        url = queue.pop(0)
        if url in seen or not url.startswith("http"):
            continue
        seen.add(url)
        page, body = get(url)
        boards = boards_on_page(page, body)
        if boards:
            return [(b, page) for b in boards]
        # A careers page that sends people to a jobs site on another domain: one more hop.
        queue += [link for link in _JOBS_SITE.findall(body) if link not in seen][:3]
    return []


def from_homepages(http: Any, bank: Any, companies: list, month: Optional[str] = None,
                   budget_s: float = 900, workers: int = 16) -> int:
    """Read each company's homepage (and careers pages) once a month for the boards it uses.
    companies: (name, homepage, roles), most roles first."""
    month = month or date.today().strftime("%Y-%m")
    done, deadline = bank.done_items("homepage"), time.monotonic() + budget_s
    todo = []
    for name, homepage, _ in companies:
        domain = (urlsplit(homepage).hostname or "").lower().removeprefix("www.")
        if domain and f"{domain}|{month}" not in done:
            todo.append((name, homepage, domain))
            done.add(f"{domain}|{month}")  # one read per domain, even if two names share it

    def one(item: Tuple[str, str, str]) -> int:
        name, homepage, domain = item
        if time.monotonic() > deadline:
            return 0
        found = _company_boards(http, homepage)
        added = bank.add_boards((Board(b.provider, b.key, name), page, f"homepage:{domain}") for b, page in found)
        bank.mark_done("homepage", f"{domain}|{month}")
        return added

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return sum(pool.map(one, todo))


JOBRIGHT_ORG = "jobright-ai"


def jobright_companies(http: Any) -> list:
    """(company, homepage, roles) from Jobright's public GitHub lists, most roles first."""
    repos = http.get(f"https://api.github.com/orgs/{JOBRIGHT_ORG}/repos?per_page=100")
    names = [r["name"] for r in repos.json() if re.match(r"^\d{4}-", r.get("name") or "")] if repos.status_code == 200 else []
    roles, homes = Counter(), {}
    for name in names:
        reply = http.get(f"https://raw.githubusercontent.com/{JOBRIGHT_ORG}/{name}/HEAD/README.md")
        if reply.status_code != 200:
            continue
        company = None
        for line in reply.text.splitlines():
            if "jobright.ai/jobs/info/" not in line:
                continue
            cell = line.split("|")[1] if line.count("|") > 2 else ""
            link = re.search(r"\[([^\]]+)\]\((https?://[^)]+)\)", cell)
            if link:
                company = link.group(1).strip()
                homes.setdefault(company, link.group(2).strip())
            elif "↳" not in cell:
                company = re.sub(r"\*\*", "", cell).strip() or None
            if company:
                roles[company] += 1
    return sorted(((c, homes[c], n) for c, n in roles.items() if c in homes), key=lambda r: (-r[2], r[0]))


_SUFFIXES = re.compile(r"\b(inc|llc|ltd|corp|corporation|company|co|the|group|holdings|plc|stores)\b")


def _company_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _SUFFIXES.sub("", (name or "").lower()))


def jobright_coverage(bank: Any, companies: list, top: int = 25) -> dict:
    """How much of Jobright's lists the bank already reads: roles at employers with
    an active board that has open postings. The gate for turning Jobright off."""
    read = {_company_key(r["company_name"]) for r in bank.query(
        "select distinct company_name from bank_boards where status = 'active' and open_postings > 0")}
    total = covered = n_covered = 0
    missing = []
    for name, _, roles in companies:
        total += roles
        if _company_key(name) in read:
            covered, n_covered = covered + roles, n_covered + 1
        else:
            missing.append([name, roles])
    return {"roles_total": total, "roles_covered": covered,
            "percent": round(100 * covered / total, 1) if total else 0.0,
            "companies_total": len(companies), "companies_covered": n_covered,
            "top_missing": sorted(missing, key=lambda m: -m[1])[:top]}
