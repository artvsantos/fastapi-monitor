from datetime import datetime, timedelta

import pytest

from app.database import utc_now


def url(monitor, period=None):
    base = f"/monitors/{monitor['id']}/stats"
    return f'{base}?period={period}' if period else base


async def stats(client, headers, monitor, period=None):
    response = await client.get(url(monitor, period), headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def test_monitor_without_checks(client, auth_headers, monitor):
    body = await stats(client, auth_headers, monitor)

    assert body == {
        'period': '24h',
        'since': body['since'],
        'checks': 0,
        'up_checks': 0,
        'uptime_percent': None,
        'avg_response_ms': None,
        'incidents': 0,
    }


async def test_counts_uptime_and_average_response(
    client, auth_headers, monitor, add_checks,
):
    await add_checks(
        monitor['id'],
        # A queda respondeu (um 503 lento), mas não entra na média.
        (30, True, 100), (20, True, 200), (10, False, 5000), (0, True, 300),
    )

    body = await stats(client, auth_headers, monitor)

    assert body['checks'] == 4
    assert body['up_checks'] == 3
    assert body['uptime_percent'] == 75.0
    # Só as checagens no ar entram na média: o tempo de uma página de erro
    # não diz quanto o site demora quando funciona.
    assert body['avg_response_ms'] == 200


async def test_uptime_is_rounded_to_two_decimals(
    client, auth_headers, monitor, add_checks,
):
    await add_checks(
        monitor['id'], (2, True, 100), (1, True, 101), (0, False, None),
    )

    body = await stats(client, auth_headers, monitor)

    assert body['uptime_percent'] == 66.67
    assert body['avg_response_ms'] == 101  # 100,5 arredondado


async def test_all_down_has_zero_uptime_and_no_average(
    client, auth_headers, monitor, add_checks,
):
    await add_checks(monitor['id'], (1, False, None), (0, False, None))

    body = await stats(client, auth_headers, monitor)

    assert body['uptime_percent'] == 0.0
    assert body['avg_response_ms'] is None


async def test_incident_is_each_time_the_site_went_down(
    client, auth_headers, monitor, add_checks,
):
    # no ar, caiu, continuou fora, voltou, caiu de novo = 2 quedas.
    await add_checks(
        monitor['id'],
        (50, True, 100), (40, False, None), (30, False, None),
        (20, True, 100), (10, False, None),
    )

    body = await stats(client, auth_headers, monitor)

    assert body['incidents'] == 2


async def test_period_that_starts_down_counts_as_an_incident(
    client, auth_headers, monitor, add_checks,
):
    await add_checks(monitor['id'], (20, False, None), (10, True, 100))

    body = await stats(client, auth_headers, monitor)

    assert body['incidents'] == 1


@pytest.mark.parametrize('period, expected_checks', [
    ('24h', 1),
    ('7d', 2),
    ('30d', 3),
])
async def test_period_limits_the_checks(
    client, auth_headers, monitor, add_checks, period, expected_checks,
):
    hour, day = 60, 24 * 60
    await add_checks(
        monitor['id'],
        (hour, True, 100), (3 * day, False, None), (20 * day, True, 100),
        (40 * day, False, None),
    )

    body = await stats(client, auth_headers, monitor, period)

    assert body['period'] == period
    assert body['checks'] == expected_checks


async def test_since_tells_where_the_period_starts(
    client, auth_headers, monitor,
):
    before = utc_now()
    body = await stats(client, auth_headers, monitor, '7d')

    since = datetime.fromisoformat(body['since'])
    assert before - timedelta(days=7) <= since <= utc_now() - timedelta(days=7)


async def test_invalid_period_lists_the_options(client, auth_headers, monitor):
    response = await client.get(url(monitor, '1ano'), headers=auth_headers)

    assert response.status_code == 422
    assert response.json()['detail'][0]['msg'] == (
        'Escolha uma das opções: 24h, 7d ou 30d.'
    )


async def test_stats_of_someone_else_is_404(client, monitor, other_headers):
    response = await client.get(url(monitor), headers=other_headers)

    assert response.status_code == 404


async def test_requires_login(client, monitor):
    response = await client.get(url(monitor))

    assert response.status_code == 401
