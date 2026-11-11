"""Places de mission : places gardées libres, priorités entre Fouilles, Explorations, Forages et Piratage, vérifiées dans le
faux jeu avant chaque lancement (compteur « Missions actives X / Y » commun au compte)."""

import pytest

from app.slots import SlotError, SlotSettings, need, validate

EGIDE, CONCORDE = "6-79-Egide", "8-50-Concorde"
TEAM = {"Scientifique": 2500, "Mineur": 2500}
FOUILLES = {"duration": 6, "units": TEAM, "copies": 1, "fouilles": [], "planets": [{"name": EGIDE, "copies": 2}],
            "per_slot": False, "mira": "", "delay_min": 0, "delay_max": 0}
EXPLO = {"method": "PILLARS", "composition": "model", "model": "Expe", "units": {}, "copies": 1, "duration": 1,
         "planets": [{"name": CONCORDE, "copies": 1}], "per_planet": False}


def _bot(client):
    return client.app.state.bot


def _scan(client):
    assert len(client.post("/api/planets/scan").json()["planets"]) == 3


def _enable(client, feature: str, on: bool = True):
    body = {"enabled": on} if feature in ("excavations", "hackings", "minings") else {"enabled": on, "min_minutes": 1, "max_minutes": 2}
    assert client.put(f"/api/features/{feature}", json=body).status_code == 200


def _slots(client, reserve=0, order=("excavations", "explorations", "minings", "hackings")):
    assert client.put("/api/slots", json={"reserve": reserve, "order": list(order)}).status_code == 200


def _launched(game, kind):
    return [a for a in game.actions() if a["kind"] == kind]


# ------------------------------------------------------------------------------ réglages
def test_settings_are_checked_and_saved(tmp_path):
    assert validate({}) == {"reserve": 0, "order": ["excavations", "explorations", "minings", "hackings"]}
    with pytest.raises(SlotError, match="garder libres"):
        validate({"reserve": -1})
    with pytest.raises(SlotError, match="Priorités"):
        validate({"order": ["excavations", "excavations", "minings", "hackings"]})
    store = SlotSettings(str(tmp_path))
    store.replace({"reserve": 3, "order": ["hackings", "minings", "explorations", "excavations"]})
    store.set_seen(12, 29)
    again = SlotSettings(str(tmp_path))
    assert again.get()["reserve"] == 3 and again.get()["order"][0] == "hackings" and again.seen()["max"] == 29


def test_the_api_lists_the_needs_and_refuses_bad_settings(game_client):
    data = game_client.get("/api/slots").json()
    assert data["labels"]["excavations"] == "Fouilles" and data["needs"] == {f: 0 for f in data["settings"]["order"]}
    assert game_client.put("/api/slots", json={"reserve": 2, "order": ["x"]}).status_code == 422


# ------------------------------------------------------------------------------ dans le jeu
def test_the_places_kept_free_stop_the_excavations(game_client, game):
    _scan(game_client)
    game.set_exc(missions_max=4)  # 2 explorations actives au départ : 2 / 4
    _slots(game_client, reserve=1)
    assert game_client.put("/api/excavations", json=FOUILLES).status_code == 200
    summary = game_client.portal.call(_bot(game_client).excavator.run_pass)
    assert len(_launched(game, "excavation")) == 1  # 4 − 2 − 1 gardée libre = 1 seule place
    assert "plus de place de mission (missions actives 3/4, 1 gardée(s) libre(s))" in summary
    seen = _bot(game_client).slots.seen()
    assert (seen["active"], seen["max"]) == (3, 4)


def test_a_higher_priority_keeps_its_places_for_later(game_client, game):
    _scan(game_client)
    game.set_exc(missions_max=4)  # 2 / 4 : il reste 2 places, que les Fouilles (priorité 1, 2 réglées, aucune en cours) réservent
    assert game_client.put("/api/excavations", json=FOUILLES).status_code == 200
    _enable(game_client, "excavations")
    assert game_client.put("/api/explorations", json=EXPLO).status_code == 200
    summary = game_client.portal.call(_bot(game_client).explorer.run_pass)
    assert _launched(game, "exploration") == []
    assert "réservées aux priorités au-dessus : Fouilles 2" in summary
    _slots(game_client, order=("explorations", "excavations", "minings", "hackings"))  # l'Exploration passe devant
    game_client.portal.call(_bot(game_client).explorer.run_pass)
    assert len(_launched(game, "exploration")) == 1


def test_a_disabled_feature_or_an_exhausted_exploration_quota_reserves_nothing(game_client, game):
    _scan(game_client)
    game.set_exc(missions_max=4)
    assert game_client.put("/api/excavations", json=FOUILLES).status_code == 200
    _enable(game_client, "excavations", False)  # Fouilles désactivées : elles ne réservent rien
    assert game_client.put("/api/explorations", json=EXPLO).status_code == 200
    game_client.portal.call(_bot(game_client).explorer.run_pass)
    assert len(_launched(game, "exploration")) == 1
    # quota d'exploration du jour épuisé : les Explorations ne réservent plus rien pour les fonctionnalités en dessous
    _enable(game_client, "explorations")
    assert need(_bot(game_client), "explorations") == 1
    _bot(game_client).explorer.last_counters = {"daily": [46, 46], "active": [3, 10]}
    assert need(_bot(game_client), "explorations") == 0


def test_an_exploration_needs_one_place_per_copy(game_client, game):
    _scan(game_client)
    game.set_exc(missions_max=4)  # 2 / 4 : 2 places
    planets = [{"name": CONCORDE, "copies": 3}, {"name": EGIDE, "copies": 2}]
    assert game_client.put("/api/explorations", json={**EXPLO, "planets": planets}).status_code == 200
    game_client.portal.call(_bot(game_client).explorer.run_pass)
    # Concorde (3 copies) n'a pas assez de places : sautée ; Egide (2 copies) part
    assert [(a["planet"], a["copies"]) for a in _launched(game, "exploration")] == [(EGIDE, 2)]


def test_hacking_and_mining_wait_for_a_free_place(game_client, game):
    _scan(game_client)
    game.set_exc(missions_max=2)  # 2 / 2 : plus aucune place
    assert game_client.put("/api/hackings", json={"planet": EGIDE, "delay_min": 0, "delay_max": 0}).status_code == 200
    game_client.post("/api/hackings/scan")
    assert "plus de place de mission" in game_client.portal.call(_bot(game_client).hacker.run_pass)
    assert _launched(game, "hack") == []
    assert game_client.put("/api/minings", json={"planets": [EGIDE], "resources": ["fer"], "miners": 1000, "slots": 1,
                                                 "delay_min": 0, "delay_max": 0}).status_code == 200
    assert "plus de place de mission" in game_client.portal.call(_bot(game_client).miner.run_pass)
    assert _launched(game, "mining") == []
