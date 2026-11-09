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
