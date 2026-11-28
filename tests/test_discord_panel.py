"""Panneau Discord (GRAZI-60) : réglage du salon, publication unique et persistante, clics (lecture, action, confirmation),
refus d'un autre utilisateur ou d'un autre salon. Tout contre le faux Discord local : pas le vrai Discord."""

import asyncio
import json

import pytest

from app.discord_panel import ALL, Panel
from app.discord_store import DiscordSettingsError, DiscordStore
from tests.test_discord import (  # noqa: F401 (fixture)
    OWNER, TOKEN, _drive, _interaction, _runner, _store, _wait, fake_discord, web)

PANEL = "555555555555555555"  # salon du panneau
OTHER = "666666666666666666"
STRANGER = "777777777777777777"


# ------------------------------------------------------------------ réglage du salon
def test_panel_channel_must_be_a_discord_id_and_a_new_channel_forgets_the_old_message(tmp_path):
    store = _store(tmp_path, panel_channel=PANEL)
    store.set_panel_message("123456789012345678")
    assert store.panel() == (PANEL, "123456789012345678") and store.get()["panel_channel"] == PANEL
    assert DiscordStore(str(tmp_path)).panel() == (PANEL, "123456789012345678")  # relu après un redémarrage
    with pytest.raises(DiscordSettingsError):
        store.update({"panel_channel": "pas un identifiant"})
    store.update({"token": "", "confirm": False})  # un autre réglage ne touche pas au panneau
    assert store.panel()[1] == "123456789012345678"
    store.update({"panel_channel": OTHER})
    assert store.panel() == (OTHER, "")
    store.update({"panel_channel": ""})
    assert store.panel() == ("", "")


def test_a_message_without_a_channel_in_the_file_is_dropped(tmp_path):
    (tmp_path / "discord.json").write_text(json.dumps({"enabled": True, "token": TOKEN, "panel_message": "123456789012345678"}), encoding="utf8")
    assert DiscordStore(str(tmp_path)).panel() == ("", "")


# ------------------------------------------------------------------ dessin
def test_panel_respects_discord_limits_and_marks_the_choices(settings, tmp_path):
    manager, runner = _runner(settings, tmp_path)
    manager.create("Orion")
    body = Panel(runner).message("orion", "fleets")
    rows = body["components"]
    assert len(rows) <= 5 and len(body["content"]) < 2000
    for row in rows:
        kinds = {c["type"] for c in row["components"]}
        assert (kinds == {2} and len(row["components"]) <= 5) or (kinds == {3} and len(row["components"]) == 1)
        for comp in row["components"]:
            assert len(comp["custom_id"]) <= 100 and comp["custom_id"].startswith("pn:")
            assert len(comp.get("label", "")) <= 80 and len(comp.get("options", [])) <= 25
    instance_menu, feature_menu = rows[0]["components"][0], rows[3]["components"][0]
    assert [o["value"] for o in instance_menu["options"] if o["default"]] == ["orion"]
    assert [o["value"] for o in feature_menu["options"] if o["default"]] == ["fleets"]
    assert Panel(runner).message("disparue")["components"][0]["components"][0]["options"][0]["default"]  # retour à « Toutes »


# ------------------------------------------------------------------ publication
def _panel_messages(state):
    return state.channel_messages.get(PANEL, [])


def test_panel_is_posted_once_and_edited_in_place_after_a_restart(settings, tmp_path, fake_discord):
    async def steps(manager, client):
        await _wait(lambda: _panel_messages(fake_discord))

    _drive(settings, tmp_path, fake_discord, steps, panel_channel=PANEL)
    first = _panel_messages(fake_discord)[0]
    assert len(_panel_messages(fake_discord)) == 1 and DiscordStore(str(tmp_path)).panel() == (PANEL, first["id"])

    async def again(manager, client):
        await _wait(lambda: DiscordStore(str(tmp_path)).panel()[1] and client.status()["error"] == "")
        await asyncio.sleep(0.3)

    _drive(settings, tmp_path, fake_discord, again, panel_channel=PANEL)
    assert len(_panel_messages(fake_discord)) == 1  # redémarrage : le même message est remis à jour, pas republié


def test_a_panel_already_in_the_channel_is_adopted_when_the_saved_id_is_lost(settings, tmp_path, fake_discord):
    fake_discord.channel_messages[PANEL] = [{"id": "900000000000000001", "author": {"id": "111111111111111111"}, "content": "x", "components": []}]

    async def steps(manager, client):
        await _wait(lambda: _panel_messages(fake_discord) and any(m.get("components") for m in _panel_messages(fake_discord)))

    _drive(settings, tmp_path, fake_discord, steps, panel_channel=PANEL)
    first = _panel_messages(fake_discord)
    assert len(first) == 2 and first[1]["components"]  # le message d'un autre auteur reste, le panneau est publié une fois
    store = DiscordStore(str(tmp_path))
    store.update({"panel_channel": ""})
    store.update({"panel_channel": PANEL})  # identifiant du message perdu

    async def lost(manager, client):
        await asyncio.sleep(0.6)

    _drive(settings, tmp_path, fake_discord, lost, panel_channel=PANEL)
    assert len(_panel_messages(fake_discord)) == 2  # le panneau déjà présent est repris, pas doublé
    assert DiscordStore(str(tmp_path)).panel()[1] == first[1]["id"]


def test_a_deleted_panel_is_posted_again(settings, tmp_path, fake_discord):
    async def steps(manager, client):
        await _wait(lambda: _panel_messages(fake_discord))

    _drive(settings, tmp_path, fake_discord, steps, panel_channel=PANEL)
    fake_discord.channel_messages[PANEL].clear()  # le joueur a supprimé le message
    _drive(settings, tmp_path, fake_discord, steps, panel_channel=PANEL)
    assert len(_panel_messages(fake_discord)) == 1


def test_missing_rights_are_reported_without_cutting_the_connection(settings, tmp_path, fake_discord):
    fake_discord.refused_channels.add(PANEL)
    seen = {}

    async def steps(manager, client):
        await _wait(lambda: "Panneau non publié" in client.status()["error"])
        seen["state"] = client.status()["state"]

    _drive(settings, tmp_path, fake_discord, steps, panel_channel=PANEL)
    assert seen["state"] == "connected" and not _panel_messages(fake_discord)


def test_no_channel_means_no_panel(settings, tmp_path, fake_discord):
    async def steps(manager, client):
        await asyncio.sleep(0.4)

    _drive(settings, tmp_path, fake_discord, steps)
    assert not fake_discord.channel_messages


# ------------------------------------------------------------------ clics
def _click(custom_id, components=None, *, channel=PANEL, user=OWNER, values=None, private=False):
    data = {"custom_id": custom_id, "component_type": 3 if values is not None else 2}
    if values is not None:
        data["values"] = values
    event = _interaction(3, data, user=user)
    event["channel_id"] = channel
    event["message"] = {"flags": 64 if private else 0, "components": components or []}
    return event


def _panel_of(client, instance=ALL, feature=""):
    return client._panel.message(instance, feature)["components"]


def _only(state, kind):
    return [c for c in state.callbacks if c["body"]["type"] == kind]


def test_choosing_an_instance_redraws_the_panel_with_the_choice_inscribed(settings, tmp_path, fake_discord):
    async def steps(manager, client):
        fake_discord.outbox.append(_click("pn:inst", _panel_of(client), values=["asterion"]))
        await _wait(lambda: _only(fake_discord, 7))

    _drive(settings, tmp_path, fake_discord, steps, panel_channel=PANEL)
    update = _only(fake_discord, 7)[0]["body"]["data"]
    assert [o["value"] for o in update["components"][0]["components"][0]["options"] if o["default"]] == ["asterion"]
    assert not fake_discord.edits  # simple redessin : aucune commande lancée


def test_read_buttons_answer_privately_for_the_chosen_instance(settings, tmp_path, fake_discord):
    async def steps(manager, client):
        fake_discord.outbox.append(_click("pn:do:nx", _panel_of(client, "asterion")))
        await _wait(lambda: fake_discord.edits)
        fake_discord.outbox.append(_click("pn:do:fl", _panel_of(client)))
        await _wait(lambda: len(fake_discord.edits) == 2)

    _drive(settings, tmp_path, fake_discord, steps, panel_channel=PANEL)
    assert all(c["body"]["data"]["flags"] == 64 for c in _only(fake_discord, 5))  # réponse visible de lui seul
    assert "Asterion" in fake_discord.edits[0]["body"]["content"]
    refresh = fake_discord.edits[1]["body"]["components"][0]["components"][0]
    assert refresh["label"] == "Actualiser dans le jeu" and refresh["custom_id"] == "pn:go:fl:*"


def test_refresh_button_replaces_the_private_answer_and_asks_for_a_new_reading(settings, tmp_path, fake_discord):
    async def steps(manager, client):
        scanned = []

        async def fake_scan(source):
            scanned.append(source)
            return {}

        manager.resolve().fleet.scan = fake_scan
        fake_discord.outbox.append(_click("pn:go:fl:asterion", private=True))
        await _wait(lambda: fake_discord.edits)
        assert scanned == ["Discord"]

    _drive(settings, tmp_path, fake_discord, steps, panel_channel=PANEL)
    assert _only(fake_discord, 6) and not _only(fake_discord, 5)  # la réponse privée est remplacée, pas dupliquée


def test_action_buttons_run_the_command_and_leave_a_trace(settings, tmp_path, fake_discord):
    async def steps(manager, client):
        fake_discord.outbox.append(_click("pn:do:on", _panel_of(client, "asterion")))
        await _wait(lambda: fake_discord.edits)
        assert manager.resolve().scheduler.on
        fake_discord.outbox.append(_click("pn:do:off", _panel_of(client, "asterion")))
        await _wait(lambda: len(fake_discord.edits) == 2)
        assert not manager.resolve().scheduler.on
        assert any("Discord" in e["msg"] for e in manager.resolve().events(5))

    _drive(settings, tmp_path, fake_discord, steps, panel_channel=PANEL)
    assert "Bot sur ON" in fake_discord.edits[0]["body"]["content"]


def test_stopping_everything_from_the_panel_still_asks_for_confirmation(settings, tmp_path, fake_discord):
    async def steps(manager, client):
        manager.resolve().scheduler.start()
        fake_discord.outbox.append(_click("pn:do:off", _panel_of(client)))
        await _wait(lambda: fake_discord.edits)
        assert manager.resolve().scheduler.on  # rien n'est arrêté avant le clic sur Confirmer
        buttons = fake_discord.edits[0]["body"]["components"][0]["components"]
        assert [b["label"] for b in buttons] == ["Confirmer", "Annuler"]
        fake_discord.outbox.append(_click(buttons[0]["custom_id"], private=True))
        await _wait(lambda: len(fake_discord.edits) == 2)
        assert not manager.resolve().scheduler.on

    _drive(settings, tmp_path, fake_discord, steps, panel_channel=PANEL)


def test_feature_buttons_need_a_feature_then_act_on_the_chosen_one(settings, tmp_path, fake_discord):
    async def steps(manager, client):
        fake_discord.outbox.append(_click("pn:do:fa", _panel_of(client, "asterion")))
        await _wait(lambda: fake_discord.edits)
        assert "Choisissez d'abord une fonctionnalité" in fake_discord.edits[0]["body"]["content"]
        fake_discord.outbox.append(_click("pn:do:fa", _panel_of(client, "asterion", "fleets")))
        await _wait(lambda: len(fake_discord.edits) == 2)
        assert manager.resolve().scheduler.store.get("fleets")["enabled"]
        fake_discord.outbox.append(_click("pn:do:fd", _panel_of(client, "asterion", "fleets")))
        await _wait(lambda: len(fake_discord.edits) == 3)
        assert not manager.resolve().scheduler.store.get("fleets")["enabled"]

    _drive(settings, tmp_path, fake_discord, steps, panel_channel=PANEL)


def test_pause_offers_durations_then_starts_the_pause(settings, tmp_path, fake_discord):
    async def steps(manager, client):
        manager.resolve().scheduler.start()
        fake_discord.outbox.append(_click("pn:do:pz", _panel_of(client, "asterion")))
        await _wait(lambda: fake_discord.edits)
        menu = fake_discord.edits[0]["body"]["components"][0]["components"][0]
        assert menu["custom_id"] == "pn:pz:asterion" and "30" in [o["value"] for o in menu["options"]]
        fake_discord.outbox.append(_click(menu["custom_id"], values=["30"], private=True))
        await _wait(lambda: len(fake_discord.edits) == 2)
        assert not manager.resolve().scheduler.on  # en pause : sur OFF le temps de la pause
        client._runner._pauses.cancel("asterion")

    _drive(settings, tmp_path, fake_discord, steps, panel_channel=PANEL)
    assert "repasse sur ON" in fake_discord.edits[1]["body"]["content"]


def test_status_of_one_instance_offers_to_start_or_stop_its_session(settings, tmp_path, fake_discord):
    async def steps(manager, client):
        fake_discord.outbox.append(_click("pn:do:st", _panel_of(client, "asterion")))
        await _wait(lambda: fake_discord.edits)
        buttons = fake_discord.edits[0]["body"]["components"][0]["components"]
        assert [b["custom_id"] for b in buttons] == ["pn:ss:go:asterion", "pn:ss:no:asterion"]
        fake_discord.outbox.append(_click("pn:ss:no:asterion", private=True))
        await _wait(lambda: len(fake_discord.edits) == 2)
        assert fake_discord.edits[1]["body"]["components"][0]["components"][0]["label"] == "Confirmer"  # l'arrêt se confirme
        assert manager.summary()["instances"][0]["running"]

    _drive(settings, tmp_path, fake_discord, steps, panel_channel=PANEL)


def test_stale_instance_is_explained_and_nothing_runs(settings, tmp_path, fake_discord):
    async def steps(manager, client):
        stale = _panel_of(client, "asterion")
        stale[0]["components"][0]["options"][1]["value"] = "supprimee"  # menu resté sur une instance supprimée
        fake_discord.outbox.append(_click("pn:do:on", stale))
        await _wait(lambda: len(fake_discord.edits) == 1)

    _drive(settings, tmp_path, fake_discord, steps, panel_channel=PANEL)
    assert "Actualiser le panneau" in fake_discord.edits[0]["body"]["content"]


# ------------------------------------------------------------------ refus
def test_another_user_is_refused_on_every_panel_control(settings, tmp_path, fake_discord):
    async def steps(manager, client):
        for custom_id, values in (("pn:do:on", None), ("pn:inst", ["asterion"]), ("pn:sync", None), ("pn:go:fl:*", None)):
            fake_discord.outbox.append(_click(custom_id, _panel_of(client), user=STRANGER, values=values))
        await _wait(lambda: len(_only(fake_discord, 4)) == 4)
        assert not manager.resolve().scheduler.on

    _drive(settings, tmp_path, fake_discord, steps, panel_channel=PANEL)
    assert all("réservée" in c["body"]["data"]["content"] for c in _only(fake_discord, 4))
    assert not fake_discord.edits and not _only(fake_discord, 7)


def test_clicks_outside_the_panel_channel_are_refused(settings, tmp_path, fake_discord):
    async def steps(manager, client):
        fake_discord.outbox.append(_click("pn:do:on", _panel_of(client), channel=OTHER))
        fake_discord.outbox.append(_click("pn:inst", _panel_of(client), channel=OTHER, values=["asterion"]))
        await _wait(lambda: len(_only(fake_discord, 4)) == 2)
        assert not manager.resolve().scheduler.on

    _drive(settings, tmp_path, fake_discord, steps, panel_channel=PANEL)
    assert all("son salon" in c["body"]["data"]["content"] for c in _only(fake_discord, 4))
    assert not fake_discord.edits and not _only(fake_discord, 7)


def test_without_a_panel_channel_every_panel_click_is_refused(settings, tmp_path, fake_discord):
    async def steps(manager, client):
        fake_discord.outbox.append(_click("pn:do:on", _panel_of(client)))
        await _wait(lambda: _only(fake_discord, 4))

    _drive(settings, tmp_path, fake_discord, steps)
    assert not fake_discord.edits


def test_slash_commands_and_their_confirmation_keep_working_in_other_channels(settings, tmp_path, fake_discord):
    from tests.test_discord import _command

    async def steps(manager, client):
        fake_discord.outbox.append(_command("bot", "statut"))
        await _wait(lambda: fake_discord.edits)

    _drive(settings, tmp_path, fake_discord, steps, panel_channel=PANEL)
    assert "Asterion" in fake_discord.edits[0]["body"]["content"]


def test_the_panel_channel_is_part_of_the_settings_api(web):
    assert web.get("/api/discord").json()["settings"]["panel_channel"] == ""
    assert web.put("/api/discord", json={"panel_channel": "abc"}).status_code == 422
    saved = web.put("/api/discord", json={"token": TOKEN, "enabled": True, "panel_channel": PANEL})
    assert saved.status_code == 200 and saved.json()["settings"]["panel_channel"] == PANEL
