"""Exploration : réglages (envoi commun, planètes avec leurs expéditions simultanées, envoi par planète), relevé du jour
enregistré, API."""

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.explorations import ExplorationError, ExplorationStore, describe, plans, validate
from app.main import create_app

EGIDE, CONCORDE = "6-79-Egide", "8-50-Concorde"
MODEL = {"method": "PILLARS", "composition": "model", "model": "Expe", "units": {}, "duration": 3, "copies": 1,
         "planets": [{"name": EGIDE, "copies": 2}], "per_planet": False}
FLEET = {"method": "FLEET", "composition": "manual", "model": "", "units": {">Lentus": 2}, "duration": 6}


def test_defaults_launch_nothing_until_a_composition_is_chosen():
    with pytest.raises(ExplorationError, match="modèle"):
        validate({})


def test_a_model_or_quantities_with_duration_and_simultaneous_expeditions_per_planet():
    saved = validate(MODEL)
    assert saved["model"] == "Expe" and saved["planets"] == [{"name": EGIDE, "copies": 2, "send": None}]
    manual = validate({**MODEL, "composition": "manual", "units": {"Rempart de Fer": 1500, "Soldat": 0}})
    assert manual["units"] == {"Rempart de Fer": 1500}  # les quantités nulles disparaissent
    fleet = validate({**MODEL, **FLEET})
    assert fleet["units"] == {">Lentus": 2}
    assert validate({**MODEL, "planets": [], "copies": 4})["copies"] == 4  # toutes les planètes, 4 chacune
    assert "rhythm" not in saved and "per_pass" not in saved


@pytest.mark.parametrize(
    "patch,message",
    [
        ({"method": "BATEAU"}, "Moyen"),
        ({"copies": 0}, "Expéditions simultanées"),
        ({"copies": 11}, "Expéditions simultanées"),
        ({"planets": [{"name": EGIDE, "copies": 11}]}, "expéditions simultanées"),
        ({"duration": 7}, "Durée"),
        ({"composition": "manual", "units": {}}, "au moins une unité"),
        ({"composition": "manual", "units": {"Sonde Spatiale": 3}}, "non autorisée"),
        ({"composition": "manual", "units": {"Soldat": -1}}, "entier positif"),
        ({"planets": [{"name": " "}]}, "Planètes"),
        ({"planets": "toutes"}, "Planètes"),
        ({"per_planet": "oui"}, "par planète"),
        ({"per_planet": True, "planets": []}, "choisissez d'abord les planètes"),
    ],
)
def test_bad_settings_are_refused_in_french(patch, message):
    with pytest.raises(ExplorationError, match=message):
        validate({**MODEL, **patch})


def test_known_planets_and_models_are_checked():
    with pytest.raises(ExplorationError, match="Planète inconnue"):
        validate(MODEL, known_planets=[CONCORDE])
    with pytest.raises(ExplorationError, match="Modèle inconnu"):
        validate(MODEL, known_models={"PILLARS": ["300k-5k"], "FLEET": []})
    assert validate(MODEL, known_models={"PILLARS": [], "FLEET": []})  # aucun relevé : on accepte


def test_each_planet_can_have_its_own_send_when_the_option_is_on():
    data = {**MODEL, "per_planet": True,
            "planets": [{"name": EGIDE, "copies": 3}, {"name": CONCORDE, "copies": 7, "send": FLEET}]}
    saved = validate(data)
    egide, concorde = plans(saved, [])
    assert (egide["planet"], egide["copies"], egide["method"], egide["model"], egide["duration"]) == (EGIDE, 3, "PILLARS", "Expe", 3)
    assert (concorde["planet"], concorde["copies"], concorde["method"], concorde["units"], concorde["duration"]) == (CONCORDE, 7, "FLEET", {">Lentus": 2}, 6)
    with pytest.raises(ExplorationError, match="8-50-Concorde : indiquez au moins une unité"):  # un envoi propre doit être complet
        validate({**data, "planets": [{"name": CONCORDE, "copies": 1, "send": {**FLEET, "units": {}}}]})


def test_the_common_send_may_be_incomplete_when_every_planet_has_its_own():
    own = {**MODEL, "model": "", "per_planet": True, "planets": [{"name": CONCORDE, "copies": 1, "send": FLEET}]}
    assert validate(own)["model"] == ""


def test_per_planet_sends_are_kept_but_unused_when_the_option_is_off():
    data = {**MODEL, "planets": [{"name": CONCORDE, "copies": 2, "send": {**FLEET, "units": {}}}]}  # incomplet : pas grave, inutilisé
    saved = validate(data)
    assert saved["planets"][0]["send"]["method"] == "FLEET"  # gardé en mémoire
    (plan,) = plans(saved, [])
    assert (plan["method"], plan["model"], plan["copies"]) == ("PILLARS", "Expe", 2)  # l'envoi commun s'applique


def test_all_planets_use_the_common_send_and_the_common_number():
    saved = validate({**MODEL, "planets": [], "copies": 2})
    assert [(p["planet"], p["copies"], p["model"]) for p in plans(saved, [EGIDE, CONCORDE])] == [(EGIDE, 2, "Expe"), (CONCORDE, 2, "Expe")]


def test_the_old_format_is_still_read():
    old = {"method": "PILLARS", "composition": "model", "model": "Expe", "units": {}, "copies": 3, "duration": 2,
           "planets": [EGIDE, CONCORDE], "rhythm": "max", "per_pass": 4}
    saved = validate(old)
    assert saved["planets"] == [{"name": EGIDE, "copies": 3, "send": None}, {"name": CONCORDE, "copies": 3, "send": None}]
    assert saved["per_planet"] is False and "rhythm" not in saved


def test_describe_gives_a_readable_sentence():
    text = describe(validate(MODEL))
    assert "Piliers" in text and "modèle « Expe »" in text and "3 h" in text and f"2 depuis {EGIDE}" in text
    own = describe(validate({**MODEL, "per_planet": True, "planets": [{"name": CONCORDE, "copies": 7, "send": FLEET}]}))
    assert f"7 depuis {CONCORDE} (Flotte, 2 × >Lentus, 6 h)" in own
    assert "2 expédition(s) simultanée(s) depuis chaque planète" in describe(validate({**MODEL, "planets": [], "copies": 2}))


def test_settings_and_daily_record_survive_a_restart(tmp_path):
    store = ExplorationStore(str(tmp_path))
    store.replace(MODEL)
    store.set_daily({EGIDE: {"address": "M79 50 37", "x": 1.0, "y": 2.0, "distance": 3.0, "source": "carte"}},
                    {"PILLARS": [{"name": "Expe", "units": 17000}]}, "quotidien")
    again = ExplorationStore(str(tmp_path))
    assert again.get()["model"] == "Expe" and again.get()["planets"][0]["copies"] == 2
    assert again.daily()["breaches"][EGIDE]["address"] == "M79 50 37" and not again.daily_due()
    assert again.known_models() == {"PILLARS": ["Expe"], "FLEET": []}
    (tmp_path / "explorations.json").write_text("{pas du json", encoding="utf8")
    assert ExplorationStore(str(tmp_path)).daily_due()  # fichier illisible : relevé à refaire


def test_a_file_in_the_old_format_is_converted(tmp_path):
    old = {"method": "PILLARS", "composition": "model", "model": "Expe", "units": {}, "copies": 2, "duration": 1,
           "planets": [EGIDE], "rhythm": "fixed", "per_pass": 1}
    (tmp_path / "explorations.json").write_text(json.dumps({"settings": old, "daily": {}}), encoding="utf8")
    assert ExplorationStore(str(tmp_path)).get()["planets"] == [{"name": EGIDE, "copies": 2, "send": None}]


@pytest.fixture
def api(settings, tmp_path):
    with TestClient(create_app(replace(settings, data_dir=str(tmp_path)))) as c:
        yield c


def test_api_saves_the_settings_and_lists_units_and_methods(api):
    data = api.get("/api/explorations").json()
    assert data["methods"] == {"PILLARS": "Piliers", "FLEET": "Flotte"} and "Rempart de Fer" in data["units"]["PILLARS"]
    assert data["daily"]["day"] == ""
    res = api.put("/api/explorations", json=MODEL)
    assert res.status_code == 200 and res.json()["settings"]["planets"][0]["copies"] == 2
    assert api.get("/api/explorations").json()["settings"]["model"] == "Expe"
    own = {**MODEL, "per_planet": True, "planets": [{"name": EGIDE, "copies": 3, "send": FLEET}]}
    assert api.put("/api/explorations", json=own).json()["settings"]["planets"][0]["send"]["units"] == {">Lentus": 2}


def test_api_refuses_bad_settings_and_foreign_origins(api):
    res = api.put("/api/explorations", json={**MODEL, "copies": 12})
    assert res.status_code == 422 and "Expéditions simultanées" in " ".join(res.json()["detail"])
    evil = {"Origin": "http://evil.example"}
    assert api.put("/api/explorations", json=MODEL, headers=evil).status_code == 403
    assert api.post("/api/explorations/scan", headers=evil).status_code == 403


def test_manual_daily_scan_outside_a_game_gives_a_reason(api):
    data = api.post("/api/explorations/scan").json()
    assert data["daily"]["error"]  # pas dans une partie : rien n'est relevé, la raison est donnée
