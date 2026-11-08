"""Dons de jetons d'alliance : seulement à la technologie prioritaire, tous / 1 / 2 par passage, jamais d'étoile cliquée."""

import pytest

from app.alliance import AllianceError, AllianceSettings, validate


def _bot(client):
    return client.app.state.bot


def _run(client):
    return client.portal.call(_bot(client).token_giver.run_pass)


def _tokens(game):
    return [(a["tech"], a["count"]) for a in game.actions() if a["kind"] == "token"]


def _forbidden(game):
    return [a for a in game.actions() if a["kind"] == "forbidden"]


@pytest.mark.parametrize("amount", ["all", 1, 2])
def test_valid_amounts(amount):
    assert validate({"amount": amount}) == {"amount": amount}


@pytest.mark.parametrize("amount", [0, 3, "tous", True, None])
def test_invalid_amounts_are_refused(amount):
    with pytest.raises(AllianceError, match="tous, 1 ou 2"):
        validate({"amount": amount})


def test_the_setting_is_saved(tmp_path):
    AllianceSettings(str(tmp_path)).replace({"amount": 2})
    assert AllianceSettings(str(tmp_path)).get() == {"amount": 2}


def test_the_api_saves_the_amount(client):
    assert client.get("/api/alliance").json()["settings"] == {"amount": "all"}
    assert client.put("/api/alliance", json={"amount": 1}).json()["settings"] == {"amount": 1}
    assert client.put("/api/alliance", json={"amount": 5}).status_code == 422


def test_all_tokens_go_to_the_priority_technology(game_client, game):
    summary = _run(game_client)
    assert summary == "5 jeton(s) donné(s) à Forge du Bastion", summary
    assert _tokens(game) == [("Forge du Bastion", 5)] and _forbidden(game) == []


def test_two_tokens_then_nothing_when_none_is_left(game_client, game):
    game_client.put("/api/alliance", json={"amount": 2})
    assert _run(game_client) == "2 jeton(s) donné(s) à Forge du Bastion"
    assert _tokens(game) == [("Forge du Bastion", 1), ("Forge du Bastion", 1)]
    game.set_alliance(tokens=0)
    assert _run(game_client) == "aucun jeton d'alliance disponible"


def test_one_token_when_two_are_asked_but_one_is_left(game_client, game):
    game_client.put("/api/alliance", json={"amount": 2})
    game.set_alliance(tokens=1)
    assert _run(game_client) == "1 jeton(s) donné(s) à Forge du Bastion"


def test_nothing_without_a_priority_technology(game_client, game):
    game.set_alliance(priority="")
    assert "aucune technologie n'est la priorité" in _run(game_client)
    assert _tokens(game) == [] and _forbidden(game) == []


def test_a_click_without_effect_is_reported(game_client, game):
    game_client.put("/api/alliance", json={"amount": 1})
    game.set_alliance(no_effect=True)
    assert "non confirmé" in _run(game_client)
