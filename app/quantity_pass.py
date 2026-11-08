"""Passage « quantités » du Chantier spatial : base commune des Assemblages de vaisseaux et des Composants.

Un passage (`run_pass`) parcourt les planètes (toutes celles du scan) ; sur chacune, il applique les lignes de la liste qui la
concernent, dans l'ordre de priorité, UNE SEULE ligne par élément (la première de la liste qui s'applique). Pour chaque ligne :
  - « fixed » : produit N unités ; « max » : clique sur MAX ; « target » : produit ce qui manque pour atteindre l'effectif voulu ;
  - le bot clique d'abord sur MAX pour connaître le maximum payable selon le jeu, puis ne demande jamais plus que ce maximum
    (quantité = minimum entre ce qui est voulu et ce qui est payable ; rien si l'on ne peut pas payer une seule unité).
Garde-fous : pas de nouvelle production d'un élément avant la fin présumée de la précédente (durée lue sur la page), arrêt immédiat
si le contrôle manuel s'active, désactivation de la fonctionnalité après deux lancements de suite dont le débit n'a pas pu être
constaté, et vérification que la page affiche bien les chiffres de la planète choisie (voir `ConstructionPass._settle`).
"""

from __future__ import annotations

import asyncio
import re
import time

from . import builder, human
from .builder import COOLDOWN_EXTRA_SECONDS, COOLDOWN_MARGIN, UNCONFIRMED_LIMIT, ConstructionPass
from .numbers import norm
from .planets import GameUiError, Interrupted
from .quantities import LineSettings, applies_to
from .recruiter import TAB_SWITCH_SECONDS, UNVERIFIABLE_WAIT_SECONDS, PopCard, Report, _display_step, clear_quantity, parse_card, parse_count

TARGET_ATTRIBUTE = "data-bot-target"
PENDING_FACTOR = 3.0  # un effectif visé reste « en cours » (donc compté) pendant 3 fois la durée présumée de la production
MAX_BUTTON = re.compile(r"^MAX$", re.I)
CLEAR_MARK_JS = "(attribute) => document.querySelectorAll('[' + attribute + ']').forEach((e) => e.removeAttribute(attribute))"


class QuantityPass(ConstructionPass):
    """Même accès à la page que les constructions (planète active, stocks, garde-fous), page « Chantier spatial »."""

    feature_id = ""  # « ships » ou « components » : fonctionnalité à désactiver en cas de doute
    title = ""  # « Assemblages » ou « Composants » : début des lignes du journal
    tab = ""  # onglet du Chantier spatial qui affiche les cartes
    read_js = ""  # () => { cards: [{name, owned, costs: {F, C, E}, duration, can}] }
    mark_js = ""  # ([nom normalisé, attribut]) => bool : marque la carte voulue
    go_name = re.compile(r"^$")  # nom du bouton de lancement

    def __init__(self, bot, store: LineSettings, labels: dict[str, str] | None = None) -> None:
        super().__init__(bot)
        self.store = store
        self._item_labels = labels or {}
        self._last_qty = 0
        self._pending: dict[tuple[str, str], tuple[int, float]] = {}  # (planète, élément) → (effectif visé, fin de validité)

    def label(self, line: dict) -> str:
        return self._item_labels.get(line["item"], line["item"])

    # ------------------------------------------------------------------ lecture
    async def _read_pop_cards(self) -> dict[str, PopCard]:
        raw = await self._eval(self.read_js)
        return {norm(c["name"]): parse_card(c, 0) for c in raw["cards"]}

    async def _cards_signature(self) -> tuple:
        return tuple(sorted((name, card.owned) for name, card in (await self._read_pop_cards()).items()))

    async def _open_yard(self) -> None:
        page = self.page
        if page.url.split("?")[0].rstrip("/").rsplit("/", 1)[-1] != "shipyard":
            await self._guard()
            root = page.url.split("/game/")[0] + "/game/" + page.url.split("/game/")[1].split("/")[0]
            await page.goto(f"{root}/shipyard", wait_until="domcontentloaded", timeout=30_000)
        end = time.monotonic() + TAB_SWITCH_SECONDS
        clicked = False
        while time.monotonic() < end:
            if (await self._eval(self.read_js))["cards"]:
                return
            tab = page.get_by_role("tab", name=self.tab, exact=True)
            if not clicked and await tab.count():
                await self._guard()
                await human.click(page, tab.first, self.settings)
                clicked = True
            await asyncio.sleep(0.25)
        raise GameUiError(f"l'onglet « {self.tab} » du Chantier spatial n'affiche rien")

    async def _ready(self, label: str) -> None:
        """Prépare la carte avant lecture (les vaisseaux doivent être en mode Construction). Rien par défaut."""

    # ------------------------------------------------------------------ un passage
    def _planets(self) -> list[str]:
        names = [p["name"] for p in self.bot.supervisor.scanned_planets]
        return names or self.bot.supervisor.prefs.get()["catalog"]["planets"]

    @staticmethod
    def _lines_for(planet: str, queue: list[dict]) -> list[dict]:
        """Lignes qui s'appliquent à cette planète, dans l'ordre de la liste, la première de chaque élément seulement."""
        seen: set[str] = set()
        out = []
        for line in queue:
            if applies_to(line, planet) and line["item"] not in seen:
                seen.add(line["item"])
                out.append(line)
        return out

    async def run_pass(self) -> str:
        queue = self.store.get()["queue"]
        if not queue:
            return "aucune ligne réglée, rien à faire"
        planets = self._planets()
        if not planets:
            return "aucune planète connue : lancez un scan des planètes (Réglages › Compte et univers)"
        report = Report()
        try:
            for planet in planets:
                now = time.monotonic()
                due = [
                    ln
                    for ln in self._lines_for(planet, queue)
                    if self._cooldown.get((planet, ln["item"]), 0.0) <= now and not self.store.is_done(ln, planet)
                ]
                if not due:
                    continue
                await self._open_yard()
                await self._select_planet(planet)
                await self._settle(planet, self._cards_signature)
                for line in due:
                    outcome = await self._line(planet, line)
                    if outcome is None:
                        report.idle += 1
                    else:
                        report.launched.append(outcome)
                        if line["mode"] != "target":  # « une seule fois » : ne recommence plus sur cette planète
                            self.store.mark_done(line, planet)
                await self._remember(planet, self._cards_signature)
        except Interrupted as exc:
            return self._report(report, f"interrompu ({exc})")
        except GameUiError as exc:
            self.bot.log(f"{self.title} : {exc}.", "warn", event=True)
            return self._report(report, f"arrêté ({exc})")
        dropped = self.store.drop_done(planets)
        for line in dropped:  # faites partout : la ligne disparaît de la liste
            where = ", ".join(line["planets"]) or "toutes les planètes"
            self.bot.log(f"{self.title} : ligne « {self.label(line)} » terminée sur {where}, retirée de la liste.", event=True, kind="launch")
        if dropped and not self.store.get()["queue"]:  # plus rien à faire : la fonctionnalité s'arrête d'elle-même
            self.bot.scheduler.update(self.feature_id, {"enabled": False})
            self.bot.log(f"{self.title} : toutes les lignes sont terminées, fonctionnalité désactivée.", event=True, kind="launch")
            return self._report(report, "liste terminée, fonctionnalité désactivée")
        return self._report(report, "")

    @staticmethod
    def _report(report: Report, stop: str) -> str:
        parts = [f"{len(report.launched)} lancement(s)" + (" : " + ", ".join(report.launched) if report.launched else "")]
        parts.append(f"{report.idle} ligne(s) sans rien à faire")
        if stop:
            parts.append(stop)
        return " ; ".join(parts)

    def _wanted(self, line: dict, planet: str, card: PopCard) -> int | None:
        """Quantité voulue ; None = tout ce que le jeu permet (MAX) ; 0 = rien à produire."""
        if line["mode"] == "max":
            return None
        if line["mode"] == "fixed":
            return int(line["amount"])
        owned = card.owned or 0
        pending = self._pending.get((planet, line["item"]))
        if pending and pending[1] > time.monotonic():
            owned = max(owned, pending[0])  # une production lancée n'apparaît pas encore dans l'effectif : on la compte
        return max(0, int(line["amount"]) - owned)

    async def _line(self, planet: str, line: dict) -> str | None:
        """Retourne « 12 × Mineur sur Concorde » si une production est lancée, sinon None."""
        label = self.label(line)
        await self._ready(label)
        card = (await self._read_pop_cards()).get(norm(label))
        if card is None:
            self.bot.log(f"{self.title} : « {label} » est introuvable sur {planet}.", "warn", event=True)
            return None
        if card.unreadable or not card.costs:
            self.bot.log(f"{self.title} : « {label} » sur {planet} : valeur illisible ({(card.unreadable or ['coût'])[0]}), rien lancé.", "warn", event=True)
            return None
        if not card.can:
            return None  # boutons inactifs
        if line["mode"] == "target" and card.owned is None:
            return None
        wanted = self._wanted(line, planet, card)
        if wanted == 0:
            self.store.mark_done(line, planet)  # effectif déjà atteint (les lignes « à chaque passage » ne sont jamais « faites »)
            return None
        sentence = await self._produce(planet, label, line, card, wanted)
        if sentence is not None and line["mode"] == "target" and self._last_qty == wanted:
            self.store.mark_done(line, planet)  # tout ce qui manquait est lancé : l'effectif sera atteint
        return sentence

    # ------------------------------------------------------------------ production
    async def _produce(self, planet: str, label: str, line: dict, card: PopCard, wanted: int | None) -> str | None:
        page = self.page
        await self._guard()
        if not await page.evaluate(self.mark_js, [norm(label), TARGET_ATTRIBUTE]):
            raise GameUiError(f"carte de {label} introuvable")
        try:
            box = page.locator(f"[{TARGET_ATTRIBUTE}]").first
            quantity = box.locator("input[type=number]").first
            before = await self._stocks()
            await human.think(self.settings)
            await human.click(page, box.get_by_role("button", name=MAX_BUTTON).first, self.settings)
            await asyncio.sleep(0.3)
            affordable = parse_count(await quantity.input_value()) or 0
            if affordable < 1:
                await clear_quantity(quantity)
                return None  # pas de quoi payer une seule unité
            qty = affordable if wanted is None else min(wanted, affordable)
            if qty != affordable:
                await human.click(page, quantity, self.settings)
                await quantity.fill(str(qty))
                await asyncio.sleep(0.3)
            if parse_count(await quantity.input_value()) != qty:
                await clear_quantity(quantity)
                raise GameUiError(f"la quantité saisie pour {label} n'est pas celle demandée")
            go = box.get_by_role("button", name=self.go_name).first
            if not await go.count() or not await go.is_enabled():
                await clear_quantity(quantity)  # sinon le jeu afficherait des coûts totaux au prochain passage
                return None
            await self._guard()
            await human.click(page, go, self.settings)
        finally:
            await page.evaluate(CLEAR_MARK_JS, TARGET_ATTRIBUTE)
        wait = (card.unit_seconds or 1.0) * qty * COOLDOWN_MARGIN + COOLDOWN_EXTRA_SECONDS
        self._cooldown[(planet, line["item"])] = time.monotonic() + wait
        self._pending[(planet, line["item"])] = ((card.owned or 0) + qty, time.monotonic() + wait * PENDING_FACTOR)
        self._last_qty = qty
        sentence = f"{qty} × {label} sur {planet}"
        await self._confirm(label, card, qty, before, sentence)
        return sentence

    async def _confirm(self, label: str, card: PopCard, qty: int, before: dict, sentence: str) -> None:
        """Débit visible des ressources, ou effectif en hausse ; sinon le lancement est « non confirmé »."""
        key = max(card.costs, key=lambda k: card.costs[k])
        expected = card.costs[key] * qty
        was = before.get(key)
        visible = was is not None and expected >= 2 * _display_step(was)
        end = time.monotonic() + (builder.CONFIRM_SECONDS if visible else UNVERIFIABLE_WAIT_SECONDS)
        while time.monotonic() < end:
            if visible and was - ((await self._stocks()).get(key) or was) >= expected * builder.CONFIRM_RATIO:
                self._confirmed(sentence)
                return
            seen = (await self._read_pop_cards()).get(norm(label))
            if seen is not None and (seen.owned or 0) >= (card.owned or 0) + qty:
                self._confirmed(sentence)
                return
            await asyncio.sleep(0.3)
        if not visible:
            self.bot.log(f"{self.title} lancé : {sentence} (débit trop petit pour être vérifié à l'écran).", event=True, kind="launch")
            return
        self._unconfirmed += 1
        self.bot.log(f"Clic fait ({sentence}) mais le débit des ressources n'a pas été constaté.", "warn", event=True, kind="attention")
        if self._unconfirmed >= UNCONFIRMED_LIMIT:
            self.bot.scheduler.update(self.feature_id, {"enabled": False})
            self.bot.log(f"{self.title} désactivé : deux lancements de suite non confirmés. Vérifiez à l'écran avant de le réactiver.", "error", event=True, kind="attention")
            raise Interrupted("désactivé après deux lancements non confirmés")

    def _confirmed(self, sentence: str) -> None:
        self._unconfirmed = 0
        self.bot.log(f"{self.title} lancé : {sentence}.", event=True, kind="launch")
