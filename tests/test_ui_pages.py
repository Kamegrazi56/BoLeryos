"""Les 4 pages (Accueil, Écran, Réglages, Suivi) dans un vrai navigateur."""

import threading
import time

import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from app.main import create_app
from tests.conftest import _free_port

TABS = ["account", "constructions", "research", "production", "priority", "schedule", "settings"]
PAGES = ["/", "/ecran", "/reglages", "/suivi"]


@pytest.fixture
def ui(settings, game):
    """Application réelle servie en HTTP, avec un bot « connecté » sur le faux jeu, et un navigateur."""
    from dataclasses import replace

    from app.bot import State

    app = create_app(replace(settings, login_url=game.url + "/game/asterion/buildings", allow_private_webhooks=True))
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
        page.wait_for_function("document.querySelectorAll('#cons-add-name option').length > 1")
        yield page, errors, app
        browser.close()
    server.should_exit = True
    thread.join(timeout=5)


def _saved(app):
    return app.state.bot.automation.store.get()


def _wait_saved(page):
    page.wait_for_function("document.querySelector('#save-msg').textContent.includes('Enregistré')", timeout=5000)


def _edit(page, action):
    """Fait une saisie puis attend que le nouvel enregistrement soit terminé."""
    action()
    page.wait_for_function("document.querySelector('#save-msg').textContent.includes('en cours') || document.querySelector('#save-msg').textContent.includes('Enregistr')", timeout=3000)
    _wait_saved(page)


# ------------------------------------------------------------------------------ navigation
def test_every_page_has_the_same_four_entries_the_switch_and_the_mode_banner(ui):
    page, errors, _ = ui
    for path in PAGES:
        page.goto(page.base + path)
        page.wait_for_selector("#mode-banner:not([hidden])")
        assert page.locator("header.top nav a").all_inner_texts() == ["Accueil", "Écran", "Réglages", "Suivi"]
        assert page.locator("header.top #power").is_visible()
        assert "SIMULATION" in page.locator("#mode-banner").inner_text()
        assert page.locator("header.top nav a.active").count() == 1
    assert errors == []


def test_old_addresses_still_work(ui):
    page, _, _ = ui
    page.goto(page.base + "/vision")
    assert page.url.endswith("/ecran")
    page.goto(page.base + "/actions")
    assert page.url.endswith("/reglages")


# ------------------------------------------------------------------------------- réglages
def test_settings_are_in_seven_tabs_and_only_one_panel_shows_at_a_time(ui):
    page, errors, _ = ui
    assert [t.get_attribute("data-tab") for t in page.locator("[role=tab]").all()] == TABS
    assert page.locator("[role=tab]").all_inner_texts() == [
        "Compte et univers", "Constructions", "Recherches", "Production", "Priorités", "Horaires et rythme", "Sauvegarde et alertes"]
    for tab in TABS:
        page.click(f"#tab-{tab}")
        visible = [t for t in TABS if page.locator(f"#panel-{t}").is_visible()]
        assert visible == [tab], f"onglet {tab} : panneaux visibles {visible}"
        assert page.locator(f"#tab-{tab}").get_attribute("aria-selected") == "true"
    assert errors == []


def test_selected_tab_is_remembered_and_can_be_opened_from_a_link(ui):
    page, _, _ = ui
    page.click("#tab-research")
    page.reload()
    page.wait_for_selector("#res-goals")
    assert page.locator("#panel-research").is_visible()
    page.goto(page.base + "/reglages#schedule")
    page.wait_for_selector("#windows")
    assert page.locator("#panel-schedule").is_visible()


def test_settings_page_has_no_mode_switch_and_never_sends_the_mode(ui):
    page, _, app = ui
    assert page.locator("#mode").count() == 0
    page.click("#tab-constructions")
    _edit(page, lambda: page.fill("#cons-recheck", "20"))
    assert _saved(app)["mode"] == "dry_run"


def test_the_save_indicator_confirms_each_change(ui):
    page, _, app = ui
    page.click("#tab-constructions")
    page.fill("#cons-recheck", "25")
    _wait_saved(page)
    assert "✓" in page.locator("#save-msg").inner_text()
    assert _saved(app)["constructions"]["recheck_minutes"] == 25.0


def test_account_tab_keeps_credentials_in_memory_and_never_shows_them_again(ui):
    page, _, app = ui
    page.click("#tab-account")
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
    app.state.bot.automation.store.add_catalog("universes", ["Asterion", "Kael"])
    page.reload()
    page.wait_for_function("document.querySelectorAll('#universe-list option').length == 2")
    page.click("#tab-account")
    assert page.locator("#universe-list option").evaluate_all("o => o.map(x => x.value)") == ["Asterion", "Kael"]
    _edit(page, lambda: (page.fill("#universe", "Kael"), page.press("#universe", "Tab")))
    assert _saved(app)["universe"] == "Kael"


def test_entering_the_account_makes_the_bot_connect_by_itself_without_the_switch(settings, game):
    """Comme TBot : identifiants + univers saisis dans Réglages, et le bot se connecte tout seul (interrupteur sur OFF)."""
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
            assert app.state.bot.automation.summary()["running"] is False  # l'interrupteur n'a jamais été touché
            assert "/game/" in app.state.bot._page.url and game.info()["logged_in"] is True
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_buildings_and_research_come_from_built_in_lists_without_any_name_field(ui):
    page, _, _ = ui
    page.click("#tab-constructions")
    names = page.locator("#cons-add-name option").all_text_contents()[1:]
    assert len(names) == 16 and names[:2] == ["Mine de Fer", "Extracteur de Cristal"]
    assert {"Industrie", "Infrastructures"} == {g.get_attribute("label") for g in page.locator("#cons-add-name optgroup").all()}
    assert page.locator("#panel-constructions input[type=text]").count() == 0  # aucune saisie libre de nom
    page.click("#tab-research")
    research = page.locator("#res-goals .goal-name").all_inner_texts()
    assert len(research) == 23 and "Ingénierie minière" in research


def test_the_player_builds_an_ordered_list_of_upgrades(ui):
    page, _, app = ui
    page.click("#tab-constructions")
    assert "Liste vide" in page.locator("#cons-queue").inner_text()  # message de départ
    for name, level in (("Mine de Fer", "45"), ("Centrale Énergétique", "30"), ("Caserne", "20")):
        page.select_option("#cons-add-name", name)
        page.fill("#cons-add-level", level)
        page.click("#cons-add")
    assert page.locator("#cons-queue .queue-row .goal-name").all_inner_texts() == ["Mine de Fer", "Centrale Énergétique", "Caserne"]
    assert "Mine de Fer" not in page.locator("#cons-add-name option").all_text_contents()  # déjà dans la liste
    page.locator("#cons-queue .queue-row").nth(2).locator("button[title^='Monter']").click()
    page.locator("#cons-queue .queue-row").nth(1).locator("button[title^='Monter']").click()  # Caserne passe en premier
    page.locator("#cons-queue .queue-row").nth(2).locator("button[title='Retirer de la liste']").click()  # retire Centrale
    _edit(page, lambda: page.locator("#cons-queue .queue-row").nth(1).locator("input[type=number]").fill("46"))
    queue = _saved(app)["constructions"]["queue"]
    assert [(q["name"], q["level"], q["relative"]) for q in queue] == [("Caserne", 20, False), ("Mine de Fer", 46, False)]
    assert all(q["id"] for q in queue)
    assert page.locator("#cons-queue .queue-row .rank").all_inner_texts() == ["1", "2"]
    assert page.locator("#cons-queue .queue-row").nth(0).locator("button[title^='Monter']").is_disabled()


def test_a_level_can_be_relative_to_the_current_one(ui):
    page, _, app = ui
    page.click("#tab-constructions")
    page.select_option("#cons-add-name", "Mine de Fer")
    page.select_option("#cons-add-mode", "relative")
    page.fill("#cons-add-level", "3")
    page.click("#cons-add")
    page.select_option("#cons-add-name", "Caserne")
    page.fill("#cons-add-level", "10")
    _edit(page, lambda: page.click("#cons-add"))
    page.select_option("#cons-queue .queue-row >> nth=1 >> select", "relative")  # Caserne passe en relatif
    _wait_saved(page)
    queue = _saved(app)["constructions"]["queue"]
    assert [(q["name"], q["level"], q["relative"]) for q in queue] == [("Mine de Fer", 3, True), ("Caserne", 10, True)]


def test_ready_made_lists_fill_the_queue_and_stay_editable(ui):
    page, _, app = ui
    page.click("#tab-constructions")
    assert page.locator("#preset option").all_inner_texts() == [
        "Les 3 mines", "Les 3 mines et l'énergie", "Mines, énergie et stockage"]
    page.select_option("#preset", "mines_energy")
    page.fill("#preset-n", "4")
    _edit(page, lambda: page.click("#preset-apply"))
    queue = _saved(app)["constructions"]["queue"]
    assert [(q["name"], q["level"], q["relative"]) for q in queue] == [
        ("Mine de Fer", 4, True), ("Extracteur de Cristal", 4, True), ("Synthétiseur d'Éther", 4, True), ("Centrale Énergétique", 4, True)]
    assert page.dialogs == []  # liste vide au départ : aucune confirmation
    page.locator("#cons-queue .queue-row").nth(3).locator("button[title='Retirer de la liste']").click()
    _wait_saved(page)
    assert len(_saved(app)["constructions"]["queue"]) == 3
    page.select_option("#preset", "mines")
    page.click("#preset-apply")  # liste non vide : confirmation demandée
    assert any("Remplacer" in m for m in page.dialogs)


def test_research_goals_are_saved_and_empty_means_no_goal(ui):
    page, _, app = ui
    page.click("#tab-research")
    page.fill("#res-goals [data-goal='Technologie laser']", "25")
    page.fill("#res-goals [data-goal='Technologie ionique']", "20")
    page.check("#res-enabled")
    _edit(page, lambda: page.fill("#res-goals [data-goal='Technologie ionique']", ""))
    assert _saved(app)["research"] == {"enabled": True, "targets": {"Technologie laser": 25}, "funding_planet": ""}


def _read_state(page):
    page.goto(page.base + "/suivi#state")
    page.click("#read-state")
    page.wait_for_function("document.querySelector('#read-msg').textContent.includes('terminée')", timeout=60000)
    page.goto(page.base + "/reglages")
    page.wait_for_function("document.querySelectorAll('#cons-add-name option').length > 1")


def test_all_planets_or_a_single_selected_planet(ui):
    page, _, app = ui
    _read_state(page)
    page.click("#tab-constructions")
    assert page.locator("#scope-all").is_checked() and page.locator("#cons-planet").is_disabled()
    assert page.locator("#cons-planet option").all_inner_texts()[1:] == ["6-79-Egide", "8-50-Concorde", "9-169-Strate"]
    page.check("#scope-one")
    assert page.locator("#cons-planet").is_enabled() and page.locator("#cons-planet").input_value() == "6-79-Egide"
    _edit(page, lambda: page.select_option("#cons-planet", "9-169-Strate"))
    cons = _saved(app)["constructions"]
    assert (cons["scope"], cons["planet"]) == ("one", "9-169-Strate")
    _edit(page, lambda: page.check("#scope-all"))
    cons = _saved(app)["constructions"]
    assert (cons["scope"], cons["planet"]) == ("all", "") and page.locator("#cons-planet").is_disabled()
    page.select_option("#cons-add-name", "Mine de Fer")
    page.fill("#cons-add-level", "45")
    page.click("#cons-add")
    assert "actuel : 40" in page.locator("#cons-queue .queue-row").first.inner_text()
    page.click("#tab-research")
    assert "8-50-Concorde" in page.locator("#res-planet option").all_inner_texts()


def test_the_waiting_timer_is_set_in_minutes_and_saved(ui):
    page, _, app = ui
    page.click("#tab-constructions")
    assert page.input_value("#cons-recheck") == "15"
    _edit(page, lambda: page.fill("#cons-recheck", "30"))
    assert _saved(app)["constructions"]["recheck_minutes"] == 30.0


def test_time_windows_are_edited_and_advanced_settings_are_folded(ui):
    page, _, app = ui
    page.click("#tab-schedule")
    page.check("#sched-enabled")
    _edit(page, lambda: page.fill("#windows .window-row:first-child input[type=time] >> nth=0", "09:30"))
    saved = _saved(app)["schedule"]
    assert saved["enabled"] is True and saved["windows"][0]["start"] == "09:30"
    advanced = page.locator("#panel-schedule details.advanced")
    assert advanced.get_attribute("open") is None
    for hidden in ("#tz", "#cap", "#cycle-min", "#cycle-max"):
        assert not page.locator(hidden).is_visible()
    page.click("#panel-schedule details.advanced summary")
    assert page.locator("#tz option").count() > 20
    assert "Maximum de lancements par jour" in advanced.inner_text() and "Fréquence de vérification" in advanced.inner_text()
    _edit(page, lambda: page.fill("#cap", "7"))
    assert _saved(app)["daily_cap"] == 7


def test_help_bubbles_exist_and_explain(ui):
    page, _, _ = ui
    page.click("#tab-constructions")
    helps = page.locator("#panel-constructions .help")
    assert helps.count() >= 2 and "première" in (helps.first.get_attribute("data-help") or "")


# ------------------------------------------------------------------------------ alertes
def test_alert_address_is_never_shown_again_and_bad_addresses_are_refused(ui):
    page, _, app = ui
    page.click("#tab-settings")
    page.fill("#notif-url", "ftp://exemple.invalid/hook")
    page.uncheck("#notif-launch")
    page.click("#notif-save")
    page.wait_for_function("document.querySelector('#notif-msg').textContent.includes('https')")  # autre schéma refusé
    assert not app.state.bot.notifier.configured
    secret = "http://127.0.0.1:9/webhook/TOKEN-SECRET-123"  # autorisé ici : réglage de test
    page.fill("#notif-url", secret)
    page.check("#notif-attention")
    page.click("#notif-save")
    page.wait_for_function("document.querySelector('#notif-status').textContent.includes('127.0.0.1')")
    assert page.input_value("#notif-url") == ""
    assert "TOKEN-SECRET" not in page.content() and "TOKEN-SECRET" not in page.locator("#notif-status").inner_text()
    assert app.state.bot.notifier.wants("attention") and not app.state.bot.notifier.wants("launch")
    page.click("#notif-clear")
    page.wait_for_function("document.querySelector('#notif-status').textContent.includes('Aucune adresse')")


def test_settings_import_cannot_enable_real_mode(ui, tmp_path):
    page, _, app = ui
    page.click("#tab-settings")
    file = tmp_path / "reglages.json"
    file.write_text('{"mode": "live", "daily_cap": 9}', encoding="utf8")
    page.set_input_files("#import", str(file))
    page.wait_for_function("document.querySelector('#save-msg').textContent.includes('importés')")
    assert _saved(app)["mode"] == "dry_run" and _saved(app)["daily_cap"] == 9


# --------------------------------------------------------------------------------- accueil
def test_home_page_says_what_the_bot_does_and_what_is_missing(ui):
    page, _, app = ui
    page.goto(page.base + "/")
    page.wait_for_function("document.querySelector('#sentence').textContent.includes('arrêté')")
    assert page.locator("#checklist li").count() == 5
    work = page.locator("#checklist li[data-step=work]")
    assert "✗" in work.inner_text() and work.locator("a").get_attribute("href") == "/reglages#constructions"
    assert "✓" in page.locator("#checklist li[data-step=account]").inner_text()  # déjà connecté
    assert page.locator("[data-power]").is_visible()
    assert "Simulation (ne clique pas)" in page.inner_text("#mode-sim")
    assert "Aucun événement" in page.inner_text("#events") or page.locator("#events li").count() >= 1
    assert "Rien pour l'instant" in page.inner_text("#sim-list")


def test_switch_on_with_missing_items_opens_a_dialog_with_fix_links(ui):
    page, _, app = ui
    page.goto(page.base + "/")
    page.wait_for_selector("#checklist li")
    page.click("[data-power]")
    dialog = page.locator("dialog.modal")
    dialog.wait_for(state="visible")
    assert "Quelque chose à faire" in dialog.inner_text()
    assert dialog.locator("a", has_text="Corriger").get_attribute("href") == "/reglages#constructions"
    dialog.get_by_role("button", name="Annuler").click()
    time.sleep(0.5)
    assert app.state.bot.automation.summary()["running"] is False
    page.click("[data-power]")
    page.get_by_role("button", name="Démarrer quand même").click()
    page.wait_for_function("document.querySelector('#power').textContent.includes('ON')", timeout=15000)
    assert app.state.bot.automation.summary()["running"] is True
    assert "ON" in page.inner_text("[data-power]")
    page.click("#power")
    page.wait_for_function("document.querySelector('#power').textContent.includes('OFF')", timeout=15000)
    assert app.state.bot.automation.summary()["running"] is False


def test_real_mode_needs_a_confirmation_and_the_banner_follows(ui):
    page, _, app = ui
    page.goto(page.base + "/")
    page.wait_for_selector("#mode-sim")
    page.click("#mode-live")
    page.wait_for_function("document.querySelector('#mode-banner').textContent.includes('MODE RÉEL')")
    assert any("Mode RÉEL" in m and "aucune simulation" in m for m in page.dialogs)  # prévenu : rien n'a été simulé
    assert _saved(app)["mode"] == "live"
    assert "live" in page.get_attribute("#mode-banner", "class")
    page.click("#mode-sim")
    page.wait_for_function("document.querySelector('#mode-banner').textContent.includes('SIMULATION')")
    assert _saved(app)["mode"] == "dry_run"


def test_refused_real_mode_leaves_the_simulation(ui):
    page, _, app = ui
    page.goto(page.base + "/")
    page.wait_for_selector("#mode-sim")
    page.accept_dialogs = False  # l'utilisateur répond « Annuler » à la confirmation
    page.click("#go-live")
    time.sleep(0.5)
    assert _saved(app)["mode"] == "dry_run"


def test_simulation_is_summarised_in_sentences_then_real_mode_is_offered(ui):
    page, _, app = ui
    page.goto(page.base + "/reglages#constructions")
    page.wait_for_function("document.querySelectorAll('#cons-add-name option').length > 1")
    page.select_option("#cons-add-name", "Mine de Fer")
    page.fill("#cons-add-level", "45")
    page.click("#cons-add")
    page.check("#cons-enabled")
    _wait_saved(page)
    page.click("#power")  # tout est prêt : aucune boîte de dialogue
    page.wait_for_function("document.querySelector('#power').textContent.includes('ON')", timeout=15000)
    page.goto(page.base + "/")
    page.wait_for_function("document.querySelectorAll('#sim-list li:not(.empty-state)').length >= 1", timeout=60000)
    line = page.locator("#sim-list li").first.inner_text()
    assert "sur 6-79-Egide, j'aurais lancé Mine de Fer niveau 41" in line and ("À l'instant" in line or "Il y a" in line)
    assert page.locator("#go-live").is_visible()
    page.wait_for_function("document.querySelector('#events').textContent.includes('Simulation')", timeout=30000)
    page.click("#power")


# ----------------------------------------------------------------------------------- suivi
def test_followed_planets_and_readable_journal(ui):
    page, _, app = ui
    page.goto(page.base + "/reglages#constructions")
    page.wait_for_function("document.querySelectorAll('#cons-add-name option').length > 1")
    page.select_option("#cons-add-name", "Mine de Fer")
    page.fill("#cons-add-level", "45")
    page.click("#cons-add")
    page.check("#cons-enabled")
    _wait_saved(page)
    page.click("#power")
    page.wait_for_function("document.querySelector('#power').textContent.includes('ON')", timeout=15000)
    page.goto(page.base + "/suivi")
    assert page.locator("#panel-log").is_visible() and not page.locator("#panel-planets").is_visible()
    page.wait_for_function("document.querySelector('#log').textContent.includes('Simulation')", timeout=60000)
    events = page.locator("#log div").count()
    page.check("#log-details")
    assert page.locator("#log div").count() >= events  # les détails techniques s'ajoutent aux événements
    page.select_option("#log-level", "error")
    assert "Journal vide" in page.inner_text("#log") or page.locator("#log div.error").count() == page.locator("#log div").count()
    page.click("#tab-planets")
    page.wait_for_function("document.querySelectorAll('#cons-pending tr').length >= 4", timeout=60000)
    text = page.locator("#cons-pending").inner_text()
    assert "8-50-Concorde" in text and "en attente de ressources" in text and "dans" in text
    assert "réalisable (simulation)" in text
    page.click("#power")


def test_game_state_is_read_on_the_follow_up_page(ui):
    page, _, _ = ui
    page.goto(page.base + "/suivi")
    page.click("#tab-state")
    page.click("#read-state")
    page.wait_for_function("document.querySelector('#read-msg').textContent.includes('terminée')", timeout=60000)
    assert "Planètes (3)" in page.inner_text("#state-out")


def test_screen_page_keeps_live_view_and_manual_control(ui):
    page, errors, _ = ui
    page.goto(page.base + "/ecran")
    assert page.locator("#screen").is_visible() and page.locator("#manual").is_visible()
    assert page.locator("#log").count() == 1 and errors == []


# ---------------------------------------------------------------------------------- production
def _add_prod(page, section, name, mode="once", amount=None, maxi=False):
    page.select_option("#prod-add-section", section)
    page.select_option("#prod-add-name", name)
    page.select_option("#prod-add-mode", mode)
    if maxi:
        page.check("#prod-add-max")
    else:
        page.fill("#prod-add-amount", str(amount))
    page.click("#prod-add")


def test_production_tab_builds_lines_from_built_in_lists_with_three_behaviours(ui):
    page, _, app = ui
    page.click("#tab-production")
    assert page.locator("#prod-add-section option").all_inner_texts() == [
        "Population", "Chantier spatial : vaisseaux", "Chantier spatial : composants", "Appareils spécialisés", "Défenses"]
    names = page.locator("#prod-add-name option").all_text_contents()[1:]
    assert names[:3] == ["Rempart de Fer", "Spectre du Rift", "Surchargeur Plasmique"] and "Mineur" in names
    assert "Liste vide" in page.inner_text("#prod-queue")
    _add_prod(page, "population", "Rempart de Fer", "once", 50)
    _add_prod(page, "defense", "Éclateur Ionique", "stock", 200)
    _add_prod(page, "specialized", "Drone Terrestre", "repeat", maxi=True)
    _wait_saved(page)
    queue = _saved(app)["production"]["queue"]
    assert [(q["section"], q["name"], q["mode"], q["amount"], q["max"]) for q in queue] == [
        ("population", "Rempart de Fer", "once", 50, False), ("defense", "Éclateur Ionique", "stock", 200, False),
        ("specialized", "Drone Terrestre", "repeat", 1, True)]
    assert all(q["id"] for q in queue)
    assert "Rempart de Fer" not in page.locator("#prod-add-name option").all_text_contents()  # déjà dans la liste
    # « maximum » n'existe pas pour un objectif de stock
    page.select_option("#prod-add-mode", "stock")
    assert page.locator("#prod-add-max").is_disabled()
    page.locator("#prod-queue .queue-row").nth(2).locator("select").select_option("stock")
    _wait_saved(page)
    third = _saved(app)["production"]["queue"][2]
    assert third["mode"] == "stock" and third["max"] is False


def test_production_lines_can_be_reordered_edited_and_removed_and_scope_chosen(ui):
    page, _, app = ui
    page.click("#tab-production")
    _add_prod(page, "population", "Pilote", "once", 5)
    _add_prod(page, "population", "Mineur", "once", 7)
    page.locator("#prod-queue .queue-row").nth(1).locator("button[title^='Monter']").click()
    _edit(page, lambda: page.locator("#prod-queue .queue-row").nth(0).locator("input[type=number]").fill("9"))
    queue = _saved(app)["production"]["queue"]
    assert [(q["name"], q["amount"]) for q in queue] == [("Mineur", 9), ("Pilote", 5)]
    _edit(page, lambda: page.locator("#prod-queue .queue-row").nth(1).locator("button[title='Retirer de la liste']").click())
    assert [q["name"] for q in _saved(app)["production"]["queue"]] == ["Mineur"]
    app.state.bot.automation.store.add_catalog("planets", ["6-79-Egide"])
    page.reload()
    page.wait_for_function("document.querySelectorAll('#prod-planet option').length > 1")
    page.click("#tab-production")
    _edit(page, lambda: page.check("#pscope-one"))
    assert _saved(app)["production"]["scope"] == "one" and _saved(app)["production"]["planet"] == "6-79-Egide"
    _edit(page, lambda: page.check("#prod-enabled"))
    assert _saved(app)["production"]["enabled"] is True


def test_priority_tab_orders_the_blocks(ui):
    page, _, app = ui
    page.click("#tab-priority")
    rows = page.locator("#priority-list .queue-row")
    assert rows.nth(0).get_attribute("data-block") == "constructions" and rows.count() == 6
    _edit(page, lambda: rows.nth(4).locator("button[title^='Plus']").click())
    _edit(page, lambda: page.locator("#priority-list .queue-row").nth(3).locator("button[title^='Plus']").click())
    assert _saved(app)["priority"] == ["constructions", "research", "specialized", "population", "shipyard", "defense"]
