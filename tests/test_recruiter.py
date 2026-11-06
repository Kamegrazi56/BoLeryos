"""Recrutement de population : quantité fixe, MAX ou effectif à atteindre, population par population et planète par planète."""

from app import builder

CONCORDE = "8-50-Concorde"
EGIDE = "6-79-Egide"
STRATE = "9-169-Strate"


def _bot(client):
    return client.app.state.bot


def _scan(client):
    assert len(client.post("/api/planets/scan").json()["planets"]) == 3


def _run(client):
    return client.portal.call(_bot(client).recruiter.run_pass)


def _set(client, queue):
    res = client.put("/api/populations", json={"queue": queue})
    assert res.status_code == 200, res.text


def _line(population, mode, amount=0, planet=""):
    return {"population": population, "mode": mode, "amount": amount, "planet": planet}


def _recruited(game):
    return [(a["planet"], a["name"], a["qty"]) for a in game.actions() if a["kind"] == "production" and a["section"] == "population"]


def _reload(client):
    client.portal.call(_bot(client)._page.reload)  # le faux jeu ne se met pas à jour seul, le vrai si


def test_nothing_happens_with_an_empty_list_or_no_known_planet(game_client, game):
    assert "rien à faire" in _run(game_client)
    _set(game_client, [_line("mineur", "fixed", 5)])
    assert "scan des planètes" in _run(game_client)
    assert game.actions() == []


def test_a_fixed_quantity_is_recruited_on_the_chosen_planet_only(game_client, game):
    _scan(game_client)
    _set(game_client, [_line("rempart-de-fer", "fixed", 100, CONCORDE)])
    assert "100 × Rempart de Fer sur 8-50-Concorde" in _run(game_client)
    assert _recruited(game) == [(CONCORDE, "Rempart de Fer", 100)]


def test_max_recruits_everything_the_game_allows_from_another_tab(game_client, game):
    _scan(game_client)
    _set(game_client, [_line("mineur", "max", planet=CONCORDE)])  # Administration : 300 de Fer, 200 de Cristal → Cristal limite
    _run(game_client)
    assert _recruited(game) == [(CONCORDE, "Mineur", 111800)]


def test_a_target_recruits_only_what_is_missing_then_stops(game_client, game):
    _scan(game_client)
    _set(game_client, [_line("pilote", "target", 100, CONCORDE)])  # 10 Pilotes au départ
    _run(game_client)
    assert _recruited(game) == [(CONCORDE, "Pilote", 90)]
    game.complete()
    _bot(game_client).recruiter._cooldown.clear()
    _reload(game_client)
    assert "0 recrutement(s)" in _run(game_client)  # 100 atteints : plus rien
    assert len(_recruited(game)) == 1


def test_a_target_never_asks_for_more_than_the_planet_can_pay(game_client, game):
    _scan(game_client)
    _set(game_client, [_line("rempart-de-fer", "target", 10_000_000, CONCORDE)])
    _run(game_client)
    assert _recruited(game) == [(CONCORDE, "Rempart de Fer", 4272)]  # le Cristal limite : 22,36 M / 5 233


def test_each_population_is_independent_and_each_planet_is_treated_separately(game_client, game):
    _scan(game_client)
    _set(game_client, [_line("mineur", "fixed", 10), _line("pilote", "target", 12, EGIDE)])
    _run(game_client)
    assert _recruited(game) == [
        (CONCORDE, "Mineur", 10),
        (EGIDE, "Mineur", 10),
        (EGIDE, "Pilote", 2),  # 10 Pilotes sur Egide : il en manque 2
        (STRATE, "Mineur", 10),
    ]


def test_the_first_matching_line_of_a_population_wins_on_a_planet(game_client, game):
    _scan(game_client)
    _set(game_client, [_line("mineur", "fixed", 5, EGIDE), _line("mineur", "fixed", 7)])
    _run(game_client)
    assert [(p, q) for p, _, q in _recruited(game)] == [(CONCORDE, 7), (EGIDE, 5), (STRATE, 7)]


def test_nothing_is_recruited_when_not_even_one_unit_is_payable(game_client, game):
    _scan(game_client)
    game.set_stocks(0, Fer=0, Cristal=0)
    _set(game_client, [_line("mineur", "max", planet=CONCORDE)])
    assert "0 recrutement(s)" in _run(game_client)
    assert game.actions() == []


def test_a_population_in_training_is_not_recruited_again(game_client, game):
    _scan(game_client)
    _set(game_client, [_line("mineur", "fixed", 10, CONCORDE)])
    _run(game_client)
    _bot(game_client).recruiter._cooldown.clear()  # même sans la durée estimée, la file d'attente du jeu suffit
    _reload(game_client)
    _run(game_client)
    assert len(_recruited(game)) == 1


def test_the_loop_recruits_again_once_the_training_is_over(game_client, game):
    _scan(game_client)
    _set(game_client, [_line("mineur", "fixed", 10, CONCORDE)])
    _run(game_client)
    game.complete()
    _reload(game_client)
    assert "0 recrutement(s)" in _run(game_client)  # la durée estimée côté bot n'est pas écoulée
    _bot(game_client).recruiter._cooldown.clear()
    _run(game_client)
    assert [q for _, _, q in _recruited(game)] == [10, 10]


def test_manual_control_stops_the_pass_without_any_click(game_client, game):
    _scan(game_client)
    _set(game_client, [_line("mineur", "fixed", 10, CONCORDE)])
    _bot(game_client)._manual = True
    assert "interrompu" in _run(game_client)
    assert game.actions() == []


def test_two_recruitments_in_a_row_without_a_debit_disable_the_feature(game_client, game, monkeypatch):
    monkeypatch.setattr(builder, "CONFIRM_SECONDS", 0.6)
    game.reset(no_debit=True)  # le clic passe mais rien n'est débité ni mis en file : le bot ne peut pas confirmer
    _scan(game_client)
    _set(game_client, [_line("rempart-de-fer", "fixed", 1000, CONCORDE)])
    bot = _bot(game_client)
    game_client.put("/api/features/populations", json={"enabled": True, "min_minutes": 1, "max_minutes": 2})
    _run(game_client)
    bot.recruiter._cooldown.clear()
    assert "désactivé" in _run(game_client)
    assert len(_recruited(game)) == 2
    assert bot.scheduler.store.get("populations")["enabled"] is False
    logs = [e["msg"] for e in game_client.get("/api/status?limit=200").json()["logs"]]
    assert any("débit des ressources n'a pas été constaté" in line for line in logs)
