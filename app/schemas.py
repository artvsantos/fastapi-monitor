from datetime import datetime
from typing import Annotated

from email_validator import EmailNotValidError, validate_email
from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from pydantic_core import PydanticCustomError


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
