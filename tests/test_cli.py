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


def test_a_crawl_only_crawls_resolve_has_its_own_runs(monkeypatch):
    bank = Mock()
    monkeypatch.setenv('BANK_DATABASE_URL', 'unused-test-dsn')
    monkeypatch.setattr(cli, 'Bank', lambda dsn: bank)
    monkeypatch.setattr(cli, 'crawl', lambda *a, **kw: {'changed': 1})
    asked = []
    monkeypatch.setattr(cli, 'resolve', lambda *a, **kw: asked.append(kw) or {'linked': 3})
    cli.main(['crawl'])
    assert asked == []
    cli.main(['resolve', '--homepage-budget', '30'])
    assert asked == [{'homepage_budget_s': 30.0}]


def test_find_does_nothing_without_a_claude_key(monkeypatch, capsys):
    bank = Mock()
    monkeypatch.setenv('BANK_DATABASE_URL', 'unused-test-dsn')
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.setattr(cli, 'Bank', lambda dsn: bank)
    monkeypatch.setattr(cli, 'find', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('asked')))
    cli.main(['find'])
    assert 'ANTHROPIC_API_KEY is not set' in capsys.readouterr().out


def test_crawl_passes_its_shard_on(monkeypatch):
    monkeypatch.setenv('BANK_DATABASE_URL', 'unused-test-dsn')
    monkeypatch.setattr(cli, 'Bank', lambda dsn: Mock())
    asked = []
    monkeypatch.setattr(cli, 'crawl', lambda *a, **kw: asked.append(kw) or {'changed': 1})
    cli.main(['crawl', '--shard', '2', '--shards', '4'])
    assert asked[0]['shard'] == 2 and asked[0]['shards'] == 4


def _cli_with(monkeypatch, **fakes):
    bank = Mock()
    bank.start_run.return_value = 1
    monkeypatch.setenv('BANK_DATABASE_URL', 'unused-test-dsn')
    monkeypatch.setattr(cli, 'Bank', lambda dsn: bank)
    for name, fake in fakes.items():
        monkeypatch.setattr(cli, name, fake)
    return bank


def test_the_boards_lane_reads_homepages_then_common_crawl_on_budgets(monkeypatch):
    asked = []
    bank = _cli_with(monkeypatch, jobright_companies=lambda http: [('Acme', 'https://acme.com', 3)],
                     from_homepages=lambda http, b, companies, budget_s: asked.append(('homepages', companies, budget_s)) or 2,
                     from_commoncrawl=lambda http, b, crawls, budget_s: asked.append(('commoncrawl', crawls, budget_s)) or 5)
    cli.main(['boards'])
    assert asked == [('homepages', [('Acme', 'https://acme.com', 3)], 2400.0), ('commoncrawl', 12, 1200.0)]
    bank.finish_run.assert_called_once_with(1, {'homepages': 2, 'commoncrawl': 5})


def test_the_daily_discover_keeps_to_the_quick_sources(monkeypatch):
    def slow(*a, **kw):
        raise AssertionError('the boards lane does this')
    bank = _cli_with(monkeypatch, from_directory=lambda: [], from_feeds=lambda http: [], from_bank=lambda b: [],
                     from_homepages=slow, from_commoncrawl=slow, jobright_companies=slow)
    bank.add_boards.return_value = 0
    bank.prune.return_value = 4
    cli.main(['discover'])
    assert bank.finish_run.call_args[0][1]['pruned_closed_postings'] == 4


def test_find_takes_its_daily_cap_from_the_repo_variable(monkeypatch):
    monkeypatch.setenv('BANK_DATABASE_URL', 'unused-test-dsn')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key')
    monkeypatch.setenv('FINDER_DAILY_COMPANIES', '2')
    monkeypatch.setattr(cli, 'Bank', lambda dsn: Mock())
    asked = []
    monkeypatch.setattr(cli, 'find', lambda *a, **kw: asked.append(kw) or {})
    import sys, types
    monkeypatch.setitem(sys.modules, 'anthropic', types.SimpleNamespace(Anthropic=lambda: object()))
    cli.main(['find'])
    assert asked[0]['daily_companies'] == 2 and asked[0]['max_companies'] == 20
