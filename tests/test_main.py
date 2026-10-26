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


async def test_unknown_route_returns_404(client):
    response = await client.get('/nao-existe')

    assert response.status_code == 404
