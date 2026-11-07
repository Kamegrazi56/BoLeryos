"""Piratage : réglages (planète de départ, délai), relevé et suivi enregistrés, Errants triés par distance, API."""

import json

import pytest

from app.hacker import nearest_errants
from app.hackings import HackingError, HackingStore, validate

EGIDE = "6-79-Egide"
SETTINGS = {"planet": EGIDE, "delay_min": 5, "delay_max": 15}


def test_defaults_launch_nothing():
    assert validate({}) == {"planet": "", "delay_min": None, "delay_max": None}


@pytest.mark.parametrize(
    "patch,message",
    [({"delay_min": None}, "Délai"), ({"delay_max": -1}, "Délai"), ({"delay_min": 20, "delay_max": 10}, "dépasse"), ({"planet": 4}, "Planète")],
)
def test_invalid_settings_are_refused(patch, message):
    with pytest.raises(HackingError, match=message):
        validate({**SETTINGS, **patch})


def test_an_unknown_planet_is_refused():
    with pytest.raises(HackingError, match="inconnue"):
        validate({**SETTINGS, "planet": "Nulle-Part"}, [EGIDE])


def test_settings_record_and_state_are_saved_and_a_new_planet_needs_a_new_record(tmp_path):
    store = HackingStore(str(tmp_path))
    store.replace(SETTINGS)
    assert store.daily_due()
    store.set_daily([{"address": "M58 60 94", "name": "Kairo-Vigie-228 6", "distance": 400.0}], "manuel")
    store.set_state(target="M58 60 94", end=100.0, ready=500.0)
    saved = json.loads((tmp_path / "hackings.json").read_text(encoding="utf8"))
    assert saved["state"]["target"] == "M58 60 94" and saved["daily"]["planet"] == EGIDE
    again = HackingStore(str(tmp_path))
    assert again.get() == SETTINGS and not again.daily_due() and again.state()["ready"] == 500.0
    again.replace({**SETTINGS, "planet": "8-50-Concorde"})
    assert again.daily_due() and again.state()["target"] == ""


def test_errants_are_sorted_by_real_distance():
    objects = [
        {"kind": "PLANET", "address": "M69 38 06", "positionMilli": [11570000, 1544000]},
        {"kind": "PVE_PLANET", "address": "M58 60 94", "name": "Kairo-Vigie-228 6", "positionMilli": [11300000, 1300000]},
        {"kind": "PVE_PLANET", "address": "M69 27 31", "name": "Aeryn-Courant-295 2", "positionMilli": [11508000, 1650000]},
        {"kind": "FREE_SLOT", "address": "M58 80 21", "positionMilli": [11570000, 1544100]},
    ]
    assert [e["address"] for e in nearest_errants(objects, "M69 38 06", None)] == ["M69 27 31", "M58 60 94"]


def test_the_api_saves_and_refuses_invalid_settings(client):
    assert client.get("/api/hackings").json()["settings"]["planet"] == ""
    assert client.put("/api/hackings", json=SETTINGS).json()["settings"] == SETTINGS
    refused = client.put("/api/hackings", json={**SETTINGS, "delay_max": None})
    assert refused.status_code == 422 and "Délai" in " ".join(refused.json()["detail"])
    assert client.put("/api/hackings", json=SETTINGS, headers={"Origin": "https://ailleurs.example"}).status_code == 403
