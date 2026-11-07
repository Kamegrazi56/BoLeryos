"""Chantier spatial (page /shipyard) : scan des modèles de vaisseaux, Assemblages (construction avec des ressources) et Composants.

Relevé sur le vrai jeu (2026-11-07) :
  - onglets role=tab « Assemblages », « Réparation », « Composants », « Modèle », « Recyclage » ;
  - Assemblages : une <article> par modèle ; titre + « En service : N » + « Civil »/« Militaire » dans un même bouton ; deux boutons
    « Construction » / « Assemblages » (aria-pressed) ; en mode Construction, le coût d'UNE unité est dans trois éléments au
    aria-label « FER : … » / « CRISTAL : … » / « ÉTHER : … », puis champ « Qté » (aria-label), « MAX », durée et « Lancer » ;
  - Composants : une ligne par composant ; nom dans un <span title="nom">, « ×N », coût « F: … C: … E: … » (par unité), durée,
    champ « Qté » (placeholder), « MAX » et un bouton d'icône dont le title est « Produire ».
Le bot ne construit les vaisseaux qu'avec des RESSOURCES (mode Construction) ; il ne touche ni à Réparation, ni à Modèle, ni à Recyclage.
"""

from __future__ import annotations

import asyncio
import re
import time

from . import human
from .builder import ConstructionPass
from .components import COMPONENT_LABELS
from .numbers import norm
from .planets import GameUiError
from .quantity_pass import CLEAR_MARK_JS, TARGET_ATTRIBUTE, QuantityPass
from .recruiter import TAB_SWITCH_SECONDS, parse_count

_PRELUDE_JS = """
const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
// Durée : unités en minuscules (« 21,43 s », « 10 min 5 s ») pour ne pas confondre avec « 2,33 M ».
const DURATION = /^(?:\\d+(?:[.,]\\d+)?\\s*(?:j|d|h|min|m|s)\\s*)+$/;
const COST_LETTER = { FER: 'F', CRISTAL: 'C', 'ÉTHER': 'E' };
const norm = (s) => s.replace(/[’‘]/g, "'").toLowerCase().replace(/\\s+/g, ' ').trim();
// Carte de modèle : un <article> avec « En service : N », le bouton « Construction » et un champ de quantité.
function shipCards() {
  const out = [];
  for (const art of document.querySelectorAll('article')) {
    const construction = [...art.querySelectorAll('button')].find((b) => text(b) === 'Construction');
    const svc = [...art.querySelectorAll('span')].find((s) => /^En service\\s*:/i.test(text(s)));
    if (construction && svc && art.querySelector('input[type=number]')) out.push([art, svc, construction]);
  }
  return out;
}
const shipName = (svc) => text(svc.parentElement.firstElementChild);
// Ligne de composant : le plus grand bloc qui ne contient qu'UN champ « Qté ».
function componentRows() {
  return [...document.querySelectorAll('input[placeholder="Qté"]')].map((input) => {
    let row = input;
    while (row.parentElement && row.parentElement.querySelectorAll('input[placeholder="Qté"]').length === 1) row = row.parentElement;
    return row;
  });
}
const componentName = (row) => { const s = [...row.querySelectorAll('span[title]')].find((e) => e.getAttribute('title') === text(e)); return s ? text(s) : ''; };
"""

READ_SHIPS_JS = (
    "() => {"
    + _PRELUDE_JS
    + """
  const cards = shipCards().map(([art, svc, construction]) => {
    const head = svc.parentElement;
    const kind = [...head.children].filter((e) => e !== svc && e !== head.firstElementChild).map(text)[0] || '';
    const owned = (text(svc).match(/(\\d[\\d\\s\\u00a0\\u202f]*)$/) || [])[1] || null;
    const costs = {};
    art.querySelectorAll('[aria-label]').forEach((e) => {
      const m = /^(FER|CRISTAL|ÉTHER)\\s*:\\s*(.+)$/i.exec(e.getAttribute('aria-label') || '');
      if (m) costs[COST_LETTER[m[1].toUpperCase()]] = m[2];
    });
    const dur = [...art.querySelectorAll('span')].find((s) => !s.hasAttribute('aria-label') && DURATION.test(text(s)));
    const max = [...art.querySelectorAll('button')].find((b) => text(b) === 'MAX');
    return { name: shipName(svc), kind, owned, costs, duration: dur ? text(dur) : '', can: !!max && !max.disabled,
             mode: construction.getAttribute('aria-pressed') === 'true' ? 'construction' : 'assemblages' };
  });
  return { cards };
}"""
)

MARK_SHIP_JS = (
    "(args) => {"
    + _PRELUDE_JS
    + """
  const [wanted, attribute] = args;
  document.querySelectorAll('[' + attribute + ']').forEach((e) => e.removeAttribute(attribute));
  for (const [art, svc] of shipCards()) {
    if (norm(shipName(svc)) !== wanted) continue;
    art.setAttribute(attribute, '1');
    return true;
  }
  return false;
}"""
)

READ_COMPONENTS_JS = (
    "() => {"
    + _PRELUDE_JS
    + """
  const cards = componentRows().filter((row) => componentName(row)).map((row) => {
    const spans = [...row.querySelectorAll('span')];
    const costs = {};
    spans.forEach((s) => { const m = text(s).match(/^([FCE]):\\s*(.+)$/); if (m) costs[m[1]] = m[2]; });
    const own = spans.map(text).find((t) => /^×\\s*\\d[\\d\\s\\u00a0\\u202f]*$/.test(t));
    const dur = spans.find((s) => DURATION.test(text(s)));
    const go = [...row.querySelectorAll('button')].find((b) => (b.getAttribute('title') || '') === 'Produire');
    const max = [...row.querySelectorAll('button')].find((b) => text(b) === 'MAX');
    return { name: componentName(row), owned: own ? own.replace(/^×\\s*/, '') : null, costs, duration: dur ? text(dur) : '',
             can: !!go && !!max && !max.disabled };
  });
  return { cards };
}"""
)

MARK_COMPONENT_JS = (
    "(args) => {"
    + _PRELUDE_JS
    + """
  const [wanted, attribute] = args;
  document.querySelectorAll('[' + attribute + ']').forEach((e) => e.removeAttribute(attribute));
  for (const row of componentRows()) {
    if (norm(componentName(row)) !== wanted) continue;
    row.setAttribute(attribute, '1');
    return true;
  }
  return false;
}"""
)


class ShipPass(QuantityPass):
    """Assemblages : construit des vaisseaux (modèles déjà créés) avec des ressources, en mode « Construction »."""

    feature_id = "ships"
    title = "Assemblages"
    tab = "Assemblages"
    read_js = READ_SHIPS_JS
    mark_js = MARK_SHIP_JS
    go_name = re.compile(r"^Lancer$", re.I)

    async def _ready(self, label: str) -> None:
        """Le vaisseau doit être en mode Construction (ressources) : bascule la carte si besoin et vérifie le résultat."""
        page = self.page
        if not await page.evaluate(MARK_SHIP_JS, [norm(label), TARGET_ATTRIBUTE]):
            return  # carte introuvable : `_line` le signale
        try:
            raw = next((c for c in (await self._eval(READ_SHIPS_JS))["cards"] if norm(c["name"]) == norm(label)), None)
            if raw is None or raw["mode"] == "construction":
                return
            button = page.locator(f"[{TARGET_ATTRIBUTE}]").first.get_by_role("button", name="Construction", exact=True)
            await self._guard()
            await human.click(page, button.first, self.settings)
        finally:
            await page.evaluate(CLEAR_MARK_JS, TARGET_ATTRIBUTE)
        for _ in range(20):
            raw = next((c for c in (await self._eval(READ_SHIPS_JS))["cards"] if norm(c["name"]) == norm(label)), None)
            if raw is not None and raw["mode"] == "construction":
                return
            await asyncio.sleep(0.15)
        raise GameUiError(f"impossible de passer « {label} » en mode Construction")


class ComponentPass(QuantityPass):
    """Composants : produit des composants avec des ressources (bouton « Produire »)."""

    feature_id = "components"
    title = "Composants"
    tab = "Composants"
    read_js = READ_COMPONENTS_JS
    mark_js = MARK_COMPONENT_JS
    go_name = re.compile(r"^Produire$", re.I)

    def __init__(self, bot, store) -> None:
        super().__init__(bot, store, COMPONENT_LABELS)


class ModelScanner(ConstructionPass):
    """Lit la liste des modèles de l'onglet Assemblages (lecture seule : ni quantité saisie, ni lancement)."""

    async def list_models(self) -> list[dict]:
        page = self.page
        if page.url.split("?")[0].rstrip("/").rsplit("/", 1)[-1] != "shipyard":
            await self._guard()
            root = page.url.split("/game/")[0] + "/game/" + page.url.split("/game/")[1].split("/")[0]
            await page.goto(f"{root}/shipyard", wait_until="domcontentloaded", timeout=30_000)
        tab = page.get_by_role("tab", name="Assemblages", exact=True)
        end = time.monotonic() + TAB_SWITCH_SECONDS
        while time.monotonic() < end and not await tab.count():
            await asyncio.sleep(0.25)
        if not await tab.count():
            raise GameUiError("la page « Chantier spatial » ne s'affiche pas")
        cards = (await self._eval(READ_SHIPS_JS))["cards"]
        if not cards:
            await self._guard()
            await human.click(page, tab.first, self.settings)
            for _ in range(32):
                cards = (await self._eval(READ_SHIPS_JS))["cards"]
                if cards:
                    break
                await asyncio.sleep(0.25)
        models = []
        for card in cards:
            owned = parse_count(card.get("owned"))
            models.append({"name": card["name"], "kind": card["kind"].lower(), "owned": owned})
        return models
