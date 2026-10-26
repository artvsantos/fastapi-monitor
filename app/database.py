from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import DateTime, MetaData, TypeDecorator
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    # Nomes fixos para índices e constraints. Sem isso cada banco inventa
    # o seu, e uma migração que precise apagar um deles não o encontra.
    metadata = MetaData(naming_convention={
        'ix': 'ix_%(column_0_label)s',
        'uq': 'uq_%(table_name)s_%(column_0_name)s',
        'ck': 'ck_%(table_name)s_%(constraint_name)s',
        'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
        'pk': 'pk_%(table_name)s',
    })


class UTCDateTime(TypeDecorator):
    """Data e hora sempre em UTC, com fuso.

    O Postgres guarda o fuso, mas o SQLite devolve a data "sem fuso", e a
    API mostraria um horário ambíguo. Aqui as duas voltam iguais.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


def utc_now() -> datetime:
    return datetime.now(UTC)


def make_engine(url: str) -> AsyncEngine:
    if url in ('sqlite+aiosqlite://', 'sqlite+aiosqlite:///:memory:'):
        # Banco em memória some quando a conexão fecha; com StaticPool
        # todas as sessões usam a mesma conexão e enxergam as tabelas.
        return create_async_engine(
            url, poolclass=StaticPool,
            connect_args={'check_same_thread': False},
        )
    return create_async_engine(url, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # Sem expire_on_commit, ler um campo depois do commit não dispara
    # outra consulta (que no modo async daria erro).
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
