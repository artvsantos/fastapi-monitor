from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from app.database import UTCDateTime, _enable_foreign_keys, make_engine

BRASILIA = timezone(timedelta(hours=-3))


@pytest.mark.parametrize('stored, expected', [
    # SQLite: volta sem fuso, e o horário salvo já era UTC.
    (datetime(2026, 10, 26, 12, 0), datetime(2026, 10, 26, 12, 0, tzinfo=UTC)),
    # Postgres: já volta com fuso, e fica como está.
    (datetime(2026, 10, 26, 9, 0, tzinfo=BRASILIA),
     datetime(2026, 10, 26, 9, 0, tzinfo=BRASILIA)),
    (None, None),
])
def test_dates_always_come_back_with_timezone(stored, expected):
    result = UTCDateTime().process_result_value(stored, dialect=None)

    assert result == expected
    assert result is None or result.tzinfo is not None


def test_dates_in_other_timezones_are_saved_in_utc():
    stored = UTCDateTime().process_bind_param(
        datetime(2026, 10, 27, 9, 0, tzinfo=BRASILIA), dialect=None,
    )

    assert stored == datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    assert stored.tzinfo == UTC


@pytest.mark.parametrize('url, foreign_keys_pragma', [
    ('sqlite+aiosqlite://', True),
    ('sqlite+aiosqlite:///./arquivo.db', True),
    # O Postgres já respeita as chaves estrangeiras; nada a ligar.
    ('postgresql+asyncpg://usuario:senha@localhost/banco', False),
])
def test_foreign_keys_are_enforced_on_sqlite(url, foreign_keys_pragma):
    engine = make_engine(url)

    assert event.contains(
        engine.sync_engine, 'connect', _enable_foreign_keys,
    ) is foreign_keys_pragma
