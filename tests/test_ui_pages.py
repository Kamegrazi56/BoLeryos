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
        page.wait_for_selector("#acct-status")
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
    assert tabs == ["compte et univers", "fonctionnalités", "planning", "discord", "constructions", "recherches", "population", "assemblages", "composants", "places de mission", "exploration", "fouilles", "piratage", "forages", "piliers", "flottes", "espionnage", "jetons d'alliance"]
    for tab in tabs:  # aucune section supprimée ne réapparaît, quel que soit l'onglet
        page.click(f'[role=tab]:has-text("{tab}")', strict=False)
        # titres seulement : « Expéditions simultanées » (Exploration) est un réglage, pas l'ancienne section Expéditions
        shown = [t.strip().lower() for t in page.locator("main h2:visible, main h3:visible, [role=tab]").all_inner_texts()]
        titles = " ".join(shown)
        for word in ("expéditions", "attaques", "alertes"):
            assert word not in titles
        assert "horaires" not in shown  # l'ancienne section « Horaires » ; « Horaires du piratage » est la nouvelle
    for route in ("/api/automation", "/api/dashboard", "/api/notifications"):
        assert page.request.get(page.base + route).status == 404


# ------------------------------------------------------------------------------- réglages
def test_settings_has_no_login_form_and_never_shows_credentials(ui):
    """La connexion se fait uniquement sur l'Accueil : Réglages n'a ni champ ni bouton de connexion."""
    page, _, app = ui
    for selector in ("#acct-id", "#acct-pw", "#acct-save", "#acct-login", "#acct-forget"):
        assert page.locator(selector).count() == 0, selector
    app.state.bot.set_credentials("titulaire.secret@example.com", "mot-de-passe-tres-secret")
    page.reload()
    page.wait_for_function("document.querySelector('#acct-status').textContent.includes('en mémoire (ti***)')", timeout=5000)
    assert "titulaire.secret" not in page.content() and "mot-de-passe-tres-secret" not in page.content()
    app.state.bot.forget_credentials()
    page.reload()
    page.wait_for_function("document.querySelector('#acct-status').textContent.includes('page Accueil')", timeout=5000)


def test_settings_no_longer_offer_to_choose_the_universe(ui):
    page, _, _ = ui
    for selector in ("#universe", "#universe-list"):
        assert page.locator(selector).count() == 0, selector
    for selector in ("#login-delay-min", "#login-delay-max", "#engine", "#scan-run"):
        assert page.locator(selector).count() == 1, selector


def test_login_delay_is_saved_and_a_bad_range_is_refused(ui):
    page, _, app = ui
    _edit(page, lambda: (page.fill("#login-delay-min", "2"), page.fill("#login-delay-max", "3")))
    assert _saved(app)["login_delay"] == {"min_seconds": 2.0, "max_seconds": 3.0}
    page.fill("#login-delay-min", "9")
    page.wait_for_function("document.querySelector('#save-msg').className.includes('error')", timeout=5000)
    assert _saved(app)["login_delay"]["min_seconds"] == 2.0


def test_entering_the_account_makes_the_bot_connect_by_itself(settings, game):
    """Comme TBot : identifiants saisis sur l'Accueil + univers de l'instance, et le bot se connecte tout seul."""
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
            page.wait_for_selector("#engine")
            # L'univers n'est plus dans les Réglages : il vient de l'instance (créée depuis l'Accueil) ; ici, par la même route.
            assert page.request.put(f"http://127.0.0.1:{port}/api/preferences", data={"universe": "Asterion"}).ok
            # La connexion se fait depuis l'Accueil : ici on enregistre les identifiants par la même route que son bouton.
            assert page.request.post(f"http://127.0.0.1:{port}/api/account/credentials", data={"identifier": "joueur.test@example.com", "password": "good-password"}).ok
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
    assert page.locator("#sentence").count() == 0 and page.locator("#hero-title").count() == 0  # plus de carte « Connexion »
    assert errors == []


def test_follow_up_page_is_only_the_journal(ui):
    page, _, app = ui
    page.click("#scan-run")
    page.wait_for_function("document.querySelectorAll('#scan-list tr').length === 4", timeout=10000)
    page.goto(page.base + "/suivi")
    assert page.locator("[role=tab]").count() == 0 and page.locator("#read-state").count() == 0
    page.wait_for_function("document.querySelector('#log').textContent.includes('Scan des planètes')", timeout=10000)
    # le plus récent en haut : la dernière ligne écrite par le bot est la première affichée
    app.state.bot.log("Ligne d'essai la plus récente.", event=True)
    page.wait_for_function("document.querySelector('#log div').textContent.includes('Ligne d\\'essai la plus récente.')", timeout=5000)
    assert page.evaluate("document.getElementById('log').scrollTop") == 0
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
    page.fill("[data-feature=test] input[aria-label^='Minuteur minimum, minutes']", "0.5")  # heures laissées vides : 0
    page.fill("[data-feature=test] input[aria-label^='Minuteur maximum, minutes']", "1")
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


def test_constructions_panel_raises_targets_from_the_levels_table(ui, game):
    page, errors, app = ui
    game.reset(levels={"0": {"Caserne": 10}, "1": {"Caserne": 12}})  # niveaux différents selon la planète
    page.click("#scan-run")
    page.wait_for_function("document.querySelectorAll('#scan-list tr').length === 4", timeout=10000)
    page.goto(page.base + "/reglages#constructions")
    assert page.locator("#cons-add, #cons-add-building, #cons-add-level").count() == 0  # plus de liste déroulante : on ne passe que par le tableau
    page.click("#cons-inv-refresh")
    page.wait_for_function("document.querySelectorAll('#cons-inv td[data-planet]').length === 16 * 3", timeout=20000)
    assert page.locator("#cons-inv button[aria-expanded]").evaluate_all("b => b.map(x => x.textContent.trim())") == ["▾ Industrie · 8", "▾ Infrastructures · 8"]
    # tableau affiché en entier : ni hauteur maximale ni barre de défilement
    assert page.locator("#cons-inv").evaluate("e => getComputedStyle(e).maxHeight === 'none' && e.scrollHeight <= e.clientHeight + 1 && e.scrollWidth <= e.clientWidth + 1")
    # un clic sur la ligne de catégorie masque, puis réaffiche, ses bâtiments
    page.click("#cons-inv button[aria-expanded]:has-text('Industrie')")
    assert page.locator("#cons-inv tr[data-building]").count() == 8 and page.locator("#cons-inv tr[data-building='mine-de-fer']").count() == 0
    page.click("#cons-inv td.inv-group:has-text('Industrie')")  # toute la ligne est cliquable
    assert page.locator("#cons-inv tr[data-building]").count() == 16
    # « +1 » d'une case : objectif propre à cette planète, puis un deuxième clic le monte encore (40 → 41 → 42)
    cell = "#cons-inv tr[data-building='mine-de-fer'] td[data-planet='6-79-Egide'] button.add-level"
    page.click(cell)
    page.click(cell)
    page.wait_for_function("document.querySelector('#cons-msg').textContent.includes('Enregistré')", timeout=5000)
    assert app.state.bot.constructions.get()["queue"] == [{"building": "mine-de-fer", "level": 42, "planet": "6-79-Egide"}]
    assert page.text_content("#cons-inv tr[data-building='mine-de-fer'] td[data-planet='6-79-Egide'] .lv") == "40 → 42"
    # « +1 » en début de ligne : chaque planète monte de 1 niveau, depuis SON niveau
    before = page.locator("#cons-inv tr[data-building='caserne'] td[data-planet] .lv").evaluate_all("l => l.map(x => Number(x.textContent))")
    assert before[:2] == [10, 12]
    page.click("#cons-inv tr[data-building='caserne'] th button.add-level")
    page.wait_for_function("document.querySelector('#cons-msg').textContent.includes('Enregistré')", timeout=5000)
    caserne = [line for line in app.state.bot.constructions.get()["queue"] if line["building"] == "caserne"]
    assert [line["level"] for line in caserne] == [level + 1 for level in before]
    assert [line["planet"] for line in caserne] == ["8-50-Concorde", "6-79-Egide", "9-169-Strate"]
    assert page.locator("#cons-queue li[data-building]").count() == 4  # la liste montre une ligne par bâtiment et par planète
    # la liste est regroupée par planète (ordre du scan), un titre par planète, et la priorité se règle au sein de chaque planète
    assert page.locator("#cons-queue li[data-planet]").evaluate_all("l => l.map(x => x.dataset.planet)") == ["6-79-Egide", "6-79-Egide", "8-50-Concorde", "9-169-Strate"]
    assert page.locator("#cons-queue li.group-head .group-toggle").evaluate_all("h => h.map(x => x.textContent)") == ["▾ 6-79-Egide · 2", "▾ 8-50-Concorde · 1", "▾ 9-169-Strate · 1"]
    # menu déroulant par planète : un clic masque (puis réaffiche) les lignes de cette planète seulement
    page.click("#cons-queue .group-toggle:has-text('6-79-Egide')")
    assert page.locator("#cons-queue li[data-planet='6-79-Egide']").count() == 0 and page.locator("#cons-queue li[data-building]").count() == 2
    assert page.get_attribute("#cons-queue .group-toggle:has-text('6-79-Egide')", "aria-expanded") == "false"
    page.click("#cons-queue .group-toggle:has-text('6-79-Egide')")
    assert page.locator("#cons-queue li[data-building]").count() == 4
    # deux modes de lancement par planète, « dès que les ressources suffisent » par défaut ; le choix est enregistré pour cette planète
    assert page.locator("#cons-queue select.mode").evaluate_all("s => s.map(x => x.value)") == ["any", "any", "any"]
    page.select_option("#cons-queue select[aria-label='Mode de lancement : 8-50-Concorde']", "order")
    page.wait_for_function("document.querySelector('#cons-msg').textContent.includes('Enregistré')", timeout=5000)
    assert app.state.bot.constructions.get()["modes"] == {"8-50-Concorde": "order"}
    assert page.locator("button[aria-label='Monter : Caserne (8-50-Concorde)']").is_disabled()  # seule ligne de sa planète
    assert page.locator("button[aria-label='Monter : Mine de Fer (6-79-Egide)']").is_disabled()  # première de sa planète
    page.click("button[aria-label='Monter : Caserne (6-79-Egide)']")
    page.wait_for_function("document.querySelector('#cons-msg').textContent.includes('Enregistré')", timeout=5000)
    first = app.state.bot.constructions.get()["queue"][0]
    assert (first["building"], first["planet"]) == ("caserne", "6-79-Egide")
    assert page.locator("#cons-queue li[data-planet='6-79-Egide']").evaluate_all("l => l.map(x => x.dataset.building)") == ["caserne", "mine-de-fer"]
    page.wait_for_function("document.querySelectorAll('#cons-planets .planet-grid input').length === 3", timeout=10000)
    assert page.is_checked("#cons-planets input[value='all']")
    page.check("#cons-planets input[value='some']")
    page.wait_for_function("document.querySelector('#cons-msg').className.includes('error')", timeout=5000)  # pas de planète cochée
    assert app.state.bot.constructions.get()["planets"] == []  # rien enregistré
    page.check("#cons-planets input[aria-label^='8-50-Concorde']")
    page.check("#cons-planets input[aria-label^='6-79-Egide']")
    page.wait_for_function("document.querySelector('#cons-msg').textContent.includes('Enregistré')", timeout=5000)
    assert app.state.bot.constructions.get()["planets"] == ["6-79-Egide", "8-50-Concorde"]
    page.reload()  # réaffiché tel qu'enregistré
    page.wait_for_function("document.querySelectorAll('#cons-planets .planet-grid input:checked').length === 2", timeout=10000)
    assert page.is_checked("#cons-planets input[value='some']")
    page.click("button[aria-label='Tout cocher : les constructions']")
    page.wait_for_function("document.querySelector('#cons-msg').textContent.includes('Enregistré')", timeout=5000)
    assert len(app.state.bot.constructions.get()["planets"]) == 3
    page.click("button[aria-label='Supprimer : Mine de Fer (6-79-Egide)']")
    page.wait_for_function("document.querySelectorAll('#cons-queue li[data-building]').length === 3", timeout=5000)
    assert errors == []


def test_researches_panel_builds_an_ordered_list_with_one_research_planet(ui):
    page, errors, app = ui
    page.click("#scan-run")
    page.wait_for_function("document.querySelectorAll('#scan-list tr').length === 4", timeout=10000)
    page.goto(page.base + "/reglages#researches")
    assert page.locator("#res-add, #res-add-research, #res-add-level").count() == 0  # plus de liste déroulante : on ne passe que par le tableau
    page.wait_for_function("document.querySelectorAll('#res-levels tr[data-research]').length === 23", timeout=5000)
    assert page.locator("#res-levels button[aria-expanded]").evaluate_all("b => b.map(x => x.textContent.trim())") == ["▾ Civil · 11", "▾ Militaire · 12"]
    assert page.locator("#res-levels tr[data-research='technologie-laser'] button.add-level").is_disabled()  # niveau pas encore relevé
    page.click("#res-inv-refresh")
    page.wait_for_function("document.querySelector('#res-levels tr[data-research=technologie-laser] td').textContent === '24'", timeout=20000)
    # tableau affiché en entier : ni hauteur maximale ni barre de défilement
    assert page.locator("#res-levels").evaluate("e => getComputedStyle(e).maxHeight === 'none' && e.scrollHeight <= e.clientHeight + 1 && e.scrollWidth <= e.clientWidth + 1")
    # un clic sur la ligne Civil / Militaire masque, puis réaffiche, ses recherches
    page.click("#res-levels button[aria-expanded]:has-text('Civil')")
    assert page.locator("#res-levels tr[data-research]").count() == 12
    assert page.locator("#res-levels button[aria-expanded='false']").count() == 1 and page.locator("#res-levels tr[data-research='ordinateur-quantique']").count() == 0
    page.click("#res-levels td.inv-group:has-text('Civil')")  # toute la ligne est cliquable, pas seulement le texte
    assert page.locator("#res-levels tr[data-research]").count() == 23
    page.wait_for_function("document.querySelectorAll('#res-planet option').length > 1", timeout=10000)
    page.click("#res-levels tr[data-research='technologie-laser'] button.add-level")  # niveau 24 → objectif 25
    page.wait_for_function("document.querySelector('#res-msg').className.includes('error')", timeout=5000)  # pas de planète choisie
    page.select_option("#res-planet", "6-79-Egide")
    page.click("#res-levels tr[data-research='technologie-energie'] button.add-level")  # niveau 20 → objectif 21
    page.wait_for_function("document.querySelector('#res-msg').textContent.includes('Enregistré')", timeout=5000)
    assert page.locator("#res-queue li[data-research]").evaluate_all("l => l.map(x => x.dataset.research)") == ["technologie-laser", "technologie-energie"]
    assert [line["level"] for line in app.state.bot.researches.get()["queue"]] == [25, 21]
    assert page.locator("#res-levels tr[data-research='technologie-laser'] button.add-level").is_disabled()  # déjà dans la liste
    page.click("button[aria-label='Monter : Technologie Énergie']")
    page.wait_for_function("document.querySelector('#res-msg').textContent.includes('Enregistré')", timeout=5000)
    saved = app.state.bot.researches.get()
    assert saved["planet"] == "6-79-Egide" and [line["research"] for line in saved["queue"]] == ["technologie-energie", "technologie-laser"]
    assert errors == []


def test_population_panel_buttons_create_lines_in_the_chosen_mode(ui):
    page, errors, app = ui
    page.click("#scan-run")
    page.wait_for_function("document.querySelectorAll('#scan-list tr').length === 4", timeout=10000)
    page.goto(page.base + "/reglages#populations")
    assert page.locator("#pop-add, #pop-add-population, #pop-add-mode, #pop-add-amount").count() == 0  # plus de liste déroulante : on ne passe que par le tableau
    assert "Aucun effectif relevé" in page.text_content("#pop-inv")
    page.click("#pop-inv-refresh")
    page.wait_for_function("document.querySelectorAll('#pop-inv td[data-planet]').length === 10 * 3", timeout=20000)
    assert page.locator("#pop-inv button[aria-expanded]").evaluate_all("b => b.map(x => x.textContent.trim())") == ["▾ Assaut · 4", "▾ Logistique · 3", "▾ Administration · 3"]
    # tableau affiché en entier : ni hauteur maximale ni barre de défilement
    assert page.locator("#pop-inv").evaluate("e => getComputedStyle(e).maxHeight === 'none' && e.scrollHeight <= e.clientHeight + 1 && e.scrollWidth <= e.clientWidth + 1")
    # un clic sur la ligne de catégorie masque, puis réaffiche, ses populations
    page.click("#pop-inv button[aria-expanded]:has-text('Assaut')")
    assert page.locator("#pop-inv tr[data-population]").count() == 6 and page.locator("#pop-inv tr[data-population='rempart-de-fer']").count() == 0
    page.click("#pop-inv td.inv-group:has-text('Assaut')")  # toute la ligne est cliquable
    assert page.locator("#pop-inv tr[data-population]").count() == 10
    # barre au-dessus du tableau : par défaut « Nombre à atteindre », qui ne se répète jamais
    assert page.locator("#pop-tools input[name=pop-tool-mode]").evaluate_all("r => r.map(x => [x.value, x.checked])") == [["max", False], ["fixed", False], ["target", True]]
    assert page.locator("#pop-tool-repeat").is_disabled() and page.is_visible("#pop-tool-amount")
    # « Nombre à atteindre » 12 : le bouton d'une case fixe l'objectif de CETTE planète, un deuxième clic ne change rien
    page.fill("#pop-tool-amount", "12")
    cell = "#pop-inv tr[data-population='pilote'] td[data-planet='6-79-Egide'] button.add-level"
    assert page.text_content(cell) == "→ 12"
    page.click(cell)
    page.click(cell)
    page.wait_for_function("document.querySelector('#pop-msg').textContent.includes('Enregistré')", timeout=5000)
    assert app.state.bot.populations.get()["queue"] == [{"population": "pilote", "mode": "target", "amount": 12, "planets": ["6-79-Egide"], "repeat": False}]
    assert page.text_content("#pop-inv tr[data-population='pilote'] td[data-planet='6-79-Egide'] .lv") == "10 → 12"
    # le bouton en début de ligne agit sur chaque planète : 6 Mineurs partout
    page.fill("#pop-tool-amount", "6")
    page.click("#pop-inv tr[data-population='mineur'] th button.add-level")
    page.wait_for_function("document.querySelector('#pop-msg').textContent.includes('Enregistré')", timeout=5000)
    mineurs = [ln for ln in app.state.bot.populations.get()["queue"] if ln["population"] == "mineur"]
    assert [(ln["planets"][0], ln["amount"], ln["mode"], ln["repeat"]) for ln in mineurs] == [("8-50-Concorde", 6, "target", False), ("6-79-Egide", 6, "target", False), ("9-169-Strate", 6, "target", False)]
    assert page.locator("#pop-queue li[data-population]").count() == 4  # une ligne par population et par planète
    # « Quantité définie » 25, à chaque passage : la ligne de la case est REMPLACÉE, à sa place dans la liste
    page.check("#pop-tools input[value=fixed]")
    assert page.locator("#pop-tool-repeat").is_enabled() and page.is_checked("#pop-tool-repeat")  # à chaque passage par défaut
    page.fill("#pop-tool-amount", "25")
    assert page.text_content(cell) == "+25"
    page.click(cell)
    page.wait_for_function("document.querySelector('#pop-msg').textContent.includes('Enregistré')", timeout=5000)
    queue = app.state.bot.populations.get()["queue"]
    assert queue[0] == {"population": "pilote", "mode": "fixed", "amount": 25, "planets": ["6-79-Egide"], "repeat": True} and len(queue) == 4
    assert page.text_content("#pop-inv tr[data-population='pilote'] td[data-planet='6-79-Egide'] .tag") == "+25 ↻"
    # l'interrupteur « Une seule fois » s'applique aux lignes créées ensuite
    page.uncheck("#pop-tool-repeat")
    assert "Une seule fois" in page.text_content("#pop-tool-repeat-label")
    page.click(cell)
    page.wait_for_function("document.querySelector('#pop-msg').textContent.includes('Enregistré')", timeout=5000)
    assert app.state.bot.populations.get()["queue"][0]["repeat"] is False
    assert page.text_content("#pop-inv tr[data-population='pilote'] td[data-planet='6-79-Egide'] .tag") == "+25 1×"
    # « Max » : pas de quantité, le bouton affiche MAX
    page.check("#pop-tools input[value=max]")
    assert page.is_hidden("#pop-tool-amount") and page.text_content(cell) == "MAX"
    strate = "#pop-inv tr[data-population='mineur'] td[data-planet='9-169-Strate'] button.add-level"
    page.click(strate)
    page.wait_for_function("document.querySelector('#pop-msg').textContent.includes('Enregistré')", timeout=5000)
    last = [ln for ln in app.state.bot.populations.get()["queue"] if ln["population"] == "mineur" and ln["planets"] == ["9-169-Strate"]]
    assert last == [{"population": "mineur", "mode": "max", "amount": 0, "planets": ["9-169-Strate"], "repeat": False}]
    # une quantité invalide désactive les boutons (sauf en mode Max)
    page.check("#pop-tools input[value=target]")
    page.fill("#pop-tool-amount", "0")
    assert page.locator("#pop-inv button.add-level:not([disabled])").count() == 0
    page.fill("#pop-tool-amount", "3")
    assert page.locator("#pop-inv button.add-level[disabled]").count() == 0
    # la liste reste modifiable : ordre, suppression
    page.click("button[aria-label='Monter : Mineur']")
    page.wait_for_function("document.querySelector('#pop-msg').textContent.includes('Enregistré')", timeout=5000)
    assert app.state.bot.populations.get()["queue"][0]["population"] == "mineur"
    page.click("button[aria-label='Supprimer : Pilote']")
    page.wait_for_function("document.querySelectorAll('#pop-queue li[data-population]').length === 3", timeout=5000)
    assert errors == []


def test_population_cell_goes_above_the_target_of_an_all_planets_line(ui):
    page, errors, app = ui
    page.click("#scan-run")
    page.wait_for_function("document.querySelectorAll('#scan-list tr').length === 4", timeout=10000)
    page.goto(page.base + "/reglages#populations")
    page.click("#pop-inv-refresh")
    page.wait_for_function("document.querySelectorAll('#pop-inv td[data-planet]').length === 10 * 3", timeout=20000)
    app.state.bot.populations.replace({"queue": [{"population": "pilote", "mode": "target", "amount": 50, "planets": [], "repeat": False}]})
    page.reload()
    page.wait_for_function("document.querySelectorAll('#pop-inv td[data-planet]').length === 10 * 3", timeout=20000)
    assert page.text_content("#pop-inv tr[data-population='pilote'] td[data-planet='8-50-Concorde'] .lv") == "10 → 50"
    page.fill("#pop-tool-amount", "51")
    page.click("#pop-inv tr[data-population='pilote'] td[data-planet='8-50-Concorde'] button.add-level")
    page.wait_for_function("document.querySelector('#pop-msg').textContent.includes('Enregistré')", timeout=5000)
    queue = app.state.bot.populations.get()["queue"]
    assert [(ln["planets"], ln["amount"]) for ln in queue] == [([], 50), (["8-50-Concorde"], 51)]  # la ligne de la planète est la plus précise
    page.reload()  # les réglages des boutons sont retenus par le navigateur
    page.wait_for_function("document.querySelectorAll('#pop-inv td[data-planet]').length === 10 * 3", timeout=20000)
    assert page.input_value("#pop-tool-amount") == "51" and page.is_checked("#pop-tools input[value=target]")
    assert errors == []


def test_pillars_panel_programs_a_change_and_explains_a_refusal(ui):
    page, errors, app = ui
    page.click("#scan-run")
    page.wait_for_function("document.querySelectorAll('#scan-list tr').length === 4", timeout=10000)
    page.goto(page.base + "/reglages#pillars")
    page.reload()
    page.wait_for_function("document.querySelectorAll('#pil-planet option').length === 3", timeout=10000)
    assert page.input_value("#pil-at")  # date proposée : dans 2 h
    assert "Le bot cliquera le" in page.text_content("#pil-hint")
    page.select_option("#pil-planet", "6-79-Egide")
    page.check("input[name=pil-action][value=close]")
    page.click("#pil-add")
    page.wait_for_function("document.getElementById('pil-msg').classList.contains('ok')")
    (change,) = app.state.bot.pillars.pending()
    assert (change["planet"], change["action"]) == ("6-79-Egide", "close")
    assert page.locator("#pil-changes li[data-id]").count() == 1 and "Programmé" in page.text_content("#pil-changes")
    page.click("#pil-add")  # même planète, même heure : déjà fermée à ce moment-là
    page.wait_for_function("document.getElementById('pil-msg').classList.contains('error')")
    assert "déjà fermés" in page.text_content("#pil-msg")
    page.click(f"#pil-changes li[data-id='{change['id']}'] button")
    page.wait_for_function("document.querySelectorAll('#pil-changes li[data-id]').length === 0")
    assert app.state.bot.pillars.pending() == []
    assert errors == []


def test_fleets_panel_reads_the_flights_programs_a_return_and_explains_a_refusal(ui):
    page, errors, app = ui
    page.click("#scan-run")
    page.wait_for_function("document.querySelectorAll('#scan-list tr').length === 4", timeout=10000)
    page.goto(page.base + "/reglages#fleets")
    page.reload()
    assert "Aucun relevé" in page.text_content("#flt-status-line")
    page.click("#flt-scan")
    page.wait_for_function("document.querySelectorAll('#flt-fleet option').length === 1", timeout=20000)
    assert "Déploiement" in page.text_content("#flt-fleets") and "476051 U" in page.text_content("#flt-fleets")
    page.fill("#flt-at", "2020-01-01T10:00")  # dans le passé
    page.click("#flt-add")
    page.wait_for_function("document.getElementById('flt-msg').classList.contains('error')")
    assert "déjà passée" in page.text_content("#flt-msg")
    page.fill("#flt-at", time.strftime("%Y-%m-%dT%H:%M", time.localtime(time.time() + 7 * 24 * 3600)))
    page.click("#flt-add")
    page.wait_for_function("document.getElementById('flt-msg').classList.contains('ok')")
    (recall,) = app.state.bot.fleets.pending()
    assert page.locator("#flt-recalls li[data-id]").count() == 1 and "Programmé" in page.text_content("#flt-recalls")
    page.fill("#flt-margin", "9")
    page.press("#flt-margin", "Tab")
    page.wait_for_function("document.getElementById('flt-msg').textContent.includes('Marge')", timeout=5000)
    assert app.state.bot.fleets.margin() == 9
    page.click(f"#flt-recalls li[data-id='{recall['id']}'] button")
    page.wait_for_function("document.querySelectorAll('#flt-recalls li[data-id]').length === 0")
    assert errors == []


def test_slots_panel_saves_the_places_kept_free_and_the_priority(ui):
    page, errors, app = ui
    page.goto(page.base + "/reglages#slots")
    page.wait_for_function("document.querySelectorAll('#slots-order li[data-feature]').length === 4", timeout=10000)
    assert page.locator("#slots-order li[data-feature]").evaluate_all("l => l.map(x => x.dataset.feature)") == ["excavations", "explorations", "minings", "hackings"]
    assert "désactivée : ne réserve rien" in page.text_content("#slots-order")
    page.fill("#slots-reserve", "2")
    page.wait_for_function("document.getElementById('slots-msg').textContent === 'Enregistré.'", timeout=5000)
    page.click("button[aria-label='Plus prioritaire : Piratage']")
    page.wait_for_function("document.querySelector('#slots-order li[data-feature]:nth-child(3)').dataset.feature === 'hackings'", timeout=5000)
    assert app.state.bot.slots.get() == {"reserve": 2, "order": ["excavations", "explorations", "hackings", "minings"]}
    assert "2 place(s) libre(s)" in page.text_content("#slots-summary")
    assert errors == []


def test_settings_page_has_eighteen_tabs_and_only_one_panel_shows_at_a_time(ui):
    page, errors, _ = ui
    page.goto(page.base + "/reglages")
    # ordre du menu latéral : Bot, Économie, Opérations, Alliance
    ids = ["account", "features", "planning", "discord", "constructions", "researches", "populations", "ships", "components", "slots", "explorations", "excavations", "hackings", "minings", "pillars", "fleets", "spying", "alliance"]
    assert page.locator("[role=tab]").count() == 18
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
    # minuteur en heures + minutes : 2 h à 4 h 30 min, enregistré en minutes par le bot
    head.get_by_label("Minuteur minimum, heures, de Dons de jetons d'alliance (page)").fill("2")
    head.get_by_label("Minuteur maximum, heures, de Dons de jetons d'alliance (page)").fill("4")
    head.get_by_label("Minuteur maximum, minutes, de Dons de jetons d'alliance (page)").fill("30")
    page.wait_for_function("document.querySelector('#feat-msg').textContent.includes('Enregistré')", timeout=5000)
    head.get_by_label("Activer Dons de jetons d'alliance (page)").check()
    page.wait_for_selector("[data-dot=alliance].on", timeout=5000)  # pastille verte dans le menu
    saved = app.state.bot.scheduler.store.get("alliance_tokens")
    assert (saved["enabled"], saved["min_minutes"], saved["max_minutes"]) == (True, 120.0, 270.0)
    # relu : 270 minutes s'affichent « 4 h 30 min », dans la liste comme dans l'en-tête
    card = page.locator("[data-feature=alliance_tokens]")
    assert card.get_by_label("Minuteur maximum, heures, de Dons de jetons d'alliance").input_value() == "4"
    assert card.get_by_label("Minuteur maximum, minutes, de Dons de jetons d'alliance").input_value() == "30"
    page.click("#tab-features")
    assert page.locator("[data-feature=alliance_tokens] input[type=checkbox]").is_checked()  # même réglage dans la liste
    assert page.locator("[data-feature=test]").count() == 0  # « Test » est masquée
    assert errors == []


def test_excavation_panel_sets_departure_planets_and_the_duration_and_team_of_each_excavation(ui):
    page, errors, app = ui
    egide, concorde = "6-79-Egide", "8-50-Concorde"
    page.click("#scan-run")  # scan des planètes (page Compte et univers), comme les autres onglets
    page.wait_for_function("document.querySelectorAll('#scan-list tr').length === 4", timeout=10000)
    page.goto(page.base + "/reglages#excavations")
    page.reload()  # même page (seul l'onglet change) : on la recharge pour relire les planètes scannées
    page.wait_for_function("document.querySelectorAll('#exc-planets .planet-grid input').length === 3", timeout=10000)
    # envoi commun : équipe et durée
    page.fill("#exc-units input[aria-label='Quantité Scientifique']", "3000")
    page.fill("#exc-units input[aria-label='Quantité Mineur']", "2000")
    assert "Équipe : 5000 / 5000" in page.text_content("#exc-units")
    page.select_option("#exc-duration", "12")
    # planètes choisies, chacune avec ses Fouilles simultanées ; Mira avec la Fouille 1 d'Egide
    page.check("#exc-planets input[value='some']")
    page.check(f"#exc-planets input[aria-label^='{egide}']")
    page.check(f"#exc-planets input[aria-label^='{concorde}']")
    page.fill(f"input[aria-label='Fouilles simultanées : {egide}']", "2")
    page.dispatch_event(f"input[aria-label='Fouilles simultanées : {egide}']", "change")
    page.select_option("#exc-mira", egide)
    page.click("#exc-save")  # délai pas encore choisi : refusé avec un message en français
    page.wait_for_function("document.getElementById('exc-msg').classList.contains('error')")
    assert "Délai" in page.text_content("#exc-msg")
    page.fill("#exc-delay-min", "5")
    page.fill("#exc-delay-max", "15")
    page.click("#exc-save")
    page.wait_for_function("document.getElementById('exc-msg').textContent === 'Enregistré.'")
    s = app.state.bot.excavations.get()
    assert (s["duration"], s["units"], s["mira"], s["per_slot"], s["delay_min"], s["delay_max"]) == (12, {"Scientifique": 3000, "Mineur": 2000}, egide, False, 5, 15)
    assert [(p["name"], p["copies"]) for p in s["planets"]] == [(egide, 2), (concorde, 1)]
    # vérification de secours : 30 min par défaut, délai modifiable, case décochée = désactivée, délai gardé en recochant
    assert s["recheck_min"] == 30 and page.is_checked("#exc-recheck-on") and page.input_value("#exc-recheck-min") == "30"
    page.fill("#exc-recheck-min", "90")
    page.uncheck("#exc-recheck-on")
    assert page.is_disabled("#exc-recheck-min") and "Contrôle de secours désactivé" in page.text_content("#exc-summary")
    page.click("#exc-save")
    page.wait_for_function("document.getElementById('exc-msg').textContent === 'Enregistré.'")
    assert app.state.bot.excavations.get()["recheck_min"] == 0
    assert app.state.bot.excavations.get()["recheck_kept_min"] == 90  # mémorisé avec les réglages
    page.reload()  # le délai choisi survit au rechargement de la page
    page.click("#tab-excavations")
    page.wait_for_selector("#exc-recheck-min", state="visible")
    assert not page.is_checked("#exc-recheck-on") and page.is_disabled("#exc-recheck-min") and page.input_value("#exc-recheck-min") == "90"
    page.check("#exc-recheck-on")
    assert page.input_value("#exc-recheck-min") == "90" and "toutes les 90 min" in page.text_content("#exc-summary")
    page.fill("#exc-recheck-min", "0")  # délai illisible avec la case cochée : refusé, jamais remplacé en silence
    page.click("#exc-save")
    page.wait_for_function("document.getElementById('exc-msg').classList.contains('error')")
    assert "Vérification de secours" in page.text_content("#exc-msg") and app.state.bot.excavations.get()["recheck_min"] == 0
    page.fill("#exc-recheck-min", "90")
    page.click("#exc-save")
    page.wait_for_function("document.getElementById('exc-msg').textContent === 'Enregistré.'")
    assert app.state.bot.excavations.get()["recheck_min"] == 90
    # réglages différents par Fouille : chaque Fouille part de l'envoi commun, puis se modifie
    page.check("#exc-per-slot")
    where = f"{egide}, Fouille 2"
    assert page.input_value(f"select[aria-label='Durée : {where}']") == "12"
    page.select_option(f"select[aria-label='Durée : {where}']", "24")
    page.fill(f"input[aria-label='Quantité Scientifique : {where}']", "")
    page.fill(f"input[aria-label='Quantité Mineur : {where}']", "1000")
    assert "Fouille 2 : 24 h, 1000 unités" in page.text_content("#exc-summary")
    page.click("#exc-save")
    page.wait_for_function("document.getElementById('exc-msg').textContent === 'Enregistré.'")
    s = app.state.bot.excavations.get()
    assert s["per_slot"] is True
    assert s["planets"][0]["fouilles"] == [{"duration": 12, "units": {"Scientifique": 3000, "Mineur": 2000}}, {"duration": 24, "units": {"Mineur": 1000}}]
    assert errors == []


def _drag(page, scope, start, end):
    """Cliquer-glisser sur la grille de la semaine, de la case (jour, quart d'heure) `start` à `end`."""
    boxes = [page.locator(f"{scope} .wg-cell[data-d='{d}'][data-i='{i}']").bounding_box() for d, i in (start, end)]
    page.mouse.move(boxes[0]["x"] + boxes[0]["width"] / 2, boxes[0]["y"] + boxes[0]["height"] / 2)
    page.mouse.down()
    page.mouse.move(boxes[1]["x"] + boxes[1]["width"] / 2, boxes[1]["y"] + boxes[1]["height"] / 2, steps=8)
    page.mouse.up()


def test_planning_panel_paints_and_types_slots_and_saves_the_random_offset(ui):
    page, errors, app = ui
    page.goto(page.base + "/reglages#planning")
    page.wait_for_selector("#plan-days input[aria-label='Planning, lundi']")
    page.check("#plan-enabled")
    page.fill("#plan-days input[aria-label='Planning, lundi']", "7:30-11h45")  # au clavier : minutes exactes gardées
    page.press("#plan-days input[aria-label='Planning, lundi']", "Tab")
    page.fill("#plan-days input[aria-label='Planning, jeudi']", "22-2")  # passe minuit : une seule plage
    page.press("#plan-days input[aria-label='Planning, jeudi']", "Tab")
    _drag(page, "#plan-days", (1, 32), (2, 47))  # mardi 08:00 → mercredi 11:45 (dernière case) : 2 jours d'un geste
    assert page.input_value("#plan-days input[aria-label='Planning, mardi']") == "08:00-12:00"
    page.fill("#plan-offset-min", "2")
    page.fill("#plan-offset-max", "9")
    page.click("#plan-save")
    page.wait_for_function("document.getElementById('plan-msg').textContent === 'Enregistré.'")
    saved = app.state.bot.planning.get()
    assert saved["enabled"] is True and saved["offset_min"] == 2 and saved["offset_max"] == 9
    assert saved["days"]["mon"] == [{"start": "07:30", "end": "11:45"}]
    assert saved["days"]["tue"] == saved["days"]["wed"] == [{"start": "08:00", "end": "12:00"}]
    assert saved["days"]["thu"] == [{"start": "22:00", "end": "02:00"}] and saved["days"]["fri"] == []
    _drag(page, "#plan-days", (1, 40), (1, 40))  # un clic sur une case allumée l'éteint : mardi 10:00-10:15
    assert page.input_value("#plan-days input[aria-label='Planning, mardi']") == "08:00-10:00, 10:15-12:00"
    page.fill("#plan-days input[aria-label='Planning, vendredi']", "8 à midi")
    page.press("#plan-days input[aria-label='Planning, vendredi']", "Tab")
    page.click("#plan-save")
    page.wait_for_function("document.getElementById('plan-msg').textContent.startsWith('À corriger')")
    assert "non compris" in page.text_content("#plan-msg")
    assert page.text_content("#plan-status").startswith(("Dans une plage", "Hors plage"))
    assert errors == []


def test_week_grid_keeps_midnight_slots_whole(ui):
    page, errors, _ = ui
    page.goto(page.base + "/reglages#planning")
    page.wait_for_selector("#plan-days .wg-cell")
    roundtrip = "(days) => window.weekGrid.fromCells(window.weekGrid.toCells(days))"
    assert page.evaluate(roundtrip, {"tue": [{"start": "22:00", "end": "02:00"}]})["tue"] == [{"start": "22:00", "end": "02:00"}]
    sunday = page.evaluate(roundtrip, {"sun": [{"start": "23:00", "end": "01:30"}]})  # dimanche → lundi
    assert sunday["sun"] == [{"start": "23:00", "end": "01:30"}] and sunday["mon"] == []
    full = page.evaluate(roundtrip, {"wed": [{"start": "00:00", "end": "12:00"}, {"start": "12:00", "end": "00:00"}]})
    assert full["wed"] == [{"start": "00:00", "end": "12:00"}, {"start": "12:00", "end": "00:00"}]  # journée entière
    long_run = {"mon": [{"start": "20:00", "end": "00:00"}], "tue": [{"start": "00:00", "end": "12:00"}, {"start": "12:00", "end": "00:00"}],
                "wed": [{"start": "00:00", "end": "03:00"}]}
    split = page.evaluate(roundtrip, long_run)  # plus de 24 h d'affilée : coupée à minuit
    assert split["mon"] == long_run["mon"] and split["tue"] == long_run["tue"] and split["wed"] == long_run["wed"]
    assert page.evaluate("() => window.weekGrid.parse('8-10; 16h30-20, 24-1')") == [
        {"start": "00:00", "end": "01:00"}, {"start": "08:00", "end": "10:00"}, {"start": "16:30", "end": "20:00"}]
    assert errors == []


def test_hacking_hours_save_inside_the_bot_planning_and_refuse_outside(ui):
    page, errors, app = ui
    bot = app.state.bot
    bot.planning.replace({"enabled": True, "days": {"mon": [{"start": "08:00", "end": "22:00"}]}, "offset_min": 0, "offset_max": 0})
    page.goto(page.base + "/reglages#hackings")
    page.reload()  # la page était déjà chargée par la fixture : relue avec le planning du bot ci-dessus
    box = "#panel-hackings [data-feature-hours=hackings]"
    page.wait_for_selector(f"{box} input[aria-label='Limiter le piratage à des horaires']")
    page.check(f"{box} input[aria-label='Limiter le piratage à des horaires']")
    grey = f"{box} .wg-cell.blocked"
    assert page.locator(f"{box} .wg-cell[data-d='0'][data-i='90']").get_attribute("class").count("blocked") == 1  # lundi 22:30
    assert "blocked" not in page.locator(f"{box} .wg-cell[data-d='0'][data-i='40']").get_attribute("class")  # lundi 10:00
    assert page.locator(grey).count() == 96 * 7 - 56  # seul le lundi 08:00-22:00 (56 quarts d'heure) n'est pas grisé
    field = f"{box} input[aria-label='Horaires du piratage, lundi']"
    page.fill(field, "8-10, 16-20")
    page.press(field, "Tab")
    _drag(page, box, (0, 84), (0, 92))  # lundi 21:00 → 23:00 : seules les cases du planning du bot s'allument
    assert page.input_value(field) == "08:00-10:00, 16:00-20:00, 21:00-22:00"
    page.click(f"{box} button:has-text('Enregistrer les horaires')")
    page.wait_for_function(f"document.querySelector(\"{box} .msg\").textContent === 'Enregistré.'", timeout=5000)
    assert bot.feature_hours.get("hackings")["days"]["mon"] == [
        {"start": "08:00", "end": "10:00"}, {"start": "16:00", "end": "20:00"}, {"start": "21:00", "end": "22:00"}]
    page.fill(field, "8-10, 16-23")  # au clavier, on peut sortir du planning : le serveur refuse
    page.press(field, "Tab")
    page.click(f"{box} button:has-text('Enregistrer les horaires')")
    page.wait_for_function(f"document.querySelector(\"{box} .msg\").textContent.includes('sort du planning du bot')", timeout=5000)
    assert bot.feature_hours.get("hackings")["days"]["mon"][1]["end"] == "20:00"  # refusé : rien n'a changé
    assert "on" in page.locator(f"{box} .wg-cell[data-d='0'][data-i='90']").get_attribute("class")  # 22:30 en rouge (allumé + grisé)
    assert page.locator("#panel-explorations [data-feature-hours=explorations]").count() == 1
    assert errors == []


def test_spying_panel_saves_the_minimums_and_refuses_a_launch_without_planet(ui):
    page, errors, app = ui
    page.goto(page.base + "/reglages#spying")
    page.wait_for_selector("#spy-minimums input[aria-label='Minimum Fer']")
    page.fill("#spy-minimums input[aria-label='Minimum Fer']", "1000000")
    page.fill("#spy-minimums input[aria-label='Minimum Éther']", "250000")
    page.select_option("#spy-required", "2")
    assert page.is_checked("#spy-factions input[aria-label='Espionner les Veyrs']")  # toutes cochées par défaut
    page.uncheck("#spy-factions input[aria-label='Espionner les Veyrs']")
    page.click("#spy-save")
    page.wait_for_function("document.getElementById('spy-msg').textContent === 'Enregistré.'")
    assert app.state.bot.spying.get() == {"planet": "", "minimums": {"Fer": 1000000, "Cristal": 0, "Éther": 250000}, "required": 2,
                                          "factions": ["noctes", "haldens"]}
    for label in ("Noctes", "Haldens"):
        page.uncheck(f"#spy-factions input[aria-label='Espionner les {label}']")
    page.click("#spy-save")  # aucune faction : refusé
    page.wait_for_function("document.getElementById('spy-msg').textContent.includes('au moins une faction')")
    assert app.state.bot.spying.get()["factions"] == ["noctes", "haldens"]
    page.check("#spy-factions input[aria-label='Espionner les Noctes']")
    page.click("#spy-start")  # aucune planète choisie : refusé avec un message en français
    page.wait_for_function("document.getElementById('spy-msg').textContent.includes('planète')")
    assert "error" in page.get_attribute("#spy-msg", "class")
    assert page.text_content("#spy-status") == "Aucun espionnage lancé." and page.text_content("#spy-closed-title") == "Piliers fermés (0)"
    assert errors == []


def test_exploration_panel_sets_departure_planets_simultaneous_expeditions_and_a_send_per_planet(ui):
    page, errors, _ = ui
    page.click("#scan-run")
    page.wait_for_function("document.querySelectorAll('#scan-list tr').length === 4", timeout=10000)
    page.goto(page.base + "/reglages#explorations")
    page.wait_for_function("document.querySelectorAll('#exp-planets .planet-grid input').length === 3", timeout=10000)
    saved = lambda: page.evaluate("fetch('/api/explorations').then((r) => r.json()).then((d) => d.settings)")  # noqa: E731
    # toutes les planètes : un seul nombre d'expéditions simultanées, pas de réglages par planète
    assert page.is_visible("#exp-copies-label") and page.is_disabled("#exp-per-planet")
    page.select_option("#exp-composition", "manual")
    page.fill("#exp-units input[aria-label='Quantité Rempart de Fer']", "1500")
    page.fill("#exp-copies", "3")
    page.select_option("#exp-duration", "4")
    assert "3 expéditions simultanées depuis chaque planète (4 h, par les Piliers, 1500 × Rempart de Fer)" in page.text_content("#exp-summary")
    page.click("#exp-save")
    page.wait_for_function("document.getElementById('exp-msg').textContent === 'Enregistré.'")
    s = saved()
    assert (s["composition"], s["units"], s["copies"], s["duration"], s["planets"], s["per_planet"]) == ("manual", {"Rempart de Fer": 1500}, 3, 4, [], False)
    # planètes choisies : un nombre par planète cochée
    page.check("#exp-planets input[value='some']")
    page.click("#exp-save")  # aucune planète cochée : refusé
    assert "Cochez au moins une planète" in page.text_content("#exp-msg")
    page.check("#exp-planets input[aria-label^='8-50-Concorde']")
    page.check("#exp-planets input[aria-label^='6-79-Egide']")
    assert page.is_hidden("#exp-copies-label") and page.is_enabled("#exp-per-planet")
    page.fill("input[aria-label='Expéditions simultanées : 8-50-Concorde']", "7")
    assert "7 expéditions depuis 8-50-Concorde" in page.text_content("#exp-summary")
    # réglages différents par planète : chaque planète part de l'envoi commun, puis se modifie
    page.check("#exp-per-planet")
    assert page.input_value("select[aria-label='Durée de l\\'expédition : 6-79-Egide']") == "4"
    page.select_option("select[aria-label='Durée de l\\'expédition : 6-79-Egide']", "2")
    page.click("#exp-save")
    page.wait_for_function("document.getElementById('exp-msg').textContent === 'Enregistré.'")
    s = saved()
    planets = {p["name"]: p for p in s["planets"]}
    assert s["per_planet"] is True and planets["8-50-Concorde"]["copies"] == 7 and planets["6-79-Egide"]["copies"] == 1
    assert planets["6-79-Egide"]["send"]["duration"] == 2 and planets["8-50-Concorde"]["send"]["duration"] == 4
    # option décochée : l'envoi commun vaut pour toutes, les réglages par planète restent en mémoire
    page.uncheck("#exp-per-planet")
    assert page.locator("#exp-planet-rows .exp-send").count() == 0
    page.click("#exp-save")
    page.wait_for_function("document.getElementById('exp-msg').textContent === 'Enregistré.'")
    s = saved()
    assert s["per_planet"] is False and {p["name"]: p["send"]["duration"] for p in s["planets"]}["6-79-Egide"] == 2
    page.check("#exp-per-planet")
    assert page.input_value("select[aria-label='Durée de l\\'expédition : 6-79-Egide']") == "2"  # retrouvé
    page.uncheck("#exp-per-planet")  # l'envoi commun sert de nouveau : il doit être complet
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
    page.select_option("#shp-add-mode", "target")
    assert page.is_disabled("#shp-add-repeat input[value='1']") and page.is_disabled("#shp-add-repeat input[value='0']")
    page.fill("#shp-add-amount", "60")
    page.wait_for_function("document.querySelectorAll('#shp-add-planets .planet-grid input').length === 3", timeout=10000)
    page.check("#shp-add-planets input[value='some']")
    page.check("#shp-add-planets input[aria-label^='6-79-Egide']")
    page.check("#shp-add-planets input[aria-label^='9-169-Strate']")
    page.click("#shp-add")
    page.select_option("#shp-add-item", "Plasmatique")
    page.select_option("#shp-add-mode", "max")
    page.check("#shp-add-repeat input[value='0']")  # MAX une seule fois
    assert page.is_checked("#shp-add-planets input[aria-label^='9-169-Strate']")  # le formulaire garde le dernier choix de planètes
    page.check("#shp-add-planets input[value='all']")  # on revient à « toutes »
    assert page.locator("#shp-add-amount-label").is_hidden()  # MAX : pas de quantité
    page.click("#shp-add")
    page.wait_for_function("document.querySelector('#shp-msg').textContent.includes('Enregistré')", timeout=5000)
    assert app.state.bot.ships.get()["queue"] == [
        {"item": "Anode", "mode": "target", "amount": 60, "planets": ["6-79-Egide", "9-169-Strate"], "repeat": False},
        {"item": "Plasmatique", "mode": "max", "amount": 0, "planets": [], "repeat": False},
    ]
    page.select_option("select[aria-label='Mode de Plasmatique']", "fixed")
    page.check("input[aria-label='À chaque passage : Plasmatique']")
    page.fill("input[aria-label='Quantité de Plasmatique']", "3")
    page.click("button[aria-label='Monter : Plasmatique']")
    page.wait_for_function("document.querySelector('#shp-msg').textContent.includes('Enregistré')", timeout=5000)
    assert [(ln["item"], ln["mode"], ln["amount"], ln["repeat"]) for ln in app.state.bot.ships.get()["queue"]] == [("Plasmatique", "fixed", 3, True), ("Anode", "target", 60, False)]
    assert errors == []


def test_planet_choice_sorts_naturally_on_two_rows_and_checks_all(ui):
    page, errors, _ = ui
    page.goto(page.base + "/reglages#populations")
    page.wait_for_function("typeof window.planetChoice === 'function'")
    result = page.evaluate("""() => {
        const names = ['10-84-Silence', '2-69-Mnesis', 'Aurore-Bis', '1-146-Elyria', '12-13-Zenith'];
        const choice = window.planetChoice({ planets: names, selected: ['2-69-Mnesis'], name: 'essai' });
        document.body.append(choice.node);
        const grid = choice.node.querySelector('.planet-grid');
        const order = [...grid.querySelectorAll('label')].map((l) => l.textContent.trim());
        const before = choice.value();
        choice.node.querySelector("button[aria-label='Tout cocher : essai']").click();
        const all = choice.value();
        choice.node.querySelector("button[aria-label='Tout décocher : essai']").click();
        const none = choice.value();
        choice.node.querySelector("input[value='all']").click();
        const disabled = [...grid.querySelectorAll('input')].every((b) => b.disabled);
        return { order, cols: grid.style.getPropertyValue('--cols'), before, all, none, everything: choice.value(), disabled };
    }""")
    assert result["order"] == ["1-146-Elyria", "2-69-Mnesis", "10-84-Silence", "12-13-Zenith", "Aurore-Bis"]  # 1, 2, 10, 12, puis les noms
    assert result["cols"] == "3"  # 5 planètes sur 2 lignes : 3 + 2
    assert result["before"] == ["2-69-Mnesis"]
    assert result["all"] == result["order"]  # « Tout cocher » : la liste explicite des planètes connues
    assert result["none"] is None  # « Planètes choisies » sans case cochée : refusé par la page
    assert result["everything"] == [] and result["disabled"]  # « Toutes les planètes » : cases grisées
    assert errors == []


def test_a_line_removed_by_the_bot_disappears_from_the_page(ui):
    page, errors, app = ui
    page.click("#scan-run")
    page.wait_for_function("document.querySelectorAll('#scan-list tr').length === 4", timeout=10000)
    page.goto(page.base + "/reglages#components")
    assert page.locator("#cmp-add, #cmp-add-item, #cmp-add-mode, #cmp-add-amount").count() == 0  # plus de liste déroulante : on ne passe que par le tableau
    page.wait_for_function("document.querySelectorAll('#cmp-inv td[data-planet]').length > 0", timeout=5000)
    page.fill("#cmp-tool-amount", "150")  # mode par défaut : nombre à atteindre
    cell = "#cmp-inv tr[data-item='canon-laser'] td[data-planet='6-79-Egide']"
    assert page.text_content(cell + " .lv") == "—"  # pas encore relevé
    page.click("#cmp-inv-refresh")
    page.wait_for_function("document.querySelector('#cmp-inv tr[data-item=\"canon-laser\"] td[data-planet] .lv').textContent !== '—'", timeout=30000)
    assert page.text_content(cell + " .lv") == "100"  # effectif lu dans l'onglet Composants du Chantier spatial
    assert page.locator("#cmp-inv button[aria-expanded]").evaluate_all("b => b.map(x => x.textContent.trim().split(' ')[1])") == ["Infrastructures", "Hangars", "Armement", "Blindages", "Boucliers", "Propulsion"]
    assert page.locator("#cmp-inv tr[data-item]").count() == 37
    assert page.locator("#cmp-inv").evaluate("e => getComputedStyle(e).maxHeight === 'none' && e.scrollHeight <= e.clientHeight + 1")
    page.fill("#cmp-tool-amount", "")  # champ vidé : les boutons se grisent au lieu de garder l'ancien nombre
    assert page.locator("#cmp-inv button.add-level:not([disabled])").count() == 0
    page.fill("#cmp-tool-amount", "150")
    page.click(cell + " button.add-level")
    page.wait_for_function("document.querySelector('#cmp-msg').textContent.includes('Enregistré')", timeout=5000)
    assert app.state.bot.components.get()["queue"] == [{"item": "canon-laser", "mode": "target", "amount": 150, "planets": ["6-79-Egide"], "repeat": False}]
    assert page.locator("#cmp-queue li[data-item='canon-laser']").count() == 1
    assert page.text_content(cell + " .lv") == "100 → 150"
    app.state.bot.components.replace({"queue": []})  # le bot retire la ligne atteinte
    page.wait_for_function("document.querySelectorAll('#cmp-queue li.empty-state').length === 1", timeout=8000)
    assert errors == []


def test_discord_panel_saves_without_ever_showing_the_token(ui):
    """Onglet Discord : le jeton se saisit mais ne se relit pas ; « Oublier le jeton » le retire."""
    page, errors, _ = ui
    token = "T" * 24 + "." + "u" * 6 + "." + "v" * 27
    page.goto(page.base + "/reglages#discord")
    page.fill("#dc-token", token)
    assert page.locator("#dc-owner, #dc-guild, #dc-channel").count() == 0  # le jeton suffit
    page.click("#dc-save")
    page.wait_for_function("document.querySelector('#dc-msg').textContent.includes('Enregistré')", timeout=5000)
    assert page.input_value("#dc-token") == "" and "vvvv" in page.get_attribute("#dc-token", "placeholder")
    assert token not in page.content() and page.is_enabled("#dc-forget")
    page.click("#dc-forget")
    page.wait_for_function("document.querySelector('#dc-forget').disabled", timeout=5000)
    assert not errors
