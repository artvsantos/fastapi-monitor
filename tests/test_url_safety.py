import pytest

from app.url_safety import (
    UnsafeURLError,
    ensure_public_host,
    is_public_address,
    resolve_host,
)


@pytest.mark.parametrize('address', [
    '8.8.8.8',
    '93.184.216.34',
    '2606:4700::1111',
])
def test_internet_addresses_are_public(address):
    assert is_public_address(address)


@pytest.mark.parametrize('address', [
    '127.0.0.1',        # a própria máquina
    '127.10.20.30',
    '0.0.0.0',
    '10.1.2.3',         # redes privadas
    '172.16.0.1',
    '192.168.0.1',
    '169.254.169.254',  # metadados da nuvem (credenciais da AWS ficam aqui)
    '100.64.0.1',       # rede interna de provedor
    '224.0.0.1',        # multicast
    '255.255.255.255',
    '::1',
    'fe80::1',
    'fc00::1',
    '::ffff:127.0.0.1',  # IPv4 "embrulhado" em IPv6
    '::ffff:10.0.0.1',
])
def test_internal_addresses_are_not_public(address):
    assert not is_public_address(address)


def fake_resolver(table):
    async def resolve(host):
        if host not in table:
            raise UnsafeURLError('url_not_found')
        return table[host]

    return resolve


async def test_host_resolving_to_public_addresses_returns_them():
    resolve = fake_resolver({
        'exemplo.com.br': ['93.184.216.34', '2606:2800:21f:cb07::1'],
    })

    addresses = await ensure_public_host('exemplo.com.br', resolve)

    assert addresses == ['93.184.216.34', '2606:2800:21f:cb07::1']


async def test_public_ip_in_the_url_does_not_need_dns():
    await ensure_public_host('93.184.216.34', fake_resolver({}))


async def test_internal_ip_in_the_url_is_blocked_without_dns():
    with pytest.raises(UnsafeURLError) as error:
        await ensure_public_host('10.0.0.1', fake_resolver({}))

    assert error.value.kind == 'url_internal'


@pytest.mark.parametrize('addresses', [
    ['10.0.0.5'],
    # Basta um endereço interno: a conexão poderia cair justo nele.
    ['93.184.216.34', '127.0.0.1'],
])
async def test_host_resolving_to_internal_address_is_blocked(addresses):
    resolve = fake_resolver({'interno.exemplo.com': addresses})

    with pytest.raises(UnsafeURLError) as error:
        await ensure_public_host('interno.exemplo.com', resolve)

    assert error.value.kind == 'url_internal'
    assert error.value.message == (
        'Endereços internos não podem ser monitorados.'
    )


async def test_host_that_does_not_resolve_is_reported():
    with pytest.raises(UnsafeURLError) as error:
        await ensure_public_host('nao-existe.com.br', fake_resolver({}))

    assert error.value.kind == 'url_not_found'
    assert error.value.message == (
        'Não encontrei esse endereço. Confira se a URL está certa.'
    )


async def test_real_resolver_finds_localhost():
    addresses = await resolve_host('localhost')

    assert {'127.0.0.1', '::1'} & set(addresses)


async def test_real_resolver_reports_unknown_host():
    # .invalid é reservado: nunca existe (RFC 6761).
    with pytest.raises(UnsafeURLError) as error:
        await resolve_host('nao-existe.invalid')

    assert error.value.kind == 'url_not_found'
