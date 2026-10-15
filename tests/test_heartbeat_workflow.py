from pathlib import Path

WORKFLOWS = Path(__file__).parent.parent / ".github" / "workflows"


LANE = ("inputs.command || (github.event.schedule == '41 6 * * *' && 'discover') "
        "|| (github.event.schedule == '53 * * * *' && 'resolve') || 'crawl'")


def test_crawl_runs_are_named_by_what_they_do():
    # The heartbeat tells a discover from a crawl from a resolve by this name.
    text = (WORKFLOWS / "crawl.yml").read_text()
    assert f"run-name: ${{{{ {LANE} }}}}" in text


def test_each_kind_of_run_has_its_own_queue():
    # 10/15: a resolve waited two hours behind a crawl. Each lane queues alone; two runs of
    # one lane still never overlap (only one crawl writes postings at a time).
    text = (WORKFLOWS / "crawl.yml").read_text()
    assert f"group: bank-${{{{ {LANE} }}}}" in text
    assert "cancel-in-progress: false" in text
    assert '- cron: "53 * * * *"' in text


def test_a_crawl_has_room_to_finish_the_boards_it_started():
    # 40 minutes of starting boards; one big Jibe or SuccessFactors site can take 20 more.
    # A run cut off at the old 55 minutes lost its counts and ended the chain (10/15 01:35).
    assert "timeout-minutes: 120" in (WORKFLOWS / "crawl.yml").read_text()


def test_heartbeat_follows_every_finished_crawl_run_and_never_doubles_up():
    text = (WORKFLOWS / "heartbeat.yml").read_text()
    assert "workflow_run:\n    workflows: [crawl]\n    types: [completed]" in text
    # A run cut off by its time limit counts as cancelled: the chain must survive it.
    # The waiting-run check below is what keeps chains from multiplying.
    assert "conclusion != 'cancelled'" not in text
    assert "permissions:\n  actions: write\n  contents: read" in text
    assert "secrets." not in text  # it never touches the bank
    assert 'select(.status != "completed")' in text
    assert "for lane in crawl resolve find; do" in text
    assert 'gh workflow run crawl.yml -f command="$lane"' in text


def test_an_overdue_discover_is_queued_even_while_crawls_keep_the_queue_busy():
    text = (WORKFLOWS / "heartbeat.yml").read_text()
    overdue = text.index('if [ "$overdue" = 1 ] && [ "$(busy discover)" -eq 0 ]')
    lanes = text.index("for lane in crawl resolve find; do")
    assert overdue < lanes
    assert 'select(.displayTitle == "discover")' in text


def test_heartbeat_starts_the_daily_coverage_report_with_the_daily_discover():
    text = (WORKFLOWS / "heartbeat.yml").read_text()
    assert "gh workflow run crawl.yml -f command=discover && gh workflow run jobright-coverage.yml" in text


def test_only_scheduled_and_manual_runs_see_the_claude_key():
    text = (WORKFLOWS / "crawl.yml").read_text()
    assert "ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}" in text
    assert "pull_request" not in text


def test_a_crawl_run_is_four_parallel_shards():
    text = (WORKFLOWS / "crawl.yml").read_text()
    assert "shard: [0, 1, 2, 3]" in text and "fail-fast: false" in text
    assert "python -m jobboards crawl --shard ${{ matrix.shard }} --shards 4" in text
    assert f"({LANE}) == 'crawl'" in text and f"({LANE}) != 'crawl'" in text
