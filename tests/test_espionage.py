"""Espionnage automatique : lecture de la Galaxie, choix d'une cible par passage, simulation (aucun envoi)."""

from app.automation.espionage import choose, eligible, my_tag, parse_galaxy
from app.automation.settings import coord_key
from tests.test_automation import _cycle, _has, _logs

ROWS = [
    {"texts": ["KameGrazi [REPCO]", "5-32-Sceau · I27 95 00"], "actions": []},  # ma planète : pas de bouton
    {"texts": ["Kairo-Vigie-378 [AI]", "Kairo-Vigie-378 7 · I17 34 68"], "actions": ["Flotte", "Piliers", "Sonde", "Drone"]},
    {"texts": ["Splif [REPCO]", "F5 · I29 06 25"], "actions": ["Flotte", "Piliers", "Sonde", "Drone"]},
    {"texts": ["Hades33", "Gaia - M · I17 99 83"], "actions": ["Flotte", "Piliers", "Sonde", "Drone"]},
    {"texts": ["Planete miniere I27 40 68"], "actions": ["Miner", "Sonde"]},
    {"texts": ["Brèches I28 91 47"], "actions": []},
]
SPY = {"coord_min": "I17 00 00", "coord_max": "I29 99 99", "players": True, "errants": True, "exclude_alliance": True}


# ----------------------------------------------------------------------------- sans navigateur
def test_the_galaxy_list_is_read_the_way_the_game_writes_it():
    targets = parse_galaxy(ROWS)
    assert [(t.player, t.tag, t.planet, t.coords, t.errant) for t in targets] == [
        ("Kairo-Vigie-378", "AI", "Kairo-Vigie-378 7", "I17 34 68", True), ("Splif", "REPCO", "F5", "I29 06 25", False),
        ("Hades33", "", "Gaia - M", "I17 99 83", False)]  # ni ma planète, ni la planète minière, ni la brèche
    assert my_tag(ROWS, ["5-32-Sceau"]) == "REPCO"


def test_coordinates_are_compared_letter_then_numbers_bounds_included():
    assert coord_key("I27 95 00") < coord_key("I29 06 25") < coord_key("J00 00 00")
    targets = parse_galaxy(ROWS)
    inside = eligible(targets, {**SPY, "coord_min": "I17 34 68", "coord_max": "I17 99 83"}, ["5-32-Sceau"], "REPCO")
    assert [t.coords for t in inside] == ["I17 34 68", "I17 99 83"]


def test_players_errants_and_my_alliance():
    targets = parse_galaxy(ROWS)
    labels = lambda spy, tag="REPCO": [t.player for t in eligible(targets, {**SPY, **spy}, ["5-32-Sceau"], tag)]  # noqa: E731
    assert labels({}) == ["Kairo-Vigie-378", "Hades33"]  # Splif est de mon alliance
    assert labels({"exclude_alliance": False}) == ["Kairo-Vigie-378", "Splif", "Hades33"]
    assert labels({"players": False}) == ["Kairo-Vigie-378"]
    assert labels({"errants": False}) == ["Hades33"]


def test_one_target_per_pass_never_spied_first_then_the_oldest():
    targets = eligible(parse_galaxy(ROWS), SPY, ["5-32-Sceau"], "REPCO")
    assert choose(targets, {}).coords == "I17 34 68"
    assert choose(targets, {"I17 34 68": 100.0}).coords == "I17 99 83"
    assert choose(targets, {"I17 34 68": 100.0, "I17 99 83": 50.0}).coords == "I17 99 83"
    assert choose([], {}) is None


# ----------------------------------------------------------------------------------- réglages
def test_espionage_settings_are_validated(game_client):
    def put(**spy):
        return game_client.put("/api/automation/settings", json={"settings": {"espionage": spy}})

    ok = put(enabled=True, coord_min="m69 00 00", coord_max="M69 99 99", way="both", players=True, errants=False)
    assert ok.status_code == 200 and ok.json()["settings"]["espionage"]["coord_min"] == "M69 00 00"
    for bad, word in [
        ({"coord_min": "M6 00 00"}, "format"), ({"coord_min": "M70 00 00", "coord_max": "M69 00 00"}, "avant"),
        ({"enabled": True, "coord_min": "", "coord_max": ""}, "minimum"), ({"way": "pigeon"}, "sonde"),
        ({"enabled": True, "coord_min": "M69 00 00", "coord_max": "M69 99 99", "players": False, "errants": False}, "joueurs"),
    ]:
        res = put(**bad)
        assert res.status_code == 422 and word in " ".join(res.json()["detail"]), (bad, res.text)


# ------------------------------------------------------------------- avec le navigateur (faux jeu)
def _spy(client, mode="dry_run", **spy):
    body = {"mode": mode, "cycle": {"min_minutes": 0.02, "max_minutes": 0.02},
            "espionage": {"enabled": True, "way": "sonde", "players": False, "errants": True, "exclude_alliance": True,
                          "coord_min": "M69 00 00", "coord_max": "M69 99 99", "planet": "", **spy}}
    res = client.put("/api/automation/settings", json={"settings": body, "confirm_live": mode == "live"})
    assert res.status_code == 200, res.text
    client.app.state.bot.automation.store.add_catalog("planets", ["6-79-Egide", "8-50-Concorde", "9-169-Strate"])


def _spied(client):
    return [line for line in _logs(client) if line.startswith("[Simulation] Espionnage")]


def test_simulation_spies_one_errant_per_pass_in_the_interval_and_rotates(game_client, game):
    _spy(game_client)
    for _ in range(3):
        _cycle(game_client)
    lines = _spied(game_client)
    assert len(lines) == 3 and game.actions() == []
    # deux errants dans l'intervalle M69 : Bryn-Socle (M69 37 24) et Kairo-Vigie (M69 34 68) ; Torek (M97) est hors intervalle
    assert "une sonde sur Kairo-Vigie-378 [AI] (Kairo-Vigie-378 7, M69 34 68)" in lines[0]
    assert "Bryn-Socle-176 [AI]" in lines[1] and "Kairo-Vigie-378 [AI]" in lines[2]
    assert all("Torek" not in line and "Hades33" not in line and "Splif" not in line for line in lines)
    dash = game_client.get("/api/dashboard").json()
    assert dash["simulated"][-1]["text"].startswith("une sonde sur Kairo-Vigie-378")


def test_players_only_excludes_my_alliance_and_both_ways(game_client, game):
    _spy(game_client, players=True, errants=False, way="both")
    _cycle(game_client)
    (line,) = _spied(game_client)
    assert "une sonde et un drone sur Hades33 (Gaia - M, M69 99 83)" in line  # Splif [REPCO] est de mon alliance


def test_no_target_in_the_interval_is_said_plainly(game_client, game):
    _spy(game_client, coord_min="A00 00 00", coord_max="A99 99 99")
    _cycle(game_client)
    assert _spied(game_client) == [] and _has(game_client, "Espionnage : aucune cible entre A00 00 00 et A99 99 99")


def test_real_mode_never_sends_a_probe_for_now(game_client, game):
    _spy(game_client, mode="live")
    _cycle(game_client)
    assert game.actions() == [] and _has(game_client, "l'envoi réel n'est pas encore disponible") and len(_spied(game_client)) == 1
    assert game_client.get("/api/automation").json()["status"]["launched_today"] == 0


def test_read_state_shows_the_galaxy_targets(game_client, game):
    _spy(game_client, enabled=False)
    data = game_client.post("/api/automation/read-state").json()["galaxy"]
    assert data["alliance"] == "REPCO"
    by = {t["player"]: t for t in data["targets"]}
    assert set(by) == {"Kairo-Vigie-378", "Bryn-Socle-176", "Splif", "Hades33", "Torek-Veine-165"}
    assert by["Kairo-Vigie-378"]["errant"] and by["Kairo-Vigie-378"]["in_range"] and not by["Torek-Veine-165"]["in_range"]
    assert game.actions() == []
