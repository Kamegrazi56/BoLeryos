"""Recrutement de population : lit la page Population du jeu (onglets Assaut / Logistique / Administration) et clique sur « Recruter ».

Un passage (`run_pass`) parcourt les planètes (toutes celles du scan) ; sur chacune, il applique les lignes de la liste qui la
concernent, dans l'ordre de priorité, UNE SEULE ligne par population (la première de la liste qui s'applique). Pour chaque ligne :
  - « fixed » : recrute N unités ; « max » : clique sur MAX ; « target » : recrute ce qui manque pour atteindre l'effectif voulu ;
  - le bot clique d'abord sur MAX pour connaître le maximum payable selon le jeu, puis ne demande jamais plus que ce maximum
    (quantité = minimum entre ce qui est voulu et ce qui est payable ; rien si l'on ne peut pas payer une seule unité).
Les populations sont indépendantes : chaque ligne a son mode, sa quantité et sa planète.

Garde-fous : pas de nouveau recrutement d'une population tant que la précédente est en file d'attente (file lue sur la page, sinon
durée estimée), arrêt immédiat si le contrôle manuel s'active, et désactivation de la fonctionnalité après deux recrutements de
suite dont le débit n'a pas pu être constaté.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field

from . import builder, human
from .builder import COOLDOWN_EXTRA_SECONDS, COOLDOWN_MARGIN, UNCONFIRMED_LIMIT, ConstructionPass, parse_duration
from .numbers import norm, parse_amount
from .planets import GameUiError, Interrupted
from .populations import POPULATIONS

TAB_SWITCH_SECONDS = 8.0
TARGET_ATTRIBUTE = "data-bot-target"
UNVERIFIABLE_WAIT_SECONDS = 3.0
COST_KEYS = {"F": "Fer", "C": "Cristal", "E": "Éther"}

_PRELUDE_JS = """
const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
const leaves = (root) => [...root.querySelectorAll('*')].filter((e) => e.children.length === 0 && text(e) !== '');
const HEADINGS = 'h1, h2, h3, h4, h5, h6';
// Une carte = le plus petit bloc qui contient un titre ET un seul champ de quantité (le Soldat, automatique, n'en a pas).
function popCards() {
  const out = [];
  const seen = new Set();
  for (const h of document.querySelectorAll('main ' + HEADINGS.split(', ').join(', main '))) {
    let card = h.parentElement;
    while (card && card.tagName !== 'MAIN' && !card.querySelector('input[type=number]')) card = card.parentElement;
    if (!card || card.tagName === 'MAIN' || seen.has(card)) continue;
    if (card.querySelectorAll('input[type=number]').length !== 1 || card.querySelectorAll(HEADINGS).length !== 1) continue;
    seen.add(card);
    out.push([h, card]);
  }
  return out;
}
const buttonLabel = (b) => b.getAttribute('aria-label') || text(b);
"""

# Cartes de la page : pastille d'effectif AVANT le titre, coûts « F: … / C: … / E: … » (par unité), durée d'une unité, champ de
# quantité, boutons MAX et « Recruter » ; file d'attente = `queue-operation-units` (« nom », « xN »).
READ_POP_JS = (
    "() => {"
    + _PRELUDE_JS
    + """
  const DURATION = /^(?:\\d+(?:[.,]\\d+)?\\s*[jdhms]\\s*)+$/i;
  const queue = {};
  document.querySelectorAll('[data-testid="queue-operation-units"]').forEach((q) => {
    const ls = leaves(q).map(text);
    ls.forEach((t, i) => {
      const m = t.match(/^x(\\d+)$/i);
      if (m && i > 0) queue[ls[i - 1]] = (queue[ls[i - 1]] || 0) + parseInt(m[1], 10);
    });
  });
  const cards = popCards().map(([h, card]) => {
    const ls = leaves(card);
    const at = ls.findIndex((e) => h.contains(e));
    let owned = null;
    for (let i = at - 1; i >= 0; i--) { const t = text(ls[i]); if (/^[\\d\\s\\u00a0\\u202f]+$/.test(t)) { owned = t; break; } }
    const costs = {};
    let firstCost = -1;
    ls.forEach((e, i) => { const m = text(e).match(/^([FCE]):\\s*(.+)$/); if (m) { costs[m[1]] = m[2]; if (firstCost < 0) firstCost = i; } });
    let duration = '';
    for (let i = firstCost + 1; firstCost >= 0 && i < ls.length; i++) { if (DURATION.test(text(ls[i]))) { duration = text(ls[i]); break; } }
    const buttons = [...card.querySelectorAll('button')];
    const go = buttons.find((b) => /^recruter\\b/i.test(buttonLabel(b)));
    const max = buttons.find((b) => /^(max|quantité maximale)/i.test(buttonLabel(b)));
    return { name: text(h), owned, costs, duration, can: !!go && !!max && !go.disabled && !max.disabled };
  });
  return { cards, queue };
}"""
)

# Marque la carte voulue (data-bot-target) pour que Playwright retrouve son champ et ses boutons ; false si elle est introuvable.
MARK_POP_JS = (
    "(args) => {"
    + _PRELUDE_JS
    + """
  const [wanted, attribute] = args;
  const norm = (s) => s.replace(/[’‘]/g, "'").toLowerCase().replace(/\\s+/g, ' ').trim();
  document.querySelectorAll('[' + attribute + ']').forEach((e) => e.removeAttribute(attribute));
  for (const [h, card] of popCards()) {
    if (norm(text(h)) !== wanted) continue;
    card.setAttribute(attribute, '1');
    return true;
  }
  return false;
}"""
)

CLEAR_MARK_JS = "(attribute) => document.querySelectorAll('[' + attribute + ']').forEach((e) => e.removeAttribute(attribute))"


def parse_count(text: str | None) -> int | None:
    """« 20 150 » → 20150 ; None si illisible."""
    cleaned = re.sub(r"[\s  ]", "", str(text or ""))
    return int(cleaned) if re.fullmatch(r"\d+", cleaned) else None


def _display_step(value: float) -> float:
    """Plus petit écart visible sur un stock affiché par le jeu (« 30,243 M » → 1 000)."""
    if value >= 1e9:
        return 1e6
    if value >= 1e6:
        return 1e3
    return 1.0


@dataclass
class PopCard:
    name: str
    owned: int | None
    queued: int
    costs: dict[str, float]  # coût d'UNE unité
    unit_seconds: float | None  # durée d'UNE unité
    can: bool  # boutons MAX et « Recruter » présents et actifs
    unreadable: list[str] = field(default_factory=list)


def parse_card(raw: dict, queued: int) -> PopCard:
    unreadable: list[str] = []
    owned = parse_count(raw.get("owned"))
    if raw.get("owned") not in (None, "") and owned is None:
        unreadable.append(str(raw["owned"]))
    costs: dict[str, float] = {}
    for key, value in (raw.get("costs") or {}).items():
        try:
            costs[COST_KEYS[key]] = parse_amount(value)
        except ValueError:
            unreadable.append(f"{key}: {value}")
    return PopCard(raw["name"], owned, queued, costs, parse_duration(raw.get("duration") or ""), bool(raw.get("can")), unreadable)


@dataclass
class Report:
    launched: list[str] = field(default_factory=list)
    idle: int = 0  # lignes sans rien à faire (objectif atteint, pas assez de ressources, en cours…)


class PopulationPass(ConstructionPass):
    """Même accès à la page que les constructions (planète active, stocks, garde-fous), autre page du jeu."""

    def __init__(self, bot) -> None:
        super().__init__(bot)
        self._pops = {p.id: p for p in POPULATIONS}

    # ------------------------------------------------------------------ lecture
    async def _read_pop_cards(self) -> dict[str, PopCard]:
        raw = await self._eval(READ_POP_JS)
        queue = {norm(name): count for name, count in raw["queue"].items()}
        return {norm(c["name"]): parse_card(c, queue.get(norm(c["name"]), 0)) for c in raw["cards"]}

    async def _open_staff(self) -> None:
        page = self.page
        if page.url.split("?")[0].rstrip("/").rsplit("/", 1)[-1] != "staff":
            await self._guard()
            root = page.url.split("/game/")[0] + "/game/" + page.url.split("/game/")[1].split("/")[0]
            await page.goto(f"{root}/staff", wait_until="domcontentloaded", timeout=30_000)
        for _ in range(40):
            if (await self._eval(READ_POP_JS))["cards"]:
                return
            await asyncio.sleep(0.25)
        raise GameUiError("la page « Population » ne s'affiche pas")

    async def _cards_of(self, tab: str, label: str) -> dict[str, PopCard]:
        """Cartes de l'onglet voulu ; clique sur l'onglet si la population cherchée n'est pas dans la vue affichée."""
        cards = await self._read_pop_cards()
        if norm(label) in cards:
            return cards
        button = self.page.get_by_role("tab", name=tab, exact=True)
        if not await button.count():
            raise GameUiError(f"onglet « {tab} » introuvable")
        await self._guard()
        await human.click(self.page, button.first, self.settings)
        end = time.monotonic() + TAB_SWITCH_SECONDS
        while time.monotonic() < end:
            cards = await self._read_pop_cards()
            if norm(label) in cards:
                break
            await asyncio.sleep(0.25)
        return cards

    # ------------------------------------------------------------------ un passage
    def _planets(self) -> list[str]:
        names = [p["name"] for p in self.bot.supervisor.scanned_planets]
        return names or self.bot.supervisor.prefs.get()["catalog"]["planets"]

    @staticmethod
    def _lines_for(planet: str, queue: list[dict]) -> list[dict]:
        """Lignes qui s'appliquent à cette planète, dans l'ordre de la liste, la première de chaque population seulement."""
        seen: set[str] = set()
        out = []
        for line in queue:
            if line["planet"] in ("", planet) and line["population"] not in seen:
                seen.add(line["population"])
                out.append(line)
        return out

    async def run_pass(self) -> str:
        queue = self.bot.populations.get()["queue"]
        if not queue:
            return "aucune ligne de population réglée, rien à faire"
        planets = self._planets()
        if not planets:
            return "aucune planète connue : lancez un scan des planètes (Réglages › Compte et univers)"
        report = Report()
        try:
            for planet in planets:
                now = time.monotonic()
                due = [ln for ln in self._lines_for(planet, queue) if self._cooldown.get((planet, ln["population"]), 0.0) <= now]
                if not due:
                    continue
                await self._open_staff()
                await self._select_planet(planet)
                for line in due:
                    outcome = await self._line(planet, line)
                    if outcome is None:
                        report.idle += 1
                    else:
                        report.launched.append(outcome)
        except Interrupted as exc:
            return self._report(report, f"interrompu ({exc})")
        except GameUiError as exc:
            self.bot.log(f"Population : {exc}.", "warn", event=True)
            return self._report(report, f"arrêté ({exc})")
        return self._report(report, "")

    @staticmethod
    def _report(report: Report, stop: str) -> str:
        parts = [f"{len(report.launched)} recrutement(s) lancé(s)" + (" : " + ", ".join(report.launched) if report.launched else "")]
        parts.append(f"{report.idle} ligne(s) sans rien à faire")
        if stop:
            parts.append(stop)
        return " ; ".join(parts)

    @staticmethod
    def _wanted(line: dict, card: PopCard) -> int | None:
        """Quantité voulue ; None = tout ce que le jeu permet (MAX) ; 0 = rien à recruter."""
        if line["mode"] == "max":
            return None
        if line["mode"] == "fixed":
            return int(line["amount"])
        return max(0, int(line["amount"]) - (card.owned or 0) - card.queued)  # « target »

    async def _line(self, planet: str, line: dict) -> str | None:
        """Retourne « 4272 × Rempart de Fer sur Concorde » si un recrutement est lancé, sinon None."""
        pop = self._pops[line["population"]]
        card = (await self._cards_of(pop.tab, pop.label)).get(norm(pop.label))
        if card is None:
            self.bot.log(f"Population : « {pop.label} » est introuvable sur {planet}.", "warn", event=True)
            return None
        if card.unreadable or not card.costs:
            self.bot.log(f"Population : « {pop.label} » sur {planet} : valeur illisible ({(card.unreadable or ['coût'])[0]}), rien recruté.", "warn", event=True)
            return None
        if card.queued > 0 or not card.can:
            return None  # déjà en cours d'entraînement, ou boutons inactifs (rien de payable)
        if line["mode"] == "target" and card.owned is None:
            return None
        wanted = self._wanted(line, card)
        if wanted == 0:
            return None
        return await self._recruit(planet, pop, card, wanted)

    # ------------------------------------------------------------------ recrutement
    async def _recruit(self, planet: str, pop, card: PopCard, wanted: int | None) -> str | None:
        page = self.page
        await self._guard()
        if not await page.evaluate(MARK_POP_JS, [norm(pop.label), TARGET_ATTRIBUTE]):
            raise GameUiError(f"carte de {pop.label} introuvable")
        try:
            box = page.locator(f"[{TARGET_ATTRIBUTE}]").first
            quantity = box.locator("input[type=number]").first
            before = await self._stocks()
            await human.think(self.settings)
            await human.click(page, box.get_by_role("button", name=re.compile(r"^(MAX|Quantité maximale)", re.I)).first, self.settings)
            await asyncio.sleep(0.3)
            affordable = parse_count(await quantity.input_value()) or 0
            if affordable < 1:
                return None  # pas de quoi payer une seule unité
            qty = affordable if wanted is None else min(wanted, affordable)
            if qty != affordable:
                await human.click(page, quantity, self.settings)
                await quantity.fill(str(qty))
                await asyncio.sleep(0.3)
            if parse_count(await quantity.input_value()) != qty:
                raise GameUiError(f"la quantité saisie pour {pop.label} n'est pas celle demandée")
            go = box.get_by_role("button", name=re.compile(r"^Recruter\b", re.I)).first
            if not await go.count() or not await go.is_enabled():
                return None
            await self._guard()
            await human.click(page, go, self.settings)
        finally:
            await page.evaluate(CLEAR_MARK_JS, TARGET_ATTRIBUTE)
        wait = (card.unit_seconds or 1.0) * qty * COOLDOWN_MARGIN + COOLDOWN_EXTRA_SECONDS
        self._cooldown[(planet, pop.id)] = time.monotonic() + wait
        sentence = f"{qty} × {pop.label} sur {planet}"
        await self._confirm(pop, card, qty, before, sentence)
        return sentence

    async def _confirm(self, pop, card: PopCard, qty: int, before: dict, sentence: str) -> None:
        """Débit visible des ressources, ou effectif + file d'attente en hausse ; sinon le recrutement est « non confirmé »."""
        key = max(card.costs, key=lambda k: card.costs[k])
        expected = card.costs[key] * qty
        was = before.get(key)
        visible = was is not None and expected >= 2 * _display_step(was)
        end = time.monotonic() + (builder.CONFIRM_SECONDS if visible else UNVERIFIABLE_WAIT_SECONDS)
        total_before = (card.owned or 0) + card.queued
        while time.monotonic() < end:
            if visible and was - ((await self._stocks()).get(key) or was) >= expected * builder.CONFIRM_RATIO:
                self._confirmed(sentence)
                return
            seen = (await self._read_pop_cards()).get(norm(pop.label))
            if seen is not None and (seen.owned or 0) + seen.queued >= total_before + qty:
                self._confirmed(sentence)
                return
            await asyncio.sleep(0.3)
        if not visible:
            self.bot.log(f"Recrutement lancé : {sentence} (débit trop petit pour être vérifié à l'écran).", event=True, kind="launch")
            return
        self._unconfirmed += 1
        self.bot.log(f"Clic sur « Recruter » fait ({sentence}) mais le débit des ressources n'a pas été constaté.", "warn", event=True, kind="attention")
        if self._unconfirmed >= UNCONFIRMED_LIMIT:
            self.bot.scheduler.update("populations", {"enabled": False})
            self.bot.log("Recrutement de population désactivé : deux lancements de suite non confirmés. Vérifiez à l'écran avant de le réactiver.", "error", event=True, kind="attention")
            raise Interrupted("désactivé après deux lancements non confirmés")

    def _confirmed(self, sentence: str) -> None:
        self._unconfirmed = 0
        self.bot.log(f"Recrutement lancé : {sentence}.", event=True, kind="launch")
