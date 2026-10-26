from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.database import UTCDateTime

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
