"""Accueil : phrase d'état, vérifications avant démarrage, résumé de la simulation, événements, objectifs relatifs."""

from dataclasses import replace
from functools import partial

import pytest

from app.automation.settings import DEFAULTS, SettingsError, validate
from app.automation.state import Card
from tests.test_automation import MINES, _configure, _cycle, _runner


def _dashboard(client):
    return client.get("/api/dashboard").json()


def _step(client, step_id):
    return next(s for s in _dashboard(client)["readiness"]["steps"] if s["id"] == step_id)


# ---------------------------------------------------------------------------- réglages
def test_queue_items_get_stable_ids_and_a_relative_flag():
    cons = validate({**DEFAULTS, "constructions": {"queue": [
        {"name": "Mine de Fer", "level": 3, "relative": True}, {"name": "Caserne", "level": 20}]}}, allow_live=False)["constructions"]
    first, second = cons["queue"]
    assert first["relative"] is True and second["relative"] is False
    assert len(first["id"]) == 8 and first["id"] != second["id"]
    again = validate({**DEFAULTS, "constructions": {"queue": cons["queue"]}}, allow_live=False)["constructions"]["queue"]
    assert [q["id"] for q in again] == [first["id"], second["id"]]  # les identifiants sont conservés


def test_relative_levels_must_be_at_least_one():
    with pytest.raises(SettingsError, match="niveau"):
        validate({**DEFAULTS, "constructions": {"queue": [{"name": "Mine de Fer", "level": 0, "relative": True}]}}, allow_live=False)
    validate({**DEFAULTS, "constructions": {"queue": [{"name": "Mine de Fer", "level": 0}]}}, allow_live=False)  # absolu : 0 permis


def test_partial_update_keeps_everything_else(game_client):
    _configure(game_client, constructions=MINES, cap=7)
    res = game_client.put("/api/automation/settings", json={"settings": {"constructions": {"recheck_minutes": 40}}})
    cons = res.json()["settings"]["constructions"]
    assert cons["recheck_minutes"] == 40 and len(cons["queue"]) == 3 and cons["enabled"] is True
    assert res.json()["settings"]["daily_cap"] == 7


def test_a_partial_update_cannot_switch_to_real_mode_without_confirmation(game_client):
    res = game_client.put("/api/automation/settings", json={"settings": {"mode": "live"}})
    assert res.status_code == 422 and game_client.get("/api/automation").json()["settings"]["mode"] == "dry_run"


def test_catalog_offers_ready_made_lists_of_known_buildings(game_client):
    catalog = game_client.get("/api/automation").json()["catalog"]
    known = {e["name"] for e in catalog["buildings"]}
    assert [p["id"] for p in catalog["presets"]] == ["mines", "mines_energy", "mines_energy_storage"]
    for preset in catalog["presets"]:
        assert preset["names"] and set(preset["names"]) <= known


# ---------------------------------------------------------------- objectifs « monter de N »
def _card(name, level):
    return Card(name=name, level=level, costs={}, duration_s=None, action="Améliorer", enabled=True)


def test_relative_target_is_baseline_plus_n_and_does_not_move(game_client):
    runner = _runner(game_client)
    cons = {"queue": [{"id": "abc12345", "name": "Mine de Fer", "level": 3, "relative": True},
                      {"id": "def67890", "name": "Caserne", "level": 20, "relative": False}]}
    assert runner._resolve_queue(cons, [_card("Mine de Fer", 40)], "Egide") == [("Mine de Fer", 43), ("Caserne", 20)]
    # la mine monte à 41 : l'objectif reste 43 (niveau de départ + 3), il ne glisse pas
    assert runner._resolve_queue(cons, [_card("Mine de Fer", 41)], "Egide")[0] == ("Mine de Fer", 43)
    # une autre planète a son propre niveau de départ
    assert runner._resolve_queue(cons, [_card("Mine de Fer", 10)], "Concorde")[0] == ("Mine de Fer", 13)
    # bâtiment invisible et jamais vu : l'objectif relatif est ignoré tant qu'on ne connaît pas le niveau de départ
    assert runner._resolve_queue(cons, [], "Strate") == [("Caserne", 20)]


def test_relative_goal_in_a_simulation_cycle_launches_up_to_baseline_plus_n(game_client, game):
    _configure(game_client, constructions=[("Mine de Fer", 1)])
    queue = _runner(game_client).store.get()["constructions"]["queue"]
    queue[0]["relative"] = True
    game_client.put("/api/automation/settings", json={"settings": {"constructions": {"queue": queue}}})
    _cycle(game_client)
    summary = _dashboard(game_client)
    assert summary["simulated_total"] >= 1
    assert summary["simulated"][-1]["name"] == "Mine de Fer" and summary["simulated"][-1]["to"] == 41  # niveau 40 → 41 (= 40 + 1)


# ----------------------------------------------------------------------- tableau de bord
def test_dashboard_sentence_follows_the_state_of_the_bot(game_client):
    assert "arrêté" in _dashboard(game_client)["sentence"]
    _configure(game_client)
    runner = _runner(game_client)
    runner._running = True
    try:
        assert "rien à faire" in _dashboard(game_client)["sentence"]
        _configure(game_client, constructions=MINES)
        assert "pas encore vérifié" in _dashboard(game_client)["sentence"]
        runner.waiting = "la page de connexion"
        assert _dashboard(game_client)["sentence"] == "Le bot attend : la page de connexion."
    finally:
        runner._running = False
        runner.waiting = ""


def test_dashboard_sentence_counts_followed_planets_after_a_cycle(game_client, game):
    _configure(game_client, constructions=MINES)
    runner = _runner(game_client)
    runner._running = True
    try:
        game_client.portal.call(runner.run_cycle)
        sentence = _dashboard(game_client)["sentence"]
    finally:
        runner._running = False
    assert "3 planète(s)" in sentence and "en attente de ressources" in sentence and "simulation" in sentence
    assert "Prochaine vérification dans" in sentence


def test_readiness_lists_what_is_missing_with_a_link_to_fix_it(game_client):
    steps = {s["id"]: s for s in _dashboard(game_client)["readiness"]["steps"]}
    assert list(steps) == ["account", "universe", "work", "mode", "schedule"]
    assert steps["work"]["ok"] is False and steps["work"]["link"] == "/reglages#constructions" and steps["work"]["required"]
    assert steps["account"]["ok"] is True  # déjà connecté
    assert steps["mode"]["required"] is False and "Simulation" in steps["mode"]["detail"]
    assert _dashboard(game_client)["readiness"]["ready"] is False
    _configure(game_client, constructions=MINES)
    assert _step(game_client, "work")["ok"] is True and "3 amélioration" in _step(game_client, "work")["detail"]
    assert _dashboard(game_client)["readiness"]["ready"] is True


def test_readiness_asks_for_credentials_when_not_connected(client):
    steps = {s["id"]: s for s in client.get("/api/dashboard").json()["readiness"]["steps"]}
    assert steps["account"]["ok"] is False and steps["account"]["link"] == "/reglages#account"
    client.post("/api/account/credentials", json={"identifier": "joueur@example.com", "password": "secret-pw"})
    steps = {s["id"]: s for s in client.get("/api/dashboard").json()["readiness"]["steps"]}
    assert steps["account"]["ok"] is True and "jo***" in steps["account"]["detail"] and "joueur@" not in steps["account"]["detail"]


def test_readiness_mentions_the_real_mode(game_client):
    _configure(game_client, mode="live", constructions=MINES)
    assert "RÉEL" in _step(game_client, "mode")["detail"]


# --------------------------------------------------------------- résumé de la simulation
def test_simulation_summary_lists_each_decision_once(game_client):
    _configure(game_client, constructions=MINES)
    _cycle(game_client)
    first = _dashboard(game_client)
    assert first["simulated_total"] == 1 and first["simulated"][0]["planet"] == "6-79-Egide"
    assert (first["simulated"][0]["name"], first["simulated"][0]["from"], first["simulated"][0]["to"]) == ("Mine de Fer", 40, 41)
    for state in _runner(game_client)._pstate.values():
        state["next"] = 0
    _cycle(game_client)  # même décision au contrôle suivant : pas un doublon
    assert _dashboard(game_client)["simulated_total"] == 1


def test_simulation_summary_is_cleared_when_the_bot_is_restarted(game_client):
    _configure(game_client, constructions=MINES)
    _cycle(game_client)
    assert _dashboard(game_client)["simulated_total"] == 1
    game_client.post("/api/automation/start")
    game_client.post("/api/automation/stop")
    assert _dashboard(game_client)["simulated_total"] == 0


# ------------------------------------------------------------------------------ événements
def test_events_are_the_important_log_lines_only(game_client):
    _configure(game_client, constructions=MINES)
    _cycle(game_client)
    events = game_client.get("/api/events").json()
    assert events and all(e["event"] for e in events)
    assert any("[Simulation]" in e["msg"] for e in events)
    everything = game_client.get("/api/status?limit=1000").json()["logs"]
    assert len(everything) > len(events)  # le détail technique reste dans le journal complet
    newest = max(e["t"] for e in events)
    assert game_client.get(f"/api/events?after={newest}").json() == []


def test_a_real_launch_is_a_launch_event_and_an_automatic_stop_needs_attention(game_client, game):
    _configure(game_client, mode="live", constructions=MINES)
    _cycle(game_client)
    kinds = {e["kind"]: e["msg"] for e in game_client.get("/api/status?limit=1000").json()["logs"] if e["kind"] != "info"}
    assert "Mine de Fer lancée (niveau 40 → 41)" in kinds["launch"]
    runner = _runner(game_client)
    runner._running = True
    game_client.portal.call(partial(runner.stop, "essai", automatic=True))
    attention = [e for e in game_client.get("/api/events").json() if e["kind"] == "attention"]
    assert attention and "essai" in attention[-1]["msg"]
    game_client.portal.call(partial(runner.stop, "essai", automatic=False))  # déjà arrêté : sans effet


def test_a_manual_stop_is_not_an_alert(game_client):
    game_client.post("/api/automation/start")
    game_client.post("/api/automation/stop")
    assert [e for e in game_client.get("/api/events").json() if e["kind"] == "attention"] == []


# ------------------------------------------------------------------------- connexion directe
def test_login_now_needs_remembered_credentials(game_client):
    res = game_client.post("/api/account/login")
    assert res.status_code == 409 and "identifiant" in res.json()["detail"].lower()


def test_login_now_uses_remembered_credentials(settings, game):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(replace(settings))) as client:
        client.post("/api/account/credentials", json={"identifier": "joueur.test@example.com", "password": "good-password"})
        res = client.post("/api/account/login")
        assert res.status_code == 200 and res.json()["state"] == "logged_in", res.text
        assert "good-password" not in res.text
