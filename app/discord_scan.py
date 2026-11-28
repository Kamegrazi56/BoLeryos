"""Commande Discord /scan : relit dans le jeu tout ce dont les fonctionnalités activées ont besoin, une lecture après l'autre.

Chaque lecture existe déjà (boutons « Scanner » / « Relever » de l'interface) : on ne fait que les enchaîner. Les planètes
passent toujours en premier (les autres lectures s'appuient dessus) ; les autres ne sont faites que si une fonctionnalité qui
s'en sert est activée. Si le bot est en plein passage, chaque lecture attend son tour (le verrou du bot) au lieu d'échouer.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

SOURCE = "Discord"
BUSY_PREFIX = "Une fonctionnalité est en cours"
BUSY_TRIES, BUSY_WAIT = 12, 5.0  # jusqu'à une minute d'attente d'un passage en cours


@dataclass(frozen=True)
class Reading:
    label: str
    run: Callable[[object], Awaitable[dict]]  # reçoit le bot, rend le résultat de la lecture (clé « error » : "" si réussie)
    features: tuple[str, ...] = ()  # lue si l'une d'elles est activée ; vide : toujours lue
    when: Callable[[object], bool] = lambda bot: True  # condition en plus (ex. une planète de départ est choisie)


READINGS: tuple[Reading, ...] = (
    Reading("Planètes", lambda b: b.supervisor.scan_planets(SOURCE)),
    Reading("Modèles de vaisseaux", lambda b: b.supervisor.scan_models(SOURCE), ("ships",)),
    Reading("Inventaire (bâtiments, recherches, population)", lambda b: b.inventory_reader.refresh(SOURCE),
            ("constructions", "researches", "populations")),
    Reading("Effectifs de composants", lambda b: b.producer.survey(SOURCE), ("components",)),
    Reading("Piliers", lambda b: b.pillar.scan(SOURCE), ("pillars",)),
    Reading("Explorations", lambda b: b.explorer.scan(SOURCE), ("explorations",)),
    Reading("Fouilles", lambda b: b.excavator.scan(SOURCE), ("excavations",)),
    Reading("Piratage", lambda b: b.hacker.scan(SOURCE), ("hackings",), lambda b: bool(b.hackings.get()["planet"])),
    Reading("Flottes en vol", lambda b: b.fleet.scan(SOURCE), ("fleets",)),
)


def _wanted(bot, reading: Reading) -> bool:
    if reading.features and not any(bot.scheduler.store.get(f)["enabled"] for f in reading.features):
        return False
    return reading.when(bot)


async def _read(bot, reading: Reading) -> str:
    """Fait une lecture ; retourne l'erreur ("" si elle a réussi). Réessaie tant qu'un passage occupe le bot."""
    error = ""
    for attempt in range(BUSY_TRIES):
        try:
            result = await reading.run(bot)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - une lecture en échec ne doit pas empêcher les suivantes
            return f"erreur technique ({type(exc).__name__})"
        error = str((result or {}).get("error") or "")
        if not error.startswith(BUSY_PREFIX) or attempt == BUSY_TRIES - 1:
            break
        await asyncio.sleep(BUSY_WAIT)
    return error


async def scan_all(bot) -> str:
    """Enchaîne les lectures utiles et résume ce qui a réussi ou non. Rien n'est lu si le bot n'est pas dans une partie."""
    reason = bot.supervisor.game_blocker()
    if reason:
        return f"Scan impossible : {reason}."
    lines, failed = [], 0
    for reading in READINGS:
        if not _wanted(bot, reading):
            continue
        error = await _read(bot, reading)
        failed += bool(error)
        lines.append(f"{'❌' if error else '✅'} {reading.label}" + (f" : {error}" if error else ""))
    bot.log(f"Scan complet demandé par une commande Discord : {len(lines) - failed} lecture(s) réussie(s), {failed} en échec.", event=True)
    return "\n".join(lines)
