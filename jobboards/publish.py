"""The intern and new-grad postings a crawl writes, as job_bank rows.

The bank keeps every posting. job_bank is what applyfinch.com reads; the bank
writes these rows into it through bank_publish() (sql/20261014_bank_publish.sql)
in the same transaction as the bank write, so a new posting is on the site as
soon as its board is read.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from jobboards.readers import Posting

# ponytail: copied from Backend develop (job_bank/early_career.py, ingest.is_early_career,
# ingest.make_row, interview_odds._seniority) on 2026-10-14. Two copies drift: change both,
# or delete this once Backend's JOB_BANK_BOARDS_BANK ingest publishes the bank itself.

_TRAINEE = (r"trainees?|in[- ]training"
            r"|(?:development|leadership|rotational|rotation|accelerator|training|trainee) program(?:me)?s?")
_NEW_GRADS = (r"new[- ]?grad(?:uate)?s?|college grad(?:uate)?s?|graduates"
              r"|(?:recent|upcoming)(?: (?:and|or|&) (?:recent|upcoming))? (?:college )?grad(?:uate)?s?")
_JUNIOR_MANAGER = r"(?:associate|assistant|junior|jr\.?) (?:account|product|project|territory|category) manager"
_ENTRY = re.compile(rf"\b(?:{_TRAINEE}|{_NEW_GRADS}|{_JUNIOR_MANAGER})\b", re.I)
_STATED = re.compile(rf"\b(?:{_TRAINEE}|{_NEW_GRADS})\b", re.I)
_TRACK_SENIOR = re.compile(r"\b(?:senior|sr\.?|director|vp|vice president|chief|head of|lead|ii|iii|iv)\b"
                           r"|\b[2-4]\s*$", re.I)
_RANK = re.compile(r"\b(?:staff|principal)\b", re.I)
_PROGRAMS = (r"(?:(?:development|leadership|rotational|rotation|accelerator|training|trainee|graduate) program(?:me)?s?"
             r"|early[- ]careers?(?: program(?:me)?s?)?|campus|university|new[- ]?grad(?:uate)?s?"
             r"|college grad(?:uate)?s?)")
_STAFF = r"(?:recruit\w*|talent|manager|director|lead|head|coordinator|partner|advisor|administrator|relations)"
_PROGRAM_STAFF = re.compile(rf"\b{_PROGRAMS} {_STAFF}\b", re.I)
_OPENS_ON_ROLE = re.compile(r"^\W*(?:manager|director|head|lead|coordinator)\s*[,:]", re.I)

EARLY_CAREER_RE = re.compile(
    r"\b(intern(ship)?s?|co[- ]?op|new[- ]?grad(uate)?|entry[- ]level"
    r"|university (grad(uate)?|hire)|campus hire|early[- ]career|junior"
    r"|(new |recent )?college grad(uate)?s?|recent grad(uate)?s?"
    r"|trainees?|development program(me)?"
    r"|grad(uate)? (program(me)?|hire|scheme|analyst|engineer)"
    r"|(engineer|analyst) (i|1))\b",
    re.IGNORECASE,
)
_SENIOR_TITLE_RE = re.compile(
    r"\b(senior|sr\.?|staff|principal|lead|manager|director|head|vp|vice president"
    r"|(engineer|analyst) (iii|iv|v|3|4|5))\b", re.IGNORECASE,
)
_INTERNSHIP_EMPLOYMENT_RE = re.compile(r"\b(intern(ship)?|co[- ]?op)\b", re.IGNORECASE)
_INTERNSHIP_PROGRAM_MANAGER_RE = re.compile(
    r"(?:\b(?:internship|co[- ]?op)\s+(?:program\s+)?(?:manager|director|lead|head)\b"
    r"|\b(?:manager|director|lead|head)\b.{0,35}\b(?:internship|co[- ]?op)\s+program\b)",
    re.IGNORECASE,
)

_INTERN = re.compile(r"\bintern(ship)?\b|\bco.?op\b|summer analyst|summer associate", re.I)
_SENIOR = re.compile(
    r"\bsenior\b|\bsr\.?\b|\bstaff\b|principal|\blead\b|\bdirector\b|\bhead of\b"
    r"|(?<!product )(?<!program )\bmanager\b|\bvp\b|\bexecutive\b|distinguished|architect"
    r"|\b(ii|iii|iv|2|3|4)\b(?!\s*(week|month|day))"
    r"|(engineer|developer|sde|swe|analyst|consultant|scientist)\s*-?\s*(ii|iii|iv|2|3|4)\b",
    re.I,
)
_NEWGRAD = re.compile(
    r"new ?grad|university grad(uate)?|campus|early career|entry.?level|graduate program"
    r"|\bgrad\b|associate (software|engineer)|\b(swe|sde|engineer)\s*(i|1)\b",
    re.I,
)

# Backend's ats_platform only names these; Oracle and iCIMS rows carry none.
_PLATFORMS = {"greenhouse", "lever", "workday", "ashby", "smartrecruiters"}


def runs_a_program(title: str) -> bool:
    return bool(_PROGRAM_STAFF.search(title or ""))


def is_entry_track(title: str) -> bool:
    text = title or ""
    return (bool(_ENTRY.search(text)) and not _TRACK_SENIOR.search(text)
            and not (_RANK.search(text) and not _STATED.search(text))
            and not runs_a_program(text) and not _OPENS_ON_ROLE.search(text))


def is_early_career(title: str, employment: str = "") -> bool:
    title, employment = title or "", employment or ""
    if _INTERNSHIP_PROGRAM_MANAGER_RE.search(title):
        return False
    if _INTERNSHIP_EMPLOYMENT_RE.search(title) or _INTERNSHIP_EMPLOYMENT_RE.search(employment):
        return True
    if runs_a_program(title):
        return False
    if is_entry_track(title):
        return True
    if _SENIOR_TITLE_RE.search(title):
        return False
    return bool(EARLY_CAREER_RE.search(title))


def seniority(title: str) -> str:
    if _INTERN.search(title):
        return "intern"
    if _NEWGRAD.search(title):
        return "new_grad"
    if is_entry_track(title):
        return "entry"
    if _SENIOR.search(title):
        return "senior"
    return "entry"


def job_bank_rows(postings: List[Posting]) -> List[Dict[str, Any]]:
    """The postings the board shows, shaped like Backend's make_row (same caps)."""
    rows = []
    for p in postings:
        title, company, url = (p.title or "").strip(), (p.company_name or "").strip(), (p.url or "").strip()
        employment = (p.employment_type or "").strip()
        if not (p.id and title and company and url) or not is_early_career(title, employment):
            continue
        platform = p.source.removeprefix("ats_")
        rows.append({
            "source": p.source, "source_id": p.id[:300], "company_name": company[:200], "title": title[:300],
            "locations": [loc for loc in p.locations if loc][:10], "url": url[:2000],
            "apply_url": (p.apply_url or url)[:2000],
            "category": ("internship" if _INTERNSHIP_EMPLOYMENT_RE.search(employment) else employment or "ats")[:100],
            "workplace": (p.workplace or "").strip()[:100] or None, "date_posted": p.posted_at,
            "ats_platform": platform if platform in _PLATFORMS else None, "seniority": seniority(title),
        })
    return rows
