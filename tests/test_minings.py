"""Forages : réglages, suivi des trois phases (aller, extraction, retour), choix de la planète minière, API."""

import pytest

from app.miner import pick_site, sync_minings
from app.minings import MiningError, MiningStore, validate

EGIDE = "6-79-Egide"
SETTINGS = {"planets": [EGIDE], "resources": ["fer"], "miners": 200000, "slots": 2, "delay_min": 0, "delay_max": 0}


def test_valid_settings_and_defaults():
    assert validate(SETTINGS) == SETTINGS
    assert validate({})["planets"] == [] and validate({})["miners"] == 200000


@pytest.mark.parametrize(
    "patch,message",
    [({"resources": []}, "ressource"), ({"planets": []}, "planète de départ"), ({"resources": ["or"]}, "Ressources"),
     ({"miners": 0}, "Mineurs"), ({"miners": 200001}, "Mineurs"), ({"slots": 0}, "Places"), ({"delay_min": None}, "Délai"),
     ({"delay_min": 30, "delay_max": 5}, "dépasse")],
)
def test_invalid_settings_are_refused(patch, message):
    with pytest.raises(MiningError, match=message):
        validate({**SETTINGS, **patch})


def test_settings_and_tracked_are_saved(tmp_path):
    store = MiningStore(str(tmp_path))
    store.replace(SETTINGS)
    store.set_tracked([{"planet": EGIDE, "site": "M68 18 22", "resource": "fer", "phase": "aller", "end": None, "ready": None, "delay": 0}])
    again = MiningStore(str(tmp_path))
    assert again.get() == SETTINGS and again.tracked()[0]["site"] == "M68 18 22"


def _card(kind, origin, destination, left=None, extracting=False):
    return {"kind": kind, "origin": origin, "destination": destination, "left": left, "extracting": extracting}


GOING = _card("Forage", {"name": EGIDE, "address": "M69 38 06"}, {"name": "Planete miniere", "address": "M68 18 22"}, left=600)
DIGGING = {**GOING, "left": None, "extracting": True}
BACK = _card("Retour", {"name": "Inconnue", "address": "M68 18 22"}, {"name": EGIDE, "address": "M69 38 06"}, left=300)
TRACKED = {"planet": EGIDE, "site": "M68 18 22", "resource": "fer", "phase": "aller", "end": None, "ready": None, "delay": 120.0}


def test_the_three_phases_of_a_forage():
    out, done = sync_minings([TRACKED], [GOING], SETTINGS, now=1000.0)
    assert out[0]["phase"] == "aller" and out[0]["ready"] is None and done == []
    out, _ = sync_minings(out, [DIGGING], SETTINGS, now=2000.0)
    assert out[0]["phase"] == "extraction" and out[0]["end"] is None  # fin inconnue : soute pleine ou planète épuisée
    out, _ = sync_minings(out, [BACK], SETTINGS, now=3000.0)
    assert (out[0]["phase"], out[0]["end"], out[0]["ready"]) == ("retour", 3300.0, 3420.0)
    out, done = sync_minings(out, [], SETTINGS, now=3350.0)  # rentrée : place libre après le délai
    assert out[0]["phase"] == "terminé" and out[0]["ready"] == 3420.0
    out, done = sync_minings(out, [], SETTINGS, now=3500.0)
    assert out == [] and done[0]["site"] == "M68 18 22"


def test_a_forage_launched_by_hand_is_adopted_but_not_an_other_fleet_return():
    other_return = _card("Retour", {"name": "Inconnue", "address": "M79 50 37"}, {"name": EGIDE, "address": "M69 38 06"}, left=100)
    out, _ = sync_minings([], [DIGGING, other_return], SETTINGS, now=0.0)
    assert [(t["site"], t["phase"]) for t in out] == [("M68 18 22", "extraction")]


OBJECTS = [
    {"kind": "PLANET", "address": "M69 38 06", "positionMilli": [11570000, 1544000]},
    {"kind": "MINING", "address": "M68 18 22", "miningResourceType": "fer", "positionMilli": [11500000, 1600000]},
    {"kind": "MINING", "address": "M78 16 44", "miningResourceType": "fer", "positionMilli": [11650000, 1650000]},
    {"kind": "MINING", "address": "M70 00 01", "miningResourceType": "cristal", "positionMilli": [11600000, 1500000]},
]


def test_the_nearest_mining_planet_of_a_chosen_resource_without_forage():
    assert pick_site(OBJECTS, "M69 38 06", None, ["fer"], set(), []) == {"address": "M68 18 22", "resource": "fer"}
    assert pick_site(OBJECTS, "M69 38 06", None, ["fer", "cristal"], set(), [])["address"] == "M70 00 01"
    assert pick_site(OBJECTS, "M69 38 06", None, ["fer"], {"M68 18 22"}, [])["address"] == "M78 16 44"  # déjà forée
    assert pick_site(OBJECTS, "M69 38 06", None, ["fer"], set(), ["M78 16 44"])["address"] == "M78 16 44"  # celle d'avant
    assert pick_site(OBJECTS, "M69 38 06", None, ["ether"], set(), []) is None


def test_the_api_saves_and_refuses(client):
    assert client.get("/api/minings").json()["resources"] == {"fer": "Fer", "cristal": "Cristal", "ether": "Éther"}
    assert client.put("/api/minings", json={**SETTINGS, "planets": []}).status_code == 422
    assert client.put("/api/minings", json=SETTINGS, headers={"Origin": "https://ailleurs.example"}).status_code == 403
