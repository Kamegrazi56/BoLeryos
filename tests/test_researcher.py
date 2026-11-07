"""Lancement des recherches : clic sur « Rechercher » sur la planète chargée des recherches, une seule recherche à la fois."""

from app import builder

EGIDE = "6-79-Egide"
CONCORDE = "8-50-Concorde"
QUANTIQUE = {"research": "ordinateur-quantique", "level": 30}  # Civil, niveau 21 : 900 M de Fer… seule Egide peut payer
ENERGIE = {"research": "technologie-energie", "level": 25}  # Civil, niveau 20 : 10 M de Fer… Concorde peut payer
LASER = {"research": "technologie-laser", "level": 30}  # Militaire, niveau 24 : seule Egide peut payer
EFFICIENCE = {"research": "efficience-etherique", "level": 30}  # verrouillée


def _bot(client):
    return client.app.state.bot


def _scan(client):
    assert len(client.post("/api/planets/scan").json()["planets"]) == 3


def _run(client):
    return client.portal.call(_bot(client).researcher.run_pass)


def _set(client, queue, planet=EGIDE):
    assert client.put("/api/researches", json={"planet": planet, "queue": queue}).status_code == 200


def _launched(game):
    return [(a["planet"], a["name"], a["level_after"]) for a in game.actions() if a["kind"] == "research"]


def _reload(client):
    client.portal.call(_bot(client)._page.reload)  # le faux jeu ne se met pas à jour seul, le vrai si


def test_nothing_happens_with_an_empty_list_or_no_research_planet(game_client, game):
    assert "rien à faire" in _run(game_client)
    _bot(game_client).researches._data.update({"planet": "", "queue": [QUANTIQUE]})  # contourne la validation
    assert "choisissez" in _run(game_client)
    assert game.actions() == []


def test_the_first_line_of_the_list_is_launched_on_the_research_planet(game_client, game):
    _scan(game_client)
    _set(game_client, [QUANTIQUE, LASER])
    assert "Ordinateur Quantique niveau 22 sur 6-79-Egide" in _run(game_client)
    assert _launched(game) == [(EGIDE, "Ordinateur Quantique", 22)]


def test_priority_order_decides_and_the_military_tab_is_reached(game_client, game):
    _scan(game_client)
    _set(game_client, [LASER, QUANTIQUE])
    _run(game_client)
    assert _launched(game) == [(EGIDE, "Technologie laser", 25)]


def test_a_line_whose_target_level_is_reached_is_skipped(game_client, game):
    _scan(game_client)
    _set(game_client, [{"research": "ordinateur-quantique", "level": 21}, LASER])  # déjà niveau 21
    _run(game_client)
    assert _launched(game) == [(EGIDE, "Technologie laser", 25)]
    assert [ln["research"] for ln in game_client.get("/api/researches").json()["settings"]["queue"]] == ["technologie-laser"]  # la ligne atteinte a disparu


def test_a_line_the_planet_cannot_pay_is_skipped_for_the_next_one(game_client, game):
    _scan(game_client)
    _set(game_client, [QUANTIQUE, ENERGIE], planet=CONCORDE)  # Concorde : trop pauvre pour l'Ordinateur Quantique
    _run(game_client)
    assert _launched(game) == [(CONCORDE, "Technologie Énergie", 21)]


def test_a_locked_research_is_never_clicked(game_client, game):
    _scan(game_client)
    _set(game_client, [EFFICIENCE])
    assert "rien à lancer" in _run(game_client)
    assert game.actions() == []


def test_the_stocks_of_the_research_planet_are_really_debited(game_client, game):
    _scan(game_client)
    _set(game_client, [ENERGIE], planet=CONCORDE)
    _run(game_client)
    assert game_client.post("/api/planets/scan").json()["planets"][0]["fer"] < 50e6  # 58 M - 10 M


def test_only_one_research_at_a_time_then_the_next_one_after_its_duration(game_client, game):
    _scan(game_client)
    _set(game_client, [LASER, QUANTIQUE])
    _run(game_client)
    game.complete()  # le jeu dit « terminé »…
    _reload(game_client)
    assert "en cours" in _run(game_client)  # … mais la durée lue (4 j 18 h) n'est pas écoulée côté bot
    assert len(_launched(game)) == 1
    _bot(game_client).researcher._busy_until = 0.0  # durée écoulée
    _reload(game_client)
    _run(game_client)
    # Egide n'a plus de quoi payer un second Laser : la ligne suivante de la liste (Ordinateur Quantique) part.
    assert _launched(game) == [(EGIDE, "Technologie laser", 25), (EGIDE, "Ordinateur Quantique", 22)]


def test_manual_control_stops_the_pass_without_any_click(game_client, game):
    _scan(game_client)
    _set(game_client, [QUANTIQUE])
    _bot(game_client)._manual = True
    assert "interrompu" in _run(game_client)
    assert game.actions() == []


def test_a_planet_missing_from_the_game_stops_the_pass_with_a_message(game_client, game):
    _scan(game_client)
    _set(game_client, [QUANTIQUE])
    _bot(game_client).researches._data.update({"planet": "Nulle-Part"})  # contourne la validation : le jeu ne la connaît pas
    assert "arrêté" in _run(game_client) and game.actions() == []


def test_two_launches_in_a_row_without_a_debit_disable_the_feature(game_client, game, monkeypatch):
    monkeypatch.setattr(builder, "CONFIRM_SECONDS", 0.6)
    game.reset(no_debit=True)  # le clic passe mais rien n'est débité : le bot ne peut pas confirmer
    _scan(game_client)
    _set(game_client, [QUANTIQUE, LASER])
    bot = _bot(game_client)
    game_client.put("/api/features/researches", json={"enabled": True, "min_minutes": 1, "max_minutes": 2})
    _run(game_client)
    bot.researcher._busy_until = 0.0
    game.complete()
    _reload(game_client)
    assert "désactivées" in _run(game_client)
    assert len(_launched(game)) == 2
    assert bot.scheduler.store.get("researches")["enabled"] is False
    logs = [e["msg"] for e in game_client.get("/api/status?limit=200").json()["logs"]]
    assert any("débit des ressources n'a pas été constaté" in line for line in logs)
