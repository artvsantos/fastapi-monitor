from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app.config import Settings
from app.database import make_engine, make_sessionmaker
from app.errors import validation_error_handler
from app.routers import auth

DESCRIPTION = """
Cadastre os sites que quer acompanhar. A API checa cada um no intervalo
escolhido, guarda o status e o tempo de resposta, e mostra o histórico e a
porcentagem de tempo no ar.
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    app = FastAPI(
        title='Monitor de Sites',
        description=DESCRIPTION,
        version='0.1.0',
    )
    # Cada app tem o próprio banco: os testes criam um em memória sem
    # mexer no de verdade.
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)
    app.state.sessionmaker = make_sessionmaker(app.state.engine)

    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(auth.router)

    @app.get(
        '/health', summary='Verifica se a API está no ar', tags=['Sistema'],
    )
    async def health():
        return {'status': 'ok'}

    return app


app = create_app()
