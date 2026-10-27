"""Coisas usadas pelos testes do worker e dos alertas."""

from datetime import UTC, datetime

import httpx

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def always(status_code):
    return client_for(lambda request: httpx.Response(status_code))
