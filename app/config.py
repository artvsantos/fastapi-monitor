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
