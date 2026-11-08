"""Missions : récupération des récompenses de l'onglet Missions, une fois par jour, juste avant que le bot s'arrête.

Relevé sur le vrai jeu (2026-11-08) : page `/game/asterion/missions`, onglets Missions / Quêtes / Événements ; blocs
Quotidiennes, Hebdomadaires, Mensuelles ; une mission atteinte affiche un bouton « Récupérer » (aria-label « Récupérer +5
Astrium, +5 XP ») ; un clic, sans confirmation, affiche « 5 Astrium et 5 XP réclamés. » et le bouton devient « Récupéré ».
Choix du joueur : seulement l'onglet Missions ; une fois par jour (date française), (A) quand le joueur met le bot sur OFF,
(B) quand le planning termine la dernière plage de la journée.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path

from . import human
from .builder import ConstructionPass
from .planets import GameUiError, Interrupted
from .planning import TIMEZONE

FILE_NAME = "missions.json"
MISSIONS_PATH = "missions"
PAGE_WAIT_SECONDS = 10.0
CLAIM_WAIT_SECONDS = 5.0
MAX_CLAIMS = 40  # sécurité : jamais plus de récompenses que de missions affichées
MARK = "data-bot-claim"

# Marque le premier bouton « Récupérer » visible et actif ; renvoie sa récompense (aria-label) ou null.
MARK_CLAIM_JS = """(attribute) => {
  document.querySelectorAll('[' + attribute + ']').forEach((e) => e.removeAttribute(attribute));
  const button = [...document.querySelectorAll('main button')].find((b) => b.getClientRects().length && !b.disabled
    && /^Récupérer$/i.test((b.textContent || '').replace(/\\s+/g, ' ').trim()));
  if (!button) return null;
  button.setAttribute(attribute, '1');
  return (button.getAttribute('aria-label') || 'Récupérer').replace(/^Récupérer\\s*/i, '') || 'récompense';
}"""

COUNT_CLAIMS_JS = """() => [...document.querySelectorAll('main button')].filter((b) => b.getClientRects().length && !b.disabled
  && /^Récupérer$/i.test((b.textContent || '').replace(/\\s+/g, ' ').trim())).length"""

LOADED_JS = """() => [...document.querySelectorAll('main *')].some((e) => !e.children.length && e.getClientRects().length
  && /^Quotidiennes$/i.test((e.textContent || '').trim()))"""

CLEAR_JS = "(attribute) => document.querySelectorAll('[' + attribute + ']').forEach((e) => e.removeAttribute(attribute))"


def today() -> str:
    return datetime.now(TIMEZONE).strftime("%Y-%m-%d")


class MissionsState:
    """Date (française) de la dernière récupération et son résultat, dans `missions.json`."""

    def __init__(self, data_dir: str | None) -> None:
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._path = folder / FILE_NAME if folder else None
        self._data = {"last_claim_day": "", "last_result": ""}
        if self._path is not None:
            try:
                saved = json.loads(self._path.read_text(encoding="utf8"))
                if isinstance(saved, dict):
                    self._data.update({k: str(saved.get(k, "")) for k in self._data})
            except (OSError, ValueError):
                pass

    def get(self) -> dict:
        return dict(self._data)

    def done_today(self) -> bool:
        return self._data["last_claim_day"] == today()

    def record(self, result: str) -> None:
        self._data = {"last_claim_day": today(), "last_result": result}
        if self._path:
            try:
                fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".missions-")
                with os.fdopen(fd, "w", encoding="utf8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self._path)
            except OSError:
                pass


class MissionClaimer(ConstructionPass):
    """Clique sur chaque « Récupérer » de l'onglet Missions (Quêtes et Événements ne sont pas touchés)."""

    def __init__(self, bot, state: MissionsState) -> None:
        super().__init__(bot)
        self.state = state

    async def claim_daily(self, reason: str) -> str | None:
        """Récupère les récompenses si ce n'est pas déjà fait aujourd'hui. L'appelant tient `bot.work`. Ne lève jamais."""
        if self.state.done_today():
            return None
        try:
            rewards = await self._claim_all()
        except (Interrupted, GameUiError) as exc:
            self.bot.log(f"Missions : récupération impossible avant {reason} ({exc}).", "warn", event=True)
            return None
        except Exception as exc:  # noqa: BLE001 — l'arrêt du bot ne doit jamais échouer à cause des missions
            self.bot.log(f"Missions : erreur pendant la récupération ({type(exc).__name__}).", "error", event=True)
            return None
        result = f"{len(rewards)} récompense(s) récupérée(s)" + (" : " + " ; ".join(rewards) if rewards else "")
        self.state.record(result)
        self.bot.log(f"Missions (avant {reason}) : {result}.", event=True, kind="launch" if rewards else "info")
        return result

    async def _open(self) -> None:
        page = self.page
        await self._goto(MISSIONS_PATH)
        end = time.monotonic() + PAGE_WAIT_SECONDS
        tab = page.get_by_role("tab", name=re.compile(r"^Missions", re.I))
        while time.monotonic() < end and not await tab.count():
            await asyncio.sleep(0.25)
        if not await tab.count():
            raise GameUiError("onglet « Missions » introuvable")
        if await tab.first.get_attribute("aria-selected") != "true":
            await self._guard()
            await human.click(page, tab.first, self.settings)
        while time.monotonic() < end:
            if await self._eval(LOADED_JS):
                await asyncio.sleep(1.0)  # les compteurs et boutons arrivent avec les blocs
                return
            await asyncio.sleep(0.25)
        raise GameUiError("la liste des missions ne s'affiche pas")

    async def _claim_all(self) -> list[str]:
        page = self.page
        await self._open()
        rewards: list[str] = []
        for _ in range(MAX_CLAIMS):
            before = await self._eval(COUNT_CLAIMS_JS)
            reward = await page.evaluate(MARK_CLAIM_JS, MARK)
            if reward is None:
                break
            try:
                await self._guard()
                await human.click(page, page.locator(f"[{MARK}]").first, self.settings)
            finally:
                await page.evaluate(CLEAR_JS, MARK)
            end = time.monotonic() + CLAIM_WAIT_SECONDS
            while time.monotonic() < end and await self._eval(COUNT_CLAIMS_JS) >= before:
                await asyncio.sleep(0.25)
            if await self._eval(COUNT_CLAIMS_JS) >= before:
                self.bot.log(f"Missions : clic sur « Récupérer » ({reward}) sans effet visible.", "warn", event=True)
                break
            rewards.append(reward)
            await human.micro(self.settings)
        return rewards
