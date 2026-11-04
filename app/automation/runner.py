"""Planificateur : plages horaires, cycles à intervalle aléatoire, constructions et recherches (à blanc ou réel)."""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from collections import deque
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .. import human
from . import catalog as catalog_module
from .decide import Outcome, decide, decide_ordered
from .driver import GameDriver, GameUiError
from .numbers import norm
from . import expedition as expedition_module
from . import production as production_module
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
        self.connection = ""  # état de la connexion automatique (indépendante de l'interrupteur ON/OFF)
        self._keeper: asyncio.Task | None = None
        self._ready_lock = asyncio.Lock()
        self._next_login_at = 0.0
        self._unknown_since: float | None = None
        self._universe_attempts = 0
        self._in_game = False
        self.simulated: deque[dict] = deque(maxlen=30)  # ce que le bot « aurait lancé » en simulation
        self.simulated_total = 0
        self._baseline: dict[str, int] = {}  # niveaux de départ des objectifs relatifs : « id|planète » → niveau
        # Constructions : état de chaque planète (en attente de ressources, occupée…) et prochaine vérification
        self._pstate: dict[str, dict] = {}
        self._cons_signature = ""
        # Production : état par bloc et par planète, commandes uniques déjà faites (« id|planète » → signature de la ligne)
        self._prodstate: dict[str, dict] = {}
        self._prod_signature = ""
        self._prod_done: dict[str, str] = {}
        self._known_planets: list[PlanetInfo] = []
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
            self._baseline = {str(k): int(v) for k, v in saved.get("baseline", {}).items()}
            self._prod_done = {str(k): str(v) for k, v in saved.get("prod_done", {}).items()}
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _save_state(self) -> None:
        if not self._state_path:
            return
        try:
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"counter": self._counter, "busy": self._busy_until, "baseline": self._baseline, "prod_done": self._prod_done}), encoding="utf8")
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
            "connection": self.connection if self.bot.has_credentials() else "",
            "has_credentials": self.bot.has_credentials(),
            "universe": cfg["universe"],
            "planets_state": self.planets_state(),
            "production_state": self.production_state(),
            "production_done": self.production_done(),
        }

    # ------------------------------------------------------------ tableau de bord
    def _record_simulated(self, planet_label: str, decision) -> None:
        entry = {
            "t": time.time(), "planet": planet_label.removeprefix("Planète "), "name": decision.card.name,
            "from": decision.card.level, "to": decision.card.level + 1, "target": decision.target,
        }
        last = self.simulated[-1] if self.simulated else None
        if last and (last["planet"], last["name"], last["from"]) == (entry["planet"], entry["name"], entry["from"]):
            last["t"] = entry["t"]  # même décision qu'au contrôle précédent : on ne la compte qu'une fois
            return
        self.simulated.append(entry)
        self.simulated_total += 1

    @staticmethod
    def _duration_text(seconds: float | None) -> str:
        if seconds is None:
            return ""
        return f"{seconds / 60:.0f} min" if seconds >= 90 else f"{seconds:.0f} s"

    def describe(self, cfg: dict | None = None) -> str:
        """Une phrase qui dit ce que fait le bot en ce moment."""
        cfg = cfg or self._cfg()
        if not self._running:
            link = f" {self.connection[0].upper()}{self.connection[1:]}." if self.bot.has_credentials() and self.connection else ""
            return f"Le bot est arrêté.{link} Cliquez sur ON pour qu'il travaille."
        active, window = self.in_window(cfg)
        if not active:
            return f"Le bot est en veille : hors de la plage horaire ({window})."
        if self.waiting:
            return f"Le bot attend : {self.waiting}."
        if self.cycle_running:
            return "Le bot est en train de vérifier vos planètes."
        cons, research, prod = cfg["constructions"], cfg["research"], cfg["production"]
        production_on = bool(self._production_blocks(cfg))
        if not ((cons["enabled"] and cons["queue"]) or (research["enabled"] and research["targets"]) or production_on):
            return "Le bot est prêt mais n'a rien à faire : activez les constructions, les recherches ou la production dans Réglages."
        rows = self.planets_state()
        if not (cons["enabled"] and cons["queue"]):
            return "Le bot surveille vos recherches et vos productions." if production_on else "Le bot surveille vos recherches."
        if not rows:
            return "Le bot n'a pas encore vérifié de planète."
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        labels = {
            "pending": "{n} en attente de ressources", "busy": "{n} en construction",
            "would_launch": "{n} où il lancerait une amélioration (simulation)", "done": "{n} avec tous les objectifs atteints",
            "idle": "{n} sans rien à lancer", "retry": "{n} à réessayer",
        }
        parts = [labels[k].format(n=n) for k, n in counts.items() if k in labels]
        upcoming = [r["next_in_s"] for r in rows if r["status"] in ("pending", "would_launch", "idle", "retry")]
        nxt = f" Prochaine vérification dans {self._duration_text(min(upcoming))}." if upcoming else ""
        return f"Le bot surveille {len(rows)} planète(s) : {', '.join(parts)}.{nxt}"

    def readiness(self, cfg: dict | None = None) -> dict:
        """Liste « Prêt à démarrer ? » : chaque étape, son état et où la corriger."""
        cfg = cfg or self._cfg()
        b = self.bot
        connected = b._state.value == "logged_in"
        in_game = b._page is not None and not b._page.is_closed() and "/game/" in b._page.url
        cons, research, prod = cfg["constructions"], cfg["research"], cfg["production"]
        work = []
        if cons["enabled"] and cons["queue"]:
            work.append(f"{len(cons['queue'])} amélioration(s) de bâtiment")
        if research["enabled"] and research["targets"]:
            work.append(f"{len(research['targets'])} recherche(s)")
        if prod["enabled"] and prod["queue"]:
            work.append(f"{len(prod['queue'])} ligne(s) de production")
        if cfg["expeditions"]["enabled"] and cfg["expeditions"]["queue"]:
            work.append(f"{len(cfg['expeditions']['queue'])} ligne(s) d'expédition")
        steps = [
            {"id": "account", "label": "Identifiants mémorisés", "required": True, "link": "/reglages#account",
             "ok": b.has_credentials() or connected,
             "detail": f"mémorisés ({b.credentials_hint()})" if b.has_credentials() else "déjà connecté" if connected else "à saisir : sans eux, le bot ne peut pas se connecter tout seul"},
            {"id": "universe", "label": "Univers choisi", "required": True, "link": "/reglages#account",
             "ok": bool(cfg["universe"]) or in_game,
             "detail": cfg["universe"] or ("déjà dans une partie" if in_game else "à choisir (il apparaît dans la liste dès que le bot a vu la page de choix)")},
            {"id": "work", "label": "Quelque chose à faire", "required": True, "link": "/reglages#constructions",
             "ok": bool(work), "detail": " et ".join(work) if work else "ajoutez des améliorations, des recherches ou de la production et activez-les"},
            {"id": "mode", "label": "Mode", "required": False, "link": "/", "ok": True,
             "detail": "RÉEL : le bot clique et dépense" if cfg["mode"] == "live" else "Simulation : le bot ne clique pas (recommandé pour commencer)"},
            {"id": "schedule", "label": "Plage horaire", "required": False, "link": "/reglages#schedule", "ok": True,
             "detail": self.in_window(cfg)[1] if cfg["schedule"]["enabled"] else "aucune restriction"},
        ]
        return {"ready": all(step["ok"] for step in steps if step["required"]), "steps": steps}

    def dashboard(self) -> dict:
        cfg = self._cfg()
        return {
            "sentence": self.describe(cfg),
            "readiness": self.readiness(cfg),
            "simulated": list(self.simulated)[-10:],
            "simulated_total": self.simulated_total,
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
        self._pstate.clear()
        self._last_notes.clear()
        self.simulated.clear()
        self.simulated_total = 0
        self.bot.log(f"Bot activé (ON) — mode {'RÉEL' if self._cfg()['mode'] == 'live' else 'simulation'}.", event=True)
        await self.bot.ensure_started()  # le navigateur tourne même si aucun écran de l'interface n'est ouvert
        self._task = asyncio.create_task(self._loop())

    async def stop(self, reason: str = "demande de l'utilisateur", *, automatic: bool = False) -> None:
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
        self.bot.log(f"Bot désactivé (OFF) — {reason}.", "warn", event=True, kind="attention" if automatic else "info")

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

    def _note(self, key: str, message: str, level: str = "info", event: bool = False, kind: str = "info") -> None:
        """Écrit dans le journal sauf si c'est le même message que la fois précédente pour cette clé."""
        if self._last_notes.get(key) != message:
            self._last_notes[key] = message
            self.bot.log(message, level, event=event, kind=kind)

    async def _loop(self) -> None:
        errors = 0
        while self._running:
            cfg = self._cfg()
            active, text = self.in_window(cfg)
            if self._window_active != active:
                self._window_active = active
                self.bot.log(f"Plage horaire : {'active' if active else 'inactive'} ({text}).", event=True)
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
                self._note("precondition", f"En attente : {problem}.", event=True)
                await self._sleep(poll)
                continue
            self._last_notes.pop("precondition", None)
            try:
                await self.run_cycle(cfg)
                errors = 0
            except Interrupted as exc:
                self.bot.log(f"Cycle interrompu : {exc}.", "warn", event=True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                errors += 1
                self.bot.log(f"Erreur pendant le cycle ({type(exc).__name__}) : {str(exc)[:200]}", "error", event=True)
                if errors >= 3:
                    await self.stop("3 erreurs consécutives", automatic=True)
                    return
            lo, hi = cfg["cycle"]["min_minutes"], cfg["cycle"]["max_minutes"]
            wait = random.uniform(lo, hi) * 60
            due_in = self.next_due_in(self._cfg())
            if due_in is not None:  # on ne dépasse pas l'échéance d'une revérification
                wait = min(wait, max(2.0, due_in))
            await self._sleep(wait)

    # ------------------------------------------------------------ superviseur de connexion
    async def _ensure_ready(self, cfg: dict) -> str | None:
        async with self._ready_lock:  # la boucle principale et la connexion automatique ne agissent jamais en même temps
            return await self._ensure_ready_locked(cfg)

    async def _ensure_ready_locked(self, cfg: dict) -> str | None:
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
        if screen != "game":
            self._in_game = False
        if screen == "game":
            self._universe_attempts = 0
            if not self._in_game:  # arrivée (ou retour) dans la partie
                self._in_game = True
                self._last_notes.pop("idle-cycle", None)
                b.log("Dans la partie : le bot travaille." if self._running else "Connecté à la partie.", event=True)
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

    # ------------------------------------------------- connexion automatique (sans interrupteur)
    def reset_connection(self) -> None:
        """Nouveaux identifiants ou nouvelle demande : les blocages de connexion précédents tombent."""
        self._login_failures, self._login_blocked, self._next_login_at = 0, "", 0.0
        self._universe_attempts = 0

    def ensure_keeper(self) -> None:
        """Dès que des identifiants sont en mémoire, le bot se connecte et choisit l'univers tout seul, puis garde la
        connexion (reconnexion si la session expire), que l'interrupteur soit sur ON ou sur OFF."""
        if self._keeper is None or self._keeper.done():
            self._keeper = asyncio.create_task(self._keep_connected())

    async def stop_keeper(self) -> None:
        task, self._keeper = self._keeper, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.connection = ""

    async def _keep_connected(self) -> None:
        poll = self.bot._settings.poll_seconds
        while self.bot.has_credentials():
            if self._running and self.in_window()[0]:  # la boucle principale (ON, dans la plage) s'en charge
                await asyncio.sleep(poll)
                continue
            try:
                problem = await self._ensure_ready(self._cfg())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                problem = f"vérification de l'écran impossible ({type(exc).__name__})"
            self.connection = problem or "connecté, dans la partie"
            if problem:
                self._note("connection", f"Connexion automatique : {problem}.", event=True)
            else:
                self._last_notes.pop("connection", None)
            await asyncio.sleep(poll)
        self.connection = ""  # plus d'identifiants en mémoire : plus rien à maintenir

    async def _pick_universe(self, cfg: dict) -> str | None:
        b = self.bot
        universes = await b.list_universes()
        self.store.add_catalog("universes", [u["name"] for u in universes])
        wanted = cfg["universe"]
        if not wanted:
            return "choisissez un univers (Réglages › Compte et univers, ou bouton d'univers sur la page Écran)"
        match = next((u for u in universes if norm(u["name"]) == norm(wanted)), None)
        if match is None:
            return f"univers « {wanted} » indisponible sur la page de choix"
        self._universe_attempts += 1
        if self._universe_attempts > 4:
            return "l'entrée dans l'univers n'aboutit pas : entrez-y à la main (contrôle manuel) ; le bot reprendra seul"
        b.log(f"Page de choix de l'univers : entrée dans « {match['name']} ».", event=True)
        await b.enter_universe(match["index"])
        return "entrée dans l'univers en cours"

    async def _log_in(self) -> str | None:
        b = self.bot
        if self._login_blocked:
            return self._login_blocked
        if not b.has_credentials():
            return "identifiants requis (Réglages › Compte et univers)"
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
            self._login_blocked = "connexion refusée par le site : corrigez les identifiants (Réglages › Compte et univers)"
            b.log("Reconnexion automatique suspendue : le site a refusé les identifiants (corrigez-les dans Réglages › Compte).", "error", kind="attention")
        elif self._login_failures >= 3:
            self._login_blocked = "intervention requise (contrôle anti-bot ?) : connectez-vous à la main (page Écran, contrôle manuel) ; le bot reprendra seul"
            b.log("Trois tentatives de connexion sans résultat : connectez-vous à la main (page Écran, contrôle manuel) ; le bot reprendra seul.", "error", kind="attention")
        else:
            delay = (60, 300)[min(self._login_failures - 1, 1)]
            self._next_login_at = time.monotonic() + delay
            b.log(f"Connexion sans résultat : nouvelle tentative dans {delay} s.", "warn", event=True)
        return self._login_blocked or "connexion non aboutie"

    # ------------------------------------------------------------------- cycle
    async def run_cycle(self, cfg: dict | None = None) -> None:
        cfg = cfg or self._cfg()
        live = cfg["mode"] == "live"
        cons = cfg["constructions"]
        self.cycle_running = True
        self.idle.clear()
        started = time.monotonic()
        try:
            await self.guard()
            signature = self._cons_sig(cons)
            if signature != self._cons_signature:  # réglages modifiés : on repart d'un état propre
                self._cons_signature = signature
                self._pstate.clear()
            prod_sig = json.dumps([cfg["production"], cfg["expeditions"]], sort_keys=True)
            if prod_sig != self._prod_signature:
                self._prod_signature = prod_sig
                self._prodstate.clear()
            research_on = cfg["research"]["enabled"]
            if not research_on and not self._constructions_due(cfg) and not self._production_due(cfg):
                due_in = self.next_due_in(cfg)
                when = f" (prochain contrôle dans {due_in / 60:.0f} min)" if due_in is not None else ""
                self._note("idle-cycle", f"Cycle : rien à vérifier pour l'instant{when}.")
                return
            self._last_notes.pop("idle-cycle", None)
            planets = await self.driver.list_planets()
            self._known_planets = planets
            self.store.add_catalog("planets", [p.name for p in planets])
            self.bot.log(f"Cycle : {len(planets)} planète(s) lues ({'réel' if live else 'simulation'}).")
            for block in cfg["priority"]:  # le joueur choisit qui sert ses ressources en premier (Réglages › Priorités)
                if block == "research":
                    if research_on:
                        await self._research_step(cfg, planets, live)
                elif block == "constructions":
                    if cons["enabled"] and cons["queue"]:
                        await self._constructions_step(cfg, planets, live)
                elif block == "expeditions":
                    await self._expeditions_step(cfg, planets, live)
                else:
                    await self._production_step(cfg, planets, live, block)
            self.last_cycle = f"{datetime.now():%H:%M:%S} ({time.monotonic() - started:.0f} s)"
        finally:
            self.cycle_running = False
            self.idle.set()

    # ----------------------------------------------- constructions : état par planète
    @staticmethod
    def _cons_sig(cons: dict) -> str:
        keys = ("enabled", "scope", "planet", "queue", "recheck_minutes")
        return json.dumps({k: cons[k] for k in keys}, sort_keys=True)

    @staticmethod
    def _in_scope(cons: dict, planets: list[PlanetInfo]) -> list[PlanetInfo]:
        if cons["scope"] == "one":
            return [p for p in planets if norm(p.name) == norm(cons["planet"])]
        return list(planets)

    def _planet_due(self, key: str) -> bool:
        state = self._pstate.get(key)
        return state is None or state["next"] <= time.time()

    def _constructions_due(self, cfg: dict) -> bool:
        cons = cfg["constructions"]
        if not cons["enabled"] or not cons["queue"]:
            return False
        if not self._known_planets:
            return True  # liste des planètes pas encore lue
        scope = self._in_scope(cons, self._known_planets)
        if not scope:
            return True  # planète désignée absente de la liste connue : on relit la liste
        return any(self._planet_due(norm(p.name)) for p in scope)

    def next_due_in(self, cfg: dict) -> float | None:
        """Secondes avant la prochaine revérification d'une planète (None si rien n'est programmé)."""
        cons = cfg["constructions"]
        waits = []
        if cons["enabled"] and cons["queue"] and self._known_planets:
            for planet in self._in_scope(cons, self._known_planets):
                state = self._pstate.get(norm(planet.name))
                waits.append(max(0.0, state["next"] - time.time()) if state else 60.0)
        if self._known_planets:
            for block, block_cfg in self._production_blocks(cfg):
                for planet in self._in_scope(block_cfg, self._known_planets):
                    state = self._prodstate.get(f"{block}|{norm(planet.name)}")
                    waits.append(max(0.0, state["next"] - time.time()) if state else 60.0)
        return min(waits) if waits else None

    def _set_pstate(self, key: str, name: str, status: str, seconds: float, detail: str = "") -> None:
        self._pstate[key] = {"name": name, "status": status, "next": time.time() + seconds, "detail": detail}

    def planets_state(self) -> list[dict]:
        """Planètes suivies par le timer des constructions : statut, raison, prochaine vérification."""
        now = time.time()
        rows = [
            {"planet": st["name"], "status": st["status"], "detail": st["detail"], "next_in_s": max(0, round(st["next"] - now))}
            for st in self._pstate.values()
        ]
        return sorted(rows, key=lambda r: (r["next_in_s"], r["planet"]))[:30]

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
        view["presets"] = catalog_module.PRESETS
        view["production"] = catalog_module.production_entries(cfg["catalog"]["ships"])
        view["production_sections"] = catalog_module.PRODUCTION_SECTIONS
        view["expedition_ways"] = [{"id": k, "label": v} for k, v in catalog_module.EXPEDITION_WAYS.items()]
        view["expedition_units"] = catalog_module.expedition_units(cfg["catalog"]["ships"])
        view["blocks"] = [{"id": b, "label": catalog_module.BLOCK_LABELS[b]} for b in catalog_module.PRIORITY_BLOCKS]
        return view

    async def _launch_decision(self, cfg: dict, live: bool, note_key: str, key: str, decision, cards, planet_label: str) -> str:
        """Journalise la décision et, en mode réel, clique. Retourne « would_launch », « launched », « capped » ou « failed »."""
        section = next(s for s, c in cards if c is decision.card)
        if not live:
            self._record_simulated(planet_label, decision)
            self._note(note_key, f"[Simulation] {planet_label} : je lancerais {decision.describe()}.", event=True)
            return "would_launch"
        if self.launched_today(cfg) >= cfg["daily_cap"]:
            self._note("cap", f"Maximum de lancements atteint ({cfg['daily_cap']} par jour) : plus aucune action aujourd'hui.", "warn", event=True)
            return "capped"
        self._last_notes.pop("cap", None)
        self.bot.log(f"{planet_label} : je lance {decision.describe()}.")
        result = await self.driver.launch(decision.card, section)
        if result.confirmed:
            self._unconfirmed_streak = 0
            self._count_launch(cfg)
            self._mark_busy(key, decision.card)
            self._last_notes.pop(note_key, None)
            self.bot.log(
                f"{planet_label} : {decision.card.name} lancée (niveau {decision.card.level} → {decision.card.level + 1}) — "
                f"{self.launched_today(cfg)}/{cfg['daily_cap']} aujourd'hui.", event=True, kind="launch")
            return "launched"
        self._unconfirmed_streak += 1
        self.bot.log(f"{planet_label} : lancement non confirmé ({result.detail}).", "warn", event=True)
        if self._unconfirmed_streak >= 2:
            await self.stop("deux lancements consécutifs non confirmés", automatic=True)
            raise Interrupted("lancements non confirmés")
        return "failed"

    async def _decide_and_act(
        self, cfg: dict, live: bool, label: str, key: str, targets: dict[str, int], cards: list[tuple[str, Card]],
        resources, action: str, planet_label: str,
    ) -> bool:
        """Recherches : décide (le moins pesant sur le stock d'abord) et lance. True si une action réelle est partie."""
        outcome = decide([c for _, c in cards], resources, targets, action)
        if outcome.decision is None:
            self._note(f"{label}:{key}", f"{planet_label} : rien à lancer ({outcome.summary()}).")
            return False
        return await self._launch_decision(cfg, live, f"{label}:{key}", key, outcome.decision, cards, planet_label) == "launched"

    def _resolve_queue(self, cons: dict, cards: list[Card], planet_key: str) -> list[tuple[str, int]]:
        """Objectifs en niveaux absolus. « Monter de N » = niveau de départ de la planète + N ; le niveau de départ est
        lu la première fois qu'on voit le bâtiment sur la planète, puis conservé."""
        by_name = {norm(c.name): c for c in cards}
        resolved: list[tuple[str, int]] = []
        for item in cons["queue"]:
            if not item["relative"]:
                resolved.append((item["name"], item["level"]))
                continue
            key = f"{item['id']}|{planet_key}"
            card = by_name.get(norm(item["name"]))
            if key not in self._baseline and card is not None and card.level is not None:
                self._baseline[key] = card.level
                self._save_state()
            if key in self._baseline:
                resolved.append((item["name"], self._baseline[key] + item["level"]))
        return resolved

    async def _read_buildings(self, planet: PlanetInfo, names: list[str]):
        """Ouvre les pages Industrie puis Infrastructures (seulement si nécessaire) et lit les cartes de la planète."""
        cards: list[tuple[str, Card]] = []
        resources = None
        for section in ("buildings", "facilities"):
            if not await self.driver.open_section(section):
                self.bot.log(f"Planète {planet.name} : page « {section} » inaccessible.", "warn")
                break
            await human.think(self.bot._settings)
            page_state = await self.driver.read_page()
            resources = page_state.resources
            self._catalog("buildings", page_state.cards, planet.name)
            cards += [(section, c) for c in page_state.cards]
            if {norm(n) for n in names} <= {norm(c.name) for _, c in cards}:
                break  # tous les bâtiments de la liste ont été vus : inutile de charger l'autre page
        return cards, resources

    async def _constructions_step(self, cfg: dict, planets: list[PlanetInfo], live: bool) -> None:
        """Pour chaque planète à vérifier : lance la PREMIÈRE amélioration réalisable de la liste, sinon met la planète
        en attente et la revérifiera dans `recheck_minutes`."""
        cons = cfg["constructions"]
        names = [q["name"] for q in cons["queue"]]
        ids = {q["id"] for q in cons["queue"]}
        self._baseline = {k: v for k, v in self._baseline.items() if k.split("|")[0] in ids}  # éléments retirés de la liste
        scope = self._in_scope(cons, planets)
        if cons["scope"] == "one" and not scope:
            self._note("cons:planet", f"Constructions : planète « {cons['planet']} » introuvable dans la liste des planètes.", "warn")
            return
        self._last_notes.pop("cons:planet", None)
        recheck = cons["recheck_minutes"] * 60
        due = [p for p in scope if self._planet_due(norm(p.name))]
        random.shuffle(due)
        for planet in due:
            await self.guard()
            key, label = norm(planet.name), f"Planète {planet.name}"
            if self._busy(key):
                remaining = self._busy_until[key] - time.time()
                self._set_pstate(key, planet.name, "busy", max(5.0, remaining), "construction en cours")
                self._note(f"busy:{key}", f"{label} : construction en cours, nouvelle vérification après {datetime.fromtimestamp(self._busy_until[key]):%H:%M}.", event=True)
                continue
            self._last_notes.pop(f"busy:{key}", None)
            try:
                if not await self.driver.switch_planet(planet):
                    self.bot.log(f"{label} : changement de planète non confirmé, planète ignorée.", "warn")
                    continue
                cards, resources = await self._read_buildings(planet, names)
                if resources is None:
                    continue
                queue = self._resolve_queue(cons, [c for _, c in cards], key)
                outcome = decide_ordered([c for _, c in cards], resources, queue)
                await self._settle_planet(cfg, live, planet, key, label, outcome, cards, recheck)
            except GameUiError as exc:
                self.bot.log(f"{label} : {exc}.", "error")
                await self.stop("écran du jeu inattendu", automatic=True)
                raise Interrupted(str(exc)) from exc
            await human.think(self.bot._settings)

    async def _settle_planet(self, cfg: dict, live: bool, planet: PlanetInfo, key: str, label: str, outcome: Outcome, cards, recheck: float) -> None:
        wait = recheck * random.uniform(0.95, 1.1)  # légère variation : pas de métronome
        minutes = f"{wait / 60:.0f}" if wait >= 90 else f"{wait / 60:.1f}"
        if outcome.decision is not None:
            result = await self._launch_decision(cfg, live, f"c:{key}", key, outcome.decision, cards, label)
            if result == "launched":
                self._set_pstate(key, planet.name, "busy", max(5.0, self._busy_until[key] - time.time()), f"{outcome.decision.card.name} en cours")
            elif result == "would_launch":
                self._set_pstate(key, planet.name, "would_launch", wait, f"à blanc : {outcome.decision.card.name}")
            else:  # plafond atteint ou lancement non confirmé
                self._set_pstate(key, planet.name, "retry", wait, "lancement non effectué")
        elif outcome.pending:
            detail = " ; ".join(outcome.pending[:3])
            self._set_pstate(key, planet.name, "pending", wait, detail)
            self._note(f"c:{key}", f"{label} : en attente ({detail}) — nouvelle vérification dans {minutes} min.", event=True)
        elif outcome.reached and not outcome.notes:
            self._set_pstate(key, planet.name, "done", 6 * 3600, "objectifs atteints")
            self._note(f"c:{key}", f"{label} : tous les objectifs de la liste sont atteints.", event=True)
        else:
            self._set_pstate(key, planet.name, "idle", wait, outcome.summary())
            self._note(f"c:{key}", f"{label} : rien à lancer ({outcome.summary()}) — nouvelle vérification dans {minutes} min.")

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
            await self.stop("écran du jeu inattendu", automatic=True)
            raise Interrupted(str(exc)) from exc

    # ------------------------------------------------------------------ production
    def _production_blocks(self, cfg: dict) -> list[tuple[str, dict]]:
        """(bloc, réglages du bloc) des productions et expéditions activées qui ont des lignes, dans l'ordre de priorité."""
        prod, exp = cfg["production"], cfg["expeditions"]
        sections = {item["section"] for item in prod["queue"]} if prod["enabled"] else set()
        out = []
        for block in cfg["priority"]:
            if block == "expeditions":
                if exp["enabled"] and exp["queue"]:
                    out.append((block, exp))
            elif block in ("population", "shipyard", "specialized", "defense") and sections & set(catalog_module.section_of_block(block)):
                out.append((block, prod))
        return out

    def _production_due(self, cfg: dict) -> bool:
        blocks = self._production_blocks(cfg)
        if not blocks:
            return False
        if not self._known_planets:
            return True
        for block, block_cfg in blocks:
            scope = self._in_scope(block_cfg, self._known_planets)
            if not scope or any(self._prod_planet_due(block, p) for p in scope):
                return True
        return False

    def _prod_planet_due(self, block: str, planet: PlanetInfo) -> bool:
        state = self._prodstate.get(f"{block}|{norm(planet.name)}")
        return state is None or state["next"] <= time.time()

    def production_state(self) -> list[dict]:
        now = time.time()
        rows = [
            {"block": st["block"], "label": catalog_module.BLOCK_LABELS[st["block"]], "planet": st["planet"], "status": st["status"],
             "detail": st["detail"], "next_in_s": max(0, round(st["next"] - now))}
            for st in self._prodstate.values()
        ]
        return sorted(rows, key=lambda r: (r["next_in_s"], r["planet"], r["block"]))[:60]

    def reset_production_done(self, item_id: str | None = None) -> int:
        """Réactive une commande unique (ou toutes) : elle sera relancée au prochain passage."""
        keys = [k for k in self._prod_done if item_id is None or k.split("|")[0] == item_id]
        for key in keys:
            del self._prod_done[key]
        self._prodstate.clear()
        self._save_state()
        return len(keys)

    def production_done(self) -> dict[str, int]:
        """id de ligne → nombre de planètes où la commande unique est faite (pour l'interface)."""
        valid = {production_module.signature(i): i["id"] for i in self._cfg()["production"]["queue"]}
        counts: dict[str, int] = {}
        for key, sig in self._prod_done.items():
            item_id = key.split("|")[0]
            if valid.get(sig) == item_id:
                counts[item_id] = counts.get(item_id, 0) + 1
        return counts

    def _record_simulated_production(self, planet_label: str, item: dict, decision) -> None:
        entry = {"t": time.time(), "planet": planet_label.removeprefix("Planète "), "name": item["name"], "from": None, "to": None,
                 "qty": decision.label()}
        last = self.simulated[-1] if self.simulated else None
        if last and (last["planet"], last["name"], last.get("qty")) == (entry["planet"], entry["name"], entry["qty"]):
            last["t"] = entry["t"]
            return
        self.simulated.append(entry)
        self.simulated_total += 1

    async def _production_step(self, cfg: dict, planets: list[PlanetInfo], live: bool, block: str) -> None:
        """Pour chaque planète à vérifier : traite, dans l'ordre de la liste, les lignes de production du bloc."""
        prod = cfg["production"]
        sections = set(catalog_module.section_of_block(block))
        items = [i for i in prod["queue"] if i["section"] in sections]
        if not prod["enabled"] or not items:
            return
        scope = self._in_scope(prod, planets)
        if prod["scope"] == "one" and not scope:
            self._note("prod:planet", f"Production : planète « {prod['planet']} » introuvable dans la liste des planètes.", "warn")
            return
        self._last_notes.pop("prod:planet", None)
        recheck = prod["recheck_minutes"] * 60
        due = [p for p in scope if self._prod_planet_due(block, p)]
        random.shuffle(due)
        for planet in due:
            await self.guard()
            label = f"Planète {planet.name}"
            try:
                if not await self.driver.switch_planet(planet):
                    self.bot.log(f"{label} : changement de planète non confirmé, planète ignorée.", "warn")
                    continue
                statuses = []
                for item in items:
                    await self.guard()
                    statuses.append(await self._production_item(cfg, live, planet, label, item))
                    await human.think(self.bot._settings)
            except GameUiError as exc:
                self.bot.log(f"{label} : {exc}.", "error")
                await self.stop("écran du jeu inattendu", automatic=True)
                raise Interrupted(str(exc)) from exc
            self._settle_production(block, planet, statuses, recheck)

    def _settle_production(self, block: str, planet: PlanetInfo, statuses: list[tuple[str, str]], recheck: float) -> None:
        wait = recheck * random.uniform(0.95, 1.1)
        kinds = [k for k, _ in statuses]
        if kinds and all(k == "reached" for k in kinds):
            status, seconds = "done", max(wait, 1800.0)
        elif "launched" in kinds:
            status, seconds = "launched", wait
        elif "wait" in kinds:
            status, seconds = "pending", wait
        elif "would" in kinds:
            status, seconds = "would_launch", wait
        elif "skip" in kinds or "failed" in kinds:
            status, seconds = "retry", wait
        else:
            status, seconds = "idle", wait
        detail = " ; ".join(d for k, d in statuses if k != "reached" or status == "done")[:160]
        self._prodstate[f"{block}|{norm(planet.name)}"] = {
            "block": block, "planet": planet.name, "status": status, "next": time.time() + seconds, "detail": detail}

    async def _production_item(self, cfg: dict, live: bool, planet: PlanetInfo, label: str, item: dict) -> tuple[str, str]:
        """Traite une ligne. Retourne (statut, détail) : launched | would | wait | reached | skip | failed."""
        section, name = item["section"], item["name"]
        tab = next((t for t, names in catalog_module.POPULATION if any(norm(n) == norm(name) for n in names)), None) if section == "population" else None
        key = f"{item['id']}|{norm(planet.name)}"
        note = f"p:{key}"
        sig = production_module.signature(item)
        done = item["mode"] == "once" and self._prod_done.get(key) == sig
        busy_key = f"prod|{key}"
        if item["mode"] == "stock" and section != "population" and self._busy(busy_key):
            until = datetime.fromtimestamp(self._busy_until[busy_key])
            self._note(note, f"{label} : {name} : commande récente, nouvelle vérification après {until:%H:%M}.")
            return "wait", f"{name} : commande récente"
        if done:
            self._note(note, f"{label} : {name} : commande déjà faite sur cette planète.")
            return "reached", f"{name} : commande déjà faite"
        page = await self.driver.read_production(section, tab, want=name)
        if section == "ships":
            self.store.add_catalog("ships", [c.name for c in page.cards])
        card = next((c for c in page.cards if norm(c.name) == norm(name)), None)
        decision = production_module.decide_production(item, card, page.resources, done)
        what = production_module.describe(item, decision)
        if decision.action == "reached":
            self._note(note, f"{label} : {name} : {decision.reason}.")
            return "reached", f"{name} : {decision.reason}"
        if decision.action == "skip":
            self._note(note, f"{label} : {name} : rien lancé ({decision.reason}).", "warn", event=True)
            return "skip", f"{name} : {decision.reason}"
        if decision.action == "wait":
            self._note(note, f"{label} : {name} : en attente ({decision.reason}).", event=True)
            return "wait", f"{name} : {decision.reason}"
        # decision.action == "launch"
        if not live:
            self._record_simulated_production(label, item, decision)
            self._note(note, f"[Simulation] {label} : je lancerais {what}.", event=True)
            return "would", f"à blanc : {what}"
        if self.launched_today(cfg) >= cfg["daily_cap"]:
            self._note("cap", f"Maximum de lancements atteint ({cfg['daily_cap']} par jour) : plus aucune action aujourd'hui.", "warn", event=True)
            return "failed", "maximum journalier atteint"
        self._last_notes.pop("cap", None)
        self.bot.log(f"{label} : je lance {what}.")
        result = await self.driver.produce(section, tab, name, decision.qty)
        if result.confirmed:
            self._unconfirmed_streak = 0
            self._count_launch(cfg)
            if item["mode"] == "once":
                self._prod_done[key] = sig
            if item["mode"] == "stock" and section != "population":
                self._busy_until[busy_key] = time.time() + 600
            self._save_state()
            self._last_notes.pop(note, None)
            self.bot.log(
                f"{label} : {name} × {result.qty} lancée(s) — {self.launched_today(cfg)}/{cfg['daily_cap']} aujourd'hui.",
                event=True, kind="launch")
            if not result.verified:
                self.bot.log(f"{label} : {name} : le débit est trop petit pour être vérifié à l'écran, contrôlez dans le jeu.", "warn")
            return "launched", f"{name} × {result.qty} lancée(s)"
        self._unconfirmed_streak += 1
        self.bot.log(f"{label} : {name} : lancement non confirmé ({result.detail}).", "warn", event=True)
        if self._unconfirmed_streak >= 2:
            await self.stop("deux lancements consécutifs non confirmés", automatic=True)
            raise Interrupted("lancements non confirmés")
        return "failed", f"{name} : lancement non confirmé"

    def _record_simulated_text(self, planet_label: str, text: str, name: str) -> None:
        entry = {"t": time.time(), "planet": planet_label.removeprefix("Planète "), "name": name, "from": None, "to": None,
                 "qty": None, "text": text}
        last = self.simulated[-1] if self.simulated else None
        if last and (last["planet"], last.get("text")) == (entry["planet"], text):
            last["t"] = entry["t"]
            return
        self.simulated.append(entry)
        self.simulated_total += 1

    async def _expeditions_step(self, cfg: dict, planets: list[PlanetInfo], live: bool) -> None:
        """Remplit les places d'exploration voulues (SIMULATION seulement pour l'instant : aucun lancement n'est codé)."""
        exp = cfg["expeditions"]
        if not exp["enabled"] or not exp["queue"]:
            return
        if live:
            self._note("exp:live", "Expéditions : le lancement réel n'est pas encore disponible (captures manquantes) : "
                       "le bot lit la page et simule seulement.", "warn", event=True)
        scope = self._in_scope(exp, planets)
        if exp["scope"] == "one" and not scope:
            self._note("exp:planet", f"Expéditions : planète « {exp['planet']} » introuvable dans la liste des planètes.", "warn")
            return
        self._last_notes.pop("exp:planet", None)
        recheck = exp["recheck_minutes"] * 60
        due = [p for p in scope if self._prod_planet_due("expeditions", p)]
        random.shuffle(due)
        ways = list(dict.fromkeys(line["way"] for line in exp["queue"]))
        for planet in due:
            await self.guard()
            label = f"Planète {planet.name}"
            try:
                if not await self.driver.switch_planet(planet):
                    self.bot.log(f"{label} : changement de planète non confirmé, planète ignorée.", "warn")
                    continue
                pages = {}
                for way in ways:
                    await self.guard()
                    pages[way] = await self.driver.read_expedition(way)
                    if way == "flotte":
                        self.store.add_catalog("ships", [pages[way].names[k] for k in pages[way].names
                                                          if k not in {norm(n) for n in catalog_module.SPECIALIZED}])
            except GameUiError as exc:
                self.bot.log(f"{label} : {exc}.", "error")
                await self.stop("écran du jeu inattendu", automatic=True)
                raise Interrupted(str(exc)) from exc
            plan = expedition_module.plan_expeditions(exp["queue"], pages, exp["active_target"])
            statuses: list[tuple[str, str]] = []
            for launch in plan.launches:
                text = launch.describe()
                self._record_simulated_text(label, text, "Exploration")
                self._note(f"x:{planet.name}:{text}", f"[Simulation] {label} : je lancerais {text}.", event=True)
                statuses.append(("would", f"à blanc : {text}"))
            if not plan.launches:
                reason = " ; ".join(plan.notes) or "rien à lancer"
                self._note(f"x:{planet.name}", f"{label} : pas d'exploration à lancer ({reason}).")
                statuses.append(("wait" if plan.free_slots else "idle", reason))
            self._settle_production("expeditions", planet, statuses, recheck)
            await human.think(self.bot._settings)

    async def _read_expedition_state(self) -> dict[str, dict]:
        """Lecture seule de Opérations › Exploration (compteurs et unités disponibles), pour chaque voie."""
        out: dict[str, dict] = {}
        for way, label in catalog_module.EXPEDITION_WAYS.items():
            try:
                page = await self.driver.read_expedition(way)
                out[way] = {"label": label, "active": page.active, "active_max": page.active_max, "daily": page.daily,
                            "daily_max": page.daily_max, "unreadable": page.unreadable,
                            "units": [{"name": page.names[k], "available": v} for k, v in page.available.items()]}
            except GameUiError as exc:
                out[way] = {"label": label, "error": str(exc), "units": []}
        return out

    async def _read_production_state(self) -> dict[str, dict]:
        """Lecture seule des pages de production : ce que le bot comprend (stock, coût d'une unité, bouton actif)."""
        out: dict[str, dict] = {}
        for entry in catalog_module.PRODUCTION_SECTIONS:
            section = entry["id"]
            try:
                if section == "population":
                    cards, page = [], None
                    for tab, _names in catalog_module.POPULATION:
                        page = await self.driver.read_production(section, tab, force_tab=True)
                        cards += page.cards
                else:
                    page = await self.driver.read_production(section, None, force_tab=section in ("ships", "components"))
                    cards = page.cards
                if section == "ships":
                    self.store.add_catalog("ships", [c.name for c in cards])
                out[section] = {"label": entry["label"], "planet": page.planet, "cards": [asdict(c) for c in cards]}
            except GameUiError as exc:
                out[section] = {"label": entry["label"], "error": str(exc), "cards": []}
        return out

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
            return {"planets": [asdict(p) for p in planets], "sections": sections, "production": await self._read_production_state(),
                    "expeditions": await self._read_expedition_state()}
        finally:
            self.driver.guard = previous
            self.cycle_running = False
            self.idle.set()
