"""Pause du bot : OFF pour une durée, puis ON tout seul (bouton de la barre du haut, commande Discord /pause).

Une pause appartient à l'ordonnanceur de l'instance : l'interface web et Discord partagent donc le même état.
Elle n'existe qu'en mémoire : si le conteneur redémarre, le bot reste sur OFF (c'est déjà la règle de l'application).
Tout ordre « ON » ou « OFF » donné entre-temps, comme l'arrêt de l'instance, annule la pause : le dernier ordre l'emporte.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from . import planning

MIN_MINUTES, MAX_MINUTES = 1, 720


class PauseError(Exception):
    """Pause refusée (durée hors limites, bot déjà sur OFF) ; le message est affichable tel quel."""


class BotPause:
    def __init__(self, scheduler, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self._scheduler = scheduler
        self._sleep = sleep  # remplaçable dans les tests : on n'attend pas une vraie minute
        self._task: asyncio.Task | None = None
        self._resume_at: datetime | None = None

    def start(self, minutes: int) -> datetime:
        """Bot sur OFF maintenant (le passage en cours se termine), sur ON dans `minutes`. Retourne l'heure de reprise."""
        if not MIN_MINUTES <= minutes <= MAX_MINUTES:
            raise PauseError(f"Durée refusée : de {MIN_MINUTES} à {MAX_MINUTES} minutes.")
        if not self._scheduler.on:
            raise PauseError("Le bot est déjà sur OFF : rien à mettre en pause.")
        self._scheduler.stop()  # annule aussi une pause précédente
        self._resume_at = datetime.now(planning.TIMEZONE) + timedelta(minutes=minutes)
        self._task = asyncio.create_task(self._resume(minutes * 60.0))
        return self._resume_at

    def cancel(self) -> bool:
        task, self._task, self._resume_at = self._task, None, None
        if task is None:
            return False
        task.cancel()
        return True

    @property
    def active(self) -> bool:
        return self._task is not None

    def info(self) -> dict | None:
        """Pour l'affichage : heure de reprise (heure française) et secondes restantes ; None hors pause."""
        if self._resume_at is None:
            return None
        left = (self._resume_at - datetime.now(planning.TIMEZONE)).total_seconds()
        return {"until": f"{self._resume_at:%H:%M}", "left_s": max(0.0, left)}

    async def _resume(self, seconds: float) -> None:
        try:
            await self._sleep(seconds)
        except asyncio.CancelledError:
            return
        self._task = self._resume_at = None  # avant start() : il annulerait sinon cette tâche
        if self._scheduler.on:  # déjà remis sur ON à la main
            return
        self._scheduler.start()
        self._scheduler.bot.log("Fin de la pause : bot sur ON.", event=True)
