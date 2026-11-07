"""Espionnage des Errants : réglages (planète, minimums, nombre de ressources), tri des rapports, API."""

import json

import pytest

from app.spying import SpySettings, SpyingError, report_matches, validate

EGIDE = "6-79-Egide"
SETTINGS = {"planet": EGIDE, "minimums": {"Fer": 1_000_000, "Cristal": 500_000, "Éther": 1_000_000}, "required": 2}


def test_defaults_have_no_planet_and_zero_minimums():
    assert validate({}) == {"planet": "", "minimums": {"Fer": 0, "Cristal": 0, "Éther": 0}, "required": 1}


@pytest.mark.parametrize(
    "patch,message",
    [
        ({"required": 0}, "1, 2 ou 3"),
        ({"required": 4}, "1, 2 ou 3"),
        ({"required": True}, "1, 2 ou 3"),
        ({"minimums": {"Fer": -1}}, "Minimum de Fer"),
        ({"minimums": {"Cristal": 1.5}}, "Minimum de Cristal"),
        ({"minimums": "beaucoup"}, "Minimums"),
        ({"planet": 3}, "Planète"),
    ],
)
def test_invalid_settings_are_refused(patch, message):
    with pytest.raises(SpyingError, match=message):
        validate({**SETTINGS, **patch})


def test_an_unknown_planet_is_refused_when_the_planets_are_known():
    with pytest.raises(SpyingError, match="inconnue"):
        validate({**SETTINGS, "planet": "Nulle-Part"}, [EGIDE])
    assert validate(SETTINGS, [EGIDE])["planet"] == EGIDE


@pytest.mark.parametrize(
    "resources,required,kept",
    [
        ({"Fer": 2_000_000, "Cristal": 900_000, "Éther": 0}, 2, True),  # Fer et Cristal atteints
        ({"Fer": 2_000_000, "Cristal": 100, "Éther": 0}, 2, False),  # un seul atteint
        ({"Fer": 2_000_000, "Cristal": 100, "Éther": 0}, 1, True),
        ({"Fer": 2_000_000, "Cristal": 900_000, "Éther": 999_999}, 3, False),
        ({"Fer": 2_000_000, "Cristal": 900_000, "Éther": 1_000_000}, 3, True),  # le minimum lui-même suffit
        ({"Fer": None, "Cristal": None, "Éther": None}, 1, False),  # illisible : compté comme 0
    ],
)
def test_a_report_is_kept_when_enough_resources_reach_their_minimum(resources, required, kept):
    assert report_matches(resources, {**SETTINGS, "required": required}) is kept


def test_settings_are_saved_and_reloaded(tmp_path):
    store = SpySettings(str(tmp_path))
    assert store.persistent
    store.replace(SETTINGS)
    assert json.loads((tmp_path / "spying.json").read_text(encoding="utf8"))["required"] == 2
    assert SpySettings(str(tmp_path)).get() == SETTINGS


def test_an_unreadable_file_gives_the_defaults(tmp_path):
    (tmp_path / "spying.json").write_text("{pas du json", encoding="utf8")
    assert SpySettings(str(tmp_path)).get()["planet"] == ""


def test_the_api_saves_and_refuses_invalid_settings(client):
    body = client.get("/api/spying").json()
    assert body["settings"]["planet"] == "" and body["resources"] == ["Fer", "Cristal", "Éther"]
    assert body["status"]["running"] is False and body["status"]["closed"] == []
    saved = client.put("/api/spying", json=SETTINGS)
    assert saved.status_code == 200 and saved.json()["settings"] == SETTINGS
    refused = client.put("/api/spying", json={**SETTINGS, "required": 5})
    assert refused.status_code == 422 and "1, 2 ou 3" in " ".join(refused.json()["detail"])


def test_the_campaign_needs_a_planet(client):
    res = client.post("/api/spying/start")
    assert res.status_code == 409 and "planète" in res.json()["detail"]


def test_requests_from_another_site_are_refused(client):
    res = client.post("/api/spying/start", headers={"Origin": "https://ailleurs.example"})
    assert res.status_code == 403
