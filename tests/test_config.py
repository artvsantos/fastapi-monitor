import pytest
from pydantic import ValidationError

from app.config import DEV_SECRET_KEY, Settings

POSTGRES = 'postgresql+asyncpg://monitor:senha@localhost/monitor'
STRONG_KEY = 'uma-chave-de-producao-com-mais-de-32-caracteres'


def make(**kwargs):
    # _env_file=None: o teste não lê um .env que exista na máquina.
    return Settings(_env_file=None, **kwargs)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('SECRET_KEY', raising=False)


@pytest.mark.parametrize('given, expected', [
    # Como o Neon mostra a URL: sem o driver, e com opções que o asyncpg
    # não conhece (ele recusaria a conexão com TypeError).
    (
        'postgresql://ana:segredo@ep-x.sa-east-1.aws.neon.tech/neondb'
        '?sslmode=require&channel_binding=require',
        'postgresql+asyncpg://ana:segredo@ep-x.sa-east-1.aws.neon.tech/neondb'
        '?ssl=require',
    ),
    # Heroku e outros usam "postgres://".
    ('postgres://ana:segredo@host:5432/db',
     'postgresql+asyncpg://ana:segredo@host:5432/db'),
    # Senha com caractere especial continua escapada.
    ('postgresql://ana:p%40ss@host/db',
     'postgresql+asyncpg://ana:p%40ss@host/db'),
    # O que já está certo fica como está.
    (POSTGRES, POSTGRES),
    ('sqlite+aiosqlite:///./monitor.db', 'sqlite+aiosqlite:///./monitor.db'),
])
def test_database_url_is_adapted_for_asyncpg(given, expected):
    settings = make(database_url=given, secret_key=STRONG_KEY)

    assert settings.database_url == expected


def test_local_sqlite_works_without_secret_key():
    # Para rodar no PC sem configurar nada.
    assert make().secret_key == DEV_SECRET_KEY


@pytest.mark.parametrize('secret_key', [DEV_SECRET_KEY, 'curta-demais'])
def test_real_database_requires_a_real_secret_key(secret_key):
    # Com a chave do repositório (pública), qualquer um geraria um token
    # válido e entraria em qualquer conta.
    with pytest.raises(ValidationError) as error:
        make(database_url=POSTGRES, secret_key=secret_key)

    # A mensagem vai para o log do deploy: não pode mostrar a chave nem a
    # senha do banco.
    assert 'Defina SECRET_KEY' in str(error.value)
    assert secret_key not in str(error.value)
    assert 'senha' not in str(error.value)


def test_real_database_with_a_strong_key_is_accepted():
    settings = make(database_url=POSTGRES, secret_key=STRONG_KEY)

    assert settings.secret_key == STRONG_KEY
