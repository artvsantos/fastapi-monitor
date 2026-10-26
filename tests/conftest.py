import httpx
import pytest

from app.config import Settings
from app.database import Base
from app.main import create_app

EMAIL = 'ana@exemplo.com'
PASSWORD = 'senha-forte-123'


@pytest.fixture
def settings():
    # Banco em memória: cada teste começa vazio e nada fica no disco.
    return Settings(
        database_url='sqlite+aiosqlite://',
        secret_key='chave-so-para-os-testes-com-mais-de-32-bytes',
    )


@pytest.fixture
async def app(settings):
    app = create_app(settings)
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
