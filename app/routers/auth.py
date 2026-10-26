from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import security
from app.database import SessionDep
from app.dependencies import CurrentUser, SettingsDep, unauthorized
from app.models import User
from app.schemas import Token, UserCreate, UserOut

router = APIRouter(prefix='/auth', tags=['Contas'])


@router.post(
    '/register',
    status_code=status.HTTP_201_CREATED,
    response_model=UserOut,
    summary='Cria uma conta',
    responses={409: {'description': 'Já existe uma conta com este e-mail.'}},
)
async def register(data: UserCreate, session: SessionDep):
    # O argon2 é lento de propósito (~50 ms). Numa thread à parte, ele não
    # trava as outras requisições enquanto calcula.
    hashed = await run_in_threadpool(security.hash_password, data.password)
    user = User(email=data.email, hashed_password=hashed)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:
        # O unique do banco decide, em vez de procurar o e-mail antes:
        # dois cadastros ao mesmo tempo não passam os dois.
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, 'Já existe uma conta com este e-mail.',
        ) from None
    return user


@router.post(
    '/token',
    response_model=Token,
    summary='Entra e recebe o token',
    description=(
        'Envie como formulário (não JSON). O token vale 60 minutos; use no '
        'cabeçalho `Authorization: Bearer <token>`. No botão **Authorize** '
        'desta página, o campo "username" é o seu e-mail.'
    ),
    responses={401: {'description': 'E-mail ou senha incorretos.'}},
)
async def login(
    username: Annotated[str, Form(description='Seu e-mail.')],
    password: Annotated[str, Form()],
    session: SessionDep,
    settings: SettingsDep,
):
    email = username.strip().lower()
    user = await session.scalar(select(User).where(User.email == email))

    hashed = user.hashed_password if user else security.DUMMY_HASH
    valid = await run_in_threadpool(security.verify_password, password, hashed)
    if user is None or not valid:
        raise unauthorized('E-mail ou senha incorretos.')

    return Token(access_token=security.create_access_token(user.id, settings))


@router.get(
    '/me',
    response_model=UserOut,
    summary='Mostra a sua conta',
    responses={401: {'description': 'Sem token, ou token inválido.'}},
)
async def me(user: CurrentUser):
    return user
