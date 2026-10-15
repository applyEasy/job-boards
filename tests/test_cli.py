from unittest.mock import Mock

import pytest

from jobboards import __main__ as cli


@pytest.mark.parametrize('stats, fails', [({'changed': 1}, False), ({'bank_error': 1}, True), ({'touch_error': 1}, True)])
def test_crawl_reports_publishing_failures(monkeypatch, stats, fails):
    bank = Mock()
    monkeypatch.setenv('BANK_DATABASE_URL', 'unused-test-dsn')
    monkeypatch.setattr(cli, 'Bank', lambda dsn: bank)
    monkeypatch.setattr(cli, 'crawl', lambda *a, **kw: stats)
    if fails:
        with pytest.raises(SystemExit, match='publishing failures'):
            cli.main(['crawl'])
    else:
        cli.main(['crawl'])
    bank.close.assert_called_once()


def test_crawl_looks_up_jobright_links_after_and_survives_their_failure(monkeypatch):
    bank = Mock()
    monkeypatch.setenv('BANK_DATABASE_URL', 'unused-test-dsn')
    monkeypatch.setattr(cli, 'Bank', lambda dsn: bank)
    monkeypatch.setattr(cli, 'crawl', lambda *a, **kw: {'changed': 1})
    asked = []
    monkeypatch.setattr(cli, 'resolve', lambda *a, **kw: asked.append(kw) or {'linked': 3})
    cli.main(['crawl', '--resolve-budget', '30'])
    assert asked == [{'homepage_budget_s': 30.0}]

    def broken(*a, **kw):
        raise RuntimeError('function bank_jobright_unresolved(integer) does not exist')
    monkeypatch.setattr(cli, 'resolve', broken)
    cli.main(['crawl'])  # no SystemExit: the crawl's own result stands
    bank.close.assert_called()
