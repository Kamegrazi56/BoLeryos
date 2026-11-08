"""Les 4 pages (Accueil, Écran, Réglages, Suivi) dans un vrai navigateur."""

import threading
import time

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from app.main import create_app
from tests.conftest import _free_port

PAGES = ["/", "/ecran", "/reglages", "/suivi"]


@pytest.fixture
def ui(settings, game):
    """Application réelle servie en HTTP, avec un bot « connecté » sur le faux jeu, et un navigateur."""
    from dataclasses import replace

    from app.bot import State

    app = create_app(replace(settings, login_url=game.url + "/game/asterion/buildings"))
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    bot = app.state.bot
    base = f"http://127.0.0.1:{port}"
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=settings.chromium_path, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.base = base
        errors = []
        dialogs = []

        def on_dialog(d):
            dialogs.append(d.message)
            d.accept() if page.accept_dialogs else d.dismiss()

        page.on("dialog", on_dialog)
        page.dialogs = dialogs
        page.accept_dialogs = True
        # Comme un utilisateur : l'ouverture de la page Écran démarre le navigateur du bot (dans la boucle du serveur).
        page.goto(base + "/ecran")
        for _ in range(300):
            if bot._page is not None and "/game/" in bot._page.url:
                break
            time.sleep(0.1)
        bot._set(State.LOGGED_IN, "test")
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/reglages")
        page.wait_for_selector("#acct-id")
        yield page, errors, app
        browser.close()
    server.should_exit = True
    thread.join(timeout=5)


def _saved(app):
    return app.state.bot.supervisor.prefs.get()


def _wait_saved(page):
    page.wait_for_function("document.querySelector('#save-msg').textContent.includes('Enregistré')", timeout=5000)


def _edit(page, action):
    """Fait une saisie puis attend que le nouvel enregistrement soit terminé."""
    action()
    page.wait_for_function("document.querySelector('#save-msg').textContent.includes('en cours') || document.querySelector('#save-msg').textContent.includes('Enregistr')", timeout=3000)
    _wait_saved(page)


# ------------------------------------------------------------------------------ navigation
def test_every_page_has_the_same_four_entries_and_the_general_bot_switch(ui):
    page, errors, _ = ui
    for path in PAGES:
        page.goto(page.base + path)
        assert [t.strip().lower() for t in page.locator("header.top nav a").all_inner_texts()] == ["accueil", "écran", "réglages", "suivi"]
        assert page.locator("header.top nav a.active").count() == 1
        assert page.locator("header.top #power").text_content() == "Bot : OFF"
        assert page.locator("[data-power], #mode-chip, #mode-banner").count() == 0
        if path == "/ecran":
            assert page.evaluate("document.documentElement.scrollHeight <= innerHeight")  # aucune barre de défilement
    assert errors == []


def test_old_addresses_still_work(ui):
    page, _, _ = ui
    page.goto(page.base + "/vision")
    assert page.url.endswith("/ecran")
    page.goto(page.base + "/actions")
    assert "/reglages" in page.url
    page.goto(page.base + "/fonctionnalites")
    assert page.url.endswith("/reglages#features")
    assert page.locator("#panel-features").is_visible()


def test_the_removed_features_have_no_settings_and_no_routes(ui):
    page, _, _ = ui
    page.goto(page.base + "/reglages")
    tabs = [t.strip().lower() for t in page.locator("[role=tab]").all_inner_texts()]
    assert tabs == ["compte et univers", "fonctionnalités", "planning", "constructions", "recherches", "population", "assemblages", "composants", "exploration", "fouilles", "piratage", "forages", "espionnage", "jetons d'alliance"]
    for tab in tabs:  # aucune section supprimée ne réapparaît, quel que soit l'onglet
        page.click(f'[role=tab]:has-text("{tab}")', strict=False)
        text = page.inner_text("main").lower()
        for word in ("expéditions", "attaques", "horaires", "alertes"):
            assert word not in text
    for route in ("/api/automation", "/api/dashboard", "/api/notifications"):
        assert page.request.get(page.base + route).status == 404


# ------------------------------------------------------------------------------- réglages
def test_account_keeps_credentials_in_memory_and_never_shows_them_again(ui):
    page, _, app = ui
    page.fill("#acct-id", "titulaire.secret@example.com")
    page.fill("#acct-pw", "mot-de-passe-tres-secret")
    page.click("#acct-save")
    page.wait_for_function("document.querySelector('#acct-status').textContent.includes('en mémoire (ti***)')", timeout=5000)
    assert page.input_value("#acct-pw") == ""  # le mot de passe ne reste pas dans la page
    assert app.state.bot.has_credentials()
    assert "titulaire.secret" not in page.content()
    page.click("#acct-forget")
    page.wait_for_function("document.querySelector('#acct-status').textContent.includes('Aucun identifiant')", timeout=5000)
    assert not app.state.bot.has_credentials()
    page.click("#acct-login")
    page.wait_for_function("document.querySelector('#acct-msg').textContent.includes('Aucun identifiant')", timeout=5000)


def test_universe_is_typed_with_suggestions_and_saved(ui):
    page, _, app = ui
    app.state.bot.supervisor.prefs.add_catalog("universes", ["Asterion", "Kael"])
    page.reload()
    page.wait_for_function("document.querySelectorAll('#universe-list option').length == 2")
    assert page.locator("#universe-list option").evaluate_all("o => o.map(x => x.value)") == ["Asterion", "Kael"]
    _edit(page, lambda: (page.fill("#universe", "Kael"), page.press("#universe", "Tab")))
    assert _saved(app)["universe"] == "Kael"


def test_login_delay_is_saved_and_a_bad_range_is_refused(ui):
    page, _, app = ui
    _edit(page, lambda: (page.fill("#login-delay-min", "2"), page.fill("#login-delay-max", "3")))
    assert _saved(app)["login_delay"] == {"min_seconds": 2.0, "max_seconds": 3.0}
    page.fill("#login-delay-min", "9")
    page.wait_for_function("document.querySelector('#save-msg').className.includes('error')", timeout=5000)
    assert _saved(app)["login_delay"]["min_seconds"] == 2.0


def test_entering_the_account_makes_the_bot_connect_by_itself(settings, game):
    """Comme TBot : identifiants + univers saisis dans Réglages, et le bot se connecte tout seul."""
    from dataclasses import replace as _replace

    game.reset(require_auth=True)
    app = create_app(_replace(settings, login_url=game.url + "/", login_timeout=15.0))
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=settings.chromium_path, args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/reglages#account")
            page.wait_for_selector("#acct-id")
            page.fill("#acct-id", "joueur.test@example.com")
            page.fill("#acct-pw", "good-password")
            page.fill("#universe", "Asterion")
            page.click("#acct-save")
            page.wait_for_function("document.querySelector('#conn-status').textContent.includes('dans la partie')", timeout=90000)
            assert "Connecté" in page.inner_text("#conn-status")
            assert "/game/" in app.state.bot._page.url and game.info()["logged_in"] is True
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_screen_page_fills_the_page_and_keeps_tools_in_a_drawer(ui):
    page, errors, _ = ui
    page.goto(page.base + "/ecran")
    assert page.locator("#screen").is_visible() and page.locator("header.top #manual").is_visible()
    header = page.locator("header.top").bounding_box()
    screen = page.locator("#screen").bounding_box()
    viewport = page.viewport_size
    assert abs(screen["y"] - (header["y"] + header["height"])) <= 2  # juste sous le bandeau
    assert screen["width"] >= viewport["width"] - 2 and abs(screen["y"] + screen["height"] - viewport["height"]) <= 2
    assert not page.locator("#log").is_visible()  # journal escamoté
    page.click("#tools-toggle")
    page.wait_for_selector("#drawer.open")
    assert page.locator("#log").is_visible() and page.locator("#rec").count() == 1
    page.click("#drawer-close")
    page.wait_for_selector("#drawer:not(.open)", state="attached")
    assert errors == []


# ---------------------------------------------------------------------------------- accueil, suivi
def test_home_shows_connection_scanned_planets_and_events(ui):
    page, errors, _ = ui
    page.click("#scan-run")
    page.wait_for_function("document.querySelectorAll('#scan-list tr').length === 4", timeout=10000)
    page.goto(page.base + "/")
    page.wait_for_function("document.querySelectorAll('#planets tr').length === 4", timeout=10000)
    assert "8-50-Concorde" in page.text_content("#planets")
    assert "Scan des planètes" in page.text_content("#events")
    assert "Aucun identifiant" in page.text_content("#sentence")
    assert errors == []


def test_follow_up_page_is_only_the_journal(ui):
    page, _, _ = ui
    page.click("#scan-run")
    page.wait_for_function("document.querySelectorAll('#scan-list tr').length === 4", timeout=10000)
    page.goto(page.base + "/suivi")
    assert page.locator("[role=tab]").count() == 0 and page.locator("#read-state").count() == 0
    page.wait_for_function("document.querySelector('#log').textContent.includes('Scan des planètes')", timeout=10000)
    page.check("#log-details")
    page.select_option("#log-level", "error")
    assert "Journal vide" in page.inner_text("#log") or page.locator("#log div.error").count() == page.locator("#log div").count()


def test_account_has_a_planet_scan_section_with_a_button_and_the_scanned_list(ui):
    page, _, _app = ui
    assert "Scan des planètes" in page.text_content("#scan-title")
    assert "Aucun scan" in page.text_content("#scan-status")
    page.click("#scan-run")
    page.wait_for_function("document.querySelectorAll('#scan-list tr').length === 4", timeout=10000)  # en-tête + 3 planètes
    listed = page.text_content("#scan-list")
    assert "8-50-Concorde" in listed and "Z25 95 16" in listed
    assert "3 planète(s)" in page.text_content("#scan-status") and "à la demande" in page.text_content("#scan-status")


def test_features_page_sets_the_timer_enables_reorders_and_the_switch_starts_the_bot(ui):
    page, errors, app = ui
    page.goto(page.base + "/reglages#features")
    page.evaluate("localStorage.setItem('boleryos.showTest', '1')")  # « Test » est masquée par défaut
    page.reload()
    page.wait_for_selector("[data-feature=test]")
    card = page.locator("[data-feature=test]")
    assert "Désactivée" in card.inner_text()
    card.get_by_label("Activer Test (ne fait rien)").check()  # sans minuteur : refusé, la case revient
    page.wait_for_function("document.querySelector('#feat-msg').className.includes('error')", timeout=5000)
    assert "minuteur" in page.inner_text("#feat-msg").lower()
    page.wait_for_function("!document.querySelector('[data-feature=test] input[type=checkbox]').checked", timeout=5000)
    page.fill("[data-feature=test] input[aria-label^='Minuteur minimum']", "0.5")
    page.fill("[data-feature=test] input[aria-label^='Minuteur maximum']", "1")
    page.wait_for_function("document.querySelector('#feat-msg').textContent.includes('Enregistré')", timeout=5000)
    page.locator("[data-feature=test]").get_by_label("Activer Test (ne fait rien)").check()
    page.wait_for_function("document.querySelector('[data-feature=test]').classList.contains('enabled')", timeout=5000)
    saved = app.state.bot.scheduler.store.get("test")
    assert (saved["enabled"], saved["min_minutes"], saved["max_minutes"]) == (True, 0.5, 1.0)
    assert page.locator("[data-feature=test] button[aria-label^='Descendre']").is_disabled()  # dernière de la liste
    page.click("#power")
    page.wait_for_function("document.querySelector('#power').textContent === 'Bot : ON'", timeout=5000)
    assert app.state.bot.scheduler.on is True
    page.click("#power")
    page.wait_for_function("document.querySelector('#power').textContent === 'Bot : OFF'", timeout=5000)
    assert errors == []


def test_constructions_panel_builds_an_ordered_list_of_targets_for_all_or_one_planet(ui):
    page, errors, app = ui
    page.click("#scan-run")
    page.wait_for_function("document.querySelectorAll('#scan-list tr').length === 4", timeout=10000)
    page.goto(page.base + "/reglages#constructions")
    page.wait_for_function("document.querySelectorAll('#cons-add-building option').length === 16", timeout=5000)
    assert page.locator("#cons-add-building optgroup").evaluate_all("g => g.map(x => x.label)") == ["Industrie", "Infrastructures"]
    page.select_option("#cons-add-building", "mine-de-fer")
    page.fill("#cons-add-level", "45")
    page.click("#cons-add")
    page.select_option("#cons-add-building", "caserne")
    page.fill("#cons-add-level", "25")
    page.click("#cons-add")
    page.wait_for_function("document.querySelector('#cons-msg').textContent.includes('Enregistré')", timeout=5000)
    assert page.locator("#cons-queue li[data-building]").evaluate_all("l => l.map(x => x.dataset.building)") == ["mine-de-fer", "caserne"]
    assert page.locator("#cons-add-building option").count() == 14  # déjà dans la liste : plus proposés
    page.click("button[aria-label='Monter : Caserne']")
    page.wait_for_function("document.querySelector('#cons-msg').textContent.includes('Enregistré')", timeout=5000)
    assert [line["building"] for line in app.state.bot.constructions.get()["queue"]] == ["caserne", "mine-de-fer"]
    page.check("input[name=cons-scope][value=one]")
    page.wait_for_function("document.querySelector('#cons-msg').className.includes('error')", timeout=5000)  # pas de planète choisie
    page.wait_for_function("document.querySelectorAll('#cons-planet option').length > 1", timeout=10000)
    page.select_option("#cons-planet", "8-50-Concorde")
    page.wait_for_function("document.querySelector('#cons-msg').textContent.includes('Enregistré')", timeout=5000)
    assert app.state.bot.constructions.get()["planet"] == "8-50-Concorde"
    page.click("button[aria-label='Supprimer : Caserne']")
    page.wait_for_function("document.querySelectorAll('#cons-queue li[data-building]').length === 1", timeout=5000)
    assert errors == []


def test_researches_panel_builds_an_ordered_list_with_one_research_planet(ui):
    page, errors, app = ui
    page.click("#scan-run")
    page.wait_for_function("document.querySelectorAll('#scan-list tr').length === 4", timeout=10000)
    page.goto(page.base + "/reglages#researches")
    page.wait_for_function("document.querySelectorAll('#res-add-research option').length === 23", timeout=5000)
    assert page.locator("#res-add-research optgroup").evaluate_all("g => g.map(x => x.label)") == ["Civil", "Militaire"]
    page.wait_for_function("document.querySelectorAll('#res-planet option').length > 1", timeout=10000)
    page.select_option("#res-add-research", "technologie-laser")
    page.fill("#res-add-level", "30")
    page.click("#res-add")
    page.wait_for_function("document.querySelector('#res-msg').className.includes('error')", timeout=5000)  # pas de planète choisie
    page.select_option("#res-planet", "6-79-Egide")
    page.select_option("#res-add-research", "technologie-energie")
    page.fill("#res-add-level", "25")
    page.click("#res-add")
    page.wait_for_function("document.querySelector('#res-msg').textContent.includes('Enregistré')", timeout=5000)
    assert page.locator("#res-queue li[data-research]").evaluate_all("l => l.map(x => x.dataset.research)") == ["technologie-laser", "technologie-energie"]
    assert page.locator("#res-add-research option").count() == 21
    page.click("button[aria-label='Monter : Technologie Énergie']")
    page.wait_for_function("document.querySelector('#res-msg').textContent.includes('Enregistré')", timeout=5000)
    saved = app.state.bot.researches.get()
    assert saved["planet"] == "6-79-Egide" and [line["research"] for line in saved["queue"]] == ["technologie-energie", "technologie-laser"]
    assert errors == []


def test_population_panel_sets_mode_quantity_and_planet_for_each_population(ui):
    page, errors, app = ui
    page.click("#scan-run")
    page.wait_for_function("document.querySelectorAll('#scan-list tr').length === 4", timeout=10000)
    page.goto(page.base + "/reglages#populations")
    page.wait_for_function("document.querySelectorAll('#pop-add-population option').length === 10", timeout=5000)
    page.wait_for_function("document.querySelectorAll('#pop-add-planet option').length > 1", timeout=10000)
    assert page.locator("#pop-add-population optgroup").evaluate_all("g => g.map(x => x.label)") == ["Assaut", "Logistique", "Administration"]
    page.select_option("#pop-add-population", "pilote")
    page.select_option("#pop-add-mode", "target:0")
    page.fill("#pop-add-amount", "100")
    page.select_option("#pop-add-planet", "6-79-Egide")
    page.click("#pop-add")
    page.select_option("#pop-add-population", "mineur")
    page.select_option("#pop-add-mode", "max:0")  # MAX une seule fois
    page.select_option("#pop-add-planet", "")  # toutes les planètes
    assert page.locator("#pop-add-amount-label").is_hidden()  # MAX : pas de quantité
    page.click("#pop-add")
    page.wait_for_function("document.querySelector('#pop-msg').textContent.includes('Enregistré')", timeout=5000)
    assert app.state.bot.populations.get()["queue"] == [
        {"population": "pilote", "mode": "target", "amount": 100, "planet": "6-79-Egide", "repeat": False},
        {"population": "mineur", "mode": "max", "amount": 0, "planet": "", "repeat": False},
    ]
    page.select_option("select[aria-label='Mode de Mineur']", "fixed:0")
    page.fill("input[aria-label='Quantité de Mineur']", "25")
    page.click("button[aria-label='Monter : Mineur']")
    page.wait_for_function("document.querySelector('#pop-msg').textContent.includes('Enregistré')", timeout=5000)
    assert [(ln["population"], ln["mode"], ln["amount"], ln["repeat"]) for ln in app.state.bot.populations.get()["queue"]] == [("mineur", "fixed", 25, False), ("pilote", "target", 100, False)]
    assert errors == []


def test_settings_page_has_fourteen_tabs_and_only_one_panel_shows_at_a_time(ui):
    page, errors, _ = ui
    page.goto(page.base + "/reglages")
    # ordre du menu latéral : Bot, Économie, Opérations, Alliance
    ids = ["account", "features", "planning", "constructions", "researches", "populations", "ships", "components", "explorations", "excavations", "hackings", "minings", "spying", "alliance"]
    assert page.locator("[role=tab]").count() == 14
    for tab in ids:
        page.click(f"#tab-{tab}")
        assert page.get_attribute(f"#tab-{tab}", "aria-selected") == "true"
        assert [page.locator(f"#panel-{p}").is_visible() for p in ids] == [p == tab for p in ids]
        assert page.url.endswith(f"#{tab}")
    page.goto(page.base + "/reglages#constructions")
    assert page.locator("#panel-constructions").is_visible()  # un lien direct ouvre le bon onglet
    for current, following in zip(ids[3:], ids[4:] + ids[:1]):  # flèche du bas : entrée suivante, puis retour au début
        page.press(f"#tab-{current}", "ArrowDown")
        assert page.locator(f"#panel-{following}").is_visible(), following
    page.press("#tab-account", "ArrowUp")
    assert page.locator("#panel-alliance").is_visible()
    assert errors == []


def test_each_feature_page_has_its_switch_and_the_menu_shows_its_state(ui):
    page, errors, app = ui
    page.goto(page.base + "/reglages#alliance")
    head = page.locator("[data-feature-head=alliance_tokens]")
    head.get_by_label("Minuteur minimum (minutes) de Dons de jetons d'alliance (page)").fill("120")
    head.get_by_label("Minuteur maximum (minutes) de Dons de jetons d'alliance (page)").fill("240")
    page.wait_for_function("document.querySelector('#feat-msg').textContent.includes('Enregistré')", timeout=5000)
    head.get_by_label("Activer Dons de jetons d'alliance (page)").check()
    page.wait_for_selector("[data-dot=alliance].on", timeout=5000)  # pastille verte dans le menu
    saved = app.state.bot.scheduler.store.get("alliance_tokens")
    assert (saved["enabled"], saved["min_minutes"], saved["max_minutes"]) == (True, 120.0, 240.0)
    page.click("#tab-features")
    assert page.locator("[data-feature=alliance_tokens] input[type=checkbox]").is_checked()  # même réglage dans la liste
    assert page.locator("[data-feature=test]").count() == 0  # « Test » est masquée
    assert errors == []


def test_excavation_panel_saves_a_line_with_its_team_and_the_delay(ui):
    page, errors, app = ui
    page.click("#scan-run")  # scan des planètes (page Compte et univers), comme les autres onglets
    page.wait_for_function("document.querySelectorAll('#scan-list tr').length === 4", timeout=10000)
    page.goto(page.base + "/reglages#excavations")
    page.reload()  # même page (seul l'onglet change) : on la recharge pour relire les planètes scannées
    page.wait_for_selector("#exc-add")
    planets = page.evaluate("fetch('/api/excavations').then((r) => r.json()).then((d) => d.planets)")
    page.wait_for_function("document.getElementById('exc-daily').textContent !== \"Aucun relevé pour l'instant.\"")  # données chargées
    page.click("#exc-add")
    page.select_option("#exc-lines select[aria-label='Planète de départ, ligne 1']", planets[0])
    page.select_option("#exc-lines select[aria-label='Durée, ligne 1']", "12")
    page.fill("#exc-lines input[aria-label='Places, ligne 1']", "2")
    page.fill("#exc-lines input[aria-label='Scientifique, ligne 1']", "3000")
    page.fill("#exc-lines input[aria-label='Mineur, ligne 1']", "2000")
    page.check("#exc-lines input[aria-label='Mira Venn, ligne 1']")
    page.click("#exc-save")  # délai pas encore choisi : refusé avec un message en français
    page.wait_for_function("document.getElementById('exc-msg').classList.contains('error')")
    assert "Délai" in page.text_content("#exc-msg")
    page.fill("#exc-delay-min", "5")
    page.fill("#exc-delay-max", "15")
    page.click("#exc-save")
    page.wait_for_function("document.getElementById('exc-msg').textContent === 'Enregistré.'")
    assert app.state.bot.excavations.get() == {"lines": [{"planet": planets[0], "units": {"Scientifique": 3000, "Mineur": 2000},
                                                          "duration": 12, "slots": 2, "mira": True}], "delay_min": 5, "delay_max": 15}
    assert "Équipe : 5000 / 5000" in page.text_content("#exc-lines")
    assert errors == []


def test_planning_panel_saves_slots_and_the_random_offset(ui):
    page, errors, app = ui
    page.goto(page.base + "/reglages#planning")
    page.wait_for_selector("#plan-days button[aria-label='Ajouter une plage le lundi']")
    page.check("#plan-enabled")
    page.click("#plan-days button[aria-label='Ajouter une plage le lundi']")
    page.fill("#plan-days input[aria-label='Lundi, plage 1, début']", "07:30")
    page.fill("#plan-days input[aria-label='Lundi, plage 1, fin']", "11:45")
    page.click("#plan-days button[aria-label='Ajouter une plage le mardi']")
    page.fill("#plan-offset-min", "2")
    page.fill("#plan-offset-max", "9")
    page.click("#plan-save")
    page.wait_for_function("document.getElementById('plan-msg').textContent === 'Enregistré.'")
    saved = app.state.bot.planning.get()
    assert saved["enabled"] is True and saved["offset_min"] == 2 and saved["offset_max"] == 9
    assert saved["days"]["mon"] == [{"start": "07:30", "end": "11:45"}] and saved["days"]["tue"] == [{"start": "08:00", "end": "12:00"}]
    assert page.text_content("#plan-status").startswith(("Dans une plage", "Hors plage"))
    assert errors == []


def test_spying_panel_saves_the_minimums_and_refuses_a_launch_without_planet(ui):
    page, errors, app = ui
    page.goto(page.base + "/reglages#spying")
    page.wait_for_selector("#spy-minimums input[aria-label='Minimum Fer']")
    page.fill("#spy-minimums input[aria-label='Minimum Fer']", "1000000")
    page.fill("#spy-minimums input[aria-label='Minimum Éther']", "250000")
    page.select_option("#spy-required", "2")
    page.click("#spy-save")
    page.wait_for_function("document.getElementById('spy-msg').textContent === 'Enregistré.'")
    assert app.state.bot.spying.get() == {"planet": "", "minimums": {"Fer": 1000000, "Cristal": 0, "Éther": 250000}, "required": 2}
    page.click("#spy-start")  # aucune planète choisie : refusé avec un message en français
    page.wait_for_function("document.getElementById('spy-msg').classList.contains('error')")
    assert "planète" in page.text_content("#spy-msg")
    assert page.text_content("#spy-status") == "Aucun espionnage lancé." and page.text_content("#spy-closed-title") == "Piliers fermés (0)"
    assert errors == []


def test_exploration_panel_saves_a_manual_composition_with_copies_duration_and_rhythm(ui):
    page, errors, _ = ui
    page.goto(page.base + "/reglages#explorations")
    page.select_option("#exp-composition", "manual")
    page.fill("#exp-units input[aria-label='Quantité Rempart de Fer']", "1500")
    page.fill("#exp-copies", "3")
    page.select_option("#exp-duration", "4")
    page.select_option("#exp-rhythm", "max")
    page.click("#exp-save")
    page.wait_for_function("document.getElementById('exp-msg').textContent === 'Enregistré.'")
    saved = page.evaluate("fetch('/api/explorations').then((r) => r.json()).then((d) => d.settings)")
    assert (saved["composition"], saved["units"], saved["copies"], saved["duration"], saved["rhythm"]) == ("manual", {"Rempart de Fer": 1500}, 3, 4, "max")
    page.select_option("#exp-composition", "model")
    page.click("#exp-save")  # aucun modèle choisi : refusé avec un message en français
    page.wait_for_function("document.getElementById('exp-msg').classList.contains('error')")
    assert "modèle" in page.text_content("#exp-msg")
    assert errors == []


def test_ships_panel_scans_the_models_then_sets_mode_quantity_and_several_planets(ui):
    page, errors, app = ui
    page.click("#scan-run")
    page.wait_for_function("document.querySelectorAll('#scan-list tr').length === 4", timeout=10000)
    page.goto(page.base + "/reglages#ships")
    assert page.locator("#shp-add").is_disabled()  # aucun modèle connu tant que le scan n'a pas eu lieu
    page.click("#shp-scan")
    page.wait_for_function("document.querySelectorAll('#shp-scan-list li').length === 3", timeout=10000)
    assert "3 modèle(s)" in page.inner_text("#shp-scan-status")
    page.wait_for_function("document.querySelectorAll('#shp-add-item option').length === 3", timeout=10000)
    page.select_option("#shp-add-item", "Anode")
    page.select_option("#shp-add-mode", "target:0")
    page.fill("#shp-add-amount", "60")
    page.click("#shp-add-planets summary")
    page.wait_for_function("document.querySelectorAll('#shp-add-planets input').length === 4", timeout=10000)
    page.uncheck("#shp-add-planets input[aria-label^='Toutes']")
    page.check("#shp-add-planets input[aria-label^='6-79-Egide']")
    page.check("#shp-add-planets input[aria-label^='9-169-Strate']")
    page.click("#shp-add")
    page.select_option("#shp-add-item", "Plasmatique")
    page.select_option("#shp-add-mode", "max:0")
    page.click("#shp-add-planets summary")  # le formulaire garde le dernier choix de planètes : on revient à « toutes »
    page.check("#shp-add-planets input[aria-label^='Toutes']")
    assert page.locator("#shp-add-amount-label").is_hidden()  # MAX : pas de quantité
    page.click("#shp-add")
    page.wait_for_function("document.querySelector('#shp-msg').textContent.includes('Enregistré')", timeout=5000)
    assert app.state.bot.ships.get()["queue"] == [
        {"item": "Anode", "mode": "target", "amount": 60, "planets": ["6-79-Egide", "9-169-Strate"], "repeat": False},
        {"item": "Plasmatique", "mode": "max", "amount": 0, "planets": [], "repeat": False},
    ]
    page.select_option("select[aria-label='Mode de Plasmatique']", "fixed:1")
    page.fill("input[aria-label='Quantité de Plasmatique']", "3")
    page.click("button[aria-label='Monter : Plasmatique']")
    page.wait_for_function("document.querySelector('#shp-msg').textContent.includes('Enregistré')", timeout=5000)
    assert [(ln["item"], ln["mode"], ln["amount"], ln["repeat"]) for ln in app.state.bot.ships.get()["queue"]] == [("Plasmatique", "fixed", 3, True), ("Anode", "target", 60, False)]
    assert errors == []


def test_a_line_removed_by_the_bot_disappears_from_the_page(ui):
    page, errors, app = ui
    page.goto(page.base + "/reglages#components")
    page.wait_for_function("document.querySelectorAll('#cmp-add-item option').length === 37", timeout=5000)
    assert page.locator("#cmp-add-item optgroup").evaluate_all("g => g.map(x => x.label)") == ["Infrastructures", "Hangars", "Armement", "Blindages", "Boucliers", "Propulsion"]
    page.select_option("#cmp-add-item", "canon-laser")
    page.click("#cmp-add")
    page.wait_for_function("document.querySelector('#cmp-msg').textContent.includes('Enregistré')", timeout=5000)
    assert page.locator("#cmp-queue li[data-item='canon-laser']").count() == 1
    app.state.bot.components.replace({"queue": []})  # le bot retire la ligne atteinte
    page.wait_for_function("document.querySelectorAll('#cmp-queue li.empty-state').length === 1", timeout=8000)
    assert errors == []
