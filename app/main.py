import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from app import worker
from app.checker import make_check_client
from app.config import Settings
from app.database import make_engine, make_sessionmaker
from app.errors import validation_error_handler
from app.routers import auth, monitors
from app.url_safety import Resolver, resolve_host

DESCRIPTION = """
Cadastre os sites que quer acompanhar. A API checa cada um no intervalo
escolhido, guarda o status e o tempo de resposta, e mostra o histórico e a
porcentagem de tempo no ar.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    state = app.state
    if not state.settings.run_worker_in_api:
        yield
        await state.engine.dispose()
        return

    stop = asyncio.Event()
    async with make_check_client(state.resolver) as client:
        task = asyncio.create_task(worker.run_forever(
            state.sessionmaker, client, state.settings, stop,
        ))
        yield
        # Ao desligar a API, o worker termina a rodada e para.
        stop.set()
        await task
    await state.engine.dispose()


def create_app(
    settings: Settings | None = None, resolver: Resolver = resolve_host,
) -> FastAPI:
    settings = settings or Settings()

    app = FastAPI(
        title='Monitor de Sites',
        description=DESCRIPTION,
        version='0.1.0',
        lifespan=lifespan,
    )
    # Cada app tem o próprio banco: os testes criam um em memória sem
    # mexer no de verdade.
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)
    app.state.sessionmaker = make_sessionmaker(app.state.engine)
    # Trocável nos testes, que usam um DNS de mentira.
    app.state.resolver = resolver

    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(auth.router)
    app.include_router(monitors.router)

    @app.get(
        '/health', summary='Verifica se a API está no ar', tags=['Sistema'],
    )
    async def health():
        return {'status': 'ok'}

    return app


app = create_app()
