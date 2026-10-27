from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def alembic_config(tmp_path, monkeypatch):
    database = tmp_path / 'migracoes.db'
    monkeypatch.setenv(
        'DATABASE_URL', f'sqlite+aiosqlite:///{database.as_posix()}',
    )
    config = Config(str(ROOT / 'alembic.ini'))
    config.attributes['database_file'] = database
    return config


def table_names(config):
    engine = sa.create_engine(f"sqlite:///{config.attributes['database_file']}")
    with engine.connect() as conn:
        names = set(sa.inspect(conn).get_table_names())
    engine.dispose()
    return names - {'alembic_version'}


def test_migrations_create_the_tables(alembic_config):
    command.upgrade(alembic_config, 'head')

    assert table_names(alembic_config) == {'users', 'monitors', 'checks'}


def test_migrations_match_the_models(alembic_config):
    command.upgrade(alembic_config, 'head')

    # Falha se alguém mudou um model e esqueceu de gerar a migração.
    command.check(alembic_config)


def test_migrations_can_be_undone(alembic_config):
    command.upgrade(alembic_config, 'head')
    command.downgrade(alembic_config, 'base')

    assert table_names(alembic_config) == set()


def test_monitors_that_already_existed_get_scheduled(alembic_config):
    # Rodar a migração com monitores já cadastrados não pode falhar por
    # causa da coluna nova obrigatória.
    command.upgrade(alembic_config, 'd61b1539538d')
    engine = sa.create_engine(
        f"sqlite:///{alembic_config.attributes['database_file']}",
    )
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO users (email, hashed_password, created_at) "
            "VALUES ('ana@exemplo.com', 'x', '2026-10-01 00:00:00')",
        ))
        conn.execute(sa.text(
            "INSERT INTO monitors (owner_id, name, url, interval_minutes, "
            "is_active, created_at) VALUES (1, 'Site', 'https://a.com/', 5, "
            "1, '2026-10-01 00:00:00')",
        ))

    command.upgrade(alembic_config, 'head')

    with engine.connect() as conn:
        row = conn.execute(sa.text(
            'SELECT next_check_at, is_up FROM monitors',
        )).one()
    engine.dispose()
    assert row.next_check_at is not None
    assert row.is_up is None
