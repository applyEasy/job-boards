"""The crawler role can verify owner-applied migrations without owning the table."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
GOOD = ("timestamp with time zone", "YES")


class FakeConnection:
    def __init__(self, columns):
        self.columns = dict(columns)
        self.queries = []
        self.ddl = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, prepare=None):
        self.queries.append(sql)
        if "information_schema.columns" in sql:
            return SimpleNamespace(fetchall=lambda: [
                (name, *kind) for name, kind in self.columns.items()
            ])
        self.ddl.append(sql)
        if "last_listed_at" in sql:
            self.columns["last_listed_at"] = GOOD
        if "gone_first_seen_at" in sql:
            self.columns["gone_first_seen_at"] = GOOD
        return self


def load_script(monkeypatch, conn):
    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(
        Connection=FakeConnection,
        connect=lambda *_args, **_kwargs: conn,
    ))
    monkeypatch.setenv("BANK_DATABASE_URL", "postgresql://example.invalid/test")
    spec = importlib.util.spec_from_file_location(
        "apply_capped_migrations_test", ROOT / "scripts/apply_capped_migrations.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_owner_applied_schema_skips_ddl(monkeypatch, capsys):
    conn = FakeConnection({"last_listed_at": GOOD, "gone_first_seen_at": GOOD})
    load_script(monkeypatch, conn).main()
    assert conn.ddl == []
    assert "already applied" in capsys.readouterr().out


def test_missing_columns_still_apply_and_verify_with_owner_role(monkeypatch):
    conn = FakeConnection({})
    load_script(monkeypatch, conn).main()
    assert len(conn.ddl) == 2
    assert conn.columns == {"last_listed_at": GOOD, "gone_first_seen_at": GOOD}


def test_wrong_existing_column_fails_before_ddl(monkeypatch):
    conn = FakeConnection({"last_listed_at": ("timestamp without time zone", "YES")})
    with pytest.raises(RuntimeError, match="wrong type"):
        load_script(monkeypatch, conn).main()
    assert conn.ddl == []
