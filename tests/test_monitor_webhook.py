import pytest
from sqlalchemy import select

from app.models import Alert

URL = '/monitors'
HOOK = 'https://hooks.exemplo.com.br/avisos'
INTERNAL = 'O aviso não pode ir para um endereço interno.'


@pytest.fixture(autouse=True)
def hook_dns(dns):
    dns['hooks.exemplo.com.br'] = ['93.184.216.40']


def payload(**kwargs):
    data = {'name': 'Meu site', 'url': 'https://exemplo.com.br/'}
    data.update(kwargs)
    return data


def webhook_message(response):
    errors = {e['loc'][-1]: e['msg'] for e in response.json()['detail']}
    return errors['webhook_url']


async def create(client, headers, **kwargs):
    return await client.post(URL, json=payload(**kwargs), headers=headers)


# --- cadastrar --------------------------------------------------------------

async def test_webhook_is_optional(client, auth_headers):
    response = await create(client, auth_headers)

    assert response.status_code == 201
    assert response.json()['webhook_url'] is None


async def test_webhook_is_saved_normalized(client, auth_headers):
    response = await create(
        client, auth_headers, webhook_url='HTTPS://Hooks.Exemplo.com.br/avisos',
    )

    assert response.status_code == 201
    assert response.json()['webhook_url'] == HOOK


async def test_each_monitor_gets_its_own_signing_secret(
    client, auth_headers,
):
    first = (await create(client, auth_headers)).json()
    second = (await create(
        client, auth_headers, url='https://outro-site.com/',
    )).json()

    assert len(first['webhook_secret']) >= 32
    assert first['webhook_secret'] != second['webhook_secret']


@pytest.mark.parametrize('hook', [
    'http://127.0.0.1:8000/hook',
    'http://localhost/hook',
    'http://169.254.169.254/latest/meta-data/',
    'http://interno.exemplo.com/hook',   # só o DNS revela que é interno
])
async def test_webhook_to_internal_address_is_blocked(
    client, auth_headers, dns, hook,
):
    # O aviso também é uma requisição do servidor: sem o filtro, viraria
    # outra porta para a rede interna (com o corpo que nós montamos).
    dns['interno.exemplo.com'] = ['10.0.0.7']

    response = await create(client, auth_headers, webhook_url=hook)

    assert response.status_code == 422
    assert webhook_message(response) == INTERNAL


@pytest.mark.parametrize('hook, message', [
    ('ftp://hooks.exemplo.com.br/', (
        'Use um endereço que comece com http:// ou https://.'
    )),
    ('https://user:senha@hooks.exemplo.com.br/', (
        'Não coloque usuário e senha na URL.'
    )),
    ('https://nao-existe.com.br/hook', (
        'Não encontrei esse endereço. Confira se a URL está certa.'
    )),
])
async def test_invalid_webhooks_are_rejected(
    client, auth_headers, hook, message,
):
    response = await create(client, auth_headers, webhook_url=hook)

    assert response.status_code == 422
    assert webhook_message(response) == message


# --- editar -----------------------------------------------------------------

async def test_patch_sets_and_removes_the_webhook(
    client, auth_headers, monitor,
):
    path = f"{URL}/{monitor['id']}"

    added = await client.patch(
        path, json={'webhook_url': HOOK}, headers=auth_headers,
    )
    removed = await client.patch(
        path, json={'webhook_url': None}, headers=auth_headers,
    )

    assert added.json()['webhook_url'] == HOOK
    assert removed.status_code == 200
    assert removed.json()['webhook_url'] is None


async def test_patch_validates_the_new_webhook(
    client, auth_headers, monitor, dns,
):
    dns['interno.exemplo.com'] = ['10.0.0.7']

    response = await client.patch(
        f"{URL}/{monitor['id']}",
        json={'webhook_url': 'http://interno.exemplo.com/hook'},
        headers=auth_headers,
    )

    assert response.status_code == 422
    assert webhook_message(response) == INTERNAL


async def test_secret_stays_the_same_when_the_webhook_changes(
    client, auth_headers, monitor,
):
    response = await client.patch(
        f"{URL}/{monitor['id']}", json={'webhook_url': HOOK},
        headers=auth_headers,
    )

    assert response.json()['webhook_secret'] == monitor['webhook_secret']


async def test_changing_the_url_cancels_pending_alerts(
    client, auth_headers, monitor, session, add_checks,
):
    # Os avisos pendentes falam do endereço antigo.
    [check] = await add_checks(monitor['id'], (0, False, None))
    session.add(Alert(
        monitor_id=monitor['id'], check_id=check.id, event='down',
    ))
    await session.commit()

    await client.patch(
        f"{URL}/{monitor['id']}", json={'url': 'https://outro-site.com/'},
        headers=auth_headers,
    )
    session.expire_all()
    alert = await session.scalar(select(Alert))

    assert alert.status == 'cancelled'
