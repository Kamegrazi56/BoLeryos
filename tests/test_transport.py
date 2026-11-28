"""Transport des ressources manquantes pour une construction : réglages, calcul du manque, envoi par Piliers ou Flotte."""

import time

import pytest

from app.transporter import HUMAN_EXTRA_SECONDS, TRANSIT_FALLBACK_SECONDS, parse_travel, transit_wait
from app.transport_ships import fit_cargo, parse_available, parse_volume, pick_ships
from app.transports import DISPLAY_SLACK, TransportError, TransportSettings, shortfall, validate, with_margin

CONCORDE, EGIDE, STRATE = "8-50-Concorde", "6-79-Egide", "9-169-Strate"
# Concorde (58 M de Fer, 22,36 M de Cristal) ne peut pas payer ce niveau 41 : il manque 143,765 M de Fer et 28,081 M de Cristal.
MINE_CONCORDE = {"building": "mine-de-fer", "level": 41, "planet": CONCORDE}
BASE = {"enabled": True, "donors": [EGIDE], "method": "PILLARS", "models": ["Expe"], "ships": [], "margin": 0}
TESTO, LENTUS = "Testo Civil Transpo Destroy Def", ">Lentus"


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


def test_ships_serve_the_spatial_way_and_models_the_pillar_way():
    assert validate({**BASE, "ships": [TESTO]})["ships"] == [TESTO]
    flight = validate({**BASE, "method": "FLEET", "models": ["Expe"], "ships": [TESTO]})
    assert flight["ships"] == [TESTO] and flight["models"] == []  # les modèles enregistrés ne servent pas en voie Flotte


def test_the_flight_way_needs_ships_not_models():
    with pytest.raises(TransportError) as err:
        validate({**BASE, "method": "FLEET", "models": ["Expe"]})
    assert "vaisseau" in str(err.value)
    assert validate({**BASE, "method": "FLEET", "models": [], "ships": [TESTO]})["enabled"] is True


def test_an_old_flight_setting_without_ships_is_disabled_at_loading(tmp_path):
    import json
    before = {k: v for k, v in BASE.items() if k != "ships"}  # le fichier d'avant n'avait pas de clé « ships »
    (tmp_path / "transport.json").write_text(json.dumps({**before, "method": "FLEET", "fleet_models": ["Planète minière"]}), encoding="utf8")
    old = TransportSettings(str(tmp_path)).get()
    assert old["enabled"] is False and old["method"] == "FLEET" and old["ships"] == []
    (tmp_path / "transport.json").write_text(json.dumps({**before, "fleet_models": ["Planète minière"]}), encoding="utf8")
    assert TransportSettings(str(tmp_path)).get()["enabled"] is True  # voie Piliers : seule la liste de secours disparaît


# ------------------------------------------------------------------------------ vaisseaux
def test_the_hold_is_read_from_the_pages_text():
    assert parse_volume("2 968 928 m³") == 2968928
    assert parse_volume("Espace libre : 2 968 448 m³") == 2968448
    assert parse_volume("—") is None and parse_volume(None) is None


def test_the_available_ships_are_read_from_their_rows():
    rows = [{"name": ">Lentus", "available": "6"}, {"name": "Trasnpo civil cuir", "available": "1 583"}, {"name": "Inconnu", "available": ""}]
    assert parse_available(rows) == {">Lentus": 6, "Trasnpo civil cuir": 1583, "Inconnu": 0}


def test_ships_are_taken_in_order_up_to_what_is_available():
    available, hold = {"A": 2, "B": 10}, {"A": 100, "B": 30}
    assert pick_ships(["A", "B"], available, hold, 230) == ({"A": 2, "B": 1}, 230)  # A d'abord (2 × 100), puis B pour le reste (30)
    assert pick_ships(["A", "B"], available, hold, 50) == ({"A": 1}, 100)
    assert pick_ships(["B", "A"], available, hold, 61) == ({"B": 3}, 90)
    assert pick_ships(["A", "C"], {"A": 0, "C": 5}, {"A": 100}, 10) == ({}, 0)  # indisponible ou soute inconnue : sauté


def test_the_cargo_is_reduced_when_the_ships_cannot_carry_it_all():
    assert fit_cargo({"Fer": 600, "Cristal": 400}, 2000) == {"Fer": 600, "Cristal": 400}
    assert fit_cargo({"Fer": 600, "Cristal": 400}, 500) == {"Fer": 300, "Cristal": 200}
    assert fit_cargo({"Fer": 1, "Cristal": 400}, 100) == {"Cristal": 99}


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
    _set(game_client, ships=[TESTO])
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
    _set(game_client, ships=[TESTO])
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


# ------------------------------------------------------------------------------ relevé et envoi par vaisseaux
def _scan_ships(client):
    _scan(client)
    _set(client)
    res = client.post("/api/transport/scan", json={"planets": [EGIDE]})
    assert res.status_code == 200 and res.json()["error"] == "", res.text
    return res.json()["ships"]


def test_the_ships_of_a_donor_are_read_with_their_hold(game_client):
    ships = _scan_ships(game_client)
    by_name = {s["name"]: s for s in ships["planets"][EGIDE]["ships"]}
    assert by_name[TESTO]["available"] == 43 and by_name[TESTO]["cargo"] == 10**9 and by_name[TESTO]["free"] == 10**9 - 480
    assert by_name[LENTUS]["available"] == 2 and by_name[LENTUS]["cargo"] == 10**8
    assert ships["names"] == [LENTUS, TESTO]
    assert game_client.get("/api/transport").json()["ships"]["planets"][EGIDE]["ships"] == ships["planets"][EGIDE]["ships"]


def test_scanning_without_a_donor_says_so(game_client):
    _scan(game_client)
    assert "donneuse" in game_client.post("/api/transport/scan", json={"planets": []}).json()["error"]
    assert "donneuse" in game_client.post("/api/transport/scan", json={"planets": ["Inconnue"]}).json()["error"]


def test_the_flight_way_sends_the_number_of_ships_the_cargo_needs(game_client, game):
    _scan_ships(game_client)
    assert game_client.put("/api/constructions", json={"planets": [], "queue": [MINE_CONCORDE]}).status_code == 200
    _set(game_client, method="FLEET", models=[], ships=[TESTO])
    _run(game_client)
    [sent] = _transports(game)
    assert sent["method"] == "FLEET" and sent["planet"] == EGIDE and sent["units"] == {TESTO: 1}
    assert sent["cargo"]["Fer"] == 143_765_000 + DISPLAY_SLACK


def test_more_ships_are_sent_when_one_cannot_carry_everything(game_client, game):
    """Lentus porte 100 M : il en faut 2 pour les 143,8 M de Fer et 28,1 M de Cristal (172 M en tout)."""
    _scan_ships(game_client)
    assert game_client.put("/api/constructions", json={"planets": [], "queue": [MINE_CONCORDE]}).status_code == 200
    _set(game_client, method="FLEET", models=[], ships=[LENTUS])
    _run(game_client)
    [sent] = _transports(game)
    assert sent["units"] == {LENTUS: 2}


def test_a_ship_never_scanned_is_measured_on_the_page_before_the_send(game_client, game):
    _scan(game_client)
    assert game_client.put("/api/constructions", json={"planets": [], "queue": [MINE_CONCORDE]}).status_code == 200
    _set(game_client, method="FLEET", models=[], ships=[TESTO])  # aucun relevé : la soute est lue sur la page
    _run(game_client)
    [sent] = _transports(game)
    assert sent["units"] == {TESTO: 1}
