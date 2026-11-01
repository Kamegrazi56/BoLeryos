"""Automatisation sur le faux jeu : lecture, décisions à blanc, lancements réels, plafond, sécurités, plages horaires."""

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.automation.runner import Interrupted

ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]
MINES = {"Mine de Fer": 45, "Extracteur de Cristal": 45, "Synthétiseur d'Éther": 40}


def _bot(client):
    return client.app.state.bot


def _runner(client):
    return _bot(client).automation


def _until(predicate, timeout=15.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def _configure(client, *, mode="dry_run", cap=20, constructions=None, research=None, schedule=None, cycle=(0.02, 0.02),
               scope="all", planet="", recheck=15.0):
    """`constructions` : dict ou liste de (nom, niveau) — l'ORDRE est la priorité."""
    items = list((constructions or {}).items()) if isinstance(constructions, dict) else list(constructions or [])
    body = {
        "mode": mode,
        "daily_cap": cap,
        "cycle": {"min_minutes": cycle[0], "max_minutes": cycle[1]},
        "constructions": {
            "enabled": bool(items), "scope": scope, "planet": planet, "recheck_minutes": recheck,
            "queue": [{"name": n, "level": lvl} for n, lvl in items],
        },
        "research": {"enabled": bool(research), "targets": research or {}},
    }
    if schedule:
        body["schedule"] = schedule
    res = client.put("/api/automation/settings", json={"settings": body, "confirm_live": mode == "live"})
    assert res.status_code == 200, res.text
    return res.json()


def _force_due(client):
    """Fait comme si le délai de revérification de chaque planète était écoulé."""
    for state in _runner(client)._pstate.values():
        state["next"] = 0


def _planets_state(client):
    return {row["planet"]: row for row in client.get("/api/automation").json()["status"]["planets_state"]}


def _cycle(client):
    """Un cycle complet, sans la boucle du planificateur."""
    runner = _runner(client)
    runner._running = True
    try:
        client.portal.call(runner.run_cycle)
    finally:
        runner._running = False


def _logs(client):
    return [e["msg"] for e in client.get("/api/status?limit=1000").json()["logs"]]


def _has(client, text):
    return any(text in line for line in _logs(client))


# --------------------------------------------------------------------------- lecture
def test_read_state_shows_planets_resources_and_cards(game_client):
    data = game_client.post("/api/automation/read-state").json()
    assert [p["name"] for p in data["planets"]] == ["8-50-Concorde", "6-79-Egide", "9-169-Strate"]
    assert data["planets"][1]["stocks"][0] == pytest.approx(4.1e9, rel=0.01)
    b = data["sections"]["buildings"]
    assert b["planet"] == "8-50-Concorde" and b["title"] == "Industrie"
    assert b["resources"]["fer"] == pytest.approx(58e6) and b["resources"]["energie"] == 11014
    cards = {c["name"]: c for c in b["cards"]}
    assert cards["Mine de Fer"]["level"] == 40 and cards["Mine de Fer"]["enabled"] is False
    assert cards["Centrale Énergétique"]["enabled"] is True and cards["Centrale Énergétique"]["duration_s"] == 325
    assert {"Chantier Spatial", "Caserne"} <= {c["name"] for c in data["sections"]["facilities"]["cards"]}
    tech = {c["name"]: c for c in data["sections"]["tech"]["cards"]}
    assert tech["Technologie laser"]["action"] == "Ressources insuffisantes" and tech["Technologie laser"]["enabled"] is False
    # les noms découverts alimentent la page Actions
    catalog = game_client.get("/api/automation").json()["settings"]["catalog"]
    assert "Mine de Fer" in catalog["buildings"] and "Technologie laser" in catalog["research"]


def test_read_state_is_refused_when_not_in_a_game_page(game_client):
    _bot(game_client)._set(_bot(game_client)._state.__class__.IDLE, "test")
    assert game_client.post("/api/automation/read-state").status_code == 409


@pytest.mark.parametrize("close_on_select", [True, False])
def test_switching_planet_works_whether_or_not_the_window_closes_itself(game_client, game, close_on_select):
    game.reset(close_on_select=close_on_select)
    game_client.portal.call(_bot(game_client)._page.reload)  # le faux jeu a été réinitialisé : on recharge l'écran
    runner = _runner(game_client)
    runner._running = True
    try:
        planets = game_client.portal.call(runner.driver.list_planets)
        assert game_client.portal.call(runner.driver.switch_planet, planets[1])
        assert game_client.portal.call(runner.driver.active_planet) == "6-79-Egide"
    finally:
        runner._running = False


# ----------------------------------------------------------------------------- à blanc
def test_dry_run_decides_and_logs_but_never_clicks(game_client, game):
    _configure(game_client, constructions={**MINES, "Centrale Énergétique": 30})
    _cycle(game_client)
    assert game.actions() == []
    assert _has(game_client, "[À blanc] Planète 8-50-Concorde : je lancerais Centrale Énergétique")
    assert _has(game_client, "[À blanc] Planète 6-79-Egide : je lancerais Mine de Fer")  # la première de la liste réalisable
    assert _has(game_client, "Planète 9-169-Strate : en attente")


def test_disabled_features_do_nothing(game_client, game):
    _configure(game_client)  # ni constructions ni recherches
    _cycle(game_client)
    assert game.actions() == [] and not _has(game_client, "je lancerais")


# ------------------------------------------------------------------------------- réel
def test_live_mode_launches_the_first_feasible_upgrade_of_the_list_and_confirms_it(game_client, game):
    _configure(game_client, mode="live", constructions=MINES)  # pas de Centrale visée
    _cycle(game_client)
    assert game.actions() == [{"kind": "building", "planet": "6-79-Egide", "name": "Mine de Fer", "level_after": 41}]
    assert _has(game_client, "lancement confirmé")
    assert game_client.get("/api/automation").json()["status"]["launched_today"] == 1


def test_a_planet_with_a_construction_in_progress_is_left_alone(game_client, game):
    _configure(game_client, mode="live", constructions=MINES)
    _cycle(game_client)
    _force_due(game_client)
    _cycle(game_client)  # deuxième cycle : la construction n'est pas terminée
    assert len(game.actions()) == 1
    assert _planets_state(game_client)["6-79-Egide"]["status"] == "busy"


def test_each_planet_gets_at_most_one_launch_per_cycle(game_client, game):
    _configure(game_client, mode="live", constructions={**MINES, "Centrale Énergétique": 30})
    _cycle(game_client)
    planets = [a["planet"] for a in game.actions()]
    assert sorted(planets) == ["6-79-Egide", "8-50-Concorde"]


def test_daily_cap_stops_new_launches(game_client, game):
    _configure(game_client, mode="live", cap=1, constructions={**MINES, "Centrale Énergétique": 30})
    _cycle(game_client)
    assert len(game.actions()) == 1
    assert _has(game_client, "Plafond quotidien atteint")


def test_negative_energy_blocks_mines_but_allows_the_power_plant(game_client, game):
    game.reset(energy={"1": -50})
    game_client.portal.call(_bot(game_client)._page.reload)
    _configure(game_client, mode="live", constructions={**MINES, "Centrale Énergétique": 30}, cap=5)
    _cycle(game_client)
    egide = [a for a in game.actions() if a["planet"] == "6-79-Egide"]
    assert [a["name"] for a in egide] == ["Centrale Énergétique"]


def test_unreadable_screen_never_leads_to_a_click(game_client, game):
    game_client.portal.call(_bot(game_client)._page.evaluate, "document.querySelectorAll('.res span').forEach(e => { if (/^\\d/.test(e.textContent)) e.textContent = '12 To' })")
    runner = _runner(game_client)
    state = game_client.portal.call(runner.driver.read_page)
    assert state.resources.fer is None  # « 12 To » n'est pas compris : le bot ne devine pas


# ----------------------------------------------------- planète(s), priorité, timer d'attente
def test_one_selected_planet_is_the_only_one_touched(game_client, game):
    _configure(game_client, mode="live", constructions={**MINES, "Centrale Énergétique": 30}, scope="one", planet="6-79-Egide")
    _cycle(game_client)
    assert [(a["planet"], a["name"]) for a in game.actions()] == [("6-79-Egide", "Mine de Fer")]
    assert list(_planets_state(game_client)) == ["6-79-Egide"]  # Concorde et Strate ne sont même pas visitées


def test_an_unknown_single_planet_is_reported_and_nothing_happens(game_client, game):
    _configure(game_client, mode="live", constructions=MINES, scope="one", planet="Planète fantôme")
    _cycle(game_client)
    assert game.actions() == [] and _has(game_client, "introuvable dans la liste des planètes")


def test_the_players_order_decides_which_feasible_upgrade_goes_first(game_client, game):
    # Sur Egide tout est abordable : l'ordre de la liste décide, pas le coût
    _configure(game_client, mode="live", constructions=[("Centrale Énergétique", 30), ("Mine de Fer", 45)], scope="one", planet="6-79-Egide")
    _cycle(game_client)
    assert [a["name"] for a in game.actions()] == ["Centrale Énergétique"]


def test_the_first_unaffordable_entries_are_skipped_for_the_first_feasible_one(game_client, game):
    _configure(game_client, mode="live", scope="one", planet="8-50-Concorde",
               constructions=[("Mine de Fer", 45), ("Extracteur de Cristal", 45), ("Centrale Énergétique", 30), ("Caserne", 25)])
    _cycle(game_client)
    assert [a["name"] for a in game.actions()] == ["Centrale Énergétique"]  # seule abordable avec les ressources de Concorde


def test_a_planet_waiting_for_resources_is_rechecked_only_when_its_timer_is_due(game_client, game):
    _configure(game_client, constructions=[("Mine de Fer", 45)], recheck=5.0)  # à blanc, revérification toutes les 5 min
    _cycle(game_client)
    state = _planets_state(game_client)
    assert state["8-50-Concorde"]["status"] == "pending" and "ressources insuffisantes" in state["8-50-Concorde"]["detail"]
    assert state["9-169-Strate"]["status"] == "pending"
    assert state["6-79-Egide"]["status"] == "would_launch"
    assert all(270 <= row["next_in_s"] <= 340 for row in state.values())  # ≈ 5 min (± quelques %)
    assert _has(game_client, "en attente") and _has(game_client, "nouvelle vérification dans 5 min")

    runner = _runner(game_client)
    runner._running = True
    before = game_client.portal.call(runner.driver.active_planet)
    cycles = sum("Cycle : 3 planète" in line for line in _logs(game_client))
    _cycle(game_client)  # immédiatement : aucune planète n'est à revérifier
    assert game_client.portal.call(runner.driver.active_planet) == before  # le bot n'a même pas changé de planète
    assert sum("Cycle : 3 planète" in line for line in _logs(game_client)) == cycles
    assert _has(game_client, "Cycle : rien à vérifier pour l'instant")
    runner._running = False

    _force_due(game_client)  # le délai est écoulé
    _cycle(game_client)
    assert sum("Cycle : 3 planète" in line for line in _logs(game_client)) == cycles + 1


def test_the_construction_is_launched_once_the_resources_are_there(game_client, game):
    _configure(game_client, mode="live", constructions=[("Mine de Fer", 45)], scope="one", planet="9-169-Strate", recheck=5.0)
    _cycle(game_client)
    assert game.actions() == [] and _planets_state(game_client)["9-169-Strate"]["status"] == "pending"
    game.set_stocks(2, Fer=1e9, Cristal=1e9)  # les ressources arrivent
    _cycle(game_client)
    assert game.actions() == []  # pas encore l'heure de revérifier
    _force_due(game_client)
    _cycle(game_client)
    assert game.actions() == [{"kind": "building", "planet": "9-169-Strate", "name": "Mine de Fer", "level_after": 41}]
    assert _planets_state(game_client)["9-169-Strate"]["status"] == "busy"


def test_goals_all_reached_are_reported_and_not_rechecked_for_hours(game_client):
    _configure(game_client, constructions=[("Mine de Fer", 40)], scope="one", planet="6-79-Egide")  # déjà niveau 40
    _cycle(game_client)
    state = _planets_state(game_client)["6-79-Egide"]
    assert state["status"] == "done" and state["next_in_s"] > 5 * 3600
    assert _has(game_client, "tous les objectifs de la liste sont atteints")


def test_changing_the_list_makes_every_planet_due_again(game_client):
    _configure(game_client, constructions=[("Mine de Fer", 45)], recheck=60.0)
    _cycle(game_client)
    cycles = sum("Cycle : 3 planète" in line for line in _logs(game_client))
    _configure(game_client, constructions=[("Mine de Fer", 46)], recheck=60.0)  # liste modifiée
    _cycle(game_client)
    assert sum("Cycle : 3 planète" in line for line in _logs(game_client)) == cycles + 1


def test_the_dry_run_decision_is_logged_once_not_at_every_check(game_client):
    _configure(game_client, constructions=[("Mine de Fer", 45)], scope="one", planet="6-79-Egide", recheck=5.0)
    for _ in range(3):
        _cycle(game_client)
        _force_due(game_client)
    assert sum("[À blanc] Planète 6-79-Egide" in line for line in _logs(game_client)) == 1


def test_next_due_in_tells_the_scheduler_when_to_wake_up(game_client):
    runner = _runner(game_client)
    cfg = _configure(game_client, constructions=[("Mine de Fer", 45)], recheck=5.0)["settings"]
    assert runner.next_due_in(cfg) is None  # rien n'a encore été lu
    _cycle(game_client)
    assert 270 <= runner.next_due_in(cfg) <= 340
    _force_due(game_client)
    assert runner.next_due_in(cfg) == 0


def test_scope_and_timer_are_validated_by_the_api(game_client):
    bad_scope = {"constructions": {"enabled": True, "scope": "one", "planet": "", "queue": []}}
    res = game_client.put("/api/automation/settings", json={"settings": bad_scope})
    assert res.status_code == 422 and any("planète" in e for e in res.json()["detail"])
    bad_timer = {"constructions": {"enabled": True, "scope": "all", "queue": [], "recheck_minutes": 0}}
    assert game_client.put("/api/automation/settings", json={"settings": bad_timer}).status_code == 422


# --------------------------------------------------------------------------- recherches
def test_research_runs_on_the_richest_planet_in_live_mode_only(game_client, game):
    _configure(game_client, research={"Technologie laser": 25})
    _cycle(game_client)
    assert game.actions() == [] and _has(game_client, "[À blanc] Recherches (planète 6-79-Egide)")
    _configure(game_client, mode="live", research={"Technologie laser": 25})
    _cycle(game_client)
    assert game.actions() == [{"kind": "research", "planet": "6-79-Egide", "name": "Technologie laser", "level_after": 23}]


def test_research_skips_what_is_unaffordable_or_already_at_target(game_client, game):
    _configure(game_client, mode="live", research={"Technologie d’espionnage": 30, "Technologie laser": 22})
    _cycle(game_client)
    assert game.actions() == []


# ---------------------------------------------------------------------------- sécurités
def test_two_unconfirmed_launches_stop_the_automation(game_client, game):
    game.reset(no_debit=True)  # le jeu accepte le clic mais les ressources ne sont pas débitées
    game_client.portal.call(_bot(game_client)._page.reload)
    _configure(game_client, mode="live", constructions={**MINES, "Centrale Énergétique": 30})
    runner = _runner(game_client)
    runner._running = True
    with pytest.raises(Interrupted):
        game_client.portal.call(runner.run_cycle)
    assert runner._running is False and _has(game_client, "non confirmé")


def test_an_unexpected_dialog_stops_everything(game_client, game):
    game.reset(popup_after_click=True)
    game_client.portal.call(_bot(game_client)._page.reload)
    _configure(game_client, mode="live", constructions={"Centrale Énergétique": 30})
    runner = _runner(game_client)
    runner._running = True
    with pytest.raises(Interrupted):
        game_client.portal.call(runner.run_cycle)
    assert runner._running is False and _has(game_client, "fenêtre inattendue")


def test_guard_interrupts_for_manual_control_closed_session_and_stop(game_client):
    runner, bot = _runner(game_client), _bot(game_client)
    with pytest.raises(Interrupted, match="arrêt"):
        game_client.portal.call(runner.guard)  # pas démarré
    runner._running = True
    game_client.portal.call(runner.guard)  # tout va bien
    bot._manual = True
    with pytest.raises(Interrupted, match="manuel"):
        game_client.portal.call(runner.guard)
    bot._manual = False
    bot._set(bot._state.__class__.IDLE, "test")
    with pytest.raises(Interrupted, match="session"):
        game_client.portal.call(runner.guard)
    runner._running = False


def test_manual_input_is_ignored_while_a_cycle_is_finishing(game_client):
    bot, runner = _bot(game_client), _runner(game_client)
    bot._manual = True
    runner.cycle_running = True
    game_client.portal.call(bot.handle_input, {"t": "down", "button": "left", "x": 0.5, "y": 0.5})
    assert not _has(game_client, "Clic manuel")
    runner.cycle_running = False


# ------------------------------------------------------------------------ plages horaires
def _window_excluding_now():
    now = datetime.now(ZoneInfo("Europe/Paris"))
    start = f"{(now.hour + 2) % 24:02d}:00"
    end = f"{(now.hour + 3) % 24:02d}:00"
    return {"enabled": True, "timezone": "Europe/Paris", "windows": [{"start": start, "end": end, "days": ALL_DAYS}]}


def test_outside_the_time_window_nothing_happens(game_client, game):
    _configure(game_client, mode="live", constructions=MINES, schedule=_window_excluding_now())
    assert game_client.post("/api/automation/start").json()["in_window"] is False
    assert _until(lambda: _has(game_client, "Plage horaire : inactive"))
    time.sleep(1.5)
    assert game.actions() == [] and not _has(game_client, "Cycle :")
    game_client.post("/api/automation/stop")


def test_the_time_window_also_interrupts_a_running_cycle(game_client):
    _configure(game_client, constructions=MINES, schedule=_window_excluding_now())
    runner = _runner(game_client)
    runner._running = True
    with pytest.raises(Interrupted, match="plage"):
        game_client.portal.call(runner.guard)
    runner._running = False


# ------------------------------------------------------------------------- planificateur
def test_scheduler_runs_cycles_and_stops_on_demand(game_client, game):
    _configure(game_client, constructions={**MINES, "Centrale Énergétique": 30}, cycle=(0.02, 0.02))
    assert game_client.post("/api/automation/start").json()["running"] is True
    assert _until(lambda: sum("Cycle :" in line for line in _logs(game_client)) >= 2, timeout=40)
    summary = game_client.post("/api/automation/stop").json()
    assert summary["running"] is False
    count = sum("Cycle :" in line for line in _logs(game_client))
    time.sleep(2.5)
    assert sum("Cycle :" in line for line in _logs(game_client)) == count  # plus aucun cycle
    assert game.actions() == []  # à blanc


def test_scheduler_detects_an_already_open_session_instead_of_waiting(game_client):
    bot = _bot(game_client)
    bot._set(bot._state.__class__.IDLE, "test")  # le bot a oublié son état, mais le site est dans une partie
    _configure(game_client, constructions=MINES)
    game_client.post("/api/automation/start")
    assert _until(lambda: _has(game_client, "Session détectée"))
    assert _until(lambda: _has(game_client, "Cycle :"))
    game_client.post("/api/automation/stop")


def test_logout_stops_the_automation(game_client):
    _configure(game_client, constructions=MINES)
    game_client.post("/api/automation/start")
    game_client.post("/api/logout")
    assert game_client.get("/api/automation").json()["status"]["running"] is False


# ------------------------------------------------------------------------------------ API
def test_live_mode_requires_confirmation_through_the_api(game_client):
    body = {"settings": {"mode": "live"}, "confirm_live": False}
    res = game_client.put("/api/automation/settings", json=body)
    assert res.status_code == 422 and any("confirmation" in e for e in res.json()["detail"])
    assert game_client.get("/api/automation").json()["settings"]["mode"] == "dry_run"
    assert game_client.put("/api/automation/settings", json={**body, "confirm_live": True}).json()["settings"]["mode"] == "live"


def test_invalid_settings_return_clear_errors(game_client):
    res = game_client.put("/api/automation/settings", json={"settings": {"daily_cap": 0, "constructions": {"targets": {"Mine": 5000}}}})
    assert res.status_code == 422 and len(res.json()["detail"]) >= 2


def test_export_is_a_download_without_secrets(game_client):
    _configure(game_client, constructions=MINES)
    res = game_client.get("/api/automation/export")
    assert res.headers["content-disposition"].startswith("attachment")
    assert res.json()["constructions"]["queue"] == [{"name": n, "level": lvl} for n, lvl in MINES.items()]
    assert "password" not in res.text.lower()


def test_automation_endpoints_reject_other_sites(game_client):
    evil = {"Origin": "http://evil.example"}
    for method, url in (("put", "/api/automation/settings"), ("post", "/api/automation/start"),
                        ("post", "/api/automation/stop"), ("post", "/api/automation/read-state")):
        kwargs = {"json": {"settings": {}}} if method == "put" else {}
        assert getattr(game_client, method)(url, headers=evil, **kwargs).status_code == 403
