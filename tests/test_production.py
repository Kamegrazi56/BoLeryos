"""Production d'un nombre d'éléments : population, vaisseaux, composants, appareils spécialisés, défenses."""

import pytest

from tests.test_automation import MINES, _bot, _configure, _cycle, _force_due, _has, _logs, _runner

EGIDE = "6-79-Egide"  # la planète riche du faux jeu (index 1)
CONCORDE = "8-50-Concorde"  # la planète pauvre (index 0)


def line(section, name, mode="once", amount=1, maxi=False):
    return {"section": section, "name": name, "mode": mode, "amount": amount, "max": maxi}


def _produce(client, items, *, mode="dry_run", planet=EGIDE, enabled=True, priority=None, cap=50, recheck=15.0):
    body = {
        "mode": mode, "daily_cap": cap, "cycle": {"min_minutes": 0.02, "max_minutes": 0.02},
        "production": {"enabled": enabled, "scope": "one" if planet else "all", "planet": planet, "recheck_minutes": recheck, "queue": items},
    }
    if priority:
        body["priority"] = priority
    res = client.put("/api/automation/settings", json={"settings": body, "confirm_live": mode == "live"})
    assert res.status_code == 200, res.text
    return res.json()


def _launches(game):
    return [a for a in game.actions() if a["kind"] == "production"]


def _dash(client):
    return client.get("/api/dashboard").json()


# ------------------------------------------------------------------------------ simulation
def test_simulation_decides_and_summarises_but_never_clicks(game_client, game):
    _produce(game_client, [line("population", "Rempart de Fer", amount=50)])
    _cycle(game_client)
    assert game.actions() == []
    assert _has(game_client, f"[Simulation] Planète {EGIDE} : je lancerais 50 × Rempart de Fer")
    dash = _dash(game_client)
    assert dash["simulated_total"] == 1 and dash["simulated"][-1]["qty"] == "50" and dash["simulated"][-1]["name"] == "Rempart de Fer"


# ---------------------------------------------------------------------------------- population
def test_population_a_chosen_number_then_the_order_is_done_and_not_repeated(game_client, game):
    _produce(game_client, [line("population", "Rempart de Fer", amount=50)], mode="live")
    _cycle(game_client)
    acts = _launches(game)
    assert [(a["section"], a["planet"], a["name"], a["qty"]) for a in acts] == [("population", EGIDE, "Rempart de Fer", 50)]
    assert _has(game_client, "Rempart de Fer × 50 lancée(s) — 1/50 aujourd'hui.")
    item_id = game_client.get("/api/automation").json()["settings"]["production"]["queue"][0]["id"]
    assert game_client.get("/api/automation").json()["status"]["production_done"] == {item_id: 1}
    _force_due(game_client)
    _runner(game_client)._prodstate.clear()
    _cycle(game_client)
    assert len(_launches(game)) == 1  # commande unique : pas de seconde fois
    assert _has(game_client, "commande déjà faite sur cette planète")
    # « Relancer » la commande
    assert game_client.post("/api/automation/production/reset", json={"id": item_id}).json()["reset"] == 1
    _cycle(game_client)
    assert len(_launches(game)) == 2


def test_modifying_a_done_order_makes_it_new_again(game_client, game):
    _produce(game_client, [line("population", "Rempart de Fer", amount=5)], mode="live")
    _cycle(game_client)
    assert len(_launches(game)) == 1
    queue = game_client.get("/api/automation").json()["settings"]["production"]["queue"]
    queue[0]["amount"] = 6
    game_client.put("/api/automation/settings", json={"settings": {"production": {"queue": queue}}})
    _cycle(game_client)
    assert [a["qty"] for a in _launches(game)] == [5, 6]


def test_population_max_clicks_the_games_max_button(game_client, game):
    _produce(game_client, [line("population", "Rempart de Fer", maxi=True)], mode="live")
    _cycle(game_client)
    (act,) = _launches(game)
    # Egide : 3,2 G de Cristal / 5 233 par unité = le facteur limitant
    assert act["qty"] == int(3.201e9 // 5233) and act["name"] == "Rempart de Fer"
    assert _has(game_client, "Rempart de Fer (maximum)")


def test_a_tab_that_is_not_the_default_is_opened(game_client, game):
    _produce(game_client, [line("population", "Technicien", amount=10), line("population", "Mineur", amount=3)], mode="live")
    _cycle(game_client)
    assert [(a["name"], a["qty"]) for a in _launches(game)] == [("Technicien", 10), ("Mineur", 3)]


def test_stock_target_only_orders_the_difference_and_counts_the_queue(game_client, game):
    _produce(game_client, [line("population", "Rempart de Fer", mode="stock", amount=3200)], mode="live")  # stock actuel : 3 182
    _cycle(game_client)
    assert [a["qty"] for a in _launches(game)] == [18]
    _force_due(game_client)
    _runner(game_client)._prodstate.clear()
    _cycle(game_client)  # les 18 sont dans la file d'attente du jeu : rien de plus
    assert len(_launches(game)) == 1 and _has(game_client, "objectif de stock atteint (3200 sur 3200)")
    game.complete()
    _runner(game_client)._prodstate.clear()
    _cycle(game_client)
    assert len(_launches(game)) == 1


def test_not_enough_resources_means_wait_and_nothing_is_clicked(game_client, game):
    _produce(game_client, [line("specialized", "Grand Transporteur", amount=100000)], mode="live", planet=CONCORDE)
    _cycle(game_client)
    assert game.actions() == [] and _has(game_client, "en attente (ressources insuffisantes")
    rows = game_client.get("/api/automation").json()["status"]["production_state"]
    assert [(r["planet"], r["status"], r["block"]) for r in rows] == [(CONCORDE, "pending", "specialized")]
    assert game_client.get("/api/automation").json()["status"]["launched_today"] == 0


# --------------------------------------------------------------------- vaisseaux et composants
def test_ships_are_built_in_construction_mode_and_models_are_discovered(game_client, game):
    _produce(game_client, [line("ships", "Anode", amount=2)], mode="live")
    _cycle(game_client)
    (act,) = _launches(game)
    assert (act["section"], act["name"], act["qty"], act["mode"]) == ("ship", "Anode", 2, "construction")
    catalog = game_client.get("/api/automation").json()["catalog"]["production"]["ships"]
    assert [m["name"] for m in catalog] == ["Anode", "Plasmatique"]  # « Intercepteur » (catalogue) n'est pas un de mes modèles


def test_ship_models_in_stock_mode_use_the_en_service_count(game_client, game):
    _produce(game_client, [line("ships", "Anode", mode="stock", amount=50)], mode="live")  # 47 en service
    _cycle(game_client)
    assert [a["qty"] for a in _launches(game)] == [3]
    _force_due(game_client)
    _runner(game_client)._prodstate.clear()
    _cycle(game_client)  # commande récente : le bot attend avant de recompter
    assert len(_launches(game)) == 1 and _has(game_client, "commande récente")


def test_components_use_the_unlabeled_button_of_their_row(game_client, game):
    _produce(game_client, [line("components", "Chasseur", amount=1000)], mode="live")
    _cycle(game_client)
    (act,) = _launches(game)
    assert (act["section"], act["name"], act["qty"]) == ("component", "Chasseur", 1000)


def test_components_max(game_client, game):
    _produce(game_client, [line("components", "Destroyer", maxi=True)], mode="live", planet=EGIDE)
    _cycle(game_client)
    (act,) = _launches(game)
    assert act["name"] == "Destroyer" and act["qty"] == int(min(4.121e9 // 479710, 3.201e9 // 104664, 942.839e6 // 71520))


# ------------------------------------------------------------- appareils spécialisés et défenses
def test_specialized_devices_and_defenses_even_when_the_games_labels_are_shortened(game_client, game):
    _produce(game_client, [line("specialized", "Sonde Spatiale", amount=10), line("defense", "Faisceau de Verrouillage", amount=5)], mode="live")
    _cycle(game_client)
    assert [(a["section"], a["name"], a["qty"]) for a in _launches(game)] == [
        ("specialized", "Sonde Spatiale", 10), ("defense", "Faisceau de Verrouillage", 5)]


def test_repeat_relaunches_at_every_pass(game_client, game):
    _produce(game_client, [line("defense", "Sentinelle de Rempart", mode="repeat", amount=7)], mode="live")
    for _ in range(3):
        _runner(game_client)._prodstate.clear()
        _cycle(game_client)
    assert [a["qty"] for a in _launches(game)] == [7, 7, 7]
    assert game_client.get("/api/automation").json()["status"]["launched_today"] == 3


def test_repeat_max_on_every_pass(game_client, game):
    _produce(game_client, [line("specialized", "Drone Terrestre", mode="repeat", maxi=True)], mode="live")
    _cycle(game_client)
    first = _launches(game)[0]["qty"]
    assert first == int(min(4.121e9 // 436, 3.201e9 // 436))
    _runner(game_client)._prodstate.clear()
    _cycle(game_client)
    assert len(_launches(game)) == 1  # il reste moins d'une unité de la ressource limitante : rien à relancer
    assert _has(game_client, "ressources insuffisantes pour une seule unité")


# ------------------------------------------------------------------------------------ priorité
def test_the_player_chooses_who_spends_first(game_client, game):
    """Constructions d'abord (défaut) : la Mine de Fer part, puis la production prend ce qui reste. Production d'abord :
    le « maximum » vide le Cristal et la Mine de Fer devient impossible."""
    items = [line("population", "Rempart de Fer", maxi=True)]
    _configure(game_client, mode="live", constructions=[("Mine de Fer", 45)], scope="one", planet=EGIDE)
    _produce(game_client, items, mode="live")
    _cycle(game_client)
    assert [a["kind"] for a in game.actions()] == ["building", "production"]

    game.reset()
    game_client.portal.call(_bot(game_client)._page.reload)
    _runner(game_client)._pstate.clear()
    _runner(game_client)._prodstate.clear()
    _runner(game_client)._prod_done.clear()
    _produce(game_client, items, mode="live", priority=["population", "constructions", "research", "shipyard", "specialized", "defense"])
    _cycle(game_client)
    assert [a["kind"] for a in game.actions()] == ["production"]  # plus de Cristal pour la Mine de Fer


# ------------------------------------------------------------------------------------ sécurités
def test_production_counts_in_the_daily_cap(game_client, game):
    _produce(game_client, [line("defense", "Sentinelle de Rempart", mode="repeat", amount=1)], mode="live", cap=2)
    for _ in range(4):
        _runner(game_client)._prodstate.clear()
        _cycle(game_client)
    assert len(_launches(game)) == 2 and _has(game_client, "Maximum de lancements atteint (2 par jour)")


def test_two_unconfirmed_productions_stop_the_automation(game_client, game):
    from app.automation.runner import Interrupted

    game.reset(no_debit=True)
    game_client.portal.call(_bot(game_client)._page.reload)
    _produce(game_client, [line("population", "Rempart de Fer", amount=50), line("population", "Spectre du Rift", amount=50)], mode="live")
    runner = _runner(game_client)
    runner._running = True
    with pytest.raises(Interrupted):
        game_client.portal.call(runner.run_cycle)
    assert runner._running is False and _has(game_client, "lancement non confirmé (les ressources n'ont pas été débitées)")


def test_an_unexpected_dialog_after_a_production_click_stops_everything(game_client, game):
    from app.automation.runner import Interrupted

    game.reset(popup_after_click=True)
    game_client.portal.call(_bot(game_client)._page.reload)
    _produce(game_client, [line("population", "Rempart de Fer", amount=5)], mode="live")
    runner = _runner(game_client)
    runner._running = True
    with pytest.raises(Interrupted):
        game_client.portal.call(runner.run_cycle)
    assert runner._running is False and _has(game_client, "fenêtre inattendue")


def test_a_small_order_on_a_rounded_stock_is_launched_but_flagged_as_unverifiable(game_client, game):
    """Egide : le Cristal s'affiche « 3,201 G » (au million près) : 10 sondes (8 720 de Cristal) ne se voient pas à l'écran.
    Ici le stock de l'élément augmente dans le faux jeu, ce qui sert de confirmation."""
    _produce(game_client, [line("specialized", "Sonde Spatiale", amount=10)], mode="live")
    _cycle(game_client)
    assert len(_launches(game)) == 1 and not _has(game_client, "non confirmé")


def test_unreadable_stock_blocks_a_stock_target(game_client, game):
    from app.automation.production import ProdCard, decide_production
    from app.automation.state import Resources

    card = ProdCard("X", None, 0, {"Fer": 10.0}, True, True)
    decision = decide_production({"mode": "stock", "amount": 5, "max": False}, card, Resources(fer=1000.0), False)
    assert decision.action == "skip" and "stock actuel illisible" in decision.reason


def test_disabled_production_does_nothing(game_client, game):
    _produce(game_client, [line("population", "Rempart de Fer", amount=5)], mode="live", enabled=False)
    _cycle(game_client)
    assert game.actions() == [] and not _has(game_client, "Rempart")


# ------------------------------------------------------------------------- réglages et lecture
def test_production_settings_are_validated(game_client):
    def put(items, **extra):
        return game_client.put("/api/automation/settings", json={"settings": {"production": {"queue": items, **extra}}})

    assert put([line("population", "Pilote", amount=5)]).status_code == 200
    bad = [
        ([line("nulle part", "X")], "section"), ([line("population", "")], "nom"), ([line("population", "X", mode="souvent")], "mode"),
        ([line("population", "X", mode="stock", maxi=True)], "maximum"), ([line("population", "X", amount=0)], "quantité"),
        ([line("population", "X", amount=True)], "quantité"), ([line("population", "X"), line("population", "x")], "double"),
    ]
    for items, word in bad:
        res = put(items)
        assert res.status_code == 422 and word in " ".join(res.json()["detail"]), (items, res.text)
    assert put([line("population", "Pilote", amount=5)], scope="one").status_code == 422  # planète à choisir
    assert put([line("population", "Pilote", amount=5)], recheck_minutes=0).status_code == 422
    assert put([line("population", f"U{i}") for i in range(61)]).status_code == 422


def test_priority_is_a_complete_ordering_of_the_known_blocks(game_client):
    def put(order):
        return game_client.put("/api/automation/settings", json={"settings": {"priority": order}})

    ok = put(["defense", "population"])  # les blocs oubliés sont ajoutés à la fin
    assert ok.status_code == 200 and ok.json()["settings"]["priority"] == ["defense", "population", "constructions", "research", "shipyard", "specialized", "expeditions", "espionage"]
    assert put(["defense", "magie"]).status_code == 422
    assert put("population").status_code == 422


def test_ids_are_kept_and_the_catalog_lists_the_built_in_production_elements(game_client):
    items = [line("population", "Pilote", amount=5), line("defense", "Éclateur Ionique", mode="stock", amount=100)]
    first = _produce(game_client, items)["settings"]["production"]["queue"]
    again = game_client.put("/api/automation/settings", json={"settings": {"production": {"queue": first}}}).json()["settings"]["production"]["queue"]
    assert [q["id"] for q in again] == [q["id"] for q in first]
    catalog = game_client.get("/api/automation").json()["catalog"]
    assert [s["id"] for s in catalog["production_sections"]] == ["population", "ships", "components", "specialized", "defense"]
    pop = [e["name"] for e in catalog["production"]["population"]]
    assert "Soldat" not in pop and pop[:2] == ["Rempart de Fer", "Spectre du Rift"] and len(pop) == 10
    assert len(catalog["production"]["components"]) == 37 and len(catalog["production"]["specialized"]) == 7 and len(catalog["production"]["defense"]) == 8
    assert [b["id"] for b in catalog["blocks"]] == ["constructions", "research", "population", "shipyard", "specialized", "defense", "expeditions", "espionage"]


def test_read_state_shows_what_the_bot_understands_of_the_production_pages(game_client, game):
    data = game_client.post("/api/automation/read-state").json()
    prod = data["production"]
    assert set(prod) == {"population", "ships", "components", "specialized", "defense"}
    names = {c["name"]: c for c in prod["population"]["cards"]}
    assert {"Rempart de Fer", "Pilote", "Mineur"} <= set(names)  # les trois onglets sont lus
    assert names["Rempart de Fer"]["owned"] == 3182 and names["Rempart de Fer"]["costs"]["Cristal"] == pytest.approx(5233) and names["Rempart de Fer"]["can_produce"]
    assert [c["name"] for c in prod["ships"]["cards"]] == ["Anode", "Plasmatique"] and prod["ships"]["cards"][0]["owned"] == 47
    assert {c["name"] for c in prod["components"]["cards"]} == {"Chasseur", "Destroyer"} and prod["components"]["cards"][1]["owned"] == 91
    assert game_client.get("/api/automation").json()["settings"]["catalog"]["ships"] == ["Anode", "Plasmatique"]
    assert game.actions() == []  # lecture seule


# --------------------------------------------------------------------------------- décisions
def test_decisions_without_a_browser():
    from app.automation.production import ProdCard, decide_production
    from app.automation.state import Resources

    card = ProdCard("X", 10, 5, {"Fer": 100.0, "Cristal": 50.0}, True, True)
    res = Resources(fer=10_000.0, cristal=2_000.0)  # payable : min(100, 40) = 40
    item = lambda **kw: {"mode": "once", "amount": 1, "max": False, **kw}  # noqa: E731
    assert decide_production(item(amount=40), card, res, False).qty == 40
    assert decide_production(item(amount=41), card, res, False).action == "wait"
    assert decide_production(item(max=True), card, res, False).expected == 40 and decide_production(item(max=True), card, res, False).qty is None
    assert decide_production(item(mode="stock", amount=30), card, res, False).qty == 15  # 30 − 10 en stock − 5 en file
    assert decide_production(item(mode="stock", amount=15), card, res, False).action == "reached"
    assert decide_production(item(mode="stock", amount=100), card, res, False).action == "wait"  # 85 à payer, 40 possibles
    assert decide_production(item(), card, res, True).action == "reached"  # commande unique déjà faite
    assert decide_production(item(mode="repeat"), card, res, True).action == "launch"  # « done » ne concerne que « once »
    assert decide_production(item(), None, res, False).action == "skip"
    assert decide_production(item(), ProdCard("X", 1, 0, {}, True, True), res, False).reason == "coût illisible"
    assert decide_production(item(), ProdCard("X", 1, 0, {"Fer": 1.0}, True, False), res, False).action == "skip"
    assert decide_production(item(), card, Resources(fer=None, cristal=5.0), False).reason == "ressources de la planète illisibles"
    assert decide_production(item(max=True), card, Resources(fer=50.0, cristal=2000.0), False).action == "wait"  # pas même une unité
    assert decide_production(item(), ProdCard("X", 1, 0, {"Fer": 1.0}, False, True), res, False).action == "wait"  # bouton inactif


def test_counts_and_costs_are_read_the_way_the_game_writes_them():
    from app.automation.production import parse_costs, parse_count

    assert [parse_count(t) for t in ("2 507 383", "×4 694", "×91", "x21331", "12 393", "0", None, "beaucoup")] == [2507383, 4694, 91, 21331, 12393, 0, None, None]
    bad: list[str] = []
    costs = parse_costs({"F": "2,616 K", "C": "5,233 K", "E": "218", "FER": "410,152 K", "Z": "9"}, bad)
    assert costs == {"Fer": 410152.0, "Cristal": 5233.0, "Éther": 218.0} and bad == []
    assert parse_costs({"F": "beaucoup"}, bad) == {} and bad == ["F: beaucoup"]
