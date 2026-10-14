import pytest

from jobboards.http import Http, Throttled, TooLarge


class FakeResponse:
    def __init__(self, status=200, body=b"{}", headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}
        self._content = None

    def iter_content(self, chunk_size):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        pass


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls, self.headers = list(responses), [], {}

    def request(self, method, url, **kw):
        self.calls.append((method, url))
        return self.responses.pop(0)


class Clock:
    def __init__(self):
        self.now, self.slept = 100.0, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(seconds, 3))
        self.now += seconds


def make(responses, **kw):
    clock = Clock()
    http = Http(session=FakeSession(responses), clock=clock, sleep=clock.sleep, **kw)
    return http, clock


def test_second_request_to_one_host_waits_the_gap():
    http, clock = make([FakeResponse(), FakeResponse()], min_gap=0.5)
    http.get("https://api.lever.co/a")
    http.get("https://api.lever.co/b")
    assert clock.slept == [0.5]


def test_other_hosts_do_not_wait():
    http, clock = make([FakeResponse(), FakeResponse()], min_gap=0.5)
    http.get("https://api.lever.co/a")
    http.get("https://api.ashbyhq.com/b")
    assert clock.slept == []


def test_429_waits_retry_after_then_asks_once_more():
    http, clock = make([FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse(200, b'{"ok":1}')])
    resp = http.get("https://api.lever.co/a")
    assert resp.status_code == 200 and resp.json() == {"ok": 1}
    assert 7 in clock.slept


def test_a_host_that_keeps_throttling_is_left_alone_for_the_run():
    http, _ = make([FakeResponse(429, headers={"Retry-After": "1"})] * 6)
    for _ in range(3):
        http.get("https://api.lever.co/a")
    with pytest.raises(Throttled):
        http.get("https://api.lever.co/b")


def test_oversized_body_is_refused():
    http, _ = make([FakeResponse(body=b"x" * 2000)], max_bytes=1000)
    with pytest.raises(TooLarge):
        http.get("https://api.lever.co/a")
