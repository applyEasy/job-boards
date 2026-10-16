"""Finding the employer's own posting for a Jobright row that opens only a homepage."""

from unittest.mock import Mock

import pytest

from jobboards.resolve import best_match, resolve


def posting(n, title, locations=("Austin, TX",)):
    return {"id": f"gh-acme-{n}", "title": title, "locations": list(locations),
            "url": f"https://boards.greenhouse.io/acme/jobs/{n}"}


# --- the match: precision first; a wrong posting is worse than the homepage ---

@pytest.mark.parametrize("jobright, employer", [
    ("Software Engineer Intern", "Software Engineer Intern"),
    ("Software Engineering Intern", "Software Engineer, Intern"),
    ("Internship: Software Engineering", "Software Engineering Internship"),
    # Jobright copies page furniture into a few titles (10/15, Ascend Learning).
    ("Intern - Software Engineering Job Details / Ascend", "Intern - Software Engineering"),
    ("Co-op, Mechanical Engineering (Spring 2027)", "Mechanical Engineering Co-Op - Spring 2027"),
    ("Data Analyst Intern & Reporting", "Data Analyst Intern and Reporting"),
])
def test_the_same_role_matches(jobright, employer):
    assert best_match(jobright, ["Austin, TX"], [posting(1, employer)])["id"] == "gh-acme-1"


@pytest.mark.parametrize("jobright, employer", [
    ("Software Engineer Intern", "Senior Software Engineer"),
    ("Software Engineer Intern", "Software Engineer"),
    ("Software Engineer I", "Software Engineer II"),
    ("Software Engineer Intern - Summer 2026", "Software Engineer Intern - Summer 2027"),
    ("Mechanical Engineering Co-op", "Mechanical Engineering Intern"),
    ("Software Engineer Intern", "Software Engineer Intern - Machine Learning Platform Team"),
    ("Data Analyst Intern", "Data Scientist Intern"),
    ("Intern", "Intern"),
])
def test_another_role_does_not(jobright, employer):
    assert best_match(jobright, ["Austin, TX"], [posting(1, employer)]) is None


def test_a_title_jobright_took_from_a_job_details_page_finds_its_intern_posting():
    # Hubbell, 10/15: Jobright's copy lost "2027 Summer Intern:" and gained the page title's tail.
    hubbell = [posting(1, "2027 Summer Intern: HR - People Analytics", ["Greenville, SC, US, 29615"]),
               posting(2, "HR - People Analytics Manager", ["Greenville, SC, US, 29615"])]
    found = best_match("HR - People Analytics Job Details / Hubbell Incorporated", ["Greenville, SC, United States"], hubbell)
    assert found["id"] == "gh-acme-1"
    # ...but only in the same city, and only when the title shows it is such a copy.
    assert best_match("HR - People Analytics Job Details / Hubbell Incorporated", ["Boston, MA"], hubbell[:1]) is None
    assert best_match("HR - People Analytics", ["Greenville, SC"], hubbell[:1]) is None


def test_workdays_n_locations_is_no_city():
    # Booz Allen, 10/15: "Back End Developer" in McLean, listed by Workday as "2 Locations".
    assert best_match("Back End Developer", ["McLean, VA, United States"], [posting(1, "Back End Developer", ["2 Locations"])])


def test_the_city_picks_between_two_postings_of_one_title():
    found = best_match("Software Engineer Intern", ["Seattle, WA"],
                       [posting(1, "Software Engineer Intern", ["Austin, TX"]),
                        posting(2, "Software Engineer Intern", ["Seattle, Washington, United States"])])
    assert found["id"] == "gh-acme-2"


def test_a_posting_in_another_city_is_not_the_role():
    assert best_match("Software Engineer Intern", ["Seattle, WA"],
                      [posting(1, "Software Engineer Intern", ["Austin, TX"])]) is None


def test_a_place_on_either_side_missing_or_remote_does_not_block():
    assert best_match("Software Engineer Intern", [], [posting(1, "Software Engineer Intern")])
    assert best_match("Software Engineer Intern", ["Remote"], [posting(1, "Software Engineer Intern")])
    assert best_match("Software Engineer Intern", ["Hybrid in Austin"], [posting(1, "Software Engineer Intern")])


def test_the_exact_title_wins_over_a_close_one():
    found = best_match("Software Engineer Intern", ["Austin, TX"],
                       [posting(1, "Software Engineer Intern - Backend"), posting(2, "Software Engineer Intern")])
    assert found["id"] == "gh-acme-2"


# --- the step, against a real throwaway Postgres (BANK_TEST_DATABASE_URL) ---

def jobright_row(bank, title, company="Acme", website="https://www.acme.com", locations=("Austin, TX",),
                 link_kind="company_site", visible=True, hidden_reason=None):
    job_id = bank.query(
        "insert into job_bank (source, source_id, company_name, title, locations, url, company_website, date_posted) "
        "values ('jobright', gen_random_uuid()::text, %s, %s, %s, 'https://jobright.ai/jobs/info/x', %s, now()) "
        "returning id", (company, title, list(locations), website))[0]["id"]
    bank.query("insert into job_bank_read_v1 (job_id, link_kind, link_url, visible, hidden_reason) "
               "values (%s, %s, %s, %s, %s)", (job_id, link_kind, website if link_kind else None, visible, hidden_reason))
    return job_id


def board_with(bank, *titles, company="Acme", evidence="homepage:acme.com", closed=()):
    bank.query("insert into bank_boards (id, provider, key, company_name, evidence_url, evidence_source, status, "
               "last_ok_at) values ('greenhouse:acme', 'greenhouse', 'acme', %s, 'https://acme.com', %s, 'active', "
               "now())", (company, evidence))
    for n, title in enumerate(titles, 1):
        bank.query("insert into bank_postings (id, source, board_id, company_name, title, locations, url, "
                   "content_hash, closed_at) values (%s, 'ats_greenhouse', 'greenhouse:acme', %s, %s, "
                   "'{\"Austin, TX\"}', %s, 'h', %s)",
                   (f"gh-acme-{n}", company, title, f"https://boards.greenhouse.io/acme/jobs/{n}",
                    "2026-10-14" if title in closed else None))


def links(bank):
    return {str(r["job_id"]): r for r in bank.query("select * from job_bank_jobright_links")}


def test_a_homepage_row_gets_the_employers_posting(bank):
    job = jobright_row(bank, "Software Engineering Intern")
    board_with(bank, "Software Engineer Intern", "Senior Software Engineer")
    stats = resolve(bank, Mock(), homepage_budget_s=0)
    assert links(bank)[str(job)]["url"] == "https://boards.greenhouse.io/acme/jobs/1"
    assert links(bank)[str(job)]["posting_id"] == "gh-acme-1"
    assert stats["linked"] == 1


def test_a_board_named_for_the_company_counts_without_the_homepage(bank):
    job = jobright_row(bank, "Software Engineer Intern", company="Acme Inc", website=None)
    board_with(bank, "Software Engineer Intern", company="ACME", evidence="commoncrawl:x")
    resolve(bank, Mock(), homepage_budget_s=0)
    assert links(bank)[str(job)]["url"]


def test_a_row_hidden_for_having_no_link_is_looked_up_too(bank):
    job = jobright_row(bank, "Software Engineer Intern", link_kind=None, visible=False,
                       hidden_reason="no_employer_link")
    board_with(bank, "Software Engineer Intern")
    resolve(bank, Mock(), homepage_budget_s=0)
    assert links(bank)[str(job)]["url"]


def test_rows_that_already_open_a_posting_are_left_alone(bank):
    jobright_row(bank, "Software Engineer Intern", link_kind="posting")
    board_with(bank, "Software Engineer Intern")
    assert resolve(bank, Mock(), homepage_budget_s=0)["rows"] == 0


def test_no_match_is_noted_and_not_asked_again_for_hours(bank):
    job = jobright_row(bank, "Quantum Chef Intern")
    board_with(bank, "Software Engineer Intern")
    assert resolve(bank, Mock(), homepage_budget_s=0)["missed"] == 1
    assert links(bank)[str(job)]["url"] is None
    assert resolve(bank, Mock(), homepage_budget_s=0)["rows"] == 0


def test_a_closed_posting_takes_its_link_back(bank):
    job = jobright_row(bank, "Software Engineer Intern")
    board_with(bank, "Software Engineer Intern")
    resolve(bank, Mock(), homepage_budget_s=0)
    bank.query("update bank_postings set closed_at = now()")
    stats = resolve(bank, Mock(), homepage_budget_s=0)
    assert stats["links_dropped"] == 1 and str(job) not in {k for k, v in links(bank).items() if v["url"]}


def test_an_employer_with_no_board_gets_its_homepage_read(bank, monkeypatch):
    from jobboards import resolve as step

    jobright_row(bank, "Software Engineer Intern", company="Zeta", website="https://www.zeta.io")
    asked = []
    monkeypatch.setattr(step, "from_homepages", lambda http, b, companies, budget_s: asked.extend(companies) or 1)
    stats = resolve(bank, Mock(), homepage_budget_s=60)
    assert asked == [("Zeta", "https://www.zeta.io", 1)]
    assert stats["boards_found"] == 1 and stats["no_board"] == 1 and stats.get("missed", 0) == 0


def test_board_owners_reads_one_posting_name_per_board_not_every_posting(bank):
    # 10/16 05:12 and 05:31: resolve died on the crawler role's 120 s statement timeout while
    # board_owners aggregated every open posting's company name (over a million rows).
    bank.query("insert into bank_boards (id, provider, key, company_name, evidence_url, evidence_source, status) "
               "values ('greenhouse:x', 'greenhouse', 'x', '', 'https://x', 'commoncrawl:c', 'active')")
    for n in range(3):
        bank.query("insert into bank_postings (id, source, board_id, company_name, title, url, content_hash) "
                   "values (%s, 'ats_greenhouse', 'greenhouse:x', 'Xylo Labs', 'Intern', %s, 'h')", (f"gh-x-{n}", f"https://x/{n}"))
    owners = {b["id"]: b for b in bank.board_owners()}
    assert owners["greenhouse:x"]["posting_name"] == "Xylo Labs"
    plan = "\n".join(r["QUERY PLAN"] for r in bank.query("explain " + bank.BOARD_OWNERS))
    assert "HashAggregate" not in plan and "GroupAggregate" not in plan
