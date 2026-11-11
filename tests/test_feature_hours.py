"""Horaires propres au Piratage et à l'Exploration : plages dans le planning du bot, refus hors planning, pause."""

import asyncio
from datetime import datetime, timedelta

import pytest

from app.feature_hours import FeatureHoursError, FeatureHoursStore, outside_global, validate
from app.planning import TIMEZONE
from app.planning import validate as validate_planning
from tests.test_features import make, manual

MONDAY = datetime(2026, 11, 9, tzinfo=TIMEZONE)  # lundi


def _at(day_offset: int, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return MONDAY + timedelta(days=day_offset, hours=h, minutes=m)


def _plan(days: dict, enabled: bool = True) -> dict:
    return validate_planning({"enabled": enabled, "days": days, "offset_min": 0, "offset_max": 0})


GLOBAL = _plan({"mon": [{"start": "08:00", "end": "22:00"}], "tue": [{"start": "20:00", "end": "02:00"}]})


def test_slots_inside_the_bot_planning_are_accepted():
    hours = {"enabled": True, "days": {"mon": [{"start": "08:00", "end": "10:00"}, {"start": "16:00", "end": "20:00"}]}}
    out = validate(hours, GLOBAL)
    assert out["days"]["mon"] == [{"start": "08:00", "end": "10:00"}, {"start": "16:00", "end": "20:00"}]


def test_a_slot_outside_the_bot_planning_is_refused():
    with pytest.raises(FeatureHoursError, match="Lundi : la plage 21:00 → 23:00 sort du planning du bot"):
        validate({"enabled": True, "days": {"mon": [{"start": "21:00", "end": "23:00"}]}}, GLOBAL)
    with pytest.raises(FeatureHoursError, match="Mercredi"):  # aucun planning le mercredi
        validate({"enabled": True, "days": {"wed": [{"start": "10:00", "end": "11:00"}]}}, GLOBAL)


def test_the_midnight_overflow_of_the_bot_planning_counts():
    # mardi 20:00 → 02:00 : mercredi 00:30 → 01:30 est dans le planning, mercredi 01:30 → 03:00 n'y est pas.
    assert outside_global(_plan({"wed": [{"start": "00:30", "end": "01:30"}]}), GLOBAL) == []
    assert outside_global(_plan({"tue": [{"start": "23:00", "end": "01:00"}]}), GLOBAL) == []
    assert len(outside_global(_plan({"wed": [{"start": "01:30", "end": "03:00"}]}), GLOBAL)) == 1


def test_no_check_when_the_hours_or_the_bot_planning_are_off():
    anything = {"enabled": False, "days": {"mon": [{"start": "23:00", "end": "23:30"}]}}
    assert validate(anything, GLOBAL)["enabled"] is False
    assert validate({**anything, "enabled": True}, _plan({}, enabled=False))["enabled"] is True


def test_pause_reason_outside_the_slots_and_on_a_day_without_slot(tmp_path):
    store = FeatureHoursStore(str(tmp_path))
    assert store.pause_reason("hackings", _at(0, "11:00")) is None  # horaires non utilisés
    store.replace("hackings", {"enabled": True, "days": {"mon": [{"start": "08:00", "end": "10:00"}, {"start": "16:00", "end": "20:00"}]}}, GLOBAL)
    assert store.pause_reason("hackings", _at(0, "09:00")) is None
    assert store.pause_reason("hackings", _at(0, "11:00")) == "hors horaires du piratage — reprise aujourd'hui à 16:00"
    assert store.pause_reason("hackings", _at(1, "09:00")) == "hors horaires du piratage — reprise lundi à 08:00"  # mardi : rien
    assert store.pause_reason("explorations", _at(0, "11:00")) is None  # l'autre fonctionnalité n'est pas concernée
    assert store.pause_reason("constructions", _at(0, "11:00")) is None
    again = FeatureHoursStore(str(tmp_path))  # relu depuis feature_hours.json
    assert again.get("hackings")["days"]["mon"][1] == {"start": "16:00", "end": "20:00"}


def test_the_scheduler_skips_a_paused_feature_and_keeps_it_due():
    sched, bot, clock, runs = make(ids=("a", "b"))
    paused = {"a": "hors horaires du piratage — reprise aujourd'hui à 16:00"}
    bot.feature_pause = lambda fid: paused.get(fid)
    sched.update("a", {"enabled": True, "min_minutes": 10, "max_minutes": 20})
    sched.update("b", {"enabled": True, "min_minutes": 10, "max_minutes": 20})

    async def scenario():
        manual(sched)
        assert await sched.step() is True  # « a » en pause : « b » passe à sa place
        assert runs == ["b"]
        assert sched.summary()["features"][0]["paused"].startswith("hors horaires")
        assert await sched.step() is False  # « a » reste due mais ne passe pas
        paused.clear()  # début de la plage
        assert await sched.step() is True and runs == ["b", "a"]

    asyncio.run(scenario())


def test_the_api_saves_and_refuses_outside_the_bot_planning(client):
    body = client.get("/api/feature-hours/hackings").json()
    assert body["settings"]["enabled"] is False and "toute heure" in body["message"]
    client.put("/api/planning", json={"enabled": True, "days": {"mon": [{"start": "08:00", "end": "22:00"}]}, "offset_min": 0, "offset_max": 0})
    ok = client.put("/api/feature-hours/hackings", json={"enabled": True, "days": {"mon": [{"start": "08:00", "end": "10:00"}]}})
    assert ok.status_code == 200 and ok.json()["settings"]["days"]["mon"] == [{"start": "08:00", "end": "10:00"}]
    refused = client.put("/api/feature-hours/explorations", json={"enabled": True, "days": {"mon": [{"start": "21:00", "end": "23:00"}]}})
    assert refused.status_code == 422 and "planning général reste le maître" in refused.json()["detail"][0]
    assert client.get("/api/feature-hours/constructions").status_code == 404
