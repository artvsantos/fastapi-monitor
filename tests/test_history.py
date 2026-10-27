from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.models import Check

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def url(monitor, **params):
    query = '&'.join(f'{key}={value}' for key, value in params.items())
    return f"/monitors/{monitor['id']}/checks" + (f'?{query}' if query else '')


def times(response):
    return [item['checked_at'] for item in response.json()['items']]


async def test_lists_checks_newest_first(
    client, auth_headers, monitor, add_checks,
):
    await add_checks(
        monitor['id'], (10, True, 120), (5, False, None), (0, True, 80),
        now=NOW,
    )

    response = await client.get(url(monitor), headers=auth_headers)
    body = response.json()

    assert response.status_code == 200
    assert times(response) == [
        '2026-10-27T12:00:00Z', '2026-10-27T11:55:00Z', '2026-10-27T11:50:00Z',
    ]
    assert body['items'][1] == {
        'checked_at': '2026-10-27T11:55:00Z',
        'is_up': False,
        'status_code': 503,
        'response_time_ms': None,
        'error': 'O site respondeu com o código 503.',
    }
    assert body['next_before'] is None


async def test_empty_history(client, auth_headers, monitor):
    response = await client.get(url(monitor), headers=auth_headers)

    assert response.json() == {'items': [], 'next_before': None}


async def test_does_not_mix_checks_of_other_monitors(
    client, auth_headers, monitor, add_checks,
):
    other = (await client.post('/monitors', json={
        'name': 'Outro', 'url': 'https://outro-site.com/',
    }, headers=auth_headers)).json()
    await add_checks(other['id'], (0, True, 50))

    response = await client.get(url(monitor), headers=auth_headers)

    assert response.json()['items'] == []


# --- páginas ----------------------------------------------------------------
# Paginação por cursor: a próxima página começa antes da última checagem
# vista. Com "página 2, 3...", uma checagem nova no meio empurraria tudo
# e a pessoa veria itens repetidos.

async def test_pages_follow_the_cursor(
    client, auth_headers, monitor, add_checks,
):
    await add_checks(
        monitor['id'], *[(minutes, True, 100) for minutes in range(5)],
        now=NOW,
    )

    first = await client.get(url(monitor, limit=2), headers=auth_headers)
    cursor = first.json()['next_before']
    second = await client.get(
        url(monitor, limit=2, before=cursor), headers=auth_headers,
    )
    third = await client.get(
        url(monitor, limit=2, before=second.json()['next_before']),
        headers=auth_headers,
    )

    assert times(first) == ['2026-10-27T12:00:00Z', '2026-10-27T11:59:00Z']
    assert cursor == '2026-10-27T11:59:00Z'
    assert times(second) == ['2026-10-27T11:58:00Z', '2026-10-27T11:57:00Z']
    assert times(third) == ['2026-10-27T11:56:00Z']
    assert third.json()['next_before'] is None


async def test_last_full_page_has_no_next_cursor(
    client, auth_headers, monitor, add_checks,
):
    await add_checks(monitor['id'], (0, True, 1), (1, True, 1), now=NOW)

    response = await client.get(url(monitor, limit=2), headers=auth_headers)

    assert response.json()['next_before'] is None


async def test_default_page_has_50_checks(
    client, auth_headers, monitor, add_checks,
):
    await add_checks(
        monitor['id'], *[(minutes, True, 100) for minutes in range(51)],
    )

    response = await client.get(url(monitor), headers=auth_headers)

    assert len(response.json()['items']) == 50
    assert response.json()['next_before'] is not None


async def test_cursor_in_another_timezone_means_the_same_moment(
    client, auth_headers, monitor, add_checks,
):
    await add_checks(monitor['id'], (0, True, 1), (60, True, 1), now=NOW)

    # 09:00 em Brasília (-03:00) é 12:00 UTC.
    response = await client.get(
        url(monitor, before='2026-10-27T09:00:00-03:00'), headers=auth_headers,
    )

    assert times(response) == ['2026-10-27T11:00:00Z']


@pytest.mark.parametrize('params, field, message', [
    ({'limit': 0}, 'limit', 'Use um valor de pelo menos 1.'),
    ({'limit': 201}, 'limit', 'Use um valor de no máximo 200.'),
    ({'before': 'ontem'}, 'before', 'Informe uma data e hora válida.'),
    ({'before': '2026-10-27T12:00:00'}, 'before',
     'Informe a data com fuso horário, como 2026-10-27T12:00:00Z.'),
])
async def test_invalid_parameters_are_explained(
    client, auth_headers, monitor, params, field, message,
):
    response = await client.get(url(monitor, **params), headers=auth_headers)

    assert response.status_code == 422
    errors = {e['loc'][-1]: e['msg'] for e in response.json()['detail']}
    assert errors == {field: message}


# --- acesso -----------------------------------------------------------------

async def test_requires_login(client, monitor):
    response = await client.get(url(monitor))

    assert response.status_code == 401


async def test_history_of_someone_else_is_404(
    client, monitor, other_headers, add_checks,
):
    await add_checks(monitor['id'], (0, True, 100))

    response = await client.get(url(monitor), headers=other_headers)

    assert response.status_code == 404
    assert response.json() == {'detail': 'Monitor não encontrado.'}


async def test_deleting_the_monitor_deletes_its_history(
    client, auth_headers, monitor, add_checks, session,
):
    await add_checks(monitor['id'], (0, True, 100), (5, False, None))

    await client.delete(f"/monitors/{monitor['id']}", headers=auth_headers)

    assert await session.scalar(select(func.count(Check.id))) == 0
