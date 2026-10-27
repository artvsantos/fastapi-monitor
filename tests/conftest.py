from datetime import timedelta

import httpx
import pytest
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from app import security
from app.config import Settings
from app.database import Base, utc_now
from app.main import create_app
from app.models import Check
from app.url_safety import UnsafeURLError

EMAIL = 'ana@exemplo.com'
PASSWORD = 'senha-forte-123'


@pytest.fixture(autouse=True)
def fast_password_hash(monkeypatch):
    # O argon2 de verdade leva ~50 ms por senha, de propósito. Nos testes,
    # que criam contas o tempo todo, usa o mesmo algoritmo bem mais leve.
    monkeypatch.setattr(security, 'password_hash', PasswordHash((
        Argon2Hasher(time_cost=1, memory_cost=8, parallelism=1),
    )))


@pytest.fixture
def settings():
    # Banco em memória: cada teste começa vazio e nada fica no disco.
    return Settings(
        database_url='sqlite+aiosqlite://',
        secret_key='chave-so-para-os-testes-com-mais-de-32-bytes',
    )


@pytest.fixture
def dns():
    # DNS de mentira: os testes não dependem da internet. Cada teste pode
    # acrescentar nomes neste dicionário.
    return {
        'exemplo.com.br': ['93.184.216.34'],
        'outro-site.com': ['151.101.1.69', '2a04:4e42::261'],
    }


@pytest.fixture
async def app(settings, dns):
    async def fake_resolver(host):
        if host not in dns:
            raise UnsafeURLError('url_not_found')
        return dns[host]

    app = create_app(settings, resolver=fake_resolver)
    async with app.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield app
    await app.state.engine.dispose()


@pytest.fixture
async def client(app):
    # Cliente async no mesmo loop dos testes. O TestClient do FastAPI roda
    # o app em outra thread, o que briga com o banco async.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url='http://test',
    ) as client:
        yield client


@pytest.fixture
async def session(app):
    async with app.state.sessionmaker() as session:
        yield session


@pytest.fixture
async def user(client):
    response = await client.post(
        '/auth/register', json={'email': EMAIL, 'password': PASSWORD},
    )
    return response.json()


@pytest.fixture
async def token(client, user):
    response = await client.post(
        '/auth/token', data={'username': EMAIL, 'password': PASSWORD},
    )
    return response.json()['access_token']


@pytest.fixture
def auth_headers(token):
    return {'Authorization': f'Bearer {token}'}


@pytest.fixture
async def other_headers(client):
    """Login de outra pessoa, para testar o acesso ao que não é dela."""
    login = {'username': 'bruno@exemplo.com', 'password': 'outra-senha-123'}
    await client.post('/auth/register', json={
        'email': login['username'], 'password': login['password'],
    })
    token = (await client.post('/auth/token', data=login)).json()
    return {'Authorization': f"Bearer {token['access_token']}"}


@pytest.fixture
async def monitor(client, auth_headers):
    response = await client.post('/monitors', json={
        'name': 'Meu site', 'url': 'https://exemplo.com.br/',
        'interval_minutes': 5,
    }, headers=auth_headers)
    return response.json()


@pytest.fixture
def add_checks(session):
    """Grava checagens direto no banco: (minutos atrás, no ar?, ms)."""

    async def add(monitor_id, *checks, now=None):
        now = now or utc_now()
        rows = [
            Check(
                monitor_id=monitor_id,
                checked_at=now - timedelta(minutes=minutes_ago),
                is_up=is_up,
                status_code=200 if is_up else 503,
                response_time_ms=ms,
                error=None if is_up else 'O site respondeu com o código 503.',
            )
            for minutes_ago, is_up, ms in checks
        ]
        session.add_all(rows)
        await session.commit()
        return rows

    return add
