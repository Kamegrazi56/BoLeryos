"""Forages dans le faux jeu : planète minière la plus proche de la ressource voulue, places, phases, relance sur la même."""

EGIDE = "6-79-Egide"
BASE = {"planets": [EGIDE], "resources": ["fer"], "miners": 150000, "slots": 2, "delay_min": 0, "delay_max": 0}


def _bot(client):
    return client.app.state.bot


def _scan(client):
    assert len(client.post("/api/planets/scan").json()["planets"]) == 3


def _set(client, **patch):
    res = client.put("/api/minings", json={**BASE, **patch})
    assert res.status_code == 200, res.text


def _run(client):
    return client.portal.call(_bot(client).miner.run_pass)


def _sent(game):
    return [(a["target"], a["miners"]) for a in game.actions() if a["kind"] == "mining"]


def test_each_place_goes_to_the_nearest_free_mining_planet_of_the_resource(game_client, game):
    _scan(game_client)
    _set(game_client)
    summary = _run(game_client)
    assert summary.startswith("2 forage(s) lancé(s)"), summary
    assert _sent(game) == [("M68 18 22", 150000), ("M78 16 44", 150000)]  # le cristal (plus proche) n'est pas visé
    assert [t["phase"] for t in _bot(game_client).minings.tracked()] == ["aller", "aller"]
    assert "0 forage(s) lancé(s) ; 2 en cours" in _run(game_client) and len(_sent(game)) == 2


def test_a_target_lost_just_after_typing_is_typed_again(game_client, game):
    # Vrai jeu (08/11) : « cible M68 18 22 refusée par le jeu » alors que la même saisie à la main est acceptée.
    game.reset(wipe_target=1)
    _scan(game_client)
    _set(game_client, slots=1)
    summary = _run(game_client)
    assert summary.startswith("1 forage(s) lancé(s)"), summary
    assert _sent(game) == [("M68 18 22", 150000)]


def test_a_target_never_shown_is_explained_in_the_log(game_client, game):
    game.reset(wipe_target=5)
    _scan(game_client)
    _set(game_client, slots=1)
    assert _run(game_client).startswith("0 forage(s) lancé(s)") and _sent(game) == []
    logs = [e["msg"] for e in game_client.get("/api/status?limit=50").json()["logs"]]
    assert any("cible M68 18 22 refusée par le jeu (champ Cible" in line for line in logs), logs


def test_the_place_is_relaunched_on_the_same_mining_planet_after_the_return(game_client, game):
    _scan(game_client)
    _set(game_client, slots=1)
    _run(game_client)
    game.set_mine(active=[{"origin": EGIDE, "site": "M68 18 22", "phase": "extraction"}])
    _run(game_client)
    assert _bot(game_client).minings.tracked()[0]["phase"] == "extraction"
    game.set_mine(active=[{"origin": EGIDE, "site": "M68 18 22", "phase": "retour", "end_in": 300}])
    _run(game_client)
    assert _bot(game_client).minings.tracked()[0]["phase"] == "retour"
    game.set_mine(active=[])  # rentrée ; délai nul : relance tout de suite, sur la même planète minière
    _run(game_client)
    assert _sent(game) == [("M68 18 22", 150000), ("M68 18 22", 150000)]


def test_a_vanished_mining_planet_means_a_new_search(game_client, game):
    _scan(game_client)
    _set(game_client, slots=1)
    _run(game_client)
    game.set_mine(active=[], mines=[["M78 16 44", "fer", 11650, 1650]])  # M68 18 22 épuisée et disparue
    _run(game_client)
    assert _sent(game) == [("M68 18 22", 150000), ("M78 16 44", 150000)]


def test_a_forage_in_progress_after_a_restart_takes_its_place(game_client, game):
    _scan(game_client)
    game.set_mine(active=[{"origin": EGIDE, "site": "M68 18 22", "phase": "extraction"}])
    _set(game_client, slots=1)
    assert "0 forage(s) lancé(s) ; 1 en cours" in _run(game_client) and _sent(game) == []


def test_nothing_when_no_mining_planet_of_the_resource_exists(game_client, game):
    _scan(game_client)
    _set(game_client, resources=["ether"], slots=1)  # la seule planète d'Éther est près de Concorde, trop loin pour Egide
    game.set_mine(mines=[["M70 00 01", "cristal", 11600, 1500]])
    assert _run(game_client).startswith("0 forage(s) lancé(s)") and _sent(game) == []
