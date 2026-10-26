from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

from app.config import Settings

ALGORITHM = 'HS256'

password_hash = PasswordHash.recommended()  # argon2

# Hash de uma senha que ninguém usa. No login de um e-mail que não existe,
# a senha é conferida contra ele, para a resposta demorar o mesmo tanto
# que a de uma senha errada.
DUMMY_HASH = password_hash.hash('senha-que-nenhuma-conta-usa')


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return password_hash.verify(password, hashed)


def create_access_token(
    user_id: int, settings: Settings, *, now: datetime | None = None,
) -> str:
    now = now or datetime.now(UTC)
    payload = {
        'sub': str(user_id),
        'iat': now,
        'exp': now + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def read_access_token(token: str, settings: Settings) -> int | None:
    """Devolve o id do usuário, ou None se o token não vale."""
    try:
        claims = jwt.decode(
            token, settings.secret_key,
            # Lista fixa: sem ela, um token com alg "none" passaria.
            algorithms=[ALGORITHM],
            options={'require': ['sub', 'exp']},
        )
    except jwt.InvalidTokenError:
        return None

    subject = claims['sub']
    return int(subject) if subject.isdigit() else None
