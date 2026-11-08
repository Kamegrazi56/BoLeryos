"""Alliance : dons de jetons à la technologie prioritaire de l'alliance.

Relevé sur le vrai jeu (2026-11-08) : Alliance › Techs (`/game/asterion/alliance`, onglet « Techs ») montre « Jetons d'alliance
N /6 » (un jeton toutes les 2 h environ) et les cartes des technologies. Seule la technologie marquée « priorité de dons de
l'alliance » accepte des jetons : son étoile porte l'aria-label « Retirer <Nom> de la priorité de dons de l’alliance » et ses
boutons « +1 jeton » et « Max. (+N) » sont actifs (grisés partout ailleurs). Un clic sur « +1 jeton » donne un jeton sans
confirmation : message « Don effectué : 1 jeton(s). », jetons 5/6 → 4/6, progression 47 → 48/300.
Le bot ne clique JAMAIS sur une étoile (changer la priorité) ni sur une carte (fiche de présentation).
Réglage (`<data_dir>/alliance.json`) : nombre de jetons à donner à chaque passage — "all" (tous), 1 ou 2.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import tempfile
import time
from pathlib import Path

from . import human
from .builder import ConstructionPass
from .planets import GameUiError, Interrupted

FILE_NAME = "alliance.json"
AMOUNTS = ("all", 1, 2)
DEFAULT_SETTINGS: dict = {"amount": "all"}
ALLIANCE_PATH = "alliance"
PAGE_WAIT_SECONDS = 10.0
CONFIRM_SECONDS = 6.0
MARK = "data-bot-token"


class AllianceError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(" ; ".join(errors))
        self.errors = errors


def validate(data) -> dict:
    if not isinstance(data, dict):
        raise AllianceError(["Réglages d'alliance illisibles."])
    amount = data.get("amount", "all")
    if isinstance(amount, bool) or amount not in AMOUNTS:
        raise AllianceError(["Jetons à donner : tous, 1 ou 2."])
    return {"amount": amount}


class AllianceSettings:
    def __init__(self, data_dir: str | None) -> None:
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._path = folder / FILE_NAME if folder else None
        self._data = copy.deepcopy(DEFAULT_SETTINGS)
        if self._path is not None:
            try:
                self._data = validate(json.loads(self._path.read_text(encoding="utf8")))
            except (OSError, ValueError, AllianceError):
                pass

    @property
    def persistent(self) -> bool:
        return self._path is not None

    def get(self) -> dict:
        return copy.deepcopy(self._data)

    def replace(self, data) -> dict:
        self._data = validate(data)
        if self._path:
            try:
                fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".alliance-")
                with os.fdopen(fd, "w", encoding="utf8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self._path)
            except OSError:
                pass
        return self.get()


_PRELUDE_JS = """
const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
const visible = (el) => el.getClientRects().length > 0;
"""

# Jetons disponibles : « Jetons d'alliance » puis « N /6 ».
TOKENS_JS = (
    "() => {"
    + _PRELUDE_JS
    + """
  const label = [...document.querySelectorAll('main *')].find((e) => !e.children.length && visible(e) && /^Jetons d.alliance$/i.test(text(e)));
  let box = label;
  while (box && !/\\d+\\s*\\/\\s*\\d+/.test(text(box).replace(/^Jetons d.alliance/i, ''))) box = box.parentElement;
  if (!box) return null;
  const m = /(\\d+)\\s*\\/\\s*(\\d+)/.exec(text(box).replace(/^Jetons d.alliance/i, ''));
  return m ? [Number(m[1]), Number(m[2])] : null;
}"""
)

# Technologie prioritaire : nom, progression, et marque le bouton demandé (« +1 jeton » ou « Max. »).
PRIORITY_JS = (
    "([kind, attribute]) => {"
    + _PRELUDE_JS
    + """
  document.querySelectorAll('[' + attribute + ']').forEach((e) => e.removeAttribute(attribute));
  const star = [...document.querySelectorAll('main button[aria-label]')].find((b) => visible(b)
    && /^Retirer .+ de la priorité de dons/i.test(b.getAttribute('aria-label')));
  if (!star) return null;
  const name = /^Retirer (.+?) de la priorité de dons/i.exec(star.getAttribute('aria-label'))[1];
  let card = star;
  while (card && ![...card.querySelectorAll('button')].some((b) => /^\\+1 jeton$/i.test(text(b)))) card = card.parentElement;
  if (!card) return { name, button: false };
  const progress = (/Progression\\s*(\\d+\\s*\\/\\s*\\d+)/.exec(text(card)) || [])[1] || '';
  const wanted = [...card.querySelectorAll('button')].find((b) => visible(b) && (kind === 'max' ? /^Max\\./i.test(text(b)) : /^\\+1 jeton$/i.test(text(b))));
  if (!wanted || wanted.disabled) return { name, progress, button: false };
  wanted.setAttribute(attribute, '1');
  return { name, progress, button: true };
}"""
)

CLEAR_JS = "(attribute) => document.querySelectorAll('[' + attribute + ']').forEach((e) => e.removeAttribute(attribute))"


class TokenPass(ConstructionPass):
    """Donne des jetons d'alliance à la technologie prioritaire (tous, 1 ou 2 à chaque passage)."""

    feature_id = "alliance_tokens"

    def __init__(self, bot, store) -> None:
        super().__init__(bot)
        self.store = store
        self._tech = ""  # nom de la technologie prioritaire vue au dernier don

    async def _open_techs(self) -> None:
        page = self.page
        await self._goto(ALLIANCE_PATH)  # toujours rechargée : compteur de jetons et priorité à jour
        end = time.monotonic() + PAGE_WAIT_SECONDS
        tab = page.get_by_role("tab", name=re.compile(r"Techs", re.I))
        while time.monotonic() < end and not await tab.count():
            await asyncio.sleep(0.25)
        if not await tab.count():
            raise GameUiError("onglet « Techs » de l'alliance introuvable (le joueur est-il dans une alliance ?)")
        if await tab.first.get_attribute("aria-selected") != "true":
            await self._guard()
            await human.click(page, tab.first, self.settings)
        while time.monotonic() < end:
            if await self._eval(TOKENS_JS):
                await asyncio.sleep(0.5)  # les cartes arrivent avec le compteur
                return
            await asyncio.sleep(0.25)
        raise GameUiError("le compteur de jetons d'alliance ne s'affiche pas")

    async def run_pass(self) -> str:
        amount = self.store.get()["amount"]
        try:
            await self._open_techs()
            tokens = await self._eval(TOKENS_JS)
            if not tokens or tokens[0] <= 0:
                return "aucun jeton d'alliance disponible"
            if amount == "all":
                return await self._give("max", tokens[0], tokens[0])
            given, last = 0, ""
            for _ in range(min(int(amount), tokens[0])):
                last = await self._give("one", 1, (await self._eval(TOKENS_JS) or [0])[0])
                if not last.startswith("1 jeton"):
                    break
                given += 1
                await human.think(self.settings)
            return f"{given} jeton(s) donné(s)" + (f" ({last})" if given == 0 else f" à {self._tech}")
        except Interrupted as exc:
            return f"interrompu ({exc})"
        except GameUiError as exc:
            self.bot.log(f"Alliance : {exc}.", "warn", event=True)
            return f"arrêté ({exc})"

    async def _give(self, kind: str, expected: int, before: int) -> str:
        """Clique « +1 jeton » ou « Max. » sur la technologie prioritaire ; vérifie que le compteur baisse."""
        page = self.page
        info = await page.evaluate(PRIORITY_JS, ["max" if kind == "max" else "one", MARK])
        if info is None:
            return "aucune technologie n'est la priorité de dons de l'alliance"
        self._tech = info["name"]
        if not info["button"]:
            return f"{info['name']} n'accepte pas de jeton (niveau maximal ?)"
        try:
            await self._guard()
            await human.click(page, page.locator(f"[{MARK}]").first, self.settings)
        finally:
            await page.evaluate(CLEAR_JS, MARK)
        end = time.monotonic() + CONFIRM_SECONDS
        while time.monotonic() < end:
            now = await self._eval(TOKENS_JS)
            if now and now[0] < before:
                given = before - now[0]
                self.bot.log(f"Alliance : {given} jeton(s) donné(s) à {info['name']} (jetons {now[0]}/{now[1]}).", event=True, kind="launch")
                return f"{given} jeton(s) donné(s) à {info['name']}"
            await asyncio.sleep(0.3)
        self.bot.log(f"Alliance : clic sur « {'Max.' if kind == 'max' else '+1 jeton'} » fait mais le compteur de jetons n'a pas bougé.", "warn", event=True, kind="attention")
        return "don non confirmé (le compteur de jetons n'a pas bougé)"
