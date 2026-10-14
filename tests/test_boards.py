import pytest

from jobboards.boards import Board, board_from_url


@pytest.mark.parametrize("url, provider, key", [
    ("https://boards.greenhouse.io/stripe/jobs/123", "greenhouse", "stripe"),
    ("https://job-boards.greenhouse.io/Stripe", "greenhouse", "Stripe"),
    ("https://boards-api.greenhouse.io/v1/boards/stripe/jobs", "greenhouse", "stripe"),
    ("https://boards.greenhouse.io/embed/job_board?for=stripe", "greenhouse", "stripe"),
    ("https://boards.greenhouse.io/embed/job_app?for=stripe&token=1", "greenhouse", "stripe"),
    ("https://jobs.lever.co/zoox/1a2b", "lever", "zoox"),
    ("https://api.lever.co/v0/postings/zoox?mode=json", "lever", "zoox"),
    ("https://jobs.ashbyhq.com/ramp/abc-uuid", "ashby", "ramp"),
    ("https://jobs.smartrecruiters.com/Visa/7438-role", "smartrecruiters", "Visa"),
    ("https://careers.smartrecruiters.com/Visa", "smartrecruiters", "Visa"),
    ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/x_JR1", "workday",
     "nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"),
    ("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite", "workday",
     "nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"),
    ("https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs", "workday",
     "nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"),
    ("https://eeho.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/123", "oracle",
     "eeho.fa.us2.oraclecloud.com/CX_1"),
    ("https://careers-lennox.icims.com/jobs/12345/role/job", "icims", "careers-lennox.icims.com"),
    ("http://jobs.lever.co/zoox", "lever", "zoox"),
])
def test_public_board_links_become_boards(url, provider, key):
    assert board_from_url(url) == Board(provider, key)


@pytest.mark.parametrize("url", [
    "https://greenhouse.io/stripe",
    "https://www.icims.com/careers",
    "https://cdn.icims.com/x",
    "https://evil.com/boards.greenhouse.io/stripe",
    "https://boards.greenhouse.io.evil.com/stripe",
    "https://boards.greenhouse.io/",
    "https://boards.greenhouse.io/embed/job_board",
    "https://jobs.lever.co/",
    "https://www.myworkdayjobs.com/x",
    "https://nvidia.wd5.myworkdayjobs.com/",
    "https://nvidia.wd5.myworkdayjobs.com/en-US",
    "https://nvidia.wd5.myworkdayjobs.com/llms.txt",
    "https://nvidia.wd5.myworkdayjobs.com/en-US/favicon.png",
    "https://user:pw@jobs.lever.co/zoox",
    "ftp://jobs.lever.co/zoox",
    "not a url",
    "",
])
def test_anything_else_is_not_a_board(url):
    assert board_from_url(url) is None


def test_board_id_folds_case_so_two_spellings_are_one_board():
    assert board_from_url("https://job-boards.greenhouse.io/Stripe").id == \
        board_from_url("https://boards.greenhouse.io/stripe").id == "greenhouse:stripe"
