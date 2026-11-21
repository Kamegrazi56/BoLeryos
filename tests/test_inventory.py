"""Inventaire de l'empire (Empire › Inventaire) : niveaux des bâtiments de chaque planète, lus en une seule page."""

import json

import pytest

from app.inventory import InventoryStore, parse_inventory, parse_researches
from app.planets import GameUiError

NNBSP = " "  # espace fine insécable : séparateur de milliers du jeu


def _head(*planets):
    cells = [{"text": "Objet", "leaves": ["Objet"]}]
    cells += [{"text": f"{name}{coords}", "leaves": [name, coords]} for name, coords in planets]
    return cells + [{"text": "Total", "leaves": ["Total"]}]


# Données relevées sur le vrai jeu le 2026-11-15 (univers kael) : une ligne de section = une seule cellule.
REAL = {
    "head": _head(("K64-Ker", "K64 87 14"), ("Colonie 2", "O23 81 72"), ("Colonie 4", "N35 63 60"), ("Colonie 5", "S47 52 26")),
    "rows": [
        ["Caractéristiques"],
        ["Cases", "115 / 240", "131 / 228", "151 / 221", "161 / 212", "558 / 901"],
        ["Fer", f"577{NNBSP}471", "11 890", "43 109", "6 869", "639 340"],
        ["Installations minières"],
        ["Mine de Fer", "17", "17", "14", "11", "59"],
        ["Extracteur de Cristal", "17", "16", "13", "11", "57"],
        ["Synthétiseur d'Éther", "15", "11", "10", "7", "43"],
        ["Installations énergétiques"],
        ["Centrale Énergétique", "17", "16", "13", "11", "57"],
        ["Centrale à Impulsion", "0", "0", "0", "0", "0"],
        ["Installations techniques"],
        ["Centre de Recherche", "10", "3", "0", "0", "13"],
        ["Tour à résonance", "4", "3", "2", "1", "10"],
        ["Terraformeur", "0", "0", "0", "0", "0"],
        ["Technologies civiles"],
        ["Raffinement de l’Éther", "—", "—", "—", "—", "0"],
    ],
}


def _levels(planets, name):
    return next(p for p in planets if p["name"] == name)["levels"]


# ------------------------------------------------------------------------------ analyse du tableau
def test_each_planet_gets_the_levels_of_its_column():
    planets = parse_inventory(REAL)
    assert [(p["name"], p["coords"]) for p in planets] == [
        ("K64-Ker", "K64 87 14"), ("Colonie 2", "O23 81 72"), ("Colonie 4", "N35 63 60"), ("Colonie 5", "S47 52 26"),
    ]
    assert _levels(planets, "K64-Ker")["mine-de-fer"] == 17
    assert _levels(planets, "Colonie 4")["centre-de-recherche"] == 0
    assert _levels(planets, "Colonie 5")["synthetiseur-d-ether"] == 7  # apostrophe droite


def test_game_labels_are_matched_whatever_their_case_or_apostrophe():
    levels = _levels(parse_inventory(REAL), "Colonie 2")
    assert levels["tour-a-resonance"] == 3  # « Tour à résonance » (minuscule) dans le jeu, « Tour à Résonance » dans le bot
    assert levels["centrale-a-impulsion"] == 0


def test_only_the_sixteen_buildings_are_kept():
    levels = _levels(parse_inventory(REAL), "K64-Ker")
    assert set(levels) == {"mine-de-fer", "extracteur-de-cristal", "synthetiseur-d-ether", "centrale-energetique",
                           "centrale-a-impulsion", "centre-de-recherche", "tour-a-resonance", "terraformeur"}  # ni stocks, ni technologies


def test_the_total_column_is_never_taken_for_a_planet():
    assert "Total" not in [p["name"] for p in parse_inventory(REAL)]


def test_a_planet_without_the_split_leaves_is_read_from_its_text():
    raw = {**REAL, "head": [{"text": "Objet", "leaves": []}, {"text": "6-79-EgideM69 38 06", "leaves": []}, {"text": "Total", "leaves": []}],
           "rows": [["Mine de Fer", "41", "41"]]}
    (planet,) = parse_inventory(raw)
    assert (planet["name"], planet["coords"]) == ("6-79-Egide", "M69 38 06")


def test_thousands_separators_and_dashes_are_handled():
    raw = {"head": _head(("Egide", "M69 38 06")), "rows": [["Mine de Fer", f"1{NNBSP}234", "1 234"], ["Caserne", "—", "—"]]}
    (planet,) = parse_inventory(raw)
    assert planet["levels"] == {"mine-de-fer": 1234}  # « — » : pas de niveau lu


def test_a_row_with_the_wrong_number_of_cells_is_ignored():
    raw = {**REAL, "rows": [["Mine de Fer", "17", "17"], ["Caserne", "11", "10", "6", "3", "30"]]}
    assert _levels(parse_inventory(raw), "K64-Ker") == {"caserne": 11}  # la ligne tronquée (page en cours d'affichage) est écartée


def test_a_table_without_any_building_is_an_error():
    with pytest.raises(GameUiError, match="bâtiments"):
        parse_inventory({"head": REAL["head"], "rows": [["Caractéristiques"], ["Cases", "1", "1", "1", "1", "4"]]})
    with pytest.raises(GameUiError, match="Objet"):
        parse_inventory({"head": [], "rows": []})


# ------------------------------------------------------------------------------ niveaux de recherche (colonne « Total »)
TECH = {**REAL, "rows": REAL["rows"] + [
    ["Technologies civiles"],
    ["Ordinateur Quantique", "—", "—", "—", "—", "21"],
    ["Raffinement de l’Éther", "—", "—", "—", "—", "0"],
    ["Technologies militaires"],
    ["Technologie d'espionnage", "—", "—", "—", "—", f"1{NNBSP}026"],  # apostrophe droite, minuscule, séparateur de milliers
    ["Virus des Piliers", "—", "—", "—", "—", "—"],  # pas de niveau lisible
]}


def test_research_levels_are_read_from_the_total_column():
    assert parse_researches(TECH) == {"ordinateur-quantique": 21, "raffinement-de-l-ether": 0, "technologie-d-espionnage": 1026}


def test_a_table_without_technologies_gives_no_research_level():
    no_tech = {**REAL, "rows": [r for r in REAL["rows"] if r[0] != "Raffinement de l’Éther"]}
    assert parse_researches(no_tech) == {}
    assert parse_researches({"head": [], "rows": []}) == {}


def test_buildings_are_never_taken_for_researches_and_researches_never_for_buildings():
    assert not set(parse_researches(TECH)) & {"mine-de-fer", "caserne"}
    assert "ordinateur-quantique" not in _levels(parse_inventory(TECH), "K64-Ker")


def test_a_research_row_with_the_wrong_number_of_cells_is_ignored():
    raw = {**TECH, "rows": [["Ordinateur Quantique", "—", "21"]]}
    assert parse_researches(raw) == {}


# ------------------------------------------------------------------------------ effectifs de population (« Unités terrestres »)
UNITS = {**REAL, "rows": REAL["rows"] + [
    ["Unités terrestres"],
    ["Soldat", "2 507 458", "10", "0", "0", "2 507 468"],  # automatique : pas une population recrutable
    ["Pilote", "57", "0", "0", "0", "57"],
    ["Scientifique", "110", "44", "0", "0", "154"],
    ["Faucheur Cinétique", f"1{NNBSP}380{NNBSP}303", "—", "0", "0", "1 380 303"],
]}


def _units(planets, name):
    return next(p for p in planets if p["name"] == name)["units"]


def test_each_planet_gets_the_head_count_of_every_recruitable_population():
    planets = parse_inventory(UNITS)
    assert _units(planets, "K64-Ker") == {"pilote": 57, "scientifique": 110, "faucheur-cinetique": 1380303}
    assert _units(planets, "Colonie 2") == {"pilote": 0, "scientifique": 44}  # « — » : effectif inconnu, pas 0
    assert _units(planets, "Colonie 5") == {"pilote": 0, "scientifique": 0, "faucheur-cinetique": 0}


def test_the_soldier_and_the_total_column_are_not_head_counts():
    planets = parse_inventory(UNITS)
    assert all("soldat" not in p["units"] for p in planets)
    assert len(planets) == 4 and all(p["name"] != "Total" for p in planets)  # la colonne « Total » (57) n'est pas une planète


def test_a_table_without_units_gives_planets_with_no_head_count():
    assert all(p["units"] == {} for p in parse_inventory(REAL))


def test_a_unit_row_with_the_wrong_number_of_cells_is_ignored():
    raw = {**REAL, "rows": REAL["rows"] + [["Pilote", "57", "57"]]}
    assert all(p["units"] == {} for p in parse_inventory(raw))


def test_populations_are_never_taken_for_buildings():
    assert set(_levels(parse_inventory(UNITS), "K64-Ker")) == set(_levels(parse_inventory(REAL), "K64-Ker"))


def test_head_counts_survive_a_restart(tmp_path):
    InventoryStore(str(tmp_path)).set(parse_inventory(UNITS), "manuel")
    assert _units(InventoryStore(str(tmp_path)).get()["planets"], "K64-Ker")["pilote"] == 57


def test_a_file_saved_before_head_counts_existed_still_loads(tmp_path):
    old = {"planets": [{"name": "A", "coords": "", "levels": {"mine-de-fer": 3}}], "researches": {}, "read_at": 1.0, "source": "manuel"}
    (tmp_path / "inventory.json").write_text(json.dumps(old), encoding="utf8")
    assert InventoryStore(str(tmp_path)).get()["planets"][0]["levels"] == {"mine-de-fer": 3}


# ------------------------------------------------------------------------------ enregistrement
def test_research_levels_survive_a_restart_and_an_error(tmp_path):
    store = InventoryStore(str(tmp_path))
    assert store.get()["researches"] == {}
    store.set(parse_inventory(TECH), "manuel", parse_researches(TECH))
    store.set_error("Relevé impossible : test")
    assert InventoryStore(str(tmp_path)).get()["researches"]["ordinateur-quantique"] == 21
    assert store.get()["researches"]["ordinateur-quantique"] == 21  # l'erreur garde les niveaux d'avant


def test_a_file_saved_before_researches_existed_still_loads(tmp_path):
    (tmp_path / "inventory.json").write_text(json.dumps({"planets": [], "read_at": 1.0, "source": "manuel"}), encoding="utf8")
    assert InventoryStore(str(tmp_path)).get()["researches"] == {}
    (tmp_path / "inventory.json").write_text(json.dumps({"planets": [], "researches": {"x": "pas un entier"}}), encoding="utf8")
    assert InventoryStore(str(tmp_path)).get()["researches"] == {}


def test_the_last_reading_survives_a_restart(tmp_path):
    store = InventoryStore(str(tmp_path))
    assert store.get() == {"planets": [], "researches": {}, "read_at": None, "source": "", "error": ""}
    store.set(parse_inventory(REAL), "manuel")
    again = InventoryStore(str(tmp_path)).get()
    assert again["source"] == "manuel" and again["read_at"] is not None and again["error"] == ""
    assert _levels(again["planets"], "K64-Ker")["mine-de-fer"] == 17


def test_an_error_keeps_the_previous_levels(tmp_path):
    store = InventoryStore(str(tmp_path))
    store.set(parse_inventory(REAL), "manuel")
    store.set_error("Relevé impossible : test")
    data = store.get()
    assert data["error"] == "Relevé impossible : test" and len(data["planets"]) == 4  # le tableau d'avant reste affiché
    store.set(parse_inventory(REAL), "passage")
    assert store.get()["error"] == ""


def test_a_corrupt_file_gives_an_empty_inventory(tmp_path):
    (tmp_path / "inventory.json").write_text("{pas du json", encoding="utf8")
    assert InventoryStore(str(tmp_path)).get()["planets"] == []
    (tmp_path / "inventory.json").write_text(json.dumps({"planets": "x"}), encoding="utf8")
    assert InventoryStore(str(tmp_path)).get()["planets"] == []


def test_the_store_works_in_memory_without_a_data_folder():
    store = InventoryStore(None)
    store.set(parse_inventory(REAL), "manuel")
    assert len(store.get()["planets"]) == 4


# ------------------------------------------------------------------------------ relevé sur le (faux) jeu
def _refresh(client):
    res = client.post("/api/constructions/inventory/refresh")
    assert res.status_code == 200, res.text
    return res.json()["inventory"]


def test_the_bot_reads_every_planet_from_the_inventory_page(game_client, game):
    game.reset(levels={"0": {"Mine de Fer": 51}, "1": {"Mine de Fer": 52, "Caserne": 7}, "2": {"Mine de Fer": 53}})
    inventory = _refresh(game_client)
    assert inventory["error"] == "" and inventory["read_at"] is not None and inventory["source"] == "manuel"
    assert [p["name"] for p in inventory["planets"]] == ["8-50-Concorde", "6-79-Egide", "9-169-Strate"]
    assert [p["levels"]["mine-de-fer"] for p in inventory["planets"]] == [51, 52, 53]
    assert _levels(inventory["planets"], "6-79-Egide")["caserne"] == 7
    assert len(_levels(inventory["planets"], "6-79-Egide")) == 16
    assert game.actions() == []  # lecture seule : rien n'est cliqué dans le jeu


def test_the_bot_reads_the_research_levels_too(game_client, game):
    inventory = _refresh(game_client)
    levels = inventory["researches"]
    assert levels["ordinateur-quantique"] == 21 and levels["technologie-energie"] == 20  # technologies civiles
    assert levels["technologie-laser"] == 24 and levels["technologie-d-espionnage"] == 26  # technologies militaires
    assert game.actions() == []


def test_the_bot_reads_the_head_count_of_every_population_on_every_planet(game_client, game):
    inventory = _refresh(game_client)
    assert [p["units"]["pilote"] for p in inventory["planets"]] == [10, 10, 10]
    assert inventory["planets"][0]["units"]["technicien"] == 21331 and len(inventory["planets"][0]["units"]) == 10
    assert game.actions() == []


def test_the_populations_api_shows_the_reading(game_client, game):
    assert game_client.get("/api/populations").json()["inventory"]["planets"] == []
    _refresh(game_client)
    shown = game_client.get("/api/populations").json()["inventory"]
    assert len(shown["planets"]) == 3 and shown["reading"] is False and shown["planets"][0]["units"]["mineur"] == 5
    assert "levels" not in shown["planets"][0]  # l'onglet Population n'a pas besoin des niveaux de bâtiments


def test_the_researches_page_shows_the_levels_and_the_reading_state(game_client, game):
    assert game_client.get("/api/researches").json()["inventory"]["researches"] == {}
    _refresh(game_client)
    shown = game_client.get("/api/researches").json()["inventory"]
    assert shown["researches"]["technologie-laser"] == 24 and shown["reading"] is False and shown["error"] == ""


def test_columns_are_matched_by_planet_even_when_the_player_reorders_them(game_client, game):
    game.reset(empire_reversed=True, levels={"0": {"Mine de Fer": 51}, "2": {"Mine de Fer": 53}})
    inventory = _refresh(game_client)
    assert [p["name"] for p in inventory["planets"]] == ["9-169-Strate", "6-79-Egide", "8-50-Concorde"]  # ordre du jeu
    assert _levels(inventory["planets"], "8-50-Concorde")["mine-de-fer"] == 51
    assert _levels(inventory["planets"], "9-169-Strate")["mine-de-fer"] == 53


def test_the_reading_is_shown_by_the_constructions_api_and_kept(game_client, game):
    assert game_client.get("/api/constructions").json()["inventory"]["planets"] == []
    _refresh(game_client)
    shown = game_client.get("/api/constructions").json()["inventory"]
    assert len(shown["planets"]) == 3 and shown["reading"] is False


def test_a_refresh_outside_a_game_gives_a_french_error_and_keeps_the_table(client):
    res = client.post("/api/constructions/inventory/refresh")
    assert res.status_code == 200
    inventory = res.json()["inventory"]
    assert inventory["planets"] == [] and inventory["error"] and inventory["error"][0].isupper()


def test_the_refresh_route_checks_the_origin(game_client):
    assert game_client.post("/api/constructions/inventory/refresh", headers={"Origin": "http://evil.example"}).status_code == 403


# ------------------------------------------------------------------------------ au passage de construction
CENTRALE = {"building": "centrale-energetique", "level": 30}


def _run_pass(client):
    return client.portal.call(client.app.state.bot.builder.run_pass)


def _queue(client):
    assert client.post("/api/planets/scan").status_code == 200
    assert client.put("/api/constructions", json={"planets": [], "queue": [CENTRALE]}).status_code == 200


def test_each_construction_pass_refreshes_the_inventory(game_client, game):
    _queue(game_client)
    assert game_client.get("/api/constructions").json()["inventory"]["read_at"] is None
    _run_pass(game_client)
    inventory = game_client.get("/api/constructions").json()["inventory"]
    assert inventory["source"] == "passage" and len(inventory["planets"]) == 3 and inventory["error"] == ""
    built = [(a["planet"], a["name"]) for a in game.actions() if a["kind"] == "building"]
    assert built == [("8-50-Concorde", "Centrale Énergétique"), ("6-79-Egide", "Centrale Énergétique")]  # les constructions se font comme avant


CENTRALE_AT_TARGET = {"Centrale Énergétique": 30}


def _built(game):
    return [(a["planet"], a["name"]) for a in game.actions() if a["kind"] == "building"]


def test_planets_already_at_every_target_are_not_visited(game_client, game):
    game.reset(levels={"0": CENTRALE_AT_TARGET, "1": CENTRALE_AT_TARGET, "2": CENTRALE_AT_TARGET})
    _queue(game_client)
    summary = _run_pass(game_client)
    assert "0 construction(s) lancée(s)" in summary and "3 planète(s) déjà au niveau" in summary
    assert game.actions() == []
    assert game_client.get("/api/constructions").json()["settings"]["queue"] == []  # atteinte partout : la ligne est retirée comme avant


def test_only_the_planets_below_target_are_visited_and_built_on(game_client, game):
    game.reset(levels={"0": CENTRALE_AT_TARGET, "1": {"Centrale Énergétique": 10}, "2": {"Centrale Énergétique": 10}})
    _queue(game_client)
    summary = _run_pass(game_client)
    built = _built(game)
    assert ("6-79-Egide", "Centrale Énergétique") in built and all(planet != "8-50-Concorde" for planet, _ in built)
    assert "1 planète(s) déjà au niveau" in summary
    assert game_client.get("/api/constructions").json()["settings"]["queue"] == [CENTRALE]  # pas atteinte partout : la ligne reste


def test_a_line_for_one_planet_skips_that_planet_when_the_inventory_shows_it_reached(game_client, game):
    game.reset(levels={"0": CENTRALE_AT_TARGET, "1": {"Centrale Énergétique": 10}})
    assert game_client.post("/api/planets/scan").status_code == 200
    own = {**CENTRALE, "planet": "8-50-Concorde"}
    assert game_client.put("/api/constructions", json={"planets": [], "queue": [own]}).status_code == 200
    summary = _run_pass(game_client)
    assert "1 planète(s) déjà au niveau" in summary and "0 construction(s) lancée(s)" in summary
    assert game.actions() == []  # Concorde n'est pas visitée, et Egide (sans ligne) non plus
    assert game_client.get("/api/constructions").json()["settings"]["queue"] == []  # objectif atteint sur sa planète : la ligne part


def test_a_failed_reading_never_skips_a_planet_on_old_data(game_client, game, monkeypatch):
    game.reset(levels={"0": CENTRALE_AT_TARGET, "1": CENTRALE_AT_TARGET, "2": CENTRALE_AT_TARGET})
    _queue(game_client)
    _refresh(game_client)  # le dernier relevé en mémoire dit : tout est au niveau
    game.reset(levels={"0": {"Centrale Énergétique": 10}, "1": {"Centrale Énergétique": 10}, "2": {"Centrale Énergétique": 10}})
    bot = game_client.app.state.bot

    async def broken(self):
        raise GameUiError("tableau cassé")

    monkeypatch.setattr(type(bot.inventory_reader), "read", broken)
    summary = _run_pass(game_client)
    assert "déjà au niveau" not in summary and _built(game)  # on retourne voir chaque planète, comme avant


QUANTIQUE = {"research": "ordinateur-quantique", "level": 30}


def _research_pass(client):
    return client.portal.call(client.app.state.bot.researcher.run_pass)


def _research_queue(client):
    assert client.post("/api/planets/scan").status_code == 200
    assert client.put("/api/researches", json={"planet": "6-79-Egide", "queue": [QUANTIQUE]}).status_code == 200


def test_each_research_pass_refreshes_the_inventory_and_still_launches(game_client, game):
    _research_queue(game_client)
    summary = _research_pass(game_client)
    inventory = game_client.get("/api/researches").json()["inventory"]
    assert inventory["source"] == "passage" and inventory["researches"]["ordinateur-quantique"] == 21
    assert "Ordinateur Quantique" in summary and [a for a in game.actions() if a["kind"] == "research"]


def test_a_failing_inventory_reading_does_not_stop_the_researches(game_client, game, monkeypatch):
    _research_queue(game_client)
    bot = game_client.app.state.bot

    async def broken(self):
        raise GameUiError("tableau cassé")

    monkeypatch.setattr(type(bot.inventory_reader), "read", broken)
    assert "Ordinateur Quantique" in _research_pass(game_client)
    assert "tableau cassé" in game_client.get("/api/researches").json()["inventory"]["error"]


def test_a_browser_error_during_the_reading_is_kept_as_an_error_not_a_crash(game_client, game, monkeypatch):
    _queue(game_client)
    bot = game_client.app.state.bot

    async def timeout(self):
        raise TimeoutError("Page.goto: Timeout 30000ms exceeded")  # erreur Playwright, pas GameUiError

    monkeypatch.setattr(type(bot.inventory_reader), "read", timeout)
    assert "2 construction(s) lancée(s)" in _run_pass(game_client)  # le passage de constructions n'est pas interrompu
    inventory = _refresh(game_client)  # et le bouton répond au lieu d'une erreur 500
    assert "Timeout" in inventory["error"] and inventory["planets"] == []
    assert game_client.get("/api/constructions").json()["inventory"]["reading"] is False


def test_a_level_that_is_not_a_plain_number_is_skipped():
    raw = {"head": _head(("Egide", "M69 38 06")), "rows": [["Mine de Fer", "²", "²"], ["Caserne", "12", "12"]]}
    (planet,) = parse_inventory(raw)
    assert planet["levels"] == {"caserne": 12}


def test_a_failing_inventory_reading_does_not_stop_the_constructions(game_client, game, monkeypatch):
    _queue(game_client)
    bot = game_client.app.state.bot

    async def broken(self):
        raise GameUiError("tableau cassé")

    monkeypatch.setattr(type(bot.inventory_reader), "read", broken)
    summary = _run_pass(game_client)
    assert "2 construction(s) lancée(s)" in summary  # le passage continue comme avant
    inventory = game_client.get("/api/constructions").json()["inventory"]
    assert inventory["planets"] == [] and "tableau cassé" in inventory["error"]


def test_a_missing_table_says_what_the_page_contained(game_client, monkeypatch):
    """Sans tableau, l'erreur décrit la page (onglets, tableaux) au lieu de « tableau absent » tout court."""
    from app import inventory

    monkeypatch.setattr(inventory, "READ_INVENTORY_JS", "() => null")
    monkeypatch.setattr(inventory, "READ_ATTEMPTS", 14)  # assez pour déclencher le second clic sur l'onglet (RECLICK_AFTER)
    monkeypatch.setattr(inventory, "READ_POLL_SECONDS", 0.01)
    error = _refresh(game_client)["error"]
    assert "tableau absent ; page : onglets" in error and "tableau(x)" in error
