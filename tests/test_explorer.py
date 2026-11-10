"""Exploration dans le jeu : relevé du jour (brèche la plus proche, modèles) puis lancements, planète par planète, dans les quotas."""

from app import builder
from app.explorer import formula_distance, nearest_from_objects, parse_address

CONCORDE, EGIDE, STRATE = "8-50-Concorde", "6-79-Egide", "9-169-Strate"
BASE = {"method": "PILLARS", "composition": "model", "model": "Expe", "units": {}, "copies": 1, "duration": 1,
        "planets": [CONCORDE], "per_planet": False}


def _bot(client):
    return client.app.state.bot


def _scan(client):
    assert len(client.post("/api/planets/scan").json()["planets"]) == 3


def _set(client, **patch):
    """Une planète donnée par son nom prend `copies` expéditions simultanées (comme « toutes les planètes »)."""
    body = {**BASE, **patch}
    body["planets"] = [{"name": p, "copies": body["copies"]} if isinstance(p, str) else p for p in body["planets"]]
    res = client.put("/api/explorations", json=body)
    assert res.status_code == 200, res.text


def _run(client):
    return client.portal.call(_bot(client).explorer.run_pass)


def _launched(game):
    return [a for a in game.actions() if a["kind"] == "exploration"]


def _forbidden(game):
    return [a for a in game.actions() if a["kind"] == "forbidden"]


# ------------------------------------------------------------------------------ positions
def test_an_address_is_a_zone_a_row_a_column_then_a_position_in_the_cell():
    assert parse_address("M69 38 06") == ("M", 6, 9, 38, 6)
    assert parse_address("rien") is None


def test_real_positions_choose_the_nearest_breach():
    objects = [
        {"kind": "PLANET", "address": "M69 38 06", "positionMilli": [11570000, 1544000]},
        {"kind": "BREACH", "address": "M69 99 99", "positionMilli": [12900000, 1600000]},
        {"kind": "BREACH", "address": "M79 50 37", "positionMilli": [11629000, 2112000]},
        {"kind": "PVE_PLANET", "address": "M68 18 22", "positionMilli": [10931000, 1611000]},
    ]
    found = nearest_from_objects(objects, "M69 38 06", None)
    assert found["address"] == "M79 50 37" and found["source"] == "carte" and round(found["distance"]) == 571
    # sans la planète dans la fenêtre : le centre de la fenêtre (centrée sur elle) sert de repère
    window = [[11070000, 1044000], [12070000, 2044000]]
    assert nearest_from_objects(objects[1:], None, window)["address"] == "M79 50 37"
    assert nearest_from_objects(objects[:1], "M69 38 06", window) is None


def test_the_formula_only_compares_addresses_of_the_same_zone():
    # vraies positions relevées autour d'Egide : M69 27 31 à 132 unités, M79 50 37 à 568 — la formule garde cet ordre
    assert formula_distance("M69 38 06", "M69 27 31") < formula_distance("M69 38 06", "M79 50 37")
    assert formula_distance("N94 17 48", "K03 18 09") is None  # disposition des lettres inconnue


# ------------------------------------------------------------------------------ relevé du jour
def test_the_daily_record_finds_the_nearest_breach_of_each_planet_and_the_models(game_client, game):
    _scan(game_client)
    _set(game_client, planets=[])
    daily = game_client.post("/api/explorations/scan").json()["daily"]
    assert daily["breaches"][CONCORDE]["address"] == "Z25 60 80" and daily["breaches"][CONCORDE]["source"] == "carte"
    assert daily["breaches"][EGIDE]["address"] == "M79 50 37"
    assert STRATE not in daily["breaches"] and STRATE in daily["error"]  # aucune brèche autour de Strate
    assert [m["name"] for m in daily["models"]["PILLARS"]] == ["Expe", "300k-5k"]
    assert daily["models"]["PILLARS"][0]["units"] == 1700 and [m["name"] for m in daily["models"]["FLEET"]] == ["Planète minière"]
    assert _launched(game) == [] and _forbidden(game) == []  # le relevé ne lance rien et ne supprime aucun modèle


def test_without_map_positions_the_formula_chooses_among_the_listed_breaches(game_client, game):
    _scan(game_client)
    game.set_exp(map_api=False)
    daily = game_client.post("/api/explorations/scan").json()["daily"]
    found = daily["breaches"][CONCORDE]
    assert (found["address"], found["x"], found["source"]) == ("Z26 10 20", None, "formule")


# ------------------------------------------------------------------------------ lancements
def test_nothing_is_launched_without_a_composition(game_client, game):
    assert "composition non réglée" in _run(game_client)
    assert game.actions() == []


def test_a_model_exploration_goes_to_the_nearest_breach_with_the_chosen_copies_and_duration(game_client, game):
    _scan(game_client)
    _set(game_client, copies=2, duration=3)
    assert "1 lancement(s) : 2 exploration(s) Piliers depuis 8-50-Concorde vers Z25 60 80" in _run(game_client)
    (action,) = _launched(game)
    assert action["units"] == {"Rempart de Fer": 1500, "Grand Transporteur": 200}
    assert (action["method"], action["target"], action["copies"], action["duration"]) == ("PILLARS", "Z25 60 80", 2, 3)
    assert _bot(game_client).explorations.daily()["breaches"][CONCORDE]["address"] == "Z25 60 80"  # relevé fait au premier passage
    assert _forbidden(game) == []


def test_manual_quantities_by_fleet(game_client, game):
    _scan(game_client)
    _set(game_client, method="FLEET", composition="manual", units={">Lentus": 1}, planets=[EGIDE])
    _run(game_client)
    (action,) = _launched(game)
    assert (action["planet"], action["method"], action["target"], action["units"]) == (EGIDE, "FLEET", "M79 50 37", {">Lentus": 1})


def test_a_planet_missing_units_or_a_breach_is_skipped_and_the_next_one_is_served(game_client, game):
    _scan(game_client)
    game.set_exp(units={"0": {"PILLARS": {"Rempart de Fer": 10}}})  # Concorde n'a plus assez de Remparts
    _set(game_client, planets=[STRATE, CONCORDE, EGIDE])  # Strate : aucune brèche proche
    result = _run(game_client)
    assert "2 planète(s) sautée(s)" in result
    assert [a["planet"] for a in _launched(game)] == [EGIDE]


def test_the_daily_quota_and_the_active_places_are_respected(game_client, game):
    _scan(game_client)
    game.set_exp(daily=45, daily_max=46)
    _set(game_client, copies=2)
    assert "1 planète(s) sautée(s)" in _run(game_client)  # 2 expéditions demandées, 1 seule restante : planète sautée
    assert _launched(game) == []
    assert any("il en reste 1 dans le quota quotidien" in e["msg"] for e in _bot(game_client).events(20))
    game.set_exp(daily=46, daily_max=46)
    assert "quota quotidien atteint (46/46)" in _run(game_client)  # plus rien : le passage s'arrête
    game.set_exp(daily=3, active=9, active_max=10)  # une seule place active libre
    _set(game_client, copies=1, planets=[{"name": CONCORDE, "copies": 2}, {"name": EGIDE, "copies": 1}])
    result = _run(game_client)
    assert "1 lancement(s)" in result and "1 planète(s) sautée(s)" in result  # Concorde en voulait 2 : sautée, Egide servie
    assert [(a["planet"], a["copies"]) for a in _launched(game)] == [(EGIDE, 1)]
    game.set_exp(daily=3, active=10, active_max=10)
    assert "plus de place pour une exploration active (10/10)" in _run(game_client)


def test_a_planet_chosen_after_the_daily_record_gets_its_breach_at_the_next_pass_once(game_client, game):
    _scan(game_client)
    _set(game_client)
    _run(game_client)  # relevé du jour : Concorde seule
    assert set(_bot(game_client).explorations.daily()["breaches"]) == {CONCORDE}
    _set(game_client, planets=[CONCORDE, EGIDE, STRATE])  # Egide et Strate ajoutées dans la journée
    _run(game_client)
    daily = _bot(game_client).explorations.daily()
    assert daily["breaches"][EGIDE]["address"] == "M79 50 37" and STRATE not in daily["breaches"]  # aucune brèche autour de Strate
    assert EGIDE in [a["planet"] for a in _launched(game)]
    events = len(_bot(game_client).events(200))
    _run(game_client)
    assert not any("brèches relevées" in e["msg"] for e in _bot(game_client).events(200)[events:])  # Strate n'est pas recherchée à chaque passage


def test_one_launch_per_planet_with_its_own_number_of_simultaneous_expeditions(game_client, game):
    _scan(game_client)
    _set(game_client, planets=[{"name": CONCORDE, "copies": 3}, {"name": EGIDE, "copies": 1}])
    _run(game_client)
    assert [(a["planet"], a["copies"]) for a in _launched(game)] == [(CONCORDE, 3), (EGIDE, 1)]


def test_each_planet_can_have_its_own_send(game_client, game):
    _scan(game_client)
    fleet = {"method": "FLEET", "composition": "manual", "model": "", "units": {">Lentus": 1}, "duration": 2}
    _set(game_client, per_planet=True, duration=3,
         planets=[{"name": CONCORDE, "copies": 2}, {"name": EGIDE, "copies": 1, "send": fleet}])
    _run(game_client)
    concorde, egide = _launched(game)
    assert (concorde["planet"], concorde["method"], concorde["copies"], concorde["duration"]) == (CONCORDE, "PILLARS", 2, 3)
    assert (egide["planet"], egide["method"], egide["units"], egide["duration"]) == (EGIDE, "FLEET", {">Lentus": 1}, 2)


def test_two_unconfirmed_launches_disable_the_feature(game_client, game, monkeypatch):
    monkeypatch.setattr(builder, "CONFIRM_SECONDS", 0.6)
    _scan(game_client)
    game.set_exp(no_effect=True)  # le clic passe mais le compteur ne bouge pas
    game_client.put("/api/features/explorations", json={"enabled": True, "min_minutes": 1, "max_minutes": 2})
    _set(game_client, planets=[CONCORDE, EGIDE])  # deux lancements dans le passage
    assert "désactivée" in _run(game_client)
    assert _bot(game_client).scheduler.store.get("explorations")["enabled"] is False
