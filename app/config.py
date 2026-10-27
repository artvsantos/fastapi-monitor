from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuração lida das variáveis de ambiente (ou do arquivo .env).

    Os nomes são os mesmos em maiúsculas: DATABASE_URL, SECRET_KEY...
    """

    model_config = SettingsConfigDict(env_file='.env', extra='ignore')

    database_url: str = 'sqlite+aiosqlite:///./monitor.db'
    # Valor só para rodar no PC. Em produção, SECRET_KEY é obrigatória.
    secret_key: str = 'INSECURE-chave-so-para-desenvolvimento-local'
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
