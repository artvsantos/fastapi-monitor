"""O cliente que o worker usa: só conecta em endereço público.

Checar o DNS no cadastro não basta. O dono do domínio pode, depois,
fazer o domínio apontar para 127.0.0.1 ("DNS rebinding"). Por isso o
endereço é conferido de novo a cada conexão, e a conexão vai para o IP
conferido, sem uma segunda consulta ao DNS que poderia dar outra resposta.
"""

import logging

import httpcore
import httpx
import pytest

from app.checker import PublicOnlyBackend, check_url, make_check_client
from app.url_safety import UnsafeURLError

OK_RESPONSE = [b'HTTP/1.1 200 OK\r\n', b'Content-Length: 0\r\n', b'\r\n']


class RecordingBackend(httpcore.AsyncMockBackend):
    """Finge a rede: anota onde conectaria e devolve um 200 pronto."""

    def __init__(self, refuse=()):
        super().__init__(list(OK_RESPONSE))
        self.connected_to = []
        self.refuse = set(refuse)

    async def connect_tcp(self, host, port, *args, **kwargs):
        self.connected_to.append((host, port))
        if host in self.refuse:
            raise httpcore.ConnectError('recusado')
        return await super().connect_tcp(host, port, *args, **kwargs)


def resolver(table):
    async def resolve(host):
        if host not in table:
            raise UnsafeURLError('url_not_found')
        return table[host]

    return resolve


async def check(url, table, backend):
    async with make_check_client(resolver(table), backend=backend) as client:
        return await check_url(client, url)


async def test_connects_to_the_address_that_was_checked():
    backend = RecordingBackend()

    result = await check(
        'http://exemplo.com.br/status', {'exemplo.com.br': ['93.184.216.34']},
        backend,
    )

    assert result.is_up
    assert backend.connected_to == [('93.184.216.34', 80)]


async def test_https_uses_port_443():
    backend = RecordingBackend()

    await check(
        'https://exemplo.com.br/', {'exemplo.com.br': ['93.184.216.34']},
        backend,
    )

    assert backend.connected_to == [('93.184.216.34', 443)]


async def test_domain_that_now_points_inside_is_blocked():
    backend = RecordingBackend()

    result = await check(
        'http://exemplo.com.br/', {'exemplo.com.br': ['127.0.0.1']}, backend,
    )

    assert not result.is_up
    assert result.error == 'Endereços internos não podem ser monitorados.'
    assert backend.connected_to == []


async def test_domain_that_stopped_resolving_is_reported():
    result = await check('http://exemplo.com.br/', {}, RecordingBackend())

    assert not result.is_up
    assert result.error == (
        'Não encontrei esse endereço. Confira se a URL está certa.'
    )


async def test_ip_in_the_url_is_used_directly():
    backend = RecordingBackend()

    result = await check('http://93.184.216.34:8080/', {}, backend)

    assert result.is_up
    assert backend.connected_to == [('93.184.216.34', 8080)]


async def test_tries_the_next_address_when_one_does_not_answer():
    backend = RecordingBackend(refuse={'2a04:4e42::261'})

    result = await check(
        'http://outro-site.com/',
        {'outro-site.com': ['2a04:4e42::261', '151.101.1.69']},
        backend,
    )

    assert result.is_up
    assert backend.connected_to == [
        ('2a04:4e42::261', 80), ('151.101.1.69', 80),
    ]


async def test_reports_failure_when_no_address_answers():
    backend = RecordingBackend(refuse={'93.184.216.34'})

    result = await check(
        'http://exemplo.com.br/', {'exemplo.com.br': ['93.184.216.34']},
        backend,
    )

    assert result.error == 'Não foi possível conectar ao site.'


async def test_real_network_layer_also_blocks():
    # Sem backend de mentira: confere que o filtro está mesmo ligado no
    # cliente do httpx. Se não estivesse, ele tentaria o 127.0.0.1 de
    # verdade e a mensagem seria outra.
    async with make_check_client(
        resolver({'exemplo.com.br': ['127.0.0.1']}),
    ) as client:
        result = await check_url(client, 'http://exemplo.com.br:9/')

    assert result.error == 'Endereços internos não podem ser monitorados.'


@pytest.mark.parametrize(
    'variable', ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'],
)
async def test_ignores_proxy_from_environment(monkeypatch, variable):
    # Um proxy faria a conexão sair por ele, e o filtro conferiria o IP do
    # proxy, não o do site.
    monkeypatch.setenv(variable, 'http://10.0.0.1:3128')
    backend = RecordingBackend()

    await check(
        'http://exemplo.com.br/', {'exemplo.com.br': ['93.184.216.34']},
        backend,
    )

    assert backend.connected_to == [('93.184.216.34', 80)]


async def test_unix_socket_is_refused():
    # Como o socket do Docker, que daria controle da máquina.
    with pytest.raises(httpcore.ConnectError):
        await PublicOnlyBackend().connect_unix_socket('/var/run/docker.sock')


async def test_pauses_are_left_to_the_real_network_layer():
    slept = []

    class Inner(RecordingBackend):
        async def sleep(self, seconds):
            slept.append(seconds)

    await PublicOnlyBackend(inner=Inner()).sleep(0.5)

    assert slept == [0.5]


async def test_request_addresses_are_not_logged(caplog):
    # O httpx anota cada requisição no log com o endereço completo, e o
    # endereço de um webhook (Discord, Slack...) leva um token de acesso.
    httpx_logger = logging.getLogger('httpx')
    original_level = httpx_logger.level
    try:
        async with make_check_client():
            pass
        with caplog.at_level(logging.INFO):
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200),
            )) as client:
                await client.post('https://hooks.com/webhooks/1/token-secreto')
    finally:
        httpx_logger.setLevel(original_level)

    assert 'token-secreto' not in caplog.text
