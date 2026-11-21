"""Lectures faites toutes seules après la connexion : effectifs (population, bâtiments) et modèles d'exploration
(Piliers et Flotte), en plus des planètes, des modèles de vaisseaux et des Piliers."""


def _bot(client):
    return client.app.state.bot


def test_population_and_exploration_models_are_read_at_startup_once_per_connection(game_client, game):
    bot = _bot(game_client)
    runner = bot.supervisor
    assert bot.inventory.get()["read_at"] is None and not any(bot.explorations.known_models().values())
    assert game_client.portal.call(runner.ensure_ready) is None
    inventory = bot.inventory.get()
    assert inventory["source"] == "démarrage" and inventory["error"] == "" and len(inventory["planets"]) == 3
    assert any(p["units"] for p in inventory["planets"])  # les effectifs de population sont là
    daily = bot.explorations.daily()
    assert daily["source"] == "démarrage" and any(daily["models"].values())
    assert bot.explorations.daily_due() and daily["breaches"] == {}  # le relevé du jour (brèches) reste à faire
    events = [e["msg"] for e in bot.events(30)]
    assert any("Inventaire relevé (démarrage)" in m for m in events)
    assert any("Exploration : modèles relevés (démarrage)" in m for m in events)

    read_at = inventory["read_at"]
    game_client.portal.call(runner.ensure_ready)
    assert bot.inventory.get()["read_at"] == read_at  # pas de second relevé pour la même connexion
    runner._inventory_done_session, runner._inventory_attempts = False, 0  # nouvelle connexion
    runner._explo_models_done_session, runner._explo_models_attempts = False, 0
    game_client.portal.call(runner.ensure_ready)
    assert bot.inventory.get()["read_at"] > read_at
    assert [a for a in game.actions() if a["kind"] != "view"] == []  # lecture seule : aucune action sur le jeu


def test_a_failed_startup_read_is_retried_a_limited_number_of_times(game_client, game):
    bot = _bot(game_client)
    runner = bot.supervisor

    async def broken(source: str) -> dict:
        return {**bot.inventory.get(), "error": "page absente"}

    bot.inventory_reader.refresh = broken
    for _ in range(6):
        game_client.portal.call(runner.ensure_ready)
    assert not runner._inventory_done_session and runner._inventory_attempts == 3  # pas d'acharnement


def test_an_incomplete_models_read_is_not_announced_as_a_success(game_client, game):
    from app.planets import GameUiError

    bot = _bot(game_client)
    real = bot.explorer._read_models

    async def flaky(method: str) -> list[dict]:
        if method == "FLEET":
            raise GameUiError("la fenêtre des modèles ne s'ouvre pas")
        return await real(method)

    bot.explorer._read_models = flaky
    game_client.portal.call(bot.supervisor.ensure_ready)
    events = bot.events(30)
    assert not any("Exploration : modèles relevés" in e["msg"] for e in events)  # pas de faux succès
    incomplete = [e for e in events if "relevé des modèles incomplet" in e["msg"]]
    assert incomplete and incomplete[0]["level"] == "warn" and "Anciens modèles gardés" in incomplete[0]["msg"]
