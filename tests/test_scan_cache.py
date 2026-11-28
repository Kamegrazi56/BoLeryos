"""Mémoire des scans : planètes et modèles de vaisseaux gardés d'un démarrage à l'autre, relus seulement s'ils sont vieux."""

import json
import time

from app.scan_cache import FILE_NAME, ScanCache, age_of, describe_age, is_fresh, scope_of

PLANETS = [{"name": "8-50-Concorde", "coords": "M69 38 06", "fer": 10, "cristal": 5, "ether": 1}]
SCOPE = scope_of("Asterion", "joueur@example.org")


def test_scope_depends_on_universe_and_account_and_hides_the_account_name():
    assert scope_of("Asterion", "a@x.org") != scope_of("Asterion", "b@x.org")
    assert scope_of("Asterion", "a@x.org") != scope_of("Orion", "a@x.org")
    assert scope_of("Asterion", " A@X.org ") == scope_of("asterion", "a@x.org")  # casse et espaces sans importance
    assert "a@x.org" not in scope_of("Asterion", "a@x.org")
    assert scope_of("", "a@x.org") == "" and scope_of("Asterion", "") == ""  # rien de connu : rien n'est gardé


def test_freshness_and_age_helpers():
    now = 1_000_000.0
    assert is_fresh(now - 10, 60, now) and not is_fresh(now - 100, 60, now)
    assert not is_fresh(None, 60, now) and not is_fresh(now + 5, 60, now)  # jamais lu, ou date dans le futur
    assert age_of(True) is None and age_of("x") is None
    assert describe_age(30) == "1 min" and describe_age(20 * 60) == "20 min"
    assert describe_age(3 * 3600) == "3 h" and describe_age(2 * 86400) == "2 j"


def test_saved_scans_survive_a_restart(tmp_path):
    cache = ScanCache(str(tmp_path))
    cache.save("planets", SCOPE, PLANETS, time.time(), "démarrage")
    cache.save("models", SCOPE, [{"name": "Chasseur"}], time.time(), "manuel")
    reborn = ScanCache(str(tmp_path))  # le bot redémarre
    assert reborn.load("planets", SCOPE)["items"] == PLANETS
    assert reborn.load("planets", SCOPE)["source"] == "démarrage"
    assert reborn.load("models", SCOPE)["items"] == [{"name": "Chasseur"}]


def test_another_universe_or_account_never_reuses_the_saved_scans(tmp_path):
    cache = ScanCache(str(tmp_path))
    cache.save("planets", SCOPE, PLANETS, time.time(), "démarrage")
    assert cache.load("planets", scope_of("Orion", "joueur@example.org")) is None
    assert cache.load("planets", "") is None
    cache.save("models", scope_of("Orion", "joueur@example.org"), [{"name": "Autre"}], time.time(), "manuel")
    assert cache.load("planets", SCOPE) is None  # l'ancienne portée est remplacée, jamais mélangée


def test_nothing_is_written_without_a_known_scope(tmp_path):
    ScanCache(str(tmp_path)).save("planets", "", PLANETS, time.time(), "démarrage")
    assert not (tmp_path / FILE_NAME).exists()


def test_the_account_name_is_not_written_in_the_file(tmp_path):
    ScanCache(str(tmp_path)).save("planets", SCOPE, PLANETS, time.time(), "démarrage")
    assert "joueur@example.org" not in (tmp_path / FILE_NAME).read_text(encoding="utf8")


def test_a_broken_or_invalid_file_is_ignored(tmp_path):
    path = tmp_path / FILE_NAME
    path.write_text("{pas du json", encoding="utf8")
    assert ScanCache(str(tmp_path)).load("planets", SCOPE) is None
    path.write_text(json.dumps({"scope": SCOPE, "planets": {"items": [1, 2], "read_at": time.time()},
                                "models": {"items": [{"name": "A"}], "read_at": "hier"}}), encoding="utf8")
    cache = ScanCache(str(tmp_path))
    assert cache.load("planets", SCOPE) is None and cache.load("models", SCOPE) is None
    cache.save("planets", SCOPE, PLANETS, time.time(), "démarrage")  # et le fichier se répare au scan suivant
    assert ScanCache(str(tmp_path)).load("planets", SCOPE)["items"] == PLANETS


def test_without_a_data_folder_the_cache_works_in_memory_only():
    cache = ScanCache(None)
    cache.save("planets", SCOPE, PLANETS, time.time(), "démarrage")
    assert cache.load("planets", SCOPE)["items"] == PLANETS


# ---------------------------------------------------------------- avec le faux jeu
def _runner(client):
    return client.app.state.bot.supervisor


def _ready(client):
    return client.portal.call(_runner(client).ensure_ready)


def _count_scans(runner, calls: list) -> None:
    real = runner.bot.engine.scan_planets

    async def counted():
        calls.append(1)
        return await real()

    runner.bot.engine.scan_planets = counted


def _restart(runner, tmp_path) -> None:
    """Ce que fait un redémarrage : plus rien en mémoire, mais le dossier de données est là."""
    runner.cache = ScanCache(str(tmp_path))
    runner.scanned_planets, runner.scan_at, runner.scan_source = [], None, ""
    runner.scanned_models, runner.models_scan_at, runner.models_scan_source = [], None, ""
    runner._scope_seen = None
    for name in ("scan", "models", "pillars", "inventory", "explo_models"):
        setattr(runner, f"_{name}_done_session", False)
        setattr(runner, f"_{name}_attempts", 0)


def test_a_restart_reuses_the_saved_scans_instead_of_reading_the_game_again(game_client, tmp_path, monkeypatch):
    runner = _runner(game_client)
    monkeypatch.setattr(runner, "_scope", lambda: SCOPE)
    runner.cache = ScanCache(str(tmp_path))
    assert _ready(game_client) is None
    first = runner.scan_summary()
    models_at = runner.models_summary()["scanned_at"]
    assert first["planets"] and models_at
    _restart(runner, tmp_path)
    calls: list = []
    _count_scans(runner, calls)
    assert _ready(game_client) is None
    assert calls == []  # aucun nouveau scan des planètes
    again = runner.scan_summary()
    assert again["planets"] == first["planets"] and again["scanned_at"] == first["scanned_at"]
    assert runner.models_summary()["scanned_at"] == models_at and runner.models_summary()["models"]
    assert any("gardée, pas de nouveau scan" in e["msg"] for e in game_client.app.state.bot.events(40))


def test_a_restart_rescans_when_the_saved_scan_is_too_old(game_client, tmp_path, monkeypatch):
    runner = _runner(game_client)
    monkeypatch.setattr(runner, "_scope", lambda: SCOPE)
    runner.cache = ScanCache(str(tmp_path))
    _ready(game_client)
    first = runner.scan_summary()["scanned_at"]
    _restart(runner, tmp_path)
    monkeypatch.setattr("app.supervisor.PLANETS_MAX_AGE", -1.0)
    _ready(game_client)
    assert runner.scan_summary()["scanned_at"] > first


def test_a_change_of_universe_drops_the_saved_scans_and_reads_again(game_client, tmp_path, monkeypatch):
    runner = _runner(game_client)
    scope = {"value": SCOPE}
    monkeypatch.setattr(runner, "_scope", lambda: scope["value"])
    runner.cache = ScanCache(str(tmp_path))
    _ready(game_client)
    first = runner.scan_summary()["scanned_at"]
    scope["value"] = scope_of("Orion", "joueur@example.org")  # l'utilisateur change d'univers
    runner._scan_done_session, runner._scan_attempts = False, 0
    runner._models_done_session, runner._models_attempts = False, 0
    calls: list = []
    _count_scans(runner, calls)
    _ready(game_client)
    assert calls == [1] and runner.scan_summary()["scanned_at"] > first
    assert ScanCache(str(tmp_path)).load("planets", SCOPE) is None  # l'ancien univers n'est pas resté dans le fichier


def test_an_inventory_of_other_planets_is_read_again_even_if_recent(game_client, monkeypatch):
    bot = game_client.app.state.bot
    runner = bot.supervisor
    _ready(game_client)
    stranger = [{"name": "Planète d'un autre univers", "coords": "", "levels": {"mine_fer": 1}, "units": {}}]
    bot.inventory.set(stranger, "ancien")
    runner._inventory_done_session, runner._inventory_attempts = False, 0
    _ready(game_client)
    assert bot.inventory.get()["source"] == "démarrage"  # relu : les noms ne correspondent pas aux planètes du scan


def test_the_instance_file_list_knows_the_scan_file():
    from app.instances import INSTANCE_FILES

    assert FILE_NAME in INSTANCE_FILES
