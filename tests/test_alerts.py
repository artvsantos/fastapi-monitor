import asyncio
import hashlib
import hmac
import json
import logging
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app import alerts, worker
from app.models import Alert, Check, Monitor
from app.url_safety import MESSAGES
from tests.helpers import NOW, always, client_for

HOOK = 'https://hooks.exemplo.com.br/avisos'


async def check_round(app, status_code, now=NOW):
    async with always(status_code) as client:
        await worker.run_once(
            app.state.sessionmaker, client, app.state.settings, now=now,
        )


async def send(app, client, now=NOW):
    return await alerts.send_due_alerts(
        app.state.sessionmaker, client, app.state.settings, now=now,
    )


async def alerts_of(app, monitor=None):
    async with app.state.sessionmaker() as session:
        query = select(Alert).order_by(Alert.id)
        if monitor is not None:
            query = query.where(Alert.monitor_id == monitor.id)
        return (await session.scalars(query)).all()


class Recorder:
    """Webhook de mentira: guarda o que recebeu e responde o que mandarem."""

    def __init__(self, *answers):
        self.requests = []
        self.answers = list(answers) or [200]

    def __call__(self, request):
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else (
            self.answers[0]
        )
        if isinstance(answer, Exception):
            raise answer
        if 300 <= answer < 400:
            # Redireciona para a rede interna: seguir seria o SSRF.
            return httpx.Response(answer, headers={
                'Location': 'http://169.254.169.254/latest/meta-data/',
            })
        return httpx.Response(answer)

    @property
    def events(self):
        return [json.loads(r.content)['event'] for r in self.requests]


@pytest.fixture
async def watched(make_monitor):
    """Monitor no ar, com webhook, pronto para ser checado."""
    return await make_monitor(is_up=True, webhook_url=HOOK)


@pytest.fixture
async def went_down(app, watched):
    """O site caiu na checagem do NOW: há um aviso esperando envio."""
    await check_round(app, 503)
    return watched


# --- quando o aviso é criado ------------------------------------------------

async def test_site_going_down_creates_an_alert(app, watched):
    await check_round(app, 503)

    [alert] = await alerts_of(app)
    assert alert.monitor_id == watched.id
    assert alert.event == 'down'
    assert alert.status == 'pending'
    assert alert.attempts == 0
    assert alert.next_attempt_at == NOW


async def test_site_coming_back_creates_an_up_alert(app, make_monitor):
    await make_monitor(is_up=False, webhook_url=HOOK)

    await check_round(app, 200)

    [alert] = await alerts_of(app)
    assert alert.event == 'up'


@pytest.mark.parametrize('status_code, expected', [
    (503, ['down']),   # fora do ar três vezes seguidas = um aviso só
    (200, []),         # no ar o tempo todo = nenhum
])
async def test_same_status_does_not_repeat_the_alert(
    app, watched, status_code, expected,
):
    for minutes in (0, 5, 10):
        await check_round(
            app, status_code, now=NOW + timedelta(minutes=minutes),
        )

    async with app.state.sessionmaker() as session:
        checks = await session.scalar(select(func.count(Check.id)))
    assert checks == 3
    assert [alert.event for alert in await alerts_of(app)] == expected


@pytest.mark.parametrize('status_code, expected', [
    (503, ['down']),   # já começou fora do ar: vale avisar
    (200, []),         # começou no ar: nada mudou para quem cadastrou
])
async def test_first_check(app, make_monitor, status_code, expected):
    await make_monitor(is_up=None, webhook_url=HOOK)

    await check_round(app, status_code)

    assert [alert.event for alert in await alerts_of(app)] == expected


async def test_no_webhook_no_alert(app, make_monitor):
    await make_monitor(is_up=True)

    await check_round(app, 503)

    assert await alerts_of(app) == []


async def test_alert_points_to_the_check_that_changed_the_status(
    app, watched,
):
    await check_round(app, 503)

    [alert] = await alerts_of(app)
    async with app.state.sessionmaker() as session:
        check = await session.get(Check, alert.check_id)
    assert (check.monitor_id, check.status_code) == (watched.id, 503)


# --- envio ------------------------------------------------------------------

async def test_delivers_the_alert_as_json(app, went_down):
    webhook = Recorder(200)

    async with client_for(webhook) as client:
        sent = await send(app, client)

    [request] = webhook.requests
    [alert] = await alerts_of(app)
    body = json.loads(request.content)
    assert sent == 1
    assert request.method == 'POST'
    assert str(request.url) == HOOK
    assert request.headers['Content-Type'] == 'application/json'
    assert request.headers['X-Monitor-Event'] == 'down'
    assert body == {
        'event': 'down',
        'alert_id': alert.id,
        'message': (
            'Site (https://exemplo.com.br/) caiu: '
            'O site respondeu com o código 503.'
        ),
        'monitor': {
            'id': went_down.id, 'name': 'Site',
            'url': 'https://exemplo.com.br/',
        },
        'check': {
            'checked_at': NOW.isoformat(),
            'status_code': 503,
            'response_time_ms': body['check']['response_time_ms'],
            'error': 'O site respondeu com o código 503.',
        },
    }
    assert alert.status == 'sent'
    assert alert.sent_at == NOW
    assert alert.attempts == 1


async def test_up_message(app, make_monitor):
    await make_monitor(is_up=False, webhook_url=HOOK)
    await check_round(app, 200)
    webhook = Recorder(200)

    async with client_for(webhook) as client:
        await send(app, client)

    body = json.loads(webhook.requests[0].content)
    assert body['message'] == 'Site (https://exemplo.com.br/) voltou ao ar.'
    assert body['check']['error'] is None


async def test_alert_is_signed_with_the_monitor_secret(app, went_down):
    # Quem recebe confere a assinatura com o segredo do monitor e sabe que
    # o aviso veio daqui, e não de alguém que descobriu o endereço.
    webhook = Recorder(200)

    async with client_for(webhook) as client:
        await send(app, client)

    [request] = webhook.requests
    timestamp = request.headers['X-Monitor-Timestamp']
    expected = hmac.new(
        went_down.webhook_secret.encode(),
        f'{timestamp}.'.encode() + request.content,
        hashlib.sha256,
    ).hexdigest()
    assert timestamp == str(int(NOW.timestamp()))
    assert request.headers['X-Monitor-Signature'] == f'sha256={expected}'


async def test_sent_alert_is_not_sent_again(app, went_down):
    webhook = Recorder(200)

    async with client_for(webhook) as client:
        await send(app, client)
        sent_again = await send(app, client, now=NOW + timedelta(hours=1))

    assert sent_again == 0
    assert len(webhook.requests) == 1


async def test_uses_the_configured_timeout(app, went_down):
    app.state.settings.check_timeout = 3
    webhook = Recorder(200)

    async with client_for(webhook) as client:
        await send(app, client)

    assert webhook.requests[0].extensions['timeout']['read'] == 3


async def test_uses_the_current_webhook_address(app, went_down, session):
    saved = await session.get(Monitor, went_down.id)
    saved.webhook_url = 'https://novo-hook.com/'
    await session.commit()
    webhook = Recorder(200)

    async with client_for(webhook) as client:
        await send(app, client)

    assert str(webhook.requests[0].url) == 'https://novo-hook.com/'


async def test_removed_webhook_cancels_the_alert(app, went_down, session):
    saved = await session.get(Monitor, went_down.id)
    saved.webhook_url = None
    await session.commit()
    webhook = Recorder(200)

    async with client_for(webhook) as client:
        await send(app, client)

    [alert] = await alerts_of(app)
    assert webhook.requests == []
    assert alert.status == 'cancelled'


# --- falhas e novas tentativas ----------------------------------------------

@pytest.mark.parametrize('answer, message', [
    (500, 'O webhook respondeu com o código 500.'),
    # Não segue redirecionamento: o destino nunca passou pelo filtro.
    (302, 'O webhook respondeu com o código 302.'),
    (httpx.ReadTimeout('lento'), 'O webhook não respondeu em 10 segundos.'),
    (httpx.ConnectError('recusada'), 'Não foi possível conectar ao webhook.'),
    (httpx.ConnectError(MESSAGES['url_internal']), MESSAGES['url_internal']),
    (httpx.RemoteProtocolError('quebrou'), 'Erro ao enviar o webhook.'),
])
async def test_failed_delivery_is_retried_in_a_minute(
    app, went_down, answer, message,
):
    webhook = Recorder(answer)

    async with client_for(webhook) as client:
        await send(app, client)

    [alert] = await alerts_of(app)
    assert len(webhook.requests) == 1
    assert alert.status == 'pending'
    assert alert.attempts == 1
    assert alert.last_error == message
    assert alert.next_attempt_at == NOW + timedelta(minutes=1)


async def test_retry_waits_for_its_time(app, went_down):
    webhook = Recorder(500, 200)

    async with client_for(webhook) as client:
        await send(app, client)
        early = await send(app, client, now=NOW + timedelta(seconds=59))
        on_time = await send(app, client, now=NOW + timedelta(minutes=1))

    [alert] = await alerts_of(app)
    assert (early, on_time) == (0, 1)
    assert alert.status == 'sent'
    assert alert.attempts == 2


async def test_wait_doubles_and_then_gives_up(app, went_down):
    # 5 tentativas: na hora e depois de 1, 2, 4 e 8 minutos (15 no total).
    app.state.settings.webhook_max_attempts = 5
    now = NOW
    waits = []

    async with client_for(Recorder(500)) as client:
        for _ in range(5):
            await send(app, client, now=now)
            [alert] = await alerts_of(app)
            if alert.status == 'pending':
                waits.append(alert.next_attempt_at - now)
                now = alert.next_attempt_at

    assert waits == [timedelta(minutes=m) for m in (1, 2, 4, 8)]
    assert alert.status == 'failed'
    assert alert.attempts == 5
    assert alert.last_error == 'O webhook respondeu com o código 500.'


async def test_alert_being_sent_is_not_taken_again(app, went_down):
    # Se o worker cair no meio do envio, o aviso volta para a fila depois
    # de um tempo; antes disso, outro worker não pega o mesmo aviso.
    async with app.state.sessionmaker() as session:
        claimed = await alerts.claim_due_alerts(session, NOW, 10)
    webhook = Recorder(200)

    async with client_for(webhook) as client:
        meanwhile = await send(app, client, now=NOW + timedelta(minutes=1))
        later = await send(app, client, now=NOW + alerts.DELIVERY_LEASE)

    assert len(claimed) == 1
    assert (meanwhile, later) == (0, 1)


# --- ordem e independência --------------------------------------------------

async def test_alerts_of_a_monitor_go_out_in_order(app, went_down):
    # Caiu e voltou. O "voltou" espera o "caiu" sair: chegar antes faria
    # quem recebe achar que o site está fora do ar.
    await check_round(app, 200, now=NOW + timedelta(minutes=5))
    webhook = Recorder(500, 200, 200)
    later = NOW + timedelta(minutes=5)

    async with client_for(webhook) as client:
        await send(app, client, now=later)
        await send(app, client, now=later + timedelta(minutes=1))
        await send(app, client, now=later + timedelta(minutes=2))

    assert webhook.events == ['down', 'down', 'up']
    assert [a.status for a in await alerts_of(app)] == ['sent', 'sent']


async def test_failing_webhook_does_not_hold_other_monitors(
    app, went_down, make_monitor,
):
    other = await make_monitor(
        'https://outro-site.com/', is_up=True,
        webhook_url='https://outro-hook.com/',
    )
    await check_round(app, 503)

    def handler(request):
        if request.url.host == 'hooks.exemplo.com.br':
            return httpx.Response(500)
        return httpx.Response(200)

    async with client_for(handler) as client:
        await send(app, client)

    [mine] = await alerts_of(app, went_down)
    [theirs] = await alerts_of(app, other)
    assert (mine.status, theirs.status) == ('pending', 'sent')


async def test_sends_in_parallel_up_to_the_limit(app, make_monitor):
    app.state.settings.worker_concurrency = 2
    for letter in 'abcde':
        await make_monitor(
            f'https://{letter}.com/', is_up=True, webhook_url=HOOK,
        )
    await check_round(app, 503)
    running = 0
    peak = 0

    async def slow(request):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return httpx.Response(200)

    async with client_for(slow) as client:
        sent = await send(app, client)

    assert sent == 5
    assert peak == 2


async def test_bug_in_one_delivery_does_not_stop_the_others(
    app, make_monitor, caplog,
):
    await make_monitor('https://a.com/', is_up=True,
                       webhook_url='https://quebrado.com/')
    await make_monitor('https://b.com/', is_up=True, webhook_url=HOOK)
    await check_round(app, 503)

    def handler(request):
        if request.url.host == 'quebrado.com':
            raise RuntimeError('bug inesperado')
        return httpx.Response(200)

    with caplog.at_level(logging.ERROR, logger='app.alerts'):
        async with client_for(handler) as client:
            await send(app, client)

    statuses = sorted(a.status for a in await alerts_of(app))
    assert statuses == ['pending', 'sent']
    assert 'bug inesperado' in caplog.text


async def test_deleting_the_monitor_deletes_its_alerts(
    app, went_down, session,
):
    await session.delete(await session.get(Monitor, went_down.id))
    await session.commit()

    assert await alerts_of(app) == []


# --- no laço do worker ------------------------------------------------------

async def test_loop_sends_alerts_after_each_round(app, monkeypatch):
    stop = asyncio.Event()
    calls = []

    async def fake_run_once(sessionmaker, client, settings):
        calls.append('checar')
        return 0

    async def fake_send(sessionmaker, client, settings):
        calls.append('avisar')
        if calls.count('avisar') == 2:
            stop.set()
        return 0

    monkeypatch.setattr(worker, 'run_once', fake_run_once)
    monkeypatch.setattr(worker, 'send_due_alerts', fake_send)
    app.state.settings.worker_interval_seconds = 0

    await asyncio.wait_for(
        worker.run_forever(
            app.state.sessionmaker, None, app.state.settings, stop,
        ),
        timeout=2,
    )

    assert calls == ['checar', 'avisar', 'checar', 'avisar']


async def test_monitor_deleted_during_delivery_is_ignored(app, went_down):
    async def delete_then_answer(request):
        async with app.state.sessionmaker() as session:
            await session.delete(await session.get(Monitor, went_down.id))
            await session.commit()
        return httpx.Response(200)

    async with client_for(delete_then_answer) as client:
        sent = await send(app, client)

    assert sent == 1
    assert await alerts_of(app) == []
