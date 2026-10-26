import httpx
import pytest

from app.checker import CheckResult, check_url

URL = 'https://exemplo.com.br/'


def make_client(handler, **kwargs):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)


def respond_with(status_code):
    return make_client(lambda request: httpx.Response(status_code))


def fail_with(error_class):
    def handler(request):
        raise error_class('falhou', request=request)

    return make_client(handler)


class BodyThatMustNotBeRead(httpx.AsyncByteStream):
    async def __aiter__(self):
        raise AssertionError('o corpo da página não devia ser baixado')
        yield b''  # pragma: no cover


@pytest.mark.parametrize('status_code', [200, 204, 301, 302, 304])
async def test_2xx_and_3xx_count_as_up(status_code):
    async with respond_with(status_code) as client:
        result = await check_url(client, URL)

    assert result.is_up
    assert result.status_code == status_code
    assert result.error is None


@pytest.mark.parametrize('status_code', [400, 404, 500, 503])
async def test_4xx_and_5xx_count_as_down(status_code):
    async with respond_with(status_code) as client:
        result = await check_url(client, URL)

    assert not result.is_up
    assert result.status_code == status_code
    assert result.error == f'O site respondeu com o código {status_code}.'


async def test_measures_response_time_in_milliseconds():
    ticks = iter([10.0, 10.25])

    async with respond_with(200) as client:
        result = await check_url(client, URL, clock=lambda: next(ticks))

    assert result.response_time_ms == 250


async def test_sends_one_get_and_does_not_follow_redirects():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(301, headers={'Location': 'https://outro.com/'})

    # Mesmo com o client configurado para seguir, a checagem não segue:
    # o monitor diz se aquela URL responde, não a página final.
    async with make_client(handler, follow_redirects=True) as client:
        result = await check_url(client, URL)

    assert [(r.method, str(r.url)) for r in requests] == [('GET', URL)]
    assert result.status_code == 301


async def test_identifies_itself_in_the_user_agent():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200)

    async with make_client(handler) as client:
        await check_url(client, URL)

    assert requests[0].headers['User-Agent'].startswith('fastapi-monitor/')


async def test_does_not_download_the_page_body():
    def handler(request):
        return httpx.Response(200, stream=BodyThatMustNotBeRead())

    async with make_client(handler) as client:
        result = await check_url(client, URL)

    assert result.is_up


@pytest.mark.parametrize('timeout, expected', [(None, 10), (5, 5)])
async def test_applies_the_timeout(timeout, expected):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200)

    kwargs = {} if timeout is None else {'timeout': timeout}
    async with make_client(handler) as client:
        await check_url(client, URL, **kwargs)

    assert requests[0].extensions['timeout'] == {
        'connect': expected, 'read': expected,
        'write': expected, 'pool': expected,
    }


@pytest.mark.parametrize('timeout, message', [
    (5, 'O site não respondeu em 5 segundos.'),
    (1, 'O site não respondeu em 1 segundo.'),
    (2.5, 'O site não respondeu em 2.5 segundos.'),
])
async def test_timeout_counts_as_down(timeout, message):
    async with fail_with(httpx.ReadTimeout) as client:
        result = await check_url(client, URL, timeout=timeout)

    assert result == CheckResult(
        is_up=False, status_code=None, response_time_ms=None, error=message,
    )


@pytest.mark.parametrize('error_class, message', [
    (httpx.ConnectError, 'Não foi possível conectar ao site.'),
    (httpx.ConnectTimeout, 'O site não respondeu em 10 segundos.'),
    (httpx.RemoteProtocolError, 'Erro ao acessar o site.'),
])
async def test_network_errors_count_as_down(error_class, message):
    async with fail_with(error_class) as client:
        result = await check_url(client, URL)

    assert not result.is_up
    assert result.status_code is None
    assert result.response_time_ms is None
    assert result.error == message
