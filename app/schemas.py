from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from email_validator import EmailNotValidError, validate_email
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
)
from pydantic_core import PydanticCustomError

from app.url_safety import MESSAGES, is_ip, is_public_address


def normalize_email(value: str) -> str:
    try:
        result = validate_email(
            value.strip().lower(), check_deliverability=False,
        )
    except EmailNotValidError:
        raise PydanticCustomError(
            'invalid_email', 'Informe um e-mail válido.',
        ) from None
    return result.normalized


Email = Annotated[
    str,
    AfterValidator(normalize_email),
    Field(examples=['ana@exemplo.com']),
]


def _url_error(kind: str, message: str) -> PydanticCustomError:
    return PydanticCustomError(kind, message)


def normalize_url(value: str) -> str:
    """Confere a URL e devolve numa forma única (para o "já monitora").

    Aqui só entra o que dá para ver no texto. Se o domínio aponta para a
    rede interna, quem descobre é o url_safety, consultando o DNS.
    """
    parts = urlsplit(value.strip())
    if parts.scheme.lower() not in ('http', 'https'):
        raise _url_error(
            'url_not_http',
            'Use um endereço que comece com http:// ou https://.',
        )
    try:
        parts.port  # noqa: B018  (porta inválida só aparece ao ler)
    except ValueError:
        raise _url_error('url_invalid', 'Informe uma URL válida.') from None
    host = parts.hostname
    if not host:
        raise _url_error('url_invalid', 'Informe uma URL válida.')
    if parts.username is not None or parts.password is not None:
        # Ficaria salva em texto puro e apareceria no histórico.
        raise _url_error(
            'url_credentials', 'Não coloque usuário e senha na URL.',
        )
    if host == 'localhost' or host.endswith('.localhost'):
        raise _url_error('url_internal', MESSAGES['url_internal'])
    if is_ip(host) and not is_public_address(host):
        raise _url_error('url_internal', MESSAGES['url_internal'])

    # Esquema e host em minúsculas; caminho e query como vieram; sem o
    # "#trecho", que o navegador nem envia ao servidor.
    url = f'{parts.scheme.lower()}://{parts.netloc.lower()}{parts.path or "/"}'
    return f'{url}?{parts.query}' if parts.query else url


def normalize_webhook_url(value: str) -> str:
    # Mesmas regras da URL do site; só a mensagem do endereço interno
    # muda, porque "não podem ser monitorados" não faz sentido aqui.
    try:
        return normalize_url(value)
    except PydanticCustomError as error:
        if error.type == 'url_internal':
            raise _url_error(
                'webhook_internal', MESSAGES['webhook_internal'],
            ) from None
        raise


Url = Annotated[
    str,
    Field(max_length=2048, examples=['https://exemplo.com.br/']),
    AfterValidator(normalize_url),
]
WebhookUrl = Annotated[
    str,
    Field(
        max_length=2048,
        description=(
            'Recebe um POST em JSON quando o site cai ou volta. Opcional.'
        ),
        examples=['https://meu-servidor.com.br/avisos'],
    ),
    AfterValidator(normalize_webhook_url),
]
Name = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
    Field(examples=['Meu site']),
]
Interval = Annotated[
    int,
    Field(ge=1, le=1440, description='De quanto em quanto tempo checar.'),
]


class MonitorCreate(BaseModel):
    name: Name
    url: Url
    interval_minutes: Interval = 5
    is_active: bool = True
    webhook_url: WebhookUrl | None = None


class MonitorUpdate(BaseModel):
    # Default None sem aceitar null: o campo pode faltar (fica como está),
    # mas {"name": null} é recusado. A rota só aplica o que foi enviado.
    name: Name = None
    url: Url = None
    interval_minutes: Interval = None
    is_active: bool = None
    # Este aceita null: é como se remove o webhook.
    webhook_url: WebhookUrl | None = None


class MonitorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    url: str
    interval_minutes: int
    is_active: bool
    created_at: datetime
    is_up: bool | None = Field(
        description='Resultado da última checagem. null: ainda não checado.',
    )
    last_checked_at: datetime | None
    webhook_url: str | None
    webhook_secret: str = Field(
        description=(
            'Para conferir a assinatura dos avisos (cabeçalho '
            'X-Monitor-Signature).'
        ),
    )


class CheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    checked_at: datetime
    is_up: bool
    status_code: int | None
    response_time_ms: int | None
    error: str | None


class CheckPage(BaseModel):
    items: list[CheckOut]
    next_before: datetime | None = Field(
        description=(
            'Para a próxima página, envie este valor em `before`. '
            'null: não há mais checagens.'
        ),
    )


Period = Literal['24h', '7d', '30d']


class MonitorStats(BaseModel):
    period: Period
    since: datetime = Field(description='Início do período.')
    checks: int
    up_checks: int
    uptime_percent: float | None = Field(
        description='Porcentagem de checagens no ar. null: sem checagens.',
    )
    avg_response_ms: int | None = Field(
        description='Tempo médio das checagens no ar.',
    )
    incidents: int = Field(
        description='Quantas vezes o site caiu no período.',
    )


class UserCreate(BaseModel):
    email: Email
    password: str = Field(
        min_length=8, max_length=128,
        description='Entre 8 e 128 caracteres.',
        examples=['uma-senha-forte'],
    )


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = 'bearer'
