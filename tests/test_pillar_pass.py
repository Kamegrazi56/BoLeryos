"""Piliers programmés dans le faux jeu : lecture de « Gérer les Piliers », clic puis confirmation à l'heure du clic,
compte à rebours, refus du jeu, retard de plus d'1 h, Champ de force jamais touché."""

import time

from app.pillar_pass import parse_left, read_status
from app.pillars import click_time

CONCORDE, EGIDE, STRATE = "8-50-Concorde", "6-79-Egide", "9-169-Strate"


def _bot(client):
    return client.app.state.bot


def _scan(client):
    assert len(client.post("/api/planets/scan").json()["planets"]) == 3


def _program(client, planet: str, action: str, click_in_minutes: float) -> dict:
    """Programme un changement dont l'heure de clic est dans `click_in_minutes` minutes (négatif : déjà passée)."""
    store = _bot(client).pillars
    change = {"id": f"t{len(store.changes())}", "planet": planet, "action": action,
              "at": time.time() + 3600 + click_in_minutes * 60, "state": "pending", "note": ""}
    store._changes.append(change)  # contourne la vérification « au moins 1 h » pour simuler une heure de clic arrivée
    return change


def _run(client):
    return client.portal.call(_bot(client).pillar.run_pass)


def _pillar_actions(game):
    return [(a["planet"], a["to"]) for a in game.actions() if a["kind"] == "pillar"]


def _forbidden(game):
    return [a for a in game.actions() if a["kind"] == "forbidden"]


def test_countdown_and_rows_are_read():
    assert parse_left("58 min 36 s") == 3516 and parse_left("1 h 2 min") == 3720 and parse_left("rien") is None
    rows = [{"name": EGIDE, "button": "Désactiver les Piliers (…)", "pending": None, "closedNow": False},
            {"name": STRATE, "button": None, "pending": "10 min 0 s", "closedNow": True}]
    status = read_status(rows, now=1000.0)
    assert status[EGIDE] == {"closed": False, "pending_to": None, "effective_at": None}
    assert status[STRATE] == {"closed": True, "pending_to": "open", "effective_at": 1600.0}


def test_the_status_of_each_planet_is_read_from_the_manager_window(game_client, game):
    _scan(game_client)
    game.set_pillars(planets={STRATE: {"closed": True}, EGIDE: {"closed": False, "pending_to": "close", "end_in": 1200}})
    assert game_client.post("/api/pillars/scan").json()["error"] == ""
    planets = _bot(game_client).pillars.status()["planets"]
    assert planets[CONCORDE]["closed"] is False and planets[STRATE]["closed"] is True
    assert planets[EGIDE]["pending_to"] == "close" and abs(planets[EGIDE]["effective_at"] - (time.time() + 1200)) < 30
    assert game.actions() == [] and _forbidden(game) == []  # le relevé ne clique sur rien


def test_a_due_change_is_clicked_and_confirmed_then_the_countdown_is_seen(game_client, game):
    _scan(game_client)
    change = _program(game_client, EGIDE, "close", click_in_minutes=-1)
    summary = _run(game_client)
    assert summary.startswith(f"{EGIDE} : fermer, effectif le"), summary
    assert _pillar_actions(game) == [(EGIDE, "close")] and _forbidden(game) == []  # jamais le Champ de force
    done = next(c for c in _bot(game_client).pillars.changes() if c["id"] == change["id"])
    assert done["state"] == "done" and "effectif le" in done["note"]
    assert _bot(game_client).pillars.status()["planets"][EGIDE]["pending_to"] == "close"
    assert abs(_bot(game_client).pillars.history()[EGIDE] - (time.time() + 3600)) < 60


def test_reopening_a_closed_planet(game_client, game):
    _scan(game_client)
    game.set_pillars(planets={STRATE: {"closed": True}})
    _program(game_client, STRATE, "open", click_in_minutes=-1)
    _run(game_client)
    assert _pillar_actions(game) == [(STRATE, "open")]


def test_nothing_is_done_before_the_click_time(game_client, game):
    _scan(game_client)
    change = _program(game_client, EGIDE, "close", click_in_minutes=30)
    assert "aucun changement des Piliers à faire maintenant" in _run(game_client)
    assert game.actions() == [] and _bot(game_client).pillar.next_due() == click_time(change)


def test_a_change_more_than_one_hour_late_is_abandoned(game_client, game):
    _scan(game_client)
    change = _program(game_client, EGIDE, "close", click_in_minutes=-61)  # bot OFF pendant plus d'1 h
    _run(game_client)
    late = next(c for c in _bot(game_client).pillars.changes() if c["id"] == change["id"])
    assert late["state"] == "abandoned" and "plus d'1 h de retard" in late["note"] and _pillar_actions(game) == []


def test_a_refusal_of_the_game_is_reported(game_client, game):
    _scan(game_client)
    game.set_pillars(planets={STRATE: {"closed": True}, CONCORDE: {"closed": True}})  # déjà 2 planètes fermées
    change = _program(game_client, EGIDE, "close", click_in_minutes=-1)
    assert "échec" in _run(game_client)
    failed = next(c for c in _bot(game_client).pillars.changes() if c["id"] == change["id"])
    assert failed["state"] == "failed" and "plus de 2 planètes" in failed["note"]


def test_a_planet_already_in_the_wanted_state_or_already_changing_is_not_clicked(game_client, game):
    _scan(game_client)
    game.set_pillars(planets={STRATE: {"closed": True}, CONCORDE: {"closed": False, "pending_to": "close"}})
    shut = _program(game_client, STRATE, "close", click_in_minutes=-1)
    busy = _program(game_client, CONCORDE, "open", click_in_minutes=-1)
    _run(game_client)
    notes = {c["id"]: (c["state"], c["note"]) for c in _bot(game_client).pillars.changes()}
    assert notes[shut["id"]] == ("failed", "les Piliers sont déjà fermés")
    assert notes[busy["id"]] == ("failed", "un changement de statut est déjà en cours sur cette planète")
    assert _pillar_actions(game) == []
