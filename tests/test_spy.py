"""Campagne d'espionnage dans le faux jeu : Galaxie centrée sur la planète, zoom ×17,5, un drone par Errant, tri des rapports."""

import time

import pytest

from app.spy import ZOOM_LEVEL, read_amount

EGIDE = "6-79-Egide"
SETTINGS = {"planet": EGIDE, "minimums": {"Fer": 1_000_000, "Cristal": 500_000, "Éther": 1_000_000}, "required": 2}
CAMPAIGN_SECONDS = 90


def _scan(client):
    assert len(client.post("/api/planets/scan").json()["planets"]) == 3


def _start(client, **patch):
    assert client.put("/api/spying", json={**SETTINGS, **patch}).status_code == 200
    res = client.post("/api/spying/start")
    assert res.status_code == 200, res.text
    return res.json()


def _wait_end(client) -> dict:
    end = time.monotonic() + CAMPAIGN_SECONDS
    while time.monotonic() < end:
        status = client.get("/api/spying").json()["status"]
        if not status["running"]:
            return status
        time.sleep(0.3)
    raise AssertionError("la campagne d'espionnage ne se termine pas")


def _of(game, kind):
    return [a for a in game.actions() if a["kind"] == kind]


def _errant(n: int, **extra) -> dict:
    return {"player": f"Errant-{n}", "tag": "AI", "planet": f"Errant-{n} 1", "coords": f"M69 {n:02d} 10",
            "actions": ["Flotte", "Piliers", "Sonde", "Drone"], "spy": {"Fer": 10, "Cristal": 10, "Éther": 10}, **extra}


@pytest.mark.parametrize("raw,value", [("2 147 794", 2147794.0), ("2 147 794", 2147794.0), ("3,4 M", 3.4e6), ("?", None), (None, None)])
def test_report_amounts_are_read_like_the_rest_of_the_game(raw, value):
    assert read_amount(raw) == value


def test_the_campaign_spies_every_errant_and_sorts_the_reports(game_client, game):
    _scan(game_client)
    game_client.post("/api/bot/start")
    started = _start(game_client)
    assert started["status"]["running"] is True and started["bot_on"] is False  # les autres automatismes s'arrêtent
    status = _wait_end(game_client)
    assert status["message"] == "terminé", status
    # 4 Errants [AI] : Kairo (gardé), Bryn (piliers fermés), Lyra (supprimé), Torek (Drone grisé : Drone indisponible, ratio ×2)
    assert (status["total"], status["spied"], status["kept"], status["deleted"], status["out_of_range"]) == (4, 2, 1, 1, 1)
    assert status["closed"] == ["Bryn-Socle-176 3 (M69 37 24)"]
    assert [a["planet"] for a in _of(game, "drone")] == ["Kairo-Vigie-378 7", "Bryn-Socle-176 3", "Lyra-Voile-12 1"]
    assert [a["planet"] for a in _of(game, "spy_delete")] == ["Lyra-Voile-12 1"]  # seul le rapport trop pauvre est supprimé
    assert [a["level"] for a in _of(game, "zoom")] == [ZOOM_LEVEL]
    assert game_client.get("/api/spying").json()["bot_on"] is False  # le bot reste sur OFF après la campagne


def test_the_closed_pillars_list_starts_empty_at_each_launch(game_client, game):
    _scan(game_client)
    _start(game_client)
    assert _wait_end(game_client)["closed"] == ["Bryn-Socle-176 3 (M69 37 24)"]
    game.set_galaxy([_errant(20)])
    _start(game_client)
    assert _wait_end(game_client)["closed"] == []


def test_two_closed_errants_in_a_row_are_both_noted(game_client, game):
    # Vrai jeu : tant que le message « piliers désactivés » est affiché, un second refus n'ajoute pas de message
    _scan(game_client)
    game.set_galaxy([_errant(1, closed=True), _errant(2, closed=True), _errant(3)])
    _start(game_client, required=1, minimums={"Fer": 0, "Cristal": 0, "Éther": 0})
    status = _wait_end(game_client)
    assert status["closed"] == ["Errant-1 1 (M69 01 10)", "Errant-2 1 (M69 02 10)"]
    assert (status["errors"], status["kept"]) == (0, 1)


def test_the_whole_list_is_loaded_page_after_page(game_client, game):
    _scan(game_client)
    game.set_galaxy([_errant(n) for n in range(30)])  # 25 lignes, puis « Afficher la suite »
    _start(game_client, required=1, minimums={"Fer": 0, "Cristal": 0, "Éther": 0})
    status = _wait_end(game_client)
    assert status["total"] == 30 and status["kept"] == 30 and _of(game, "spy_delete") == []


def test_only_the_checked_factions_are_spied(game_client, game):
    _scan(game_client)
    game.set_galaxy([_errant(1, faction="noctes"), _errant(2, faction="veyrs"), _errant(3, faction="haldens"),
                     _errant(4, faction="veyrs"), _errant(5, faction=None)])  # 5 : avatar absent
    _start(game_client, required=1, minimums={"Fer": 0, "Cristal": 0, "Éther": 0}, factions=["noctes", "haldens"])
    status = _wait_end(game_client)
    assert status["message"] == "terminé", status
    assert [a["planet"] for a in _of(game, "drone")] == ["Errant-1 1", "Errant-3 1"]  # aucun clic sur les Veyrs ni l'inconnu
    assert status["skipped"] == {"noctes": 0, "veyrs": 2, "haldens": 0} and status["unknown_faction"] == 1
    logs = [e["msg"] for e in game_client.get("/api/status?limit=200").json()["logs"]]
    assert any("faction de Errant-5 1 (M69 05 10) illisible, Errant passé" in m for m in logs)
    assert any("écartés : 2 Veyrs, 1 de faction illisible" in m for m in logs)


def test_repeated_refusals_stop_the_campaign(game_client, game):
    _scan(game_client)
    game.set_galaxy([_errant(n, refuse="Impossible d'envoyer le drone : drones insuffisants.") for n in range(5)])
    _start(game_client)
    status = _wait_end(game_client)
    assert status["message"].startswith("interrompu") and "drones refusés" in status["message"]
    assert len(_of(game, "drone")) == 3 and status["errors"] == 3


def test_the_campaign_can_be_stopped(game_client, game):
    _scan(game_client)
    game.set_galaxy([_errant(n) for n in range(30)])
    _start(game_client)
    game_client.post("/api/spying/stop")
    status = _wait_end(game_client)
    assert "arrêté par l'utilisateur" in status["message"] and len(_of(game, "drone")) < 30


def test_a_second_launch_is_refused_while_one_runs(game_client, game):
    _scan(game_client)
    _start(game_client)
    again = game_client.post("/api/spying/start")
    assert again.status_code == 409 and "déjà" in again.json()["detail"]
    _wait_end(game_client)
