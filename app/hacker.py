"""Piratage : Naël Soryn pirate en boucle l'Errant le plus proche de la planète choisie (gain d'expérience).

À chaque passage : lecture de « Missions en cours » (un piratage déjà en cours fixe la fin) et de l'état de Naël ; s'il est
« Disponible » et que la fin du dernier piratage + le délai tiré au hasard est passée, un nouveau piratage part vers l'Errant
retenu. Si le jeu refuse cet Errant (piliers désactivés…), le suivant le plus proche est retenu à sa place.
Le relevé du jour (Errants les plus proches, Drone non grisé = dans le ratio ×2) se fait sur la carte de la Galaxie.
"""

from __future__ import annotations

import asyncio
import math
import random
import re
import time
from datetime import datetime

from . import human
from .builder import UNCONFIRMED_LIMIT
from .excavator import MISSIONS_JS, READY_SECONDS, stable_read, wait_enabled, wait_missions_loaded
from .explorer import CLEAR_MARK_JS, MARK_TARGET_JS, TARGET_ATTRIBUTE, ExplorationPass
from .hackings import TARGETS_KEPT
from .planets import GameUiError, Interrupted
from .planning import TIMEZONE
from .spy import TARGETS_JS

HACKING_PATH = "operations?method=PILLARS&mission=HACKING"
HACK_SECONDS = 30 * 60  # durée fixe d'un piratage (wiki et jeu)
PAGE_WAIT_SECONDS = 10.0
LAUNCH_NAME = re.compile(r"^Lancer le piratage", re.I)
REFUSED_RE = re.compile(r"^Impossible", re.I)
HACK_CONFIRM_SECONDS = 20.0  # vu sur le vrai jeu : un lancement réussi n'a pas été vu en 8 s


def _clock(ts: float) -> str:
    """Heure française (HH:MM) d'un instant, quel que soit le fuseau du conteneur."""
    return datetime.fromtimestamp(ts, TIMEZONE).strftime("%H:%M")

# Message du jeu après un lancement réussi : « Vortex généré. Mission de Piratage en cours. »
STARTED_JS = """() => [...document.querySelectorAll('body *')].some((e) => !e.children.length && e.getClientRects().length
  && /Mission de Piratage en cours/i.test((e.textContent || '').trim()))"""

# État de Naël sur la carte visible du formulaire : available, busy ou null (carte absente).
NAEL_JS = """() => {
  const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
  for (const e of document.querySelectorAll('main *')) {
    if (e.children.length || text(e) !== 'Naël Soryn' || !e.getClientRects().length) continue;
    let box = e;
    while (box && !/disponible/i.test(text(box))) box = box.parentElement;
    if (!box) return null;
    return /Indisponible/i.test(text(box)) ? 'busy' : 'available';
  }
  return null;
}"""

MESSAGES_JS = """() => [...document.querySelectorAll('body *')]
  .filter((e) => !e.children.length && e.getClientRects().length && /^Impossible/i.test((e.textContent || '').trim()))
  .map((e) => e.textContent.replace(/\\s+/g, ' ').trim())"""

RECHECK_SECONDS = 2 * 60  # lancement non confirmé : on revérifie vite au lieu d'attendre la vérification de secours (30 min)

# Diagnostic d'un lancement non confirmé : ce que montre l'écran juste après le clic (champ Cible, bouton, messages).
SCREEN_JS = """() => {
  const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
  const visible = (el) => el.getClientRects().length > 0;
  let target = null;
  for (const label of document.querySelectorAll('main *')) {
    if (label.children.length || text(label) !== 'Cible' || !visible(label)) continue;
    let box = label;
    while (box && !box.querySelector('input[type=text], input:not([type])')) box = box.parentElement;
    const input = box && [...box.querySelectorAll('input[type=text], input:not([type])')].find(visible);
    if (input) { target = input.value; break; }
  }
  const button = [...document.querySelectorAll('button')].find((b) => visible(b) && /^Lancer le piratage/i.test(text(b)));
  const notes = [...document.querySelectorAll('[role=status], [role=alert], [data-sonner-toast], .toast, [class*=toast]')]
    .filter(visible).map(text).filter(Boolean).slice(-3);
  return { target, button: button ? (button.disabled ? 'grisé' : 'actif') : 'absent', notes };
}"""


def nearest_errants(objects: list, planet_address: str | None, bounds, limit: int = TARGETS_KEPT) -> list[dict]:
    """Planètes d'Errants (`PVE_PLANET`) de la fenêtre détaillée, de la plus proche à la plus lointaine."""
    errants = [o for o in objects if isinstance(o, dict) and o.get("kind") == "PVE_PLANET" and o.get("address")
               and isinstance(o.get("positionMilli"), list) and len(o["positionMilli"]) == 2]
    if not errants:
        return []
    own = next((o for o in objects if isinstance(o, dict) and planet_address and o.get("address") == planet_address
                and isinstance(o.get("positionMilli"), list) and len(o["positionMilli"]) == 2), None)
    if own is not None:
        px, py = own["positionMilli"][0] / 1000, own["positionMilli"][1] / 1000
    elif isinstance(bounds, list) and len(bounds) == 2:
        px, py = (bounds[0][0] + bounds[1][0]) / 2000, (bounds[0][1] + bounds[1][1]) / 2000
    else:
        return []

    def distance(o: dict) -> float:
        return math.hypot(o["positionMilli"][0] / 1000 - px, o["positionMilli"][1] / 1000 - py)

    ranked = sorted(errants, key=distance)[: limit * 3]  # de la marge : certains seront hors du ratio ×2
    return [{"address": o["address"], "name": o.get("name") or "", "distance": round(distance(o), 1)} for o in ranked]


def draw_delay(settings: dict) -> float:
    return random.uniform(settings["delay_min"], settings["delay_max"]) * 60


class HackingPass(ExplorationPass):
    """Piratage en boucle de l'Errant le plus proche par Naël Soryn."""

    feature_id = "hackings"
    _recheck_at: float | None = None  # après un lancement non confirmé : revérification rapide (epoch)

    # ------------------------------------------------------------------ relevé du jour
    async def _daily_scan(self, settings: dict, source: str) -> None:
        planet = settings["planet"]
        try:
            targets = await self._find_errants(planet)
            await self._remember(planet)
            self.store.set_daily(targets, source)
            self.store.set_state(target=targets[0]["address"] if targets else "")
            best = f"{targets[0]['name'] or targets[0]['address']} ({targets[0]['address']})" if targets else "aucun"
            self.bot.log(f"Piratage : relevé du jour ({source}) — Errant le plus proche de {planet} : {best}.", event=True)
        except GameUiError as exc:
            self.store.set_daily([], source, f"{planet} : {exc}")
            self.bot.log(f"Piratage : aucun Errant relevé autour de {planet} ({exc}).", "warn", event=True)

    async def _find_errants(self, planet: str) -> list[dict]:
        coords = self._coords(planet)
        ranked = await self._search_map(planet, lambda objects, bounds: nearest_errants(objects, coords, bounds))
        if not ranked:
            raise GameUiError("aucun Errant sur la carte autour de la planète")
        rows = {r["address"]: r for r in await self._eval(TARGETS_JS)}  # liste « Ensemble » : Drone grisé = hors du ratio ×2
        usable = [{**t, "name": rows[t["address"]]["name"] or t["name"]} for t in ranked if t["address"] in rows and rows[t["address"]]["drone"]]
        if not usable:
            raise GameUiError("aucun Errant à portée (ratio ×2) autour de la planète")
        return usable[:TARGETS_KEPT]

    # ------------------------------------------------------------------ page Piratage
    async def _open_hacking(self) -> None:
        await self._goto(HACKING_PATH)
        end = time.monotonic() + PAGE_WAIT_SECONDS
        while time.monotonic() < end:
            if await self.page.evaluate(MARK_TARGET_JS, TARGET_ATTRIBUTE) and await self.page.evaluate(NAEL_JS):
                await self.page.evaluate(CLEAR_MARK_JS, TARGET_ATTRIBUTE)
                await wait_missions_loaded(self.page)
                return
            await asyncio.sleep(0.25)
        raise GameUiError("la page Opérations › Piratage ne s'affiche pas")

    # ------------------------------------------------------------------ passage
    async def run_pass(self) -> str:
        s = self.store.get()
        if not s["planet"]:
            return "aucune planète de départ réglée (onglet Piratage), rien à lancer"
        self._recheck_at = None  # la revérification rapide, si elle était prévue, a lieu maintenant
        try:
            if self.store.daily_due():
                await self._daily_scan(s, "quotidien")
            await self._open_hacking()
            async def read():
                return await self.page.evaluate(MISSIONS_JS, "Piratage"), await self.page.evaluate(NAEL_JS)

            # Cohérent : une carte Piratage si et seulement si Naël est indisponible.
            (missions, _nael), _ok = await stable_read(read, lambda v: bool(v[0]) == (v[1] == "busy"))
            wait = self._sync(s, missions)
            if wait:
                return wait
            if not await self._nael_available():
                return "Naël Soryn n'est pas disponible"
            room, why = await self._mission_room()  # places gardées libres et priorités (onglet Places de mission)
            if room <= 0:
                self.bot.log(f"Piratage : plus de place de mission ({why}).", event=True)
                return f"plus de place de mission ({why})"
            return await self._launch_next(s)
        except Interrupted as exc:
            return f"interrompu ({exc})"
        except GameUiError as exc:
            self.bot.log(f"Piratage : {exc}.", "warn", event=True)
            return f"arrêté ({exc})"

    def next_due(self) -> float | None:
        """Heure (epoch) où le prochain piratage peut partir : fin du dernier + délai. None : inconnue."""
        now = time.time()
        ready = self.store.state()["ready"]
        upcoming = [t for t in (ready, self._recheck_at) if t is not None and t > now]
        return min(upcoming) if upcoming else None  # déjà passée : vérification de secours

    async def _nael_available(self) -> bool:
        """Vu sur le vrai jeu : l'état de Naël peut s'afficher un instant après le reste de la page."""
        end = time.monotonic() + READY_SECONDS
        while True:
            if await self.page.evaluate(NAEL_JS) == "available":
                return True
            if time.monotonic() >= end:
                return False
            await asyncio.sleep(0.25)

    def _sync(self, s: dict, missions: list[dict]) -> str:
        """Met à jour la fin du piratage en cours ; renvoie pourquoi on attend, ou "" si l'on peut relancer."""
        state, now = self.store.state(), time.time()
        if missions:
            mission = missions[0]
            end = now + (mission["left"] or 0)
            delay = (state["ready"] - state["end"]) if state["end"] is not None and state["ready"] is not None else draw_delay(s)
            self.store.set_state(end=end, ready=end + delay)
            return f"piratage en cours vers {mission['site'] or '?'}, fin à {_clock(end)}"
        if state["end"] is not None and state["end"] > now:  # absent de la liste : terminé au plus tard maintenant
            delay = state["ready"] - state["end"]
            state = self.store.set_state(end=now, ready=now + delay)
        if state["ready"] is not None and now < state["ready"]:
            return f"délai avant relance : prochain piratage vers {_clock(state['ready'])}"
        return ""

    async def _launch_next(self, s: dict) -> str:
        """Pirate l'Errant retenu ; s'il est refusé, le suivant le plus proche devient la cible."""
        state = self.store.state()
        targets = [t for t in self.store.daily()["targets"] if t["address"] not in state["refused"]]
        if state["target"] in {t["address"] for t in targets}:
            targets.sort(key=lambda t: t["address"] != state["target"])
        for target in targets:
            outcome = await self._launch(s["planet"], target)
            if outcome == "refused":
                refused = self.store.state()["refused"] + [target["address"]]
                self.store.set_state(refused=refused, target="")
                continue
            if outcome == "launched":
                end = time.time() + HACK_SECONDS
                self.store.set_state(target=target["address"], end=end, ready=end + draw_delay(s))
                return f"piratage lancé vers {target['name'] or target['address']} ({target['address']})"
            return outcome
        return "aucun Errant piratable autour de la planète (refaites le relevé du jour)"

    async def _launch(self, planet: str, target: dict) -> str:
        """« launched », « refused » (le jeu refuse cette cible), ou la raison pour laquelle rien n'est lancé."""
        page = self.page
        label = f"{target['name'] or target['address']} ({target['address']})"
        await self._open_hacking()
        await self._select_planet(planet)
        await self._settle(planet)
        if not await self._fill_target(target["address"]):
            self.bot.log(f"Piratage : cible {label} refusée par le jeu.", "warn", event=True)
            return "refused"
        if not await self._nael_available():
            return "Naël Soryn n'est pas disponible"
        go = page.get_by_role("button", name=LAUNCH_NAME)
        if not await wait_enabled(go):
            return "« Lancer le piratage » est grisé (énergie ou RT insuffisants ?)"
        before = len(await page.evaluate(MESSAGES_JS))
        await self._guard()
        await human.think(self.settings)
        await human.click(page, go.first, self.settings)
        return await self._confirm_hack(label, before)

    async def _screen_report(self) -> str:
        """Ce que montrait l'écran juste après un clic non confirmé (pour comprendre la cause au prochain échec)."""
        try:
            seen = await self.page.evaluate(SCREEN_JS)
            nael = {"available": "disponible", "busy": "indisponible"}.get(await self.page.evaluate(NAEL_JS), "introuvable")
        except Exception:  # noqa: BLE001 - diagnostic seulement : ne doit jamais faire échouer le passage
            return "écran illisible"
        notes = " | ".join(seen["notes"]) or "aucun"
        return f"écran : cible « {seen['target'] or ''} », bouton {seen['button']}, Naël {nael}, messages : {notes}"

    async def _confirm_hack(self, label: str, before: int) -> str:
        """Naël « Indisponible » ou une carte Piratage = lancé ; un message « Impossible … » = cible refusée."""
        end = time.monotonic() + HACK_CONFIRM_SECONDS
        while time.monotonic() < end:
            if (await self.page.evaluate(STARTED_JS) or await self.page.evaluate(NAEL_JS) == "busy"
                    or await self.page.evaluate(MISSIONS_JS, "Piratage")):
                self._unconfirmed = 0
                self.bot.log(f"Piratage lancé : Naël Soryn vers {label}.", event=True, kind="launch")
                return "launched"
            messages = await self.page.evaluate(MESSAGES_JS)
            if len(messages) > before:
                self.bot.log(f"Piratage : {label} refusé par le jeu (« {messages[-1]} »), Errant suivant.", "warn", event=True)
                return "refused"
            await asyncio.sleep(0.3)
        self._unconfirmed += 1
        self._recheck_at = time.time() + RECHECK_SECONDS
        self.bot.log(f"Clic sur « Lancer le piratage » fait ({label}) mais Naël est toujours disponible "
                     f"({await self._screen_report()}) ; nouvelle vérification dans {RECHECK_SECONDS // 60} min.",
                     "warn", event=True, kind="attention")
        if self._unconfirmed >= UNCONFIRMED_LIMIT:
            self.bot.scheduler.update(self.feature_id, {"enabled": False})
            self.bot.log("Piratage désactivé : deux lancements de suite non confirmés. Vérifiez à l'écran avant de le réactiver.", "error", event=True, kind="attention")
            raise Interrupted("désactivé après deux lancements non confirmés")
        return "lancement non confirmé"
