from pathlib import Path

WORKFLOWS = Path(__file__).parent.parent / ".github" / "workflows"


def test_crawl_runs_are_named_by_what_they_do():
    # The heartbeat tells a discover from a crawl by this name.
    text = (WORKFLOWS / "crawl.yml").read_text()
    assert "run-name: ${{ inputs.command || (github.event.schedule == '41 6 * * *' && 'discover') || 'crawl' }}" in text


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
    assert "gh workflow run crawl.yml -f command=crawl" in text


def test_an_overdue_discover_is_queued_even_while_crawls_keep_the_queue_busy():
    text = (WORKFLOWS / "heartbeat.yml").read_text()
    overdue = text.index('if [ "$overdue" = 1 ] && [ "$discover_waiting" -eq 0 ]')
    busy = text.index('if [ "$waiting" -gt 0 ]')
    assert overdue < busy
    assert 'select(.displayTitle == "discover")' in text


def test_heartbeat_starts_the_daily_coverage_report_with_the_daily_discover():
    text = (WORKFLOWS / "heartbeat.yml").read_text()
    assert "gh workflow run crawl.yml -f command=discover && gh workflow run jobright-coverage.yml" in text
