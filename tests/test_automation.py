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


def _configure(client, *, mode="dry_run", cap=20, constructions=None, research=None, schedule=None, cycle=(0.02, 0.02)):
    body = {
        "mode": mode,
        "daily_cap": cap,
        "cycle": {"min_minutes": cycle[0], "max_minutes": cycle[1]},
        "constructions": {"enabled": bool(constructions), "targets": constructions or {}},
        "research": {"enabled": bool(research), "targets": research or {}},
    }
    if schedule:
        body["schedule"] = schedule
    res = client.put("/api/automation/settings", json={"settings": body, "confirm_live": mode == "live"})
    assert res.status_code == 200, res.text
    return res.json()


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
    assert _has(game_client, "[À blanc] Planète 6-79-Egide : je lancerais Centrale Énergétique")
    assert _has(game_client, "Planète 9-169-Strate : rien à lancer")


def test_disabled_features_do_nothing(game_client, game):
    _configure(game_client)  # ni constructions ni recherches
    _cycle(game_client)
    assert game.actions() == [] and not _has(game_client, "je lancerais")


# ------------------------------------------------------------------------------- réel
def test_live_mode_launches_the_upgrade_that_weighs_least_and_confirms_it(game_client, game):
    _configure(game_client, mode="live", constructions=MINES)  # pas de Centrale visée
    _cycle(game_client)
    assert game.actions() == [{"kind": "building", "planet": "6-79-Egide", "name": "Synthétiseur d'Éther", "level_after": 35}]
    assert _has(game_client, "lancement confirmé")
    assert game_client.get("/api/automation").json()["status"]["launched_today"] == 1


def test_a_planet_with_a_construction_in_progress_is_left_alone(game_client, game):
    _configure(game_client, mode="live", constructions=MINES)
    _cycle(game_client)
    _cycle(game_client)  # deuxième cycle : la construction n'est pas terminée
    assert len(game.actions()) == 1
    assert _has(game_client, "construction en cours")


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


def test_scheduler_waits_until_the_player_is_in_a_game(game_client):
    bot = _bot(game_client)
    bot._set(bot._state.__class__.IDLE, "test")
    _configure(game_client, constructions=MINES)
    game_client.post("/api/automation/start")
    assert _until(lambda: _has(game_client, "En attente : pas connecté"))
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
    assert res.json()["constructions"]["targets"] == MINES and "password" not in res.text.lower()


def test_automation_endpoints_reject_other_sites(game_client):
    evil = {"Origin": "http://evil.example"}
    for method, url in (("put", "/api/automation/settings"), ("post", "/api/automation/start"),
                        ("post", "/api/automation/stop"), ("post", "/api/automation/read-state")):
        kwargs = {"json": {"settings": {}}} if method == "put" else {}
        assert getattr(game_client, method)(url, headers=evil, **kwargs).status_code == 403
