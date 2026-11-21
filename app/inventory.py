"""Inventaire de l'empire : niveaux des bâtiments de TOUTES les planètes, lus sur une seule page du jeu.

Page « Empire » › onglet « Inventaire » (relevée sur le vrai jeu le 2026-11-15) : un vrai tableau, une colonne par planète
(nom + coordonnées) puis « Total », une ligne par objet, les lignes de section n'ayant qu'une cellule. Le bot la lit en
lecture seule (il ne touche jamais aux champs « Ordre d'affichage » de l'en-tête) et garde le dernier relevé dans
`<data_dir>/inventory.json` pour l'onglet Constructions : plus besoin de parcourir les pages de chaque planète pour
connaître les niveaux.
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
from .constructions import BUILDINGS
from .numbers import norm
from .planets import GameUiError, Interrupted
from .populations import POPULATIONS
from .researches import RESEARCHES

FILE_NAME = "inventory.json"
TAB_NAME = "Inventaire"
READ_ATTEMPTS = 40  # le tableau arrive un instant après le clic sur l'onglet
READ_POLL_SECONDS = 0.25
RECLICK_AFTER = 12  # lectures sans tableau avant de recliquer une fois sur l'onglet (le premier clic peut ne pas avoir pris)

# Ce que la page contient quand le tableau manque : état des onglets et nombre de tableaux (aucun texte de jeu).
DIAGNOSE_INVENTORY_JS = r"""
() => {
  const text = (e) => (e.textContent || '').replace(/\s+/g, ' ').trim();
  const tabs = [...document.querySelectorAll('[role=tab]')].filter((t) => t.getClientRects().length)
    .map((t) => text(t) + '=' + (t.getAttribute('aria-selected') ?? 'aucun'));
  const tables = [...document.querySelectorAll('table')];
  return { tabs, tables: tables.length, visible: tables.filter((t) => t.getClientRects().length).length,
           heads: tables.filter((t) => t.tHead).length, grids: document.querySelectorAll('[role=grid], [role=table]').length };
}
"""
TAB_WAIT_SECONDS = 5.0

# Le tableau visible dont la première colonne est « Objet » : en-tête (texte + feuilles de chaque cellule) et lignes (texte
# de chaque cellule). Aucune valeur de champ n'est lue.
READ_INVENTORY_JS = r"""
() => {
  const text = (e) => (e.textContent || '').replace(/\s+/g, ' ').trim();
  const table = [...document.querySelectorAll('table')].find((t) => t.getClientRects().length && t.tHead && t.tHead.rows.length
    && text(t.tHead.rows[0].cells[0] || t).toLowerCase() === 'objet');
  if (!table) return null;
  const leaves = (e) => [...e.querySelectorAll('*')].filter((x) => x.children.length === 0 && text(x) !== '').map(text);
  const head = [...table.tHead.rows[0].cells].map((c) => ({ text: text(c), leaves: leaves(c) }));
  const rows = [...table.tBodies].flatMap((b) => [...b.rows]).map((r) => [...r.cells].map(text));
  return { head, rows };
}
"""

_IDS = {norm(b.label): b.id for b in BUILDINGS}
_RESEARCH_IDS = {norm(r.label): r.id for r in RESEARCHES}
_POPULATION_IDS = {norm(p.label): p.id for p in POPULATIONS}  # le Soldat (automatique) n'y est pas
_COORDS_RE = re.compile(r"^(.*?)\s*([A-Z]\d{2} \d{2} \d{2})$")


def _split_planet(cell: dict) -> tuple[str, str]:
    """(nom, coordonnées) d'un en-tête de colonne : les deux feuilles de la cellule, sinon le texte « NomM69 38 06 » coupé."""
    leaves = [x for x in cell.get("leaves", []) if x]
    if len(leaves) >= 2:
        return leaves[0], leaves[1]
    text = str(cell.get("text", "")).strip()
    found = _COORDS_RE.match(text)
    return (found.group(1), found.group(2)) if found else (text, "")


def _level(cell: str) -> int | None:
    digits = re.sub(r"\s", "", str(cell))  # \s couvre aussi les espaces insécables du jeu
    return int(digits) if digits.isdecimal() else None  # « — » : pas de niveau ; isdecimal : « ² » n'est pas convertible


def parse_inventory(raw: dict) -> list[dict]:
    """Planètes dans l'ordre du jeu : [{"name", "coords", "levels": {id du bâtiment: niveau}, "units": {id de la population: effectif}}].
    Colonnes repérées par leur en-tête (jamais par leur rang : le joueur peut les réordonner) ; lignes tronquées écartées.
    Lève GameUiError si le tableau n'est pas celui de l'inventaire ou ne contient aucun bâtiment."""
    head = raw.get("head") or []
    if not head or norm(head[0].get("text", "")) != "objet":
        raise GameUiError("colonne « Objet » introuvable dans le tableau de l'inventaire")
    end = next((i for i, cell in enumerate(head) if i and norm(cell.get("text", "")) == "total"), len(head))
    columns: dict[int, dict] = {}
    for i in range(1, end):
        name, coords = _split_planet(head[i])
        if name:
            columns[i] = {"name": name, "coords": coords, "levels": {}, "units": {}}
    for row in raw.get("rows") or []:
        label = norm(row[0]) if row else ""
        building, population = _IDS.get(label), _POPULATION_IDS.get(label)
        if (building is None and population is None) or len(row) != len(head):
            continue  # section, technologie, stock… ou ligne pas encore entièrement affichée
        for i, planet in columns.items():
            level = _level(row[i])
            if level is None:
                continue
            if building is not None:
                planet["levels"][building] = level
            else:
                planet["units"][population] = level
    planets = list(columns.values())
    if not any(p["levels"] for p in planets):
        raise GameUiError("aucun niveau de bâtiments lu dans le tableau de l'inventaire")
    return planets


def parse_researches(raw: dict) -> dict[str, int]:
    """Niveau de chaque recherche : {id de la recherche: niveau}. Une recherche est commune à l'empire, le jeu ne met que sa
    valeur dans la colonne « Total » (« — » sur chaque planète). Tableau sans technologies : {} (ce n'est pas une erreur)."""
    head = raw.get("head") or []
    if not head or norm(head[0].get("text", "")) != "objet":
        return {}
    total = next((i for i, cell in enumerate(head) if i and norm(cell.get("text", "")) == "total"), None)
    if total is None:
        return {}
    levels: dict[str, int] = {}
    for row in raw.get("rows") or []:
        research = _RESEARCH_IDS.get(norm(row[0])) if row else None
        if research is None or len(row) != len(head):
            continue
        level = _level(row[total])
        if level is not None:
            levels[research] = level
    return levels


def _empty() -> dict:
    return {"planets": [], "researches": {}, "read_at": None, "source": "", "error": ""}


def _valid(planets) -> bool:
    return isinstance(planets, list) and all(
        isinstance(p, dict) and isinstance(p.get("name"), str) and isinstance(p.get("levels"), dict) for p in planets
    )


def _valid_levels(levels) -> bool:
    return isinstance(levels, dict) and all(isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) for k, v in levels.items())


class InventoryStore:
    """Dernier relevé de l'inventaire (gardé sur disque : il survit au redémarrage du conteneur)."""

    def __init__(self, data_dir: str | None) -> None:
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._path = folder / FILE_NAME if folder else None
        self._data = _empty()
        if self._path is not None and self._path.exists():
            try:
                saved = json.loads(self._path.read_text(encoding="utf8"))
                if isinstance(saved, dict) and _valid(saved.get("planets")):
                    researches = saved.get("researches")  # absent des fichiers écrits avant les niveaux de recherche
                    planets = [{**p, "units": p["units"] if _valid_levels(p.get("units")) else {}} for p in saved["planets"]]  # absent des fichiers plus anciens
                    self._data = {**_empty(), "planets": planets, "read_at": saved.get("read_at"), "source": str(saved.get("source", "")),
                                  "researches": researches if _valid_levels(researches) else {}}
            except (OSError, ValueError):
                self._data = _empty()  # fichier illisible : tableau vide, relevé au prochain passage

    def get(self) -> dict:
        return copy.deepcopy(self._data)

    def set(self, planets: list[dict], source: str, researches: dict[str, int] | None = None) -> None:
        self._data = {"planets": copy.deepcopy(planets), "researches": dict(researches or {}), "read_at": time.time(), "source": source, "error": ""}
        self._save()

    def set_error(self, message: str) -> None:
        """Le relevé a échoué : le tableau d'avant reste affiché, avec la raison."""
        self._data = {**self._data, "error": message}

    def _save(self) -> None:
        if not self._path:
            return
        payload = {k: self._data[k] for k in ("planets", "researches", "read_at", "source")}
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".inventory-")
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            # l'enregistrement est facultatif : le bot continue en mémoire


class InventoryReader(ConstructionPass):
    """Ouvre Empire › Inventaire, lit le tableau et l'enregistre. Lecture seule : aucun clic hors de l'onglet."""

    def __init__(self, bot, store: InventoryStore) -> None:
        super().__init__(bot)
        self.store = store
        self.reading = False

    async def _open_inventory_tab(self) -> None:
        tab = self.page.get_by_role("tab", name=TAB_NAME, exact=True)
        end = time.monotonic() + TAB_WAIT_SECONDS
        while not await tab.count():
            if time.monotonic() >= end:
                raise GameUiError(f"onglet « {TAB_NAME} » introuvable")
            await asyncio.sleep(0.25)
        if await tab.first.get_attribute("aria-selected") != "true":
            await self._guard()
            await human.click(self.page, tab.first, self.settings)

    async def read(self) -> tuple[list[dict], dict[str, int]]:
        """(planètes avec leurs niveaux de bâtiments, niveaux des recherches) lus dans le jeu. Lève GameUiError (page ou
        tableau inattendus) ou Interrupted."""
        await self._goto("empire")  # l'univers est celui de la page courante
        await self._open_inventory_tab()
        problem = "tableau absent"
        for attempt in range(READ_ATTEMPTS):
            if attempt == RECLICK_AFTER:
                await self._reclick_tab()
            raw = await self._eval(READ_INVENTORY_JS)
            if raw:
                try:
                    return parse_inventory(raw), parse_researches(raw)
                except GameUiError as exc:
                    problem = str(exc)
            await asyncio.sleep(READ_POLL_SECONDS)
        raise GameUiError(f"l'inventaire ne s'affiche pas ({problem} ; page : {await self._diagnose()})")

    async def _reclick_tab(self) -> None:
        tab = self.page.get_by_role("tab", name=TAB_NAME, exact=True)
        if await tab.count():
            await self._guard()
            await human.click(self.page, tab.first, self.settings)

    async def _diagnose(self) -> str:
        try:
            info = await self.page.evaluate(DIAGNOSE_INVENTORY_JS)
        except Exception:  # noqa: BLE001 - le diagnostic ne doit jamais masquer l'erreur d'origine
            return "diagnostic indisponible"
        return (f"onglets {', '.join(info['tabs']) or 'aucun'} ; {info['tables']} tableau(x) dont {info['visible']} visible(s), "
                f"{info['heads']} avec en-tête, {info['grids']} grille(s) ARIA")

    async def update(self, source: str) -> list[dict] | None:
        """Relit et enregistre ; rend les planètes qui viennent d'être lues, ou None si la lecture a échoué (l'ancien tableau
        reste affiché mais n'est plus « frais »). L'appelant tient `bot.work`. Un échec est gardé dans l'inventaire (et
        journalisé) ; seule une interruption (contrôle manuel…) remonte."""
        was_reading, self.reading = self.reading, True
        try:
            planets, researches = await self.bot.engine.read_inventory()
        except Interrupted:
            raise
        except Exception as exc:  # GameUiError, mais aussi erreur Playwright (navigation lente, page rechargée…) : jamais fatal
            reason = str(exc) or type(exc).__name__
            self.store.set_error(f"Relevé impossible : {reason}")
            self.bot.log(f"Inventaire : relevé impossible ({reason}).", "warn", event=True)
            return None
        finally:
            self.reading = was_reading
        self.store.set(planets, source, researches)
        known = {p["name"] for p in self.bot.supervisor.scanned_planets}
        if known and known != {p["name"] for p in planets}:  # colonnes manquantes (affichage partiel ?) : on le dit
            self.bot.log(f"Inventaire : {len(planets)} planète(s) lue(s) pour {len(known)} planète(s) connue(s) au scan.", "warn", event=True)
        self.bot.log(f"Inventaire relevé ({source}) : {len(planets)} planète(s).", event=source == "manuel")
        return planets

    async def refresh(self, source: str) -> dict:
        """Relevé à la demande. Ne lève jamais : l'échec est dans `error`."""
        if self.reading:
            return self.store.get()
        busy = "une fonctionnalité est en cours, réessayez dans un instant" if self.bot.work.locked() else None
        reason = self.bot.supervisor.game_blocker() or busy
        if reason:
            self.store.set_error(reason[0].upper() + reason[1:])
            return self.store.get()
        self.reading = True
        try:
            async with self.bot.work:  # jamais en même temps qu'un passage de fonctionnalité
                await self.update(source)
        except Interrupted as exc:
            self.store.set_error(f"Relevé impossible : {exc}")
        finally:
            self.reading = False
        return self.store.get()
