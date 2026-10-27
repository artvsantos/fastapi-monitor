from typing import Any, get_args

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic_core.core_schema import ErrorType

# As mensagens do Pydantic vêm em inglês. Estas são as que a API usa.
MESSAGES = {
    'missing': 'Este campo é obrigatório.',
    'string_type': 'Informe um texto.',
    'string_too_short': 'Use pelo menos {min_length} caracteres.',
    'string_too_long': 'Use no máximo {max_length} caracteres.',
    'int_type': 'Informe um número inteiro.',
    'int_parsing': 'Informe um número inteiro.',
    'int_from_float': 'Informe um número inteiro.',
    'greater_than_equal': 'Use um valor de pelo menos {ge}.',
    'less_than_equal': 'Use um valor de no máximo {le}.',
    'bool_type': 'Use true ou false.',
    'bool_parsing': 'Use true ou false.',
    'json_invalid': 'O corpo da requisição não é um JSON válido.',
    'model_attributes_type': 'Envie um objeto JSON.',
    'dict_type': 'Envie um objeto JSON.',
    'datetime_type': 'Informe uma data e hora válida.',
    'datetime_parsing': 'Informe uma data e hora válida.',
    'datetime_from_date_parsing': 'Informe uma data e hora válida.',
    'timezone_aware': (
        'Informe a data com fuso horário, como 2026-10-27T12:00:00Z.'
    ),
}
GENERIC_MESSAGE = 'Valor inválido.'

PYDANTIC_ERROR_TYPES = frozenset(get_args(ErrorType))


def translate_error(error: dict[str, Any]) -> dict[str, Any]:
    kind = error['type']
    if kind == 'string_too_short' and error['ctx']['min_length'] == 1:
        message = 'Este campo não pode ficar vazio.'
    elif kind == 'literal_error':
        # O Pydantic lista as opções como "'24h', '7d' or '30d'".
        expected = error['ctx']['expected']
        options = expected.replace("'", '').replace(' or ', ' ou ')
        message = f'Escolha uma das opções: {options}.'
    elif kind in MESSAGES:
        message = MESSAGES[kind].format(**error.get('ctx', {}))
    elif kind in PYDANTIC_ERROR_TYPES:
        message = GENERIC_MESSAGE
    else:
        # Erro criado por nós (PydanticCustomError): já está em português.
        message = error['msg']
    # Sem "input": o padrão do FastAPI devolve o valor enviado, o que
    # mostraria a senha de volta na resposta de erro.
    return {'type': kind, 'loc': error['loc'], 'msg': message}


def field_error(field: str, kind: str, message: str) -> HTTPException:
    """Erro 422 no mesmo formato da validação, para checagens feitas na
    rota (como a do DNS), que o Pydantic não tem como fazer."""
    return HTTPException(
        status_code=422,
        detail=[{'type': kind, 'loc': ['body', field], 'msg': message}],
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError,
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={'detail': [translate_error(e) for e in exc.errors()]},
    )
