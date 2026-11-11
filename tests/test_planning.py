"""Planning : plages actives par jour (heure française), passage de minuit, décalage aléatoire, déconnexion hors plage."""

from datetime import datetime, timedelta

import pytest

from app.bot import State
from app.planning import TIMEZONE, PlanningError, describe, pause_reason, state, validate

MONDAY = datetime(2026, 11, 9, tzinfo=TIMEZONE)  # lundi


def _at(day_offset: int, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return MONDAY + timedelta(days=day_offset, hours=h, minutes=m)


def _plan(days: dict, offset=(0, 0), enabled=True) -> dict:
    return validate({"enabled": enabled, "days": days, "offset_min": offset[0], "offset_max": offset[1]})


def test_without_the_planning_the_bot_is_always_active():
    assert state(_plan({}, enabled=False), _at(0, "03:00"))["active"] is True
    assert pause_reason(_plan({}, enabled=False), _at(0, "03:00")) is None


def test_active_only_inside_the_slots_of_the_day():
    plan = _plan({"mon": [{"start": "08:00", "end": "12:30"}, {"start": "18:00", "end": "23:00"}]})
    assert state(plan, _at(0, "09:15"))["active"] is True
    assert state(plan, _at(0, "12:30"))["active"] is False  # la fin est exclue
    st = state(plan, _at(0, "13:00"))
    assert st["active"] is False and st["next"] == _at(0, "18:00")
    assert pause_reason(plan, _at(0, "13:00")) == "hors planning — reprise aujourd'hui à 18:00"
    assert pause_reason(plan, _at(0, "23:30")) == "hors planning — reprise lundi à 08:00"  # la semaine suivante


def test_a_day_without_slot_is_inactive_and_a_slot_can_cross_midnight():
    plan = _plan({"tue": [{"start": "22:00", "end": "02:00"}]})
    assert state(plan, _at(0, "12:00"))["active"] is False  # lundi : aucune plage
    assert pause_reason(plan, _at(0, "12:00")) == "hors planning — reprise demain à 22:00"
    assert state(plan, _at(1, "23:00"))["active"] is True
    assert state(plan, _at(2, "01:30"))["active"] is True  # mercredi 01:30 : toujours la plage de mardi
    assert state(plan, _at(2, "02:30"))["active"] is False


def test_the_random_offset_stays_between_the_bounds_and_is_stable():
    plan = _plan({d: [{"start": "10:00", "end": "11:00"}] for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}, offset=(5, 15))
    starts = []
    for day in range(7):
        start = state(plan, _at(day, "09:00"))["next"]
        gap = abs((start - _at(day, "10:00")).total_seconds()) / 60
        assert 5 <= gap <= 15
        starts.append(start)
    assert len(set(s.minute for s in starts)) > 1  # pas le même décalage tous les jours
    assert state(plan, _at(0, "09:00"))["next"] == starts[0]  # même tirage à chaque lecture


@pytest.mark.parametrize(
    "data,message",
    [
        ({"days": {"mon": [{"start": "8h", "end": "12:00"}]}}, "HH:MM"),
        ({"days": {"mon": [{"start": "08:00", "end": "08:00"}]}}, "différents"),
        ({"offset_min": 30, "offset_max": 10}, "dépasse"),
        ({"offset_max": 500}, "Décalage"),
        ({"enabled": "oui"}, "Utiliser le planning"),
    ],
)
def test_invalid_plannings_are_refused(data, message):
    with pytest.raises(PlanningError, match=message):
        validate(data)


def test_describe_names_the_day():
    assert describe(_at(3, "07:05"), _at(0, "10:00")) == "jeudi à 07:05"
    assert describe(None) == "aucune plage prévue"


def test_log_times_and_daily_keys_are_in_french_time_even_in_a_utc_container(client, monkeypatch):
    import time as _time

    from app import excavations, explorations, hackings
    from app.hacker import _clock

    monkeypatch.setenv("TZ", "UTC")
    _time.tzset()  # conteneur Docker sans réglage : UTC
    try:
        bot = client.app.state.bot
        before = datetime.now(TIMEZONE)
        bot.log("test de l'heure")
        after = datetime.now(TIMEZONE)
        assert bot._logs[-1]["ts"] in {before.strftime("%H:%M:%S"), after.strftime("%H:%M:%S")}
        assert {explorations.today(), excavations.today(), hackings.today()} <= {before.strftime("%Y-%m-%d"), after.strftime("%Y-%m-%d")}
        noon_paris = datetime(2026, 11, 11, 12, 0, tzinfo=TIMEZONE).timestamp()
        assert _clock(noon_paris) == "12:00"  # 11:00 UTC
    finally:
        monkeypatch.undo()
        _time.tzset()


def test_the_api_saves_the_planning_and_shows_the_state(client):
    body = client.get("/api/planning").json()
    assert body["settings"]["enabled"] is False and "désactivé" in body["message"] and body["days"]["mon"] == "lundi"
    saved = client.put("/api/planning", json={"enabled": True, "days": {"mon": [{"start": "08:00", "end": "09:00"}]}, "offset_min": 0, "offset_max": 5})
    assert saved.status_code == 200 and saved.json()["settings"]["days"]["mon"] == [{"start": "08:00", "end": "09:00"}]
    refused = client.put("/api/planning", json={"enabled": True, "days": {"mon": [{"start": "25:00", "end": "09:00"}]}})
    assert refused.status_code == 422


def test_outside_the_planning_the_bot_on_does_nothing_and_logs_out(game_client):
    bot = game_client.app.state.bot
    bot.planning.replace({"enabled": True, "days": {}, "offset_min": 0, "offset_max": 0})  # aucune plage
    assert bot.planning_pause() is None  # bot sur OFF : le planning ne s'applique pas
    bot.scheduler._on = True  # ON sans lancer la boucle (le test pilote lui-même)
    try:
        assert bot.planning_pause() == "hors planning — aucune plage prévue"
        assert bot._feature_blocker() == "hors planning — aucune plage prévue"
        page_before = bot._page
        reason = game_client.portal.call(bot.supervisor.ensure_ready)
        assert reason == "hors planning — aucune plage prévue"
        # déconnecté : session du navigateur effacée (nouvelle page) et état « non connecté »
        assert bot._state == State.IDLE and bot._page is not page_before
        assert any("Planning : hors planning" in entry["msg"] for entry in bot.logs(10))
        assert game_client.portal.call(bot.supervisor.ensure_ready) == reason  # une seule déconnexion, puis attente
    finally:
        bot.scheduler._on = False
    assert bot.planning_pause() is None
