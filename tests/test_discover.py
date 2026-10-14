import json

from jobboards.boards import Board
from jobboards.discover import from_commoncrawl, from_directory, from_feeds
from jobboards.http import Reply


class FakeHttp:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, **kw):
        self.calls.append(url)
        for prefix, body in self.routes.items():
            if url.startswith(prefix):
                status, body = body if isinstance(body, tuple) else (200, body)
                return Reply(status, {}, body.encode())
        return Reply(404, {}, b"")


class FakeBank:
    def __init__(self, done=()):
        self.added, self.done = [], set(done)

    def add_boards(self, items):
        items = list(items)
        self.added.extend(items)
        return len(items)

    def done_items(self, source):
        return {i for s, i in self.done if s == source}

    def mark_done(self, source, item):
        self.done.add((source, item))


def test_directory_gives_every_vendor_row_a_board_with_its_company():
    found = list(from_directory())
    assert len(found) > 9000
    board, url, source = next(f for f in found if f[0].key == "0x")
    assert board == Board("ashby", "0x") and board.company_name == "0x"
    assert url == "https://jobs.ashbyhq.com/0x" and source.startswith("directory:")
    assert {b.provider for b, _, _ in found} == {"greenhouse", "lever", "ashby"}


def test_feeds_take_board_links_and_skip_the_rest():
    simplify = json.dumps([
        {"company_name": "Stripe", "url": "https://boards.greenhouse.io/stripe/jobs/1"},
        {"company_name": "Acme", "url": "https://acme.com/careers/1"},
    ])
    readme = "| [Apply](https://jobs.lever.co/zoox/abc) | [x](https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/a_JR1) |"
    http = FakeHttp({"https://raw.githubusercontent.com/SimplifyJobs/": simplify,
                     "https://raw.githubusercontent.com/speedyapply/": readme})
    found = {b.id: (b, source) for b, _, source in from_feeds(http)}
    assert set(found) == {"greenhouse:stripe", "lever:zoox", "workday:nvidia.wd5.myworkdayjobs.com/nvidiaexternalcareersite"}
    assert found["greenhouse:stripe"][0].company_name == "Stripe" and found["lever:zoox"][1].startswith("feed:")


def test_a_feed_that_fails_does_not_stop_the_others():
    http = FakeHttp({"https://raw.githubusercontent.com/speedyapply/": "https://jobs.lever.co/zoox/abc"})
    assert [b.id for b, _, _ in from_feeds(http)] == ["lever:zoox"] * 2  # both SpeedyApply files answer


def _cc_routes(pages_done_fail=False):
    def route(urls):
        return "\n".join(urls)
    return {
        "https://index.commoncrawl.org/collinfo.json": json.dumps([{"id": "CC-MAIN-2026-39"}, {"id": "CC-MAIN-2026-34"}]),
        "https://index.commoncrawl.org/CC-MAIN-2026-39-index?url=jobs.lever.co%2F%2A&output=json&showNumPages=true":
            json.dumps({"pages": 2}),
        "https://index.commoncrawl.org/CC-MAIN-2026-39-index?url=jobs.lever.co%2F%2A&output=text&fl=url&page=0":
            route(["https://jobs.lever.co/zoox/1", "https://jobs.lever.co/zoox/2", "https://jobs.lever.co/ramp"]),
        "https://index.commoncrawl.org/CC-MAIN-2026-39-index?url=jobs.lever.co%2F%2A&output=text&fl=url&page=1":
            (502, "Bad Gateway") if pages_done_fail else route(["https://jobs.lever.co/notion/x"]),
    }


def test_commoncrawl_records_each_finished_page_and_retries_failed_ones(monkeypatch):
    monkeypatch.setattr("jobboards.discover.CC_PATTERNS", {"jobs.lever.co/*": None})
    bank = FakeBank()
    added = from_commoncrawl(FakeHttp(_cc_routes(pages_done_fail=True)), bank, crawls=1)
    assert added == 2 and {b.id for b, _, _ in bank.added} == {"lever:zoox", "lever:ramp"}
    assert bank.done == {("commoncrawl", "CC-MAIN-2026-39|jobs.lever.co/*|0")}  # page 1 failed: tomorrow
    bank.added.clear()
    from_commoncrawl(FakeHttp(_cc_routes()), bank, crawls=1)
    assert [b.id for b, _, _ in bank.added] == ["lever:notion"]


def test_commoncrawl_skips_crawls_already_walked(monkeypatch):
    monkeypatch.setattr("jobboards.discover.CC_PATTERNS", {"jobs.lever.co/*": None})
    bank = FakeBank(done={("commoncrawl", "CC-MAIN-2026-39|jobs.lever.co/*|*")})
    http = FakeHttp(_cc_routes())
    from_commoncrawl(http, bank, crawls=1)
    assert bank.added == [] and not any("page=" in url for url in http.calls)


def test_commoncrawl_evidence_is_the_link_that_proved_the_board(monkeypatch):
    monkeypatch.setattr("jobboards.discover.CC_PATTERNS", {"jobs.lever.co/*": None})
    bank = FakeBank()
    from_commoncrawl(FakeHttp(_cc_routes()), bank, crawls=1)
    assert ("lever:zoox", "https://jobs.lever.co/zoox/1") in {(b.id, url) for b, url, _ in bank.added}
