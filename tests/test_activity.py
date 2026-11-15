"""État d'activité affiché à côté du bouton ON/OFF : passage en cours, attente d'un minuteur, bloqué, arrêté."""

import asyncio
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.features import Feature
from app.main import create_app

from .test_features import build, enable, make, manual


def test_bot_off_reports_off():
    sched, _, _, _ = make()
    enable(sched, "a")
    assert sched.activity() == {"state": "off"}
    assert sched.summary()["activity"]["state"] == "off"


def test_bot_on_without_any_enabled_feature_is_idle_with_nothing_planned():
    async def scenario():
        sched, _, _, _ = make()
        manual(sched)
        assert sched.activity() == {"state": "idle", "next": None}

    asyncio.run(scenario())


def test_idle_reports_the_nearest_feature_timer():
    async def scenario():
        sched, _, clock, _ = make()
        enable(sched, "a", 10, 10)  # 10 min
        enable(sched, "b", 1, 1)  # 1 min
        manual(sched)
        for _ in range(2):
            await sched.step()  # les deux passages ont lieu tout de suite, puis chacun a son minuteur
        clock.now += 5
        act = sched.activity()
        assert act["state"] == "idle"
        assert act["next"]["id"] == "b" and act["next"]["label"] == "B"
        assert 50 < act["next"]["in_s"] <= 60

    asyncio.run(scenario())


def test_idle_next_feature_is_the_one_due_first_not_the_first_in_the_list():
    async def scenario():
        sched, _, _, _ = make()
        enable(sched, "a", 30, 30)
        enable(sched, "c", 2, 2)
        manual(sched)
        await sched.step()
        await sched.step()
        assert sched.activity()["next"]["id"] == "c"

    asyncio.run(scenario())


def test_a_running_pass_is_reported_with_its_feature():
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def slow(bot):
            started.set()
            await release.wait()
            return "fini"

        sched, _, _ = build((Feature("a", "Exploration", "", slow),))
        sched.update("a", {"enabled": True, "min_minutes": 1, "max_minutes": 1})
        sched.start()
        await started.wait()
        assert sched.activity() == {"state": "running", "feature": {"id": "a", "label": "Exploration"}}
        sched.stop()
        assert sched.activity() == {"state": "stopping", "feature": {"id": "a", "label": "Exploration"}}
        release.set()
        await sched._task
        assert sched.activity() == {"state": "off"}

    asyncio.run(scenario())


def test_a_due_pass_that_cannot_start_is_reported_as_blocked_with_the_reason():
    async def scenario():
        sched, _, _, runs = make(blocker=lambda: "le bot n'est pas dans une partie")
        enable(sched, "a")
        manual(sched)
        await sched.step()
        assert runs == []
        assert sched.activity() == {"state": "blocked", "reason": "le bot n'est pas dans une partie"}

    asyncio.run(scenario())


def test_a_due_feature_outside_its_own_hours_shows_its_pause_in_the_next_entry():
    async def scenario():
        sched, bot, clock, _ = make()
        bot.feature_pause = lambda fid: "hors horaires (reprise à 08:00)" if fid == "a" else None
        enable(sched, "a", 1, 1)
        manual(sched)
        clock.now += 1000
        act = sched.activity()
        assert act["state"] == "idle"
        assert act["next"]["id"] == "a" and act["next"]["paused"].startswith("hors horaires")
        assert act["next"]["in_s"] == 0

    asyncio.run(scenario())


@pytest.fixture
def api(settings, tmp_path):
    with TestClient(create_app(replace(settings, data_dir=str(tmp_path)))) as c:
        yield c


def test_status_api_exposes_the_activity_next_to_the_bot_switch(api):
    assert api.get("/api/status").json()["scheduler"]["activity"] == {"state": "off"}
    assert api.put("/api/features/test", json={"enabled": True, "min_minutes": 0.01, "max_minutes": 0.01}).status_code == 200
    assert api.post("/api/bot/start").json()["bot_on"] is True
    deadline = time.time() + 5
    act = api.get("/api/status").json()["scheduler"]["activity"]
    while time.time() < deadline and act["state"] not in ("blocked", "running"):
        time.sleep(0.1)
        act = api.get("/api/status").json()["scheduler"]["activity"]
    assert act["state"] == "blocked" and "partie" in act["reason"]  # le bot n'est pas dans une partie : passage dû mais bloqué
    assert api.get("/api/features").json()["activity"]["state"] == "blocked"
    assert api.post("/api/bot/stop").json()["bot_on"] is False
    assert api.get("/api/status").json()["scheduler"]["activity"] == {"state": "off"}
