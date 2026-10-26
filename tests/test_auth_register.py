from datetime import datetime

import pytest
from sqlalchemy import select

from app.models import User

from .conftest import EMAIL, PASSWORD

URL = '/auth/register'


def messages(response):
    return {e['loc'][-1]: e['msg'] for e in response.json()['detail']}


async def test_creates_account(client):
    response = await client.post(
        URL, json={'email': EMAIL, 'password': PASSWORD},
    )
    body = response.json()

    assert response.status_code == 201
    assert set(body) == {'id', 'email', 'created_at'}
    assert body['email'] == EMAIL


async def test_created_at_is_in_utc(client):
    response = await client.post(
        URL, json={'email': EMAIL, 'password': PASSWORD},
    )
    created_at = datetime.fromisoformat(response.json()['created_at'])

    assert created_at.utcoffset().total_seconds() == 0


async def test_password_is_stored_hashed(client, session):
    await client.post(URL, json={'email': EMAIL, 'password': PASSWORD})

    user = await session.scalar(select(User))

    assert user.hashed_password != PASSWORD
    assert user.hashed_password.startswith('$argon2')


async def test_email_is_saved_in_lowercase_without_spaces(client):
    response = await client.post(
        URL, json={'email': '  Ana@Exemplo.COM ', 'password': PASSWORD},
    )

    assert response.json()['email'] == 'ana@exemplo.com'


async def test_duplicate_email_returns_409_ignoring_case(client, user):
    response = await client.post(
        URL, json={'email': 'ANA@exemplo.com', 'password': PASSWORD},
    )

    assert response.status_code == 409
    assert response.json() == {
        'detail': 'Já existe uma conta com este e-mail.',
    }


@pytest.mark.parametrize('email', [
    'ana', 'ana@', '@exemplo.com', 'ana@exemplo', 'ana exemplo@a.com', '',
])
async def test_invalid_email_is_rejected(client, email):
    response = await client.post(
        URL, json={'email': email, 'password': PASSWORD},
    )

    assert response.status_code == 422
    assert messages(response) == {'email': 'Informe um e-mail válido.'}


@pytest.mark.parametrize('password, message', [
    ('a' * 7, 'Use pelo menos 8 caracteres.'),
    ('a' * 129, 'Use no máximo 128 caracteres.'),
])
async def test_password_length_is_checked(client, password, message):
    response = await client.post(
        URL, json={'email': EMAIL, 'password': password},
    )

    assert response.status_code == 422
    assert messages(response) == {'password': message}


async def test_missing_fields_are_required(client):
    response = await client.post(URL, json={})

    assert messages(response) == {
        'email': 'Este campo é obrigatório.',
        'password': 'Este campo é obrigatório.',
    }


async def test_validation_error_does_not_echo_the_password(client):
    response = await client.post(
        URL, json={'email': 'ana-sem-arroba', 'password': 'segredo'},
    )

    assert 'segredo' not in response.text
    assert 'ana-sem-arroba' not in response.text


async def test_body_that_is_not_an_object_is_rejected(client):
    response = await client.post(URL, json=['ana@exemplo.com'])

    assert response.status_code == 422
    assert response.json()['detail'][0]['msg'] == 'Envie um objeto JSON.'


async def test_body_that_is_not_json_is_rejected(client):
    response = await client.post(
        URL, content='{email:', headers={'Content-Type': 'application/json'},
    )

    assert response.status_code == 422
    assert response.json()['detail'][0]['msg'] == (
        'O corpo da requisição não é um JSON válido.'
    )
