from typing import Any, get_args

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic_core.core_schema import ErrorType

# As mensagens do Pydantic vêm em inglês. Estas são as que a API usa.
MESSAGES = {
    'missing': 'Este campo é obrigatório.',
    'string_type': 'Informe um texto.',
    'string_too_short': 'Use pelo menos {min_length} caracteres.',
    'string_too_long': 'Use no máximo {max_length} caracteres.',
    'json_invalid': 'O corpo da requisição não é um JSON válido.',
    'model_attributes_type': 'Envie um objeto JSON.',
    'dict_type': 'Envie um objeto JSON.',
}
GENERIC_MESSAGE = 'Valor inválido.'

PYDANTIC_ERROR_TYPES = frozenset(get_args(ErrorType))


def translate_error(error: dict[str, Any]) -> dict[str, Any]:
    kind = error['type']
    if kind in MESSAGES:
        message = MESSAGES[kind].format(**error.get('ctx', {}))
    elif kind in PYDANTIC_ERROR_TYPES:
        message = GENERIC_MESSAGE
    else:
        # Erro criado por nós (PydanticCustomError): já está em português.
        message = error['msg']
    # Sem "input": o padrão do FastAPI devolve o valor enviado, o que
    # mostraria a senha de volta na resposta de erro.
    return {'type': kind, 'loc': error['loc'], 'msg': message}


async def validation_error_handler(
    request: Request, exc: RequestValidationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={'detail': [translate_error(e) for e in exc.errors()]},
    )
