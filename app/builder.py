"""Construction de bâtiments : lit les pages Industrie / Infrastructures du jeu et clique sur « Améliorer ».

Un passage (`run_pass`) :
  pour chaque planète visée, dans l'ordre : ouvre la planète, lit les cartes des bâtiments de la liste, puis prend la
  PREMIÈRE ligne (ordre de priorité) dont le niveau actuel est sous le niveau cible et dont le bouton « Améliorer » est
  actif, clique UNE fois, et vérifie que les ressources ont été débitées. Au plus une construction par planète et par passage.

Garde-fous : un seul clic par bâtiment tant que sa construction n'est pas censée être finie (durée lue sur la carte),
arrêt immédiat si le contrôle manuel s'active, et désactivation de la fonctionnalité après deux lancements de suite
dont le débit n'a pas pu être confirmé.
"""

from __future__ import annotations

import asyncio
import re
import time

from . import human, interruptions
from .constructions import BUILDINGS
from .numbers import norm, parse_amount
from .planets import GameUiError, Interrupted

CONFIRM_SECONDS = 8.0  # attente maximale du débit des ressources après le clic
CONFIRM_RATIO = 0.5  # le stock principal doit baisser d'au moins cette part du coût
COOLDOWN_MARGIN = 1.1  # on attend la durée lue + 10 % avant de recliquer sur le même bâtiment
COOLDOWN_EXTRA_SECONDS = 30.0
UNCONFIRMED_LIMIT = 2  # lancements non confirmés de suite avant désactivation
PLANET_SWITCH_SECONDS = 6.0
SETTLE_SECONDS = 10.0  # attente maximale que la page affiche les chiffres de la NOUVELLE planète
SETTLE_POLL_SECONDS = 0.4
UPGRADE_WAIT_SECONDS = 5.0  # attente de la carte du bâtiment visé avant de cliquer sur « Améliorer »
PLANET_BUTTON_LABEL = "Sélectionner la planète active"
PAGE_PATHS = {"Industrie": "buildings", "Infrastructures": "facilities"}

# Cartes de la page : un bouton « Améliorer » par bâtiment. Niveau = premier nombre entier, nom = titre h3,
# coûts = paires (Fer|Cristal|Éther, valeur), durée = valeur après « Durée ».
READ_CARDS_JS = """
() => {
  const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
  const out = [];
  for (const b of document.querySelectorAll('button')) {
    if (text(b).toLowerCase() !== 'améliorer') continue;
    let card = b;
    while (card.parentElement && card.querySelectorAll('h3').length !== 1) card = card.parentElement;
    const leaves = [...card.querySelectorAll('*')].filter((e) => e.children.length === 0 && text(e) !== '').map(text);
    const name = text(card.querySelector('h3'));
    const level = leaves.find((t) => /^\\d+$/.test(t));
    const costs = {};
    let duration = '';
    const start = leaves.indexOf(name);
    for (let i = start + 1; i < leaves.length - 1; i++) {
      if (['Fer', 'Cristal', 'Éther'].includes(leaves[i])) costs[leaves[i]] = leaves[i + 1];
      else if (leaves[i].toLowerCase() === 'durée') duration = leaves[i + 1];
    }
    out.push({ name, level: level === undefined ? null : Number(level), costs, duration, disabled: b.disabled });
  }
  return out;
}
"""

# Titres (h3) de toutes les cartes affichées, avec ou sans bouton « Améliorer » : sert à reconnaître l'onglet affiché.
CARD_TITLES_JS = "() => [...document.querySelectorAll('h3')].map((e) => (e.textContent || '').replace(/\\s+/g, ' ').trim())"

# Marque le bouton « Améliorer » de la carte dont le titre h3 correspond (même repérage que READ_CARDS_JS).
UPGRADE_MARK = "data-bot-upgrade"
MARK_UPGRADE_JS = """
([wanted, attribute]) => {
  const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
  const norm = (s) => s.replace(/[’‘]/g, "'").toLowerCase().replace(/\\s+/g, ' ').trim();
  document.querySelectorAll('[' + attribute + ']').forEach((e) => e.removeAttribute(attribute));
  for (const b of document.querySelectorAll('button')) {
    if (text(b).toLowerCase() !== 'améliorer' || !b.getClientRects().length) continue;
    let card = b;
    while (card.parentElement && card.querySelectorAll('h3').length !== 1) card = card.parentElement;
    const h3 = card.querySelector('h3');
    if (h3 && norm(text(h3)) === wanted) { b.setAttribute(attribute, '1'); return true; }
  }
  return false;
}
"""

# Stocks de la planète active (en-tête) : éléments au libellé « FER : 104,334 M ».
READ_STOCKS_JS = """
() => {
  const out = {};
  for (const el of document.querySelectorAll('[aria-label]')) {
    const m = /^(FER|CRISTAL|ÉTHER)\\s*:\\s*(.+)$/i.exec(el.getAttribute('aria-label') || '');
    if (m) out[m[1][0].toUpperCase() + m[1].slice(1).toLowerCase()] = m[2];
  }
  return out;
}
"""

_DURATION_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(j|d|h|m|s)")
_DURATION_UNITS = {"j": 86400.0, "d": 86400.0, "h": 3600.0, "m": 60.0, "s": 1.0}


def parse_duration(text: str) -> float | None:
    """« 15h 36m » → 56160.0 ; « 3m 10s » → 190.0 ; « 5.00s » → 5.0. None si illisible."""
    parts = _DURATION_RE.findall((text or "").lower())
    if not parts:
        return None
    return sum(float(n.replace(",", ".")) * _DURATION_UNITS[u] for n, u in parts)


def _amount(raw: str | None) -> float | None:
    try:
        return parse_amount(raw) if raw else None
    except ValueError:
        return None


class Card:
    """Une carte de bâtiment lue sur la page."""

    def __init__(self, raw: dict) -> None:
        self.name: str = raw["name"]
        self.level: int | None = raw["level"]
        self.disabled: bool = bool(raw["disabled"])
        self.costs = {k: _amount(v) for k, v in raw["costs"].items()}
        self.duration_s = parse_duration(raw["duration"])


class ConstructionPass:
    def __init__(self, bot) -> None:
        self.bot = bot
        self._cooldown: dict[tuple[str, str], float] = {}  # (planète, bâtiment) → fin présumée de la construction
        self._unconfirmed = 0
        self._reached: dict[str, set[str]] = {}  # bâtiment → planètes où le niveau cible est atteint (passage en cours)
        self._last_planet: str | None = None  # dernière planète traitée et ce que la page affichait à la fin de son traitement
        self._last_sig: tuple | None = None
        self._labels = {b.id: b.label for b in BUILDINGS}
        self._pages = {b.id: b.page for b in BUILDINGS}

    # ------------------------------------------------------------------ accès à la page
    @property
    def page(self):
        page = self.bot._page
        if page is None or page.is_closed():
            raise GameUiError("le navigateur n'est pas disponible")
        return page

    @property
    def settings(self):
        return self.bot._settings

    async def _guard(self) -> None:
        reason = self.bot.supervisor.game_blocker()
        if reason:
            raise Interrupted(reason)
        await asyncio.sleep(0)

    async def _eval(self, script: str):
        await interruptions.dismiss(self.page)
        return await self.page.evaluate(script)

    async def _stocks(self) -> dict[str, float | None]:
        raw = await self._eval(READ_STOCKS_JS)
        return {k: _amount(v) for k, v in raw.items()}

    async def _read_cards(self) -> dict[str, Card]:
        return {norm(r["name"]): Card(r) for r in await self._eval(READ_CARDS_JS)}

    # ------------------------------------------------------------------ navigation
    async def _open_page(self, tab: str) -> None:
        """Ouvre Industrie ou Infrastructures (adresse /buildings ou /facilities)."""
        wanted = PAGE_PATHS[tab]
        page = self.page
        path = page.url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
        if path == wanted:
            return
        await self._guard()
        if path in PAGE_PATHS.values():  # déjà dans la zone : un clic sur l'onglet suffit (la planète est conservée)
            tab_button = page.get_by_role("tab", name=tab, exact=True)
            if not await tab_button.count():
                raise GameUiError(f"onglet « {tab} » introuvable")
            await human.click(page, tab_button.first, self.settings)
        else:
            root = page.url.split("/game/")[0] + "/game/" + page.url.split("/game/")[1].split("/")[0]
            await page.goto(f"{root}/{wanted}", wait_until="domcontentloaded", timeout=30_000)
        # Vu sur le vrai jeu : après un clic d'onglet, les cartes de l'ANCIEN onglet restent un instant affichées. On attend
        # donc qu'un bâtiment de l'onglet voulu soit à l'écran (Industrie et Infrastructures n'ont aucun bâtiment en commun).
        # On lit le titre de TOUTES les cartes : sur une planète sans aucun bouton « Améliorer » (construction en cours…),
        # l'onglet doit être reconnu quand même (vrai jeu, 08/11).
        expected = {norm(b.label) for b in BUILDINGS if b.page == tab}
        for _ in range(40):
            if any(norm(title) in expected for title in await self._eval(CARD_TITLES_JS)):
                return
            await asyncio.sleep(0.25)
        raise GameUiError(f"la page « {tab} » ne s'affiche pas")

    async def _goto(self, path: str) -> None:
        """Ouvre une page de la partie par son adresse (ex. « galaxy », « operations?… »)."""
        page = self.page
        await self._guard()
        root = page.url.split("/game/")[0] + "/game/" + page.url.split("/game/")[1].split("/")[0]
        await page.goto(f"{root}/{path}", wait_until="domcontentloaded", timeout=30_000)

    async def _click_named(self, name: str) -> None:
        button = self.page.get_by_role("button", name=name, exact=True)
        if not await button.count():
            raise GameUiError(f"bouton « {name} » introuvable")
        await self._guard()
        await human.click(self.page, button.first, self.settings)

    async def _current_planet(self) -> str:
        button = self.page.get_by_role("button", name=PLANET_BUTTON_LABEL).first
        if not await button.count():
            raise GameUiError("sélecteur de planète introuvable")
        return await button.evaluate(
            "b => { const l = [...b.querySelectorAll('*')].filter((e) => !e.children.length && e.textContent.trim()); return l.length ? l[0].textContent.trim() : ''; }"
        )

    async def _select_planet(self, name: str) -> None:
        if norm(await self._current_planet()) == norm(name):
            return
        await self._guard()
        page = self.page
        await human.click(page, page.get_by_role("button", name=PLANET_BUTTON_LABEL).first, self.settings)
        entry = page.locator("button", has=page.get_by_text(name, exact=True)).filter(has_text=re.compile(r"[A-Z]\d{2} \d{2} \d{2}"))
        target = None
        for i in range(await entry.count()):  # des fenêtres cachées du jeu peuvent contenir les mêmes noms : seul le visible compte
            if await entry.nth(i).is_visible():
                target = entry.nth(i)
                break
        if target is None:
            raise GameUiError(f"planète « {name} » absente de la liste")
        await human.click(page, target, self.settings)  # masquée par la barre du bas (écran bas) : human.click passe au clavier
        end = time.monotonic() + PLANET_SWITCH_SECONDS
        while time.monotonic() < end:
            if norm(await self._current_planet()) == norm(name):
                await asyncio.sleep(0.3)
                return
            await asyncio.sleep(0.25)
        raise GameUiError(f"le changement vers la planète « {name} » n'a pas abouti")

    # ------------------------------------------------------------------ chiffres de la bonne planète
    async def _signature(self, read=None) -> tuple:
        """Ce que la page affiche : les stocks, plus (selon la page) les effectifs ou niveaux lus par `read`."""
        stocks = await self._stocks()
        return (tuple(sorted(stocks.items())), await read() if read else ())

    async def _settle(self, planet: str, read=None) -> None:
        """Après un changement de planète : attend deux lectures identiques de la page, différentes de celles de la planète
        précédente (sinon la page affiche encore l'ancienne planète). Échec → GameUiError : rien n'est cliqué."""
        previous = self._last_sig if self._last_planet not in (None, planet) else None
        end = time.monotonic() + SETTLE_SECONDS
        last = None
        while time.monotonic() < end:
            signature = await self._signature(read)
            if signature == last and signature != previous:
                return
            last = signature
            await asyncio.sleep(SETTLE_POLL_SECONDS)
        raise GameUiError(f"les chiffres affichés pour « {planet} » ne sont pas stables ou sont ceux de la planète précédente, rien n'a été fait")

    async def _remember(self, planet: str, read=None) -> None:
        """Note ce que la page affiche une fois la planète traitée, pour détecter une page pas encore rafraîchie ensuite."""
        self._last_planet, self._last_sig = planet, await self._signature(read)

    # ------------------------------------------------------------------ lancement
    async def _upgrade(self, label: str) -> None:
        """Clique sur « Améliorer » de la carte du bâtiment, repérée de la même façon que la lecture des cartes
        (bouton « Améliorer » → carte au titre h3 unique) : ce qui a été lu est bien ce qui est cliqué."""
        page = self.page
        end = time.monotonic() + UPGRADE_WAIT_SECONDS  # la carte peut s'afficher un instant après le changement d'onglet
        while not await page.evaluate(MARK_UPGRADE_JS, [norm(label), UPGRADE_MARK]):
            if time.monotonic() >= end:
                shown = ", ".join(sorted(c["name"] for c in await self._eval(READ_CARDS_JS))) or "aucune"
                raise GameUiError(f"bouton « Améliorer » de {label} introuvable (cartes affichées : {shown})")
            await asyncio.sleep(0.25)
        try:
            await self._guard()
            await human.click(page, page.locator(f"[{UPGRADE_MARK}]").first, self.settings)
        finally:
            await page.evaluate("(a) => document.querySelectorAll('[' + a + ']').forEach((e) => e.removeAttribute(a))", UPGRADE_MARK)

    async def _debited(self, before: dict, card: Card) -> bool:
        """Le plus gros coût de la carte doit avoir baissé dans l'en-tête (la production seule ne l'explique pas)."""
        costs = {k: v for k, v in card.costs.items() if v}
        if not costs:
            return False
        key = max(costs, key=lambda k: costs[k])
        if before.get(key) is None:
            return False
        end = time.monotonic() + CONFIRM_SECONDS
        while time.monotonic() < end:
            now = (await self._stocks()).get(key)
            if now is not None and before[key] - now >= costs[key] * CONFIRM_RATIO:
                return True
            await asyncio.sleep(0.3)
        return False

    # ------------------------------------------------------------------ un passage
    def _targets(self, settings: dict) -> list[str]:
        if settings["scope"] == "one":
            return [settings["planet"]]
        names = [p["name"] for p in self.bot.supervisor.scanned_planets]
        return names or self.bot.supervisor.prefs.get()["catalog"]["planets"]

    async def run_pass(self) -> str:
        settings = self.bot.constructions.get()
        if not settings["queue"]:
            return "aucune ligne de construction réglée, rien à faire"
        planets = self._targets(settings)
        if not planets:
            return "aucune planète connue : lancez un scan des planètes (Réglages › Compte et univers)"
        launched: list[str] = []
        waiting = 0
        self._reached = {}
        try:
            for planet in planets:
                outcome = await self._planet(planet, settings["queue"])
                await self._remember(planet)
                if outcome is None:
                    waiting += 1
                else:
                    launched.append(outcome)
        except Interrupted as exc:
            return self._summary(launched, waiting, f"interrompu ({exc})")
        except GameUiError as exc:
            self.bot.log(f"Constructions : {exc}.", "warn", event=True)
            return self._summary(launched, waiting, f"arrêté ({exc})")
        self._drop_reached(settings["queue"], planets)
        return self._summary(launched, waiting, "")

    def _drop_reached(self, queue: list[dict], planets: list[str]) -> None:
        """Retire de la liste les lignes dont le niveau cible est atteint sur TOUTES les planètes visées (passage complet seulement)."""
        finished = [(ln["building"], ln["level"]) for ln in queue if set(planets) <= self._reached.get(ln["building"], set())]
        for building, level in self.bot.constructions.drop(finished):
            self.bot.log(f"Construction : {self._labels[building]} a atteint le niveau {level} sur toutes les planètes visées, ligne retirée de la liste.", event=True, kind="launch")

    @staticmethod
    def _summary(launched: list[str], waiting: int, stop: str) -> str:
        parts = [f"{len(launched)} construction(s) lancée(s)" + (" : " + ", ".join(launched) if launched else "")]
        parts.append(f"{waiting} planète(s) sans rien à lancer")
        if stop:
            parts.append(stop)
        return " ; ".join(parts)

    async def _planet(self, planet: str, queue: list[dict]) -> str | None:
        """Retourne « Mine de Fer niveau 41 sur Egide » si une construction est lancée, sinon None."""
        tabs: list[str] = []
        for line in queue:
            tab = self._pages[line["building"]]
            if tab not in tabs:
                tabs.append(tab)
        await self._open_page(tabs[0])
        await self._select_planet(planet)
        await self._settle(planet)
        cards: dict[str, tuple[str, Card]] = {}
        for tab in tabs:
            await self._open_page(tab)
            for key, card in (await self._read_cards()).items():
                cards[key] = (tab, card)
        for line in queue:  # lignes déjà atteintes sur cette planète (retirées en fin de passage si c'est vrai partout)
            seen = cards.get(norm(self._labels[line["building"]]))
            if seen is not None and seen[1].level is not None and seen[1].level >= line["level"]:
                self._reached.setdefault(line["building"], set()).add(planet)
        for line in queue:  # ordre de la liste = priorité
            label = self._labels[line["building"]]
            found = cards.get(norm(label))
            if found is None:
                continue
            tab, card = found
            if card.level is None or card.level >= line["level"] or card.disabled:
                continue
            if self._cooldown.get((planet, line["building"]), 0.0) > time.monotonic():
                continue
            return await self._launch(planet, label, line, tab, card)
        return None

    async def _launch(self, planet: str, label: str, line: dict, tab: str, card: Card) -> str:
        await self._open_page(tab)
        before = await self._stocks()
        await human.think(self.settings)
        await self._upgrade(label)
        # Un seul clic par bâtiment tant que la construction n'est pas censée être finie.
        wait = (card.duration_s or 3600.0) * COOLDOWN_MARGIN + COOLDOWN_EXTRA_SECONDS
        self._cooldown[(planet, line["building"])] = time.monotonic() + wait
        sentence = f"{label} niveau {(card.level or 0) + 1} sur {planet}"
        if await self._debited(before, card):
            self._unconfirmed = 0
            self.bot.log(f"Construction lancée : {sentence}.", event=True, kind="launch")
        else:
            self._unconfirmed += 1
            self.bot.log(f"Clic sur « Améliorer » fait ({sentence}) mais le débit des ressources n'a pas été constaté.", "warn", event=True, kind="attention")
            if self._unconfirmed >= UNCONFIRMED_LIMIT:
                self.bot.scheduler.update("constructions", {"enabled": False})
                self.bot.log("Constructions désactivées : deux lancements de suite non confirmés. Vérifiez à l'écran avant de les réactiver.", "error", event=True, kind="attention")
                raise Interrupted("désactivées après deux lancements non confirmés")
        return sentence
