"""Proteção contra SSRF.

O servidor acessa as URLs que as pessoas cadastram. Sem este filtro,
alguém cadastraria http://127.0.0.1:5432 ou http://169.254.169.254 e
usaria o servidor para chegar ao banco, aos serviços internos ou às
credenciais da nuvem, que só são acessíveis de dentro da rede.
"""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable

MESSAGES = {
    'url_internal': 'Endereços internos não podem ser monitorados.',
    'url_not_found': (
        'Não encontrei esse endereço. Confira se a URL está certa.'
    ),
    'webhook_internal': 'O aviso não pode ir para um endereço interno.',
}

Resolver = Callable[[str], Awaitable[list[str]]]


class UnsafeURLError(Exception):
    def __init__(self, kind: str):
        self.kind = kind
        self.message = MESSAGES[kind]
        super().__init__(self.message)


def parse_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    ip = ipaddress.ip_address(host)
    # ::ffff:127.0.0.1 é o 127.0.0.1 escrito em IPv6.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def is_public_address(address: str) -> bool:
    ip = parse_ip(address)
    # is_global já exclui localhost, redes privadas, link-local (onde
    # ficam os metadados da nuvem), a faixa 100.64/10 e as reservadas.
    return ip.is_global and not ip.is_multicast


def is_ip(host: str) -> bool:
    try:
        parse_ip(host)
    except ValueError:
        return False
    return True


async def resolve_host(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        results = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise UnsafeURLError('url_not_found') from None
    return sorted({result[4][0] for result in results})


async def ensure_public_host(
    host: str, resolve: Resolver = resolve_host,
) -> list[str]:
    """Devolve os endereços do host, todos públicos, ou levanta
    UnsafeURLError se o host for (ou apontar para) a rede interna.

    Um domínio público pode apontar para 10.0.0.7, então o nome não basta:
    vale o endereço que o DNS devolve. Basta um interno para bloquear.
    """
    addresses = [host] if is_ip(host) else await resolve(host)
    if not all(is_public_address(address) for address in addresses):
        raise UnsafeURLError('url_internal')
    return addresses
