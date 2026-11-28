"""Commande Discord /pause : bot sur OFF pour une durée, puis remis sur ON tout seul.

La pause n'existe qu'en mémoire : si le conteneur redémarre, le bot reste sur OFF (c'est déjà la règle de l'application).
Un ordre « /bot demarrer » ou « /bot arreter » annule la pause en cours : le dernier ordre donné l'emporte.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Awaitable, Callable

from . import planning

MIN_MINUTES, MAX_MINUTES = 1, 720


class PauseTimers:
    def __init__(self, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self._sleep = sleep  # remplaçable dans les tests : on n'attend pas une vraie minute
        self._tasks: dict[str, asyncio.Task] = {}

    def start(self, key: str, bot, minutes: float, still_running: Callable[[], bool]) -> datetime:
        """Bot sur OFF maintenant (le passage en cours se termine), sur ON dans `minutes`. Retourne l'heure de reprise."""
        self.cancel(key)
        bot.scheduler.stop()
        resume_at = datetime.now(planning.TIMEZONE) + timedelta(minutes=minutes)
        self._tasks[key] = asyncio.create_task(self._resume(key, bot, minutes * 60.0, still_running))
        return resume_at

    def cancel(self, key: str) -> bool:
        task = self._tasks.pop(key, None)
        if task is None:
            return False
        task.cancel()
        return True

    def active(self, key: str) -> bool:
        return key in self._tasks

    async def _resume(self, key: str, bot, seconds: float, still_running: Callable[[], bool]) -> None:
        try:
            await self._sleep(seconds)
        except asyncio.CancelledError:
            return
        self._tasks.pop(key, None)
        if not still_running() or bot.scheduler.on:  # instance arrêtée entre-temps, ou déjà remis sur ON à la main
            return
        bot.scheduler.start()
        bot.log("Fin de la pause demandée par Discord : bot sur ON.", event=True)
