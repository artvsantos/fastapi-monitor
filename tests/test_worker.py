import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app import worker
from app.models import Check, Monitor, User

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


@pytest.fixture
async def owner(session):
    user = User(email='ana@exemplo.com', hashed_password='x')
    session.add(user)
    await session.commit()
    return user


@pytest.fixture
def make_monitor(session, owner):
    async def make(url='https://exemplo.com.br/', **kwargs):
        data = {
            'name': 'Site', 'interval_minutes': 5,
            'next_check_at': NOW - timedelta(minutes=1),
        }
        data.update(kwargs)
        monitor = Monitor(owner_id=owner.id, url=url, **data)
        session.add(monitor)
        await session.commit()
        return monitor

    return make


def client_for(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def always(status_code):
    return client_for(lambda request: httpx.Response(status_code))


async def run(app, client, now=NOW):
    return await worker.run_once(
        app.state.sessionmaker, client, app.state.settings, now=now,
    )


async def checks_of(app, monitor):
    async with app.state.sessionmaker() as session:
        result = await session.scalars(
            select(Check).where(Check.monitor_id == monitor.id),
        )
        return result.all()


async def reload(app, monitor):
    async with app.state.sessionmaker() as session:
        return await session.get(Monitor, monitor.id)


# --- o que entra na rodada --------------------------------------------------

async def test_due_monitor_is_checked_and_saved(app, make_monitor):
    monitor = await make_monitor()

    async with always(200) as client:
        checked = await run(app, client)

    [check] = await checks_of(app, monitor)
    saved = await reload(app, monitor)
    assert checked == 1
    assert (check.is_up, check.status_code, check.error) == (True, 200, None)
    assert check.response_time_ms >= 0
    assert check.checked_at == NOW
    assert saved.is_up is True
    assert saved.last_checked_at == NOW
    assert saved.next_check_at == NOW + timedelta(minutes=5)


async def test_down_result_is_saved_with_the_reason(app, make_monitor):
    monitor = await make_monitor()

    async with always(503) as client:
        await run(app, client)

    [check] = await checks_of(app, monitor)
    assert not check.is_up
    assert check.status_code == 503
    assert check.error == 'O site respondeu com o código 503.'
    assert (await reload(app, monitor)).is_up is False


async def test_monitor_not_due_yet_is_skipped(app, make_monitor):
    monitor = await make_monitor(next_check_at=NOW + timedelta(seconds=1))

    async with always(200) as client:
        checked = await run(app, client)

    assert checked == 0
    assert await checks_of(app, monitor) == []


async def test_paused_monitor_is_skipped(app, make_monitor):
    monitor = await make_monitor(is_active=False)

    async with always(200) as client:
        await run(app, client)

    assert await checks_of(app, monitor) == []


async def test_monitor_is_not_checked_twice_in_the_same_minute(
    app, make_monitor,
):
    await make_monitor()

    async with always(200) as client:
        first = await run(app, client)
        second = await run(app, client, now=NOW + timedelta(seconds=30))

    assert (first, second) == (1, 0)


async def test_batch_takes_the_most_overdue_first(app, make_monitor):
    app.state.settings.worker_batch_size = 2
    late = await make_monitor(
        'https://a.com/', next_check_at=NOW - timedelta(hours=1),
    )
    later = await make_monitor(
        'https://b.com/', next_check_at=NOW - timedelta(hours=2),
    )
    recent = await make_monitor('https://c.com/', next_check_at=NOW)

    async with always(200) as client:
        checked = await run(app, client)

    assert checked == 2
    assert len(await checks_of(app, late)) == 1
    assert len(await checks_of(app, later)) == 1
    assert await checks_of(app, recent) == []


# --- várias checagens ao mesmo tempo ----------------------------------------

async def test_checks_run_in_parallel_up_to_the_limit(app, make_monitor):
    app.state.settings.worker_concurrency = 2
    for letter in 'abcde':
        await make_monitor(f'https://{letter}.com/')
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
        checked = await run(app, client)

    assert checked == 5
    assert peak == 2


async def test_uses_the_configured_timeout(app, make_monitor):
    app.state.settings.check_timeout = 3
    await make_monitor()
    timeouts = []

    def handler(request):
        timeouts.append(request.extensions['timeout']['read'])
        return httpx.Response(200)

    async with client_for(handler) as client:
        await run(app, client)

    assert timeouts == [3]


async def test_bug_in_one_check_does_not_stop_the_others(
    app, make_monitor, caplog,
):
    broken = await make_monitor('https://quebrado.com/')
    fine = await make_monitor('https://ok.com/')

    def handler(request):
        if request.url.host == 'quebrado.com':
            raise RuntimeError('bug inesperado')
        return httpx.Response(200)

    with caplog.at_level(logging.ERROR, logger='app.worker'):
        async with client_for(handler) as client:
            await run(app, client)

    assert await checks_of(app, broken) == []
    assert len(await checks_of(app, fine)) == 1
    assert 'quebrado.com' in caplog.text


# --- mudanças durante a checagem --------------------------------------------

async def test_monitor_deleted_during_the_check_is_ignored(app, make_monitor):
    monitor = await make_monitor()

    async def delete_then_answer(request):
        async with app.state.sessionmaker() as session:
            await session.delete(await session.get(Monitor, monitor.id))
            await session.commit()
        return httpx.Response(200)

    async with client_for(delete_then_answer) as client:
        await run(app, client)

    async with app.state.sessionmaker() as session:
        assert await session.scalar(select(func.count(Check.id))) == 0


async def test_result_of_an_old_url_is_discarded(app, make_monitor):
    monitor = await make_monitor('https://antigo.com/')

    async def change_url_then_answer(request):
        async with app.state.sessionmaker() as session:
            saved = await session.get(Monitor, monitor.id)
            saved.url = 'https://novo.com/'
            await session.commit()
        return httpx.Response(500)

    async with client_for(change_url_then_answer) as client:
        await run(app, client)

    assert await checks_of(app, monitor) == []
    assert (await reload(app, monitor)).is_up is None


# --- o laço -----------------------------------------------------------------

async def test_loop_runs_until_asked_to_stop(app, monkeypatch):
    stop = asyncio.Event()
    rounds = []

    async def fake_run_once(sessionmaker, client, settings):
        rounds.append(1)
        if len(rounds) == 3:
            stop.set()
        return 0

    monkeypatch.setattr(worker, 'run_once', fake_run_once)
    app.state.settings.worker_interval_seconds = 0

    await asyncio.wait_for(
        worker.run_forever(
            app.state.sessionmaker, None, app.state.settings, stop,
        ),
        timeout=2,
    )

    assert len(rounds) == 3


async def test_loop_survives_a_failed_round(app, monkeypatch, caplog):
    stop = asyncio.Event()
    rounds = []

    async def flaky_run_once(sessionmaker, client, settings):
        rounds.append(1)
        if len(rounds) == 1:
            raise ConnectionError('banco fora do ar')
        stop.set()
        return 0

    monkeypatch.setattr(worker, 'run_once', flaky_run_once)
    app.state.settings.worker_interval_seconds = 0

    with caplog.at_level(logging.ERROR, logger='app.worker'):
        await asyncio.wait_for(
            worker.run_forever(
                app.state.sessionmaker, None, app.state.settings, stop,
            ),
            timeout=2,
        )

    assert len(rounds) == 2
    assert 'banco fora do ar' in caplog.text


async def test_loop_stops_without_waiting_for_the_whole_interval(
    app, monkeypatch,
):
    stop = asyncio.Event()

    async def fake_run_once(sessionmaker, client, settings):
        stop.set()
        return 0

    monkeypatch.setattr(worker, 'run_once', fake_run_once)
    app.state.settings.worker_interval_seconds = 3600

    await asyncio.wait_for(
        worker.run_forever(
            app.state.sessionmaker, None, app.state.settings, stop,
        ),
        timeout=2,
    )


async def test_main_wires_settings_database_and_safe_client(
    settings, monkeypatch,
):
    received = {}

    async def fake_run_forever(sessionmaker, client, settings_, stop):
        received.update(
            sessionmaker=sessionmaker, client=client,
            settings=settings_, stop=stop,
        )

    monkeypatch.setattr(worker, 'run_forever', fake_run_forever)
    monkeypatch.setattr(worker, 'Settings', lambda: settings)

    await worker.main()

    assert received['settings'] is settings
    assert received['client'].is_closed
    assert not received['client'].trust_env
    assert isinstance(received['stop'], asyncio.Event)
