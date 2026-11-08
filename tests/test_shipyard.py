"""Chantier spatial : scan des modèles, Assemblages (construction avec des ressources) et Composants, planète par planète."""

from app import builder
from app.bot import State

CONCORDE = "8-50-Concorde"
EGIDE = "6-79-Egide"
STRATE = "9-169-Strate"
MODELS = ["Anode", "Plasmatique", "Transpo civil cuir"]


def _bot(client):
    return client.app.state.bot


def _scan(client):
    assert len(client.post("/api/planets/scan").json()["planets"]) == 3


def _set(client, route, queue):
    res = client.put(f"/api/{route}", json={"queue": queue})
    assert res.status_code == 200, res.text


def _line(item, mode, amount=0, planets=(), repeat=True):
    return {"item": item, "mode": mode, "amount": amount, "planets": list(planets), "repeat": repeat}


def _ships(client):
    return client.portal.call(_bot(client).shipper.run_pass)


def _parts(client):
    return client.portal.call(_bot(client).producer.run_pass)


def _made(game, section):
    return [(a["planet"], a["name"], a["qty"]) for a in game.actions() if a["kind"] == "production" and a["section"] == section]


def _queue(client, route):
    return client.get(f"/api/{route}").json()["settings"]["queue"]


# ------------------------------------------------------------------------------ scan des modèles
def test_nothing_is_scanned_before_the_first_models_scan(game_client):
    data = game_client.get("/api/models").json()
    assert data["models"] == [] and data["scanned_at"] is None and data["error"] == "" and data["known"] == []


def test_manual_scan_lists_the_models_with_their_type_and_count(game_client):
    data = game_client.post("/api/models/scan").json()
    assert [m["name"] for m in data["models"]] == MODELS
    assert [m["kind"] for m in data["models"]] == ["militaire", "militaire", "civil"]
    assert data["models"][0]["owned"] == 47
    assert data["source"] == "manuel" and data["scanned_at"] and data["error"] == "" and data["known"] == MODELS
    assert _bot(game_client).supervisor.prefs.get()["catalog"]["models"] == sorted(MODELS)


def test_the_models_scan_does_not_touch_the_game(game_client, game):
    game_client.post("/api/models/scan")
    assert game.actions() == []


def test_models_scan_is_refused_with_a_reason_outside_a_game_and_in_manual_control(game_client):
    _bot(game_client)._manual = True
    assert "contrôle manuel" in game_client.post("/api/models/scan").json()["error"].lower()
    _bot(game_client)._manual = False
    _bot(game_client)._set(State.IDLE, "test")
    assert "pas dans une partie" in game_client.post("/api/models/scan").json()["error"]


def test_the_bot_scans_the_models_by_itself_once_after_the_planets(game_client):
    runner = _bot(game_client).supervisor
    assert game_client.portal.call(runner.ensure_ready) is None
    data = runner.models_summary()
    assert [m["name"] for m in data["models"]] == MODELS and data["source"] == "démarrage"
    assert runner.scan_summary()["source"] == "démarrage"
    game_client.portal.call(runner.ensure_ready)
    assert runner.models_summary()["scanned_at"] == data["scanned_at"]  # pas de second scan pour la même connexion


def test_a_model_must_come_from_the_scan(game_client):
    game_client.post("/api/models/scan")
    _set(game_client, "ships", [_line("Anode", "fixed", 1)])
    res = game_client.put("/api/ships", json={"queue": [_line("Vaisseau fantôme", "fixed", 1)]})
    assert res.status_code == 422 and "scan des modèles" in " ".join(res.json()["detail"])


# ------------------------------------------------------------------------------ Assemblages
def test_ships_are_built_with_resources_even_when_the_card_shows_the_assembly_mode(game_client, game):
    _scan(game_client)
    _set(game_client, "ships", [_line("Anode", "fixed", 5, [CONCORDE])])  # le faux jeu démarre en mode Assemblages
    assert "5 × Anode sur 8-50-Concorde" in _ships(game_client)
    action = next(a for a in game.actions() if a["kind"] == "production")
    assert (action["planet"], action["name"], action["qty"], action["mode"]) == (CONCORDE, "Anode", 5, "construction")


def test_one_several_or_all_planets(game_client, game):
    _scan(game_client)
    _set(game_client, "ships", [_line("Anode", "fixed", 2, [EGIDE, STRATE])])
    _ships(game_client)
    assert _made(game, "ship") == [(EGIDE, "Anode", 2), (STRATE, "Anode", 2)]  # Concorde n'est pas dans la ligne
    _set(game_client, "ships", [_line("Transpo civil cuir", "fixed", 1)])  # toutes les planètes
    _ships(game_client)
    assert [p for p, n, _ in _made(game, "ship") if n == "Transpo civil cuir"] == [CONCORDE, EGIDE, STRATE]


def test_the_first_matching_line_of_a_model_wins_on_a_planet(game_client, game):
    _scan(game_client)
    _set(game_client, "ships", [_line("Anode", "fixed", 3, [EGIDE]), _line("Anode", "fixed", 7)])
    _ships(game_client)
    assert [(p, q) for p, _, q in _made(game, "ship")] == [(CONCORDE, 7), (EGIDE, 3), (STRATE, 7)]


def test_max_builds_everything_the_planet_can_pay(game_client, game):
    _scan(game_client)
    _set(game_client, "ships", [_line("Anode", "max", planets=[STRATE])])  # Strate : 7,3 M de Fer → 17 exemplaires
    _ships(game_client)
    assert _made(game, "ship") == [(STRATE, "Anode", 17)]


def test_a_target_builds_only_what_is_missing_then_the_line_leaves_the_list(game_client, game):
    _scan(game_client)
    _set(game_client, "ships", [_line("Anode", "target", 60, [CONCORDE])])  # 47 au départ
    _ships(game_client)
    assert _made(game, "ship") == [(CONCORDE, "Anode", 13)]
    assert _queue(game_client, "ships") == []  # effectif entièrement lancé : la ligne disparaît
    assert "rien à faire" in _ships(game_client)


def test_an_unfinished_target_counts_what_was_already_ordered(game_client, game):
    _scan(game_client)
    game.reset(slow_build=True)  # l'effectif n'augmente qu'à la fin de la fabrication
    game.set_stocks(0, Fer=10 * 410152, Cristal=22.36e6, Éther=19.8e6)  # de quoi payer 10 exemplaires seulement
    _set(game_client, "ships", [_line("Anode", "target", 100, [CONCORDE])])
    _ships(game_client)
    game.set_stocks(0, Fer=58.0e6)
    _bot(game_client).shipper._cooldown.clear()
    _ships(game_client)
    assert [q for _, _, q in _made(game, "ship")] == [10, 43]  # 100 − 47 possédés − 10 déjà commandés, pas 53


def test_a_once_line_builds_one_time_per_planet_then_leaves_the_list(game_client, game):
    _scan(game_client)
    _set(game_client, "ships", [_line("Anode", "fixed", 2, repeat=False), _line("Plasmatique", "fixed", 1, [EGIDE], repeat=True)])
    _ships(game_client)
    assert [(p, n) for p, n, _ in _made(game, "ship") if n == "Anode"] == [(CONCORDE, "Anode"), (EGIDE, "Anode"), (STRATE, "Anode")]
    assert [ln["item"] for ln in _queue(game_client, "ships")] == ["Plasmatique"]  # « une seule fois » faite partout : retirée


def test_the_feature_turns_itself_off_when_its_last_line_is_done(game_client, game):
    _scan(game_client)
    assert game_client.put("/api/features/ships", json={"enabled": True, "min_minutes": 1, "max_minutes": 2}).status_code == 200
    _set(game_client, "ships", [_line("Anode", "fixed", 2, repeat=False)])
    assert "fonctionnalité désactivée" in _ships(game_client)
    assert _queue(game_client, "ships") == []
    assert _bot(game_client).scheduler.store.get("ships")["enabled"] is False


def test_the_feature_stays_on_while_other_lines_remain(game_client, game):
    _scan(game_client)
    assert game_client.put("/api/features/ships", json={"enabled": True, "min_minutes": 1, "max_minutes": 2}).status_code == 200
    _set(game_client, "ships", [_line("Anode", "fixed", 2, repeat=False), _line("Plasmatique", "fixed", 1, [EGIDE], repeat=True)])
    _ships(game_client)
    assert _bot(game_client).scheduler.store.get("ships")["enabled"] is True


def test_a_loop_line_builds_again_at_the_next_pass(game_client, game):
    _scan(game_client)
    _set(game_client, "ships", [_line("Anode", "fixed", 1, [CONCORDE])])
    _ships(game_client)
    _bot(game_client).shipper._cooldown.clear()
    _ships(game_client)
    assert [q for _, _, q in _made(game, "ship")] == [1, 1] and len(_queue(game_client, "ships")) == 1


def test_nothing_is_built_when_not_even_one_unit_is_payable(game_client, game):
    _scan(game_client)
    game.set_stocks(0, Fer=0, Cristal=0)
    _set(game_client, "ships", [_line("Plasmatique", "max", planets=[CONCORDE])])
    assert "0 lancement(s)" in _ships(game_client)
    assert game.actions() == []


def test_the_quantity_field_is_left_empty_when_nothing_is_launched(game_client, game):
    """Une quantité laissée dans le champ fait afficher des coûts totaux : le passage suivant les lirait comme unitaires."""
    _scan(game_client)
    game.set_stocks(0, Fer=0, Cristal=0)
    _set(game_client, "ships", [_line("Plasmatique", "max", planets=[CONCORDE])])
    _ships(game_client)
    page = _bot(game_client)._page
    values = game_client.portal.call(page.evaluate, "() => [...document.querySelectorAll('input[type=number]')].map((i) => i.value)")
    assert values and all(v == "" for v in values)


def test_ships_stop_without_any_click_in_manual_control(game_client, game):
    _scan(game_client)
    _set(game_client, "ships", [_line("Anode", "fixed", 1)])
    _bot(game_client)._manual = True
    assert "interrompu" in _ships(game_client)
    assert game.actions() == []


def test_two_unconfirmed_launches_in_a_row_disable_the_ships_feature(game_client, game, monkeypatch):
    monkeypatch.setattr(builder, "CONFIRM_SECONDS", 0.6)
    game.reset(no_debit=True)  # le clic passe mais rien n'est débité : le bot ne peut pas confirmer
    _scan(game_client)
    _set(game_client, "ships", [_line("Anode", "fixed", 100, [CONCORDE])])
    game_client.put("/api/features/ships", json={"enabled": True, "min_minutes": 1, "max_minutes": 2})
    _ships(game_client)
    _bot(game_client).shipper._cooldown.clear()
    assert "désactivé" in _ships(game_client)
    assert _bot(game_client).scheduler.store.get("ships")["enabled"] is False


# ------------------------------------------------------------------------------ Composants
def test_components_are_produced_from_the_components_tab_even_after_an_assemblies_pass(game_client, game):
    _scan(game_client)
    _set(game_client, "ships", [_line("Anode", "fixed", 1, [CONCORDE])])
    _ships(game_client)
    _set(game_client, "components", [_line("canon-laser", "fixed", 10, [CONCORDE])])
    assert "10 × Canon laser sur 8-50-Concorde" in _parts(game_client)
    assert _made(game, "component") == [(CONCORDE, "Canon laser", 10)]


def test_component_modes_planets_and_priority(game_client, game):
    _scan(game_client)
    _set(
        game_client,
        "components",
        [
            _line("destroyer", "target", 100, [CONCORDE, EGIDE]),  # 50 au départ sur chaque planète
            _line("chasseur", "fixed", 3, [STRATE]),
            _line("reacteur-antimatiere", "max", planets=[STRATE]),
        ],
    )
    _parts(game_client)
    made = _made(game, "component")
    assert (CONCORDE, "Destroyer", 50) in made and (EGIDE, "Destroyer", 50) in made and (STRATE, "Chasseur", 3) in made
    assert [n for p, n, _ in made if p == STRATE] == ["Chasseur", "Réacteur antimatière"]  # ordre de la liste
    assert [ln["item"] for ln in _queue(game_client, "components")] == ["chasseur", "reacteur-antimatiere"]  # la cible atteinte est retirée


def test_a_component_line_for_one_planet_leaves_the_others_alone(game_client, game):
    _scan(game_client)
    _set(game_client, "components", [_line("coque-des-monarques", "fixed", 4, [EGIDE])])
    _parts(game_client)
    assert _made(game, "component") == [(EGIDE, "Coque des Monarques", 4)]
