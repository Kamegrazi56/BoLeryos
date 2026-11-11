"""Ordonnanceur : lance les fonctionnalités activées quand leur minuteur arrive à échéance.

- Interrupteur général (« Bot ON/OFF ») : à OFF, rien ne tourne ; il repasse toujours à OFF au redémarrage.
- Un seul passage à la fois (verrou `bot.work`, partagé avec le scan des planètes) : si plusieurs fonctionnalités sont à
  échéance, la plus haute dans la liste de priorités passe d'abord, les autres attendent leur tour.
- Un passage commencé va toujours jusqu'au bout (pas d'interruption en plein clic), y compris si le bot passe à OFF.
- Le minuteur redémarre à la FIN du passage : durée tirée au hasard entre minimum et maximum. Une pause de quelques
  secondes sépare deux passages consécutifs.
- Un passage n'a pas lieu (et reste dû) tant que le bot n'est pas dans une partie ou que le contrôle manuel est actif.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Callable

from .features import Feature, FeatureStore

TICK_SECONDS = 1.0  # fréquence à laquelle l'ordonnanceur regarde si une fonctionnalité est due
SELF_TIMED_MIN_SECONDS = 60.0  # relance automatique : jamais deux passages à moins d'une minute
SELF_TIMED_FALLBACK_SECONDS = 30 * 60.0  # relance automatique : vérification de secours (Fouille lancée à la main…)
RECOVERY_WAIT_SECONDS = 2 * 60.0  # écran « Récupération interrompue » : attente avant de retenter le passage
RECOVERY_ATTEMPTS = 3  # au bout de 3 passages tombés sur cet écran, on passe à la fonctionnalité suivante


@dataclass
class _Runtime:
    next_at: float | None = None  # horloge monotone ; None : pas de passage prévu
    last_at: float | None = None  # heure réelle du dernier passage
    last_result: str = ""
    last_ok: bool = True
    running: bool = False


class Scheduler:
    def __init__(
        self,
        bot,
        store: FeatureStore,
        features: tuple[Feature, ...],
        *,
        blocker: Callable[[], str | None],
        gap: tuple[float, float] = (3.0, 8.0),
        tick: float = TICK_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
        recovery_wait: float = RECOVERY_WAIT_SECONDS,
    ) -> None:
        self._recovery_wait = recovery_wait
        self.bot = bot
        self.store = store
        self._features = {f.id: f for f in features}
        self._blocker = blocker
        self._gap = gap
        self._tick = tick
        self._clock = clock
        self._rng = rng or random.Random()
        self._runtime = {f.id: _Runtime() for f in features}
        self._on = False
        self._task: asyncio.Task | None = None
        self.waiting = ""  # pourquoi aucun passage n'a lieu alors qu'un est dû

    # ------------------------------------------------------------------ interrupteur général
    @property
    def on(self) -> bool:
        return self._on

    def start(self) -> None:
        if not self._on:
            self._on = True
            self.bot.log("Bot : ON.", event=True)
        for fid in self.store.order:  # une fonctionnalité activée sans passage prévu passe tout de suite
            rt = self._runtime[fid]
            if self.store.get(fid)["enabled"] and rt.next_at is None:
                rt.next_at = self._clock()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        """OFF : le passage en cours se termine, puis plus rien ne tourne."""
        if self._on:
            self._on = False
            self.waiting = ""
            self.bot.log("Bot : OFF (le passage en cours se termine).", event=True)

    async def shutdown(self) -> None:
        self._on = False
        task, self._task = self._task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    # ------------------------------------------------------------------ réglages
    def update(self, fid: str, patch: dict) -> dict:
        """Enregistre les réglages d'une fonctionnalité. Lève KeyError (inconnue) ou FeaturesError (refusé)."""
        was = self.store.get(fid)["enabled"]
        saved = self.store.update(fid, patch)
        rt = self._runtime[fid]
        label = self._features[fid].label
        if saved["enabled"] and not was:
            rt.next_at = self._clock()  # lancée tout de suite (dès que le bot est sur ON)
            reset = getattr(self.bot, "reset_failures", None)
            if reset is not None:
                reset(fid)  # réactivée après vérification : les échecs d'avant ne comptent plus
            self.bot.log(f"Fonctionnalité « {label} » activée.", event=True)
        elif was and not saved["enabled"]:
            rt.next_at = None
            self.bot.log(f"Fonctionnalité « {label} » désactivée.", event=True)
        return saved

    def reorder(self, order: list[str]) -> list[str]:
        return self.store.set_order(order)

    # ------------------------------------------------------------------ passages
    def _paused(self, fid: str) -> str | None:
        """Horaires propres à la fonctionnalité (Piratage, Exploration) : raison si elle est hors de ses plages."""
        check = getattr(self.bot, "feature_pause", None)
        return check(fid) if check is not None else None

    def _due(self) -> Feature | None:
        now = self._clock()
        for fid in self.store.order:  # l'ordre de la liste est l'ordre de priorité
            rt = self._runtime[fid]
            if self.store.get(fid)["enabled"] and rt.next_at is not None and rt.next_at <= now and not self._paused(fid):
                return self._features[fid]  # hors de ses horaires : reste due, et passe dès le début de sa plage
        return None

    def _draw_delay(self, fid: str) -> float:
        entry = self.store.get(fid)
        return self._rng.uniform(entry["min_minutes"], entry["max_minutes"]) * 60.0

    async def step(self) -> bool:
        """Un tour d'ordonnanceur : lance au plus un passage. Retourne True si un passage a eu lieu."""
        if not self._on or self._due() is None:
            self.waiting = ""
            return False
        async with self.bot.work:  # un seul passage à la fois (partagé avec le scan des planètes)
            feature = self._due()  # revérifié : la liste a pu changer pendant l'attente du verrou
            if feature is None or not self._on:
                return False
            reason = self._blocker()
            if reason:
                self.waiting = reason
                return False
            self.waiting = ""
            await self._run(feature)
        return True

    async def _attempt(self, feature: Feature) -> tuple[str, bool]:
        """Un essai du passage : bonus d'activité au début, puis la fonctionnalité."""
        try:
            claim = getattr(self.bot, "claim_bonus", None)
            if claim is not None:  # bonus d'activité récupéré au début de chaque action, quelle que soit la planète
                await claim()
            return await feature.run(self.bot), True
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - une fonctionnalité en échec ne doit pas arrêter les autres
            return f"erreur technique ({type(exc).__name__})", False

    async def _recovery_screen(self) -> bool:
        check = getattr(self.bot, "recovery_screen", None)
        return bool(check is not None and await check())

    async def _run(self, feature: Feature) -> None:
        """Passage d'une fonctionnalité. Si le jeu affiche « Récupération interrompue » à la fin, le bot attend 2 min,
        clique sur « Réessayer » et retente ; au bout de 3 essais tombés sur cet écran, il passe à la suite."""
        rt = self._runtime[feature.id]
        rt.running = True
        try:
            for attempt in range(1, RECOVERY_ATTEMPTS + 1):
                result, ok = await self._attempt(feature)
                if not await self._recovery_screen():
                    break
                if attempt == RECOVERY_ATTEMPTS:
                    result, ok = f"abandonné : écran « Récupération interrompue » {RECOVERY_ATTEMPTS} fois de suite", False
                    self.bot.log(f"« {feature.label} » : écran « Récupération interrompue » au {attempt}e essai ; "
                                 "passage à la fonctionnalité suivante.", "error", event=True, kind="attention")
                    break
                self.bot.log(f"« {feature.label} » : écran « Récupération interrompue » du jeu (essai {attempt}/{RECOVERY_ATTEMPTS}) ; "
                             f"nouvel essai dans {int(self._recovery_wait // 60)} min.", "warn", event=True)
                await asyncio.sleep(self._recovery_wait)
                if not self._on:  # bot passé sur OFF pendant l'attente : on s'arrête là
                    result, ok = "arrêté : écran « Récupération interrompue », bot passé sur OFF", False
                    break
                retry = getattr(self.bot, "retry_recovery", None)
                if retry is not None:
                    await retry()
        finally:
            rt.running = False
        rt.last_at, rt.last_result, rt.last_ok = time.time(), result, ok
        self.bot.log(f"Fonctionnalité « {feature.label} » : {result}.", "info" if ok else "error", event=True)
        # Le minuteur repart de la FIN du passage (laisse des créneaux aux fonctionnalités moins prioritaires).
        delay = self._self_timed_delay(feature) if feature.self_timed else self._draw_delay(feature.id)
        rt.next_at = self._clock() + delay if self.store.get(feature.id)["enabled"] else None

    def _self_timed_delay(self, feature: Feature) -> float:
        """Relance automatique : prochain passage à l'heure donnée par la fonctionnalité (fin de mission + délai du
        joueur), au plus tôt dans une minute, au plus tard à la vérification de secours."""
        try:
            due = feature.next_due(self.bot) if feature.next_due else None
        except Exception:  # noqa: BLE001 - heure illisible : vérification de secours
            due = None
        if due is None:
            return SELF_TIMED_FALLBACK_SECONDS
        return min(max(due - time.time(), SELF_TIMED_MIN_SECONDS), SELF_TIMED_FALLBACK_SECONDS)

    async def _loop(self) -> None:
        while self._on:
            try:
                ran = await self.step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.bot.log(f"Ordonnanceur : erreur technique ({type(exc).__name__}).", "error", event=True)
                ran = False
            if ran and self._on and self._due() is not None:
                await asyncio.sleep(self._rng.uniform(*self._gap))  # pause entre deux fonctionnalités consécutives
            elif not ran:
                await asyncio.sleep(self._tick)

    # ------------------------------------------------------------------ état affiché
    def summary(self) -> dict:
        now = self._clock()
        items = []
        for fid in self.store.order:
            f, rt, entry = self._features[fid], self._runtime[fid], self.store.get(fid)
            items.append({
                "id": fid,
                "label": f.label,
                "description": f.description,
                "timer_label": f.timer_label,
                "self_timed": f.self_timed,
                **entry,
                "running": rt.running,
                "next_in_s": None if rt.next_at is None else max(0.0, rt.next_at - now),
                "last_at": rt.last_at,
                "last_result": rt.last_result,
                "last_ok": rt.last_ok,
                "paused": self._paused(fid) if entry["enabled"] else None,  # hors de ses horaires propres
            })
        return {
            "bot_on": self._on,
            "stopping": not self._on and any(r.running for r in self._runtime.values()),
            "waiting": self.waiting,
            "persistent": self.store.persistent,
            "features": items,
        }
