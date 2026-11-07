"""Exploration : réglages (une seule configuration), relevé du jour enregistré, API."""

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.explorations import ExplorationError, ExplorationStore, describe, validate
from app.main import create_app

EGIDE, CONCORDE = "6-79-Egide", "8-50-Concorde"
MODEL = {"method": "PILLARS", "composition": "model", "model": "Expe", "units": {}, "copies": 2, "duration": 3,
         "planets": [EGIDE], "rhythm": "fixed", "per_pass": 1}


def test_defaults_launch_nothing_until_a_composition_is_chosen():
    with pytest.raises(ExplorationError, match="modèle"):
        validate({})


def test_a_model_or_quantities_with_copies_duration_planets_and_rhythm():
    assert validate(MODEL)["model"] == "Expe"
    manual = validate({**MODEL, "composition": "manual", "units": {"Rempart de Fer": 1500, "Soldat": 0}, "rhythm": "max"})
    assert manual["units"] == {"Rempart de Fer": 1500} and manual["rhythm"] == "max"  # les quantités nulles disparaissent
    fleet = validate({**MODEL, "method": "FLEET", "composition": "manual", "units": {">Lentus": 2}})
    assert fleet["units"] == {">Lentus": 2}
    assert validate({**MODEL, "planets": []})["planets"] == []  # toutes les planètes


@pytest.mark.parametrize(
    "patch,message",
    [
        ({"method": "BATEAU"}, "Moyen"),
        ({"copies": 0}, "Copies"),
        ({"copies": 11}, "Copies"),
        ({"duration": 7}, "Durée"),
        ({"rhythm": "parfois"}, "Rythme"),
        ({"per_pass": 0}, "par passage"),
        ({"composition": "manual", "units": {}}, "au moins une unité"),
        ({"composition": "manual", "units": {"Sonde Spatiale": 3}}, "non autorisée"),
        ({"composition": "manual", "units": {"Soldat": -1}}, "entier positif"),
        ({"planets": [" "]}, "Planètes"),
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


def test_describe_gives_a_readable_sentence():
    text = describe(validate(MODEL))
    assert "Piliers" in text and "modèle « Expe »" in text and "2 copie(s)" in text and "3 h" in text and EGIDE in text


def test_settings_and_daily_record_survive_a_restart(tmp_path):
    store = ExplorationStore(str(tmp_path))
    store.replace(MODEL)
    store.set_daily({EGIDE: {"address": "M79 50 37", "x": 1.0, "y": 2.0, "distance": 3.0, "source": "carte"}},
                    {"PILLARS": [{"name": "Expe", "units": 17000}]}, "quotidien")
    again = ExplorationStore(str(tmp_path))
    assert again.get()["model"] == "Expe"
    assert again.daily()["breaches"][EGIDE]["address"] == "M79 50 37" and not again.daily_due()
    assert again.known_models() == {"PILLARS": ["Expe"], "FLEET": []}
    (tmp_path / "explorations.json").write_text("{pas du json", encoding="utf8")
    assert ExplorationStore(str(tmp_path)).daily_due()  # fichier illisible : relevé à refaire


@pytest.fixture
def api(settings, tmp_path):
    with TestClient(create_app(replace(settings, data_dir=str(tmp_path)))) as c:
        yield c


def test_api_saves_the_settings_and_lists_units_and_methods(api):
    data = api.get("/api/explorations").json()
    assert data["methods"] == {"PILLARS": "Piliers", "FLEET": "Flotte"} and "Rempart de Fer" in data["units"]["PILLARS"]
    assert data["daily"]["day"] == ""
    res = api.put("/api/explorations", json=MODEL)
    assert res.status_code == 200 and res.json()["settings"]["copies"] == 2
    assert api.get("/api/explorations").json()["settings"]["model"] == "Expe"


def test_api_refuses_bad_settings_and_foreign_origins(api):
    res = api.put("/api/explorations", json={**MODEL, "copies": 12})
    assert res.status_code == 422 and "Copies" in " ".join(res.json()["detail"])
    evil = {"Origin": "http://evil.example"}
    assert api.put("/api/explorations", json=MODEL, headers=evil).status_code == 403
    assert api.post("/api/explorations/scan", headers=evil).status_code == 403


def test_manual_daily_scan_outside_a_game_gives_a_reason(api):
    data = api.post("/api/explorations/scan").json()
    assert data["daily"]["error"]  # pas dans une partie : rien n'est relevé, la raison est donnée
