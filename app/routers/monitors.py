from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.database import SessionDep
from app.dependencies import CurrentUser, ResolverDep, SettingsDep
from app.errors import field_error
from app.models import Monitor
from app.schemas import MonitorCreate, MonitorOut, MonitorUpdate
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
    if 'url' in changes:
        await check_url_is_public(changes['url'], resolve)

    for field, value in changes.items():
        setattr(monitor, field, value)
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
