import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache

import httpcore
import httpx

from app.url_safety import (
    MESSAGES,
    Resolver,
    UnsafeURLError,
    ensure_public_host,
    resolve_host,
)

USER_AGENT = 'fastapi-monitor/0.1 (+https://github.com/artvsantos)'
DEFAULT_TIMEOUT = 10


@dataclass(frozen=True)
class CheckResult:
    is_up: bool
    status_code: int | None
    response_time_ms: int | None
    error: str | None


async def check_url(
    client: httpx.AsyncClient,
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    clock: Callable[[], float] = time.perf_counter,
) -> CheckResult:
    """Faz uma checagem: um GET na URL, sem seguir redirecionamento.

    2xx e 3xx contam como no ar. Erro de rede não sobe exceção: vira um
    resultado "fora do ar" com a mensagem que vai para o histórico.
    """
    start = clock()
    try:
        # stream: só os cabeçalhos interessam. Sem isso, uma página
        # enorme seria baixada inteira a cada checagem.
        async with client.stream(
            'GET', url,
            headers={'User-Agent': USER_AGENT},
            timeout=timeout,
            follow_redirects=False,
        ) as response:
            elapsed_ms = round((clock() - start) * 1000)
    except httpx.TimeoutException:
        return _down(f'O site não respondeu em {format_seconds(timeout)}.')
    except httpx.ConnectError as error:
        # O filtro de endereços explica por que não conectou; o resto vira
        # a mensagem genérica (o texto do erro de rede vem em inglês).
        reason = str(error)
        if reason not in MESSAGES.values():
            reason = 'Não foi possível conectar ao site.'
        return _down(reason)
    except httpx.HTTPError:
        return _down('Erro ao acessar o site.')

    status_code = response.status_code
    if status_code < 400:
        return CheckResult(True, status_code, elapsed_ms, None)
    return CheckResult(
        False, status_code, elapsed_ms,
        f'O site respondeu com o código {status_code}.',
    )


def _down(error: str) -> CheckResult:
    return CheckResult(
        is_up=False, status_code=None, response_time_ms=None, error=error,
    )


def format_seconds(value: float) -> str:
    unit = 'segundo' if value == 1 else 'segundos'
    return f'{value:g} {unit}'


class PublicOnlyBackend(httpcore.AsyncNetworkBackend):
    """Camada de rede que só conecta em endereço público.

    A cada conexão: resolve o nome, confere os endereços e conecta direto
    no IP conferido. Assim o DNS não tem como responder uma coisa na
    conferência e outra na conexão (DNS rebinding). O HTTPS continua
    validando o certificado pelo nome do site, que o httpcore guarda à
    parte do endereço.
    """

    def __init__(
        self,
        resolve: Resolver = resolve_host,
        inner: httpcore.AsyncNetworkBackend | None = None,
    ):
        self.resolve = resolve
        self.inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, *args, **kwargs):
        try:
            addresses = await ensure_public_host(host, self.resolve)
        except UnsafeURLError as error:
            raise httpcore.ConnectError(error.message) from None

        for address in addresses:
            try:
                return await self.inner.connect_tcp(
                    address, port, *args, **kwargs,
                )
            except httpcore.ConnectError as error:
                last_error = error
        raise last_error

    async def connect_unix_socket(self, *args, **kwargs):
        raise httpcore.ConnectError(MESSAGES['url_internal'])

    async def sleep(self, seconds):
        await self.inner.sleep(seconds)


@cache
def _ssl_context() -> ssl.SSLContext:
    # Carregar os certificados leva ~0,5 s; um contexto serve para todos.
    return httpx.create_ssl_context()


def make_check_client(
    resolve: Resolver = resolve_host,
    *,
    backend: httpcore.AsyncNetworkBackend | None = None,
) -> httpx.AsyncClient:
    """Cliente HTTP do worker, com o filtro de endereços ligado."""
    transport = httpx.AsyncHTTPTransport(verify=_ssl_context())
    # O httpx não aceita trocar a camada de rede pelo construtor, então o
    # pool de conexões é refeito com ela. O teste
    # test_real_network_layer_also_blocks avisa se isso parar de valer
    # numa versão nova do httpx.
    transport._pool = httpcore.AsyncConnectionPool(
        ssl_context=_ssl_context(),
        network_backend=PublicOnlyBackend(resolve, backend),
    )
    # trust_env=False: um HTTP_PROXY no ambiente faria a conexão sair pelo
    # proxy, e o filtro conferiria o endereço dele, não o do site.
    return httpx.AsyncClient(transport=transport, trust_env=False)
