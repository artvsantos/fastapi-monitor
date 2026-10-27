import asyncio

import pytest

from app import worker
from app.main import create_app


async def test_health_returns_ok(client):
    response = await client.get('/health')

    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}


async def test_docs_are_in_portuguese(client):
    info = (await client.get('/openapi.json')).json()['info']

    assert info['title'] == 'Monitor de Sites'
    assert 'tempo no ar' in info['description']


async def test_health_is_listed_in_portuguese_in_the_docs(client):
    paths = (await client.get('/openapi.json')).json()['paths']

    assert paths['/health']['get']['summary'] == 'Verifica se a API está no ar'
    assert paths['/health']['get']['tags'] == ['Sistema']


async def test_home_page_opens_the_docs(client):
    # Quem abre o endereço do deploy cai direto na documentação.
    response = await client.get('/')

    assert response.status_code == 307
    assert response.headers['location'] == '/docs'


async def test_unknown_route_returns_404(client):
    response = await client.get('/nao-existe')

    assert response.status_code == 404


@pytest.mark.parametrize('enabled', [True, False])
async def test_worker_can_run_inside_the_api(
    settings, monkeypatch, enabled,
):
    # No Render grátis não dá para ter um processo só para o worker.
    settings.run_worker_in_api = enabled
    events = []

    async def fake_run_forever(sessionmaker, client, settings_, stop):
        events.append('começou')
        await stop.wait()
        events.append('parou')

    monkeypatch.setattr(worker, 'run_forever', fake_run_forever)
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        await asyncio.sleep(0)
        assert events == (['começou'] if enabled else [])

    assert events == (['começou', 'parou'] if enabled else [])
