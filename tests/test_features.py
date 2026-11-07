"""Fonctionnalités : réglages enregistrés, minuteur min/max, priorités, interrupteur général, un seul passage à la fois."""

import asyncio
import random
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.features import Feature, FeaturesError, FeatureStore, validate_entry
from app.main import create_app
from app.scheduler import Scheduler


# ------------------------------------------------------------------------------ outils
class FakeBot:
    def __init__(self):
        self.work = asyncio.Lock()
        self.messages = []

    def log(self, message, level="info", event=False, kind="info"):
        self.messages.append((level, message))


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class MidRng(random.Random):
    """Tire toujours le milieu de l'intervalle : minuteur prévisible."""

    def uniform(self, a, b):
        return (a + b) / 2


def build(features, tmp_path=None, blocker=lambda: None, rng=None):
    store = FeatureStore(str(tmp_path) if tmp_path else None, features, floor=0.5)
    clock, bot = Clock(), FakeBot()
    sched = Scheduler(bot, store, features, blocker=blocker, gap=(0, 0), tick=0.01, clock=clock, rng=rng or MidRng())
    return sched, bot, clock


def make(tmp_path=None, blocker=lambda: None, ids=("a", "b", "c"), rng=None):
    runs = []

    def feature(fid):
        async def run(bot):
            runs.append(fid)
            return f"{fid} fait"

        return Feature(fid, fid.upper(), "test", run)

    sched, bot, clock = build(tuple(feature(i) for i in ids), tmp_path, blocker, rng)
    return sched, bot, clock, runs


def enable(sched, fid, low=10, high=20):
    sched.update(fid, {"enabled": True, "min_minutes": low, "max_minutes": high})


def manual(sched):
    """Bot ON sans la boucle de fond : les tests déclenchent chaque tour à la main."""
    sched.start()
    sched._task.cancel()


# ------------------------------------------------------------------------ réglages
def test_a_feature_cannot_be_enabled_without_its_timer():
    with pytest.raises(FeaturesError) as err:
        validate_entry({"enabled": True}, floor=1)
    assert "minuteur" in err.value.errors[0].lower()
    assert validate_entry({"enabled": False}, floor=1) == {"enabled": False, "min_minutes": None, "max_minutes": None}


@pytest.mark.parametrize("low,high", [(5, 2), (0.1, 3), (1, 99999), (True, 3), ("2", 3), (-1, 3)])
def test_bad_timers_are_refused(low, high):
    with pytest.raises(FeaturesError):
        validate_entry({"enabled": False, "min_minutes": low, "max_minutes": high}, floor=1)


def test_settings_survive_a_restart_including_order_and_activation(tmp_path):
    sched, *_ = make(tmp_path)
    enable(sched, "b", 5, 7)
    sched.reorder(["c", "a", "b"])
    again, *_ = make(tmp_path)
    assert again.store.order == ["c", "a", "b"]
    assert again.store.get("b") == {"enabled": True, "min_minutes": 5.0, "max_minutes": 7.0}
    assert again.on is False  # seul l'interrupteur général repasse à OFF


def test_a_corrupt_or_invalid_file_falls_back_to_defaults(tmp_path):
    (tmp_path / "features.json").write_text('{"order": ["zzz", "b"], "features": {"a": {"enabled": true}}}', encoding="utf8")
    sched, *_ = make(tmp_path)
    assert sched.store.get("a")["enabled"] is False  # activée sans minuteur : refusée
    assert sched.store.order == ["b", "a", "c"]  # inconnue ignorée, les autres complétées
    (tmp_path / "features.json").write_text("pas du json", encoding="utf8")
    assert make(tmp_path)[0].store.order == ["a", "b", "c"]


def test_order_must_list_every_feature_once():
    sched, *_ = make()
    for bad in (["a", "b"], ["a", "a", "b"], ["a", "b", "c", "d"]):
        with pytest.raises(FeaturesError):
            sched.reorder(bad)
    assert sched.store.order == ["a", "b", "c"]


# ------------------------------------------------------------------------ ordonnanceur
def test_nothing_runs_while_the_bot_is_off():
    async def scenario():
        sched, _, _, runs = make()
        enable(sched, "a")
        assert await sched.step() is False and runs == []

    asyncio.run(scenario())


def test_an_enabled_feature_runs_right_away_when_the_bot_goes_on_then_waits_its_timer():
    async def scenario():
        sched, _, clock, runs = make()
        enable(sched, "a", 10, 20)  # bot OFF : rien
        manual(sched)
        assert await sched.step() is True and runs == ["a"]
        assert await sched.step() is False  # minuteur : milieu de 10..20 = 15 min
        clock.now += 14 * 60
        assert await sched.step() is False
        clock.now += 61
        assert await sched.step() is True and runs == ["a", "a"]

    asyncio.run(scenario())


def test_the_timer_restarts_from_the_end_of_the_pass_with_a_value_between_min_and_max():
    async def scenario():
        sched, *_ = make(rng=random.Random(3))
        enable(sched, "a", 10, 20)
        manual(sched)
        await sched.step()
        wait = sched.summary()["features"][0]["next_in_s"]
        assert 600 <= wait <= 1200

    asyncio.run(scenario())


def test_priority_decides_who_goes_first():
    async def scenario():
        sched, _, _, runs = make()
        for fid in ("a", "b", "c"):
            enable(sched, fid)
        sched.reorder(["c", "a", "b"])
        manual(sched)
        for _ in range(3):
            assert await sched.step() is True
        assert runs == ["c", "a", "b"]
        assert await sched.step() is False

    asyncio.run(scenario())


def test_two_passes_never_overlap():
    async def scenario():
        running, seen = 0, []

        async def slow(bot):
            nonlocal running
            running += 1
            seen.append(running)
            await asyncio.sleep(0.05)
            running -= 1
            return "ok"

        sched, *_ = build((Feature("a", "A", "", slow), Feature("b", "B", "", slow)))
        for fid in ("a", "b"):
            sched.update(fid, {"enabled": True, "min_minutes": 1, "max_minutes": 1})
        manual(sched)
        await asyncio.gather(sched.step(), sched.step())
        assert seen == [1, 1]  # le verrou sérialise les passages

    asyncio.run(scenario())


def test_a_blocked_pass_stays_due_and_runs_once_the_blocker_clears():
    async def scenario():
        reason = ["le bot n'est pas dans une partie"]
        sched, _, _, runs = make(blocker=lambda: reason[0])
        enable(sched, "a")
        manual(sched)
        assert await sched.step() is False and runs == [] and "partie" in sched.waiting
        reason[0] = None
        assert await sched.step() is True and runs == ["a"] and sched.waiting == ""

    asyncio.run(scenario())


def test_bot_off_lets_the_running_pass_finish_then_stops_everything():
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def slow(bot):
            started.set()
            await release.wait()
            return "fini"

        sched, _, clock = build((Feature("a", "A", "", slow),))
        sched.update("a", {"enabled": True, "min_minutes": 1, "max_minutes": 1})
        sched.start()
        await started.wait()
        sched.stop()
        state = sched.summary()
        assert state["bot_on"] is False and state["stopping"] is True and state["features"][0]["running"] is True
        release.set()
        await sched._task
        assert sched.summary()["stopping"] is False
        assert sched.summary()["features"][0]["last_result"] == "fini"
        clock.now += 3600
        assert await sched.step() is False  # OFF : plus rien ne part

    asyncio.run(scenario())


def test_a_failing_feature_is_logged_and_does_not_stop_the_others():
    async def scenario():
        async def boom(bot):
            raise RuntimeError("secret interne")

        async def fine(bot):
            return "ok"

        sched, bot, _ = build((Feature("a", "A", "", boom), Feature("b", "B", "", fine)))
        for fid in ("a", "b"):
            sched.update(fid, {"enabled": True, "min_minutes": 1, "max_minutes": 1})
        manual(sched)
        await sched.step()
        await sched.step()
        items = {f["id"]: f for f in sched.summary()["features"]}
        assert items["a"]["last_ok"] is False and "RuntimeError" in items["a"]["last_result"]
        assert items["b"]["last_ok"] is True
        assert not any("secret interne" in m for _, m in bot.messages)  # pas de détail interne dans le journal

    asyncio.run(scenario())


def test_disabling_a_feature_cancels_its_next_pass():
    async def scenario():
        sched, _, clock, runs = make()
        enable(sched, "a")
        manual(sched)
        sched.update("a", {"enabled": False})
        clock.now += 10_000
        assert await sched.step() is False and runs == []
        assert sched.summary()["features"][0]["next_in_s"] is None

    asyncio.run(scenario())


def test_the_background_loop_runs_passes_by_itself():
    async def scenario():
        sched, _, _, runs = make()
        enable(sched, "a")
        sched.start()
        for _ in range(100):
            if runs:
                break
            await asyncio.sleep(0.02)
        sched.stop()
        await sched.shutdown()
        assert runs == ["a"]

    asyncio.run(scenario())


# ------------------------------------------------------------------------------ API
def _test(client):
    return next(f for f in client.get("/api/features").json()["features"] if f["id"] == "test")


@pytest.fixture
def api(settings, tmp_path):
    with TestClient(create_app(replace(settings, data_dir=str(tmp_path)))) as c:
        yield c


def test_features_api_lists_the_test_feature_with_no_timer_and_bot_off(api):
    data = api.get("/api/features").json()
    assert data["bot_on"] is False
    assert [f["id"] for f in data["features"]] == ["constructions", "researches", "populations", "ships", "components", "explorations", "excavations", "hackings", "test"]
    assert all(f["enabled"] is False and f["min_minutes"] is None for f in data["features"])


def test_api_refuses_enabling_without_a_timer_and_bad_ranges(api):
    res = api.put("/api/features/test", json={"enabled": True})
    assert res.status_code == 422 and "minuteur" in " ".join(res.json()["detail"]).lower()
    assert api.put("/api/features/test", json={"min_minutes": 5, "max_minutes": 1}).status_code == 422
    assert api.put("/api/features/inconnue", json={"enabled": False}).status_code == 404
    assert api.put("/api/features/order", json={"order": ["x"]}).status_code == 422


def test_api_origin_check_on_every_write(api):
    evil = {"Origin": "http://evil.example"}
    assert api.put("/api/features/test", json={"enabled": False}, headers=evil).status_code == 403
    assert api.put("/api/features/order", json={"order": ["test", "constructions"]}, headers=evil).status_code == 403
    assert api.post("/api/bot/start", headers=evil).status_code == 403
    assert api.post("/api/bot/stop", headers=evil).status_code == 403


def test_the_settings_are_kept_after_a_restart_but_the_bot_is_off(settings, tmp_path):
    cfg = replace(settings, data_dir=str(tmp_path))
    with TestClient(create_app(cfg)) as c:
        assert c.put("/api/features/test", json={"enabled": True, "min_minutes": 0.5, "max_minutes": 1}).status_code == 200
        assert c.post("/api/bot/start").json()["bot_on"] is True
    with TestClient(create_app(cfg)) as c:
        data = c.get("/api/features").json()
        assert data["bot_on"] is False
        test = next(f for f in data["features"] if f["id"] == "test")
        assert test["enabled"] is True and test["min_minutes"] == 0.5


def test_bot_switch_and_a_pass_waits_until_the_bot_is_in_a_game(api):
    assert api.put("/api/features/test", json={"enabled": True, "min_minutes": 0.01, "max_minutes": 0.01}).status_code == 200
    time.sleep(0.4)
    assert _test(api)["last_at"] is None  # OFF : rien ne tourne
    assert api.post("/api/bot/start").json()["bot_on"] is True
    assert api.get("/api/status").json()["scheduler"]["on"] is True
    deadline = time.time() + 5
    while time.time() < deadline and not api.get("/api/features").json()["waiting"]:
        time.sleep(0.1)
    data = api.get("/api/features").json()
    assert "partie" in data["waiting"] and _test(api)["last_at"] is None  # pas dans une partie : il attend
    assert api.post("/api/bot/stop").json()["bot_on"] is False


def test_a_pass_runs_once_the_bot_is_in_a_game(world_client):
    c = world_client
    assert c.post("/api/login", json={"identifier": "joueur.test@example.com", "password": "good-password"}).json()["state"] == "logged_in"
    assert c.post("/api/universe", json={"index": 0}).status_code == 200
    deadline = time.time() + 30
    while time.time() < deadline and "/game/" not in c.get("/api/status").json()["page"]:
        time.sleep(0.2)
    assert c.put("/api/features/test", json={"enabled": True, "min_minutes": 0.01, "max_minutes": 0.01}).status_code == 200
    c.post("/api/bot/start")
    deadline = time.time() + 15
    while time.time() < deadline and not _test(c)["last_at"]:
        time.sleep(0.2)
    item = _test(c)
    assert item["last_at"] and "rien n'a été fait" in item["last_result"]
    assert any("Fonctionnalité « Test" in e["msg"] for e in c.get("/api/events").json())
    c.post("/api/bot/stop")
