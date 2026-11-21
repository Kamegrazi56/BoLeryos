"""Instances de jeu : registre, démarrage/arrêt indépendants, routes `/api/i/<instance>/…`, session de connexion."""

import asyncio
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.account import Account
from app.browser_host import BrowserHost
from app.instance_manager import InstanceError, InstanceManager
from app.main import create_app


@pytest.fixture
def no_browser(monkeypatch):
    """Les instances démarrent sans lancer Chromium : on note seulement lesquelles ont été « lancées »."""
    kicked = []
    monkeypatch.setattr(InstanceManager, "_kick", staticmethod(lambda bot: kicked.append(bot)))
    return kicked


@pytest.fixture
def data_client(settings, tmp_path, no_browser):
    cfg = replace(settings, data_dir=str(tmp_path))
    with TestClient(create_app(cfg)) as client:
        yield client


def _create(client, universe, **extra):
    r = client.post("/api/instances", json={"universe": universe, **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _manager(settings, tmp_path, **overrides):
    cfg = replace(settings, data_dir=str(tmp_path), **overrides)
    return InstanceManager(cfg, BrowserHost(cfg), Account(cfg.data_dir))


# ------------------------------------------------------------------ registre
def test_first_run_makes_one_instance_from_the_existing_files(settings, tmp_path):
    (tmp_path / "preferences.json").write_text(json.dumps({"universe": "Asterion"}), encoding="utf8")
    manager = _manager(settings, tmp_path)
    summary = manager.summary()
    assert [i["id"] for i in summary["instances"]] == ["asterion"]
    assert summary["instances"][0]["universe"] == "Asterion" and summary["instances"][0]["running"]
    assert (tmp_path / "instances.json").exists()


def test_registry_is_saved_and_reloaded(settings, tmp_path):
    first = _manager(settings, tmp_path)
    first.create("Orion")
    second = _manager(settings, tmp_path)
    assert [i["universe"] for i in second.summary()["instances"]] == ["", "Orion"]
    assert not second.summary()["instances"][1]["running"]  # créée mais pas démarrée


def test_a_broken_registry_is_ignored(settings, tmp_path):
    (tmp_path / "instances.json").write_text("pas du json", encoding="utf8")
    manager = _manager(settings, tmp_path)
    assert len(manager.summary()["instances"]) == 1


def test_registry_entries_with_unsafe_ids_are_dropped(settings, tmp_path):
    bad = [{"id": "../x"}, {"id": "compte"}, {"id": "ok", "universe": "Orion"}, {"id": "ok"}, "x"]
    (tmp_path / "instances.json").write_text(json.dumps({"instances": bad}), encoding="utf8")
    assert [i["id"] for i in _manager(settings, tmp_path).summary()["instances"]] == ["ok"]


# ------------------------------------------------------------------ création
def test_create_refuses_a_second_instance_on_the_same_server(settings, tmp_path):
    manager = _manager(settings, tmp_path)
    manager.create("Orion")
    with pytest.raises(InstanceError) as err:
        manager.create(" orion ")
    assert err.value.status == 409


def test_the_server_chosen_after_creation_counts_as_used(settings, tmp_path):
    """L'instance d'origine naît sans serveur ; celui que le joueur choisit ensuite doit bloquer une 2e instance dessus."""
    manager = _manager(settings, tmp_path)  # instance « principale », sans serveur, démarrée
    manager.resolve().supervisor.prefs.update({"universe": "Asterion"})
    summary = manager.summary()
    assert summary["instances"][0]["universe"] == "Asterion"
    with pytest.raises(InstanceError) as err:
        manager.create(" asterion ")
    assert err.value.status == 409
    manager.universes = ["Asterion", "Kael"]
    assert {u["name"]: u["used"] for u in manager.summary()["universes"]} == {"Asterion": True, "Kael": False}


def test_the_server_of_a_stopped_instance_is_read_from_its_preferences(settings, tmp_path, no_browser):
    manager = _manager(settings, tmp_path)
    info = manager.create("Orion")
    asyncio.run(manager.start(info["id"]))
    manager.resolve(info["id"]).supervisor.prefs.update({"universe": "Kael"})
    asyncio.run(manager.stop(info["id"]))
    assert [i["universe"] for i in manager.summary()["instances"]] == ["", "Kael"]
    with pytest.raises(InstanceError):
        manager.create("Kael")


def test_create_refuses_beyond_the_maximum(settings, tmp_path):
    manager = _manager(settings, tmp_path, max_instances=2)
    manager.create("Orion")
    with pytest.raises(InstanceError, match="maximal"):
        manager.create("Vega")


def test_create_refuses_an_empty_server(settings, tmp_path):
    with pytest.raises(InstanceError):
        _manager(settings, tmp_path).create("   ")


def test_the_session_name_is_reserved_for_the_login_session(settings, tmp_path):
    manager = _manager(settings, tmp_path)
    created = manager.create("Compte")
    assert created["id"] != "compte"


def test_create_writes_the_server_in_the_instance_preferences(settings, tmp_path):
    created = _manager(settings, tmp_path).create("Orion", "Mon bot")
    assert created["name"] == "Mon bot"
    prefs = json.loads((tmp_path / "instances" / created["id"] / "preferences.json").read_text(encoding="utf8"))
    assert prefs["universe"] == "Orion"


# ------------------------------------------------------------------ routes
def test_instances_api_lists_creates_starts_stops_and_deletes(data_client, no_browser, tmp_path):
    assert data_client.get("/api/instances").json()["max"] == 5
    summary = _create(data_client, "Orion")
    orion = next(i for i in summary["instances"] if i["universe"] == "Orion")
    assert not orion["running"] and orion["state"] == "stopped"

    started = data_client.post(f"/api/instances/{orion['id']}/start").json()
    assert next(i for i in started["instances"] if i["id"] == orion["id"])["running"]
    assert len(no_browser) == 1  # une seule instance lancée

    stopped = data_client.post(f"/api/instances/{orion['id']}/stop").json()
    assert not next(i for i in stopped["instances"] if i["id"] == orion["id"])["running"]

    deleted = data_client.delete(f"/api/instances/{orion['id']}").json()
    assert all(i["id"] != orion["id"] for i in deleted["instances"])
    assert not (tmp_path / "instances" / orion["id"]).exists()
    assert any((tmp_path / "instances" / ".corbeille").iterdir())  # mis à la corbeille, pas effacé


def test_api_errors_use_the_right_status(data_client):
    assert data_client.post("/api/instances/inconnue/start").status_code == 404
    assert data_client.post("/api/instances", json={"universe": ""}).status_code == 422
    _create(data_client, "Orion")
    assert data_client.post("/api/instances", json={"universe": "Orion"}).status_code == 409


def test_management_routes_refuse_a_foreign_origin(data_client):
    headers = {"Origin": "https://pirate.example"}
    assert data_client.post("/api/instances", json={"universe": "Orion"}, headers=headers).status_code == 403
    assert data_client.post("/api/instances/scan", headers=headers).status_code == 403
    assert data_client.delete("/api/instances/principale", headers=headers).status_code == 403


def test_scoped_routes_reach_the_named_instance_only(data_client):
    orion = next(i for i in _create(data_client, "Orion")["instances"] if i["universe"] == "Orion")
    assert data_client.get(f"/api/i/{orion['id']}/status").status_code == 409  # arrêtée
    data_client.post(f"/api/instances/{orion['id']}/start")
    assert data_client.get(f"/api/i/{orion['id']}/status").status_code == 200

    line = {"building": "mine-de-fer", "level": 45}
    assert data_client.put(f"/api/i/{orion['id']}/constructions", json={"planets": [], "queue": [line]}).status_code == 200
    mine = data_client.get(f"/api/i/{orion['id']}/constructions").json()["settings"]["queue"]
    default = data_client.get("/api/constructions").json()["settings"]["queue"]  # instance par défaut : autre dossier
    assert [q["building"] for q in mine] == ["mine-de-fer"] and default == []


def test_stopped_instance_does_not_stop_the_others(data_client):
    a = data_client.get("/api/instances").json()["default"]
    b = next(i for i in _create(data_client, "Orion")["instances"] if i["universe"] == "Orion")["id"]
    data_client.post(f"/api/instances/{b}/start")
    data_client.post(f"/api/instances/{b}/stop")
    assert data_client.get(f"/api/i/{a}/status").status_code == 200
    assert data_client.get(f"/api/i/{b}/status").status_code == 409


def test_unknown_or_malformed_instance_is_not_found(data_client):
    assert data_client.get("/api/i/inconnue/status").status_code == 404
    assert data_client.get("/api/i/A!b/status").status_code == 404
    assert data_client.get("/api/i/principale").status_code == 404  # pas de chemin derrière l'identifiant


def test_websocket_of_an_unknown_instance_is_refused(data_client):
    with pytest.raises(WebSocketDisconnect):
        with data_client.websocket_connect("/ws/i/inconnue/vision"):
            pass


def test_bare_routes_follow_the_default_instance(data_client):
    default = data_client.get("/api/instances").json()["default"]
    bare = data_client.get("/api/status").json()
    scoped = data_client.get(f"/api/i/{default}/status").json()
    assert bare["state"] == scoped["state"] and bare["display"] == scoped["display"]


@pytest.mark.parametrize("page", ["/", "/ecran", "/reglages", "/suivi"])
def test_every_page_loads_the_instance_script_before_the_shell(data_client, page):
    html = data_client.get(page).text
    assert html.index("/static/instance.js") < html.index("/static/shell.js")
    assert data_client.get("/static/instance.js").status_code == 200


# ------------------------------------------------------------------ session de connexion
def test_login_session_has_no_data_folder_and_answers_without_instance(settings, tmp_path, no_browser):
    cfg = replace(settings, data_dir=str(tmp_path))
    with TestClient(create_app(cfg)) as client:
        client.delete("/api/instances/principale")  # plus aucune instance
        assert client.get("/api/status").status_code == 409  # « créez-en une »
        assert client.get("/api/i/compte/account").status_code == 200  # mais le compte reste accessible
        assert client.app.state.manager.lobby()._settings.data_dir is None


def test_scan_without_credentials_asks_for_them(data_client):
    result = data_client.post("/api/instances/scan").json()
    assert result["universes"] == [] and "identifiants" in result["error"]


def test_starting_an_instance_closes_the_login_session(data_client):
    manager = data_client.app.state.manager
    orion = next(i for i in _create(data_client, "Orion")["instances"] if i["universe"] == "Orion")["id"]
    manager.lobby()
    assert manager.summary()["lobby"]["open"]
    data_client.post(f"/api/instances/{orion}/start")
    assert not manager.summary()["lobby"]["open"]


# ------------------------------------------------------------------ vrai navigateur : un contexte par instance
def test_each_running_instance_has_its_own_browser_context(settings, tmp_path):
    async def scenario():
        manager = _manager(settings, tmp_path)
        second = manager.create("Orion")["id"]
        first = manager.default_id
        try:
            await manager.start(second)
            for bot in manager.running_bots():
                await bot.ensure_started()
            assert manager._host.context_count == 2
            await manager.stop(second)
            assert manager._host.context_count == 1  # l'autre instance continue
            assert manager.resolve(first).status()["state"] is not None
        finally:
            await manager.shutdown()
            await manager._host.shutdown()

    asyncio.run(scenario())


# ------------------------------------------------------------------ reprise, arrêt, écran commun, mémoire
def test_instances_running_at_shutdown_resume_at_next_start(settings, tmp_path, no_browser):
    first = _manager(settings, tmp_path)
    orion = first.create("Orion")["id"]
    asyncio.run(first.start(orion))
    again = _manager(settings, tmp_path)  # redémarrage de l'application
    assert {b._settings.data_dir.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for b in again.running_bots()} == {first.default_id, orion}


def test_a_stopped_instance_stays_stopped_after_restart(settings, tmp_path, no_browser):
    first = _manager(settings, tmp_path)
    orion = first.create("Orion")["id"]

    async def scenario():
        await first.start(orion)
        await first.stop(orion)

    asyncio.run(scenario())
    assert orion not in [i["id"] for i in _manager(settings, tmp_path).summary()["instances"] if i["running"]]


def test_startup_only_launches_instances_when_autostart_is_on(settings, tmp_path, no_browser, monkeypatch):
    monkeypatch.setattr("app.instance_manager.STARTUP_STAGGER_SECONDS", 0.0)

    async def scenario(autostart):
        manager = _manager(settings, tmp_path, autostart=autostart)
        await manager.startup()
        if manager._startup_task:
            await manager._startup_task
        await manager.shutdown()

    asyncio.run(scenario(False))
    assert no_browser == []
    asyncio.run(scenario(True))
    assert len(no_browser) == 1  # l'instance qui tournait reprend


def test_shutdown_closes_every_session(settings, tmp_path, no_browser):
    async def scenario():
        manager = _manager(settings, tmp_path)
        manager.create("Orion")
        await manager.start("orion")
        manager.lobby()
        await manager.shutdown()
        return manager

    manager = asyncio.run(scenario())
    assert manager.running_bots() == [] and not manager.summary()["lobby"]["open"]


def test_the_window_watched_goes_back_to_front_when_another_instance_opens_one(settings, tmp_path, monkeypatch):
    monkeypatch.setattr("app.instance_manager.REFRONT_DELAY_SECONDS", 0.0)
    fronted = []

    async def scenario():
        manager = _manager(settings, tmp_path)
        watched = manager.running_bots()[0]
        watched._viewers = 1

        async def record(delay=0.0):
            fronted.append(watched)

        monkeypatch.setattr(watched, "bring_to_front", record)
        manager.refront()
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert len(fronted) == 1


def test_chromium_memory_is_read_from_proc(tmp_path):
    from app.browser_host import chromium_memory_mb

    for pid, cmd, pages in (("101", b"/usr/bin/chromium\0--x", 2560), ("102", b"python\0app", 9999), ("103", b"chrome\0", 2560)):
        folder = tmp_path / pid
        folder.mkdir()
        (folder / "cmdline").write_bytes(cmd)
        (folder / "statm").write_text(f"1 {pages} 0 0 0 0 0")
    (tmp_path / "self").mkdir()  # pas un identifiant : ignoré
    import os

    expected = round(5120 * os.sysconf("SC_PAGE_SIZE") / 1_048_576, 1)
    assert chromium_memory_mb(str(tmp_path)) == expected
    assert chromium_memory_mb(str(tmp_path / "absent")) is None


def test_deleting_the_last_instance_is_not_undone_by_the_old_flat_files(settings, tmp_path, no_browser):
    (tmp_path / "preferences.json").write_text(json.dumps({"universe": "Asterion"}), encoding="utf8")
    (tmp_path / "constructions.json").write_text(json.dumps({"scope": "all", "queue": []}), encoding="utf8")
    first = _manager(settings, tmp_path)
    assert first.default_id == "asterion"
    asyncio.run(first.delete("asterion"))

    again = _manager(settings, tmp_path)  # redémarrage : les anciens fichiers à plat sont toujours là
    assert again.summary()["instances"] == []
    assert not (tmp_path / "instances" / "asterion").exists()


def test_logins_of_the_same_account_never_overlap(settings):
    from app.bot import BotSession

    async def scenario():
        host, account = BrowserHost(settings), Account(None)
        one, two = BotSession(settings, host, account), BotSession(settings, host, account)
        events = []
        for name, bot in (("a", one), ("b", two)):
            real = bot._do_login

            async def wrapped(identifier, password, real=real, name=name):
                events.append(name + "+")
                await asyncio.sleep(0.2)
                outcome = await real(identifier, password)
                events.append(name + "-")
                return outcome

            bot._do_login = wrapped
        try:
            await asyncio.gather(one.login("joueur.test@example.com", "good-password"),
                                 two.login("joueur.test@example.com", "good-password"))
        finally:
            await one.shutdown()
            await two.shutdown()
            await host.shutdown()
        return events

    events = asyncio.run(scenario())
    assert events in (["a+", "a-", "b+", "b-"], ["b+", "b-", "a+", "a-"])


def test_chromium_memory_prefers_the_proportional_share_when_available(tmp_path):
    from app.browser_host import chromium_memory_mb

    folder = tmp_path / "201"
    folder.mkdir()
    (folder / "cmdline").write_bytes(b"/usr/bin/chromium\0")
    (folder / "statm").write_text("1 99999 0 0 0 0 0")  # résident énorme (pages partagées comptées en entier)
    (folder / "smaps_rollup").write_text("Rss: 400000 kB\nPss:  102400 kB\nShared_Clean: 1 kB\n")
    assert chromium_memory_mb(str(tmp_path)) == 100.0
