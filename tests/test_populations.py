"""Population : catalogue des 10 populations recrutables, lignes « mode + quantité + planètes », une ligne par
(population, planètes)."""

import asyncio
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.features import FEATURES
from app.main import create_app
from app.populations import POPULATIONS, PopulationError, PopulationSettings, describe, validate

FIXED = {"population": "pilote", "mode": "fixed", "amount": 50, "planets": [], "repeat": True}
PLANET = "6-79-Egide"
OTHER = "8-50-Concorde"


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
        {"population": "mineur", "mode": "max", "amount": 99, "planets": []},
        {"population": "rempart-de-fer", "mode": "target", "amount": 20000, "planets": [PLANET]},
    ]
    lines = validate({"queue": queue}, known_planets=[PLANET])["queue"]
    assert [(ln["mode"], ln["amount"]) for ln in lines] == [("fixed", 50), ("max", 0), ("target", 20000)]


def test_the_list_keeps_its_order_which_is_the_priority():
    queue = [{**FIXED, "population": "mineur"}, FIXED, {**FIXED, "population": "technicien"}]
    assert [ln["population"] for ln in validate({"queue": queue})["queue"]] == ["mineur", "pilote", "technicien"]


def test_a_line_can_target_several_planets():
    line = validate({"queue": [{**FIXED, "planets": [PLANET, OTHER, PLANET]}]}, known_planets=[PLANET, OTHER])["queue"][0]
    assert line["planets"] == [PLANET, OTHER]  # doublon retiré, ordre gardé


def test_the_same_population_can_have_one_line_per_set_of_planets_but_not_two_for_the_same_one():
    both = [{**FIXED, "planets": [PLANET]}, FIXED]
    assert len(validate({"queue": both})["queue"]) == 2
    with pytest.raises(PopulationError, match="déjà dans la liste"):
        validate({"queue": [FIXED, FIXED]})
    with pytest.raises(PopulationError, match="déjà dans la liste"):  # mêmes planètes dans un autre ordre
        validate({"queue": [{**FIXED, "planets": [PLANET, OTHER]}, {**FIXED, "planets": [OTHER, PLANET]}]})


def test_the_old_single_planet_format_is_still_read():
    old = [{"population": "pilote", "mode": "fixed", "amount": 5, "planet": PLANET}, {"population": "mineur", "mode": "max", "planet": ""}]
    assert [ln["planets"] for ln in validate({"queue": old})["queue"]] == [[PLANET], []]


@pytest.mark.parametrize(
    "line",
    [
        {"population": "inconnue", "mode": "fixed", "amount": 5, "planets": []},
        {"population": "pilote", "mode": "souvent", "amount": 5, "planets": []},
        {"population": "pilote", "mode": "fixed", "amount": 0, "planets": []},
        {"population": "pilote", "mode": "target", "amount": -3, "planets": []},
        {"population": "pilote", "mode": "fixed", "amount": 4.5, "planets": []},
        {"population": "pilote", "mode": "fixed", "amount": True, "planets": []},
        {"population": "pilote", "mode": "fixed", "amount": "5", "planets": []},
        {"population": "pilote", "mode": "fixed", "amount": 5_000_000_000, "planets": []},
        {"population": "pilote", "mode": "fixed", "amount": 5, "planets": [12]},
        {"population": "pilote", "mode": "fixed", "amount": 5, "planets": "6-79-Egide"},
        {"population": "pilote", "mode": "fixed", "amount": 5, "planet": 12},
        {"population": "pilote", "mode": "fixed", "amount": 5, "planets": [], "repeat": "non"},
        "pilote",
    ],
)
def test_bad_lines_are_refused_with_a_message(line):
    with pytest.raises(PopulationError) as err:
        validate({"queue": [line]})
    assert err.value.errors[0].startswith("Ligne 1")


def test_a_planet_must_be_known_when_the_scan_exists():
    with pytest.raises(PopulationError, match="planète inconnue"):
        validate({"queue": [{**FIXED, "planets": [PLANET, "Nulle-Part"]}]}, known_planets=[PLANET])
    assert validate({"queue": [{**FIXED, "planets": [PLANET]}]}, known_planets=[PLANET])["queue"][0]["planets"] == [PLANET]
    assert validate({"queue": [FIXED]}, known_planets=[PLANET])["queue"][0]["planets"] == []  # « toutes » : toujours valable


def test_describe_gives_a_readable_sentence():
    assert describe(validate({})) == "aucune ligne de population réglée"
    text = describe(validate({"queue": [FIXED, {"population": "mineur", "mode": "max", "planets": [PLANET, OTHER]}]}))
    assert "Pilote : recruter 50 à chaque passage sur toutes les planètes" in text
    assert "Mineur : MAX à chaque passage sur 6-79-Egide, 8-50-Concorde" in text
    once = describe(validate({"queue": [{**FIXED, "repeat": False}]}))
    assert "recruter 50 une seule fois" in once


# ------------------------------------------------------------------------------ enregistrement
def test_settings_survive_a_restart(tmp_path):
    PopulationSettings(str(tmp_path)).replace({"queue": [FIXED]})
    assert PopulationSettings(str(tmp_path)).get() == {"queue": [FIXED]}


def test_a_file_in_the_old_format_is_converted_and_keeps_what_was_done(tmp_path):
    old = {"population": "pilote", "mode": "fixed", "amount": 5, "planet": PLANET, "repeat": False}
    (tmp_path / "populations.json").write_text(json.dumps({"queue": [old], "done": {f"pilote|{PLANET}|fixed|5": [PLANET]}}), encoding="utf8")
    store = PopulationSettings(str(tmp_path))
    line = store.get()["queue"][0]
    assert line["planets"] == [PLANET] and "planet" not in line
    assert store.is_done(line, PLANET)  # déjà fait avant la mise à jour : pas recommencé


def test_a_refused_change_keeps_the_previous_settings(tmp_path):
    store = PopulationSettings(str(tmp_path))
    store.replace({"queue": [FIXED]})
    with pytest.raises(PopulationError):
        store.replace({"queue": [{"population": "x", "mode": "fixed", "amount": 1, "planets": []}]})
    assert store.get()["queue"] == [FIXED]


def test_a_corrupt_file_gives_an_empty_list(tmp_path):
    (tmp_path / "populations.json").write_text("{pas du json", encoding="utf8")
    assert PopulationSettings(str(tmp_path)).get() == {"queue": []}


def test_repeat_defaults_to_every_pass_and_is_always_off_for_a_target():
    assert validate({"queue": [{k: v for k, v in FIXED.items() if k != "repeat"}]})["queue"][0]["repeat"] is True
    target = {"population": "pilote", "mode": "target", "amount": 9, "planets": [], "repeat": False}
    assert validate({"queue": [target]})["queue"][0]["repeat"] is False  # un effectif à atteindre ne se répète pas : il est retiré une fois atteint
    assert validate({"queue": [{**target, "repeat": True}]})["queue"][0]["repeat"] is False


def test_a_once_line_is_done_per_planet_until_it_is_edited_and_survives_a_restart(tmp_path):
    once = {**FIXED, "repeat": False}
    store = PopulationSettings(str(tmp_path))
    store.replace({"queue": [once]})
    assert not store.is_done(once, PLANET)
    store.mark_done(once, PLANET)
    assert store.is_done(once, PLANET) and not store.is_done(once, OTHER)
    assert PopulationSettings(str(tmp_path)).is_done(once, PLANET)
    store.replace({"queue": [{**once, "amount": 60}]})  # quantité modifiée : la ligne repart de zéro
    assert not store.is_done({**once, "amount": 60}, PLANET)
    every = {**FIXED}
    store.replace({"queue": [every]})
    store.mark_done(every, PLANET)
    assert not store.is_done(every, PLANET)  # « à chaque passage » n'est jamais « fait »


def test_a_once_line_on_several_planets_is_dropped_when_done_on_each_of_them(tmp_path):
    once = {**FIXED, "planets": [PLANET, OTHER], "repeat": False}
    store = PopulationSettings(str(tmp_path))
    store.replace({"queue": [once]})
    store.mark_done(once, PLANET)
    assert store.drop_done([PLANET, OTHER, "1-146-Elyria"]) == []  # pas encore faite sur OTHER
    store.mark_done(once, OTHER)
    assert store.drop_done([PLANET, OTHER, "1-146-Elyria"]) == [once]  # les autres planètes ne comptent pas
    assert store.get()["queue"] == []


# ------------------------------------------------------------------------------ API
@pytest.fixture
def api(settings, tmp_path):
    with TestClient(create_app(replace(settings, data_dir=str(tmp_path)))) as c:
        yield c


def test_api_lists_the_catalog_and_saves_the_list(api):
    data = api.get("/api/populations").json()
    assert len(data["populations"]) == 10 and data["settings"] == {"queue": []}
    assert api.put("/api/populations", json={"queue": [FIXED, {"population": "mineur", "mode": "max", "planets": []}]}).status_code == 200
    saved = api.get("/api/populations").json()["settings"]["queue"]
    assert [(ln["population"], ln["mode"]) for ln in saved] == [("pilote", "fixed"), ("mineur", "max")]


def test_api_refuses_a_bad_list_in_french_and_checks_the_origin(api):
    res = api.put("/api/populations", json={"queue": [{"population": "x", "mode": "fixed", "amount": 3, "planets": []}]})
    assert res.status_code == 422 and "population inconnue" in " ".join(res.json()["detail"])
    assert api.put("/api/populations", json={"queue": [{**FIXED, "amount": 0}]}).status_code == 422
    assert api.put("/api/populations", json={}, headers={"Origin": "http://evil.example"}).status_code == 403


def test_a_planet_must_come_from_the_scan(game_client):
    game_client.post("/api/planets/scan")
    assert PLANET in game_client.get("/api/populations").json()["planets"]
    assert game_client.put("/api/populations", json={"queue": [{**FIXED, "planets": [PLANET]}]}).status_code == 200
    assert game_client.put("/api/populations", json={"queue": [{**FIXED, "planets": ["Nulle-Part"]}]}).status_code == 422


def test_the_population_feature_with_an_empty_list_does_nothing(api):
    feature = next(f for f in FEATURES if f.id == "populations")
    assert "rien à faire" in asyncio.run(feature.run(api.app.state.bot))
