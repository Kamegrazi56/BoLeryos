"""Pause du bot (GRAZI-68) : OFF pour X minutes puis ON tout seul, depuis l'ordonnanceur et l'API de la barre du haut."""

import asyncio

import pytest

from app.pause import BotPause, PauseError

from .test_features import make, manual


async def _wait(condition, timeout=2.0):
    for _ in range(int(timeout / 0.01)):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("délai dépassé")


def test_a_pause_turns_the_bot_off_then_back_on_and_shows_the_resume_time():
    async def scenario():
        sched, bot, _, _ = make()
        gate = asyncio.Event()

        async def fake_sleep(seconds):
            assert seconds == 90 * 60
            await gate.wait()

        sched.pause = BotPause(sched, fake_sleep)
        manual(sched)
        back = sched.pause.start(90)
        act = sched.activity()
        assert not sched.on and act["state"] == "paused" and act["until"] == f"{back:%H:%M}" and 5390 < act["left_s"] <= 5400
        gate.set()
        await _wait(lambda: sched.on)
        assert not sched.pause.active and sched.activity()["state"] != "paused"
        assert any("Fin de la pause" in m for _, m in bot.messages)

    asyncio.run(scenario())


def test_bad_durations_and_a_bot_already_off_are_refused():
    async def scenario():
        sched, _, _, _ = make()
        with pytest.raises(PauseError, match="déjà sur OFF"):
            sched.pause.start(10)
        manual(sched)
        for bad in (0, -5, 721):
            with pytest.raises(PauseError, match="refusée"):
                sched.pause.start(bad)
        assert sched.on and not sched.pause.active

    asyncio.run(scenario())


def test_an_on_or_off_order_or_a_shutdown_cancels_the_pause():
    async def scenario():
        sched, _, _, _ = make()
        sched.pause = BotPause(sched, lambda s: asyncio.sleep(3600))
        for order in (sched.start, sched.stop, sched.shutdown):
            manual(sched)
            sched.pause.start(30)
            assert sched.pause.active
            result = order()
            if asyncio.iscoroutine(result):
                await result
            assert not sched.pause.active and sched.pause.info() is None
        assert not sched.on  # l'arrêt de l'instance ne relance rien

    asyncio.run(scenario())


def test_a_second_pause_replaces_the_first():
    async def scenario():
        sched, _, _, _ = make()
        sched.pause = BotPause(sched, lambda s: asyncio.sleep(3600))
        manual(sched)
        sched.pause.start(30)
        first = sched.pause._task
        sched.start()
        sched.pause.start(60)
        await asyncio.sleep(0)
        assert first.cancelled() or first.done()
        assert sched.pause.active and 3590 < sched.pause.info()["left_s"] <= 3600
        sched.pause.cancel()

    asyncio.run(scenario())


def test_pause_api_pauses_the_bot_and_the_next_order_cancels_it(game_client):
    assert game_client.post("/api/bot/pause", json={"minutes": 5}).status_code == 409  # bot sur OFF : rien à mettre en pause
    assert game_client.post("/api/bot/start").json()["bot_on"] is True
    assert game_client.post("/api/bot/pause", json={"minutes": 0}).status_code == 422
    assert game_client.post("/api/bot/pause", json={"minutes": 721}).status_code == 422
    assert game_client.post("/api/bot/pause", json={"minutes": "abc"}).status_code == 422
    data = game_client.post("/api/bot/pause", json={"minutes": 5}).json()
    assert data["bot_on"] is False and data["activity"]["state"] == "paused"
    assert game_client.get("/api/status").json()["scheduler"]["activity"]["state"] == "paused"
    assert game_client.post("/api/bot/start").json()["activity"]["state"] != "paused"  # Bot ON annule la pause
    assert game_client.post("/api/bot/pause", json={"minutes": 5}).json()["activity"]["state"] == "paused"
    assert game_client.post("/api/bot/stop").json()["activity"] == {"state": "off"}  # Bot OFF aussi


def test_pause_api_refuses_a_foreign_origin(game_client):
    game_client.post("/api/bot/start")
    assert game_client.post("/api/bot/pause", json={"minutes": 5}, headers={"Origin": "http://evil.example"}).status_code == 403
    game_client.post("/api/bot/stop")
