"""Retours de flottes dans le faux jeu : relevé des Capteurs (lecture seule), lecture de la durée de retour, attente,
clic de confirmation à l'heure voulue, échecs expliqués. Le faux jeu reproduit la structure connue des Capteurs ; la
fenêtre de retour est SUPPOSÉE (non relevée sur le vrai jeu)."""

import time
from datetime import datetime

import pytest

from app.planning import TIMEZONE


def _bot(client):
    return client.app.state.bot


def _at(ts: float) -> str:
    return datetime.fromtimestamp(ts, TIMEZONE).strftime("%Y-%m-%dT%H:%M:%S")


def _move(kind="Déploiement", flown=100.0, left=500.0, origin=("6-79-Egide", "M69 38 06"), destination=("8-50-Concorde", "Z25 95 16")):
    now = time.time()
    return {"type": kind, "arrival": "06:56:16", "countdown": "", "units": "476051 U", "start": now - flown, "end": now + left,
            "origin": {"planet": origin[0], "coords": origin[1], "player": "KameGrazi"},
            "destination": {"planet": destination[0], "coords": destination[1], "player": "KameGrazi"}}


def _scan(client) -> dict:
    if not _bot(client).supervisor.scan_summary()["planets"]:
        assert len(client.post("/api/planets/scan").json()["planets"]) == 3  # le bot doit avoir vu la partie
    return client.post("/api/fleets/scan").json()


def _program(client, target_in: float) -> dict:
    fleet = next(f for f in client.get("/api/fleets").json()["fleets"] if f["can_recall"])
    res = client.post("/api/fleets/recalls", json={"fleet_id": fleet["id"], "at": _at(time.time() + target_in)})
    assert res.status_code == 200, res.text
    return res.json()["recalls"][-1]


def _run(client) -> str:
    return client.portal.call(_bot(client).fleet.run_pass)


def _recalls(game):
    return [a for a in game.actions() if a["kind"] == "recall"]


def _state(client, recall_id: str) -> dict:
    return next(r for r in _bot(client).fleets.recalls() if r["id"] == recall_id)


def test_the_flight_list_is_read_without_touching_the_game(game_client, game):
    game.set_movements([_move(), _move("Exploration", destination=("Brèche", "M79 50 37"))])
    data = _scan(game_client)
    assert data["error"] == "" and [f["kind"] for f in data["fleets"]] == ["Déploiement"] and data["excluded"] == 1
    fleet = data["fleets"][0]
    assert fleet["origin"]["coords"] == "M69 38 06" and fleet["destination"]["coords"] == "Z25 95 16" and fleet["can_recall"] is True
    assert fleet["units"] == "476051 U" and 495 <= fleet["left"] <= 505
    assert game.actions() == []  # lecture seule : aucun rappel, aucun clic du jeu
    assert _scan(game_client)["error"] == ""  # le panneau a été refermé : un second relevé le rouvre


def test_cards_arriving_after_the_panel_are_still_read(game_client, game):
    late = _move()
    late["late_ms"] = 1500  # le vrai jeu affiche « Silence spatial » avant les cartes
    game.set_movements([late])
    data = _scan(game_client)
    assert data["error"] == "" and [f["kind"] for f in data["fleets"]] == ["Déploiement"]


def test_a_return_is_confirmed_when_the_displayed_duration_reaches_the_wanted_time(game_client, game):
    game.set_movements([_move(flown=100)])  # durée de retour = vol déjà fait, elle avance de 2 s par seconde
    _scan(game_client)
    target = time.time() + 140  # rappel immédiat : arrivée dans 100 s ; marge 5 s → clic vers +17 s (le bot attend dans la fenêtre)
    recall = _program(game_client, 140)
    summary = _run(game_client)
    done = _state(game_client, recall["id"])
    assert done["state"] == "done", (summary, done)
    (action,) = _recalls(game)
    arrival = action["at"] + action["duration"]
    assert target - 5 - 3 <= arrival <= target - 5 + 3, (arrival - target)  # arrive environ 5 s avant l'heure voulue
    assert any("Flottes : retour de" in e["msg"] for e in _bot(game_client).events(10))


def test_a_constant_return_duration_is_handled_too(game_client, game):
    game.set_recall(mode="constant", return_seconds=600)
    game.set_movements([_move(flown=100)])
    _scan(game_client)
    target = time.time() + 606
    recall = _program(game_client, 606)
    _run(game_client)
    assert _state(game_client, recall["id"])["state"] == "done"
    (action,) = _recalls(game)
    assert abs(action["at"] + 600 - (target - 5)) <= 3


def test_a_far_wanted_time_waits_for_a_later_check_without_confirming(game_client, game):
    game.set_movements([_move(flown=100, left=5000)])
    _scan(game_client)
    recall = _program(game_client, 1000)
    assert "contrôle suivant" in _run(game_client)
    waiting = _state(game_client, recall["id"])
    assert waiting["state"] == "pending" and waiting["next_check"] > time.time() + 300  # écart ≈ 895 s, vitesse 2 : ~390 s
    assert _recalls(game) == [] and _bot(game_client).fleet.next_due() == waiting["next_check"]
    assert waiting["click_at"] == pytest.approx(waiting["next_check"] + 60, abs=1)  # clic prévu = contrôle + 1 min
    assert "aucun retour de flotte à faire maintenant" in _run(game_client)  # pas encore l'heure du contrôle


def test_a_time_that_cannot_be_met_is_refused_with_the_earliest_arrival(game_client, game):
    game.set_movements([_move(flown=100)])
    _scan(game_client)
    recall = _program(game_client, 50)  # un rappel immédiat arriverait dans 100 s
    _run(game_client)
    failed = _state(game_client, recall["id"])
    assert failed["state"] == "failed" and "trop tard" in failed["note"] and _recalls(game) == []


def test_a_fleet_that_is_gone_is_reported(game_client, game):
    game.set_movements([_move()])
    _scan(game_client)
    recall = _program(game_client, 300)
    game.set_movements([])
    _run(game_client)
    assert _state(game_client, recall["id"])["state"] == "failed" and "introuvable" in _state(game_client, recall["id"])["note"]


def test_an_unknown_confirmation_window_is_never_clicked(game_client, game):
    game.set_recall(no_confirm=True)
    game.set_movements([_move(flown=100)])
    _scan(game_client)
    recall = _program(game_client, 106)
    _run(game_client)
    failed = _state(game_client, recall["id"])
    assert failed["state"] == "failed" and "non reconnu" in failed["note"] and _recalls(game) == []


def test_a_confirmation_that_changes_nothing_is_a_failure(game_client, game):
    game.set_recall(no_effect=True)
    game.set_movements([_move(flown=100)])
    _scan(game_client)
    recall = _program(game_client, 106)
    _run(game_client)
    assert _state(game_client, recall["id"])["state"] == "failed" and "n'a pas pris en compte" in _state(game_client, recall["id"])["note"]


def test_a_return_is_a_success_even_if_the_game_keeps_its_window_open(game_client, game):
    """Vrai jeu (23/11) : le rappel est pris en compte mais la fenêtre reste ouverte. L'état réel de la flotte fait foi."""
    game.set_recall(stay_open=True)
    game.set_movements([_move(flown=100)])
    _scan(game_client)
    recall = _program(game_client, 106)
    _run(game_client)
    done = _state(game_client, recall["id"])
    assert done["state"] == "done", done
    assert len(_recalls(game)) == 1


def test_a_wanted_time_long_past_is_abandoned(game_client, game):
    game.set_movements([_move()])
    _scan(game_client)
    recall = _program(game_client, 300)
    _bot(game_client).fleets._recalls[0]["at"] = time.time() - 600
    _run(game_client)
    assert _state(game_client, recall["id"])["state"] == "abandoned" and _recalls(game) == []


def test_the_api_lists_programs_and_removes_returns(game_client, game):
    game.set_movements([_move()])
    _scan(game_client)
    recall = _program(game_client, 300)
    assert game_client.put("/api/fleets/settings", json={"margin": 8}).json()["margin"] == 8
    assert game_client.post("/api/fleets/recalls", json={"fleet_id": "nope", "at": _at(time.time() + 300)}).status_code == 422
    assert game_client.delete(f"/api/fleets/recalls/{recall['id']}").json()["recalls"] == []
    assert game_client.delete("/api/fleets/recalls/nope").status_code == 404


def test_the_return_time_of_the_real_dialog_is_read_as_an_arrival_time():
    from app.fleet_pass import return_duration
    dialog = {"text": "Confirmer le retour. Si vous confirmez maintenant, la flotte reviendra le 23/11/2026 09:41:16.",
              "returns_at": 1000.0 + 150, "durations": [], "buttons": ["Annuler", "Confirmer le retour"]}
    assert return_duration(dialog, now=1000.0) == 150.5  # heure tronquée à la seconde : +0,5 s en moyenne
    assert return_duration({**dialog, "returns_at": 900.0}, now=1000.0) == 0.0  # jamais négatif
    assert return_duration({"text": "x", "durations": [], "buttons": []}, now=1000.0) is None


def test_adding_a_return_wakes_the_feature_without_waiting_for_its_timer(game_client, game):
    bot = _bot(game_client)
    game_client.put("/api/features/fleets", json={"enabled": True})
    game.set_movements([_move()])
    _scan(game_client)
    runtime = bot.scheduler._runtime["fleets"]
    runtime.next_at = bot.scheduler._clock() + 1700  # le dernier passage vient d'avoir lieu : prochain dans ~28 min
    _program(game_client, 300)
    assert runtime.next_at <= bot.scheduler._clock()
