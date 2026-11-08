"""Piratage dans le faux jeu : Errant le plus proche à portée, Errant suivant si refusé, relance après la fin + le délai."""

EGIDE = "6-79-Egide"


def _bot(client):
    return client.app.state.bot


def _scan(client):
    assert len(client.post("/api/planets/scan").json()["planets"]) == 3


def _set(client, delay=(0, 0)):
    res = client.put("/api/hackings", json={"planet": EGIDE, "delay_min": delay[0], "delay_max": delay[1]})
    assert res.status_code == 200, res.text


def _run(client):
    return client.portal.call(_bot(client).hacker.run_pass)


def _hacks(game):
    return [a["target"] for a in game.actions() if a["kind"] == "hack"]


def test_the_record_keeps_the_nearest_errants_within_range(game_client, game):
    _scan(game_client)
    _set(game_client)
    body = game_client.post("/api/hackings/scan").json()
    # Torek est le plus proche mais son Drone est grisé (hors du ratio ×2)
    assert [t["address"] for t in body["daily"]["targets"]] == ["M69 37 24", "M69 34 68", "M69 41 02"]
    assert body["daily"]["targets"][1]["name"] == "Kairo-Vigie-378 7" and body["state"]["target"] == "M69 37 24"
    assert _hacks(game) == []


def test_a_refused_errant_is_replaced_by_the_next_nearest_then_hacked_in_a_loop(game_client, game):
    _scan(game_client)
    _set(game_client)
    summary = _run(game_client)
    assert summary == "piratage lancé vers Kairo-Vigie-378 7 (M69 34 68)", summary
    assert _hacks(game) == ["M69 37 24", "M69 34 68"]  # Bryn : piliers désactivés, refusé ; Kairo ensuite
    state = _bot(game_client).hackings.state()
    assert state["target"] == "M69 34 68" and state["refused"] == ["M69 37 24"]
    assert _run(game_client).startswith("piratage en cours vers M69 34 68")  # Naël est en mission : on attend
    game.set_hack(active=None)  # piratage terminé
    assert _run(game_client) == "piratage lancé vers Kairo-Vigie-378 7 (M69 34 68)"  # délai nul : même Errant, tout de suite
    assert _hacks(game) == ["M69 37 24", "M69 34 68", "M69 34 68"]


def test_the_next_hack_waits_for_the_delay_chosen_by_the_player(game_client, game):
    _scan(game_client)
    _set(game_client, delay=(20, 40))
    _run(game_client)
    game.set_hack(active=None)
    assert _run(game_client).startswith("délai avant relance")
    state = _bot(game_client).hackings.state()
    assert 20 * 60 <= state["ready"] - state["end"] <= 40 * 60 and len(_hacks(game)) == 2


def test_the_game_message_confirms_a_hack_shown_late(game_client, game):
    # Vrai jeu (08/11) : Naël encore « Disponible » juste après le clic ; le message « Mission de Piratage en cours » suffit.
    _scan(game_client)
    _set(game_client)
    game.set_hack(late_state=True)
    assert _run(game_client) == "piratage lancé vers Kairo-Vigie-378 7 (M69 34 68)"
    assert _bot(game_client).hacker._unconfirmed == 0


def test_nothing_is_launched_while_nael_is_busy(game_client, game):
    _scan(game_client)
    _set(game_client)
    game.set_hack(busy=True)
    assert _run(game_client) == "Naël Soryn n'est pas disponible" and _hacks(game) == []


def test_a_hack_started_by_hand_sets_the_end(game_client, game):
    _scan(game_client)
    _set(game_client, delay=(10, 10))
    game.set_hack(active={"origin": EGIDE, "target": "M69 34 68", "end_in": 600})
    assert _run(game_client).startswith("piratage en cours vers M69 34 68")
    state = _bot(game_client).hackings.state()
    assert abs(state["ready"] - state["end"] - 600) < 1 and _hacks(game) == []
