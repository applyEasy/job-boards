from pathlib import Path


WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "jobright-coverage.yml"


def test_coverage_workflow_uses_only_trusted_triggers_and_keeps_credentials_out_of_artifact():
    text = WORKFLOW.read_text()

    assert "pull_request:" not in text
    assert "branches: [main]" in text
    assert "permissions:\n  contents: read" in text
    assert "BANK_DATABASE_URL: ${{ secrets.BANK_DATABASE_URL }}" in text
    assert 'python -m jobboards jobright-coverage > "$REPORT"' in text
    assert 'json.load(open(os.environ["REPORT"]))' in text
    assert "actions/upload-artifact@v4" in text
    assert "path: ${{ runner.temp }}/jobright-coverage.json" in text
