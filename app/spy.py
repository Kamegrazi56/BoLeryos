"""Campagne d'espionnage des Errants par drone (Piliers), lancée à la main depuis les Réglages.

Relevé sur le vrai jeu (2026-11-07) :
  - Galaxie : liste « Fenêtre visible / Vue d'ensemble » (lignes `.continuous-inspector__list-row`, 25 par page, bouton
    « Afficher la suite ») ; un Errant porte « [AI] » ; boutons aria-label Flotte / Piliers / Sonde / Drone (Drone grisé : cible
    hors du ratio ×2). Zoom : curseur `input[type=range][aria-label="Niveau de zoom"]` en log2 (×17,5 ≈ 4,13).
  - Clic sur Drone : soit un message de quelques secondes « Impossible d'atteindre la cible : les piliers de la colonie de
    destination sont désactivés. », soit la fenêtre `[role=dialog][aria-label="Espionnage"]` « Rapport de drone : <planète> »
    (Fer / Cristal / Éther suivis du nombre, boutons « Fermer le message » et « Supprimer le message », suppression immédiate).
Le bot ne fait QUE cliquer sur Drone, lire le rapport, puis le garder ou le supprimer selon les minimums réglés.
"""

from __future__ import annotations

import asyncio
import math
import re
import time
from datetime import datetime

from . import human
from .builder import ConstructionPass
from .numbers import parse_amount
from .planets import GameUiError, Interrupted
from .planning import TIMEZONE
from .spying import FACTIONS, RESOURCES, report_matches

ZOOM = 17.5
ZOOM_LEVEL = round(math.log2(ZOOM), 2)  # valeur du curseur « Niveau de zoom » (échelle log2)
PAGE_WAIT_SECONDS = 10.0
LIST_WAIT_SECONDS = 2.0  # la liste se met à jour après le zoom
MORE_LABEL = "Afficher la suite"
MAX_MORE_CLICKS = 80  # 25 lignes par page : 2 000 lignes au plus
OUTCOME_SECONDS = 8.0  # attente du rapport ou du message après le clic sur Drone
DIALOG_GONE_SECONDS = 3.0
MESSAGE_GONE_SECONDS = 8.0  # un message du jeu reste environ 3 s
REFUSAL_LIMIT = 3  # refus de suite (autres que les piliers fermés) : drones ou ressources épuisés, la campagne s'arrête
MARK = "data-bot-spy"
CLOSED_RE = re.compile(r"piliers.*désactivés", re.I)

_PRELUDE_JS = """
const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
const ADDRESS = /[A-Z]\\d{2} \\d{2} \\d{2}/;
const rows = () => [...document.querySelectorAll('.continuous-inspector__list-row')];
const errant = (row) => /\\[AI\\]/.test(text(row)) && ADDRESS.test(text(row));
const drone = (row) => row.querySelector('button[aria-label="Drone"]');
"""

TARGETS_JS = (
    "() => {"
    + _PRELUDE_JS
    + """
  const out = [];
  for (const row of rows().filter(errant)) {
    const address = ADDRESS.exec(text(row))[0];
    if (out.some((t) => t.address === address)) continue;
    const label = [...row.querySelectorAll('button')].map(text).find((t) => t.includes(address)) || '';
    const name = label.replace(address, '').replace(/[·\\s]+$/, '').trim();
    const button = drone(row);
    // faction : début du nom de l'image d'avatar (vu sur le vrai jeu, 2026-11-11 : « thumbs/noctes-01-executeur-48.webp »)
    const avatar = row.querySelector('img');
    const file = avatar ? (avatar.getAttribute('src') || '').split('?')[0].split('/').pop().toLowerCase() : '';
    const faction = (/^(noctes|veyrs|haldens)-/.exec(file) || [])[1] || '';
    out.push({ address, name, faction, drone: !!button && !button.disabled && button.getAttribute('aria-disabled') !== 'true' });
  }
  return out;
}"""
)

MARK_DRONE_JS = (
    "([address, attribute]) => {"
    + _PRELUDE_JS
    + """
  document.querySelectorAll('[' + attribute + ']').forEach((e) => e.removeAttribute(attribute));
  const row = rows().filter(errant).find((r) => ADDRESS.exec(text(r))[0] === address);
  const button = row && drone(row);
  if (!button || button.disabled) return false;
  button.setAttribute(attribute, '1');
  return true;
}"""
)

_MESSAGES_JS = """const messages = () => [...document.querySelectorAll('body *')]
  .filter((e) => !e.children.length && /^Impossible d/i.test((e.textContent || '').trim()))
  .map((e) => e.textContent.replace(/\\s+/g, ' ').trim());"""

# Messages « Impossible … » à l'écran avant le clic (un message encore affiché, même redessiné, n'est pas une réponse).
COUNT_MESSAGES_JS = "() => {" + _MESSAGES_JS + " return messages().length; }"

# Réponse au clic : rapport ouvert, ou un message « Impossible … » de plus qu'avant le clic.
OUTCOME_JS = (
    "(before) => {"
    + _MESSAGES_JS
    + """
  const dialog = document.querySelector('[role=dialog][aria-label="Espionnage"]');
  if (dialog && dialog.getClientRects().length) return { kind: 'report' };
  const now = messages();
  return now.length > before ? { kind: 'refused', text: now[now.length - 1] } : null;
}"""
)

REPORT_JS = """(names) => {
  const dialog = document.querySelector('[role=dialog][aria-label="Espionnage"]');
  if (!dialog) return null;
  const leaves = [...dialog.querySelectorAll('*')].filter((e) => !e.children.length).map((e) => (e.textContent || '').replace(/\\s+/g, ' ').trim()).filter(Boolean);
  const out = { title: leaves.find((t) => /^Rapport/i.test(t)) || '' };
  for (const name of names) {
    const at = leaves.indexOf(name);
    out[name] = at >= 0 ? (leaves.slice(at + 1).find((t) => /\\d/.test(t)) || null) : null;
  }
  return out;
}"""

DIALOG_OPEN_JS = "() => { const d = document.querySelector('[role=dialog][aria-label=\"Espionnage\"]'); return !!d && d.getClientRects().length > 0; }"

SET_ZOOM_JS = """(value) => {
  const input = document.querySelector('input[type=range][aria-label="Niveau de zoom"]');
  if (!input) return false;
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
  setter.call(input, String(value));
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
}"""

CLEAR_JS = "(attribute) => document.querySelectorAll('[' + attribute + ']').forEach((e) => e.removeAttribute(attribute))"


def _now() -> str:
    return datetime.now(TIMEZONE).strftime("%H:%M:%S")  # heure française, pas UTC


def read_amount(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        return parse_amount(raw)
    except ValueError:
        return None


def idle_status() -> dict:
    return {"running": False, "planet": "", "started": "", "finished": "", "total": 0, "spied": 0, "kept": 0,
            "deleted": 0, "out_of_range": 0, "errors": 0, "message": "",
            "skipped": {f: 0 for f in FACTIONS}, "unknown_faction": 0}  # Errants écartés : faction non cochée / illisible


def skipped_text(status: dict) -> str:
    """« , écartés : 4 Veyrs, 2 Haldens, 1 de faction illisible » (vide si aucun Errant écarté)."""
    parts = [f"{n} {FACTIONS[f]}" for f, n in status["skipped"].items() if n]
    if status["unknown_faction"]:
        parts.append(f"{status['unknown_faction']} de faction illisible")
    return f", écartés : {', '.join(parts)}" if parts else ""


class SpyCampaign(ConstructionPass):
    """Un drone vers chaque Errant visible autour de la planète choisie ; rapports gardés ou supprimés selon les minimums."""

    feature_id = "spying"

    def __init__(self, bot, store) -> None:
        super().__init__(bot)
        self.store = store
        self.closed: list[str] = []  # Errants aux piliers désactivés (remise à zéro à chaque lancement)
        self._status = idle_status()
        self._task: asyncio.Task | None = None
        self._cancel = False
        self._refusals = 0

    # ------------------------------------------------------------------ commandes
    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict:
        return {**self._status, "closed": list(self.closed)}

    def start(self) -> str | None:
        """Lance la campagne en tâche de fond ; renvoie la raison du refus, ou None. Le bot passe sur OFF."""
        if self.running:
            return "un espionnage est déjà en cours"
        settings = self.store.get()
        if not settings["planet"]:
            return "choisissez d'abord la planète autour de laquelle espionner"
        reason = self.bot.supervisor.game_blocker()
        if reason:
            return reason
        self.bot.scheduler.stop()  # les autres automatismes restent arrêtés jusqu'à ce que l'utilisateur les relance
        self.closed = []
        self._cancel = False
        self._refusals = 0
        self._status = {**idle_status(), "running": True, "planet": settings["planet"], "started": _now(),
                        "message": "en attente de la fin du passage en cours"}
        self._task = asyncio.create_task(self._run(settings))
        return None

    def stop(self) -> None:
        if self.running:
            self._cancel = True
            self._status["message"] = "arrêt demandé"

    async def shutdown(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _guard(self) -> None:
        if self._cancel:
            raise Interrupted("arrêté par l'utilisateur")
        await super()._guard()

    # ------------------------------------------------------------------ campagne
    async def _run(self, settings: dict) -> None:
        try:
            async with self.bot.work:  # jamais en même temps qu'un passage de fonctionnalité
                await self.bot.claim_bonus()
                self.bot.log(f"Espionnage : campagne lancée autour de {settings['planet']} (bot sur OFF).", event=True)
                await self._campaign(settings)
                self._status["message"] = "terminé"
        except Interrupted as exc:
            self._status["message"] = f"interrompu ({exc})"
        except GameUiError as exc:
            self._status["message"] = f"arrêté ({exc})"
            self.bot.log(f"Espionnage : {exc}.", "warn", event=True)
        except Exception as exc:  # noqa: BLE001 — une erreur imprévue ne doit pas laisser l'état « en cours »
            self._status["message"] = f"erreur ({exc})"
            self.bot.log(f"Espionnage : erreur imprévue ({exc}).", "error", event=True)
        finally:
            s = self._status
            s["running"], s["finished"] = False, _now()
            self.bot.log(f"Espionnage : {s['message']} — {s['kept']} cible(s) retenue(s), {s['deleted']} rapport(s) supprimé(s), "
                         f"{len(self.closed)} Errant(s) aux piliers fermés, {s['out_of_range']} Drone indisponible (ratio ×2)"
                         f"{skipped_text(s)}.", event=True)

    async def _campaign(self, settings: dict) -> None:
        targets = await self._collect(settings["planet"])
        self._status["total"] = len(targets)
        self._status["message"] = "espionnage en cours"
        for target in targets:
            if not self._faction_wanted(target, settings):
                continue  # aucun clic sur un Errant d'une faction non cochée
            if not target["drone"]:
                self._status["out_of_range"] += 1
                continue
            await self._spy(target, settings)
            await human.think(self.settings)

    def _faction_wanted(self, target: dict, settings: dict) -> bool:
        faction = target.get("faction") or ""
        if faction not in FACTIONS:  # avatar absent ou inattendu : on ne l'espionne pas par erreur
            self._status["unknown_faction"] += 1
            label = target["name"] or target["address"]
            self.bot.log(f"Espionnage : faction de {label} ({target['address']}) illisible, Errant passé.", "warn", event=True)
            return False
        if faction not in settings["factions"]:
            self._status["skipped"][faction] += 1
            return False
        return True

    async def _collect(self, planet: str) -> list[dict]:
        """Galaxie centrée sur la planète, zoom ×17,5, liste entièrement chargée : les Errants visibles."""
        self._status["message"] = "recherche des Errants"
        page = self.page
        if page.url.split("?")[0].rstrip("/").rsplit("/", 1)[-1] != "galaxy":
            await self._goto("galaxy")
        end = time.monotonic() + PAGE_WAIT_SECONDS
        while time.monotonic() < end and not await page.get_by_role("button", name="Recentrer la carte", exact=True).count():
            await asyncio.sleep(0.25)
        await self._select_planet(planet)
        await self._settle(planet)
        await self._click_named("Recentrer la carte")
        await self._guard()
        if not await page.evaluate(SET_ZOOM_JS, ZOOM_LEVEL):
            raise GameUiError("curseur « Niveau de zoom » introuvable")
        await asyncio.sleep(LIST_WAIT_SECONDS)
        await self._load_whole_list()
        await self._remember(planet)
        return await self._eval(TARGETS_JS)

    async def _load_whole_list(self) -> None:
        more = self.page.get_by_role("button", name=MORE_LABEL, exact=True)
        for _ in range(MAX_MORE_CLICKS):
            if not await more.count() or not await more.first.is_visible():
                return
            await self._guard()
            await human.click(self.page, more.first, self.settings)
            await asyncio.sleep(0.5)
        raise GameUiError("la liste de la Galaxie ne finit pas de se charger")

    # ------------------------------------------------------------------ un Errant
    async def _spy(self, target: dict, settings: dict) -> None:
        page = self.page
        label = target["name"] or target["address"]
        if await page.evaluate(DIALOG_OPEN_JS):  # un rapport resté ouvert masquerait la liste
            await self._close_report("Fermer le message")
        before = await self._messages_gone()  # d'abord : la page peut se redessiner quand le message disparaît
        if not await page.evaluate(MARK_DRONE_JS, [target["address"], MARK]):
            self._status["out_of_range"] += 1
            return
        try:
            await self._guard()
            await human.click(page, page.locator(f"[{MARK}]").first, self.settings)
        finally:
            await page.evaluate(CLEAR_JS, MARK)
        outcome = await self._outcome(before)
        if outcome is None:
            self._status["errors"] += 1
            self.bot.log(f"Espionnage : aucune réponse du jeu pour {label} ({target['address']}).", "warn", event=True)
            return
        if outcome["kind"] == "refused":
            self._refused(label, target["address"], outcome["text"])
            return
        self._refusals = 0
        self._status["spied"] += 1
        await self._sort_report(label, target["address"], settings)

    async def _messages_gone(self) -> int:
        """Attend que le message « Impossible … » précédent disparaisse : tant qu'il est affiché, le jeu n'en ajoute pas
        un identique (vu sur le vrai jeu : deux refus de suite, un seul message). Renvoie le nombre restant."""
        end = time.monotonic() + MESSAGE_GONE_SECONDS
        while True:
            count = await self.page.evaluate(COUNT_MESSAGES_JS)
            if not count or time.monotonic() >= end:
                return count
            await asyncio.sleep(0.25)

    async def _outcome(self, before: int) -> dict | None:
        end = time.monotonic() + OUTCOME_SECONDS
        while time.monotonic() < end:
            found = await self.page.evaluate(OUTCOME_JS, before)
            if found:
                return found
            await asyncio.sleep(0.2)
        return None

    def _refused(self, label: str, address: str, text: str) -> None:
        if CLOSED_RE.search(text):
            self._refusals = 0
            self.closed.append(f"{label} ({address})")
            return
        self._refusals += 1
        self._status["errors"] += 1
        self.bot.log(f"Espionnage : drone refusé pour {label} ({address}) : « {text} ».", "warn", event=True)
        if self._refusals >= REFUSAL_LIMIT:
            raise Interrupted(f"{REFUSAL_LIMIT} drones refusés de suite (dernier message : « {text} »)")

    async def _sort_report(self, label: str, address: str, settings: dict) -> None:
        raw = await self.page.evaluate(REPORT_JS, list(RESOURCES)) or {}
        amounts = {r: read_amount(raw.get(r)) for r in RESOURCES}
        if all(v is None for v in amounts.values()):  # rapport illisible : on ne supprime pas ce qu'on n'a pas lu
            self._status["errors"] += 1
            self._status["kept"] += 1
            self.bot.log(f"Espionnage : rapport de {label} illisible, gardé.", "warn", event=True)
            await self._close_report("Fermer le message")
            return
        if report_matches(amounts, settings):
            self._status["kept"] += 1
            details = ", ".join(f"{r} {int(v or 0):,}".replace(",", " ") for r, v in amounts.items())
            self.bot.log(f"Espionnage : {label} ({address}) retenu — {details}.", event=True, kind="launch")
            await self._close_report("Fermer le message")
        else:
            self._status["deleted"] += 1
            await self._close_report("Supprimer le message")

    async def _close_report(self, button_label: str) -> None:
        page = self.page
        button = page.locator(f'[role=dialog][aria-label="Espionnage"] button[aria-label="{button_label}"]')
        if not await button.count():
            raise GameUiError(f"bouton « {button_label} » du rapport introuvable")
        await self._guard()
        await human.click(page, button.first, self.settings)
        end = time.monotonic() + DIALOG_GONE_SECONDS
        while time.monotonic() < end:
            if not await page.evaluate(DIALOG_OPEN_JS):
                return
            await asyncio.sleep(0.2)
        raise GameUiError("le rapport d'espionnage ne se ferme pas")
