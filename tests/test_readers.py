import json
from pathlib import Path

import pytest

from jobboards.boards import Board
from jobboards.http import Reply
from jobboards.readers import NotABoard, ReadFailed, READERS

FIX = Path(__file__).parent / "fixtures"


class FakeHttp:
    """Answers by the first registered prefix the URL starts with (POST bodies keyed by offset)."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def _answer(self, method, url, json_body=None):
        self.calls.append((method, url, json_body))
        for prefix, answer in self.routes.items():
            if url.startswith(prefix):
                if callable(answer):
                    answer = answer(url, json_body)
                status, body = answer if isinstance(answer, tuple) else (200, answer)
                return Reply(status, {}, body if isinstance(body, bytes) else body.encode())
        return Reply(404, {}, b"not found")

    def get(self, url, **kw):
        return self._answer("GET", url)

    def post(self, url, json=None, **kw):
        return self._answer("POST", url, json)


def fixture(name):
    return (FIX / name).read_text()


def read(provider, key, routes, known=None, company=""):
    http = FakeHttp(routes)
    return READERS[provider](Board(provider, key, company), http, known or {}), http


def test_greenhouse_lists_every_job_with_job_bank_ids():
    listing, _ = read("greenhouse", "CrossRiverBank", {
        "https://boards-api.greenhouse.io/v1/boards/CrossRiverBank/jobs": fixture("greenhouse.json")})
    assert listing.complete and len(listing.postings) == 3
    p = listing.postings[0]
    assert p.id.startswith("gh-crossriverbank-") and p.source == "ats_greenhouse"
    assert p.company_name == "Cross River" and p.locations == ["Fort Lee, New Jersey, United States"]
    assert p.url.startswith("https://") and p.posted_at.startswith("2026-07-29")


def test_lever_keeps_commitment_department_and_all_locations():
    listing, _ = read("lever", "zoox", {"https://api.lever.co/v0/postings/zoox": fixture("lever.json")},
                      company="Zoox")
    p = listing.postings[0]
    assert listing.complete and len(listing.postings) == 3
    assert p.id.startswith("lever-zoox-") and p.company_name == "Zoox"
    assert p.employment_type == "Full-time" and p.department == "Executive Office"
    assert p.locations == ["Foster City, CA"] and p.workplace == "hybrid" and p.posted_at.startswith("2026-")


def test_ashby_takes_secondary_locations():
    listing, _ = read("ashby", "ramp", {"https://api.ashbyhq.com/posting-api/job-board/ramp": fixture("ashby.json")})
    p = listing.postings[0]
    assert p.id.startswith("ashby-ramp-") and "Remote (US)" in p.locations and p.locations[0] == "New York, NY (HQ)"


def test_an_empty_board_is_a_complete_empty_listing():
    listing, _ = read("ashby", "klaviyo", {"https://api.ashbyhq.com/posting-api/job-board/klaviyo":
                                           fixture("ashby_empty.json")})
    assert listing.complete and listing.postings == []


def test_unknown_board_is_not_a_board():
    with pytest.raises(NotABoard):
        read("greenhouse", "nobody", {})


@pytest.mark.parametrize("body", ["<html>maintenance</html>", "{}", "[]"])
def test_a_wrong_shaped_answer_is_a_failed_read_not_an_empty_board(body):
    with pytest.raises(ReadFailed):
        read("greenhouse", "x", {"https://boards-api.greenhouse.io/v1/boards/x/jobs": body})


def test_server_error_is_a_failed_read():
    with pytest.raises(ReadFailed):
        read("lever", "x", {"https://api.lever.co/v0/postings/x": (500, "oops")})


def _smart_pages(total_override=None):
    pages = {0: json.loads(fixture("smartrecruiters_0.json")), 2: json.loads(fixture("smartrecruiters_2.json"))}

    def answer(url, _):
        offset = int(url.split("offset=")[1].split("&")[0])
        page = dict(pages.get(offset, {"offset": offset, "limit": 100, "totalFound": 4, "content": []}))
        page["totalFound"] = 4 if total_override is None or offset == 0 else total_override
        return json.dumps(page)
    return answer


def test_smartrecruiters_pages_until_total(monkeypatch):
    monkeypatch.setattr("jobboards.readers.SMART_PAGE", 2)
    listing, http = read("smartrecruiters", "ServiceNow",
                         {"https://api.smartrecruiters.com/v1/companies/ServiceNow/postings": _smart_pages()})
    assert listing.complete and len(listing.postings) == 4 and len(http.calls) == 2
    p = listing.postings[0]
    assert p.id.startswith("smart-servicenow-") and p.employment_type == "Intern"
    assert p.company_name == "ServiceNow" and p.workplace == "Hybrid"
    assert p.url.startswith("https://jobs.smartrecruiters.com/ServiceNow/")


def test_smartrecruiters_total_changing_mid_read_is_incomplete(monkeypatch):
    monkeypatch.setattr("jobboards.readers.SMART_PAGE", 2)
    listing, _ = read("smartrecruiters", "ServiceNow",
                      {"https://api.smartrecruiters.com/v1/companies/ServiceNow/postings": _smart_pages(5)})
    assert not listing.complete


def _workday(total):
    pages = {0: json.loads(fixture("workday_0.json")), 2: json.loads(fixture("workday_2.json"))}

    def answer(url, body):
        page = dict(pages.get(body["offset"], {"total": 0, "jobPostings": []}))
        page["total"] = total if body["offset"] == 0 else 0  # Workday sends the total on the first page only
        return json.dumps(page)
    return answer


def test_workday_pages_through_a_site(monkeypatch):
    monkeypatch.setattr("jobboards.readers.WORKDAY_PAGE", 2)
    key = "nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"
    listing, http = read("workday", key, {
        "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs": _workday(4)},
        company="NVIDIA")
    assert listing.complete and len(listing.postings) == 4
    p = listing.postings[0]
    assert p.id == "wd-nvidia-senior-staff-software-engineer---agentic-workflows_jr2027353"
    assert p.url == ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite"
                     "/job/US-CA-Santa-Clara/Senior-Staff-Software-Engineer---Agentic-Workflows_JR2027353")
    assert p.locations == ["US, CA, Santa Clara"] and p.posted_at  # "Posted Today" -> a date


def test_workday_at_its_2000_result_ceiling_is_incomplete(monkeypatch):
    monkeypatch.setattr("jobboards.readers.WORKDAY_PAGE", 2)
    key = "nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"
    listing, _ = read("workday", key, {
        "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs": _workday(2000)})
    assert not listing.complete


def test_oracle_reads_requisitions():
    key = "edbz.fa.us2.oraclecloud.com/CX"
    body = json.loads(fixture("oracle.json"))
    body["items"][0]["TotalJobsCount"] = 3
    listing, http = read("oracle", key, {"https://edbz.fa.us2.oraclecloud.com/hcmRestApi/": json.dumps(body)},
                         company="Texas Instruments")
    assert listing.complete and len(listing.postings) == 3
    p = listing.postings[0]
    assert p.id == "or-edbz-25009749" and p.title == "Global Security Operations Center (GSOC) Operator"
    assert p.locations == ["Dallas, TX, United States"] and p.posted_at == "2026-10-14"
    assert p.url == "https://edbz.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/25009749"
    assert "siteNumber=CX" in http.calls[0][1]


ICIMS = "https://careers-gdms.icims.com"


def _icims_routes(robots="User-agent: *\nDisallow: /jobs/*login\n"):
    return {f"{ICIMS}/robots.txt": robots, f"{ICIMS}/sitemap.xml": fixture("icims_sitemap.xml"),
            f"{ICIMS}/jobs/": fixture("icims_job.html")}


def test_icims_reads_new_postings_from_their_pages():
    listing, http = read("icims", "careers-gdms.icims.com", _icims_routes())
    assert listing.complete and len(listing.postings) == 2  # /jobs/intro is not a posting
    p = listing.postings[0]
    assert p.id == "ic-careers-gdms-75473" and p.title == "Network Systems Engineer"
    assert p.locations == ["Pittsfield, MA"] and p.company_name == "General Dynamics Mission Systems, Inc"
    assert p.source_updated_at == "2026-10-14T14:14:41-04:00"


def test_icims_keeps_unchanged_postings_without_opening_them():
    known = {"ic-careers-gdms-75473": "2026-10-14T14:14:41-04:00", "ic-careers-gdms-74677": "2026-10-14T14:00:40-04:00"}
    listing, http = read("icims", "careers-gdms.icims.com", _icims_routes(), known=known)
    assert listing.postings == [] and listing.keep == set(known) and listing.complete
    assert not any("/jobs/7" in url for _, url, _ in http.calls)


def test_icims_respects_robots():
    http = FakeHttp(_icims_routes("User-agent: *\nDisallow: /jobs/\n"))
    with pytest.raises(NotABoard):
        READERS["icims"](Board("icims", "careers-gdms.icims.com"), http, {})
    assert not any("sitemap" in url for _, url, _ in http.calls)


def test_icims_robots_wildcards_block_only_what_they_match():
    from jobboards.readers import _robots_allows, _robots_rules
    rules = _robots_rules("User-agent: *\nDisallow: /jobs/*login\nDisallow: /*.pdf$\n\nUser-agent: other\nDisallow: /\n")
    assert _robots_allows(rules, "/jobs/1/job?in_iframe=1")
    assert not _robots_allows(rules, "/jobs/1/login")
    assert not _robots_allows(rules, "/x.pdf") and _robots_allows(rules, "/x.pdf?y")


def test_icims_page_cap_leaves_the_read_incomplete(monkeypatch):
    monkeypatch.setattr("jobboards.readers.ICIMS_PAGES_PER_READ", 1)
    listing, _ = read("icims", "careers-gdms.icims.com", _icims_routes())
    assert len(listing.postings) == 1 and not listing.complete


def test_hash_ignores_dates_so_relative_dates_do_not_churn():
    listing, _ = read("lever", "zoox", {"https://api.lever.co/v0/postings/zoox": fixture("lever.json")})
    p = listing.postings[0]
    from dataclasses import replace
    assert replace(p, posted_at="1999-01-01").content_hash() == p.content_hash()
    assert replace(p, title=p.title + "!").content_hash() != p.content_hash()
