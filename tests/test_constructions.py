"""Constructions : catalogue des 16 bâtiments, liste ordonnée « bâtiment + niveau cible », toutes les planètes ou
les planètes cochées."""

import asyncio
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.constructions import BUILDINGS, ConstructionError, ConstructionSettings, describe, validate
from app.features import FEATURES
from app.main import create_app

LINE = {"building": "mine-de-fer", "level": 45}


# ------------------------------------------------------------------------------ catalogue
def test_catalog_has_the_sixteen_game_buildings_split_in_two_pages():
    assert len(BUILDINGS) == 16 and len({b.id for b in BUILDINGS}) == 16
    assert [b.page for b in BUILDINGS].count("Industrie") == 8
    assert [b.page for b in BUILDINGS].count("Infrastructures") == 8
    labels = {b.label for b in BUILDINGS}
    assert {"Mine de Fer", "Synthétiseur d'Éther", "Centrale Énergétique", "Cuve d'Éther", "Tour à Résonance"} <= labels


# ------------------------------------------------------------------------------ validation
def test_defaults_are_all_planets_and_an_empty_list():
    assert validate({}) == {"planets": [], "queue": []}


def test_the_list_keeps_its_order_which_is_the_priority():
    queue = [{"building": "terraformeur", "level": 12}, LINE, {"building": "caserne", "level": 25}]
    assert [line["building"] for line in validate({"queue": queue})["queue"]] == ["terraformeur", "mine-de-fer", "caserne"]


@pytest.mark.parametrize(
    "queue",
    [
        [{"building": "inconnu", "level": 3}],
        [LINE, LINE],  # une seule ligne par bâtiment
        [{"building": "mine-de-fer", "level": 0}],
        [{"building": "mine-de-fer", "level": 501}],
        [{"building": "mine-de-fer", "level": 4.5}],
        [{"building": "mine-de-fer", "level": True}],
        [{"building": "mine-de-fer", "level": "45"}],
        [{"level": 5}],
        ["mine-de-fer"],
    ],
)
def test_bad_lines_are_refused_with_a_message(queue):
    with pytest.raises(ConstructionError) as err:
        validate({"queue": queue})
    assert err.value.errors[0].startswith("Ligne ")


def test_chosen_planets_must_be_known_from_the_scan():
    known = ["8-50-Concorde", "6-79-Egide"]
    with pytest.raises(ConstructionError, match="inconnue"):
        validate({"planets": ["8-50-Concorde", "Nulle-Part"]}, known_planets=known)
    assert validate({"planets": ["8-50-Concorde", "6-79-Egide", "8-50-Concorde"]}, known_planets=known)["planets"] == known  # doublon retiré
    assert validate({"planets": []}, known_planets=known)["planets"] == []  # « toutes » : toujours valable
    for bad in ("8-50-Concorde", [12], [""]):
        with pytest.raises(ConstructionError, match="Planètes"):
            validate({"planets": bad})


def test_the_old_all_or_one_planet_format_is_still_read():
    assert validate({"scope": "one", "planet": "8-50-Concorde"})["planets"] == ["8-50-Concorde"]
    assert validate({"scope": "all", "planet": "8-50-Concorde"})["planets"] == []  # ignorée quand « toutes »
    with pytest.raises(ConstructionError, match="Planètes"):
        validate({"scope": "one"})  # « une seule » sans planète


def test_describe_gives_a_readable_sentence():
    assert describe(validate({})) == "aucune ligne de construction réglée"
    text = describe(validate({"planets": ["8-50-Concorde", "6-79-Egide"], "queue": [LINE]}))
    assert "Mine de Fer niveau 45" in text and "sur 8-50-Concorde, 6-79-Egide" in text
    assert "sur toutes les planètes" in describe(validate({"queue": [LINE]}))


# ------------------------------------------------------------------------------ enregistrement
def test_settings_survive_a_restart(tmp_path):
    ConstructionSettings(str(tmp_path)).replace({"planets": ["Aurore-Bis", "6-79-Egide"], "queue": [LINE]})
    again = ConstructionSettings(str(tmp_path)).get()
    assert again == {"planets": ["Aurore-Bis", "6-79-Egide"], "queue": [LINE]}


def test_a_file_in_the_old_format_is_converted(tmp_path):
    (tmp_path / "constructions.json").write_text(json.dumps({"scope": "one", "planet": "Aurore-Bis", "queue": [LINE]}), encoding="utf8")
    assert ConstructionSettings(str(tmp_path)).get() == {"planets": ["Aurore-Bis"], "queue": [LINE]}


def test_a_refused_change_keeps_the_previous_settings(tmp_path):
    store = ConstructionSettings(str(tmp_path))
    store.replace({"queue": [LINE]})
    with pytest.raises(ConstructionError):
        store.replace({"queue": [{"building": "x", "level": 1}]})
    assert store.get()["queue"] == [LINE]


def test_a_corrupt_file_gives_an_empty_list(tmp_path):
    (tmp_path / "constructions.json").write_text("{pas du json", encoding="utf8")
    assert ConstructionSettings(str(tmp_path)).get() == {"planets": [], "queue": []}


# ------------------------------------------------------------------------------ API
@pytest.fixture
def api(settings, tmp_path):
    with TestClient(create_app(replace(settings, data_dir=str(tmp_path)))) as c:
        yield c


def test_api_lists_the_catalog_and_saves_the_list(api):
    data = api.get("/api/constructions").json()
    assert len(data["buildings"]) == 16 and data["settings"]["queue"] == []
    res = api.put("/api/constructions", json={"planets": [], "queue": [LINE, {"building": "caserne", "level": 25}]})
    assert res.status_code == 200
    assert [line["building"] for line in api.get("/api/constructions").json()["settings"]["queue"]] == ["mine-de-fer", "caserne"]


def test_api_refuses_a_bad_list_in_french_and_checks_the_origin(api):
    res = api.put("/api/constructions", json={"queue": [{"building": "x", "level": 3}]})
    assert res.status_code == 422 and "bâtiment inconnu" in " ".join(res.json()["detail"])
    assert api.put("/api/constructions", json={"planets": [""]}).status_code == 422
    assert api.put("/api/constructions", json={}, headers={"Origin": "http://evil.example"}).status_code == 403


def test_chosen_planets_must_come_from_the_scan(game_client):
    game_client.post("/api/planets/scan")
    planets = game_client.get("/api/constructions").json()["planets"]
    assert "8-50-Concorde" in planets
    assert game_client.put("/api/constructions", json={"planets": ["8-50-Concorde", "6-79-Egide"]}).status_code == 200
    assert game_client.put("/api/constructions", json={"planets": ["Nulle-Part"]}).status_code == 422


def test_the_construction_feature_with_an_empty_list_does_nothing(api):
    feature = next(f for f in FEATURES if f.id == "constructions")
    assert "rien à faire" in asyncio.run(feature.run(api.app.state.bot))
