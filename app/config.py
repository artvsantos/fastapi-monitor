from typing import Self

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

# Valor só para rodar no PC. Com um banco de verdade, é recusado.
DEV_SECRET_KEY = 'INSECURE-chave-so-para-desenvolvimento-local'


class Settings(BaseSettings):
    """Configuração lida das variáveis de ambiente (ou do arquivo .env).

    Os nomes são os mesmos em maiúsculas: DATABASE_URL, SECRET_KEY...
    """

    # hide_input_in_errors: sem isso, o erro de configuração mostraria no
    # log os valores recebidos, inclusive a SECRET_KEY e a senha do banco.
    model_config = SettingsConfigDict(
        env_file='.env', extra='ignore', hide_input_in_errors=True,
    )

    database_url: str = 'sqlite+aiosqlite:///./monitor.db'
    secret_key: str = DEV_SECRET_KEY
    access_token_minutes: int = 60
    # Cada monitor gera uma checagem por minuto no pior caso. O limite
    # impede uma conta só de ocupar o worker inteiro.
    max_monitors_per_user: int = 20

    # Worker
    # Liga o worker dentro do processo da API (para hospedagem que não
    # roda um processo separado, como o Render grátis).
    run_worker_in_api: bool = False
    worker_interval_seconds: float = 10  # pausa entre uma rodada e outra
    worker_batch_size: int = 50          # monitores por rodada
    worker_concurrency: int = 10         # checagens ao mesmo tempo
    check_timeout: float = 10            # segundos por checagem
    # Checagens mais velhas que isso são apagadas (uma vez por hora).
    check_retention_days: int = 30
    # Tentativas de entregar um aviso ao webhook antes de desistir (a
    # espera dobra a cada uma: 1, 2, 4, 8 minutos...).
    webhook_max_attempts: int = 5

    @field_validator('database_url')
    @classmethod
    def adapt_for_asyncpg(cls, value: str) -> str:
        """Aceita a URL como o Neon (e outros) mostram.

        Eles dão "postgresql://...?sslmode=require&channel_binding=...",
        mas o app usa o driver asyncpg, que chama a opção de "ssl" e não
        conhece channel_binding (recusaria a conexão).
        """
        url = make_url(value)
        if url.drivername in ('postgres', 'postgresql'):
            url = url.set(drivername='postgresql+asyncpg')
        if url.drivername != 'postgresql+asyncpg':
            return value
        query = dict(url.query)
        if 'sslmode' in query:
            query['ssl'] = query.pop('sslmode')
        query.pop('channel_binding', None)
        return url.set(query=query).render_as_string(hide_password=False)

    @model_validator(mode='after')
    def require_real_secret_key(self) -> Self:
        # A chave padrão está no repositório, que é público: com ela,
        # qualquer um assinaria um token e entraria em qualquer conta.
        uses_real_database = not self.database_url.startswith('sqlite')
        weak = self.secret_key == DEV_SECRET_KEY or len(self.secret_key) < 32
        if uses_real_database and weak:
            raise ValueError(
                'Defina SECRET_KEY com pelo menos 32 caracteres. Para gerar '
                'uma: python -c "import secrets; '
                'print(secrets.token_urlsafe(32))"',
            )
        return self
