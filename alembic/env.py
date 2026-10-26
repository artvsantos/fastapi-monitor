import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app import models  # noqa: F401  (registra as tabelas no Base)
from app.config import Settings
from app.database import Base

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False: sem isso, rodar as migrações dentro
    # dos testes desligaria os logs do app nos testes seguintes.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# A URL vem da mesma configuração do app (variável DATABASE_URL), não do
# alembic.ini, para não ter senha do banco em arquivo versionado.
DATABASE_URL = Settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # O SQLite não sabe alterar coluna; o modo batch recria a tabela.
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(DATABASE_URL, poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
