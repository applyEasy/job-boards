from pathlib import Path


SCRIPT = (Path(__file__).parent.parent / "scripts" / "setup_bank.sh").read_text()


def test_admin_setup_applies_bank_and_publish_migrations_in_order():
    bank = 'psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -f "$ROOT/sql/20261014_boards_bank.sql"'
    publish = 'psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -q -f "$ROOT/sql/20261014_bank_publish.sql"'

    assert bank in SCRIPT
    assert publish in SCRIPT
    assert SCRIPT.index(bank) < SCRIPT.index(publish)


def test_setup_refuses_to_enable_crawls_without_publish_functions():
    assert "to_regprocedure('public.bank_publish(jsonb)')" in SCRIPT
    assert "publishing migration is missing" in SCRIPT
