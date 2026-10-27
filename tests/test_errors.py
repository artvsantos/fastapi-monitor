from app.errors import PYDANTIC_ERROR_TYPES, translate_error


def error(type_, msg='mensagem original em inglês', **ctx):
    data = {'type': type_, 'loc': ('body', 'campo'), 'msg': msg,
            'input': 'valor enviado'}
    if ctx:
        data['ctx'] = ctx
    return data


def test_known_error_is_translated_with_its_numbers():
    translated = translate_error(error('string_too_short', min_length=3))

    assert translated == {
        'type': 'string_too_short',
        'loc': ('body', 'campo'),
        'msg': 'Use pelo menos 3 caracteres.',
    }


def test_other_pydantic_errors_get_a_generic_portuguese_message():
    translated = translate_error(error('uuid_parsing'))

    assert translated['msg'] == 'Valor inválido.'


def test_our_own_errors_keep_their_message():
    translated = translate_error(
        error('invalid_email', msg='Informe um e-mail válido.'),
    )

    assert translated['msg'] == 'Informe um e-mail válido.'


def test_sent_value_is_not_echoed_back():
    assert 'input' not in translate_error(error('missing'))


def test_our_error_names_do_not_clash_with_pydantic_ones():
    # Se o nome coincidir, a mensagem em português vira "Valor inválido.".
    ours = {
        'invalid_email', 'url_not_http', 'url_invalid', 'url_credentials',
        'url_internal', 'webhook_internal',
    }

    assert not ours & PYDANTIC_ERROR_TYPES
