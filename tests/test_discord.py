"""Pilotage par Discord : réglages, commandes, client (contre un faux Discord local) et routes de l'interface."""

import asyncio
import json
import os
import stat
import threading
import time
from dataclasses import replace

import pytest
import uvicorn
from fastapi.testclient import TestClient

from app.account import Account
from app.browser_host import BrowserHost
from app.discord_commands import COMMAND_DEFINITIONS, CommandRunner
from app.discord_gateway import DiscordClient, parse_command
from app.discord_store import DiscordSettingsError, DiscordStore
from app.instance_manager import InstanceManager
from app.main import create_app
from tests.conftest import _free_port
from tests.mock_discord import GUILD_ID, OWNER_ID, DiscordState, create_mock_discord

TOKEN = "A" * 24 + "." + "b" * 6 + "." + "c" * 27
OWNER, GUILD, CHANNEL = OWNER_ID, GUILD_ID, "444444444444444444"


def _store(tmp_path, **extra) -> DiscordStore:
    store = DiscordStore(str(tmp_path))
    store.update({"token": TOKEN, "enabled": True, **extra})
    return store


def _manager(settings, tmp_path) -> InstanceManager:
    (tmp_path / "preferences.json").write_text(json.dumps({"universe": "Asterion"}), encoding="utf8")
    cfg = replace(settings, data_dir=str(tmp_path))
    return InstanceManager(cfg, BrowserHost(cfg), Account(cfg.data_dir))


# ------------------------------------------------------------------ réglages
def test_settings_never_give_the_token_back(tmp_path):
    store = _store(tmp_path)
    shown = store.get()
    assert TOKEN not in json.dumps(shown) and shown["has_token"] and shown["token_hint"] == TOKEN[-4:]
    assert stat.S_IMODE(os.stat(tmp_path / "discord.json").st_mode) == 0o600
    assert DiscordStore(str(tmp_path)).token() == TOKEN  # relu après un redémarrage


def test_settings_refuse_bad_values_and_incomplete_activation(tmp_path):
    store = DiscordStore(str(tmp_path))
    with pytest.raises(DiscordSettingsError) as err:
        store.update({"token": "trop court"})
    assert len(err.value.errors) == 1
    with pytest.raises(DiscordSettingsError) as err:
        store.update({"enabled": True})
    assert any("Jeton" in e for e in err.value.errors)
    assert not store.get()["enabled"] and not store.ready()


def test_the_token_is_the_only_required_setting_and_old_numbers_are_dropped(tmp_path):
    (tmp_path / "discord.json").write_text(json.dumps({"enabled": True, "token": TOKEN, "owner_id": OWNER, "guild_id": GUILD}), encoding="utf8")
    store = DiscordStore(str(tmp_path))
    assert store.ready() and {"owner_id", "guild_id", "channel_id"}.isdisjoint(store.get())


def test_an_empty_token_keeps_the_current_one_and_forgetting_it_turns_discord_off(tmp_path):
    store = _store(tmp_path)
    store.update({"token": "", "confirm": False})
    assert store.token() == TOKEN and not store.get()["confirm"]
    after = store.forget_token()
    assert not after["has_token"] and not after["enabled"] and not store.ready()


# ------------------------------------------------------------------ commandes
def _runner(settings, tmp_path, confirm=True):
    manager = _manager(settings, tmp_path)
    return manager, CommandRunner(manager, lambda: confirm)


def test_command_definitions_follow_discord_limits():
    for command in COMMAND_DEFINITIONS:
        assert command["name"].isascii() and command["name"].islower() and len(command["description"]) <= 100
        for sub in command["options"]:
            assert sub["name"].isascii() and sub["name"].islower() and len(sub["description"]) <= 100


def test_parse_command_reads_subcommand_and_values():
    data = {"name": "fonction", "options": [{"type": 1, "name": "activer", "options": [{"type": 3, "name": "fonction", "value": "fleets"}]}]}
    assert parse_command(data) == (("fonction", "activer"), {"fonction": "fleets"})
    assert parse_command({"name": "journal", "options": [{"type": 3, "name": "instance", "value": "x"}]}) == (("journal", ""), {"instance": "x"})


def test_status_and_list_name_every_instance(settings, tmp_path):
    manager, runner = _runner(settings, tmp_path)
    manager.create("Orion")
    reply = asyncio.run(runner.execute(("bot", "statut"), {}))
    assert "Asterion" in reply.text and "Orion" in reply.text and "arrêtée" in reply.text
    assert "Orion" in asyncio.run(runner.execute(("instance", "liste"), {})).text


def test_bot_on_and_off_for_one_instance_without_confirmation(settings, tmp_path):
    manager, runner = _runner(settings, tmp_path)
    bot = manager.resolve()
    stopped = []
    bot.stop_with_missions = lambda: stopped.append(1)
    asyncio.run(runner.execute(("bot", "demarrer"), {"instance": "asterion"}))
    assert bot.scheduler.on
    reply = asyncio.run(runner.execute(("bot", "arreter"), {"instance": "asterion"}))
    assert stopped == [1] and reply.confirm is None
    assert any("Discord" in e["msg"] for e in bot.events(5))  # l'ordre laisse une trace dans le journal de l'instance


def test_stopping_everything_asks_for_confirmation_unless_disabled(settings, tmp_path):
    manager, runner = _runner(settings, tmp_path)
    bot = manager.resolve()
    stopped = []
    bot.stop_with_missions = lambda: stopped.append(1)
    ask = asyncio.run(runner.execute(("bot", "arreter"), {}))
    assert ask.confirm == "bot:arreter:" and stopped == []
    asyncio.run(runner.execute(("bot", "arreter"), {}, confirmed=True))
    assert stopped == [1]


def test_confirmation_can_be_turned_off(settings, tmp_path):
    manager, runner = _runner(settings, tmp_path, confirm=False)
    stopped = []
    manager.resolve().stop_with_missions = lambda: stopped.append(1)
    assert asyncio.run(runner.execute(("bot", "arreter"), {})).confirm is None and stopped == [1]


def test_instance_stop_needs_confirmation_then_stops(settings, tmp_path, monkeypatch):
    manager, runner = _runner(settings, tmp_path)
    ask = asyncio.run(runner.execute(("instance", "arreter"), {"instance": "Asterion"}))
    assert ask.confirm == "instance:arreter:asterion" and manager.summary()["instances"][0]["running"]
    done = asyncio.run(runner.execute(("instance", "arreter"), {"instance": "asterion"}, confirmed=True))
    assert "arrêtée" in done.text and not manager.summary()["instances"][0]["running"]


def test_feature_switch_targets_one_or_all_running_instances(settings, tmp_path):
    manager, runner = _runner(settings, tmp_path)
    bot = manager.resolve()
    reply = asyncio.run(runner.execute(("fonction", "activer"), {"fonction": "Retours de flottes"}))
    assert "activée" in reply.text and bot.scheduler.store.get("fleets")["enabled"]
    asyncio.run(runner.execute(("fonction", "desactiver"), {"fonction": "fleets", "instance": "asterion"}))
    assert not bot.scheduler.store.get("fleets")["enabled"]
    assert "inconnue" in asyncio.run(runner.execute(("fonction", "activer"), {"fonction": "nimporte"})).text


def test_unknown_stopped_and_ambiguous_instances_are_explained(settings, tmp_path):
    manager, runner = _runner(settings, tmp_path)
    manager.create("Orion")
    manager.create("Orionis")
    assert "inconnue" in asyncio.run(runner.execute(("instance", "statut"), {"instance": "zzz"})).text
    assert "Plusieurs" in asyncio.run(runner.execute(("instance", "statut"), {"instance": "orio"})).text
    assert "arrêtée" in asyncio.run(runner.execute(("bot", "demarrer"), {"instance": "Orion"})).text


def test_journal_lists_recent_events(settings, tmp_path):
    manager, runner = _runner(settings, tmp_path)
    manager.resolve().log("Un fait marquant.", event=True)
    assert "Un fait marquant." in asyncio.run(runner.execute(("journal", ""), {})).text


def test_suggestions_filter_instances_and_features(settings, tmp_path):
    manager, runner = _runner(settings, tmp_path)
    assert [c["value"] for c in runner.suggestions("instance", "aster")] == ["asterion"]
    assert any(c["value"] == "fleets" for c in runner.suggestions("fonction", "flotte"))
    assert runner.suggestions("autre", "") == []


# ------------------------------------------------------------------ client contre un faux Discord
@pytest.fixture
def fake_discord():
    state = DiscordState(port=_free_port())
    server = uvicorn.Server(uvicorn.Config(create_mock_discord(state), host="127.0.0.1", port=state.port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield state
    server.should_exit = True
    thread.join(timeout=5)


def _interaction(kind, data, user=OWNER, guild=GUILD, n=[0]):
    n[0] += 1
    return {"type": kind, "id": f"i{n[0]}", "token": f"tok{n[0]}", "guild_id": guild, "channel_id": CHANNEL,
            "member": {"user": {"id": user}}, "data": data}


def _command(name, sub="", **values):
    options = [{"type": 3, "name": k, "value": v} for k, v in values.items()]
    return _interaction(2, {"name": name, "options": [{"type": 1, "name": sub, "options": options}] if sub else options})


async def _wait(condition, timeout=8.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "délai dépassé"
        await asyncio.sleep(0.02)


def _drive(settings, tmp_path, state, steps, **store_extra):
    """Lance le client, attend la connexion, déroule `steps(manager, client)` puis coupe."""
    store, manager = _store(tmp_path, **store_extra), _manager(settings, tmp_path)
    runner = CommandRunner(manager, lambda: store.get()["confirm"])

    async def main():
        client = DiscordClient(store, runner, api=f"http://127.0.0.1:{state.port}")
        task = asyncio.create_task(client.run())
        try:
            await _wait(lambda: client.state in ("connected", "error"))
            await steps(manager, client)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return client

    return asyncio.run(main())


def test_client_registers_commands_on_every_invited_server_and_answers(settings, tmp_path, fake_discord):
    async def steps(manager, client):
        assert client.state == "connected"
        fake_discord.outbox.append(_command("bot", "statut"))
        await _wait(lambda: fake_discord.edits)

    _drive(settings, tmp_path, fake_discord, steps)
    assert {c["name"] for c in fake_discord.registered} == {"bot", "instance", "fonction", "journal", "prochain", "flottes", "planetes", "pause", "connexion", "scan"}
    assert fake_discord.registered_on == [GUILD]
    assert fake_discord.identifies[0]["d"]["intents"] == 1 and fake_discord.identifies[0]["d"]["token"] == TOKEN
    assert fake_discord.callbacks[0]["body"] == {"type": 5, "data": {"flags": 64}}  # « en cours », visible de lui seul
    assert "Asterion" in fake_discord.edits[0]["body"]["content"]


def test_client_ignores_other_people_and_private_messages(settings, tmp_path, fake_discord):
    async def steps(manager, client):
        bot = manager.resolve()
        fake_discord.outbox.append(_command("bot", "demarrer") | {"member": {"user": {"id": "999999999999999999"}}})
        fake_discord.outbox.append(_interaction(2, {"name": "bot", "options": [{"type": 1, "name": "demarrer"}]}, guild=None))
        await _wait(lambda: len(fake_discord.callbacks) == 2)
        assert not bot.scheduler.on and not fake_discord.edits
        assert all(c["body"]["type"] == 4 and "réservée" in c["body"]["data"]["content"] for c in fake_discord.callbacks)

    _drive(settings, tmp_path, fake_discord, steps)


def test_client_accepts_every_member_of_the_owning_team(settings, tmp_path, fake_discord):
    fake_discord.team_members = ["777777777777777777", OWNER]

    async def steps(manager, client):
        fake_discord.outbox.append(_command("bot", "statut") | {"member": {"user": {"id": "777777777777777777"}}})
        await _wait(lambda: fake_discord.edits)

    _drive(settings, tmp_path, fake_discord, steps)


def test_client_installs_commands_on_a_server_joined_later(settings, tmp_path, fake_discord):
    async def steps(manager, client):
        fake_discord.guild_events.append("666666666666666666")
        await _wait(lambda: len(fake_discord.registered_on) == 2)

    _drive(settings, tmp_path, fake_discord, steps)
    assert fake_discord.registered_on == [GUILD, "666666666666666666"]


def test_client_says_so_when_the_bot_is_invited_nowhere_or_lacks_the_scope(settings, tmp_path, fake_discord):
    fake_discord.guilds = []

    async def nowhere(manager, client):
        assert client.state == "connected" and "aucun serveur" in client.error

    _drive(settings, tmp_path, fake_discord, nowhere)
    fake_discord.guilds, fake_discord.refused_guilds = [GUILD], {GUILD}

    async def refused(manager, client):
        assert client.state == "connected" and "applications.commands" in client.error, client.error

    _drive(settings, tmp_path, fake_discord, refused)


def test_client_confirmation_buttons_then_action(settings, tmp_path, fake_discord):
    async def steps(manager, client):
        bot = manager.resolve()
        stopped = []
        bot.stop_with_missions = lambda: stopped.append(1)
        fake_discord.outbox.append(_command("bot", "arreter"))
        await _wait(lambda: fake_discord.edits)
        buttons = fake_discord.edits[0]["body"]["components"][0]["components"]
        assert [b["label"] for b in buttons] == ["Confirmer", "Annuler"] and not stopped
        fake_discord.outbox.append(_interaction(3, {"custom_id": "no"}))  # annuler : rien ne change
        await _wait(lambda: len(fake_discord.edits) == 2)
        assert fake_discord.edits[1]["body"]["content"] == "Annulé." and not stopped
        fake_discord.outbox.append(_interaction(3, {"custom_id": buttons[0]["custom_id"]}))
        await _wait(lambda: len(fake_discord.edits) == 3)
        assert stopped == [1]

    _drive(settings, tmp_path, fake_discord, steps)


def test_client_suggests_instance_names_while_typing(settings, tmp_path, fake_discord):
    async def steps(manager, client):
        data = {"name": "instance", "options": [{"type": 1, "name": "statut", "options": [{"type": 3, "name": "instance", "value": "ast", "focused": True}]}]}
        fake_discord.outbox.append(_interaction(4, data))
        await _wait(lambda: fake_discord.callbacks)

    _drive(settings, tmp_path, fake_discord, steps)
    body = fake_discord.callbacks[0]["body"]
    assert body["type"] == 8 and body["data"]["choices"][0]["value"] == "asterion"


def test_client_stops_with_a_clear_message_when_the_token_is_refused(settings, tmp_path, fake_discord):
    fake_discord.reject_token = True

    async def steps(manager, client):
        assert client.state == "error" and "Jeton refusé" in client.error

    _drive(settings, tmp_path, fake_discord, steps)


def test_client_stops_when_discord_closes_the_gateway_with_an_auth_error(settings, tmp_path, fake_discord):
    fake_discord.close_code = 4004

    async def steps(manager, client):
        await _wait(lambda: client.state == "error")
        assert "Jeton refusé" in client.error

    _drive(settings, tmp_path, fake_discord, steps)


# ------------------------------------------------------------------ routes de l'interface
@pytest.fixture
def web(settings, tmp_path, monkeypatch):
    started = []

    async def fake_run(self):  # aucun accès à internet dans les tests
        started.append(self)
        await asyncio.sleep(3600)

    monkeypatch.setattr(DiscordClient, "run", fake_run)
    monkeypatch.setattr(InstanceManager, "_kick", staticmethod(lambda bot: None))
    with TestClient(create_app(replace(settings, data_dir=str(tmp_path)))) as client:
        client.started = started
        yield client


def test_routes_save_settings_hide_the_token_and_start_the_client(web, tmp_path):
    body = {"token": TOKEN, "enabled": True}
    saved = web.put("/api/discord", json=body)
    assert saved.status_code == 200 and TOKEN not in saved.text and saved.json()["settings"]["has_token"]
    assert len(web.started) == 1
    assert TOKEN not in web.get("/api/discord").text
    forgotten = web.delete("/api/discord/token")
    assert not forgotten.json()["settings"]["has_token"] and forgotten.json()["status"]["state"] == "off"


def test_routes_refuse_a_foreign_origin_and_bad_values(web):
    assert web.put("/api/discord", json={"confirm": False}, headers={"Origin": "http://evil.example"}).status_code == 403
    refused = web.put("/api/discord", json={"token": "abc"})
    assert refused.status_code == 422 and "jeton" in refused.json()["detail"][0].lower()
    assert web.put("/api/discord", json={"enabled": True}).status_code == 422
