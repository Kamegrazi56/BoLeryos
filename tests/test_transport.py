"""Transport des ressources manquantes pour une construction : réglages, calcul du manque, envoi par Piliers ou Flotte."""

import time

import pytest

from app.transporter import HUMAN_EXTRA_SECONDS, TRANSIT_FALLBACK_SECONDS, parse_travel, transit_wait
from app.transports import DISPLAY_SLACK, TransportError, shortfall, validate, with_margin

CONCORDE, EGIDE, STRATE = "8-50-Concorde", "6-79-Egide", "9-169-Strate"
# Concorde (58 M de Fer, 22,36 M de Cristal) ne peut pas payer ce niveau 41 : il manque 143,765 M de Fer et 28,081 M de Cristal.
MINE_CONCORDE = {"building": "mine-de-fer", "level": 41, "planet": CONCORDE}
BASE = {"enabled": True, "donors": [EGIDE], "method": "PILLARS", "models": ["Expe"], "fleet_models": [], "margin": 0}


def _bot(client):
    return client.app.state.bot


def _scan(client):
    assert len(client.post("/api/planets/scan").json()["planets"]) == 3


def _set(client, **patch):
    res = client.put("/api/transport", json={**BASE, **patch})
    assert res.status_code == 200, res.text


def _run(client):
    return client.portal.call(_bot(client).builder.run_pass)


def _transports(game):
    return [a for a in game.actions() if a["kind"] == "transport"]


def _built(game):
    return [(a["planet"], a["name"], a["level_after"]) for a in game.actions() if a["kind"] == "building"]


# ------------------------------------------------------------------------------ calcul
def test_the_shortfall_is_what_is_missing_right_now():
    costs = {"Fer": 200.0, "Cristal": 50.0, "Éther": None}
    assert shortfall(costs, {"Fer": 150.0, "Cristal": 80.0, "Éther": 0.0}) == {"Fer": 50.0}
    assert shortfall(costs, {"Fer": 150.0, "Cristal": None}) == {"Fer": 50.0}  # stock illisible : on ne devine pas
    assert shortfall(costs, {"Fer": 500.0, "Cristal": 80.0}) == {}


def test_the_margin_is_added_in_percent_with_the_display_slack():
    assert with_margin({"Fer": 1000.0}, 0) == {"Fer": 1000 + DISPLAY_SLACK}
    assert with_margin({"Fer": 1000.0, "Cristal": 0.0}, 10.5) == {"Fer": 1105 + DISPLAY_SLACK}


def test_the_flight_time_is_read_in_the_games_formats():
    assert parse_travel("00:37:14") == 2234.0
    assert parse_travel("1j 02:03:04") == 86400 + 7384
    assert parse_travel("37m 14s") == 2234.0
    assert parse_travel("—") is None and parse_travel("00:00:00") is None and parse_travel(None) is None


def test_the_wait_is_the_flight_time_plus_30_seconds_to_5_minutes():
    low, high = HUMAN_EXTRA_SECONDS
    assert (low, high) == (30.0, 300.0)
    waits = [transit_wait(2234.0) for _ in range(200)]
    assert all(2234 + low <= w <= 2234 + high for w in waits) and len(set(waits)) > 1
    assert all(TRANSIT_FALLBACK_SECONDS + low <= transit_wait(None) <= TRANSIT_FALLBACK_SECONDS + high for _ in range(50))


# ------------------------------------------------------------------------------ réglages
def test_the_margin_is_chosen_in_steps_of_half_a_percent():
    assert validate({**BASE, "margin": 2.5})["margin"] == 2.5
    for bad in (0.3, -1, 50.5, True, "5"):
        with pytest.raises(TransportError):
            validate({**BASE, "margin": bad})


def test_an_enabled_option_needs_donors_and_models():
    for patch, word in (({"donors": []}, "donneuse"), ({"models": []}, "modèle")):
        with pytest.raises(TransportError) as err:
            validate({**BASE, **patch})
        assert word in str(err.value)
    assert validate({**BASE, "enabled": False, "donors": [], "models": []})["enabled"] is False


def test_the_spatial_spare_models_only_matter_for_the_pillar_way():
    assert validate({**BASE, "fleet_models": ["Planète minière"]})["fleet_models"] == ["Planète minière"]
    assert validate({**BASE, "method": "FLEET", "fleet_models": ["Planète minière"]})["fleet_models"] == []


def test_unknown_planets_and_models_are_refused_when_the_lists_are_known():
    with pytest.raises(TransportError):
        validate(BASE, known_planets=[CONCORDE])
    with pytest.raises(TransportError):
        validate(BASE, known_planets=[EGIDE], known_models={"PILLARS": ["Autre"], "FLEET": []})


def test_the_settings_are_saved_through_the_api(game_client):
    assert game_client.get("/api/transport").json()["settings"]["enabled"] is False
    _set(game_client, margin=1.5)
    assert game_client.get("/api/transport").json()["settings"]["margin"] == 1.5
    assert game_client.put("/api/transport", json={**BASE, "margin": 0.3}).status_code == 422
    assert game_client.put("/api/transport", json={**BASE, "donors": []}).status_code == 422


# ------------------------------------------------------------------------------ envoi
def test_nothing_is_sent_when_the_option_is_off(game_client, game):
    _scan(game_client)
    assert game_client.put("/api/constructions", json={"planets": [], "queue": [MINE_CONCORDE]}).status_code == 200
    _run(game_client)
    assert _transports(game) == [] and _built(game) == []


def test_pillars_send_what_is_missing_then_the_building_starts_at_once(game_client, game):
    _scan(game_client)
    assert game_client.put("/api/constructions", json={"planets": [], "queue": [MINE_CONCORDE]}).status_code == 200
    _set(game_client)
    summary = _run(game_client)
    [sent] = _transports(game)
    assert (sent["planet"], sent["target"], sent["method"]) == (EGIDE, CONCORDE, "PILLARS")
    assert sent["cargo"]["Fer"] == 143_765_000 + DISPLAY_SLACK and sent["cargo"]["Cristal"] == 28_081_000 + DISPLAY_SLACK
    assert "Éther" not in sent["cargo"]
    assert _built(game) == [(CONCORDE, "Mine de Fer", 41)]
    assert "1 construction(s) lancée(s)" in summary


def test_the_margin_adds_to_what_is_sent(game_client, game):
    _scan(game_client)
    assert game_client.put("/api/constructions", json={"planets": [], "queue": [MINE_CONCORDE]}).status_code == 200
    _set(game_client, margin=10)
    _run(game_client)
    [sent] = _transports(game)
    assert sent["cargo"]["Fer"] == round(143_765_000 * 1.1) + DISPLAY_SLACK


def test_closed_pillars_fall_back_to_the_spatial_way_and_wait_for_the_arrival(game_client, game):
    _scan(game_client)
    assert game_client.put("/api/constructions", json={"planets": [], "queue": [MINE_CONCORDE]}).status_code == 200
    game.set_pillars(planets={EGIDE: {"closed": True}})
    _bot(game_client).pillars.set_status({EGIDE: {"closed": True}})
    _set(game_client, fleet_models=["Planète minière"])
    summary = _run(game_client)
    [sent] = _transports(game)
    assert sent["method"] == "FLEET" and sent["planet"] == EGIDE and sent["target"] == CONCORDE
    assert _built(game) == [] and "0 construction(s) lancée(s)" in summary  # les ressources n'arrivent pas tout de suite
    waiting = _bot(game_client).transporter._until[CONCORDE] - time.monotonic()
    assert 2234 + 30 - 5 <= waiting <= 2234 + 300 + 1  # temps de vol lu sur la page (00:37:14) + 30 s à 5 min
    _run(game_client)
    assert len(_transports(game)) == 1  # pas de deuxième envoi tant que le premier est en route


def test_pillars_refused_by_the_game_also_fall_back_to_the_spatial_way(game_client, game):
    """L'état des Piliers n'a jamais été relevé : c'est la page du jeu (lancement grisé) qui dit qu'ils sont fermés."""
    _scan(game_client)
    assert game_client.put("/api/constructions", json={"planets": [], "queue": [MINE_CONCORDE]}).status_code == 200
    game.set_pillars(planets={EGIDE: {"closed": True}})
    _set(game_client, fleet_models=["Planète minière"])
    _run(game_client)
    assert [t["method"] for t in _transports(game)] == ["FLEET"]


def test_a_donor_with_closed_pillars_and_no_spatial_model_is_skipped(game_client, game):
    _scan(game_client)
    assert game_client.put("/api/constructions", json={"planets": [], "queue": [MINE_CONCORDE]}).status_code == 200
    game.set_pillars(planets={EGIDE: {"closed": True}})
    _bot(game_client).pillars.set_status({EGIDE: {"closed": True}})
    _set(game_client)
    _run(game_client)
    assert _transports(game) == [] and _built(game) == []
    events = " ".join(e["msg"] for e in game_client.get("/api/events").json())
    assert "Piliers fermés" in events and "rien n'a pu être envoyé" in events


def test_the_next_donor_completes_what_the_first_could_not_send(game_client, game):
    """Strate (7,3 M de Fer) n'a pas de quoi payer seule : Egide envoie le reste."""
    _scan(game_client)
    assert game_client.put("/api/constructions", json={"planets": [], "queue": [MINE_CONCORDE]}).status_code == 200
    _set(game_client, donors=[STRATE, EGIDE])
    _run(game_client)
    assert [t["planet"] for t in _transports(game)] == [STRATE, EGIDE]
    assert sum(t["cargo"]["Fer"] for t in _transports(game)) == 143_765_000 + DISPLAY_SLACK
    assert _built(game) == [(CONCORDE, "Mine de Fer", 41)]
