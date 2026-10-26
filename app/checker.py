import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

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
        return _down(f'O site não respondeu em {_seconds(timeout)}.')
    except httpx.ConnectError:
        return _down('Não foi possível conectar ao site.')
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


def _seconds(value: float) -> str:
    unit = 'segundo' if value == 1 else 'segundos'
    return f'{value:g} {unit}'
