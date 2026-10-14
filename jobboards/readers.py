"""One reader per job platform. A reader reads a board's whole listing and
returns every posting on it, unfiltered.

A Listing is `complete` only when the reader saw the whole listing; only a
complete listing may close postings. A board the platform says doesn't exist
raises NotABoard; any other bad answer (an error, HTML where JSON belongs, a
wrong shape) raises ReadFailed, so an outage never looks like an empty board.
Posting ids and sources are job_bank's own (source, source_id).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from jobboards.boards import Board

SMART_PAGE, SMART_MAX = 100, 5000
WORKDAY_PAGE, WORKDAY_CEILING = 20, 2000  # Workday's search stops at 2,000 results
ORACLE_PAGE, ORACLE_MAX = 100, 5000
ICIMS_PAGES_PER_READ = 200  # new/changed iCIMS posting pages opened per read; the rest wait for the next one


class NotABoard(Exception):
    """The platform says this board does not exist (or robots.txt shuts us out)."""


class ReadFailed(Exception):
    """The board could not be read this time."""


@dataclass(frozen=True)
class Posting:
    id: str
    source: str
    company_name: str
    title: str
    url: str
    locations: List[str] = field(default_factory=list)
    workplace: Optional[str] = None
    employment_type: Optional[str] = None
    department: Optional[str] = None
    apply_url: Optional[str] = None
    posted_at: Optional[str] = None
    source_updated_at: Optional[str] = None

    def content_hash(self) -> str:
        """Everything but posted_at: Workday's 'Posted 3 Days Ago' moves daily without the posting changing."""
        data = asdict(self)
        data.pop("posted_at")
        return hashlib.sha1(json.dumps(data, sort_keys=True).encode()).hexdigest()


@dataclass
class Listing:
    postings: List[Posting]
    complete: bool
    keep: Set[str] = field(default_factory=set)  # listed, known and unchanged, so not re-read


Reader = Callable[[Board, Any, Dict[str, Optional[str]]], Listing]


def _text(value: Any) -> Optional[str]:
    text = " ".join(str(value).split()) if value is not None else ""
    return text or None


def _places(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    for value in values:
        text = _text(value)
        if text and text not in out:
            out.append(text)
    return out[:20]


def _company(board: Board, *names: Any) -> str:
    for name in (*names, board.company_name):
        if _text(name):
            return _text(name)
    slug = board.key.split("/")[0].split(".")[0]
    return slug.replace("-", " ").replace("_", " ").title()


def _json(reply: Any, gone: Iterable[int] = (404,)) -> Any:
    if reply.status_code in tuple(gone):
        raise NotABoard(f"HTTP {reply.status_code}")
    if reply.status_code != 200:
        raise ReadFailed(f"HTTP {reply.status_code}")
    try:
        return reply.json()
    except ValueError:
        raise ReadFailed("answer is not JSON") from None


def read_greenhouse(board: Board, http: Any, known: Dict[str, Optional[str]]) -> Listing:
    data = _json(http.get(f"https://boards-api.greenhouse.io/v1/boards/{board.key}/jobs"))
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ReadFailed("not a Greenhouse listing")
    slug, out = board.key.lower(), []
    for j in data["jobs"]:
        if not isinstance(j, dict) or j.get("id") is None or not _text(j.get("title")):
            continue
        out.append(Posting(
            id=f"gh-{slug}-{j['id']}", source="ats_greenhouse", company_name=_company(board, j.get("company_name")),
            title=_text(j["title"]), url=j.get("absolute_url") or f"https://boards.greenhouse.io/{slug}/jobs/{j['id']}",
            locations=_places([(j.get("location") or {}).get("name")]),
            posted_at=j.get("first_published"), source_updated_at=j.get("updated_at"),
        ))
    total = (data.get("meta") or {}).get("total")
    return Listing(out, complete=total in (None, len(data["jobs"])))


def read_lever(board: Board, http: Any, known: Dict[str, Optional[str]]) -> Listing:
    data = _json(http.get(f"https://api.lever.co/v0/postings/{board.key}?mode=json"))
    if not isinstance(data, list):
        raise ReadFailed("not a Lever listing")
    slug, out = board.key.lower(), []
    for j in data:
        if not isinstance(j, dict) or not j.get("id") or not _text(j.get("text")):
            continue
        cats = j.get("categories") or {}
        created = j.get("createdAt")
        out.append(Posting(
            id=f"lever-{slug}-{j['id']}", source="ats_lever", company_name=_company(board), title=_text(j["text"]),
            url=j.get("hostedUrl") or f"https://jobs.lever.co/{slug}/{j['id']}", apply_url=j.get("applyUrl"),
            locations=_places(cats.get("allLocations") or [cats.get("location")]),
            workplace=_text(j.get("workplaceType")), employment_type=_text(cats.get("commitment")),
            department=_text(cats.get("department") or cats.get("team")),
            posted_at=datetime.fromtimestamp(created / 1000, timezone.utc).isoformat()
            if isinstance(created, (int, float)) else None,
        ))
    return Listing(out, complete=True)


def read_ashby(board: Board, http: Any, known: Dict[str, Optional[str]]) -> Listing:
    data = _json(http.get(f"https://api.ashbyhq.com/posting-api/job-board/{board.key}?includeCompensation=false"),
                 gone=(404, 422))
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ReadFailed("not an Ashby listing")
    slug, out = board.key.lower(), []
    for j in data["jobs"]:
        if not isinstance(j, dict) or j.get("isListed") is False or not j.get("id") or not _text(j.get("title")):
            continue
        out.append(Posting(
            id=f"ashby-{slug}-{j['id']}", source="ats_ashby", company_name=_company(board), title=_text(j["title"]),
            url=j.get("jobUrl") or f"https://jobs.ashbyhq.com/{board.key}/{j['id']}", apply_url=j.get("applyUrl"),
            locations=_places([j.get("location")] + [s.get("location") for s in j.get("secondaryLocations") or []
                                                     if isinstance(s, dict)]),
            workplace=_text(j.get("workplaceType")) or ("Remote" if j.get("isRemote") else None),
            employment_type=_text(j.get("employmentType")), department=_text(j.get("department")),
            posted_at=j.get("publishedAt"), source_updated_at=j.get("updatedAt"),
        ))
    return Listing(out, complete=True)


def read_smartrecruiters(board: Board, http: Any, known: Dict[str, Optional[str]]) -> Listing:
    slug, out, seen = board.key.lower(), [], set()
    total: Optional[int] = None
    offset, complete = 0, True
    while True:
        if offset >= SMART_MAX:
            complete = False
            break
        data = _json(http.get(f"https://api.smartrecruiters.com/v1/companies/{board.key}/postings"
                              f"?limit={SMART_PAGE}&offset={offset}"), gone=(404, 422) if offset == 0 else ())
        content = data.get("content") if isinstance(data, dict) else None
        found = data.get("totalFound") if isinstance(data, dict) else None
        if not isinstance(content, list) or not isinstance(found, int):
            if total is None:
                raise ReadFailed("not a SmartRecruiters listing")
            complete = False
            break
        if total is None:
            total = found
        elif found != total:  # the listing moved under us
            complete = False
            break
        for j in content:
            job_id = str(j.get("id") or "") if isinstance(j, dict) else ""
            if not job_id or job_id in seen or not _text(j.get("name")):
                continue
            seen.add(job_id)
            loc = j.get("location") or {}
            out.append(Posting(
                id=f"smart-{slug}-{job_id}", source="ats_smartrecruiters",
                company_name=_company(board, (j.get("company") or {}).get("name")), title=_text(j["name"]),
                url=f"https://jobs.smartrecruiters.com/{board.key}/{job_id}",
                locations=_places([loc.get("fullLocation") or ", ".join(
                    str(loc[k]) for k in ("city", "region", "country") if loc.get(k))]),
                workplace="Remote" if loc.get("remote") else "Hybrid" if loc.get("hybrid") else None,
                employment_type=_text((j.get("typeOfEmployment") or {}).get("label")),
                department=_text((j.get("department") or {}).get("label")), posted_at=j.get("releasedDate"),
            ))
        offset += len(content)
        if not content or offset >= total:
            break
    return Listing(out, complete=complete and len(seen) == total)


_WORKDAY_POSTED = re.compile(r"posted\s+(today|yesterday|(\d+)\s+days?\s+ago)", re.I)
_WORKDAY_COPY = re.compile(r"(\d)-\d$")  # the -1 a company adds when one job sits on two of its sites


def _workday_job_id(rest: str) -> str:
    """Backend's key: title slug + requisition id, without the copy suffix."""
    last = rest.rstrip("/").rsplit("/", 1)[-1]
    slug, _, req = last.rpartition("_")
    req = _WORKDAY_COPY.sub(r"\1", req)
    return f"{slug}_{req}".lower() if slug else req.lower()


def _workday_posted(text: Any, now: datetime) -> Optional[str]:
    m = _WORKDAY_POSTED.search(str(text or ""))  # "Posted 30+ Days Ago" says too little: no match
    if not m:
        return None
    word = m.group(1).lower()
    days = 0 if word == "today" else 1 if word == "yesterday" else int(m.group(2))
    return (now - timedelta(days=days)).date().isoformat()


def read_workday(board: Board, http: Any, known: Dict[str, Optional[str]]) -> Listing:
    host, site = board.key.split("/", 1)
    tenant, now = host.split(".")[0].lower(), datetime.now(timezone.utc)
    out, seen = [], set()
    total: Optional[int] = None
    offset, complete = 0, True
    while True:
        data = _json(http.post(f"https://{host}/wday/cxs/{tenant}/{site}/jobs", headers={"Accept": "application/json"},
                               json={"appliedFacets": {}, "limit": WORKDAY_PAGE, "offset": offset, "searchText": ""}),
                     gone=(404, 422) if offset == 0 else ())
        posts = data.get("jobPostings") if isinstance(data, dict) else None
        if not isinstance(posts, list):
            if total is None:
                raise ReadFailed("not a Workday listing")
            complete = False
            break
        if total is None:
            total = int(data.get("total") or 0)  # sent on the first page only
        for j in posts:
            path = str(j.get("externalPath") or "") if isinstance(j, dict) else ""
            rest = path.split("/job/", 1)[-1].strip("/")
            if not path.startswith("/job/") or not rest or not _text(j.get("title")):
                continue
            posting_id = f"wd-{tenant}-{_workday_job_id(rest)}"
            if posting_id in seen:
                continue
            seen.add(posting_id)
            out.append(Posting(
                id=posting_id, source="ats_workday", company_name=_company(board), title=_text(j["title"]),
                url=f"https://{host}/en-US/{site}{path}", locations=_places([j.get("locationsText")]),
                posted_at=_workday_posted(j.get("postedOn"), now),
            ))
        offset += len(posts)
        if len(posts) < WORKDAY_PAGE or offset >= total:
            break
        if offset >= WORKDAY_CEILING:
            complete = False
            break
    return Listing(out, complete=complete and total < WORKDAY_CEILING)


def read_oracle(board: Board, http: Any, known: Dict[str, Optional[str]]) -> Listing:
    host, site = board.key.split("/", 1)
    prefix, out, seen = host.split(".")[0].lower(), [], set()
    total: Optional[int] = None
    offset, complete = 0, True
    while True:
        if offset >= ORACLE_MAX:
            complete = False
            break
        data = _json(http.get(
            f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
            f"&expand=requisitionList.secondaryLocations&finder=findReqs;siteNumber={site},"
            f"limit={ORACLE_PAGE},offset={offset},sortBy=POSTING_DATES_DESC",
            headers={"Accept": "application/json"}), gone=(400, 404) if offset == 0 else ())
        items = data.get("items") if isinstance(data, dict) else None
        reqs = items[0].get("requisitionList") if isinstance(items, list) and items and isinstance(items[0], dict) else None
        if not isinstance(reqs, list):
            if total is None:
                raise ReadFailed("not an Oracle listing")
            complete = False
            break
        if total is None:
            total = int(items[0].get("TotalJobsCount") or 0)
        for req in reqs:
            req_id = str(req.get("Id") or "") if isinstance(req, dict) else ""
            if not req_id or req_id in seen or not _text(req.get("Title")):
                continue
            seen.add(req_id)
            out.append(Posting(
                id=f"or-{prefix}-{req_id}", source="ats_oracle", company_name=_company(board), title=_text(req["Title"]),
                url=f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{req_id}",
                locations=_places([req.get("PrimaryLocation")] + [loc.get("Name") for loc in req.get("secondaryLocations")
                                                                 or [] if isinstance(loc, dict)]),
                workplace=_text(req.get("WorkplaceType")), posted_at=req.get("PostedDate") or None,
            ))
        offset += len(reqs)
        if len(reqs) < ORACLE_PAGE or offset >= total:
            break
    return Listing(out, complete=complete and offset >= (total or 0))


_ICIMS_JOB = re.compile(r"<loc>\s*https?://[^<]+?/jobs/(\d+)/([^/<]+)/job\s*</loc>\s*(?:<lastmod>\s*([^<]+?)\s*</lastmod>)?",
                        re.I)
_LD_JSON = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)


def _robots_rules(text: str) -> List[tuple]:
    """(allow, regex) rules of the `User-agent: *` group (RFC 9309, with * and $)."""
    rules, applies, in_group = [], False, False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        name, value = (part.strip() for part in line.split(":", 1))
        name = name.lower()
        if name == "user-agent":
            applies = (applies if in_group else False) or value == "*"
            in_group = True
            continue
        in_group = False
        if applies and name in ("allow", "disallow") and value:
            pattern = re.escape(value).replace(r"\*", ".*")
            pattern = pattern[:-2] + "$" if pattern.endswith(r"\$") else pattern
            rules.append((name == "allow", len(value), re.compile(pattern)))
    return rules


def _robots_allows(rules: List[tuple], path: str) -> bool:
    best = max((rule for rule in rules if rule[2].match(path)), key=lambda r: (r[1], r[0]), default=None)
    return best is None or best[0]


def _posting_ld(html: str) -> Optional[dict]:
    for block in _LD_JSON.findall(html or ""):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def _ld_places(posting: dict) -> List[str]:
    places = posting.get("jobLocation") or []
    out = []
    for place in places if isinstance(places, list) else [places]:
        address = place.get("address") if isinstance(place, dict) else None
        if not isinstance(address, dict):
            continue
        country = address.get("addressCountry")
        country = country.get("name") if isinstance(country, dict) else country
        parts = [address.get("addressLocality"), address.get("addressRegion")]
        if country and str(country).upper() not in ("US", "USA", "UNITED STATES"):
            parts.append(country)
        out.append(", ".join(str(p) for p in parts if p))
    return _places(out)


def _same_instant(a: Optional[str], b: Optional[str]) -> bool:
    if a is None or b is None:
        return a == b
    try:
        return datetime.fromisoformat(a) == datetime.fromisoformat(b)
    except ValueError:
        return a == b


def read_icims(board: Board, http: Any, known: Dict[str, Optional[str]]) -> Listing:
    host = board.key
    prefix = host.split(".")[0].lower()
    robots = http.get(f"https://{host}/robots.txt")
    if robots.status_code == 429 or robots.status_code >= 500:
        raise ReadFailed(f"robots.txt answered {robots.status_code}")  # RFC 9309: treat as disallow-all for now
    rules = _robots_rules(robots.text) if robots.status_code == 200 else []
    if not (_robots_allows(rules, "/sitemap.xml") and _robots_allows(rules, "/jobs/1/job")):
        raise NotABoard("robots.txt disallows the sitemap or job pages")
    sitemap = http.get(f"https://{host}/sitemap.xml")
    if sitemap.status_code != 200:
        raise ReadFailed(f"sitemap answered {sitemap.status_code}")
    out, keep, seen = [], set(), set()
    opened, complete = 0, True
    for job_id, _, lastmod in _ICIMS_JOB.findall(sitemap.text):
        if job_id in seen:
            continue
        seen.add(job_id)
        posting_id, lastmod = f"ic-{prefix}-{job_id}", lastmod or None
        if posting_id in known and _same_instant(known[posting_id], lastmod):
            keep.add(posting_id)
            continue
        path = f"/jobs/{job_id}/job?in_iframe=1"
        if opened >= ICIMS_PAGES_PER_READ or not _robots_allows(rules, path):
            if opened >= ICIMS_PAGES_PER_READ:
                complete = False  # the rest are read next time
            if posting_id in known:
                keep.add(posting_id)
            continue
        opened += 1
        page = http.get(f"https://{host}{path}")
        posting = _posting_ld(page.text) if page.status_code == 200 else None
        title = _text((posting or {}).get("title"))
        if not title:
            complete = False  # listed but unreadable: no closures, read again soon
            if posting_id in known:
                keep.add(posting_id)
            continue
        out.append(Posting(
            id=posting_id, source="ats_icims", company_name=_company(board, (posting.get("hiringOrganization") or {}).get("name")),
            title=title, url=f"https://{host}/jobs/{job_id}/job", locations=_ld_places(posting),
            employment_type=_text(posting.get("employmentType")), posted_at=posting.get("datePosted"),
            source_updated_at=lastmod,
        ))
    return Listing(out, complete=complete, keep=keep)


READERS: Dict[str, Reader] = {
    "greenhouse": read_greenhouse,
    "lever": read_lever,
    "ashby": read_ashby,
    "smartrecruiters": read_smartrecruiters,
    "workday": read_workday,
    "oracle": read_oracle,
    "icims": read_icims,
}
