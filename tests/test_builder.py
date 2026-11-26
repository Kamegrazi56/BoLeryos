"""Lancement des constructions : clic sur « Améliorer » dans l'ordre de priorité, une construction par planète et par passage."""

import pytest

from app import builder
from app.builder import parse_duration

MINE = {"building": "mine-de-fer", "level": 45}  # 201,8 M de Fer + 50,4 M de Cristal : seule Egide peut payer
CENTRALE = {"building": "centrale-energetique", "level": 30}  # 53,7 M de Fer + 21,5 M de Cristal : Concorde peut payer
CASERNE = {"building": "caserne", "level": 25}  # page Infrastructures, 200,7 M + 200,7 M : seule Egide peut payer


def _bot(client):
    return client.app.state.bot


def _scan(client):
    assert len(client.post("/api/planets/scan").json()["planets"]) == 3


def _run(client):
    return client.portal.call(_bot(client).builder.run_pass)


def _set(client, queue, scope="all", planet=""):
    planets = [planet] if scope == "one" else []
    assert client.put("/api/constructions", json={"planets": planets, "queue": queue}).status_code == 200


def _launched(game):
    return [(a["planet"], a["name"], a["level_after"]) for a in game.actions() if a["kind"] == "building"]


# ------------------------------------------------------------------------------ durées
@pytest.mark.parametrize(
    "text,seconds",
    [("15h 36m", 56160.0), ("3m 10s", 190.0), ("5.00s", 5.0), ("17.90s", 17.9), ("1j 2h", 93600.0), ("", None), ("bientôt", None)],
)
def test_durations_are_read_in_seconds(text, seconds):
    assert parse_duration(text) == seconds


# ------------------------------------------------------------------------------ passage
def test_nothing_happens_with_an_empty_list_or_no_known_planet(game_client, game):
    assert "rien à faire" in _run(game_client)
    _set(game_client, [CENTRALE])
    assert "scan des planètes" in _run(game_client)  # aucune planète connue : on ne devine pas
    assert game.actions() == []


def test_each_planet_gets_its_first_affordable_line_and_only_one(game_client, game):
    _scan(game_client)
    _set(game_client, [MINE, CENTRALE, CASERNE])
    summary = _run(game_client)
    assert _launched(game) == [("8-50-Concorde", "Centrale Énergétique", 28), ("6-79-Egide", "Mine de Fer", 41)]
    assert "2 construction(s) lancée(s)" in summary and "1 planète(s) sans rien à lancer" in summary


def _set_modes(client, queue, planet, modes):
    body = {"planets": [planet], "queue": queue, "modes": modes}
    assert client.put("/api/constructions", json=body).status_code == 200


def test_the_default_mode_launches_the_first_line_the_planet_can_pay(game_client, game):
    _scan(game_client)
    _set_modes(game_client, [MINE, CENTRALE], "8-50-Concorde", {})  # Concorde ne peut pas payer la Mine, mais la Centrale
    _run(game_client)
    assert _launched(game) == [("8-50-Concorde", "Centrale Énergétique", 28)]


def test_the_order_mode_waits_for_the_first_line_instead_of_skipping_it(game_client, game):
    _scan(game_client)
    _set_modes(game_client, [MINE, CENTRALE], "8-50-Concorde", {"8-50-Concorde": "order"})
    summary = _run(game_client)
    assert _launched(game) == []  # la Mine est en tête et n'est pas payable : on l'attend, la Centrale ne passe pas devant
    assert "0 construction(s) lancée(s)" in summary


def test_the_order_mode_still_launches_the_first_line_when_it_is_payable(game_client, game):
    _scan(game_client)
    _set_modes(game_client, [CENTRALE, MINE], "8-50-Concorde", {"8-50-Concorde": "order"})
    _run(game_client)
    assert _launched(game) == [("8-50-Concorde", "Centrale Énergétique", 28)]


def test_a_mode_only_applies_to_its_own_planet(game_client, game):
    _scan(game_client)
    _set(game_client, [MINE, CENTRALE])  # toutes les planètes
    assert game_client.put("/api/constructions", json={"queue": [MINE, CENTRALE], "modes": {"8-50-Concorde": "order"}}).status_code == 200
    _run(game_client)
    assert _launched(game) == [("6-79-Egide", "Mine de Fer", 41)]  # Egide : première possible ; Concorde : en attente de la Mine


def test_priority_order_decides_which_building_goes_first(game_client, game):
    _scan(game_client)
    _set(game_client, [CASERNE, MINE], scope="one", planet="6-79-Egide")  # la Caserne est avant la Mine
    _run(game_client)
    assert _launched(game) == [("6-79-Egide", "Caserne", 19)]


def test_a_building_of_the_other_tab_is_found_even_when_the_tab_is_slow_to_show(game_client, game):
    # Vrai jeu (08/11) : Terraformeur (Infrastructures) + Centrale (Industrie) → « bouton Améliorer de Terraformeur introuvable »,
    # car les cartes de l'ancien onglet restent un instant affichées après le clic d'onglet.
    game.reset(slow_tabs=True)
    _scan(game_client)
    _set(game_client, [CASERNE, MINE], scope="one", planet="6-79-Egide")
    summary = _run(game_client)
    assert _launched(game) == [("6-79-Egide", "Caserne", 19)], summary


def test_a_planet_without_any_upgrade_button_is_skipped_and_the_pass_goes_on(game_client, game):
    # Vrai jeu (08/11) : « la page Infrastructures ne s'affiche pas » sur une planète dont aucune carte n'a de bouton « Améliorer ».
    game.reset(hide_upgrade=[0])  # Concorde, qui pourrait payer la Centrale
    page = _bot(game_client)._page

    async def open_facilities():  # on part d'Infrastructures : il faut cliquer sur l'onglet Industrie
        await page.goto(game.url + "/game/asterion/facilities")

    game_client.portal.call(open_facilities)
    _scan(game_client)
    _set(game_client, [CENTRALE])
    summary = _run(game_client)
    launched = _launched(game)
    assert launched and all(planet != "8-50-Concorde" for planet, _, _ in launched), summary
    assert "arrêté" not in summary


def test_a_line_whose_target_level_is_reached_is_skipped(game_client, game):
    _scan(game_client)
    _set(game_client, [{"building": "mine-de-fer", "level": 40}, CASERNE], scope="one", planet="6-79-Egide")  # Mine déjà niveau 40
    _run(game_client)
    assert _launched(game) == [("6-79-Egide", "Caserne", 19)]


def test_a_reached_line_leaves_the_list_but_an_unfinished_one_stays(game_client, game):
    _scan(game_client)
    _set(game_client, [{"building": "mine-de-fer", "level": 40}, CASERNE], scope="one", planet="6-79-Egide")
    _run(game_client)
    assert [ln["building"] for ln in game_client.get("/api/constructions").json()["settings"]["queue"]] == [CASERNE["building"]]


def test_with_all_planets_a_line_leaves_the_list_only_when_reached_everywhere(game_client, game):
    _scan(game_client)
    _set(game_client, [{"building": "mine-de-fer", "level": 1}, {"building": "mine-de-fer".replace("mine-de-fer", CASERNE["building"]), "level": 500}])
    _run(game_client)
    assert [ln["building"] for ln in game_client.get("/api/constructions").json()["settings"]["queue"]] == [CASERNE["building"]]


# ------------------------------------------------------------------------------ objectifs propres à une planète
CONCORDE, EGIDE = "8-50-Concorde", "6-79-Egide"
MINE_40 = {"building": "mine-de-fer", "level": 40}  # la Mine de Fer est niveau 40 sur toutes les planètes du faux jeu


def _own(line, planet):
    return {**line, "planet": planet}


def _queue(client):
    return client.get("/api/constructions").json()["settings"]["queue"]


def test_a_line_for_one_planet_touches_only_that_planet(game_client, game):
    _scan(game_client)
    _set(game_client, [_own(CENTRALE, CONCORDE)])  # « toutes les planètes » choisies, mais la ligne ne vaut que pour Concorde
    summary = _run(game_client)
    assert _launched(game) == [(CONCORDE, "Centrale Énergétique", 28)]  # Egide, qui aurait pu payer la Centrale, n'est pas visitée
    assert "arrêté" not in summary


def test_a_line_for_a_planet_outside_the_chosen_planets_is_still_applied(game_client, game):
    _scan(game_client)
    assert game_client.put("/api/constructions", json={"planets": [EGIDE], "queue": [CASERNE, _own(CENTRALE, CONCORDE)]}).status_code == 200
    _run(game_client)
    assert sorted(_launched(game)) == sorted([(EGIDE, "Caserne", 19), (CONCORDE, "Centrale Énergétique", 28)])


def test_the_line_of_a_planet_wins_over_the_all_planets_line_of_the_same_building(game_client, game):
    _scan(game_client)
    _set(game_client, [CENTRALE, _own({"building": "centrale-energetique", "level": 1}, CONCORDE)])  # Concorde : déjà au-dessus de 1
    _run(game_client)
    assert all(planet != CONCORDE for planet, _, _ in _launched(game))  # la Centrale de Concorde n'est pas montée (objectif propre atteint)
    assert any(planet == EGIDE for planet, _, _ in _launched(game))  # les autres planètes suivent la ligne commune


def test_a_per_planet_line_leaves_the_list_when_reached_on_its_planet_only(game_client, game):
    game.reset(levels={"0": {"Mine de Fer": 30}})  # Concorde est en dessous de 40, Egide est à 40
    _scan(game_client)
    _set(game_client, [_own(MINE_40, EGIDE), _own(MINE_40, CONCORDE)])
    _run(game_client)
    assert _queue(game_client) == [_own(MINE_40, CONCORDE)]  # la ligne atteinte sur Egide part, celle de Concorde reste


def test_a_planet_line_below_the_all_planets_line_stays_as_a_cap(game_client, game):
    _scan(game_client)
    common = {"building": "mine-de-fer", "level": 500}
    _set(game_client, [common, _own(MINE_40, EGIDE)])  # Egide plafonnée à 40 (déjà atteint) ; la ligne commune vise 500
    _run(game_client)
    assert _own(MINE_40, EGIDE) in _queue(game_client) and common in _queue(game_client)  # la retirer ferait remonter Egide jusqu'à 500
    assert all(planet != EGIDE for planet, _, _ in _launched(game))


def test_a_line_for_a_planet_the_game_no_longer_knows_does_not_stop_the_pass(game_client, game):
    _scan(game_client)
    game_client.app.state.bot.constructions.replace({"queue": [_own(CENTRALE, "Nulle-Part"), _own(CENTRALE, CONCORDE)]})  # chargé sans contrôle
    summary = _run(game_client)
    assert _launched(game) == [(CONCORDE, "Centrale Énergétique", 28)] and "arrêté" not in summary
    assert _own(CENTRALE, "Nulle-Part") in _queue(game_client)  # la ligne reste (à supprimer par l'utilisateur), simplement ignorée


def test_an_all_planets_line_ignores_the_planets_that_have_their_own_line(game_client, game):
    game.reset(levels={"0": {"Mine de Fer": 30}})  # Concorde est en dessous de 40 ; Egide et Strate sont à 40
    _scan(game_client)
    own = _own({**MINE_40, "level": 500}, CONCORDE)  # Concorde a sa propre ligne, loin d'être atteinte
    _set(game_client, [MINE_40, own])
    _run(game_client)
    assert MINE_40 not in _queue(game_client)  # la ligne commune est atteinte sur les planètes qui la suivent : Concorde ne compte plus pour elle
    assert own in _queue(game_client)  # la ligne de Concorde reste


def test_a_single_chosen_planet_is_the_only_one_touched(game_client, game):
    _scan(game_client)
    _set(game_client, [CENTRALE], scope="one", planet="8-50-Concorde")
    _run(game_client)
    assert _launched(game) == [("8-50-Concorde", "Centrale Énergétique", 28)]


def test_a_planet_entry_the_mouse_cannot_reach_is_selected_with_the_keyboard(game_client, game):
    """Écran bas : la barre du bas masque les dernières planètes de la liste (constaté sur le vrai jeu), le clic tomberait à côté."""
    _scan(game_client)
    _set(game_client, [CENTRALE], scope="one", planet="8-50-Concorde")
    page = _bot(game_client)._page

    async def hide_list_from_the_mouse():
        await page.add_style_tag(content="#navlist { pointer-events: none; }")  # la souris traverse la liste

    game_client.portal.call(hide_list_from_the_mouse)
    assert "1 construction(s)" in _run(game_client)
    assert _launched(game) == [("8-50-Concorde", "Centrale Énergétique", 28)]


def test_the_stocks_are_really_debited_in_the_game(game_client, game):
    _scan(game_client)
    _set(game_client, [CENTRALE], scope="one", planet="8-50-Concorde")
    _run(game_client)
    assert game_client.post("/api/planets/scan").json()["planets"][0]["fer"] < 10e6  # 58 M - 53,7 M


def test_the_same_building_is_not_clicked_again_before_its_construction_is_over(game_client, game):
    _scan(game_client)
    _set(game_client, [CENTRALE], scope="one", planet="8-50-Concorde")
    _run(game_client)
    game.set_stocks(0, Fer=58.0e6, Cristal=22.36e6)  # de quoi payer à nouveau
    game.complete()  # le jeu dit « terminé » : niveau 28 < 30, bouton actif…
    game_client.portal.call(_bot(game_client)._page.reload)  # le faux jeu ne se met pas à jour seul, le vrai si
    assert "0 construction(s)" in _run(game_client)  # … mais la durée lue (5 min) n'est pas écoulée côté bot
    assert len(_launched(game)) == 1
    _bot(game_client).builder._cooldown.clear()  # durée écoulée
    game_client.portal.call(_bot(game_client)._page.reload)
    _run(game_client)
    assert [a[2] for a in _launched(game)] == [28, 29]


def test_manual_control_stops_the_pass_without_any_click(game_client, game):
    _scan(game_client)
    _set(game_client, [CENTRALE], scope="one", planet="8-50-Concorde")
    _bot(game_client)._manual = True
    assert "interrompu" in _run(game_client)
    assert game.actions() == []


def test_a_planet_missing_from_the_game_stops_the_pass_with_a_message(game_client, game):
    _scan(game_client)
    _set(game_client, [CENTRALE], scope="one", planet="8-50-Concorde")
    _bot(game_client).constructions._data.update({"planets": ["Nulle-Part"]})  # contourne la validation : le jeu ne la connaît pas
    assert "arrêté" in _run(game_client) and game.actions() == []


def test_two_launches_in_a_row_without_a_debit_disable_the_feature(game_client, game, monkeypatch):
    monkeypatch.setattr(builder, "CONFIRM_SECONDS", 0.6)
    game.reset(no_debit=True)  # le clic passe mais rien n'est débité : le bot ne peut pas confirmer
    _scan(game_client)
    _set(game_client, [MINE, CENTRALE])
    bot = _bot(game_client)
    game_client.put("/api/features/constructions", json={"enabled": True, "min_minutes": 1, "max_minutes": 2})
    summary = _run(game_client)
    assert len(_launched(game)) == 2 and "désactivées" in summary
    assert bot.scheduler.store.get("constructions")["enabled"] is False
    logs = [e["msg"] for e in game_client.get("/api/status?limit=200").json()["logs"]]
    assert any("débit des ressources n'a pas été constaté" in line for line in logs)


# ------------------------------------------------------------------ contrôle du débit avec une forte production
def test_a_debit_is_seen_when_the_stock_drops_by_the_expected_cost():
    assert builder.debit_seen(1_000.0, 500.0, 400.0)
    assert not builder.debit_seen(1_000.0, 1_000.0, 400.0)


def test_a_debit_hidden_by_the_production_is_still_seen():
    """Vrai jeu (Asterion) : la production gagne plus en 8 s que ne coûtent 10 recrues, le stock affiché monte malgré le débit."""
    was, now, cost = 575_700_000.0, 575_850_000.0, 100_000.0
    assert not builder.debit_seen(was, now, cost)  # sans tenir compte de la production : « non constaté »
    assert builder.debit_seen(was, now, cost, rate=30_000.0, elapsed=8.0)  # sans dépense il aurait monté de 240 000 ; il n'a monté que de 150 000 : 90 000 ont été dépensés


def test_no_debit_is_still_refused_when_the_production_explains_the_whole_rise():
    assert not builder.debit_seen(1_000_000.0, 1_240_000.0, 100_000.0, rate=30_000.0, elapsed=8.0)


def test_the_production_rate_is_measured_before_the_click(monkeypatch):
    import asyncio

    reads = iter([{"Fer": 1_000.0, "Cristal": None}, {"Fer": 1_500.0, "Cristal": None}])
    monkeypatch.setattr(builder, "RATE_PROBE_SECONDS", 0.05)
    page = builder.ConstructionPass(None)

    async def fake_stocks():
        return next(reads)

    page._stocks = fake_stocks
    stocks, rates, at = asyncio.run(page._stocks_timed())
    assert stocks["Fer"] == 1_500.0 and 5_000.0 < rates["Fer"] < 20_000.0 and "Cristal" not in rates and at > 0


def test_planets_listed_as_rows_instead_of_buttons_can_be_selected(game_client, game):
    """Vrai jeu (20/11) : les entrées de la liste des planètes ne sont plus des boutons ; changer de planète doit marcher."""
    game.reset(planet_rows=True)
    _scan(game_client)
    _set(game_client, [MINE, CENTRALE, CASERNE])
    summary = _run(game_client)
    assert _launched(game) == [("8-50-Concorde", "Centrale Énergétique", 28), ("6-79-Egide", "Mine de Fer", 41)]
    assert "2 construction(s) lancée(s)" in summary


def test_a_planet_list_that_opens_slowly_is_waited_for(game_client, game, monkeypatch):
    """Vrai jeu (20/11) : la liste s'ouvre avec une animation, l'entrée peut manquer à la première lecture."""
    real = builder.MARK_PLANET_ENTRY_JS
    monkeypatch.setattr(
        builder, "MARK_PLANET_ENTRY_JS",
        "(args) => { window.__lookups = (window.__lookups || 0) + 1; if (window.__lookups < 3) return false; return (" + real.strip() + ")(args); }",
    )
    _scan(game_client)
    _set(game_client, [MINE, CENTRALE, CASERNE])
    assert "2 construction(s) lancée(s)" in _run(game_client)
    assert _launched(game) == [("8-50-Concorde", "Centrale Énergétique", 28), ("6-79-Egide", "Mine de Fer", 41)]


def test_the_planet_list_diagnostic_counts_what_the_page_shows(game_client):
    async def diagnose():
        return await _bot(game_client).builder._diagnose_planet_list("Introuvable")

    sentence = game_client.portal.call(diagnose)
    assert sentence.startswith("nom trouvé 0 fois dont 0 visible(s)") and "coordonnées visibles" in sentence and "sélecteur(s) de planète" in sentence


def test_a_planet_list_that_is_already_open_is_not_closed_by_another_click(game_client, game):
    """Vrai jeu (21/11) : un clic de plus sur le sélecteur refermait la liste, et la planète était « absente de la liste »."""
    _scan(game_client)
    _set(game_client, [MINE, CENTRALE, CASERNE])

    async def open_the_list():
        await _bot(game_client)._page.click("#planet-btn")  # le faux jeu ouvre alors la liste de navigation

    game_client.portal.call(open_the_list)
    assert "2 construction(s) lancée(s)" in _run(game_client)
    assert _launched(game) == [("8-50-Concorde", "Centrale Énergétique", 28), ("6-79-Egide", "Mine de Fer", 41)]
