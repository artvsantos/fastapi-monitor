"""Worker: checa os sites no intervalo de cada um e envia os avisos.

Roda como processo próprio (python -m app.worker) ou dentro da API, com
RUN_WORKER_IN_API=1. A fila é a própria tabela de monitores: cada rodada
pega os que estão com next_check_at vencido.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import event_for, send_due_alerts
from app.checker import CheckResult, check_url, make_check_client
from app.config import Settings
from app.database import (
    Sessionmaker,
    make_engine,
    make_sessionmaker,
    utc_now,
)
from app.models import Alert, Check, Monitor

logger = logging.getLogger(__name__)


async def claim_due_monitors(
    session: AsyncSession, now: datetime, limit: int,
) -> list[tuple[int, str]]:
    """Pega os monitores vencidos e já agenda a próxima checagem deles.

    No Postgres, SKIP LOCKED faz dois workers rodando ao mesmo tempo
    pegarem monitores diferentes, em vez de um esperar o outro (ou os
    dois checarem o mesmo site). O SQLite ignora essa parte.
    """
    monitors = (await session.scalars(
        select(Monitor)
        .where(Monitor.is_active, Monitor.next_check_at <= now)
        .order_by(Monitor.next_check_at)
        .limit(limit)
        .with_for_update(skip_locked=True),
    )).all()
    for monitor in monitors:
        monitor.next_check_at = now + timedelta(
            minutes=monitor.interval_minutes,
        )
    await session.commit()
    return [(monitor.id, monitor.url) for monitor in monitors]


async def save_result(
    sessionmaker: Sessionmaker,
    monitor_id: int,
    url: str,
    result: CheckResult,
    checked_at: datetime,
) -> None:
    async with sessionmaker() as session:
        # with_for_update: ninguém apaga o monitor entre ler e gravar.
        monitor = await session.get(Monitor, monitor_id, with_for_update=True)
        # Apagado ou com a URL trocada durante a checagem: o resultado
        # não vale para o que está cadastrado agora.
        if monitor is None or monitor.url != url:
            return
        check = Check(
            monitor_id=monitor_id,
            checked_at=checked_at,
            is_up=result.is_up,
            status_code=result.status_code,
            response_time_ms=result.response_time_ms,
            error=result.error,
        )
        session.add(check)
        # Só a mudança de status gera aviso: fora do ar por dez checagens
        # seguidas é um aviso só. Vai na mesma transação da checagem.
        event = event_for(monitor.is_up, result.is_up)
        if event and monitor.webhook_url:
            await session.flush()  # para o check ganhar o id
            session.add(Alert(
                monitor_id=monitor_id, check_id=check.id, event=event,
                next_attempt_at=checked_at,
            ))
        monitor.is_up = result.is_up
        monitor.last_checked_at = checked_at
        await session.commit()


async def run_once(
    sessionmaker: Sessionmaker,
    client: httpx.AsyncClient,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> int:
    """Uma rodada: checa os monitores vencidos. Devolve quantos checou."""
    now = now or utc_now()
    async with sessionmaker() as session:
        due = await claim_due_monitors(
            session, now, settings.worker_batch_size,
        )

    # Várias checagens ao mesmo tempo (o tempo é quase todo de espera pela
    # rede), mas com limite, para não abrir centenas de conexões de uma vez.
    semaphore = asyncio.Semaphore(settings.worker_concurrency)

    async def check_and_save(monitor_id: int, url: str) -> None:
        async with semaphore:
            try:
                result = await check_url(
                    client, url, timeout=settings.check_timeout,
                )
                await save_result(sessionmaker, monitor_id, url, result, now)
            except Exception:
                # Um erro inesperado num site não derruba os outros.
                logger.exception('Falha ao checar o monitor %s (%s)',
                                 monitor_id, url)

    await asyncio.gather(*(check_and_save(*item) for item in due))
    return len(due)


CLEANUP_EVERY = timedelta(hours=1)


async def delete_old_checks(
    sessionmaker: Sessionmaker, settings: Settings, *, now: datetime,
) -> int:
    """Apaga o histórico mais velho que check_retention_days.

    Sem isso a tabela cresceria para sempre: um site checado de minuto em
    minuto gera 1.440 linhas por dia.
    """
    cutoff = now - timedelta(days=settings.check_retention_days)
    async with sessionmaker() as session:
        result = await session.execute(
            delete(Check).where(Check.checked_at < cutoff),
        )
        await session.commit()
    return result.rowcount


async def run_forever(
    sessionmaker: Sessionmaker,
    client: httpx.AsyncClient,
    settings: Settings,
    stop: asyncio.Event,
    *,
    clock: Callable[[], datetime] = utc_now,
) -> None:
    last_cleanup = None
    while not stop.is_set():
        now = clock()
        try:
            await run_once(sessionmaker, client, settings)
            # Logo depois da rodada: os avisos dela já saem agora.
            await send_due_alerts(sessionmaker, client, settings)
            # A limpeza não precisa ser a cada rodada; uma vez por hora
            # basta (e a primeira logo ao ligar).
            if last_cleanup is None or now - last_cleanup >= CLEANUP_EVERY:
                await delete_old_checks(sessionmaker, settings, now=now)
                last_cleanup = now
        except Exception:
            # Banco fora do ar, por exemplo: tenta de novo na próxima.
            logger.exception('Falha na rodada do worker')
        # Espera o intervalo, mas sai na hora se pedirem para parar.
        try:
            await asyncio.wait_for(
                stop.wait(), timeout=settings.worker_interval_seconds,
            )
        except TimeoutError:
            pass


async def main() -> None:
    settings = Settings()
    engine = make_engine(settings.database_url)
    stop = asyncio.Event()
    try:
        async with make_check_client() as client:
            await run_forever(
                make_sessionmaker(engine), client, settings, stop,
            )
    finally:
        await engine.dispose()


if __name__ == '__main__':  # pragma: no cover
    logging.basicConfig(
        level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
    )
    logger.info('Worker iniciado. Ctrl+C para parar.')
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info('Worker parado.')
