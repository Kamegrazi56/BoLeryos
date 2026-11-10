"""Fouilles dans le faux jeu : relevé des sites libres, lancements Fouille par Fouille (durée et équipe de chacune), Mira Venn,
délai avant relance, relance à l'identique."""

EGIDE, CONCORDE, STRATE = "6-79-Egide", "8-50-Concorde", "9-169-Strate"
TEAM = {"Scientifique": 2500, "Mineur": 2500}
BASE = {"duration": 6, "units": TEAM, "copies": 1, "fouilles": [], "planets": [{"name": EGIDE, "copies": 2}],
        "per_slot": False, "mira": EGIDE, "delay_min": 0, "delay_max": 0}


def _bot(client):
    return client.app.state.bot


def _scan(client):
    assert len(client.post("/api/planets/scan").json()["planets"]) == 3


def _set(client, **patch):
    res = client.put("/api/excavations", json={**BASE, **patch})
    assert res.status_code == 200, res.text


def _run(client):
    return client.portal.call(_bot(client).excavator.run_pass)


def _launched(game):
    return [a for a in game.actions() if a["kind"] == "excavation"]


def _forbidden(game):
    return [a for a in game.actions() if a["kind"] == "forbidden"]


def test_the_daily_record_finds_the_nearest_free_slots(game_client, game):
    _scan(game_client)
    _set(game_client, planets=[{"name": EGIDE, "copies": 2}, {"name": CONCORDE, "copies": 2}, {"name": STRATE, "copies": 2}])
    daily = game_client.post("/api/excavations/scan").json()["daily"]
    assert [s["address"] for s in daily["sites"][EGIDE]] == ["M58 80 21", "M68 75 95"]
    assert daily["sites"][CONCORDE][0]["address"] == "Z25 70 30" and daily["sites"][CONCORDE][0]["source"] == "carte"
    assert STRATE in daily["error"] and daily["max_active"] == 6
    assert _launched(game) == []  # le relevé ne lance rien


def test_each_excavation_goes_to_the_nearest_free_site_and_mira_goes_with_the_first(game_client, game):
    _scan(game_client)
    _set(game_client)
    summary = _run(game_client)
    assert summary.startswith("2 Fouille(s) lancée(s)"), summary
    first, second = _launched(game)
    assert (first["planet"], first["target"], first["hours"], first["mira"]) == (EGIDE, "M58 80 21", 6, True)
    assert first["units"] == TEAM
    assert (second["target"], second["mira"]) == ("M68 75 95", False)  # Mira part avec la Fouille 1 seulement
    assert [(t["site"], t["slot"]) for t in _bot(game_client).excavations.tracked()] == [("M58 80 21", 0), ("M68 75 95", 1)]
    assert "2 Fouille(s) déjà en cours" in _run(game_client) and len(_launched(game)) == 2  # tout est parti : rien de plus
    assert _forbidden(game) == []


def test_each_excavation_has_its_own_duration_and_team_and_is_relaunched_identically(game_client, game):
    _scan(game_client)
    small = {"Mineur": 1000}
    _set(game_client, per_slot=True, mira="",
         planets=[{"name": EGIDE, "copies": 2, "fouilles": [{"duration": 6, "units": TEAM}, {"duration": 24, "units": small}]}])
    _run(game_client)
    assert [(a["target"], a["hours"], a["units"]) for a in _launched(game)] == [("M58 80 21", 6, TEAM), ("M68 75 95", 24, small)]
    game.set_exc(active=[{"origin": EGIDE, "site": "M58 80 21", "hours": 6, "end_in": 3000}])  # la Fouille 2 (24 h) est finie
    _run(game_client)
    relaunched = _launched(game)[-1]
    assert (len(_launched(game)), relaunched["hours"], relaunched["units"]) == (3, 24, small)  # même durée, même équipe


def test_a_finished_excavation_is_relaunched_after_the_delay(game_client, game):
    _scan(game_client)
    _set(game_client, planets=[{"name": EGIDE, "copies": 1}], mira="")
    _run(game_client)
    game.set_exc(active=[])  # la Fouille est terminée dans le jeu
    _run(game_client)
    assert [a["target"] for a in _launched(game)] == ["M58 80 21", "M58 80 21"]  # délai nul : relancée tout de suite


def test_the_place_waits_for_the_delay_chosen_by_the_player(game_client, game):
    _scan(game_client)
    _set(game_client, planets=[{"name": EGIDE, "copies": 1}], mira="", delay_min=30, delay_max=60)
    _run(game_client)
    game.set_exc(active=[])
    assert "1 Fouille(s) déjà en cours" in _run(game_client) and len(_launched(game)) == 1
    tracked = _bot(game_client).excavations.tracked()
    assert len(tracked) == 1 and 30 * 60 <= tracked[0]["ready"] - tracked[0]["end"] <= 60 * 60


def test_an_excavation_running_in_the_game_takes_a_place_and_its_site(game_client, game):
    _scan(game_client)
    game.set_exc(active=[{"origin": EGIDE, "site": "M58 80 21", "hours": 6, "end_in": 3000}])  # lancée à la main
    _set(game_client, mira="")
    _run(game_client)
    assert [a["target"] for a in _launched(game)] == ["M68 75 95"]  # une seule place libre, et pas sur le site déjà fouillé


def test_a_planet_chosen_after_the_daily_record_gets_its_sites_at_the_next_pass(game_client, game):
    _scan(game_client)
    _set(game_client, planets=[{"name": EGIDE, "copies": 1}], mira="")
    _run(game_client)  # relevé du jour : Egide seule
    _set(game_client, planets=[{"name": EGIDE, "copies": 1}, {"name": CONCORDE, "copies": 1}], mira="")
    _run(game_client)
    assert _bot(game_client).excavations.daily()["sites"][CONCORDE][0]["address"] == "Z25 70 30"
    assert [a["planet"] for a in _launched(game)] == [EGIDE, CONCORDE]


def test_nothing_is_launched_when_the_game_has_no_free_excavation(game_client, game):
    _scan(game_client)
    game.set_exc(max=1, active=[{"origin": CONCORDE, "site": "Z25 70 30", "hours": 3, "end_in": 3000}])
    _set(game_client)
    summary = _run(game_client)
    assert "toutes les Fouilles possibles sont en cours (1/1)" in summary and _launched(game) == []


def test_mira_busy_means_leaving_without_a_hero(game_client, game):
    _scan(game_client)
    game.set_exc(mira_busy=True)
    _set(game_client, planets=[{"name": EGIDE, "copies": 1}])
    _run(game_client)
    assert [a["mira"] for a in _launched(game)] == [False]


def test_nothing_is_launched_without_a_team(game_client, game):
    assert "rien à lancer" in _run(game_client)  # réglages par défaut : pas d'équipe
    assert game.actions() == []


def test_two_unconfirmed_launches_stop_the_feature(game_client, game):
    _scan(game_client)
    game.set_exc(no_effect=True)  # le clic ne fait rien de visible
    _set(game_client)
    summary = _run(game_client)
    assert "désactivée après deux lancements non confirmés" in summary and _launched(game) == []
