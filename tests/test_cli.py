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
