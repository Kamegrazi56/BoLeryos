"""Fouilles : réglages (lignes, délai), suivi des Fouilles (fin + délai), sites les plus proches, API."""

import json

import pytest

from app.excavations import ExcavationError, ExcavationStore, validate
from app.excavator import nearest_sites, sync_tracked

EGIDE, CONCORDE = "6-79-Egide", "8-50-Concorde"
LINE = {"planet": EGIDE, "units": {"Scientifique": 2500, "Mineur": 2500}, "duration": 6, "slots": 2, "mira": True}
SETTINGS = {"lines": [LINE], "delay_min": 0, "delay_max": 0}


def test_a_line_keeps_only_the_units_sent():
    out = validate({**SETTINGS, "lines": [{**LINE, "units": {"Scientifique": 2500, "Mineur": 2500, "Soldat": 0}}]})
    assert out["lines"][0]["units"] == {"Scientifique": 2500, "Mineur": 2500}
    assert validate({}) == {"lines": [], "delay_min": None, "delay_max": None}  # rien de réglé : rien à lancer


@pytest.mark.parametrize(
    "patch,message",
    [
        ({"units": {"Scientifique": 5001}}, "de 0 à 5000"),
        ({"units": {"Scientifique": 3000, "Mineur": 2001}}, "de 1 à 5000 unités"),
        ({"units": {}}, "de 1 à 5000 unités"),
        ({"units": {"Grand Transporteur": 10}}, "non autorisée"),
        ({"duration": 5}, "3, 6, 12 ou 24"),
        ({"slots": 0}, "places"),
        ({"planet": ""}, "planète de départ"),
    ],
)
def test_invalid_lines_are_refused(patch, message):
    with pytest.raises(ExcavationError, match=message):
        validate({**SETTINGS, "lines": [{**LINE, **patch}]})


def test_the_delay_is_chosen_by_the_player():
    with pytest.raises(ExcavationError, match="Délai"):
        validate({"lines": [LINE]})
    with pytest.raises(ExcavationError, match="dépasse"):
        validate({**SETTINGS, "delay_min": 30, "delay_max": 10})


def test_mira_on_one_line_and_one_line_per_planet_and_duration():
    other = {**LINE, "duration": 24}
    with pytest.raises(ExcavationError, match="Mira Venn"):
        validate({**SETTINGS, "lines": [LINE, other]})
    assert len(validate({**SETTINGS, "lines": [LINE, {**other, "mira": False}]})["lines"]) == 2
    with pytest.raises(ExcavationError, match="par planète et par durée"):
        validate({**SETTINGS, "lines": [LINE, {**LINE, "mira": False}]})


def test_settings_daily_record_and_tracked_excavations_are_saved(tmp_path):
    store = ExcavationStore(str(tmp_path))
    store.replace(SETTINGS)
    store.set_daily({EGIDE: [{"address": "M58 80 21", "distance": 485.0, "source": "carte"}]}, 6, "manuel")
    store.set_tracked([{"planet": EGIDE, "duration": 6, "site": "M58 80 21", "end": 100.0, "ready": 160.0}])
    saved = json.loads((tmp_path / "excavations.json").read_text(encoding="utf8"))
    assert saved["daily"]["max_active"] == 6 and saved["tracked"][0]["site"] == "M58 80 21"
    again = ExcavationStore(str(tmp_path))
    assert again.get() == SETTINGS and again.tracked() == store.tracked() and not again.daily_due()


# ------------------------------------------------------------------------------ suivi
TRACKED = {"planet": EGIDE, "duration": 6, "site": "M58 80 21", "end": 1000.0, "ready": 1600.0}  # délai tiré : 600 s


def test_a_running_excavation_follows_the_end_shown_by_the_game():
    mission = {"origin": EGIDE, "site": "M58 80 21", "duration": 6, "left": 900}  # retard : fin repoussée
    out = sync_tracked([TRACKED], [mission], SETTINGS, now=500.0)
    assert out == [{**TRACKED, "end": 1400.0, "ready": 2000.0}]


def test_a_finished_excavation_frees_its_place_after_the_delay():
    assert sync_tracked([TRACKED], [], SETTINGS, now=1200.0) == [TRACKED]  # finie, délai pas écoulé
    assert sync_tracked([TRACKED], [], SETTINGS, now=1700.0) == []  # place libre
    early = sync_tracked([TRACKED], [], SETTINGS, now=500.0)  # disparue avant la fin prévue : finie maintenant
    assert early == [{**TRACKED, "end": 500.0, "ready": 1100.0}]
    assert sync_tracked([TRACKED], [], SETTINGS, now=1700.0, complete=False) == [TRACKED]  # liste incomplète : rien n'est oublié


def test_an_excavation_launched_by_hand_is_adopted_by_its_line():
    mission = {"origin": EGIDE, "site": "M68 75 95", "duration": 6, "left": 100}
    out = sync_tracked([], [mission, {**mission, "origin": CONCORDE}], SETTINGS, now=0.0)
    assert [(t["planet"], t["site"], t["end"], t["ready"]) for t in out] == [(EGIDE, "M68 75 95", 100.0, 100.0)]  # Concorde : aucune ligne


def test_free_slots_are_sorted_by_real_distance():
    objects = [
        {"kind": "PLANET", "address": "M69 38 06", "positionMilli": [11570000, 1544000]},
        {"kind": "FREE_SLOT", "address": "M68 75 95", "positionMilli": [11234000, 1931000], "capabilities": ["COLONIZE", "EXCAVATION"]},
        {"kind": "FREE_SLOT", "address": "M58 80 21", "positionMilli": [11260000, 1171000], "capabilities": ["COLONIZE", "EXCAVATION"]},
        {"kind": "FREE_SLOT", "address": "M00 00 00", "positionMilli": [11570000, 1544100], "capabilities": ["COLONIZE"]},
        {"kind": "BREACH", "address": "M79 50 37", "positionMilli": [11629000, 2112000]},
    ]
    assert [s["address"] for s in nearest_sites(objects, "M69 38 06", None)] == ["M58 80 21", "M68 75 95"]


# ------------------------------------------------------------------------------ API
def test_the_api_saves_and_refuses_invalid_lines(client):
    body = client.get("/api/excavations").json()
    assert body["settings"]["lines"] == [] and "Scientifique" in body["units"] and body["durations"] == [3, 6, 12, 24]
    saved = client.put("/api/excavations", json=SETTINGS)
    assert saved.status_code == 200 and saved.json()["settings"] == SETTINGS
    refused = client.put("/api/excavations", json={**SETTINGS, "delay_min": None})
    assert refused.status_code == 422 and "Délai" in " ".join(refused.json()["detail"])
    assert client.put("/api/excavations", json=SETTINGS, headers={"Origin": "https://ailleurs.example"}).status_code == 403
