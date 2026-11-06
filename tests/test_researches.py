"""Recherches : catalogue du Laboratoire, liste ordonnée « recherche + niveau cible », planète chargée des recherches."""

import asyncio
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.features import FEATURES
from app.main import create_app
from app.researches import RESEARCHES, ResearchError, ResearchSettings, describe, validate

LINE = {"research": "technologie-laser", "level": 30}
PLANET = "6-79-Egide"


# ------------------------------------------------------------------------------ catalogue
def test_catalog_has_the_game_researches_split_in_two_tabs():
    assert len(RESEARCHES) == 23 and len({r.id for r in RESEARCHES}) == 23
    assert [r.tab for r in RESEARCHES].count("Civil") == 11
    assert [r.tab for r in RESEARCHES].count("Militaire") == 12
    labels = {r.label for r in RESEARCHES}
    assert {"Ordinateur Quantique", "Technologie Laser", "Technologie d’Espionnage", "Champ de Force des Piliers"} <= labels


# ------------------------------------------------------------------------------ validation
def test_defaults_are_no_planet_and_an_empty_list():
    assert validate({}) == {"planet": "", "queue": []}


def test_the_list_keeps_its_order_which_is_the_priority():
    queue = [{"research": "technologie-plasma", "level": 20}, LINE, {"research": "ordinateur-quantique", "level": 25}]
    data = validate({"planet": PLANET, "queue": queue})
    assert [line["research"] for line in data["queue"]] == ["technologie-plasma", "technologie-laser", "ordinateur-quantique"]


@pytest.mark.parametrize(
    "queue",
    [
        [{"research": "inconnue", "level": 3}],
        [LINE, LINE],  # une seule ligne par recherche
        [{"research": "technologie-laser", "level": 0}],
        [{"research": "technologie-laser", "level": 501}],
        [{"research": "technologie-laser", "level": 4.5}],
        [{"research": "technologie-laser", "level": True}],
        [{"research": "technologie-laser", "level": "30"}],
        [{"level": 5}],
        ["technologie-laser"],
    ],
)
def test_bad_lines_are_refused_with_a_message(queue):
    with pytest.raises(ResearchError) as err:
        validate({"planet": PLANET, "queue": queue})
    assert err.value.errors[0].startswith("Ligne ")


def test_a_list_needs_the_research_planet_and_the_planet_must_be_known():
    with pytest.raises(ResearchError, match="Choisissez la planète chargée des recherches"):
        validate({"queue": [LINE]})
    assert validate({"planet": PLANET}) == {"planet": PLANET, "queue": []}  # planète seule : acceptée
    with pytest.raises(ResearchError, match="inconnue"):
        validate({"planet": "Nulle-Part", "queue": [LINE]}, known_planets=[PLANET])
    assert validate({"planet": PLANET, "queue": [LINE]}, known_planets=[PLANET])["planet"] == PLANET
    with pytest.raises(ResearchError):
        validate({"planet": 12, "queue": []})


def test_describe_gives_a_readable_sentence():
    assert describe(validate({})) == "aucune ligne de recherche réglée"
    text = describe(validate({"planet": PLANET, "queue": [LINE]}))
    assert "Technologie Laser niveau 30" in text and PLANET in text


# ------------------------------------------------------------------------------ enregistrement
def test_settings_survive_a_restart(tmp_path):
    ResearchSettings(str(tmp_path)).replace({"planet": PLANET, "queue": [LINE]})
    assert ResearchSettings(str(tmp_path)).get() == {"planet": PLANET, "queue": [LINE]}


def test_a_refused_change_keeps_the_previous_settings(tmp_path):
    store = ResearchSettings(str(tmp_path))
    store.replace({"planet": PLANET, "queue": [LINE]})
    with pytest.raises(ResearchError):
        store.replace({"planet": PLANET, "queue": [{"research": "x", "level": 1}]})
    assert store.get()["queue"] == [LINE]


def test_a_corrupt_file_gives_an_empty_list(tmp_path):
    (tmp_path / "researches.json").write_text("{pas du json", encoding="utf8")
    assert ResearchSettings(str(tmp_path)).get() == {"planet": "", "queue": []}


# ------------------------------------------------------------------------------ API
@pytest.fixture
def api(settings, tmp_path):
    with TestClient(create_app(replace(settings, data_dir=str(tmp_path)))) as c:
        yield c


def test_api_lists_the_catalog_and_saves_the_list(api):
    data = api.get("/api/researches").json()
    assert len(data["researches"]) == 23 and data["settings"] == {"planet": "", "queue": []}
    res = api.put("/api/researches", json={"planet": PLANET, "queue": [LINE, {"research": "ordinateur-quantique", "level": 25}]})
    assert res.status_code == 200
    saved = api.get("/api/researches").json()["settings"]
    assert saved["planet"] == PLANET and [line["research"] for line in saved["queue"]] == ["technologie-laser", "ordinateur-quantique"]


def test_api_refuses_a_bad_list_in_french_and_checks_the_origin(api):
    res = api.put("/api/researches", json={"planet": PLANET, "queue": [{"research": "x", "level": 3}]})
    assert res.status_code == 422 and "recherche inconnue" in " ".join(res.json()["detail"])
    assert api.put("/api/researches", json={"queue": [LINE]}).status_code == 422  # pas de planète
    assert api.put("/api/researches", json={}, headers={"Origin": "http://evil.example"}).status_code == 403


def test_the_research_planet_must_come_from_the_scan(game_client):
    game_client.post("/api/planets/scan")
    assert PLANET in game_client.get("/api/researches").json()["planets"]
    assert game_client.put("/api/researches", json={"planet": PLANET, "queue": [LINE]}).status_code == 200
    assert game_client.put("/api/researches", json={"planet": "Nulle-Part", "queue": [LINE]}).status_code == 422


def test_the_research_feature_with_an_empty_list_does_nothing(api):
    feature = next(f for f in FEATURES if f.id == "researches")
    assert "rien à faire" in asyncio.run(feature.run(api.app.state.bot))
