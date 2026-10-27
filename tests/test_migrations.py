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

    assert table_names(alembic_config) == {'users', 'monitors'}


def test_migrations_match_the_models(alembic_config):
    command.upgrade(alembic_config, 'head')

    # Falha se alguém mudou um model e esqueceu de gerar a migração.
    command.check(alembic_config)


def test_migrations_can_be_undone(alembic_config):
    command.upgrade(alembic_config, 'head')
    command.downgrade(alembic_config, 'base')

    assert table_names(alembic_config) == set()
