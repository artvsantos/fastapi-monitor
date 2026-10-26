from datetime import UTC, datetime, timedelta

import jwt
import pytest
from sqlalchemy import delete

from app import security
from app.models import User

URL = '/auth/me'
NOT_LOGGED_IN = {'detail': 'Faça login para continuar.'}
INVALID_TOKEN = {'detail': 'Token inválido ou expirado.'}


def in_one_hour():
    return datetime.now(UTC) + timedelta(hours=1)


def bearer(token):
    return {'Authorization': f'Bearer {token}'}


async def test_returns_the_logged_in_user(client, user, auth_headers):
    response = await client.get(URL, headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == user


@pytest.mark.parametrize('headers', [
    {},
    {'Authorization': 'Basic YW5hOnNlbmhh'},
    {'Authorization': 'Bearer'},
])
async def test_without_bearer_token_returns_401(client, headers):
    response = await client.get(URL, headers=headers)

    assert response.status_code == 401
    assert response.json() == NOT_LOGGED_IN
    assert response.headers['WWW-Authenticate'] == 'Bearer'


async def test_garbage_token_returns_401(client):
    response = await client.get(URL, headers=bearer('nao-e-um-jwt'))

    assert response.status_code == 401
    assert response.json() == INVALID_TOKEN
    assert response.headers['WWW-Authenticate'] == 'Bearer'


async def test_expired_token_returns_401(client, user, settings):
    yesterday = datetime.now(UTC) - timedelta(days=1)
    token = security.create_access_token(user['id'], settings, now=yesterday)

    response = await client.get(URL, headers=bearer(token))

    assert response.json() == INVALID_TOKEN


async def test_token_signed_with_another_key_returns_401(client, user):
    token = jwt.encode(
        {'sub': str(user['id']), 'exp': in_one_hour()},
        'outra-chave-que-nao-e-a-do-servidor-123456',
        algorithm='HS256',
    )

    response = await client.get(URL, headers=bearer(token))

    assert response.json() == INVALID_TOKEN


async def test_unsigned_token_returns_401(client, user):
    # alg "none" é o truque clássico: token sem assinatura nenhuma.
    token = jwt.encode(
        {'sub': str(user['id']), 'exp': in_one_hour()},
        None,
        algorithm='none',
    )

    response = await client.get(URL, headers=bearer(token))

    assert response.json() == INVALID_TOKEN


@pytest.mark.parametrize('claims', [
    {'sub': '1'},
    {'exp': 'amanha'},
    {'sub': 'ana', 'exp': 'amanha'},
])
async def test_token_without_valid_sub_and_exp_returns_401(
    client, user, settings, claims,
):
    hour = in_one_hour()
    payload = {
        key: (hour if value == 'amanha' else value)
        for key, value in claims.items()
    }
    token = jwt.encode(payload, settings.secret_key, algorithm='HS256')

    response = await client.get(URL, headers=bearer(token))

    assert response.json() == INVALID_TOKEN


async def test_token_of_deleted_user_returns_401(
    client, user, auth_headers, session,
):
    await session.execute(delete(User))
    await session.commit()

    response = await client.get(URL, headers=auth_headers)

    assert response.json() == INVALID_TOKEN
