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


def test_a_recruit_button_hidden_under_a_floating_toolbar_is_still_pressed(game_client, game):
    """Constaté sur le vrai jeu : la barre d'outils flottante masque « Recruter » de la dernière carte, le clic tombait à côté."""
    _scan(game_client)
    _set(game_client, [_line("rempart-de-fer", "fixed", 7, CONCORDE)])

    async def hide_buttons_from_the_mouse():  # la souris traverse le bouton : seul le clavier l'atteint
        await _bot(game_client)._page.add_style_tag(content="button { pointer-events: none !important; }")

    game_client.portal.call(hide_buttons_from_the_mouse)
    assert "7 × Rempart de Fer sur 8-50-Concorde" in _run(game_client)
    assert _recruited(game) == [(CONCORDE, "Rempart de Fer", 7)]


def test_reenabling_the_feature_forgets_the_earlier_unconfirmed_launches(game_client, game, monkeypatch):
    monkeypatch.setattr(builder, "CONFIRM_SECONDS", 0.6)
    game.reset(no_debit=True)  # le clic passe mais rien n'est débité : le bot ne peut pas confirmer
    _scan(game_client)
    _set(game_client, [_line("rempart-de-fer", "fixed", 100, CONCORDE)])

    def feature(enabled):
        res = game_client.put("/api/features/populations", json={"enabled": enabled, "min_minutes": 1, "max_minutes": 2})
        assert res.status_code == 200, res.text

    feature(True)
    _run(game_client)  # 1er lancement non confirmé
    feature(False)
    feature(True)  # l'utilisateur a vérifié à l'écran et réactive
    _bot(game_client).recruiter._cooldown.clear()
    assert "désactivé" not in _run(game_client)  # ce n'est que le 1er échec depuis la réactivation
    assert _bot(game_client).scheduler.store.get("populations")["enabled"] is True


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
    assert "rien à faire" in _run(game_client)  # 100 atteints : la ligne a disparu de la liste
    assert game_client.get("/api/populations").json()["settings"]["queue"] == []
    assert len(_recruited(game)) == 1


def test_the_recruitment_turns_itself_off_when_its_last_line_is_done(game_client, game):
    _scan(game_client)
    assert game_client.put("/api/features/populations", json={"enabled": True, "min_minutes": 1, "max_minutes": 2}).status_code == 200
    _set(game_client, [_line("mineur", "max", planet=CONCORDE) | {"repeat": False}])
    assert "fonctionnalité désactivée" in _run(game_client)
    assert game_client.get("/api/populations").json()["settings"]["queue"] == []
    assert _bot(game_client).scheduler.store.get("populations")["enabled"] is False


def test_a_target_limited_by_the_stocks_stays_in_the_list(game_client, game):
    _scan(game_client)
    _set(game_client, [_line("rempart-de-fer", "target", 10_000_000, CONCORDE)])
    _run(game_client)
    assert len(game_client.get("/api/populations").json()["settings"]["queue"]) == 1


def test_a_once_line_leaves_the_list_when_done_on_all_its_planets_and_a_loop_line_never_does(game_client, game):
    _scan(game_client)
    _set(game_client, [{**_line("mineur", "fixed", 10), "repeat": False}, _line("pilote", "fixed", 1)])
    _run(game_client)
    assert [ln["population"] for ln in game_client.get("/api/populations").json()["settings"]["queue"]] == ["pilote"]


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


def test_a_once_line_recruits_one_time_per_planet_then_stops(game_client, game):
    _scan(game_client)
    _set(game_client, [{**_line("mineur", "fixed", 10), "repeat": False}])
    _run(game_client)
    assert [(p, q) for p, _, q in _recruited(game)] == [(CONCORDE, 10), (EGIDE, 10), (STRATE, 10)]
    game.complete()
    _bot(game_client).recruiter._cooldown.clear()
    _reload(game_client)
    _run(game_client)
    assert len(_recruited(game)) == 3  # rien de plus : chaque planète l'a fait une fois


def test_a_once_max_line_waits_until_something_is_payable_before_being_done(game_client, game):
    _scan(game_client)
    game.set_stocks(0, Fer=0, Cristal=0)
    _set(game_client, [{**_line("mineur", "max", planet=CONCORDE), "repeat": False}])
    _run(game_client)
    assert game.actions() == []  # rien de payable : la ligne n'est pas « faite »
    game.set_stocks(0, Fer=1_000_000, Cristal=1_000_000)
    _reload(game_client)
    _run(game_client)
    assert len(_recruited(game)) == 1


def test_a_page_still_showing_the_previous_planet_stops_the_pass_before_any_click(game_client, game, monkeypatch):
    monkeypatch.setattr(builder, "SETTLE_SECONDS", 1.5)
    _scan(game_client)
    _set(game_client, [_line("mineur", "fixed", 10)])
    recruiter = _bot(game_client).recruiter

    async def frozen_page(read=None):  # la page affiche toujours les mêmes chiffres, quelle que soit la planète
        return (("Fer", 1.0),), ()

    monkeypatch.setattr(recruiter, "_signature", frozen_page)
    summary = _run(game_client)
    assert "arrêté" in summary and "planète précédente" in summary
    assert [p for p, _, _ in _recruited(game)] == [CONCORDE]  # la première planète est traitée, la deuxième est refusée


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
