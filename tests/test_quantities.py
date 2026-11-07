"""Chantier spatial : lignes « élément + mode + quantité + planètes » (Assemblages et Composants), enregistrement et API."""

import asyncio
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.components import COMPONENTS, SPEC as COMPONENT_SPEC
from app.features import FEATURES
from app.main import create_app
from app.quantities import LineError, LineSettings, LineSpec, describe, validate

SHIPS = LineSpec("ships.json", "modèle")
EGIDE, CONCORDE, STRATE = "6-79-Egide", "8-50-Concorde", "9-169-Strate"
FIXED = {"item": "Anode", "mode": "fixed", "amount": 5, "planets": [], "repeat": True}


# ------------------------------------------------------------------------------ catalogue
def test_catalog_has_the_37_components_on_six_categories():
    assert len(COMPONENTS) == 37 and len({c.id for c in COMPONENTS}) == 37
    by_category = {}
    for c in COMPONENTS:
        by_category[c.category] = by_category.get(c.category, 0) + 1
    assert by_category == {"Infrastructures": 11, "Hangars": 3, "Armement": 8, "Blindages": 4, "Boucliers": 3, "Propulsion": 8}


# ------------------------------------------------------------------------------ validation
def test_defaults_are_an_empty_list():
    assert validate(SHIPS, {}) == {"queue": []}


def test_the_three_modes_and_the_repeat_choice_are_accepted():
    queue = [
        FIXED,
        {"item": "Plasmatique", "mode": "max", "amount": 99, "planets": [EGIDE], "repeat": False},
        {"item": "Anode ", "mode": "target", "amount": 60, "planets": [EGIDE, STRATE], "repeat": True},
    ]
    lines = validate(SHIPS, {"queue": queue}, known_planets=[EGIDE, CONCORDE, STRATE])["queue"]
    assert [(ln["mode"], ln["amount"], ln["repeat"]) for ln in lines] == [("fixed", 5, True), ("max", 0, False), ("target", 60, False)]
    assert lines[2]["planets"] == [EGIDE, STRATE] and lines[2]["item"] == "Anode"


def test_planets_are_all_one_or_several_never_a_blank_name():
    assert validate(SHIPS, {"queue": [FIXED]})["queue"][0]["planets"] == []
    two = validate(SHIPS, {"queue": [{**FIXED, "planets": [EGIDE, EGIDE, CONCORDE]}]})["queue"][0]["planets"]
    assert two == [EGIDE, CONCORDE]  # les doublons disparaissent
    for bad in ([" "], [12], [EGIDE, ""], "Egide"):
        with pytest.raises(LineError):
            validate(SHIPS, {"queue": [{**FIXED, "planets": bad}]})


def test_a_planet_must_be_known_when_the_scan_exists():
    with pytest.raises(LineError, match="planète inconnue"):
        validate(SHIPS, {"queue": [{**FIXED, "planets": ["Nulle-Part"]}]}, known_planets=[EGIDE])


def test_a_model_must_be_known_when_a_scan_exists_but_a_component_must_be_in_the_catalog():
    with pytest.raises(LineError, match="scan des modèles"):
        validate(SHIPS, {"queue": [FIXED]}, known_items=["Plasmatique"])
    assert validate(SHIPS, {"queue": [FIXED]}, known_items=[])["queue"]  # aucun scan : on accepte
    with pytest.raises(LineError, match="composant inconnu"):
        validate(COMPONENT_SPEC, {"queue": [{**FIXED, "item": "Rayon de la mort"}]})
    assert validate(COMPONENT_SPEC, {"queue": [{**FIXED, "item": "chasseur"}]})["queue"]


def test_the_same_item_can_have_several_lines_for_different_planet_sets_only():
    ok = [{**FIXED, "planets": [EGIDE]}, {**FIXED, "planets": []}, {**FIXED, "planets": [CONCORDE, STRATE]}]
    assert len(validate(SHIPS, {"queue": ok})["queue"]) == 3
    with pytest.raises(LineError, match="déjà dans la liste"):
        validate(SHIPS, {"queue": [{**FIXED, "planets": [EGIDE, CONCORDE]}, {**FIXED, "planets": [CONCORDE, EGIDE]}]})


@pytest.mark.parametrize(
    "line",
    [
        {**FIXED, "item": ""},
        {**FIXED, "item": 7},
        {**FIXED, "mode": "souvent"},
        {**FIXED, "amount": 0},
        {**FIXED, "amount": -2},
        {**FIXED, "amount": 4.5},
        {**FIXED, "amount": True},
        {**FIXED, "amount": 5_000_000_000},
        {**FIXED, "repeat": "oui"},
        "Anode",
    ],
)
def test_bad_lines_are_refused_with_a_message(line):
    with pytest.raises(LineError) as err:
        validate(SHIPS, {"queue": [line]})
    assert err.value.errors[0].startswith("Ligne 1")


def test_describe_gives_a_readable_sentence():
    assert describe({"queue": []}) == "aucune ligne réglée"
    text = describe(validate(SHIPS, {"queue": [FIXED, {**FIXED, "item": "Plasmatique", "mode": "max", "planets": [EGIDE, STRATE], "repeat": False}]}))
    assert "Anode : produire 5 à chaque passage sur toutes les planètes" in text
    assert f"Plasmatique : MAX une seule fois sur {EGIDE}, {STRATE}" in text


# ------------------------------------------------------------------------------ enregistrement et « fait »
def test_settings_survive_a_restart(tmp_path):
    LineSettings(str(tmp_path), SHIPS).replace({"queue": [FIXED]})
    assert LineSettings(str(tmp_path), SHIPS).get() == {"queue": [FIXED]}


def test_a_refused_change_keeps_the_previous_settings_and_a_corrupt_file_gives_an_empty_list(tmp_path):
    store = LineSettings(str(tmp_path), SHIPS)
    store.replace({"queue": [FIXED]})
    with pytest.raises(LineError):
        store.replace({"queue": [{**FIXED, "mode": "x"}]})
    assert store.get()["queue"] == [FIXED]
    (tmp_path / "ships.json").write_text("{pas du json", encoding="utf8")
    assert LineSettings(str(tmp_path), SHIPS).get() == {"queue": []}


def test_a_once_line_is_done_per_planet_survives_a_restart_and_is_reset_when_edited(tmp_path):
    once = {**FIXED, "repeat": False}
    store = LineSettings(str(tmp_path), SHIPS)
    store.replace({"queue": [once]})
    store.mark_done(once, EGIDE)
    assert store.is_done(once, EGIDE) and not store.is_done(once, CONCORDE)
    assert LineSettings(str(tmp_path), SHIPS).is_done(once, EGIDE)
    store.replace({"queue": [{**once, "amount": 6}]})
    assert not store.is_done({**once, "amount": 6}, EGIDE)
    store.replace({"queue": [FIXED]})
    store.mark_done(FIXED, EGIDE)
    assert not store.is_done(FIXED, EGIDE)  # « à chaque passage » n'est jamais « faite »


def test_a_line_leaves_the_list_only_when_done_on_all_its_planets_and_a_loop_line_never_does(tmp_path):
    store = LineSettings(str(tmp_path), SHIPS)
    once_two = {**FIXED, "repeat": False, "planets": [EGIDE, STRATE]}
    once_all = {**FIXED, "item": "Plasmatique", "repeat": False}
    loop = {**FIXED, "item": "Vecteur"}
    store.replace({"queue": [once_two, once_all, loop]})
    planets = [CONCORDE, EGIDE, STRATE]
    store.mark_done(once_two, EGIDE)
    store.mark_done(once_all, EGIDE)
    assert store.drop_done(planets) == []
    store.mark_done(once_two, STRATE)
    assert [ln["item"] for ln in store.drop_done(planets)] == ["Anode"]
    store.mark_done(once_all, CONCORDE)
    store.mark_done(once_all, STRATE)
    store.mark_done(loop, CONCORDE)
    assert [ln["item"] for ln in store.drop_done(planets)] == ["Plasmatique"]
    assert [ln["item"] for ln in store.get()["queue"]] == ["Vecteur"]


# ------------------------------------------------------------------------------ API
@pytest.fixture
def api(settings, tmp_path):
    with TestClient(create_app(replace(settings, data_dir=str(tmp_path)))) as c:
        yield c


def test_api_saves_and_lists_ships_and_components(api):
    assert api.get("/api/ships").json()["settings"] == {"queue": []}
    line = {"item": "Anode", "mode": "fixed", "amount": 3, "planets": [EGIDE], "repeat": False}
    assert api.put("/api/ships", json={"queue": [line]}).status_code == 200
    assert api.get("/api/ships").json()["settings"]["queue"] == [line]
    data = api.get("/api/components").json()
    assert len(data["components"]) == 37
    comp = {"item": "canon-laser", "mode": "max", "planets": [], "repeat": True}
    assert api.put("/api/components", json={"queue": [comp]}).status_code == 200
    assert api.get("/api/components").json()["settings"]["queue"][0]["item"] == "canon-laser"


def test_api_refuses_bad_lists_in_french_and_checks_the_origin(api):
    res = api.put("/api/ships", json={"queue": [{"item": "Anode", "mode": "fixed", "amount": 0, "planets": []}]})
    assert res.status_code == 422 and "quantité" in " ".join(res.json()["detail"])
    res = api.put("/api/components", json={"queue": [{"item": "inconnu", "mode": "max", "planets": []}]})
    assert res.status_code == 422 and "composant inconnu" in " ".join(res.json()["detail"])
    for route in ("/api/ships", "/api/components"):
        assert api.put(route, json={}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert api.post("/api/models/scan", headers={"Origin": "http://evil.example"}).status_code == 403


def test_the_two_new_features_exist_with_their_own_timer_and_the_empty_lists_do_nothing(api):
    ids = [f.id for f in FEATURES]
    assert "ships" in ids and "components" in ids and ids.index("populations") < ids.index("ships") < ids.index("test")
    for fid in ("ships", "components"):
        feature = next(f for f in FEATURES if f.id == fid)
        assert "rien à faire" in asyncio.run(feature.run(api.app.state.bot))
