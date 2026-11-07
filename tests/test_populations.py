"""Population : catalogue des 10 populations recrutables, lignes « mode + quantité + planète », une ligne par (population, planète)."""

import asyncio
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.features import FEATURES
from app.main import create_app
from app.populations import POPULATIONS, PopulationError, PopulationSettings, describe, validate

FIXED = {"population": "pilote", "mode": "fixed", "amount": 50, "planet": "", "repeat": True}
PLANET = "6-79-Egide"


# ------------------------------------------------------------------------------ catalogue
def test_catalog_has_the_ten_recruitable_populations_on_three_tabs():
    assert len(POPULATIONS) == 10 and len({p.id for p in POPULATIONS}) == 10
    assert [p.tab for p in POPULATIONS].count("Assaut") == 4
    assert [p.tab for p in POPULATIONS].count("Logistique") == 3
    assert [p.tab for p in POPULATIONS].count("Administration") == 3
    assert "Soldat" not in {p.label for p in POPULATIONS}  # produit automatiquement par la Caserne


# ------------------------------------------------------------------------------ validation
def test_defaults_are_an_empty_list():
    assert validate({}) == {"queue": []}


def test_the_three_modes_are_accepted_and_max_ignores_the_amount():
    queue = [
        FIXED,
        {"population": "mineur", "mode": "max", "amount": 99, "planet": ""},
        {"population": "rempart-de-fer", "mode": "target", "amount": 20000, "planet": PLANET},
    ]
    lines = validate({"queue": queue}, known_planets=[PLANET])["queue"]
    assert [(ln["mode"], ln["amount"]) for ln in lines] == [("fixed", 50), ("max", 0), ("target", 20000)]


def test_the_list_keeps_its_order_which_is_the_priority():
    queue = [{**FIXED, "population": "mineur"}, FIXED, {**FIXED, "population": "technicien"}]
    assert [ln["population"] for ln in validate({"queue": queue})["queue"]] == ["mineur", "pilote", "technicien"]


def test_the_same_population_can_have_one_line_per_planet_but_not_two_for_the_same_one():
    both = [{**FIXED, "planet": PLANET}, {**FIXED, "planet": ""}]
    assert len(validate({"queue": both})["queue"]) == 2
    with pytest.raises(PopulationError, match="déjà dans la liste"):
        validate({"queue": [FIXED, FIXED]})


@pytest.mark.parametrize(
    "line",
    [
        {"population": "inconnue", "mode": "fixed", "amount": 5, "planet": ""},
        {"population": "pilote", "mode": "souvent", "amount": 5, "planet": ""},
        {"population": "pilote", "mode": "fixed", "amount": 0, "planet": ""},
        {"population": "pilote", "mode": "target", "amount": -3, "planet": ""},
        {"population": "pilote", "mode": "fixed", "amount": 4.5, "planet": ""},
        {"population": "pilote", "mode": "fixed", "amount": True, "planet": ""},
        {"population": "pilote", "mode": "fixed", "amount": "5", "planet": ""},
        {"population": "pilote", "mode": "fixed", "amount": 5_000_000_000, "planet": ""},
        {"population": "pilote", "mode": "fixed", "amount": 5, "planet": 12},
        {"population": "pilote", "mode": "fixed", "amount": 5, "planet": "", "repeat": "non"},
        "pilote",
    ],
)
def test_bad_lines_are_refused_with_a_message(line):
    with pytest.raises(PopulationError) as err:
        validate({"queue": [line]})
    assert err.value.errors[0].startswith("Ligne 1")


def test_a_planet_must_be_known_when_the_scan_exists():
    with pytest.raises(PopulationError, match="planète inconnue"):
        validate({"queue": [{**FIXED, "planet": "Nulle-Part"}]}, known_planets=[PLANET])
    assert validate({"queue": [{**FIXED, "planet": PLANET}]}, known_planets=[PLANET])["queue"][0]["planet"] == PLANET
    assert validate({"queue": [FIXED]}, known_planets=[PLANET])["queue"][0]["planet"] == ""  # « toutes » : toujours valable


def test_describe_gives_a_readable_sentence():
    assert describe(validate({})) == "aucune ligne de population réglée"
    text = describe(validate({"queue": [FIXED, {"population": "mineur", "mode": "max", "planet": PLANET}]}))
    assert "Pilote : recruter 50 à chaque passage sur toutes les planètes" in text and "Mineur : MAX à chaque passage sur 6-79-Egide" in text
    once = describe(validate({"queue": [{**FIXED, "repeat": False}]}))
    assert "recruter 50 une seule fois" in once


# ------------------------------------------------------------------------------ enregistrement
def test_settings_survive_a_restart(tmp_path):
    PopulationSettings(str(tmp_path)).replace({"queue": [FIXED]})
    assert PopulationSettings(str(tmp_path)).get() == {"queue": [FIXED]}


def test_a_refused_change_keeps_the_previous_settings(tmp_path):
    store = PopulationSettings(str(tmp_path))
    store.replace({"queue": [FIXED]})
    with pytest.raises(PopulationError):
        store.replace({"queue": [{"population": "x", "mode": "fixed", "amount": 1, "planet": ""}]})
    assert store.get()["queue"] == [FIXED]


def test_a_corrupt_file_gives_an_empty_list(tmp_path):
    (tmp_path / "populations.json").write_text("{pas du json", encoding="utf8")
    assert PopulationSettings(str(tmp_path)).get() == {"queue": []}


def test_repeat_defaults_to_every_pass_and_is_always_off_for_a_target():
    assert validate({"queue": [{k: v for k, v in FIXED.items() if k != "repeat"}]})["queue"][0]["repeat"] is True
    target = {"population": "pilote", "mode": "target", "amount": 9, "planet": "", "repeat": False}
    assert validate({"queue": [target]})["queue"][0]["repeat"] is False  # un effectif à atteindre ne se répète pas : il est retiré une fois atteint
    assert validate({"queue": [{**target, "repeat": True}]})["queue"][0]["repeat"] is False


def test_a_once_line_is_done_per_planet_until_it_is_edited_and_survives_a_restart(tmp_path):
    once = {**FIXED, "repeat": False}
    store = PopulationSettings(str(tmp_path))
    store.replace({"queue": [once]})
    assert not store.is_done(once, PLANET)
    store.mark_done(once, PLANET)
    assert store.is_done(once, PLANET) and not store.is_done(once, "8-50-Concorde")
    assert PopulationSettings(str(tmp_path)).is_done(once, PLANET)
    store.replace({"queue": [{**once, "amount": 60}]})  # quantité modifiée : la ligne repart de zéro
    assert not store.is_done({**once, "amount": 60}, PLANET)
    every = {**FIXED}
    store.replace({"queue": [every]})
    store.mark_done(every, PLANET)
    assert not store.is_done(every, PLANET)  # « à chaque passage » n'est jamais « fait »


# ------------------------------------------------------------------------------ API
@pytest.fixture
def api(settings, tmp_path):
    with TestClient(create_app(replace(settings, data_dir=str(tmp_path)))) as c:
        yield c


def test_api_lists_the_catalog_and_saves_the_list(api):
    data = api.get("/api/populations").json()
    assert len(data["populations"]) == 10 and data["settings"] == {"queue": []}
    assert api.put("/api/populations", json={"queue": [FIXED, {"population": "mineur", "mode": "max", "planet": ""}]}).status_code == 200
    saved = api.get("/api/populations").json()["settings"]["queue"]
    assert [(ln["population"], ln["mode"]) for ln in saved] == [("pilote", "fixed"), ("mineur", "max")]


def test_api_refuses_a_bad_list_in_french_and_checks_the_origin(api):
    res = api.put("/api/populations", json={"queue": [{"population": "x", "mode": "fixed", "amount": 3, "planet": ""}]})
    assert res.status_code == 422 and "population inconnue" in " ".join(res.json()["detail"])
    assert api.put("/api/populations", json={"queue": [{**FIXED, "amount": 0}]}).status_code == 422
    assert api.put("/api/populations", json={}, headers={"Origin": "http://evil.example"}).status_code == 403


def test_a_planet_must_come_from_the_scan(game_client):
    game_client.post("/api/planets/scan")
    assert PLANET in game_client.get("/api/populations").json()["planets"]
    assert game_client.put("/api/populations", json={"queue": [{**FIXED, "planet": PLANET}]}).status_code == 200
    assert game_client.put("/api/populations", json={"queue": [{**FIXED, "planet": "Nulle-Part"}]}).status_code == 422


def test_the_population_feature_with_an_empty_list_does_nothing(api):
    feature = next(f for f in FEATURES if f.id == "populations")
    assert "rien à faire" in asyncio.run(feature.run(api.app.state.bot))
