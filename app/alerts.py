"""Avisos por webhook: um POST quando o site cai ou volta.

O aviso é gravado junto com a checagem que mudou o status (no worker) e
entregue depois, por send_due_alerts. Se o webhook falhar, tenta de novo
com espera crescente. A entrega é "pelo menos uma vez": se o worker cair
logo depois de enviar, o aviso pode ir de novo, então o corpo leva o
alert_id para quem recebe descartar repetidos.
"""

import asyncio
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.checker import USER_AGENT, format_seconds
from app.config import Settings
from app.database import Sessionmaker, utc_now
from app.models import Alert, Check, Monitor
from app.url_safety import MESSAGES

logger = logging.getLogger(__name__)

# Tempo que um aviso fica reservado para quem o pegou. Se o worker cair no
# meio do envio, depois disso o aviso volta para a fila.
DELIVERY_LEASE = timedelta(minutes=5)


def event_for(previous: bool | None, current: bool) -> str | None:
    """O aviso que a mudança de status pede, ou None se não mudou nada.

    Ainda não checado conta como no ar: o monitor novo que já começa fora
    do ar gera aviso; o que começa no ar, não.
    """
    if current == (True if previous is None else previous):
        return None
    return 'up' if current else 'down'


def retry_delay(attempts: int) -> timedelta:
    # 1, 2, 4, 8... minutos: um webhook fora do ar não é bombardeado.
    return timedelta(minutes=2 ** (attempts - 1))


def sign(secret: str, timestamp: str, body: bytes) -> str:
    # O horário entra na conta para que um aviso antigo, capturado por
    # alguém, não possa ser reenviado depois como se fosse novo.
    digest = hmac.new(
        secret.encode(), f'{timestamp}.'.encode() + body, hashlib.sha256,
    ).hexdigest()
    return f'sha256={digest}'


def build_payload(alert: Alert, monitor: Monitor, check: Check) -> dict:
    where = f'{monitor.name} ({monitor.url})'
    if alert.event == 'down':
        message = f'{where} caiu: {check.error}'
    else:
        message = f'{where} voltou ao ar.'
    return {
        'event': alert.event,
        'alert_id': alert.id,
        'message': message,
        'monitor': {
            'id': monitor.id, 'name': monitor.name, 'url': monitor.url,
        },
        'check': {
            'checked_at': check.checked_at.isoformat(),
            'status_code': check.status_code,
            'response_time_ms': check.response_time_ms,
            'error': check.error,
        },
    }


@dataclass(frozen=True)
class Delivery:
    alert_id: int
    event: str
    url: str
    secret: str
    body: bytes


async def claim_due_alerts(
    session: AsyncSession, now: datetime, limit: int,
) -> list[Delivery]:
    """Pega os avisos vencidos e os reserva por DELIVERY_LEASE.

    Só pega o aviso mais antigo pendente de cada monitor: o "voltou" não
    pode chegar antes do "caiu" que ainda está tentando sair.
    """
    earlier = aliased(Alert)
    older_pending = exists().where(
        earlier.monitor_id == Alert.monitor_id,
        earlier.status == 'pending',
        earlier.id < Alert.id,
    )
    rows = (await session.execute(
        select(Alert, Monitor, Check)
        .join(Monitor, Monitor.id == Alert.monitor_id)
        .join(Check, Check.id == Alert.check_id)
        .where(
            Alert.status == 'pending',
            Alert.next_attempt_at <= now,
            ~older_pending,
        )
        .order_by(Alert.id)
        .limit(limit)
        .with_for_update(skip_locked=True, of=Alert),
    )).all()

    deliveries = []
    for alert, monitor, check in rows:
        if monitor.webhook_url is None:
            alert.status = 'cancelled'  # webhook removido depois do aviso
            continue
        alert.next_attempt_at = now + DELIVERY_LEASE
        body = json.dumps(
            build_payload(alert, monitor, check), ensure_ascii=False,
        ).encode()
        deliveries.append(Delivery(
            alert.id, alert.event, monitor.webhook_url,
            monitor.webhook_secret, body,
        ))
    await session.commit()
    return deliveries


async def post_webhook(
    client: httpx.AsyncClient,
    delivery: Delivery,
    *,
    now: datetime,
    timeout: float,
) -> str | None:
    """Envia o aviso. Devolve o motivo da falha, ou None se deu certo."""
    timestamp = str(int(now.timestamp()))
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': USER_AGENT,
        'X-Monitor-Event': delivery.event,
        'X-Monitor-Timestamp': timestamp,
        'X-Monitor-Signature': sign(delivery.secret, timestamp, delivery.body),
    }
    try:
        # Sem seguir redirecionamento: o destino dele não passou pelo
        # filtro de endereços. E sem baixar a resposta, que não interessa.
        async with client.stream(
            'POST', delivery.url,
            content=delivery.body,
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
        ) as response:
            status_code = response.status_code
    except httpx.TimeoutException:
        return f'O webhook não respondeu em {format_seconds(timeout)}.'
    except httpx.ConnectError as error:
        reason = str(error)
        if reason in MESSAGES.values():
            return reason
        return 'Não foi possível conectar ao webhook.'
    except httpx.HTTPError:
        return 'Erro ao enviar o webhook.'

    if 200 <= status_code < 300:
        return None
    return f'O webhook respondeu com o código {status_code}.'


async def record_delivery(
    sessionmaker: Sessionmaker,
    alert_id: int,
    error: str | None,
    *,
    now: datetime,
    settings: Settings,
) -> None:
    async with sessionmaker() as session:
        alert = await session.get(Alert, alert_id, with_for_update=True)
        if alert is None:  # monitor apagado durante o envio
            return
        alert.attempts += 1
        if error is None:
            alert.status = 'sent'
            alert.sent_at = now
        else:
            alert.last_error = error
            if alert.attempts >= settings.webhook_max_attempts:
                alert.status = 'failed'
            else:
                alert.next_attempt_at = now + retry_delay(alert.attempts)
        await session.commit()


async def send_due_alerts(
    sessionmaker: Sessionmaker,
    client: httpx.AsyncClient,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> int:
    """Entrega os avisos vencidos. Devolve quantos tentou enviar."""
    now = now or utc_now()
    async with sessionmaker() as session:
        deliveries = await claim_due_alerts(
            session, now, settings.worker_batch_size,
        )

    semaphore = asyncio.Semaphore(settings.worker_concurrency)

    async def deliver(delivery: Delivery) -> None:
        async with semaphore:
            try:
                error = await post_webhook(
                    client, delivery, now=now, timeout=settings.check_timeout,
                )
                await record_delivery(
                    sessionmaker, delivery.alert_id, error,
                    now=now, settings=settings,
                )
            except Exception:
                # Fica reservado e volta para a fila depois do DELIVERY_LEASE.
                # Só o domínio: o resto do endereço pode ter um token.
                logger.exception('Falha ao enviar o aviso %s (para %s)',
                                 delivery.alert_id,
                                 urlsplit(delivery.url).hostname)

    await asyncio.gather(*(deliver(delivery) for delivery in deliveries))
    return len(deliveries)
