"""Missions : « Récupérer » de l'onglet Missions une fois par jour, (A) au passage sur OFF, (B) à la fin de la dernière plage."""

import time
from datetime import datetime, timedelta

import pytest

from app.planning import TIMEZONE


def _bot(client):
    return client.app.state.bot


def _claims(game):
    return [(a["list"], a["name"]) for a in game.actions() if a["kind"] == "claim"]


def _wait_claims(game, count, seconds=20):
    end = time.monotonic() + seconds
    while time.monotonic() < end and len(_claims(game)) < count:
        time.sleep(0.3)
    return _claims(game)


def test_every_reward_of_the_missions_tab_is_claimed_once_a_day(game_client, game):
    bot = _bot(game_client)
    result = game_client.portal.call(bot.missions.claim_daily, "un essai")
    assert result.startswith("2 récompense(s) récupérée(s)"), result
    assert _claims(game) == [("missions", "Connexion quotidienne"), ("missions", "Réaliser 2 Fouilles")]  # pas la quête
    assert bot.mission_state.done_today()
    assert game_client.portal.call(bot.missions.claim_daily, "un essai") is None  # une seule fois par jour
    assert len(_claims(game)) == 2


def test_a_turning_the_bot_off_claims_the_missions(game_client, game):
    game_client.post("/api/bot/start")
    game_client.post("/api/bot/stop")
    assert _wait_claims(game, 2) == [("missions", "Connexion quotidienne"), ("missions", "Réaliser 2 Fouilles")]
    assert any("Missions (avant l'arrêt du bot (OFF))" in e["msg"] for e in _bot(game_client).logs(20))


def test_b_the_end_of_the_last_slot_of_the_day_claims_the_missions(game_client, game):
    bot = _bot(game_client)
    bot.planning.replace({"enabled": True, "days": {}, "offset_min": 0, "offset_max": 0})  # plus aucune plage
    bot.scheduler._on = True
    try:
        game_client.portal.call(bot.supervisor.ensure_ready)  # hors planning : missions, puis déconnexion
    finally:
        bot.scheduler._on = False
    assert _claims(game) == [("missions", "Connexion quotidienne"), ("missions", "Réaliser 2 Fouilles")]


def test_b_no_claim_when_another_slot_comes_later_today(game_client, game):
    now = datetime.now(TIMEZONE)
    if now.hour >= 21:
        pytest.skip("trop près de minuit pour placer une plage plus tard dans la journée")
    later = now + timedelta(hours=2)
    day = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[now.weekday()]
    bot = _bot(game_client)
    bot.planning.replace({"enabled": True, "days": {day: [{"start": later.strftime("%H:%M"), "end": (later + timedelta(minutes=30)).strftime("%H:%M")}]},
                          "offset_min": 0, "offset_max": 0})
    bot.scheduler._on = True
    try:
        game_client.portal.call(bot.supervisor.ensure_ready)
    finally:
        bot.scheduler._on = False
    assert _claims(game) == [] and not bot.mission_state.done_today()
