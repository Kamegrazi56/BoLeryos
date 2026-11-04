"""Alerte d'attaque : lecture des capteurs (sans clic), repérage des attaques entrantes, alerte une seule fois."""

import json
import time
from dataclasses import replace
from functools import partial

import pytest
from fastapi.testclient import TestClient

from app.automation.attacks import badge_count, incoming_attacks, parse_movements
from app.main import create_app
from tests.mock_game import _movement
from tests.test_automation import _has, _logs, _runner, _until
from tests.test_notify import Receiver

ME = "KameGrazi"
SCEAU = ("5-32-Sceau", "I27 95 00", ME)
EGIDE = ("6-79-Egide", "M69 38 06", ME)
ENEMY = ("Gaia", "I17 99 83", "Hades33")


def _raw(*movements):
    return {"movements": list(movements), "badges": [{"cls": "game-utility-badge--fleet", "text": str(len(movements))}]}


# ------------------------------------------------------------------------------- sans navigateur
def test_only_attacks_coming_from_elsewhere_onto_my_planets_count():
    movements = parse_movements(_raw(
        _movement("Attaque", ENEMY, SCEAU),
        _movement("Espionnage", ENEMY, SCEAU),  # « attaques seulement » : l'espionnage ne déclenche rien
        _movement("Déploiement", EGIDE, SCEAU),  # mouvement entre mes planètes
        _movement("Attaque", SCEAU, ENEMY),  # mon attaque qui part
        _movement("Attaque groupée", ENEMY, EGIDE),
    ))
    found = incoming_attacks(movements, ["5-32-Sceau", "6-79-Egide"])
    assert [(m.type, m.dest_planet) for m in found] == [("Attaque", "5-32-Sceau"), ("Attaque groupée", "6-79-Egide")]
    assert found[0].describe() == "Attaque de Hades33 (Gaia I17 99 83) vers 5-32-Sceau (I27 95 00), arrivée 06:56:16 (dans 01:20:00)"


def test_without_my_planet_list_players_are_compared():
    movements = parse_movements(_raw(_movement("Attaque", ENEMY, SCEAU), _movement("Attaque", EGIDE, SCEAU)))
    assert [m.origin_player for m in incoming_attacks(movements, [])] == ["Hades33"]


def test_a_panel_present_twice_in_the_page_counts_once_and_badges_are_summed():
    attack = _movement("Attaque", ENEMY, SCEAU)
    raw = _raw(attack, attack)
    assert len(parse_movements(raw)) == 1
    assert badge_count({"badges": [{"text": "1"}, {"text": "2"}, {"text": ""}]}) == 3


# ------------------------------------------------------------------------------------- réglages
def test_attack_alert_settings(game_client):
    def put(**alert):
        return game_client.put("/api/automation/settings", json={"settings": {"attack_alert": alert}})

    assert game_client.get("/api/automation").json()["settings"]["attack_alert"] == {"enabled": False, "interval_seconds": 60.0}
    assert put(enabled=True, interval_seconds=30).json()["settings"]["attack_alert"] == {"enabled": True, "interval_seconds": 30.0}
    assert put(interval_seconds=0).status_code == 422
    assert put(interval_seconds=4000).status_code == 422
    assert put(interval_seconds="souvent").status_code == 422


# -------------------------------------------------------------------- avec le navigateur (faux jeu)
@pytest.fixture
def watch_client(settings, game):
    """Bot connecté au faux jeu, alertes autorisées vers un serveur local de test."""
    from app.bot import State

    with TestClient(create_app(replace(settings, login_url=game.url + "/game/asterion/buildings", allow_private_webhooks=True))) as c:
        bot = c.app.state.bot
        c.portal.call(bot.ensure_started)
        bot._set(State.LOGGED_IN, "test")
        bot.automation.store.add_catalog("planets", ["5-32-Sceau", "6-79-Egide", "8-50-Concorde"])
        yield c


def _enable(client, interval=1, **extra):
    body = {"attack_alert": {"enabled": True, "interval_seconds": interval}, "cycle": {"min_minutes": 60, "max_minutes": 60}, **extra}
    assert client.put("/api/automation/settings", json={"settings": body}).status_code == 200


def _reload(client):
    client.portal.call(client.app.state.bot._page.reload)


def test_an_incoming_attack_is_alerted_once_with_its_details(watch_client, game):
    receiver = Receiver()
    try:
        c = watch_client
        c.put("/api/notifications", json={"url": receiver.url, "format": "json", "events": {"attack": True, "launch": False, "attention": False}})
        _enable(c)
        c.post("/api/automation/start")
        assert _until(lambda: "capteurs lus" in c.get("/api/automation").json()["status"]["attack_watch"])
        assert c.get("/api/automation").json()["status"]["attacks"] == []  # le déploiement du joueur ne compte pas
        game.set_movements([_movement("Attaque", ENEMY, SCEAU, arrival="07:10:00", countdown="00:42:10")])
        _reload(c)
        assert _until(lambda: c.get("/api/automation").json()["status"]["attacks"])
        (attack,) = c.get("/api/automation").json()["status"]["attacks"]
        assert (attack["planet"], attack["attacker"], attack["arrival"]) == ("5-32-Sceau", "Hades33", "07:10:00")
        assert _until(lambda: receiver.messages)
        assert "ATTAQUE en approche : Attaque de Hades33 (Gaia I17 99 83) vers 5-32-Sceau (I27 95 00), arrivée 07:10:00" in json.loads(receiver.messages[0][1])["content"]
        time.sleep(3)  # plusieurs lectures : toujours une seule alerte
        assert len(receiver.messages) == 1 and sum("ATTAQUE en approche" in line for line in _logs(c)) == 1
        events = [e for e in c.get("/api/events").json() if e["kind"] == "attack"]
        assert len(events) == 1 and events[0]["level"] == "error"
        dash = c.get("/api/dashboard").json()
        assert dash["automation"]["attacks"][0]["text"].startswith("Attaque de Hades33")
        game.set_movements([])  # l'attaque est arrivée
        _reload(c)
        assert _until(lambda: c.get("/api/automation").json()["status"]["attacks"] == [])
        c.post("/api/automation/stop")
    finally:
        receiver.close()


def test_espionage_and_own_movements_do_not_alert(watch_client, game):
    c = watch_client
    game.set_movements([_movement("Espionnage", ENEMY, SCEAU), _movement("Déploiement", EGIDE, SCEAU), _movement("Attaque", SCEAU, ENEMY)])
    _reload(c)
    _enable(c)
    c.post("/api/automation/start")
    assert _until(lambda: "capteurs lus" in c.get("/api/automation").json()["status"]["attack_watch"])
    time.sleep(1.5)
    assert c.get("/api/automation").json()["status"]["attacks"] == [] and not _has(c, "ATTAQUE")
    c.post("/api/automation/stop")


def test_no_watch_when_the_bot_is_off_or_the_alert_disabled(watch_client, game):
    c = watch_client
    game.set_movements([_movement("Attaque", ENEMY, SCEAU)])
    _reload(c)
    _enable(c)
    time.sleep(2)  # OFF : pas de surveillance
    assert not _has(c, "ATTAQUE") and c.get("/api/automation").json()["status"]["attack_watch"] == "en pause (le bot est sur OFF)"
    c.put("/api/automation/settings", json={"settings": {"attack_alert": {"enabled": False}}})
    c.post("/api/automation/start")
    time.sleep(2)  # ON mais alerte désactivée
    assert not _has(c, "ATTAQUE") and c.get("/api/automation").json()["status"]["attack_watch"] == "désactivée"
    c.post("/api/automation/stop")


def test_the_watch_follows_the_players_interval(watch_client, game):
    c = watch_client
    _enable(c, interval=30)
    c.post("/api/automation/start")
    assert _until(lambda: "capteurs lus" in c.get("/api/automation").json()["status"]["attack_watch"])
    game.set_movements([_movement("Attaque", ENEMY, SCEAU)])
    _reload(c)
    time.sleep(3)  # prochaine lecture dans 30 s : rien encore
    assert not _has(c, "ATTAQUE")
    assert "toutes les 30 s" in c.get("/api/automation").json()["status"]["attack_watch"]
    c.portal.call(partial(_runner(c).check_attacks))  # lecture forcée : l'attaque est vue
    assert _has(c, "ATTAQUE en approche")
    c.post("/api/automation/stop")


def test_unreadable_sensors_are_reported(watch_client, game):
    c = watch_client
    raw = {"movements": [], "badges": [{"cls": "x", "text": "2"}]}
    assert badge_count(raw) == 2 and parse_movements(raw) == []
    runner = _runner(c)

    async def fake(_js):
        return raw

    page = c.app.state.bot._page
    original = page.evaluate
    page.evaluate = fake  # type: ignore[method-assign]
    try:
        c.portal.call(runner.check_attacks)
    finally:
        page.evaluate = original  # type: ignore[method-assign]
    assert _has(c, "les capteurs signalent des mouvements que le bot ne sait pas lire")
