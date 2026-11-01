"""Planificateur : plages horaires, cycles à intervalle aléatoire, constructions et recherches (à blanc ou réel)."""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .. import human
from . import catalog as catalog_module
from .decide import decide
from .driver import GameDriver, GameUiError
from .numbers import norm
from .schedule import is_active, local_now, next_change, windows_from_settings
from .settings import SettingsStore, normalized_targets
from .state import Card, PlanetInfo


class Interrupted(Exception):
    """Le cycle s'arrête proprement (arrêt demandé, contrôle manuel, fin de plage, session fermée)."""


class AutomationRunner:
    def __init__(self, bot, store: SettingsStore, data_dir: str | None) -> None:
        self.bot = bot
        self.store = store
        self.driver = GameDriver(bot, self.guard)
        self.idle = asyncio.Event()
        self.idle.set()
        self.cycle_running = False
        self._task: asyncio.Task | None = None
        self._running = False
        self._busy_until: dict[str, float] = {}
        self._counter = {"date": "", "count": 0}
        self._unconfirmed_streak = 0
        self._next_run_at: float | None = None
        self._last_notes: dict[str, str] = {}
        self._window_active: bool | None = None
        self._window_cache: tuple | None = None
        self._levels: dict[str, dict[str, int]] = {}  # nom normalisé → planète → niveau lu
        # Superviseur de connexion
        self.waiting = ""  # raison de l'attente affichée dans l'interface
        self._login_failures = 0
        self._login_blocked = ""  # raison si les tentatives automatiques sont suspendues
        self._next_login_at = 0.0
        self._unknown_since: float | None = None
        self._universe_attempts = 0
        self.last_cycle = ""
        self._state_path = Path(data_dir) / "automation-state.json" if data_dir and os.path.isdir(data_dir) else None
        self._load_state()

    # ------------------------------------------------------------ persistance
    def _load_state(self) -> None:
        if not self._state_path or not self._state_path.exists():
            return
        try:
            saved = json.loads(self._state_path.read_text(encoding="utf8"))
            self._counter = {"date": str(saved["counter"]["date"]), "count": int(saved["counter"]["count"])}
            now = time.time()
            self._busy_until = {k: float(v) for k, v in saved.get("busy", {}).items() if float(v) > now}
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _save_state(self) -> None:
        if not self._state_path:
            return
        try:
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"counter": self._counter, "busy": self._busy_until}), encoding="utf8")
            os.replace(tmp, self._state_path)
        except OSError:
            pass

    # ------------------------------------------------------------------ état
    def _cfg(self) -> dict:
        return self.store.get()

    def _today(self, cfg: dict) -> str:
        return local_now(cfg["schedule"]["timezone"]).strftime("%Y-%m-%d")

    def launched_today(self, cfg: dict | None = None) -> int:
        cfg = cfg or self._cfg()
        return self._counter["count"] if self._counter["date"] == self._today(cfg) else 0

    def in_window(self, cfg: dict | None = None) -> tuple[bool, str]:
        """(actif, texte). Le calcul de la prochaine bascule est coûteux : il est mis en cache à la minute."""
        cfg = cfg or self._cfg()
        sched = cfg["schedule"]
        if not sched["enabled"]:
            return True, "sans restriction horaire"
        now = local_now(sched["timezone"])
        key = (now.strftime("%Y%m%d%H%M"), json.dumps(sched, sort_keys=True))
        if self._window_cache and self._window_cache[0] == key:
            return self._window_cache[1]
        windows = windows_from_settings(sched["windows"])
        active = is_active(now, windows)
        _, change = next_change(now, windows) if windows else (False, None)
        if active:
            result = (True, f"jusqu'à {change:%H:%M}" if change else "dans la plage")
        else:
            result = (False, f"reprise {change:%a %H:%M}" if change else "aucune plage définie")
        self._window_cache = (key, result)
        return result

    def summary(self) -> dict:
        cfg = self._cfg()
        active, text = self.in_window(cfg)
        return {
            "running": self._running,
            "cycle_running": self.cycle_running,
            "mode": cfg["mode"],
            "in_window": active,
            "window": text,
            "launched_today": self.launched_today(cfg),
            "cap": cfg["daily_cap"],
            "next_run_in_s": max(0, round(self._next_run_at - time.monotonic())) if self._next_run_at else None,
            "last_cycle": self.last_cycle,
            "persistent": self.store.persistent,
            "waiting": self.waiting if self._running else "",
            "has_credentials": self.bot.has_credentials(),
            "universe": cfg["universe"],
        }

    # -------------------------------------------------------------- contrôle
    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._unconfirmed_streak = 0
        self._login_failures = 0
        self._login_blocked = ""
        self._next_login_at = 0.0
        self.waiting = ""
        self._last_notes.clear()
        self.bot.log(f"Bot activé (ON) — mode {'RÉEL' if self._cfg()['mode'] == 'live' else 'à blanc'}.")
        await self.bot.ensure_started()  # le navigateur tourne même si aucun écran de l'interface n'est ouvert
        self._task = asyncio.create_task(self._loop())

    async def stop(self, reason: str = "demande de l'utilisateur") -> None:
        if not self._running and self._task is None:
            return
        self._running = False
        task, self._task = self._task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.cycle_running = False
        self.idle.set()
        self._next_run_at = None
        self.waiting = ""
        self.bot.log(f"Bot désactivé (OFF) — {reason}.", "warn")

    async def guard(self) -> None:
        """Point de contrôle appelé avant chaque action : interrompt le cycle si les conditions ne sont plus réunies."""
        if not self._running:
            raise Interrupted("arrêt demandé")
        b = self.bot
        if b._manual:
            raise Interrupted("contrôle manuel activé")
        if b._state.value != "logged_in" or b._page is None or b._page.is_closed():
            raise Interrupted("session fermée")
        if not self.in_window()[0]:
            raise Interrupted("fin de la plage horaire")
        await asyncio.sleep(0)

    def _preconditions(self) -> str | None:
        b = self.bot
        if b._manual:
            return "contrôle manuel activé"
        if b._state.value != "logged_in":
            return "pas connecté"
        if b._page is None or b._page.is_closed() or "/game/" not in b._page.url:
            return "entrez dans l'univers (page /game/…)"
        if b._lock.locked():
            return "une autre opération est en cours"
        return None

    # ------------------------------------------------------------------ boucle
    async def _sleep(self, seconds: float) -> None:
        self._next_run_at = time.monotonic() + seconds
        await asyncio.sleep(seconds)
        self._next_run_at = None

    def _note(self, key: str, message: str, level: str = "info") -> None:
        """Écrit dans le journal sauf si c'est le même message que la fois précédente pour cette clé."""
        if self._last_notes.get(key) != message:
            self._last_notes[key] = message
            self.bot.log(message, level)

    async def _loop(self) -> None:
        errors = 0
        while self._running:
            cfg = self._cfg()
            active, text = self.in_window(cfg)
            if self._window_active != active:
                self._window_active = active
                self.bot.log(f"Plage horaire : {'active' if active else 'inactive'} ({text}).")
            poll = self.bot._settings.poll_seconds
            if not active:
                await self._sleep(poll * 3)
                continue
            try:
                problem = await self._ensure_ready(cfg)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                problem = f"vérification de l'écran impossible ({type(exc).__name__})"
            self.waiting = problem or ""
            if problem:
                self._note("precondition", f"En attente : {problem}.")
                await self._sleep(poll)
                continue
            self._last_notes.pop("precondition", None)
            try:
                await self.run_cycle(cfg)
                errors = 0
            except Interrupted as exc:
                self.bot.log(f"Cycle interrompu : {exc}.", "warn")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                errors += 1
                self.bot.log(f"Erreur pendant le cycle ({type(exc).__name__}) : {str(exc)[:200]}", "error")
                if errors >= 3:
                    await self.stop("3 erreurs consécutives")
                    return
            lo, hi = cfg["cycle"]["min_minutes"], cfg["cycle"]["max_minutes"]
            await self._sleep(random.uniform(lo, hi) * 60)

    # ------------------------------------------------------------ superviseur de connexion
    async def _ensure_ready(self, cfg: dict) -> str | None:
        """Regarde où en est le site et ne fait que ce qui manque : connexion, choix de l'univers, ou rien.

        Retourne None quand le bot est dans une partie et peut agir, sinon la raison de l'attente.
        """
        b = self.bot
        if b._manual:
            return "contrôle manuel activé"
        if b._lock.locked():
            return "une autre opération est en cours"
        if b._page is None or b._page.is_closed():
            await b.ensure_started()
            return "démarrage du navigateur"
        screen = await b.detect_screen()
        if screen != "unknown":
            self._unknown_since = None
        if screen in ("game", "universes"):  # connecté (par le bot ou à la main) : les blocages de connexion tombent
            self._login_failures, self._login_blocked, self._next_login_at = 0, "", 0.0
        if screen == "game":
            self._universe_attempts = 0
            return None
        if screen == "universes":
            return await self._pick_universe(cfg)
        if screen == "login":
            return await self._log_in()
        # écran inconnu (chargement, redirection…) : on laisse un peu de temps puis on recharge la page de départ
        now = time.monotonic()
        self._unknown_since = self._unknown_since or now
        if now - self._unknown_since > 45:
            self._unknown_since = None
            b.log("Écran non reconnu depuis 45 s : retour à la page de départ.", "warn")
            await b._page.goto(b._settings.login_url, wait_until="domcontentloaded", timeout=30_000)
        return "écran du site non reconnu"

    async def _pick_universe(self, cfg: dict) -> str | None:
        b = self.bot
        universes = await b.list_universes()
        self.store.add_catalog("universes", [u["name"] for u in universes])
        wanted = cfg["universe"]
        if not wanted:
            return "choisissez un univers (onglet Compte, ou bouton d'univers sur la page Vision)"
        match = next((u for u in universes if norm(u["name"]) == norm(wanted)), None)
        if match is None:
            return f"univers « {wanted} » indisponible sur la page de choix"
        self._universe_attempts += 1
        if self._universe_attempts > 4:
            return "l'entrée dans l'univers n'aboutit pas : entrez-y à la main (contrôle manuel) ; le bot reprendra seul"
        b.log(f"Page de choix de l'univers : entrée dans « {match['name']} ».")
        await b.enter_universe(match["index"])
        return "entrée dans l'univers en cours"

    async def _log_in(self) -> str | None:
        b = self.bot
        if self._login_blocked:
            return self._login_blocked
        if not b.has_credentials():
            return "identifiants requis (onglet Compte)"
        wait = self._next_login_at - time.monotonic()
        if wait > 0:
            return f"nouvelle tentative de connexion dans {wait:.0f} s"
        identifier, password = b._creds  # type: ignore[misc]
        await b.login(identifier, password, supervised=True)
        if b._state.value == "logged_in":
            self._login_failures = 0
            return "connexion réussie, vérification de l'écran"
        kind = b.last_login_kind
        self._login_failures += 1
        if kind == "refused":  # identifiants refusés : ne pas réessayer (risque de blocage du compte)
            self._login_blocked = "connexion refusée par le site : corrigez les identifiants (onglet Compte), puis OFF puis ON"
            b.log("Reconnexion automatique suspendue : le site a refusé les identifiants.", "error")
        elif self._login_failures >= 3:
            self._login_blocked = "intervention requise (contrôle anti-bot ?) : connectez-vous à la main (page Connexion, contrôle manuel) ; le bot reprendra seul"
            b.log("Trois tentatives de connexion sans résultat : intervention manuelle requise.", "error")
        else:
            delay = (60, 300)[min(self._login_failures - 1, 1)]
            self._next_login_at = time.monotonic() + delay
            b.log(f"Connexion sans résultat : nouvelle tentative dans {delay} s.", "warn")
        return self._login_blocked or "connexion non aboutie"

    # ------------------------------------------------------------------- cycle
    async def run_cycle(self, cfg: dict | None = None) -> None:
        cfg = cfg or self._cfg()
        live = cfg["mode"] == "live"
        self.cycle_running = True
        self.idle.clear()
        started = time.monotonic()
        try:
            await self.guard()
            planets = await self.driver.list_planets()
            self.store.add_catalog("planets", [p.name for p in planets])
            self.bot.log(f"Cycle : {len(planets)} planète(s) lues ({'réel' if live else 'à blanc'}).")
            if cfg["research"]["enabled"]:
                await self._research_step(cfg, planets, live)
            if cfg["constructions"]["enabled"]:
                await self._constructions_step(cfg, planets, live)
            self.last_cycle = f"{datetime.now():%H:%M:%S} ({time.monotonic() - started:.0f} s)"
        finally:
            self.cycle_running = False
            self.idle.set()

    def _busy(self, key: str) -> bool:
        return self._busy_until.get(key, 0) > time.time()

    def _mark_busy(self, key: str, card: Card) -> None:
        self._busy_until[key] = time.time() + (card.duration_s if card.duration_s else 600) + 5
        self._save_state()

    def _count_launch(self, cfg: dict) -> None:
        today = self._today(cfg)
        if self._counter["date"] != today:
            self._counter = {"date": today, "count": 0}
        self._counter["count"] += 1
        self._save_state()

    def _catalog(self, kind: str, cards: list[Card], planet: str = "") -> None:
        self.store.add_catalog(kind, [c.name for c in cards])
        for card in cards:  # niveau actuel, par planète pour les bâtiments
            if card.level is not None:
                self._levels.setdefault(norm(card.name), {})[norm(planet) if kind == "buildings" else "-"] = card.level

    def catalog_view(self) -> dict:
        """Listes affichées par la page Actions : éléments intégrés + découverts, niveaux actuels, planètes connues."""
        cfg = self.store.get()
        levels = {name: (min(by.values()), max(by.values())) for name, by in self._levels.items() if by}
        view = {
            kind: catalog_module.entries(kind, cfg["catalog"][kind], levels) for kind in ("buildings", "research")
        }
        view["planets"] = list(cfg["catalog"]["planets"])
        view["universes"] = list(cfg["catalog"]["universes"])
        return view

    async def _decide_and_act(
        self, cfg: dict, live: bool, label: str, key: str, targets: dict[str, int], cards: list[tuple[str, Card]],
        resources, action: str, planet_label: str,
    ) -> bool:
        """Décide, journalise, et lance en mode réel. Retourne True si une action réelle a été lancée."""
        outcome = decide([c for _, c in cards], resources, targets, action)
        if outcome.decision is None:
            self._note(f"{label}:{key}", f"{planet_label} : rien à lancer ({outcome.summary()}).")
            return False
        decision = outcome.decision
        section = next(s for s, c in cards if c is decision.card)
        if not live:
            self._note(f"{label}:{key}", f"[À blanc] {planet_label} : je lancerais {decision.describe()}.")
            return False
        if self.launched_today(cfg) >= cfg["daily_cap"]:
            self._note("cap", f"Plafond quotidien atteint ({cfg['daily_cap']}) : aucune nouvelle action aujourd'hui.", "warn")
            return False
        self._last_notes.pop("cap", None)
        self.bot.log(f"{planet_label} : je lance {decision.describe()}.")
        result = await self.driver.launch(decision.card, section)
        if result.confirmed:
            self._unconfirmed_streak = 0
            self._count_launch(cfg)
            self._mark_busy(key, decision.card)
            self._last_notes.pop(f"{label}:{key}", None)
            self.bot.log(f"{planet_label} : lancement confirmé ({result.detail}) — {self.launched_today(cfg)}/{cfg['daily_cap']} aujourd'hui.")
            return True
        self._unconfirmed_streak += 1
        self.bot.log(f"{planet_label} : lancement non confirmé ({result.detail}).", "warn")
        if self._unconfirmed_streak >= 2:
            await self.stop("deux lancements consécutifs non confirmés")
            raise Interrupted("lancements non confirmés")
        return False

    async def _constructions_step(self, cfg: dict, planets: list[PlanetInfo], live: bool) -> None:
        cons = cfg["constructions"]
        base = normalized_targets(cons["targets"])
        overrides = {norm(p): normalized_targets(t) for p, t in cons["overrides"].items()}
        wanted = {norm(p) for p in cons["planets"]}
        order = [p for p in planets if not wanted or norm(p.name) in wanted]
        random.shuffle(order)
        for planet in order:
            await self.guard()
            targets = {**base, **overrides.get(norm(planet.name), {})}
            if not targets:
                continue
            key = norm(planet.name)
            label = f"Planète {planet.name}"
            if self._busy(key):
                until = datetime.fromtimestamp(self._busy_until[key])
                self._note(f"busy:{key}", f"{label} : construction en cours, nouvelle vérification après {until:%H:%M}.")
                continue
            self._last_notes.pop(f"busy:{key}", None)
            try:
                if not await self.driver.switch_planet(planet):
                    self.bot.log(f"{label} : changement de planète non confirmé, planète ignorée.", "warn")
                    continue
                cards: list[tuple[str, Card]] = []
                resources = None
                for section in ("buildings", "facilities"):
                    if not await self.driver.open_section(section):
                        self.bot.log(f"{label} : page « {section} » inaccessible.", "warn")
                        break
                    await human.think(self.bot._settings)
                    page_state = await self.driver.read_page()
                    resources = page_state.resources
                    self._catalog("buildings", page_state.cards, planet.name)
                    cards += [(section, c) for c in page_state.cards]
                    if set(targets) <= {norm(c.name) for _, c in cards}:
                        break  # tous les bâtiments visés ont été vus : inutile de charger l'autre page
                if resources is None:
                    continue
                await self._decide_and_act(cfg, live, "c", key, targets, cards, resources, "Améliorer", label)
            except GameUiError as exc:
                self.bot.log(f"{label} : {exc}.", "error")
                await self.stop("écran du jeu inattendu")
                raise Interrupted(str(exc)) from exc
            await human.think(self.bot._settings)

    async def _research_step(self, cfg: dict, planets: list[PlanetInfo], live: bool) -> None:
        res = cfg["research"]
        targets = normalized_targets(res["targets"])
        if not targets:
            return
        if self._busy("research"):
            until = datetime.fromtimestamp(self._busy_until["research"])
            self._note("busy:research", f"Recherches : une recherche est en cours, nouvelle vérification après {until:%H:%M}.")
            return
        self._last_notes.pop("busy:research", None)
        if res["funding_planet"]:
            funding = next((p for p in planets if norm(p.name) == norm(res["funding_planet"])), None)
            if funding is None:
                self._note("research:planet", f"Recherches : planète « {res['funding_planet']} » introuvable.", "warn")
                return
        else:  # automatique : la planète qui a le plus de Fer
            funding = max(planets, key=lambda p: p.stocks[0] or 0)
        label = f"Recherches (planète {funding.name})"
        try:
            await self.guard()
            if not await self.driver.switch_planet(funding):
                self.bot.log(f"{label} : changement de planète non confirmé.", "warn")
                return
            if not await self.driver.open_section("tech"):
                self.bot.log(f"{label} : page des recherches inaccessible.", "warn")
                return
            await human.think(self.bot._settings)
            page_state = await self.driver.read_page()
            self._catalog("research", page_state.cards)
            cards = [("tech", c) for c in page_state.cards]
            await self._decide_and_act(cfg, live, "r", "research", targets, cards, page_state.resources, "Rechercher", label)
        except GameUiError as exc:
            self.bot.log(f"{label} : {exc}.", "error")
            await self.stop("écran du jeu inattendu")
            raise Interrupted(str(exc)) from exc

    # ------------------------------------------------------------- diagnostic
    async def read_state(self) -> dict:
        """Lecture seule pour vérifier ce que le bot comprend de l'écran (planètes, ressources, cartes)."""
        if self.cycle_running:
            raise GameUiError("un cycle d'automatisation est en cours")
        problem = self._preconditions()
        if problem:
            raise GameUiError(problem)

        async def diag_guard() -> None:
            if self.bot._manual or self.bot._state.value != "logged_in":
                raise Interrupted("conditions non réunies")
            await asyncio.sleep(0)

        previous, self.driver.guard = self.driver.guard, diag_guard
        self.cycle_running = True
        self.idle.clear()
        try:
            planets = await self.driver.list_planets()
            self.store.add_catalog("planets", [p.name for p in planets])
            sections: dict[str, dict] = {}
            for section in ("buildings", "facilities", "tech"):
                if await self.driver.open_section(section):
                    await human.think(self.bot._settings)
                    state = await self.driver.read_page()
                    kind = "research" if section == "tech" else "buildings"
                    self._catalog(kind, state.cards, state.planet)
                    sections[section] = {"title": state.section, "planet": state.planet, "resources": asdict(state.resources), "cards": [asdict(c) for c in state.cards]}
            return {"planets": [asdict(p) for p in planets], "sections": sections}
        finally:
            self.driver.guard = previous
            self.cycle_running = False
            self.idle.set()
