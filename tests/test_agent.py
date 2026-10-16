"""The AI posting finder: Claude searches the web for an employer's own postings, and
nothing it returns is used until code has checked it."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from jobboards import agent
from jobboards.http import Reply
from test_resolve import board_with, jobright_row, links


# --- checking a page ---

@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/jobs/view/1", "https://uk.indeed.com/viewjob?jk=1", "https://jobright.ai/jobs/info/x",
    "https://www.glassdoor.co.uk/job-listing/x", "https://builtin.com/job/x", "https://wellfound.com/jobs/1",
])
def test_job_boards_are_never_a_posting(url):
    assert agent.is_job_board(url)


@pytest.mark.parametrize("url", ["https://careers.ibm.com/job/1", "https://boards.greenhouse.io/spacex/jobs/1",
                                 "https://www.amazon.jobs/en/jobs/1"])
def test_employer_sites_and_ats_hosts_are_not_job_boards(url):
    assert not agent.is_job_board(url)


PAGE = b"<html><title>Software Engineering Intern | IBM Careers</title><h1>Software Engineering Intern</h1>" \
       b"<p>Austin, TX. Join IBM Research this summer.</p></html>"


class FakeHttp:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def get(self, url):
        self.asked.append(url)
        if url in self.pages:
            status, body, final = self.pages[url]
            return Reply(status, {}, body, final or url)
        return Reply(404, {}, b"", url)


def test_a_page_that_shows_the_role_and_the_employer_is_the_posting():
    http = FakeHttp({"https://careers.ibm.com/job/1": (200, PAGE, None)})
    assert agent.verify(http, "https://careers.ibm.com/job/1", "Software Engineer Intern", "IBM") == \
        "https://careers.ibm.com/job/1"


@pytest.mark.parametrize("pages, url, title, company", [
    ({}, "https://careers.ibm.com/job/1", "Software Engineer Intern", "IBM"),                       # 404
    ({"https://careers.ibm.com/job/1": (200, PAGE, None)}, "https://careers.ibm.com/job/1",
     "Data Scientist Intern", "IBM"),                                                                # another role
    ({"https://careers.ibm.com/job/1": (200, PAGE, None)}, "https://careers.ibm.com/job/1",
     "Software Engineer Intern", "Lockheed Martin"),                                                 # another employer
    ({"https://careers.ibm.com/job/1": (200, PAGE, "https://www.linkedin.com/jobs/view/9")},
     "https://careers.ibm.com/job/1", "Software Engineer Intern", "IBM"),                            # ends on a job board
    ({"https://careers.ibm.com/robots.txt": (200, b"User-agent: *\nDisallow: /job/", None),
      "https://careers.ibm.com/job/1": (200, PAGE, None)},
     "https://careers.ibm.com/job/1", "Software Engineer Intern", "IBM"),                            # robots.txt says no
    ({}, "ftp://careers.ibm.com/job/1", "Software Engineer Intern", "IBM"),
])
def test_anything_else_is_not(pages, url, title, company):
    assert agent.verify(FakeHttp(pages), url, title, company) is None


# --- asking Claude ---

def tool_use(**report):
    return SimpleNamespace(type="tool_use", name="report", input=report)


class FakeClient:
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.requests.append(request)
        return self.responses.pop(0)


def answer(*content, stop="tool_use"):
    return SimpleNamespace(content=list(content), stop_reason=stop)


ROLES = [{"title": "Software Engineer Intern", "locations": ["Austin, TX"]}]


def test_one_call_per_company_on_haiku_with_web_search_and_a_strict_report():
    # Prince, 10/16: "make the model cheap but enough for our work".
    client = FakeClient(answer(tool_use(careers_url="https://careers.ibm.com", postings=[{"role": 1, "url": ""}])))
    got = agent.ask(client, "IBM", "https://www.ibm.com", ROLES)
    assert got == {"careers_url": "https://careers.ibm.com", "postings": [{"role": 1, "url": ""}]}
    request = client.requests[0]
    assert request["model"] == "claude-haiku-5-5"
    # Haiku has the basic search tool (dynamic filtering is Opus/Sonnet only) and no server-side fallback.
    assert {"type": "web_search_20250305", "name": "web_search", "max_uses": 5} in request["tools"]
    assert "fallbacks" not in request and "betas" not in request
    assert request["output_config"] == {"effort": "medium"}  # low can skip the searches this job is
    report = next(t for t in request["tools"] if t.get("name") == "report")
    assert report["strict"] is True and report["input_schema"]["additionalProperties"] is False
    assert "tool_choice" not in request
    assert "IBM" in request["messages"][0]["content"] and "Software Engineer Intern" in request["messages"][0]["content"]


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-sonnet-5-5"])
def test_a_bigger_model_gets_the_filtering_search_and_refusal_fallbacks(monkeypatch, model):
    monkeypatch.setattr(agent, "MODEL", model)
    client = FakeClient(answer(tool_use(careers_url="", postings=[])))
    agent.ask(client, "IBM", "https://www.ibm.com", ROLES)
    request = client.requests[0]
    assert request["model"] == model
    assert {"type": "web_search_20260209", "name": "web_search", "max_uses": 5} in request["tools"]
    assert request["fallbacks"] == "default" and request["betas"] == ["server-side-fallback-2026-07-01"]


def test_a_paused_search_turn_is_resumed():
    paused = answer(SimpleNamespace(type="server_tool_use", name="web_search", input={}), stop="pause_turn")
    client = FakeClient(paused, answer(tool_use(careers_url="", postings=[])))
    assert agent.ask(client, "IBM", "https://www.ibm.com", ROLES) == {"careers_url": "", "postings": []}
    assert client.requests[1]["messages"][1] == {"role": "assistant", "content": paused.content}


def test_an_answer_without_the_report_is_asked_for_it_once():
    client = FakeClient(answer(SimpleNamespace(type="text", text="Found it."), stop="end_turn"),
                        answer(tool_use(careers_url="", postings=[])))
    assert agent.ask(client, "IBM", "https://www.ibm.com", ROLES) == {"careers_url": "", "postings": []}
    assert "report" in client.requests[1]["messages"][-1]["content"]


def test_a_refusal_is_no_answer():
    client = FakeClient(answer(SimpleNamespace(type="text", text=""), stop="refusal"))
    assert agent.ask(client, "IBM", "https://www.ibm.com", ROLES) is None


# --- the lane, against a real throwaway Postgres ---

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


def run(bank, client, http=None, **kw):
    return agent.find(bank, http or FakeHttp({}), client, month="2026-10", **kw)


def test_a_board_it_finds_is_added_for_the_crawl_and_resolve_to_use(bank):
    jobright_row(bank, "Software Engineer Intern", company="Zeta", website="https://www.zeta.io")
    client = FakeClient(answer(tool_use(careers_url="https://boards.greenhouse.io/zeta",
                                        postings=[{"role": 1, "url": "https://boards.greenhouse.io/zeta/jobs/7"}])))
    stats = run(bank, client)
    board = bank.query("select * from bank_boards")[0]
    assert (board["id"], board["company_name"], board["evidence_source"]) == ("greenhouse:zeta", "Zeta", "agent:zeta.io")
    assert stats["companies_asked"] == 1 and stats["boards_found"] == 1
    assert not [l for l in links(bank).values() if l["url"]]  # a known platform's posting is left to resolve


def test_a_posting_on_a_custom_career_site_is_linked_once_its_page_checks_out(bank):
    job = jobright_row(bank, "Software Engineer Intern", company="IBM", website="https://www.ibm.com")
    http = FakeHttp({"https://careers.ibm.com/job/1": (200, PAGE, None)})
    client = FakeClient(answer(tool_use(careers_url="https://careers.ibm.com",
                                        postings=[{"role": 1, "url": "https://careers.ibm.com/job/1"}])))
    stats = run(bank, client, http)
    link = links(bank)[str(job)]
    assert link["url"] == "https://careers.ibm.com/job/1" and link["posting_id"] is None
    assert stats["linked"] == 1


def test_a_link_that_fails_the_check_is_not_saved(bank):
    job = jobright_row(bank, "Software Engineer Intern", company="IBM", website="https://www.ibm.com")
    client = FakeClient(answer(tool_use(careers_url="", postings=[{"role": 1, "url": "https://www.linkedin.com/jobs/1"},
                                                                  {"role": 7, "url": "https://careers.ibm.com/x"}])))
    stats = run(bank, client)
    assert str(job) not in {k for k, v in links(bank).items() if v["url"]}
    assert stats["rejected"] == 2  # a job board's link, and a role number that isn't in the list


def test_an_employer_is_asked_once_a_month(bank):
    jobright_row(bank, "Software Engineer Intern", company="IBM", website="https://www.ibm.com")
    client = FakeClient(answer(tool_use(careers_url="", postings=[])))
    run(bank, client)
    assert run(bank, client)["companies_asked"] == 0 and len(client.requests) == 1


def test_an_employer_whose_board_the_crawl_reads_is_left_to_resolve(bank):
    jobright_row(bank, "Quantum Chef Intern")  # Acme, which has a read board
    board_with(bank, "Software Engineer Intern")
    client = FakeClient()
    assert run(bank, client)["companies_asked"] == 0


def test_the_daily_cap_stops_it(bank):
    jobright_row(bank, "Software Engineer Intern", company="IBM", website="https://www.ibm.com")
    bank.query("insert into bank_runs (kind, stats) values ('agent', '{\"companies_asked\": 5}')")
    stats = run(bank, FakeClient(), daily_companies=5)
    assert stats["companies_asked"] == 0 and stats["daily_cap_reached"] == 1


def test_its_links_are_checked_again_after_three_days_and_dropped_when_the_page_is_gone(bank):
    gone = jobright_row(bank, "Software Engineer Intern", company="IBM", website="https://www.ibm.com",
                        link_kind="posting")
    kept = jobright_row(bank, "Software Engineer Intern", company="IBM", website="https://www.ibm.com",
                        link_kind="posting")
    for job, url in ((gone, "https://careers.ibm.com/job/0"), (kept, "https://careers.ibm.com/job/1")):
        bank.query("insert into job_bank_jobright_links (job_id, url, tried_at) values (%s, %s, now() - interval '4 days')",
                   (job, url))
    http = FakeHttp({"https://careers.ibm.com/job/1": (200, PAGE, None)})
    stats = run(bank, FakeClient(), http)
    assert stats["links_dropped"] == 1
    assert links(bank)[str(gone)]["url"] is None and links(bank)[str(kept)]["url"] == "https://careers.ibm.com/job/1"


def test_a_link_whose_page_cant_be_checked_right_now_is_kept_and_checked_again_later(bank):
    job = jobright_row(bank, "Software Engineer Intern", company="IBM", website="https://www.ibm.com",
                       link_kind="posting")
    bank.query("insert into job_bank_jobright_links (job_id, url, tried_at) values (%s, %s, now() - interval '4 days')",
               (job, "https://careers.ibm.com/job/1"))
    down = FakeHttp({"https://careers.ibm.com/job/1": (503, b"", None)})
    assert run(bank, FakeClient(), down)["links_dropped"] == 0
    link = links(bank)[str(job)]
    assert link["url"] == "https://careers.ibm.com/job/1"
    assert bank.query("select tried_at < now() - interval '3 days' as due from job_bank_jobright_links")[0]["due"]
