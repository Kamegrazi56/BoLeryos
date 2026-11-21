"""Moteur de jeu : choix par instance, lectures confiées au moteur, fonctionnalités qu'il ne sait pas faire."""

import asyncio

import pytest

from app import engine as engine_module
from app.engine import DEFAULT_ENGINE, ENGINES, BrowserEngine, GameEngine, describe_unsupported
from app.preferences import Preferences, PreferencesError


def _bot(client):
    return client.app.state.bot


def test_the_browser_engine_is_the_default():
    assert DEFAULT_ENGINE == "browser" and ENGINES == ("browser", "hybrid", "api")


def test_the_browser_engine_follows_the_engine_protocol_and_does_every_feature():
    assert isinstance(BrowserEngine(None, None, None), GameEngine)
    assert BrowserEngine(None, None, None).supports("constructions") and BrowserEngine(None, None, None).supports("n'importe quoi")


def test_the_engine_preference_defaults_to_the_browser_and_refuses_unknown_engines(tmp_path):
    prefs = Preferences(str(tmp_path))
    assert prefs.get()["engine"] == "browser"
    with pytest.raises(PreferencesError):
        prefs.update({"engine": "quantique"})
    assert prefs.get()["engine"] == "browser"
    assert prefs.update({"engine": "api"})["engine"] == "api"
    prefs.update({"engine": "browser"})
    assert Preferences(str(tmp_path)).get()["engine"] == "browser"  # relu sans erreur


def test_the_engine_preference_goes_through_the_api(game_client):
    assert game_client.get("/api/preferences").json()["preferences"]["engine"] == "browser"
    assert game_client.put("/api/preferences", json={"engine": "browser"}).status_code == 200
    assert game_client.put("/api/preferences", json={"engine": "quantique"}).status_code == 422


def test_each_bot_has_a_browser_engine_reading_through_its_own_readers(game_client):
    bot = _bot(game_client)
    assert bot.engine.kind == "browser" and isinstance(bot.engine, GameEngine)
    planets = game_client.portal.call(bot.engine.scan_planets)
    assert len(planets) == 3


def test_the_scan_goes_through_the_engine(game_client, monkeypatch):
    """Le superviseur ne lit plus le jeu lui-même : il le demande au moteur de l'instance."""
    bot = _bot(game_client)
    calls = []

    async def fake_scan():
        calls.append("planets")
        return []

    monkeypatch.setattr(bot.engine, "scan_planets", fake_scan)
    game_client.post("/api/planets/scan")
    assert calls == ["planets"]


class _LimitedEngine:
    kind = "limite"

    def supports(self, feature_id):
        return feature_id == "test"


def test_a_feature_the_engine_cannot_do_is_not_run_and_the_journal_says_so(game_client, monkeypatch):
    bot = _bot(game_client)
    monkeypatch.setitem(bot._engines, "browser", _LimitedEngine())
    result, ok = asyncio.run(_attempt(bot, "constructions"))
    assert ok and "n'est pas disponible avec le moteur « limite »" in result
    assert describe_unsupported(_LimitedEngine(), "Constructions de bâtiments").endswith("rien n'a été fait")


async def _attempt(bot, feature_id):
    feature = bot.scheduler._features[feature_id]
    return await bot.scheduler._attempt(feature)


def test_the_module_exposes_its_labels():
    assert set(engine_module.ENGINE_LABELS) == set(ENGINES)


# ------------------------------------------------------------------ moteur hybride : lecture par l'API, actions par le navigateur
class _Stub:
    def __init__(self, kind, planets=None, error=None, inventory=None):
        self.kind, self._planets, self._error, self._inventory = kind, planets, error, inventory
        self.calls = []

    def supports(self, feature_id):
        return self.kind == "browser"

    async def scan_planets(self):
        self.calls.append("planets")
        if self._error:
            raise self._error
        return self._planets

    async def scan_models(self):
        self.calls.append("models")
        if self._error:
            raise self._error
        return [{"name": self.kind}]

    async def read_inventory(self):
        self.calls.append("inventory")
        if self._error:
            raise self._error
        return self._inventory


def _hybrid(api, browser):
    from app.engine import HybridEngine

    notes = []
    return HybridEngine(browser, api, notes.append), notes


def test_the_hybrid_engine_reads_through_the_api_and_does_every_feature_in_the_browser():
    api, browser = _Stub("api", planets=["api"]), _Stub("browser", planets=["nav"])
    engine, notes = _hybrid(api, browser)
    assert engine.kind == "hybrid" and engine.supports("constructions") and isinstance(engine, GameEngine)
    assert asyncio.run(engine.scan_planets()) == ["api"] and asyncio.run(engine.scan_models()) == [{"name": "api"}]
    assert browser.calls == [] and notes == []  # aucune page ouverte pour lire


def test_the_hybrid_engine_falls_back_to_the_browser_and_says_so():
    from app.planets import GameUiError

    api, browser = _Stub("api", error=GameUiError("jeton expiré")), _Stub("browser", planets=["nav"])
    engine, notes = _hybrid(api, browser)
    assert asyncio.run(engine.scan_planets()) == ["nav"]
    assert len(notes) == 1 and "planètes" in notes[0] and "jeton expiré" in notes[0] and "navigateur" in notes[0]


def test_the_hybrid_inventory_takes_levels_from_the_api_and_researches_from_the_browser():
    api = _Stub("api", inventory=([{"name": "P", "levels": {"caserne": 3}, "units": {}}], {}))
    browser = _Stub("browser", inventory=([{"name": "P", "levels": {"caserne": 1}, "units": {}}], {"ordinateur-quantique": 21}))
    engine, _ = _hybrid(api, browser)
    planets, researches = asyncio.run(engine.read_inventory())
    assert planets[0]["levels"] == {"caserne": 3} and researches == {"ordinateur-quantique": 21}


def test_choosing_the_hybrid_engine_through_the_preferences(game_client):
    assert game_client.put("/api/preferences", json={"engine": "hybrid"}).status_code == 200
    assert _bot(game_client).engine.kind == "hybrid"
