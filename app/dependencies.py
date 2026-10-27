from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from app import security
from app.config import Settings
from app.database import SessionDep
from app.models import User
from app.url_safety import Resolver


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_resolver(request: Request) -> Resolver:
    return request.app.state.resolver


ResolverDep = Annotated[Resolver, Depends(get_resolver)]

# auto_error=False: sem token, o FastAPI responderia "Not authenticated",
# em inglês. Assim a mensagem é nossa.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl='/auth/token', auto_error=False)


def unauthorized(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=message,
        headers={'WWW-Authenticate': 'Bearer'},
    )


async def get_current_user(
    session: SessionDep,
    settings: SettingsDep,
    token: Annotated[str | None, Depends(oauth2_scheme)],
) -> User:
    if not token:
        raise unauthorized('Faça login para continuar.')

    user_id = security.read_access_token(token, settings)
    user = await session.get(User, user_id) if user_id else None
    if user is None:
        raise unauthorized('Token inválido ou expirado.')
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
