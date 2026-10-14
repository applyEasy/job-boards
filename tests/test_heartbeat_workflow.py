from pathlib import Path

WORKFLOWS = Path(__file__).parent.parent / ".github" / "workflows"


def test_crawl_runs_are_named_by_what_they_do():
    # The heartbeat tells a discover from a crawl by this name.
    text = (WORKFLOWS / "crawl.yml").read_text()
    assert "run-name: ${{ inputs.command || (github.event.schedule == '41 6 * * *' && 'discover') || 'crawl' }}" in text


def test_heartbeat_follows_finished_crawl_runs_and_never_doubles_up():
    text = (WORKFLOWS / "heartbeat.yml").read_text()
    assert "workflow_run:\n    workflows: [crawl]\n    types: [completed]" in text
    assert "github.event.workflow_run.conclusion != 'cancelled'" in text  # a cancelled run ends its chain
    assert "permissions:\n  actions: write\n  contents: read" in text
    assert "secrets." not in text  # it never touches the bank
    assert 'select(.status != "completed")' in text  # nothing queued while a run waits or works
    assert 'select(.displayTitle == "discover")' in text
    assert "gh workflow run crawl.yml -f command=" in text


def test_heartbeat_starts_the_daily_coverage_report_with_the_daily_discover():
    text = (WORKFLOWS / "heartbeat.yml").read_text()
    assert 'if [ "$next" = discover ]; then gh workflow run jobright-coverage.yml; fi' in text
