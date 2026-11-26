"""Retours de flottes : durées, décision d'attendre ou de cliquer, programmation et enregistrement (sans navigateur)."""

import time

import pytest

from app.fleets import (FleetError, FleetStore, build_fleet, decide, estimate_rate, estimated_click, match_fleet, parse_clock, parse_local)


def _raw(kind="Déploiement", left=3600, o="M69 38 06", d="Z25 95 16", can_recall=True, text=""):
    return {"index": 0, "kind": kind, "units": "476051 U", "left": left, "can_recall": can_recall, "text": text or kind,
            "origin": {"name": "Egide", "coords": o}, "destination": {"name": "Concorde", "coords": d}}


def _at(seconds_from_now: float) -> str:
    from datetime import datetime

    from app.planning import TIMEZONE
    return datetime.fromtimestamp(time.time() + seconds_from_now, TIMEZONE).strftime("%Y-%m-%dT%H:%M:%S")


def test_durations_are_read_in_both_formats():
    assert parse_clock("01:20:00") == 4800 and parse_clock("2j 01:00:00") == 176400
    assert parse_clock("1 h 2 min 3 s") == 3723 and parse_clock("58 min 36 s") == 3516
    assert parse_clock("rien") is None and parse_clock(None) is None


def test_the_local_date_accepts_seconds_or_not():
    assert parse_local("2026-11-22T21:53:10") - parse_local("2026-11-22T21:53") == 10
    with pytest.raises(ValueError):
        parse_local("demain")


def test_only_the_allowed_missions_are_offered():
    assert build_fleet(_raw(), 1000.0)["excluded"] is False
    for kind in ("Exploration", "Fouille", "Forage", "Piratage", "Espionnage"):
        assert build_fleet(_raw(kind=kind), 1000.0)["excluded"] is True, kind


def test_a_fleet_is_found_again_by_its_route_and_arrival():
    fleets = [build_fleet(_raw(left=3600), 1000.0), build_fleet(_raw(left=7200, d="Z25 00 00"), 1000.0)]
    assert match_fleet(fleets, fleets[0]["key"], 1000.0 + 3600 + 30)["id"] == fleets[0]["id"]  # 30 s d'écart : même flotte
    assert match_fleet(fleets, fleets[0]["key"], 1000.0 + 9000) is None


def test_the_rate_is_measured_between_two_reads_and_kept_between_one_and_two():
    assert estimate_rate([]) == 2.0 and estimate_rate([[0, 100]]) == 2.0
    assert estimate_rate([[0, 100], [30, 160]]) == 2.0  # deux lectures trop proches : prudence
    assert estimate_rate([[0, 100], [100, 200]]) == 1.0  # durée de retour fixe
    assert estimate_rate([[0, 100], [100, 340]]) == 2.0  # plafonné


def test_decision_waits_holds_clicks_or_gives_up():
    # durée de retour 100 s, heure voulue dans 1000 s, marge 5 s : écart 895 s, on attend (contrôle à écart / 2 − 60 s)
    action, at = decide(0.0, 100, 1000, 5, [])
    assert action == "wait" and at == pytest.approx(895 / 2 - 60)
    assert decide(0.0, 100, 1000, 5, [[0.0, 100.0], [100.0, 200.0]])[0] == "wait"  # vitesse 1 : on peut attendre plus longtemps
    assert decide(0.0, 100, 150, 5, []) == ("hold", 45)  # moins de 90 s de vitesse 2 : on reste dans la fenêtre
    assert decide(0.0, 100, 105, 5, [])[0] == "click"  # arrivée prévue + marge = heure voulue
    assert decide(0.0, 100, 90, 5, [])[0] == "click"  # en retard de 10 s seulement : on rappelle quand même
    assert decide(0.0, 100, 50, 5, [])[0] == "late"  # même tout de suite, ce serait 50 s après


def test_estimated_click_matches_the_wait_decision():
    # le clic prévu est 60 s après le prochain contrôle (CHECK_LEAD_SECONDS) quand le bot attend
    _, check = decide(0.0, 100, 1000, 5, [])
    assert estimated_click(0.0, 100, 1000, 5, []) == pytest.approx(check + 60) == pytest.approx(895 / 2)
    assert estimated_click(0.0, 100, 1000, 5, [[0.0, 100.0], [100.0, 200.0]]) == pytest.approx(895)  # vitesse 1
    assert estimated_click(0.0, 100, 90, 5, []) == 0.0  # déjà l'heure : maintenant


def test_a_return_is_programmed_only_for_a_known_recallable_fleet(tmp_path):
    store = FleetStore(str(tmp_path))
    ok, other, shut = build_fleet(_raw(), time.time()), build_fleet(_raw(kind="Forage", d="Z25 11 11"), time.time()), build_fleet(_raw(can_recall=False, d="Z25 22 22"), time.time())
    store.set_snapshot([ok, other, shut])
    for fleet_id, at in (("inconnue", _at(600)), (other["id"], _at(600)), (shut["id"], _at(600)), (ok["id"], "demain"), (ok["id"], _at(-10))):
        with pytest.raises(FleetError):
            store.add(fleet_id, at)
    recall = store.add(ok["id"], _at(600))
    assert recall["state"] == "pending" and recall["next_check"] <= time.time() and recall["fleet"]["key"] == ok["key"]
    assert recall["click_at"] is None  # pas de clic prévu avant la première lecture de la durée de retour
    with pytest.raises(FleetError, match="déjà programmé"):
        store.add(ok["id"], _at(900))


def test_programmed_returns_and_margin_survive_a_restart(tmp_path):
    store = FleetStore(str(tmp_path))
    fleet = build_fleet(_raw(), time.time())
    store.set_snapshot([fleet])
    recall = store.add(fleet["id"], _at(600))
    assert store.set_margin(12) == 12
    again = FleetStore(str(tmp_path))
    assert again.margin() == 12 and [r["id"] for r in again.recalls()] == [recall["id"]] and again.snapshot()["fleets"][0]["id"] == fleet["id"]
    assert again.remove(recall["id"]) and not again.remove("zzz") and again.next_check() is None
    for bad in (-1, 121, "5", True):
        with pytest.raises(FleetError):
            again.set_margin(bad)
