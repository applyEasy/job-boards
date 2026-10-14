from jobboards.publish import is_early_career, job_bank_rows, seniority
from jobboards.readers import Posting


def test_title_rules_match_backend():
    assert is_early_career("Software Engineer Intern")
    assert is_early_career("Software Engineer", "Internship")
    assert is_early_career("Operations Manager Trainee")
    assert is_early_career("Project Engineer (New Graduates)")
    assert not is_early_career("Senior Software Engineer")
    assert not is_early_career("Internship Program Manager")
    assert not is_early_career("Early Career Recruiter")
    assert not is_early_career("International Sales Manager")


def test_seniority():
    assert seniority("Data Science Intern") == "intern"
    assert seniority("New Grad Software Engineer II") == "new_grad"
    assert seniority("Manager in Training") == "entry"


def test_rows_keep_only_the_board_roles_shaped_like_backend():
    postings = [
        Posting(id="or-x-1", source="ats_oracle", company_name=" Acme ", title="Analyst Intern",
                url="https://x.oraclecloud.com/1", locations=["Austin, TX", ""], employment_type="Co-op",
                workplace=" Remote "),
        Posting(id="or-x-2", source="ats_oracle", company_name="Acme", title="Staff Engineer", url="https://x/2"),
        Posting(id="gh-a-3", source="ats_greenhouse", company_name="", title="Intern", url="https://x/3"),
    ]
    assert job_bank_rows(postings) == [{
        "source": "ats_oracle", "source_id": "or-x-1", "company_name": "Acme", "title": "Analyst Intern",
        "locations": ["Austin, TX"], "url": "https://x.oraclecloud.com/1", "apply_url": "https://x.oraclecloud.com/1",
        "category": "internship", "workplace": "Remote", "date_posted": None, "ats_platform": None,
        "seniority": "intern",
    }]
