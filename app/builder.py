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
        self._reached: dict[tuple[str, str], set[str]] = {}  # (bâtiment, planète de la ligne ou « ») → planètes (norm) où le niveau cible est atteint (passage en cours)
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
        if settings["planets"]:  # planètes cochées ; vide = toutes
            return list(settings["planets"])
        names = [p["name"] for p in self.bot.supervisor.scanned_planets]
        return names or self.bot.supervisor.prefs.get()["catalog"]["planets"]

    async def run_pass(self) -> str:
        settings = self.bot.constructions.get()
        queue = settings["queue"]
        if not queue:
            return "aucune ligne de construction réglée, rien à faire"
        targets = self._targets(settings)
        if not targets:
            return "aucune planète connue : lancez un scan des planètes (Réglages › Compte et univers)"
        known = {norm(p["name"]) for p in self.bot.supervisor.scanned_planets} or {norm(t) for t in targets}
        planets = self._with_own_planets(targets, queue, known)  # + les planètes qui n'ont que des lignes à elles
        ignored = sorted({ln["planet"] for ln in queue if ln.get("planet") and norm(ln["planet"]) not in known and norm(ln["planet"]) not in {norm(t) for t in targets}})
        if ignored:
            self.bot.log(f"Constructions : planète(s) inconnue(s) du scan, lignes ignorées : {', '.join(ignored)}.", "warn", event=True)
        lines = {planet: self._lines_for(planet, queue, targets) for planet in planets}
        launched: list[str] = []
        waiting = 0
        skipped = 0
        self._reached = {}
        try:
            satisfied = self._satisfied(lines, await self._refresh_inventory())
            for planet in planets:
                mine = lines[planet]
                if not mine:  # aucune ligne ne vaut pour cette planète : on ne la visite pas
                    continue
                if planet in satisfied:  # tout ce qui est demandé y est déjà au niveau : inutile de la visiter
                    self._mark_reached(planet, mine)
                    skipped += 1
                    continue
                outcome = await self._planet(planet, mine)
                await self._remember(planet)
                if outcome is None:
                    waiting += 1
                else:
                    launched.append(outcome)
        except Interrupted as exc:
            return self._summary(launched, waiting, f"interrompu ({exc})", skipped)
        except GameUiError as exc:
            self.bot.log(f"Constructions : {exc}.", "warn", event=True)
            return self._summary(launched, waiting, f"arrêté ({exc})", skipped)
        self._drop_reached(queue, targets)
        return self._summary(launched, waiting, "", skipped)

    async def _refresh_inventory(self) -> list[dict] | None:
        """Relève l'inventaire de l'empire (une seule page) : tableau de l'onglet Constructions, et choix des planètes à visiter.
        Un échec de ce relevé n'arrête pas les constructions : il est noté dans l'inventaire (None) et toutes les planètes
        sont visitées comme avant."""
        reader = getattr(self.bot, "inventory_reader", None)
        return await reader.update("passage") if reader is not None else None

    @staticmethod
    def _key(line: dict) -> tuple[str, str]:
        """Identité d'une ligne : bâtiment + planète (« » : toutes les planètes visées)."""
        return line["building"], line.get("planet", "")

    @staticmethod
    def _with_own_planets(targets: list[str], queue: list[dict], known: set[str]) -> list[str]:
        """Planètes à parcourir : les planètes visées, puis celles qui n'apparaissent que dans une ligne à elles ET que le scan
        connaît (une planète disparue ou renommée ferait échouer le changement de planète et arrêterait tout le passage)."""
        names = list(targets)
        seen = {norm(n) for n in names}
        for line in queue:
            own = line.get("planet")
            if own and norm(own) not in seen and norm(own) in known:
                seen.add(norm(own))
                names.append(own)
        return names

    @staticmethod
    def _lines_for(planet: str, queue: list[dict], targets: list[str]) -> list[dict]:
        """Lignes de la liste qui valent pour cette planète, dans l'ordre de la liste (= priorité). La ligne propre à une planète
        l'emporte sur la ligne « toutes les planètes » du même bâtiment ; celle-ci ne vaut que pour les planètes visées."""
        key = norm(planet)
        own = {ln["building"] for ln in queue if ln.get("planet") and norm(ln["planet"]) == key}
        targeted = key in {norm(t) for t in targets}
        return [ln for ln in queue if (norm(ln["planet"]) == key if ln.get("planet") else targeted and ln["building"] not in own)]

    def _mark_reached(self, planet: str, lines: list[dict]) -> None:
        for line in lines:
            self._reached.setdefault(self._key(line), set()).add(norm(planet))

    @staticmethod
    def _satisfied(lines: dict[str, list[dict]], fresh: list[dict] | None) -> set[str]:
        """Planètes dont le relevé de CE passage montre, pour chacune de leurs lignes, un niveau lu et déjà au niveau cible.
        Planète sans ligne, absente du relevé, bâtiment non lu ou relevé échoué : pas de saut (jamais de décision sur de vieilles données)."""
        levels = {norm(p["name"]): p["levels"] for p in fresh or []}
        return {
            planet for planet, mine in lines.items()
            if mine and levels.get(norm(planet)) and all(levels[norm(planet)].get(ln["building"], -1) >= ln["level"] for ln in mine)
        }

    def _drop_reached(self, queue: list[dict], targets: list[str]) -> None:
        """Retire de la liste les lignes dont le niveau cible est atteint (passage complet seulement) : sur sa planète pour une ligne
        propre à une planète ; sur TOUTES les planètes visées qui n'ont pas leur propre ligne de ce bâtiment pour une ligne commune."""
        finished = []
        for line in queue:
            if line.get("planet"):
                common = next((o for o in queue if o["building"] == line["building"] and not o.get("planet")), None)
                if common and common["level"] > line["level"]:
                    continue  # plafond propre à la planète, sous la ligne commune : le retirer ferait remonter la planète jusqu'à la ligne commune
                scope = {norm(line["planet"])}
            else:
                own = {norm(o["planet"]) for o in queue if o.get("planet") and o["building"] == line["building"]}
                scope = {norm(t) for t in targets} - own
            if scope and scope <= self._reached.get(self._key(line), set()):  # une ligne commune sans plus aucune planète n'est pas supprimée
                finished.append((line["building"], line.get("planet", ""), line["level"]))
        for building, planet, level in self.bot.constructions.drop(finished):
            where = planet or "toutes les planètes visées"
            self.bot.log(f"Construction : {self._labels[building]} a atteint le niveau {level} sur {where}, ligne retirée de la liste.", event=True, kind="launch")

    @staticmethod
    def _summary(launched: list[str], waiting: int, stop: str, skipped: int = 0) -> str:
        parts = [f"{len(launched)} construction(s) lancée(s)" + (" : " + ", ".join(launched) if launched else "")]
        parts.append(f"{waiting} planète(s) sans rien à lancer")
        if skipped:
            parts.append(f"{skipped} planète(s) déjà au niveau (d'après l'inventaire, non visitées)")
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
                self._mark_reached(planet, [line])
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
