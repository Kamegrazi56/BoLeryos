"""Expéditions (Opérations › Exploration) : lecture, décisions et simulation. Le lancement réel n'est pas codé."""

import pytest

from app.automation.expedition import ExpeditionPage, parse_expedition_page, parse_ratio, plan_expeditions
from tests.test_automation import _bot, _cycle, _force_due, _has, _runner

EGIDE = "6-79-Egide"


def unit(name, mode="number", value=1):
    return {"name": name, "mode": mode, "value": value}


def exp_line(way="piliers", hours=3, units=None):
    if units is None:
        units = [unit("Soldat", "percent", 10), unit("Grand Transporteur", "number", 100)]
    return {"way": way, "hours": hours, "units": units}


def _expeditions(client, lines, *, active_target=4, mode="dry_run", planet=EGIDE, enabled=True, priority=None):
    body = {
        "mode": mode, "cycle": {"min_minutes": 0.02, "max_minutes": 0.02},
        "expeditions": {"enabled": enabled, "scope": "one" if planet else "all", "planet": planet, "recheck_minutes": 15.0,
                        "active_target": active_target, "queue": lines},
    }
    if priority:
        body["priority"] = priority
    res = client.put("/api/automation/settings", json={"settings": body, "confirm_live": mode == "live"})
    assert res.status_code == 200, res.text
    return res.json()


def _dash(client):
    return client.get("/api/dashboard").json()


# ------------------------------------------------------------------ lecture et décisions (sans navigateur)
def test_counters_and_available_units_are_read_the_way_the_game_writes_them():
    assert [parse_ratio(t) for t in ("0/46", "3 / 46", "10/10", "beaucoup", None)] == [(0, 46), (3, 46), (10, 10), None, None]
    page = parse_expedition_page("piliers", {"daily": "0/46", "active": "0/10", "units": [
        {"name": "Soldat", "available": "2 507 458"}, {"name": "Rempart de Fer", "available": "3 182"}, {"name": "Mineur", "available": "plein"}]})
    assert (page.active, page.active_max, page.daily, page.daily_max) == (0, 10, 0, 46)
    assert page.available == {"soldat": 2507458, "rempart de fer": 3182} and page.unreadable == ["Mineur : plein"]


def _page(active=2, active_max=10, daily=3, daily_max=46, **units):
    avail = {k.replace("_", " "): v for k, v in units.items()}
    return ExpeditionPage("piliers", active, active_max, daily, daily_max, avail, {k: k.title() for k in avail})


def test_free_places_are_the_smallest_of_the_wished_the_games_and_the_daily_remainder():
    lines = [exp_line(units=[unit("soldat", "number", 5)])]
    page = lambda **kw: {"piliers": _page(soldat=1000, **kw)}  # noqa: E731
    assert len(plan_expeditions(lines, page(), 4).launches) == 2  # 4 voulues − 2 actives
    assert len(plan_expeditions(lines, page(active=9), 20).launches) == 1  # le jeu autorise 10 au plus
    assert len(plan_expeditions(lines, page(daily=45), 20).launches) == 1  # quota du jour : 46
    none = plan_expeditions(lines, page(active=4), 4)
    assert none.launches == [] and "4 exploration(s) active(s) sur 4 voulue(s)" in none.notes[0]
    assert "places du jeu sont occupées" in plan_expeditions(lines, page(active=10, active_max=10), 20).notes[0]
    assert "quota du jour atteint" in plan_expeditions(lines, page(daily=46), 20).notes[0]


def test_quantities_number_max_and_percent_and_all_or_nothing():
    page = {"piliers": _page(active=0, soldat=1000, grand_transporteur=9000)}
    lines = [exp_line(units=[unit("Soldat", "percent", 25), unit("Grand Transporteur", "max")])]
    (launch,) = plan_expeditions(lines, page, 1).launches
    assert launch.units == [("Soldat", 250), ("Grand Transporteur", 9000)]
    too_many = [exp_line(units=[unit("Soldat", "number", 1001)])]
    plan = plan_expeditions(too_many, page, 1)
    assert plan.launches == [] and "1000 disponible(s) sur 1001" in plan.notes[0]
    assert "absent de la page" in plan_expeditions([exp_line(units=[unit("Fantôme")])], page, 1).notes[0]
    assert "aucun disponible" in plan_expeditions([exp_line(units=[unit("Soldat", "percent", 0 or 1)])], {"piliers": _page(active=0, soldat=50)}, 1).notes[0]


def test_units_of_a_planned_exploration_are_not_reused_and_the_first_feasible_line_wins():
    page = {"piliers": _page(active=0, soldat=100, mineur=30)}
    lines = [exp_line(units=[unit("Soldat", "number", 60)]), exp_line(units=[unit("Mineur", "number", 20)])]
    plan = plan_expeditions(lines, page, 3)
    # 1re : 60 soldats ; 2e place : il n'en reste que 40, la ligne 1 n'est plus réalisable → la ligne 2 ; 3e : plus rien
    assert [l.units for l in plan.launches] == [[("Soldat", 60)], [("Mineur", 20)]]
    assert plan.notes and "Mineur" not in plan.notes[0]


def test_unreadable_counters_mean_nothing_is_planned():
    page = {"piliers": ExpeditionPage("piliers", None, None, None, None, {"soldat": 5}, {"soldat": "Soldat"}, ["actives : ?"])}
    plan = plan_expeditions([exp_line(units=[unit("Soldat")])], page, 2)
    assert plan.launches == [] and "illisible" in plan.notes[0]


# -------------------------------------------------------------------------------------- réglages
def test_expedition_settings_are_validated(game_client):
    def put(lines, **extra):
        return game_client.put("/api/automation/settings", json={"settings": {"expeditions": {"queue": lines, **extra}}})

    assert put([exp_line()]).status_code == 200
    bad = [
        ([{"way": "ciel", "hours": 3, "units": [unit("Soldat")]}], "voie"), ([exp_line(hours=0)], "durée"), ([exp_line(hours=25)], "durée"),
        ([exp_line(units=[])], "au moins une unité"), ([exp_line(units=[unit("Soldat", "percent", 101)])], "quantité"),
        ([exp_line(units=[unit("Soldat", "number", 0)])], "quantité"), ([exp_line(units=[unit("Soldat"), unit("soldat")])], "double"),
        ([exp_line(units=[unit("Soldat", "beaucoup")])], "mode"),
    ]
    for lines, word in bad:
        res = put(lines)
        assert res.status_code == 422 and word in " ".join(res.json()["detail"]), (lines, res.text)
    assert put([exp_line()], active_target=0).status_code == 422
    assert put([exp_line()], scope="one").status_code == 422
    assert put([exp_line() for _ in range(21)]).status_code == 422


def test_ids_are_kept_and_the_catalog_offers_units_per_way(game_client):
    first = _expeditions(game_client, [exp_line()])["settings"]["expeditions"]["queue"]
    again = game_client.put("/api/automation/settings", json={"settings": {"expeditions": {"queue": first}}}).json()["settings"]["expeditions"]["queue"]
    assert [q["id"] for q in again] == [q["id"] for q in first]
    catalog = game_client.get("/api/automation").json()["catalog"]
    assert [w["id"] for w in catalog["expedition_ways"]] == ["piliers", "flotte"]
    ground = [u["name"] for u in catalog["expedition_units"]["piliers"]]
    assert ground[0] == "Soldat" and "Rempart de Fer" in ground and "Grand Transporteur" in ground
    assert [b["id"] for b in catalog["blocks"]][-1] == "expeditions"


# ------------------------------------------------------------------- avec le navigateur (faux jeu)
def test_simulation_fills_the_free_places_and_says_what_it_would_launch(game_client, game):
    _expeditions(game_client, [exp_line(units=[unit("Soldat", "percent", 10), unit("Grand Transporteur", "number", 100)])], active_target=4)
    _cycle(game_client)  # 2 actives sur 4 voulues : 2 places
    assert game.actions() == []
    assert _has(game_client, f"[Simulation] Planète {EGIDE} : je lancerais une exploration par les Piliers (3 h) avec 250745 × Soldat, 100 × Grand Transporteur")
    dash = _dash(game_client)
    assert dash["simulated_total"] == 2  # deux places libres ; les unités de la première ne sont pas réutilisées par la seconde
    assert "250745 × Soldat" in dash["simulated"][0]["text"] and "225671 × Soldat" in dash["simulated"][1]["text"]
    assert dash["simulated"][-1]["planet"] == EGIDE


def test_the_flotte_way_reads_its_own_units(game_client, game):
    _expeditions(game_client, [exp_line("flotte", 6, [unit("Testo Civil Transpo Destroy Def", "number", 20)])], active_target=3)
    _cycle(game_client)
    assert _has(game_client, "je lancerais une exploration par la Flotte (6 h) avec 20 × Testo Civil Transpo Destroy Def")
    assert "Testo Civil Transpo Destroy Def" in game_client.get("/api/automation").json()["settings"]["catalog"]["ships"]


def test_nothing_to_launch_when_the_wished_number_is_already_active(game_client, game):
    _expeditions(game_client, [exp_line()], active_target=2)  # 2 actives = 2 voulues
    _cycle(game_client)
    assert _has(game_client, "pas d'exploration à lancer (2 exploration(s) active(s) sur 2 voulue(s))")
    assert _dash(game_client)["simulated_total"] == 0
    rows = game_client.get("/api/automation").json()["status"]["production_state"]
    assert [(r["block"], r["status"]) for r in rows] == [("expeditions", "idle")]


def test_a_free_place_appears_when_an_exploration_ends_and_the_daily_quota_is_respected(game_client, game):
    _expeditions(game_client, [exp_line(units=[unit("Soldat", "number", 5)])], active_target=2)
    _cycle(game_client)
    assert _dash(game_client)["simulated_total"] == 0
    game.set_exp(active=1)  # une exploration vient de se terminer
    _force_due(game_client)
    _runner(game_client)._prodstate.clear()
    _cycle(game_client)
    assert _dash(game_client)["simulated_total"] == 1
    game.set_exp(active=0, daily=46)
    _runner(game_client)._prodstate.clear()
    _cycle(game_client)
    assert _has(game_client, "quota du jour atteint (46/46)")


def test_real_mode_never_launches_an_expedition_for_now(game_client, game):
    _expeditions(game_client, [exp_line(units=[unit("Soldat", "number", 5)])], active_target=3, mode="live")
    _cycle(game_client)
    assert game.actions() == []
    assert _has(game_client, "le lancement réel n'est pas encore disponible") and _has(game_client, "[Simulation]")
    assert game_client.get("/api/automation").json()["status"]["launched_today"] == 0


def test_priority_includes_expeditions_last_and_disabled_expeditions_do_nothing(game_client, game):
    assert game_client.get("/api/automation").json()["settings"]["priority"][-1] == "expeditions"
    _expeditions(game_client, [exp_line()], enabled=False)
    _cycle(game_client)
    assert not _has(game_client, "exploration")


def test_read_state_shows_the_exploration_page_for_both_ways(game_client, game):
    data = game_client.post("/api/automation/read-state").json()["expeditions"]
    assert set(data) == {"piliers", "flotte"}
    assert (data["piliers"]["active"], data["piliers"]["active_max"], data["piliers"]["daily"], data["piliers"]["daily_max"]) == (2, 10, 3, 46)
    assert {u["name"]: u["available"] for u in data["piliers"]["units"]}["Soldat"] == 2507458
    assert {u["name"]: u["available"] for u in data["flotte"]["units"]} == {"Testo Civil Transpo Destroy Def": 43, "Sonde Spatiale": 3987}
    assert game.actions() == []
