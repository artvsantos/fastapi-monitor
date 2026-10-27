from datetime import timedelta

import pytest

from app.database import utc_now
from app.models import Monitor

URL = '/monitors'
SITE = 'https://exemplo.com.br/'
NOT_FOUND = {'detail': 'Monitor não encontrado.'}


def payload(**kwargs):
    data = {'name': 'Meu site', 'url': SITE, 'interval_minutes': 5}
    data.update(kwargs)
    return data


def url_message(response):
    return {e['loc'][-1]: e['msg'] for e in response.json()['detail']}['url']


@pytest.fixture
async def other_headers(client):
    login = {'username': 'bruno@exemplo.com', 'password': 'outra-senha-123'}
    await client.post('/auth/register', json={
        'email': login['username'], 'password': login['password'],
    })
    token = (await client.post('/auth/token', data=login)).json()
    return {'Authorization': f"Bearer {token['access_token']}"}


@pytest.fixture
async def monitor(client, auth_headers):
    response = await client.post(URL, json=payload(), headers=auth_headers)
    return response.json()


# --- acesso ---------------------------------------------------------------

@pytest.mark.parametrize('method, path', [
    ('get', URL),
    ('post', URL),
    ('get', f'{URL}/1'),
    ('patch', f'{URL}/1'),
    ('delete', f'{URL}/1'),
])
async def test_every_route_requires_login(client, method, path):
    response = await client.request(method.upper(), path, json=payload())

    assert response.status_code == 401
    assert response.json() == {'detail': 'Faça login para continuar.'}


async def test_routes_are_grouped_in_portuguese_in_the_docs(client):
    paths = (await client.get('/openapi.json')).json()['paths']

    assert paths['/monitors']['post']['tags'] == ['Monitores']
    assert paths['/monitors']['post']['summary'] == 'Cadastra um site'


# --- criar ------------------------------------------------------------------

async def test_creates_monitor(client, auth_headers):
    response = await client.post(URL, json=payload(), headers=auth_headers)
    body = response.json()

    assert response.status_code == 201
    assert set(body) == {
        'id', 'name', 'url', 'interval_minutes', 'is_active', 'created_at',
        'is_up', 'last_checked_at',
    }
    assert body['name'] == 'Meu site'
    assert body['url'] == SITE
    assert body['interval_minutes'] == 5
    assert body['is_active'] is True
    # Ainda não foi checado: nem no ar, nem fora.
    assert body['is_up'] is None
    assert body['last_checked_at'] is None


async def test_new_monitor_is_checked_right_away(
    client, auth_headers, session,
):
    before = utc_now()
    response = await client.post(URL, json=payload(), headers=auth_headers)

    saved = await session.get(Monitor, response.json()['id'])

    assert before <= saved.next_check_at <= utc_now()


async def test_interval_defaults_to_5_minutes(client, auth_headers):
    data = payload()
    del data['interval_minutes']

    response = await client.post(URL, json=data, headers=auth_headers)

    assert response.json()['interval_minutes'] == 5


@pytest.mark.parametrize('sent, saved', [
    ('  HTTPS://Exemplo.COM.br/Pagina?b=2  ', 'https://exemplo.com.br/Pagina?b=2'),
    ('https://exemplo.com.br', 'https://exemplo.com.br/'),
    ('http://exemplo.com.br:8080/status#topo', 'http://exemplo.com.br:8080/status'),
])
async def test_url_is_normalized(client, auth_headers, sent, saved):
    response = await client.post(
        URL, json=payload(url=sent), headers=auth_headers,
    )

    assert response.status_code == 201
    assert response.json()['url'] == saved


async def test_name_is_trimmed(client, auth_headers):
    response = await client.post(
        URL, json=payload(name='  Loja  '), headers=auth_headers,
    )

    assert response.json()['name'] == 'Loja'


@pytest.mark.parametrize('field, value, message', [
    ('name', '   ', 'Este campo não pode ficar vazio.'),
    ('name', 'x' * 101, 'Use no máximo 100 caracteres.'),
    ('interval_minutes', 0, 'Use um valor de pelo menos 1.'),
    ('interval_minutes', 1441, 'Use um valor de no máximo 1440.'),
    ('interval_minutes', 'toda hora', 'Informe um número inteiro.'),
    ('interval_minutes', 2.5, 'Informe um número inteiro.'),
    ('is_active', 'talvez', 'Use true ou false.'),
])
async def test_fields_are_validated(
    client, auth_headers, field, value, message,
):
    response = await client.post(
        URL, json=payload(**{field: value}), headers=auth_headers,
    )

    assert response.status_code == 422
    errors = {e['loc'][-1]: e['msg'] for e in response.json()['detail']}
    assert errors == {field: message}


@pytest.mark.parametrize('url, message', [
    ('exemplo.com.br', 'Use um endereço que comece com http:// ou https://.'),
    ('ftp://exemplo.com.br/', 'Use um endereço que comece com http:// ou https://.'),
    ('file:///etc/passwd', 'Use um endereço que comece com http:// ou https://.'),
    ('javascript:alert(1)', 'Use um endereço que comece com http:// ou https://.'),
    ('https://', 'Informe uma URL válida.'),
    ('https://exemplo.com.br:porta/', 'Informe uma URL válida.'),
    ('https://exemplo.com.br/' + 'a' * 2048, 'Use no máximo 2048 caracteres.'),
    ('https://ana:senha@exemplo.com.br/',
     'Não coloque usuário e senha na URL.'),
])
async def test_invalid_urls_are_rejected(client, auth_headers, url, message):
    response = await client.post(
        URL, json=payload(url=url), headers=auth_headers,
    )

    assert response.status_code == 422
    assert url_message(response) == message


# --- SSRF -------------------------------------------------------------------
# Sem isso, qualquer pessoa usaria o servidor para acessar a rede interna
# dele: o banco, o painel da hospedagem, os metadados da nuvem...

@pytest.mark.parametrize('url', [
    'http://127.0.0.1/',
    'http://localhost:5432/',
    'http://painel.localhost/',
    'http://10.0.0.1/admin',
    'http://192.168.0.1/',
    'http://169.254.169.254/latest/meta-data/',
    'http://[::1]:8000/',
    'http://[::ffff:127.0.0.1]/',
    'http://0.0.0.0/',
])
async def test_internal_addresses_are_blocked(client, auth_headers, url):
    response = await client.post(
        URL, json=payload(url=url), headers=auth_headers,
    )

    assert response.status_code == 422
    assert url_message(response) == (
        'Endereços internos não podem ser monitorados.'
    )


@pytest.mark.parametrize('host, addresses', [
    # Domínio público que aponta para dentro da rede.
    ('interno.exemplo.com', ['10.0.0.7']),
    # No Linux, "2130706433" e "127.1" viram 127.0.0.1 ao resolver.
    ('2130706433', ['127.0.0.1']),
    ('127.1', ['127.0.0.1']),
    # Nome de serviço do docker-compose, como o do banco.
    ('postgres', ['172.18.0.2']),
])
async def test_names_that_resolve_to_internal_addresses_are_blocked(
    client, auth_headers, dns, host, addresses,
):
    dns[host] = addresses

    response = await client.post(
        URL, json=payload(url=f'http://{host}/'), headers=auth_headers,
    )

    assert response.status_code == 422
    assert url_message(response) == (
        'Endereços internos não podem ser monitorados.'
    )


async def test_public_ip_address_is_allowed(client, auth_headers):
    response = await client.post(
        URL, json=payload(url='http://93.184.216.34/'), headers=auth_headers,
    )

    assert response.status_code == 201


async def test_unknown_domain_is_reported(client, auth_headers):
    response = await client.post(
        URL, json=payload(url='https://nao-existe.com.br/'),
        headers=auth_headers,
    )

    assert response.status_code == 422
    assert url_message(response) == (
        'Não encontrei esse endereço. Confira se a URL está certa.'
    )


async def test_blocked_url_is_not_saved(client, auth_headers):
    await client.post(
        URL, json=payload(url='http://127.0.0.1/'), headers=auth_headers,
    )

    response = await client.get(URL, headers=auth_headers)

    assert response.json() == []


# --- regras de conta --------------------------------------------------------

async def test_same_url_twice_returns_409(client, auth_headers, monitor):
    response = await client.post(
        URL, json=payload(name='De novo'), headers=auth_headers,
    )

    assert response.status_code == 409
    assert response.json() == {'detail': 'Você já monitora esta URL.'}


async def test_two_people_can_monitor_the_same_url(
    client, monitor, other_headers,
):
    response = await client.post(URL, json=payload(), headers=other_headers)

    assert response.status_code == 201


async def test_monitor_limit_per_account(client, auth_headers, settings):
    settings.max_monitors_per_user = 2
    for path in ('a', 'b'):
        await client.post(
            URL, json=payload(url=f'{SITE}{path}'), headers=auth_headers,
        )

    response = await client.post(
        URL, json=payload(url=f'{SITE}c'), headers=auth_headers,
    )

    assert response.status_code == 409
    assert response.json() == {
        'detail': 'Você chegou ao limite de 2 monitores por conta.',
    }


# --- listar e ver -----------------------------------------------------------

async def test_lists_only_my_monitors_oldest_first(
    client, auth_headers, other_headers,
):
    first = (await client.post(
        URL, json=payload(name='Primeiro'), headers=auth_headers,
    )).json()
    second = (await client.post(
        URL, json=payload(name='Segundo', url='https://outro-site.com/'),
        headers=auth_headers,
    )).json()
    await client.post(
        URL, json=payload(name='Do Bruno'), headers=other_headers,
    )

    response = await client.get(URL, headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == [first, second]


async def test_shows_one_monitor(client, auth_headers, monitor):
    response = await client.get(f"{URL}/{monitor['id']}", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == monitor


# --- editar -----------------------------------------------------------------

async def test_patch_changes_only_the_fields_sent(
    client, auth_headers, monitor,
):
    response = await client.patch(
        f"{URL}/{monitor['id']}",
        json={'interval_minutes': 30, 'is_active': False},
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        **monitor, 'interval_minutes': 30, 'is_active': False,
    }


@pytest.mark.parametrize('new_url', [
    'http://10.0.0.1/',              # barrado já na validação do texto
    'http://interno.exemplo.com/',   # só o DNS revela que é interno
])
async def test_patch_validates_the_new_url(
    client, auth_headers, monitor, dns, new_url,
):
    dns['interno.exemplo.com'] = ['10.0.0.7']

    response = await client.patch(
        f"{URL}/{monitor['id']}", json={'url': new_url},
        headers=auth_headers,
    )
    saved = await client.get(f"{URL}/{monitor['id']}", headers=auth_headers)

    assert response.status_code == 422
    assert url_message(response) == (
        'Endereços internos não podem ser monitorados.'
    )
    assert saved.json()['url'] == SITE


async def test_patch_to_an_url_already_monitored_returns_409(
    client, auth_headers, monitor,
):
    other = (await client.post(
        URL, json=payload(url='https://outro-site.com/'), headers=auth_headers,
    )).json()

    response = await client.patch(
        f"{URL}/{other['id']}", json={'url': SITE}, headers=auth_headers,
    )

    assert response.status_code == 409
    assert response.json() == {'detail': 'Você já monitora esta URL.'}


async def set_checked(session, monitor_id, **values):
    saved = await session.get(Monitor, monitor_id)
    for field, value in values.items():
        setattr(saved, field, value)
    await session.commit()


async def test_changing_the_url_starts_over(
    client, auth_headers, monitor, session,
):
    # O status era do endereço antigo; o novo é checado logo.
    tomorrow = utc_now() + timedelta(days=1)
    await set_checked(
        session, monitor['id'], is_up=False, last_checked_at=utc_now(),
        next_check_at=tomorrow,
    )

    response = await client.patch(
        f"{URL}/{monitor['id']}", json={'url': 'https://outro-site.com/'},
        headers=auth_headers,
    )
    session.expire_all()
    saved = await session.get(Monitor, monitor['id'])

    assert response.json()['is_up'] is None
    assert response.json()['last_checked_at'] is None
    assert saved.next_check_at < tomorrow


async def test_reactivating_checks_right_away(
    client, auth_headers, monitor, session,
):
    tomorrow = utc_now() + timedelta(days=1)
    await set_checked(
        session, monitor['id'], is_active=False, next_check_at=tomorrow,
    )

    await client.patch(
        f"{URL}/{monitor['id']}", json={'is_active': True},
        headers=auth_headers,
    )
    session.expire_all()
    saved = await session.get(Monitor, monitor['id'])

    assert saved.next_check_at < tomorrow


async def test_other_changes_keep_the_schedule(
    client, auth_headers, monitor, session,
):
    tomorrow = utc_now() + timedelta(days=1)
    await set_checked(
        session, monitor['id'], is_up=True, next_check_at=tomorrow,
    )

    response = await client.patch(
        f"{URL}/{monitor['id']}",
        json={'name': 'Novo nome', 'is_active': True},
        headers=auth_headers,
    )
    session.expire_all()
    saved = await session.get(Monitor, monitor['id'])

    assert response.json()['is_up'] is True
    assert saved.next_check_at == tomorrow


@pytest.mark.parametrize('field', ['name', 'url', 'interval_minutes'])
async def test_patch_does_not_accept_null(
    client, auth_headers, monitor, field,
):
    response = await client.patch(
        f"{URL}/{monitor['id']}", json={field: None}, headers=auth_headers,
    )

    assert response.status_code == 422


# --- apagar -----------------------------------------------------------------

async def test_delete_removes_the_monitor(client, auth_headers, monitor):
    response = await client.delete(
        f"{URL}/{monitor['id']}", headers=auth_headers,
    )
    after = await client.get(f"{URL}/{monitor['id']}", headers=auth_headers)

    assert response.status_code == 204
    assert after.status_code == 404


# --- monitor de outra pessoa ------------------------------------------------

@pytest.mark.parametrize('method', ['get', 'patch', 'delete'])
async def test_monitor_of_someone_else_is_404(
    client, monitor, other_headers, auth_headers, method,
):
    # 404, e não 403: nem dá para descobrir quais ids existem.
    response = await client.request(
        method.upper(), f"{URL}/{monitor['id']}",
        json={'name': 'Invadido'}, headers=other_headers,
    )
    still_there = await client.get(
        f"{URL}/{monitor['id']}", headers=auth_headers,
    )

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert still_there.json() == monitor


async def test_unknown_id_is_404(client, auth_headers):
    response = await client.get(f'{URL}/999', headers=auth_headers)

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
