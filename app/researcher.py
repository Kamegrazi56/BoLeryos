"""Recherches : lit le Laboratoire du jeu (onglets Civil / Militaire) et clique sur « Rechercher ».

Dans le jeu, une recherche est payée par UNE planète (celle qui est active) et une seule recherche tourne à la fois.
Un passage (`run_pass`) : ouvre le Laboratoire, active la planète chargée des recherches, lit les cartes, puis prend la
PREMIÈRE ligne de la liste (ordre = priorité) dont le niveau actuel est sous le niveau cible et dont le bouton est actif,
clique UNE fois et vérifie que les ressources ont été débitées. Ensuite, aucune autre recherche n'est tentée avant la fin
présumée de celle-ci (durée lue sur la carte).

Mêmes garde-fous que les constructions : arrêt si le contrôle manuel s'active, désactivation de la fonctionnalité après deux
lancements de suite dont le débit n'a pas pu être confirmé.
"""

from __future__ import annotations

import asyncio
import time

from . import human
from .builder import COOLDOWN_EXTRA_SECONDS, COOLDOWN_MARGIN, UNCONFIRMED_LIMIT, Card, ConstructionPass
from .numbers import norm
from .planets import GameUiError, Interrupted
from .researches import RESEARCHES

TAB_SWITCH_SECONDS = 8.0
TARGET_ATTRIBUTE = "data-bot-target"

# Cartes du Laboratoire : un titre (heading) par carte. « Niveau N » = niveau actuel ; coûts = paires (Fer|Cristal|Éther, valeur) ;
# durée = valeur après « Temps de recherche » ; bouton = le seul bouton qui porte du texte (« Rechercher » ou « Ressources insuffisantes »).
_CARDS_JS = """
const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
const KEYS = { fer: 'Fer', cristal: 'Cristal', 'éther': 'Éther' };
const HEADINGS = 'main h1, main h2, main h3, main h4, main h5, main h6';
const ONE_HEADING = 'h1, h2, h3, h4, h5, h6';
function labCards() {
  const out = [];
  for (const h of document.querySelectorAll(HEADINGS)) {
    let card = h;
    while (card.parentElement && card.parentElement.tagName !== 'MAIN' && card.parentElement.querySelectorAll(ONE_HEADING).length === 1) card = card.parentElement;
    if (card !== h) out.push([h, card]);
  }
  return out;
}
const actionButton = (card) => [...card.querySelectorAll('button')].find((b) => text(b) !== '') || null;
"""

READ_LAB_JS = (
    "() => {"
    + _CARDS_JS
    + """
  return labCards().map(([h, card]) => {
    const leaves = [...card.querySelectorAll('*')].filter((e) => e.children.length === 0 && text(e) !== '').map(text);
    const costs = {};
    let duration = '';
    leaves.forEach((t, i) => {
      if (KEYS[t.toLowerCase()] && i + 1 < leaves.length) costs[KEYS[t.toLowerCase()]] = leaves[i + 1];
      else if (t.toLowerCase() === 'temps de recherche' && i + 1 < leaves.length) duration = leaves[i + 1];
    });
    const m = /niveau\\s*(\\d+)/i.exec(text(card));
    const bare = leaves.find((t) => /^\\d+$/.test(t));
    const level = m ? Number(m[1]) : (bare === undefined ? null : Number(bare));
    const locked = /verrouill/i.test(text(card));
    const button = actionButton(card);
    const can = !!button && !locked && !button.disabled && !/insuffisant/i.test(text(button));
    return { name: text(h), level, costs, duration, disabled: !can };
  });
}"""
)

# Marque le bouton de la carte voulue (data-bot-target) pour que Playwright clique dessus ; renvoie false si la carte ou le bouton manque.
MARK_BUTTON_JS = (
    "(args) => {"
    + _CARDS_JS
    + """
  const [wanted, attribute] = args;
  const norm = (s) => s.replace(/[’‘]/g, "'").toLowerCase().replace(/\\s+/g, ' ').trim();
  document.querySelectorAll('[' + attribute + ']').forEach((e) => e.removeAttribute(attribute));
  for (const [h, card] of labCards()) {
    if (norm(text(h)) !== wanted) continue;
    const button = actionButton(card);
    if (!button || button.disabled || /insuffisant/i.test(text(button))) return false;
    button.setAttribute(attribute, '1');
    return true;
  }
  return false;
}"""
)

CLEAR_MARK_JS = "(attribute) => document.querySelectorAll('[' + attribute + ']').forEach((e) => e.removeAttribute(attribute))"


class ResearchPass(ConstructionPass):
    """Même accès à la page que les constructions (planète active, stocks, garde-fous), autre page du jeu."""

    def __init__(self, bot) -> None:
        super().__init__(bot)
        self._busy_until = 0.0  # fin présumée de la recherche en cours (horloge monotone)
        self._labels = {r.id: r.label for r in RESEARCHES}
        self._tabs = {r.id: r.tab for r in RESEARCHES}
        self._tab_names = {tab: {norm(r.label) for r in RESEARCHES if r.tab == tab} for tab in ("Civil", "Militaire")}

    # ------------------------------------------------------------------ lecture
    async def _read_lab(self) -> dict[str, Card]:
        return {norm(r["name"]): Card(r) for r in await self._eval(READ_LAB_JS)}

    async def _open_lab(self) -> None:
        page = self.page
        if page.url.split("?")[0].rstrip("/").rsplit("/", 1)[-1] != "tech":
            await self._guard()
            root = page.url.split("/game/")[0] + "/game/" + page.url.split("/game/")[1].split("/")[0]
            await page.goto(f"{root}/tech", wait_until="domcontentloaded", timeout=30_000)
        for _ in range(40):
            if await self._eval(READ_LAB_JS):
                return
            await asyncio.sleep(0.25)
        raise GameUiError("la page « Laboratoire » ne s'affiche pas")

    async def _read_tab(self, tab: str) -> dict[str, Card]:
        """Cartes de l'onglet demandé (Civil ou Militaire) : clique sur l'onglet si ce n'est pas celui affiché."""
        wanted = self._tab_names[tab]
        cards = await self._read_lab()
        if wanted & cards.keys():
            return cards
        button = self.page.get_by_role("tab", name=tab, exact=True)
        if not await button.count():
            raise GameUiError(f"onglet « {tab} » introuvable")
        await self._guard()
        await human.click(self.page, button.first, self.settings)
        end = time.monotonic() + TAB_SWITCH_SECONDS
        while time.monotonic() < end:
            cards = await self._read_lab()
            if wanted & cards.keys():
                return cards
            await asyncio.sleep(0.25)
        raise GameUiError(f"l'onglet « {tab} » ne s'affiche pas")

    # ------------------------------------------------------------------ un passage
    async def run_pass(self) -> str:
        settings = self.bot.researches.get()
        if not settings["queue"]:
            return "aucune ligne de recherche réglée, rien à faire"
        planet = settings["planet"]
        if not planet:
            return "aucune planète chargée des recherches : choisissez-la (Réglages › Recherches)"
        remaining = self._busy_until - time.monotonic()
        if remaining > 0:
            return f"une recherche est en cours (encore environ {int(remaining // 60) + 1} min), rien à lancer"
        try:
            launched = await self._attempt(planet, settings["queue"])
        except Interrupted as exc:
            return f"interrompu ({exc})"
        except GameUiError as exc:
            self.bot.log(f"Recherches : {exc}.", "warn", event=True)
            return f"arrêté ({exc})"
        return launched or "rien à lancer (recherches de la liste terminées, verrouillées ou trop chères)"

    async def _attempt(self, planet: str, queue: list[dict]) -> str | None:
        """Retourne « Technologie Laser niveau 23 sur Egide » si une recherche est lancée, sinon None."""
        await self._open_lab()
        await self._select_planet(planet)
        tabs: list[str] = []
        for line in queue:
            tab = self._tabs[line["research"]]
            if tab not in tabs:
                tabs.append(tab)
        found: dict[str, tuple[str, Card]] = {}
        for tab in tabs:
            for key, card in (await self._read_tab(tab)).items():
                if key in self._tab_names[tab]:
                    found[key] = (tab, card)
        for line in queue:  # ordre de la liste = priorité
            label = self._labels[line["research"]]
            entry = found.get(norm(label))
            if entry is None:
                continue
            tab, card = entry
            if card.level is None or card.level >= line["level"] or card.disabled:
                continue
            return await self._launch_research(planet, label, tab, card)
        return None

    # ------------------------------------------------------------------ lancement
    async def _click_research(self, label: str) -> None:
        page = self.page
        await self._guard()
        if not await page.evaluate(MARK_BUTTON_JS, [norm(label), TARGET_ATTRIBUTE]):
            raise GameUiError(f"bouton de recherche de {label} introuvable ou inactif")
        try:
            await human.click(page, page.locator(f"[{TARGET_ATTRIBUTE}]").first, self.settings)
        finally:
            await page.evaluate(CLEAR_MARK_JS, TARGET_ATTRIBUTE)

    async def _launch_research(self, planet: str, label: str, tab: str, card: Card) -> str:
        await self._read_tab(tab)  # l'onglet de la recherche doit être celui affiché
        before = await self._stocks()
        await human.think(self.settings)
        await self._click_research(label)
        # Une seule recherche à la fois : on n'en tente pas d'autre avant la fin présumée de celle-ci.
        self._busy_until = time.monotonic() + (card.duration_s or 3600.0) * COOLDOWN_MARGIN + COOLDOWN_EXTRA_SECONDS
        sentence = f"{label} niveau {(card.level or 0) + 1} sur {planet}"
        if await self._debited(before, card):
            self._unconfirmed = 0
            self.bot.log(f"Recherche lancée : {sentence}.", event=True, kind="launch")
        else:
            self._unconfirmed += 1
            self.bot.log(f"Clic sur « Rechercher » fait ({sentence}) mais le débit des ressources n'a pas été constaté.", "warn", event=True, kind="attention")
            if self._unconfirmed >= UNCONFIRMED_LIMIT:
                self.bot.scheduler.update("researches", {"enabled": False})
                self.bot.log("Recherches désactivées : deux lancements de suite non confirmés. Vérifiez à l'écran avant de les réactiver.", "error", event=True, kind="attention")
                raise Interrupted("désactivées après deux lancements non confirmés")
        return sentence
