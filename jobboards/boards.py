"""What a job board is, and which public links prove one exists.

A board is one employer's listing on one job platform. It is only ever made
from a link on that platform's exact host; a company name never becomes a
guessed slug.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qs, unquote, urlsplit

PROVIDERS = ("greenhouse", "lever", "ashby", "smartrecruiters", "workday", "oracle", "icims")


@dataclass(frozen=True)
class Board:
    provider: str
    key: str  # slug, host, or host/site as first found
    company_name: str = field(default="", compare=False)  # from the evidence, when it named one

    @property
    def id(self) -> str:
        return f"{self.provider}:{self.key.lower()}"


_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_WORKDAY_HOST = re.compile(r"^[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com$")
_LOCALE = re.compile(r"^[a-z]{2}(-[A-Za-z]{2})?$")
_ICIMS_NOT_PORTALS = {"www", "api", "cdn", "assets", "static", "login", "developer", "community", "support", "info"}
# Path words on the slug-in-path platforms that are pages, not employers.
_NOT_SLUGS = {"embed", "api", "v0", "v1", "jobs", "sitemap.xml", "robots.txt", "favicon.ico", "oops", "static", "assets"}

_PATH_SLUG_HOSTS = {
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.ashbyhq.com": "ashby",
    "jobs.smartrecruiters.com": "smartrecruiters",
    "careers.smartrecruiters.com": "smartrecruiters",
}


def _slug(value: str) -> Optional[str]:
    value = unquote(value or "").strip()
    return value if _SLUG.fullmatch(value) and value.lower() not in _NOT_SLUGS else None


def board_from_url(url: str) -> Optional[Board]:
    try:
        parts = urlsplit(str(url or "").strip())
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password \
            or port not in (None, 80, 443):
        return None
    path = [unquote(s) for s in parts.path.split("/") if s]

    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io") and path[:1] == ["embed"]:
        slug = _slug((parse_qs(parts.query).get("for") or [""])[0])
        return Board("greenhouse", slug) if slug else None
    if host == "boards-api.greenhouse.io":
        slug = _slug(path[2]) if path[:2] == ["v1", "boards"] and len(path) > 2 else None
        return Board("greenhouse", slug) if slug else None
    if host == "api.lever.co":
        slug = _slug(path[2]) if path[:2] == ["v0", "postings"] and len(path) > 2 else None
        return Board("lever", slug) if slug else None
    if host in _PATH_SLUG_HOSTS:
        slug = _slug(path[0]) if path else None
        return Board(_PATH_SLUG_HOSTS[host], slug) if slug else None

    if _WORKDAY_HOST.fullmatch(host):
        if path[:2] == ["wday", "cxs"]:
            site = path[3] if len(path) > 3 else ""
        else:
            rest = path[1:] if path and _LOCALE.fullmatch(path[0]) else path
            site = rest[0] if rest else ""
        site = _slug(site) if site not in ("job", "details", "login") and "." not in site else None  # llms.txt, favicon.png
        return Board("workday", f"{host}/{site}") if site else None

    if host.endswith(".oraclecloud.com") and "sites" in path:
        i = path.index("sites")
        if path[:2] == ["hcmUI", "CandidateExperience"] and len(path) > i + 1:
            site = _slug(path[i + 1])
            return Board("oracle", f"{host}/{site}") if site else None
        return None

    if host.endswith(".icims.com") and host.count(".") == 2:
        sub = host.split(".")[0]
        public = sub not in _ICIMS_NOT_PORTALS and not sub.startswith(("internal", "franchise"))  # staff-only portals
        return Board("icims", host) if public else None
    return None
