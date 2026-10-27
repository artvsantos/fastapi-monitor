import math
from datetime import timedelta
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import AwareDatetime
from sqlalchemy import case, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.database import SessionDep, utc_now
from app.dependencies import CurrentUser, ResolverDep, SettingsDep
from app.errors import field_error
from app.models import Check, Monitor
from app.schemas import (
    CheckPage,
    MonitorCreate,
    MonitorOut,
    MonitorStats,
    MonitorUpdate,
    Period,
)
from app.url_safety import UnsafeURLError, ensure_public_host

router = APIRouter(prefix='/monitors', tags=['Monitores'])

NOT_FOUND = {404: {'description': 'Monitor não encontrado.'}}
DUPLICATE = 'Você já monitora esta URL.'


async def check_url_is_public(url: str, resolve) -> None:
    try:
        await ensure_public_host(urlsplit(url).hostname, resolve)
    except UnsafeURLError as error:
        raise field_error('url', error.kind, error.message) from None


async def get_own_monitor(
    session: SessionDep, user: CurrentUser, monitor_id: int,
) -> Monitor:
    # Filtrar pelo dono faz o monitor de outra pessoa dar 404, sem nem
    # revelar que aquele id existe.
    monitor = await session.scalar(
        select(Monitor).where(
            Monitor.id == monitor_id, Monitor.owner_id == user.id,
        ),
    )
    if monitor is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, 'Monitor não encontrado.',
        )
    return monitor


async def save(session: SessionDep, monitor: Monitor) -> Monitor:
    session.add(monitor)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, DUPLICATE) from None
    return monitor


@router.post(
    '',
    status_code=status.HTTP_201_CREATED,
    response_model=MonitorOut,
    summary='Cadastra um site',
    description=(
        'Endereços da rede interna (localhost, 10.x, 192.168.x, '
        '169.254.x...) são recusados, inclusive domínios que apontam '
        'para eles.'
    ),
    responses={409: {'description': f'{DUPLICATE} Ou: limite atingido.'}},
)
async def create_monitor(
    data: MonitorCreate,
    session: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
    resolve: ResolverDep,
):
    await check_url_is_public(data.url, resolve)

    # Dois cadastros ao mesmo tempo podem passar um do limite; para um
    # limite de uso justo, tudo bem.
    count = await session.scalar(
        select(func.count()).where(Monitor.owner_id == user.id),
    )
    limit = settings.max_monitors_per_user
    if count >= limit:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f'Você chegou ao limite de {limit} monitores por conta.',
        )

    return await save(session, Monitor(owner_id=user.id, **data.model_dump()))


@router.get('', response_model=list[MonitorOut], summary='Lista seus sites')
async def list_monitors(session: SessionDep, user: CurrentUser):
    monitors = await session.scalars(
        select(Monitor)
        .where(Monitor.owner_id == user.id)
        .order_by(Monitor.id),
    )
    return monitors.all()


@router.get(
    '/{monitor_id}',
    response_model=MonitorOut,
    summary='Mostra um site',
    responses=NOT_FOUND,
)
async def get_monitor(monitor_id: int, session: SessionDep, user: CurrentUser):
    return await get_own_monitor(session, user, monitor_id)


@router.patch(
    '/{monitor_id}',
    response_model=MonitorOut,
    summary='Altera um site',
    description='Envie só os campos que quer mudar.',
    responses={**NOT_FOUND, 409: {'description': DUPLICATE}},
)
async def update_monitor(
    monitor_id: int,
    data: MonitorUpdate,
    session: SessionDep,
    user: CurrentUser,
    resolve: ResolverDep,
):
    monitor = await get_own_monitor(session, user, monitor_id)
    changes = data.model_dump(exclude_unset=True)
    url_changed = changes.get('url', monitor.url) != monitor.url
    reactivated = changes.get('is_active') and not monitor.is_active
    if url_changed:
        await check_url_is_public(changes['url'], resolve)

    for field, value in changes.items():
        setattr(monitor, field, value)
    if url_changed:
        # O status era do endereço antigo.
        monitor.is_up = None
        monitor.last_checked_at = None
    if url_changed or reactivated:
        monitor.next_check_at = utc_now()
    return await save(session, monitor)


@router.delete(
    '/{monitor_id}',
    status_code=status.HTTP_204_NO_CONTENT,
    summary='Apaga um site',
    responses=NOT_FOUND,
)
async def delete_monitor(
    monitor_id: int, session: SessionDep, user: CurrentUser,
):
    monitor = await get_own_monitor(session, user, monitor_id)
    await session.delete(monitor)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    '/{monitor_id}/checks',
    response_model=CheckPage,
    summary='Histórico de checagens',
    description=(
        'Das mais novas para as mais antigas. Para a próxima página, envie '
        'em `before` o `next_before` da resposta.'
    ),
    responses=NOT_FOUND,
)
async def list_checks(
    monitor_id: int,
    session: SessionDep,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: Annotated[
        AwareDatetime | None,
        Query(description='Só checagens anteriores a esta data e hora.'),
    ] = None,
):
    await get_own_monitor(session, user, monitor_id)

    query = select(Check).where(Check.monitor_id == monitor_id)
    if before is not None:
        query = query.where(Check.checked_at < before)
    # Um a mais que o pedido: se vier, existe próxima página. O índice
    # (monitor_id, checked_at) deixa isso rápido em qualquer página, ao
    # contrário de OFFSET, que lê e descarta tudo o que vem antes.
    rows = (await session.scalars(
        query.order_by(Check.checked_at.desc()).limit(limit + 1),
    )).all()

    items = rows[:limit]
    has_more = len(rows) > limit
    return CheckPage(
        items=items,
        next_before=items[-1].checked_at if has_more else None,
    )


PERIODS = {
    '24h': timedelta(hours=24),
    '7d': timedelta(days=7),
    '30d': timedelta(days=30),
}


@router.get(
    '/{monitor_id}/stats',
    response_model=MonitorStats,
    summary='Tempo no ar e tempo de resposta',
    responses=NOT_FOUND,
)
async def monitor_stats(
    monitor_id: int,
    session: SessionDep,
    user: CurrentUser,
    period: Period = '24h',
):
    await get_own_monitor(session, user, monitor_id)
    since = utc_now() - PERIODS[period]
    in_period = (Check.monitor_id == monitor_id, Check.checked_at >= since)

    # Tudo calculado pelo banco, numa consulta só, em vez de trazer
    # milhares de checagens para contar em Python.
    total, up, avg_ms = (await session.execute(
        select(
            func.count(),
            func.coalesce(func.sum(case((Check.is_up, 1), else_=0)), 0),
            # Sem "else", a checagem fora do ar vira NULL, e o AVG ignora.
            func.avg(case((Check.is_up, Check.response_time_ms))),
        ).where(*in_period),
    )).one()

    # Queda = checagem fora do ar cuja anterior estava no ar (ou que abre
    # o período). LAG pega o valor da linha anterior, na ordem do tempo.
    previous = func.lag(Check.is_up).over(order_by=Check.checked_at)
    timeline = select(
        Check.is_up.label('is_up'), previous.label('previous'),
    ).where(*in_period).subquery()
    incidents = await session.scalar(
        select(func.count()).select_from(timeline).where(
            ~timeline.c.is_up,
            or_(timeline.c.previous.is_(None), timeline.c.previous),
        ),
    )

    return MonitorStats(
        period=period,
        since=since,
        checks=total,
        up_checks=up,
        uptime_percent=round(up * 100 / total, 2) if total else None,
        # floor(x + 0,5): arredondamento "de escola". O round() do Python
        # arredonda 100,5 para 100 (para o par mais próximo).
        avg_response_ms=(
            math.floor(float(avg_ms) + 0.5) if avg_ms is not None else None
        ),
        incidents=incidents,
    )
