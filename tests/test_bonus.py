"""Bonus d'activité : récupéré au début de chaque action du bot s'il est disponible, ignoré sinon."""

import time


def _bot(client):
    return client.app.state.bot


def _bonus(game):
    return [a for a in game.actions() if a["kind"] == "bonus"]


def _show_bonus(client, game):
    """Le faux jeu ne relit son état qu'au chargement : la page du bot est rechargée pour afficher le bouton."""
    game.set_bonus(True)
    client.portal.call(_bot(client)._page.reload)


def test_the_bonus_is_claimed_when_the_button_is_shown(game_client, game):
    _show_bonus(game_client, game)
    message = game_client.portal.call(_bot(game_client).claim_bonus)
    assert message.startswith("Bonus récupéré (12 min)")
    assert len(_bonus(game)) == 1
    logs = [entry["msg"] for entry in _bot(game_client).logs(20)]
    assert any(m.startswith("Bonus d'activité : Bonus récupéré") for m in logs)


def test_nothing_happens_without_the_button(game_client, game):
    assert game_client.portal.call(_bot(game_client).claim_bonus) is None
    assert _bonus(game) == []


def test_each_feature_pass_starts_by_claiming_the_bonus(game_client, game):
    _show_bonus(game_client, game)
    game_client.put("/api/features/test", json={"enabled": True, "min_minutes": 60, "max_minutes": 60})
    game_client.post("/api/bot/start")
    end = time.monotonic() + 15
    while time.monotonic() < end and not _bonus(game):
        time.sleep(0.2)
    game_client.post("/api/bot/stop")
    assert len(_bonus(game)) == 1
