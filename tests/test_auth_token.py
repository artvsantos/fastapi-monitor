import jwt

from app import security

from .conftest import EMAIL, PASSWORD

URL = '/auth/token'


async def login(client, username=EMAIL, password=PASSWORD):
    return await client.post(
        URL, data={'username': username, 'password': password},
    )


async def test_login_returns_bearer_token(client, user):
    response = await login(client)
    body = response.json()

    assert response.status_code == 200
    assert body['token_type'] == 'bearer'
    assert body['access_token']


async def test_token_points_to_the_user_and_expires_in_60_minutes(
    client, user, settings,
):
    token = (await login(client)).json()['access_token']

    claims = jwt.decode(
        token, settings.secret_key, algorithms=[security.ALGORITHM],
    )

    assert claims['sub'] == str(user['id'])
    assert claims['exp'] - claims['iat'] == 60 * 60


async def test_login_ignores_email_case_and_spaces(client, user):
    response = await login(client, username='  ANA@Exemplo.com ')

    assert response.status_code == 200


async def test_wrong_password_returns_401(client, user):
    response = await login(client, password='senha-errada')

    assert response.status_code == 401
    assert response.json() == {'detail': 'E-mail ou senha incorretos.'}
    assert response.headers['WWW-Authenticate'] == 'Bearer'


async def test_unknown_email_gets_the_same_answer(client, user):
    response = await login(client, username='ninguem@exemplo.com')

    assert response.status_code == 401
    assert response.json() == {'detail': 'E-mail ou senha incorretos.'}


async def test_unknown_email_still_checks_a_password(
    client, user, monkeypatch,
):
    # Sem isso, responder "não existe" seria bem mais rápido que "senha
    # errada", e medir o tempo revelaria quais e-mails têm conta.
    calls = []
    original = security.verify_password

    def spy(password, hashed):
        calls.append(hashed)
        return original(password, hashed)

    monkeypatch.setattr(security, 'verify_password', spy)

    await login(client, username='ninguem@exemplo.com')

    assert calls == [security.DUMMY_HASH]


async def test_missing_fields_are_required(client):
    response = await client.post(URL, data={})

    assert response.status_code == 422
    assert {e['loc'][-1]: e['msg'] for e in response.json()['detail']} == {
        'username': 'Este campo é obrigatório.',
        'password': 'Este campo é obrigatório.',
    }
