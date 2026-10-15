"""The employer's own posting for each Jobright row that opens only a homepage.

Jobright's lists carry a jobright.ai page and the company's homepage, never
the employer's posting, so Backend's read model sends students to the
homepage (job_bank_read_v1.link_kind = 'company_site') whenever the
employer's copy isn't on the site already: 1,040 of 2,072 CS rows on 10/15.
This step finds that copy among the postings the bank reads and saves it
(job_bank_jobright_links, sql/20261015_jobright_links.sql); the read model
links the row to it on its next Jobright pass (every 30 minutes). It runs
after every crawl.

An employer with no board read yet gets its homepage read for one
(discover.from_homepages, once a month per domain), so the next crawl reads
that board and a later run matches. A row looked up with no match is asked
about again after 6 hours. A link whose posting closes is dropped, so the
row goes back to the homepage until a new match.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

from jobboards.discover import _company_key, from_homepages

# Words that never tell two roles apart.
_NOISE = frozenset("a an and the of for in at to with position role opening opportunity".split())
_SAME = {"internship": "intern", "internships": "intern", "interns": "intern", "engineering": "engineer",
         "engineers": "engineer", "sr": "senior", "jr": "junior"}
# A word only one side has makes it another role: Intern vs. full-time, Co-op
# vs. Intern, Engineer I vs. II, Senior.
_KIND = frozenset("intern coop junior senior staff principal lead manager director i ii iii iv 1 2 3 4".split())
# Workday lists a posting open in several places as "2 Locations": no city.
_NOT_A_CITY = frozenset("remote hybrid onsite site in usa united states america office city location locations".split())
# Jobright's copy of a title read off an employer's job page, "<title> Job Details / <Company>",
# can have lost the term the employer's title led with ("2027 Summer Intern: HR - People
# Analytics" became "HR - People Analytics Job Details / Hubbell Incorporated", 10/15).
_PAGE_TAIL = re.compile(r"\s*job details\s*/.*$", re.I)
_TERM = frozenset("intern coop summer fall winter spring".split())


def title_words(title: Optional[str]) -> frozenset:
    text = _PAGE_TAIL.sub("", title or "").lower().replace("&", " and ")
    text = re.sub(r"\bco[- ]?op\b", "coop", text)
    return frozenset(w for w in (_SAME.get(w, w) for w in re.findall(r"[a-z0-9+#]+", text)) if w not in _NOISE)


def _years(words: frozenset) -> frozenset:
    return frozenset(w for w in words if re.fullmatch(r"20\d\d", w))


def place_words(locations: Optional[Iterable[str]]) -> frozenset:
    """The city words of each place: its first part ("Seattle" of "Seattle, Washington,
    United States"), no state codes, nothing that names no city ("Remote", "Hybrid in")."""
    words = set()
    for place in locations or ():
        first = (place or "").lower().replace("nyc", "new york").split(",")[0]
        words |= {w for w in re.findall(r"[a-z]+", first) if len(w) > 2 and w not in _NOT_A_CITY}
    return frozenset(words)


def _score(jobright: frozenset, employer: frozenset, page_copy: bool = False) -> Tuple[float, bool]:
    """(how alike two titles are, 0 when they're two roles; whether that holds only
    with a city in common). Jobright's title may carry extra words (page text, a
    place) around the employer's; the employer's may not carry words Jobright's
    lacks unless nearly all the rest agree, or Jobright's is a job page's copy that
    lost only the term."""
    if len(jobright) < 2 or len(employer) < 2:
        return 0.0, False  # "Intern" alone says nothing about which role
    years_a, years_b = _years(jobright), _years(employer)
    if years_a and years_b and not years_a & years_b:
        return 0.0, False
    if (jobright & _KIND) != (employer & _KIND):
        lost = employer - jobright
        if page_copy and jobright <= employer and all(w in _TERM or w in years_b for w in lost):
            return len(jobright) / len(employer), True
        return 0.0, False
    same = len(jobright & employer) / len(jobright | employer)
    if same >= 0.8 or (employer <= jobright and same >= 0.5):
        return same, False
    return 0.0, False


def best_match(title: str, locations: Optional[Iterable[str]], postings: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The posting that is this role: the closest title, then the most city words in
    common. A posting in another city is another role; no city on a side doesn't block."""
    words, places, page_copy = title_words(title), place_words(locations), bool(_PAGE_TAIL.search(title or ""))
    found = []
    for posting in postings:
        score, needs_city = _score(words, title_words(posting["title"]), page_copy)
        if not score:
            continue
        theirs = place_words(posting.get("locations"))
        common = len(places & theirs)
        if (places and theirs and not common) or (needs_city and not common):
            continue
        found.append((-score, -common, posting["id"], posting))
    if not found:
        return None
    score, _, _, posting = min(found, key=lambda f: f[:3])
    return {**posting, "score": -score}


def _domain(url: Optional[str]) -> str:
    try:
        return (urlsplit(url or "").hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def board_index(bank: Any) -> Dict[str, Any]:
    """The boards the bank has read, by company key and by the homepage domain they were found
    from: what tells a Jobright row's employer has a board here."""
    by_name: Dict[str, set] = defaultdict(set)
    by_domain: Dict[str, set] = defaultdict(set)
    read = set()
    for board in bank.board_owners():
        for name in (board["company_name"], *board["posting_names"]):
            if _company_key(name):
                by_name[_company_key(name)].add(board["id"])
        source = board["evidence_source"] or ""
        if source.startswith("homepage:"):
            by_domain[source.removeprefix("homepage:")].add(board["id"])
        if board["last_ok_at"]:
            read.add(board["id"])
    return {"by_name": by_name, "by_domain": by_domain, "read": read}


def group_rows(rows: List[Dict[str, Any]]) -> Dict[tuple, List[Dict[str, Any]]]:
    """Rows by employer: (company key, homepage domain)."""
    groups: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(_company_key(row["company_name"]), _domain(row["company_website"]))].append(row)
    return groups


def read_boards(index: Dict[str, Any], name: str, domain: str) -> set:
    """The employer's boards the crawl has read at least once."""
    boards = (index["by_name"].get(name, set()) if name else set()) | \
        (index["by_domain"].get(domain, set()) if domain else set())
    return boards & index["read"]


def resolve(bank: Any, http: Any, limit: int = 5000, homepage_budget_s: float = 600) -> Dict[str, int]:
    """Look up to `limit` homepage-only Jobright rows up; read the homepages of
    employers with no board for up to `homepage_budget_s` seconds."""
    stats: Counter = Counter(links_dropped=bank.drop_closed_jobright_links())
    rows = bank.jobright_unresolved(limit)
    stats["rows"] = len(rows)
    if not rows:
        return dict(stats)
    index = board_index(bank)

    saves, homepages = [], {}
    for (name, domain), group in group_rows(rows).items():
        boards = read_boards(index, name, domain)
        if not boards:
            # Asked about again in 6 hours, by when its homepage's board may have been read.
            saves += [(row["job_id"], None, None, None) for row in group]
            stats["no_board"] += len(group)
            if domain:
                company, site, n = homepages.get(domain, (group[0]["company_name"], group[0]["company_website"], 0))
                homepages[domain] = (company, site, n + len(group))
            continue
        postings = bank.open_postings_of(sorted(boards))
        for row in group:
            found = best_match(row["title"], row["locations"], postings)
            if found:
                saves.append((row["job_id"], found["id"], found["url"], round(found["score"], 3)))
                stats["linked"] += 1
            else:
                saves.append((row["job_id"], None, None, None))
                stats["missed"] += 1
    bank.save_jobright_links(saves)

    stats["companies_without_board"] = len(homepages)
    if homepages and homepage_budget_s > 0:
        companies = sorted(homepages.values(), key=lambda c: (-c[2], c[0]))
        stats["boards_found"] = from_homepages(http, bank, companies, budget_s=homepage_budget_s)
    return dict(stats)
