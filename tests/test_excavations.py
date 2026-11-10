"""Fouilles : réglages (planètes de départ, Fouilles simultanées, durée et équipe de chaque Fouille, Mira, délai), suivi des
Fouilles (fin + délai), sites les plus proches, API."""

import json

import pytest

from app.excavations import ExcavationError, ExcavationStore, slots, validate
from app.excavator import nearest_sites, sync_tracked

EGIDE, CONCORDE = "6-79-Egide", "8-50-Concorde"
TEAM = {"Scientifique": 2500, "Mineur": 2500}
SETTINGS = {"duration": 6, "units": TEAM, "copies": 1, "fouilles": [], "planets": [{"name": EGIDE, "copies": 2, "fouilles": []}],
            "per_slot": False, "mira": EGIDE, "delay_min": 0, "delay_max": 0}


def test_the_common_send_keeps_only_the_units_sent():
    out = validate({**SETTINGS, "units": {**TEAM, "Soldat": 0}})
    assert out["units"] == TEAM
    assert [(w["planet"], w["index"], w["duration"], w["mira"]) for w in slots(out, [])] == [(EGIDE, 0, 6, True), (EGIDE, 1, 6, False)]


@pytest.mark.parametrize(
    "patch,message",
    [
        ({"units": {"Scientifique": 5001}}, "de 0 à 5000"),
        ({"units": {"Scientifique": 3000, "Mineur": 2001}}, "de 1 à 5000 unités"),
        ({"units": {}}, "de 1 à 5000 unités"),
        ({"units": {"Grand Transporteur": 10}}, "non autorisée"),
        ({"duration": 5}, "3, 6, 12 ou 24"),
        ({"copies": 0}, "Fouilles simultanées"),
        ({"planets": [{"name": EGIDE, "copies": 11}]}, "Fouilles simultanées"),
        ({"planets": [{"name": ""}]}, "planète de départ"),
        ({"mira": CONCORDE}, "pas une planète de départ"),
        ({"delay_min": None}, "Délai"),
        ({"delay_min": 30, "delay_max": 10}, "dépasse"),
    ],
)
def test_invalid_settings_are_refused(patch, message):
    with pytest.raises(ExcavationError, match=message):
        validate({**SETTINGS, **patch})


def test_only_soldiers_scientists_and_miners_go_on_an_excavation():
    for unit in ("Rempart de Fer", "Spectre du Rift", "Surchargeur Plasmique", "Faucheur Cinétique"):
        with pytest.raises(ExcavationError, match="non autorisée"):
            validate({**SETTINGS, "units": {**TEAM, unit: 10}})


def test_the_total_of_simultaneous_excavations_cannot_exceed_what_the_game_allows():
    two = {**SETTINGS, "planets": [{"name": EGIDE, "copies": 4}, {"name": CONCORDE, "copies": 3}]}
    with pytest.raises(ExcavationError, match="7 au total, le jeu en permet 6"):
        validate(two, max_active=6)
    assert validate({**two, "planets": [{"name": EGIDE, "copies": 4}, {"name": CONCORDE, "copies": 2}]}, max_active=6)
    with pytest.raises(ExcavationError, match="4 au total"):  # toutes les planètes : 2 Fouilles × 2 planètes connues
        validate({**SETTINGS, "planets": [], "mira": "", "copies": 2}, known_planets=[EGIDE, CONCORDE], max_active=3)
    assert validate(two)  # maximum inconnu (pas encore de relevé) : pas de limite


def test_the_store_uses_the_maximum_read_by_the_daily_record(tmp_path):
    store = ExcavationStore(str(tmp_path))
    store.set_daily({}, 2, "quotidien")
    with pytest.raises(ExcavationError, match="le jeu en permet 2"):
        store.replace({**SETTINGS, "planets": [{"name": EGIDE, "copies": 3}]})


def test_each_excavation_can_have_its_own_duration_and_team():
    own = {**SETTINGS, "per_slot": True, "planets": [
        {"name": EGIDE, "copies": 2, "fouilles": [{"duration": 6, "units": TEAM}, {"duration": 24, "units": {"Mineur": 1000}}]},
        {"name": CONCORDE, "copies": 1, "fouilles": []},  # pas de réglage propre : l'envoi commun
    ]}
    got = [(w["planet"], w["index"], w["duration"], w["units"], w["common"]) for w in slots(validate(own), [])]
    assert got == [(EGIDE, 0, 6, TEAM, False), (EGIDE, 1, 24, {"Mineur": 1000}, False), (CONCORDE, 0, 6, TEAM, True)]
    with pytest.raises(ExcavationError, match="6-79-Egide, Fouille 2 : l'équipe"):
        validate({**own, "planets": [{"name": EGIDE, "copies": 2, "fouilles": [{"duration": 6, "units": TEAM}, {"duration": 3, "units": {}}]}]})


def test_per_excavation_settings_are_kept_but_unused_when_the_option_is_off():
    saved = validate({**SETTINGS, "planets": [{"name": EGIDE, "copies": 1, "fouilles": [{"duration": 24, "units": {}}]}]})
    assert saved["planets"][0]["fouilles"] == [{"duration": 24, "units": {}}]  # gardé, même incomplet
    assert [(w["duration"], w["units"]) for w in slots(saved, [])] == [(6, TEAM)]


def test_all_planets_share_the_same_excavations():
    saved = validate({**SETTINGS, "planets": [], "copies": 2, "per_slot": True, "mira": "",
                      "fouilles": [{"duration": 3, "units": TEAM}, {"duration": 12, "units": {"Mineur": 100}}]})
    got = [(w["planet"], w["duration"]) for w in slots(saved, [EGIDE, CONCORDE])]
    assert got == [(EGIDE, 3), (EGIDE, 12), (CONCORDE, 3), (CONCORDE, 12)]


def test_the_old_lines_are_converted():
    old = {"lines": [{"planet": EGIDE, "units": TEAM, "duration": 6, "slots": 2, "mira": False},
                     {"planet": EGIDE, "units": {"Mineur": 1000}, "duration": 24, "slots": 1, "mira": True},
                     {"planet": CONCORDE, "units": TEAM, "duration": 3, "slots": 1, "mira": False}],
           "delay_min": 5, "delay_max": 15}
    saved = validate(old)
    assert saved["per_slot"] is True and saved["mira"] == EGIDE and (saved["delay_min"], saved["delay_max"]) == (5, 15)
    got = [(w["planet"], w["duration"], w["mira"]) for w in slots(saved, [])]
    assert got == [(EGIDE, 24, True), (EGIDE, 6, False), (EGIDE, 6, False), (CONCORDE, 3, False)]  # la ligne de Mira en tête


def test_settings_daily_record_and_tracked_excavations_are_saved(tmp_path):
    store = ExcavationStore(str(tmp_path))
    store.replace(SETTINGS)
    store.set_daily({EGIDE: [{"address": "M58 80 21", "distance": 485.0, "source": "carte"}]}, 6, "manuel")
    store.set_tracked([{"planet": EGIDE, "slot": 0, "duration": 6, "site": "M58 80 21", "end": 100.0, "ready": 160.0}])
    saved = json.loads((tmp_path / "excavations.json").read_text(encoding="utf8"))
    assert saved["daily"]["max_active"] == 6 and saved["tracked"][0]["site"] == "M58 80 21"
    again = ExcavationStore(str(tmp_path))
    assert again.get() == validate(SETTINGS) and again.tracked() == store.tracked() and not again.daily_due()


def test_a_file_with_old_lines_is_converted(tmp_path):
    old = {"lines": [{"planet": EGIDE, "units": TEAM, "duration": 6, "slots": 2, "mira": True}], "delay_min": 0, "delay_max": 0}
    (tmp_path / "excavations.json").write_text(json.dumps({"settings": old, "daily": {}, "tracked": []}), encoding="utf8")
    saved = ExcavationStore(str(tmp_path)).get()
    assert saved["planets"] == [{"name": EGIDE, "copies": 2, "fouilles": [{"duration": 6, "units": TEAM}] * 2}] and saved["mira"] == EGIDE


# ------------------------------------------------------------------------------ suivi
TRACKED = {"planet": EGIDE, "slot": 0, "duration": 6, "site": "M58 80 21", "end": 1000.0, "ready": 1600.0}  # délai tiré : 600 s


def test_a_running_excavation_follows_the_end_shown_by_the_game():
    mission = {"origin": EGIDE, "site": "M58 80 21", "duration": 6, "left": 900}  # retard : fin repoussée
    out = sync_tracked([TRACKED], [mission], validate(SETTINGS), now=500.0)
    assert out == [{**TRACKED, "end": 1400.0, "ready": 2000.0}]


def test_a_finished_excavation_frees_its_place_after_the_delay():
    s = validate(SETTINGS)
    assert sync_tracked([TRACKED], [], s, now=1200.0) == [TRACKED]  # finie, délai pas écoulé
    assert sync_tracked([TRACKED], [], s, now=1700.0) == []  # place libre
    early = sync_tracked([TRACKED], [], s, now=500.0)  # disparue avant la fin prévue : finie maintenant
    assert early == [{**TRACKED, "end": 500.0, "ready": 1100.0}]
    assert sync_tracked([TRACKED], [], s, now=1700.0, complete=False) == [TRACKED]  # liste incomplète : rien n'est oublié


def test_an_excavation_launched_by_hand_is_adopted_by_a_free_excavation_of_its_planet():
    mission = {"origin": EGIDE, "site": "M68 75 95", "duration": 6, "left": 100}
    out = sync_tracked([TRACKED], [mission, {**mission, "origin": CONCORDE}], validate(SETTINGS), now=0.0)
    adopted = [t for t in out if t["site"] == "M68 75 95"]
    assert [(t["planet"], t["slot"], t["end"], t["ready"]) for t in adopted] == [(EGIDE, 1, 100.0, 100.0)]  # Concorde : rien de réglé


def test_an_old_tracked_excavation_gets_the_number_of_a_matching_excavation():
    old = {k: v for k, v in TRACKED.items() if k != "slot"}
    assert sync_tracked([old], [], validate(SETTINGS), now=1200.0) == [{**old, "slot": 0}]


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
def test_the_api_saves_and_refuses_invalid_settings(client):
    body = client.get("/api/excavations").json()
    assert body["settings"]["planets"] == [] and "Scientifique" in body["units"] and body["durations"] == [3, 6, 12, 24]
    saved = client.put("/api/excavations", json=SETTINGS)
    assert saved.status_code == 200 and saved.json()["settings"] == validate(SETTINGS)
    refused = client.put("/api/excavations", json={**SETTINGS, "delay_min": None})
    assert refused.status_code == 422 and "Délai" in " ".join(refused.json()["detail"])
    assert client.put("/api/excavations", json=SETTINGS, headers={"Origin": "https://ailleurs.example"}).status_code == 403
