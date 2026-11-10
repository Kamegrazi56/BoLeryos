"""Piliers programmés dans le jeu : fenêtre « Gérer les Piliers » (Opérations), lecture de l'état et changements.

Relevé sur le vrai jeu (2026-11-10, essai autorisé : réouverture d'Aurore-Bis, fermeture de 2-69-Mnesis) :
  - Opérations › bouton « Gérer les Piliers » → [role=dialog][aria-label="Gérer les Piliers"], une ligne
    li[aria-label=<planète>] par planète ; colonne Piliers = [role=group][aria-label="Piliers"] avec un bouton :
    « DÉSACTIVER » (title « Désactiver les Piliers (protège contre les attaques terrestres) ») si les Piliers sont actifs,
    « ACTIVER » (title « Réactiver les Piliers (permet les missions terrestres) ») s'ils sont désactivés ;
  - clic → fenêtre [role=dialog][aria-label="ACTIVER les Piliers — <planète>"] (ou « DÉSACTIVER … ») : « Êtes-vous sûr…
    Cette action prendra 1 heure… 23 heures… », boutons « Annuler » et « ACTIVER » (ou « DÉSACTIVER ») ;
  - confirmé : le bouton est remplacé par un bloc title « Changement de statut en cours... » et un compte à rebours
    (« 59 min 48 s ») ; aucun message.
Le bot ne touche JAMAIS à la colonne Champ de force (« Champ de force des Piliers »), ni à « Mobilisation générale ».
"""

from __future__ import annotations

import asyncio
import re
import time

from . import human
from .builder import ConstructionPass
from .pillars import ACTIONS, EFFECT_SECONDS, LATE_SECONDS, click_time, when
from .planets import GameUiError, Interrupted

PILLARS_PATH = "operations?method=PILLARS"
MANAGER = "Gérer les Piliers"
MARK = "data-bot-pillar"
PAGE_WAIT_SECONDS = 10.0
CONFIRM_WAIT_SECONDS = 10.0
VERBS = {"open": "ACTIVER", "close": "DÉSACTIVER"}

_ROW_JS = """
const text = (e) => (e.textContent || '').replace(/\\s+/g, ' ').trim();
const manager = () => [...document.querySelectorAll('[role=dialog]')]
  .find((d) => d.getAttribute('aria-label') === 'Gérer les Piliers' && d.getClientRects().length) || null;
function rowState(li) {
  const group = li.querySelector('[role=group][aria-label="Piliers"]');
  if (!group) return null;
  const button = group.querySelector('button');
  const pending = [...group.querySelectorAll('[title]')].find((e) => e.tagName !== 'BUTTON' && /Changement de statut/i.test(e.title));
  // La colonne Champ de force dit si les Piliers sont actifs : « Les Piliers doivent être actifs… » = désactivés.
  const field = [...li.querySelectorAll('button')].find((b) => /champ de force/i.test(b.title || ''));
  const closedNow = field ? /doivent être actifs/i.test(field.title) : null;
  if (pending) return { pending: text(pending), closedNow, button: null };
  if (!button) return null;
  const title = button.title || '';
  return { pending: null, closedNow: /^Réactiver/i.test(title), button: title };
}
"""

# État de chaque planète : [{name, pending (texte du compte à rebours) | null, closedNow, button (title) | null}] ; null si
# la fenêtre n'est pas affichée.
ROWS_JS = "() => {" + _ROW_JS + """
  const d = manager();
  if (!d) return null;
  return [...d.querySelectorAll('li[aria-label]')].map((li) => ({ name: li.getAttribute('aria-label'), ...(rowState(li) || {}) }));
}"""

# Marque le bouton Piliers de la planète pour l'action voulue. Rend « marked », « pending » (changement déjà en cours),
# « same » (déjà dans l'état voulu), « missing » (planète ou colonne introuvable).
MARK_JS = "([planet, action, attr]) => {" + _ROW_JS + """
  document.querySelectorAll('[' + attr + ']').forEach((e) => e.removeAttribute(attr));
  const d = manager();
  const li = d && [...d.querySelectorAll('li[aria-label]')].find((x) => x.getAttribute('aria-label') === planet);
  const group = li && li.querySelector('[role=group][aria-label="Piliers"]');
  if (!group) return 'missing';
  const state = rowState(li);
  if (!state) return 'missing';
  if (state.pending) return 'pending';
  const wanted = action === 'open' ? /^Réactiver les Piliers/i : /^Désactiver les Piliers/i;
  const button = group.querySelector('button');
  if (!button || !wanted.test(button.title || '')) return 'same';
  button.setAttribute(attr, '1');
  return 'marked';
}"""

MESSAGES_JS = """() => [...document.querySelectorAll('[role=status], [role=alert], [data-sonner-toast], .toast, [class*=toast]')]
  .filter((e) => e.getClientRects().length).map((e) => (e.textContent || '').replace(/\\s+/g, ' ').trim()).filter(Boolean).slice(-3)"""


def parse_left(text: str | None) -> int | None:
    """« 58 min 36 s », « 1 h 2 min », « 45 s » → secondes ; None si illisible."""
    if not text:
        return None
    total, found = 0, False
    for amount, unit in re.findall(r"(\d+)\s*(h|min|s)\b", text.lower()):
        total += int(amount) * {"h": 3600, "min": 60, "s": 1}[unit]
        found = True
    return total if found else None


def read_status(rows: list[dict], now: float) -> dict:
    """Lignes de la fenêtre → {planète: {"closed", "pending_to", "effective_at"}}."""
    planets = {}
    for r in rows:
        if not r.get("name") or (r.get("button") is None and r.get("pending") is None):
            continue
        if r.get("pending") is not None:
            left = parse_left(r["pending"])
            closed = bool(r.get("closedNow"))
            planets[r["name"]] = {"closed": closed, "pending_to": "open" if closed else "close",
                                  "effective_at": now + left if left is not None else None}
        else:
            planets[r["name"]] = {"closed": bool(r.get("closedNow")), "pending_to": None, "effective_at": None}
    return planets


class PillarPass(ConstructionPass):
    """Fait les changements programmés dont l'heure de clic (1 h avant l'heure effective) est arrivée."""

    feature_id = "pillars"

    def __init__(self, bot, store) -> None:
        super().__init__(bot)
        self.store = store
        self.scanning = False

    def next_due(self) -> float | None:
        """Prochaine heure de clic (epoch) ; None : aucun changement programmé."""
        return self.store.next_click()

    # ------------------------------------------------------------------ fenêtre « Gérer les Piliers »
    async def _open_manager(self) -> list[dict]:
        page = self.page
        if not await page.evaluate(ROWS_JS):
            await self._goto(PILLARS_PATH)
            button = page.get_by_role("button", name=MANAGER, exact=True)
            end = time.monotonic() + PAGE_WAIT_SECONDS
            while not await button.count() and time.monotonic() < end:
                await asyncio.sleep(0.25)
            if not await button.count():
                raise GameUiError(f"bouton « {MANAGER} » introuvable")
            await self._guard()
            await human.click(page, button.first, self.settings)
        end = time.monotonic() + PAGE_WAIT_SECONDS
        while time.monotonic() < end:
            rows = await page.evaluate(ROWS_JS)
            if rows:
                return rows
            await asyncio.sleep(0.25)
        raise GameUiError(f"la fenêtre « {MANAGER} » ne s'affiche pas")

    async def _close_manager(self) -> None:
        dialog = self.page.locator(f'[role=dialog][aria-label="{MANAGER}"]')
        close = dialog.get_by_role("button", name="Fermer", exact=True)
        if await close.count():
            await human.click(self.page, close.last, self.settings)

    async def _save_status(self) -> dict:
        planets = read_status(await self.page.evaluate(ROWS_JS) or [], time.time())
        if planets:
            self.store.set_status(planets)
        return planets

    async def scan(self) -> dict:
        """Relève l'état des Piliers à la demande. Ne lève jamais : renvoie {"error": raison} en cas d'échec."""
        if self.scanning:
            return {"error": "relevé déjà en cours"}
        busy = "une fonctionnalité est en cours, réessayez dans un instant" if self.bot.work.locked() else None
        reason = self.bot.supervisor.game_blocker() or busy
        if reason:
            return {"error": reason[0].upper() + reason[1:]}
        self.scanning = True
        try:
            async with self.bot.work:  # jamais en même temps qu'un passage de fonctionnalité
                await self.bot.claim_bonus()
                await self._open_manager()
                planets = await self._save_status()
                await self._close_manager()
                return {"error": "", "planets": len(planets)}
        except (Interrupted, GameUiError) as exc:
            self.bot.log(f"Piliers : relevé impossible ({exc}).", "warn", event=True)
            return {"error": f"Relevé impossible : {exc}"}
        finally:
            self.scanning = False

    # ------------------------------------------------------------------ passage
    async def run_pass(self) -> str:
        now = time.time()
        due = [c for c in self.store.pending() if click_time(c) <= now]
        if not due:
            nxt = self.store.next_click()
            return "aucun changement des Piliers à faire maintenant" + (f" (prochain clic le {when(nxt)})" if nxt else "")
        done: list[str] = []
        try:
            for change in due:
                if time.time() - click_time(change) > LATE_SECONDS:
                    note = f"abandonné : le bot n'a pas pu cliquer à temps (clic prévu le {when(click_time(change))}, plus d'1 h de retard)"
                    self.store.finish(change["id"], "abandoned", note)
                    self.bot.log(f"Piliers : {self._what(change)} {note}.", "warn", event=True, kind="attention")
                    continue
                await self._open_manager()
                done.append(await self._apply(change))
            if done:
                await self._save_status()
                await self._close_manager()
        except Interrupted as exc:
            return " ; ".join([*done, f"interrompu ({exc})"])
        except GameUiError as exc:
            self.bot.log(f"Piliers : {exc}.", "warn", event=True)
            return " ; ".join([*done, f"arrêté ({exc})"])
        return " ; ".join(done) or "changements en retard abandonnés"

    @staticmethod
    def _what(change: dict) -> str:
        return f"{ACTIONS[change['action']].lower()} les Piliers de {change['planet']} (effectif prévu le {when(change['at'])})"

    async def _fail(self, change: dict, reason: str) -> str:
        self.store.finish(change["id"], "failed", reason)
        self.bot.log(f"Piliers : impossible de {self._what(change)} : {reason}.", "warn", event=True, kind="attention")
        return f"{change['planet']} : échec ({reason})"

    async def _apply(self, change: dict) -> str:
        """Clique sur le bouton Piliers de la planète, confirme, puis attend le compte à rebours du jeu."""
        page, planet, action = self.page, change["planet"], change["action"]
        state = await page.evaluate(MARK_JS, [planet, action, MARK])
        if state == "missing":
            return await self._fail(change, "planète introuvable dans « Gérer les Piliers »")
        if state == "pending":
            return await self._fail(change, "un changement de statut est déjà en cours sur cette planète")
        if state == "same":
            return await self._fail(change, f"les Piliers sont déjà {'ouverts' if action == 'open' else 'fermés'}")
        verb = VERBS[action]
        await self._guard()
        await human.click(page, page.locator(f"[{MARK}]").first, self.settings)
        confirm = page.locator(f'[role=dialog][aria-label="{verb} les Piliers — {planet}"]')
        end = time.monotonic() + CONFIRM_WAIT_SECONDS
        while not await confirm.count() and time.monotonic() < end:
            await asyncio.sleep(0.2)
        if not await confirm.count():
            return await self._fail(change, "la fenêtre de confirmation ne s'est pas affichée")
        await self._guard()
        await human.think(self.settings)
        await human.click(page, confirm.get_by_role("button", name=verb, exact=True).first, self.settings)
        clicked = time.time()
        messages: list[str] = []  # un refus du jeu s'affiche quelques secondes seulement : relevé pendant toute l'attente
        end = time.monotonic() + CONFIRM_WAIT_SECONDS
        while time.monotonic() < end:
            messages += [m for m in await page.evaluate(MESSAGES_JS) if m not in messages]
            if await page.evaluate(MARK_JS, [planet, action, MARK]) == "pending":
                rows = await page.evaluate(ROWS_JS) or []
                left = parse_left(next((r.get("pending") for r in rows if r.get("name") == planet), None))
                effective = clicked + (left if left is not None else EFFECT_SECONDS)
                note = f"cliqué le {when(clicked)}, effectif le {when(effective)}"
                self.store.finish(change["id"], "done", note, effective_at=effective)
                self.bot.log(f"Piliers : {ACTIONS[action].lower()} {planet} — {note}.", event=True, kind="launch")
                return f"{planet} : {ACTIONS[action].lower()}, effectif le {when(effective)}"
            await asyncio.sleep(0.3)
        cancel = confirm.get_by_role("button", name="Annuler", exact=True)
        if await confirm.count() and await cancel.count():
            await human.click(page, cancel.first, self.settings)  # fenêtre restée ouverte : on n'insiste pas
        return await self._fail(change, "le jeu n'a pas lancé le changement" + (f" ({' ; '.join(messages)})" if messages else ""))
