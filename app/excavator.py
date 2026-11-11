"""Fouilles : relevé du jour (sites libres les plus proches, nombre de Fouilles possibles) et lancements, Fouille par Fouille.

Relevé sur le vrai jeu (2026-11-07, Fouille Egide → M58 80 21, 1 Scientifique, 3 h) :
  - carte : les emplacements libres arrivent dans la fenêtre visible (`/map/continuous/window`) avec `kind` FREE_SLOT et la
    capacité EXCAVATION, avec leur position réelle ; dans la liste « Ensemble » : « Planètes libres <adresse> » (Coloniser / Fouille) ;
  - Opérations › Piliers › Fouille : clic sur « Lancer la fouille » → aucun message, « Fouilles actives » et « Missions actives »
    +1 ; la Fouille apparaît dans « Missions en cours » : « Fouille · 3 heures · 0 résultat · Active : 19:08 · 02:57:37 ·
    Origine 6-79-Egide · Destination Arsenal orbital scellé · M58 80 21 ».
Le bot ne touche JAMAIS à « Mobilisation générale », « Activer le champ de force » ni « Désactiver les piliers ».
"""

from __future__ import annotations

import asyncio
import math
import random
import re
import time

from . import builder, human
from .builder import UNCONFIRMED_LIMIT
from .excavations import SITES_KEPT, describe, slots
from .explorer import CLEAR_MARK_JS, MARK_TARGET_JS, TARGET_ATTRIBUTE, ExplorationPass, formula_distance
from .planets import GameUiError, Interrupted

EXCAVATION_PATH = "operations?method=PILLARS&mission=EXCAVATION"
PAGE_WAIT_SECONDS = 10.0
LAUNCH_NAME = re.compile(r"^Lancer la fouille", re.I)
HERO_ATTRIBUTE = "data-bot-hero"

_PRELUDE_JS = """
const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
const own = (el) => [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join(' ').replace(/\\s+/g, ' ').trim();
const ADDRESS = /[A-Z]\\d{2} \\d{2} \\d{2}/;
const visible = (el) => el.getClientRects().length > 0;  // la page garde les formulaires des autres missions, cachés
"""

COUNTERS_JS = (
    "() => {"
    + _PRELUDE_JS
    + """
  const out = {};
  for (const [key, label] of [['excavations', 'fouilles actives'], ['missions', 'missions actives']]) {
    const leaf = [...document.querySelectorAll('main *')].find((e) => e.children.length === 0 && visible(e) && text(e).toLowerCase().startsWith(label));
    let box = leaf;
    while (box && !/\\d+\\s*\\/\\s*\\d+/.test(text(box))) box = box.parentElement;
    const tail = box ? text(box).slice(Math.max(0, text(box).toLowerCase().indexOf(label))) : '';
    const m = /(\\d+)\\s*\\/\\s*(\\d+)/.exec(tail);
    out[key] = m ? [Number(m[1]), Number(m[2])] : null;
  }
  return out;
}"""
)

# Cartes de « Missions en cours » d'un type donné (« Fouille », « Piratage ») : origine, site, durée, temps restant (secondes).
MISSIONS_JS = (
    "(kind) => {"
    + _PRELUDE_JS
    + """
  const out = [];
  const seen = new Set();
  for (const e of document.querySelectorAll('main *')) {
    if (own(e).toLowerCase() !== kind.toLowerCase() || e.closest('button') || !visible(e)) continue;
    let box = e;
    while (box && !(/Origine/.test(text(box)) && /Destination/.test(text(box)))) box = box.parentElement;
    if (!box || seen.has(box)) continue;
    seen.add(box);
    const leaves = [...box.querySelectorAll('*')].map(own).filter(Boolean);
    const at = (label) => leaves.findIndex((t) => t.replace(/\\s*:$/, '') === label);
    const o = at('Origine'), d = at('Destination');
    const after = d >= 0 ? leaves.slice(d + 1) : [];
    const address = after.map((t) => ADDRESS.exec(t)).find(Boolean);
    const hours = leaves.map((t) => /(\\d+)\\s*heures?/i.exec(t)).find(Boolean);
    const clock = leaves.map((t) => /^(?:(\\d+)\\s*j\\s*)?(\\d+):(\\d{2}):(\\d{2})$/.exec(t)).find(Boolean);
    out.push({
      origin: o >= 0 ? (leaves[o + 1] || '') : '',
      site: address ? address[0] : '',
      name: after.length && !ADDRESS.test(after[0]) ? after[0] : '',
      duration: hours ? Number(hours[1]) : null,
      left: clock ? (Number(clock[1] || 0) * 86400 + Number(clock[2]) * 3600 + Number(clock[3]) * 60 + Number(clock[4])) : null,
    });
  }
  return out;
}"""
)

# La page a reçu ses données : « Missions en cours » montre des cartes ou « Aucune mission disponible » (vrai jeu : ~1,5 s
# après l'ouverture ; avant, les stocks, Naël et les missions affichent des valeurs provisoires).
MISSIONS_READY_JS = (
    "() => {"
    + _PRELUDE_JS
    + """
  const head = [...document.querySelectorAll('main *')].find((e) => own(e) === 'Missions en cours' && visible(e));
  let box = head;
  while (box && box.parentElement && text(box) === text(head)) box = box.parentElement;
  return !!box && (/Aucune mission/i.test(text(box)) || (/Origine/.test(text(box)) && /Destination/.test(text(box))));
}"""
)

READY_SECONDS = 4.0  # attente d'un bouton ou d'un état qui se met à jour après le chargement des données
STABLE_SECONDS = 6.0  # attente de deux lectures identiques et cohérentes de « Missions en cours »
STABLE_POLL_SECONDS = 0.75


async def stable_read(read, consistent, seconds: float = STABLE_SECONDS):
    """Relit jusqu'à deux lectures identiques et cohérentes (vu sur le vrai jeu : « Aucune mission disponible » s'affiche
    un instant avant les vraies missions). Renvoie (lecture, cohérente)."""
    end = time.monotonic() + seconds
    last = None
    while True:
        value = await read()
        ok = consistent(value)
        if ok and value == last:
            return value, True
        if time.monotonic() >= end:
            return value, ok
        last = value
        await asyncio.sleep(STABLE_POLL_SECONDS)


async def wait_missions_loaded(page, seconds: float = PAGE_WAIT_SECONDS) -> None:
    """Attend que la page Opérations ait reçu ses données (« Missions en cours » rempli), puis une courte pause."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if await page.evaluate(MISSIONS_READY_JS):
            await asyncio.sleep(0.5)
            return
        await asyncio.sleep(0.25)
    raise GameUiError("la liste « Missions en cours » ne se charge pas")


async def wait_enabled(locator, seconds: float = READY_SECONDS) -> bool:
    end = time.monotonic() + seconds
    while True:
        if await locator.count() and await locator.first.is_enabled():
            return True
        if time.monotonic() >= end:
            return False
        await asyncio.sleep(0.25)


# Héros : marque la ligne « Mira Venn » si elle est disponible. Réponses : checked, marked, busy, absent.
MARK_MIRA_JS = (
    "(attribute) => {"
    + _PRELUDE_JS
    + """
  document.querySelectorAll('[' + attribute + ']').forEach((e) => e.removeAttribute(attribute));
  for (const radio of document.querySelectorAll('main input[type=radio]')) {
    if (!visible(radio.closest('label') || radio.parentElement || radio)) continue;
    let box = radio;
    while (box && !/Mira Venn/.test(text(box))) {
      box = box.parentElement;
      if (box && box.querySelectorAll('input[type=radio]').length > 1) { box = null; break; }
    }
    if (!box) continue;
    if (!/Disponible/i.test(text(box))) return 'busy';
    if (radio.checked) return 'checked';
    box.setAttribute(attribute, '1');
    return 'marked';
  }
  return 'absent';
}"""
)

MIRA_CHECKED_JS = (
    "() => {"
    + _PRELUDE_JS
    + """
  return [...document.querySelectorAll('main input[type=radio]')].some((radio) => {
    let box = radio;
    while (box && !/Mira Venn/.test(text(box)) && box.querySelectorAll('input[type=radio]').length <= 1) box = box.parentElement;
    return !!box && /Mira Venn/.test(text(box)) && box.querySelectorAll('input[type=radio]').length <= 1 && radio.checked;
  });
}"""
)

# Secours : lignes « Planètes libres <adresse> » de l'onglet Ensemble qui proposent la Fouille.
FREE_ROWS_JS = (
    "() => {"
    + _PRELUDE_JS
    + """
  const out = [];
  for (const row of document.querySelectorAll('.continuous-inspector__list-row')) {
    if (!/^Planètes? libres?/i.test(text(row)) || !row.querySelector('button[aria-label="Fouille"]')) continue;
    const m = ADDRESS.exec(text(row));
    if (m && !out.includes(m[0])) out.push(m[0]);
  }
  return out;
}"""
)


def nearest_sites(objects: list, planet_address: str | None, bounds, limit: int = SITES_KEPT) -> list[dict]:
    """Emplacements libres où la Fouille est possible, du plus proche au plus lointain (positions réelles de la carte)."""
    sites = [o for o in objects if isinstance(o, dict) and o.get("kind") == "FREE_SLOT" and o.get("address")
             and "EXCAVATION" in (o.get("capabilities") or []) and isinstance(o.get("positionMilli"), list) and len(o["positionMilli"]) == 2]
    if not sites:
        return []
    own = next((o for o in objects if isinstance(o, dict) and planet_address and o.get("address") == planet_address
                and isinstance(o.get("positionMilli"), list) and len(o["positionMilli"]) == 2), None)
    if own is not None:
        px, py = own["positionMilli"][0] / 1000, own["positionMilli"][1] / 1000
    elif isinstance(bounds, list) and len(bounds) == 2:  # la fenêtre est centrée sur la planète
        px, py = (bounds[0][0] + bounds[1][0]) / 2000, (bounds[0][1] + bounds[1][1]) / 2000
    else:
        return []

    def distance(o: dict) -> float:
        return math.hypot(o["positionMilli"][0] / 1000 - px, o["positionMilli"][1] / 1000 - py)

    ranked = sorted(sites, key=distance)[:limit]
    return [{"address": o["address"], "distance": round(distance(o), 1), "source": "carte"} for o in ranked]


def draw_delay(settings: dict) -> float:
    """Délai avant relance, en secondes, tiré au hasard entre le minimum et le maximum choisis par le joueur."""
    return random.uniform(settings["delay_min"], settings["delay_max"]) * 60


def sync_tracked(tracked: list[dict], missions: list[dict], settings: dict, now: float, complete: bool = True,
                 wanted: list[dict] | None = None) -> list[dict]:
    """Met à jour les Fouilles suivies d'après « Missions en cours ».

    - une Fouille encore en cours : sa fin est celle que le jeu affiche (retards compris) et le délai tiré reste le même ;
    - une Fouille terminée (absente de la liste) : sa place reste occupée jusqu'à sa fin + délai, puis elle est oubliée (la place
      est libre) ; si la liste lue ne compte pas autant de Fouilles que le compteur du jeu (`complete` faux), rien n'est oublié ;
    - une Fouille du jeu que le bot ne suivait pas (lancée à la main, bot redémarré…) est adoptée par une Fouille réglée libre
      (`wanted`, voir excavations.slots) de la même planète et de la même durée ; une Fouille suivie sans numéro (ancien suivi)
      reçoit de même celui d'une Fouille réglée libre."""
    wanted = slots(settings, []) if wanted is None else wanted
    out: list[dict] = []
    matched: set[int] = set()
    for entry in tracked:
        index = next((i for i, m in enumerate(missions) if i not in matched and m["origin"] == entry["planet"] and m["site"] == entry["site"]), None)
        if index is not None:
            matched.add(index)
            left = missions[index]["left"]
            end = now + left if left is not None else entry["end"]
            out.append({**entry, "end": end, "ready": end + (entry["ready"] - entry["end"])})
        elif not complete:
            out.append(entry)
        else:
            end = min(entry["end"], now)  # absente de la liste : terminée au plus tard maintenant
            ready = end + (entry["ready"] - entry["end"])
            if now < ready:
                out.append({**entry, "end": end, "ready": ready})
    taken = {(t["planet"], t["slot"]) for t in out if t.get("slot") is not None}

    def free_slot(planet: str, duration) -> int | None:
        index = next((w["index"] for w in wanted if w["planet"] == planet and w["duration"] == duration and (planet, w["index"]) not in taken), None)
        if index is not None:
            taken.add((planet, index))
        return index

    out = [t if t.get("slot") is not None else {**t, "slot": free_slot(t["planet"], t["duration"])} for t in out]
    for i, m in enumerate(missions):
        if i in matched:
            continue
        index = free_slot(m["origin"], m["duration"])
        if index is None:
            continue
        end = now + (m["left"] or 0)
        out.append({"planet": m["origin"], "slot": index, "duration": m["duration"], "site": m["site"], "end": end, "ready": end + draw_delay(settings)})
    return out


class ExcavationPass(ExplorationPass):
    """Relevé du jour puis lancement des Fouilles des lignes réglées, dans la limite des places de chaque ligne et du jeu."""

    feature_id = "excavations"

    # ------------------------------------------------------------------ relevé du jour
    def _planets_of(self, settings: dict) -> list[str]:
        return list(dict.fromkeys(w["planet"] for w in slots(settings, self._all_planets())))

    async def _sites_of(self, planets: list[str]) -> tuple[dict, list[str]]:
        sites: dict = {}
        problems: list[str] = []
        for planet in planets:
            try:
                sites[planet] = await self._find_sites(planet)
                await self._remember(planet)
            except GameUiError as exc:
                problems.append(f"{planet} : {exc}")
                self.bot.log(f"Fouilles : pas de site libre relevé pour {planet} ({exc}).", "warn", event=True)
        return sites, problems

    async def _complete_daily(self, settings: dict) -> None:
        """Planètes de départ choisies après le relevé du jour : leurs sites sont relevés maintenant (une fois par jour chacune)."""
        missing = [p for p in self._planets_of(settings) if p not in self.store.daily()["tried"]]
        if not missing:
            return
        sites, problems = await self._sites_of(missing)
        self.store.add_sites(sites, missing, " ; ".join(problems))
        found = ", ".join(f"{p} → {v[0]['address']}" for p, v in sites.items() if v) or "aucun"
        self.bot.log(f"Fouilles : sites relevés pour {', '.join(missing)} — le plus proche : {found}.", event=True)

    async def _daily_scan(self, settings: dict, source: str) -> None:
        planets = self._planets_of(settings)
        sites, problems = await self._sites_of(planets)
        max_active = None
        try:
            await self._open_excavations()
            counters = await self._eval(COUNTERS_JS)
            max_active = counters["excavations"][1] if counters.get("excavations") else None
        except GameUiError as exc:
            problems.append(f"compteur des Fouilles : {exc}")
        self.store.set_daily(sites, max_active, source, " ; ".join(problems), tried=planets)
        found = ", ".join(f"{p} → {len(v)} site(s), le plus proche {v[0]['address']}" for p, v in sites.items() if v) or "aucun"
        self.bot.log(f"Fouilles : relevé du jour ({source}) — {found} ; Fouilles actives au plus : {max_active}.", event=True)

    async def _find_sites(self, planet: str) -> list[dict]:
        coords = self._coords(planet)
        found = await self._search_map(planet, lambda objects, bounds: nearest_sites(objects, coords, bounds))
        if found:
            return found
        rows = await self._eval(FREE_ROWS_JS)  # secours : la liste « Ensemble », classée par la formule des adresses
        if rows:
            ranked = sorted(rows, key=lambda a: (formula_distance(coords or "", a) is None, formula_distance(coords or "", a) or 0.0))
            return [{"address": a, "distance": formula_distance(coords or "", a), "source": "formule"} for a in ranked[:SITES_KEPT]]
        raise GameUiError("aucun emplacement libre trouvé autour de la planète")

    # ------------------------------------------------------------------ page Fouille
    async def _open_excavations(self) -> None:
        await self._goto(EXCAVATION_PATH)
        end = time.monotonic() + PAGE_WAIT_SECONDS
        while time.monotonic() < end:
            if await self.page.evaluate(MARK_TARGET_JS, TARGET_ATTRIBUTE) and (await self._eval(COUNTERS_JS)).get("excavations"):
                await self.page.evaluate(CLEAR_MARK_JS, TARGET_ATTRIBUTE)
                await wait_missions_loaded(self.page)
                return
            await asyncio.sleep(0.25)
        raise GameUiError("la page Opérations › Fouille ne s'affiche pas")

    async def _counters(self) -> dict:
        raw = await self._eval(COUNTERS_JS)
        if not raw.get("excavations") or not raw.get("missions"):
            raise GameUiError("compteurs de Fouilles illisibles")
        return raw

    def next_due(self) -> float | None:
        """Heure (epoch) où une place se libère : la plus proche fin de Fouille + délai. None : pas de Fouille suivie
        (l'ordonnanceur repasse à la vérification de secours)."""
        # Une heure déjà passée = place libre que ce passage n'a pas pu remplir : on ne repasse pas toutes les minutes.
        upcoming = [t["ready"] for t in self.store.tracked() if t["ready"] > time.time()]
        return min(upcoming) if upcoming else None

    @staticmethod
    def _full(counters: dict) -> str:
        (exc, exc_max), (mis, mis_max) = counters["excavations"], counters["missions"]
        if exc >= exc_max:
            return f"toutes les Fouilles possibles sont en cours ({exc}/{exc_max})"
        if mis >= mis_max:
            return f"plus de place pour une mission active ({mis}/{mis_max})"
        return ""

    # ------------------------------------------------------------------ passage
    async def run_pass(self) -> str:
        s = self.store.get()
        wanted = slots(s, self._all_planets())
        unset = "Fouilles non réglées (équipe ou délai avant relance, onglet Fouilles), rien à lancer"
        if s["delay_min"] is None or any(not w["units"] for w in wanted) or (not wanted and not s["units"]):
            return unset
        if not wanted:
            return "aucune planète connue : lancez un scan des planètes (Réglages › Compte et univers)"
        launched: list[str] = []
        waiting = 0
        stop = ""
        try:
            if self.store.daily_due():
                await self._daily_scan(s, "quotidien")
            else:
                await self._complete_daily(s)
            await self._open_excavations()
            async def read():
                return await self.page.evaluate(MISSIONS_JS, "Fouille"), (await self._counters())["excavations"][0]

            (missions, _count), complete = await stable_read(read, lambda v: len(v[0]) == v[1])
            if not complete:
                self.bot.log(f"Fouilles : {len(missions)} Fouille(s) lue(s) dans « Missions en cours » pour un compteur différent ; aucune place n'est libérée ce passage.", "warn", event=True)
            tracked = sync_tracked(self.store.tracked(), missions, s, time.time(), complete, wanted)
            self.store.set_tracked(tracked)
            busy = {m["site"] for m in missions} | {t["site"] for t in tracked}
            stuck: set[str] = set()  # planètes sans site libre ou dont un lancement a échoué : leurs autres Fouilles attendent
            for slot in wanted:
                if any((t["planet"], t.get("slot")) == (slot["planet"], slot["index"]) for t in tracked):
                    waiting += 1  # Fouille en cours (ou délai avant relance pas écoulé)
                    continue
                if slot["planet"] in stuck:
                    continue
                stop, sentence = await self._serve(slot, s, tracked, busy)
                if stop:
                    break
                if sentence is None:
                    stuck.add(slot["planet"])
                else:
                    launched.append(sentence)
        except Interrupted as exc:
            return self._report(launched, waiting, f"interrompu ({exc})")
        except GameUiError as exc:
            self.bot.log(f"Fouilles : {exc}.", "warn", event=True)
            return self._report(launched, waiting, f"arrêté ({exc})")
        return self._report(launched, waiting, stop)

    async def _serve(self, slot: dict, s: dict, tracked: list[dict], busy: set[str]) -> tuple[str, str | None]:
        """Lance une Fouille réglée libre. Renvoie (raison d'arrêt général, phrase du lancement ou None si rien n'est parti)."""
        stop = self._full(await self._counters())
        if stop:
            return stop, None
        room, why = await self._mission_room()  # places gardées libres et priorités (onglet Places de mission)
        if room <= 0:
            return f"plus de place de mission ({why})", None
        planet = slot["planet"]
        site = next((x["address"] for x in self.store.daily()["sites"].get(planet, []) if x["address"] not in busy), None)
        if site is None:
            self._skip(planet, "aucun site libre connu sans Fouille en cours")
            return "", None
        sentence = await self._launch(slot, site)
        if sentence is None:
            return "", None
        end = time.time() + slot["duration"] * 3600
        tracked.append({"planet": planet, "slot": slot["index"], "duration": slot["duration"], "site": site, "end": end, "ready": end + draw_delay(s)})
        self.store.set_tracked(tracked)
        busy.add(site)
        return "", sentence

    @staticmethod
    def _report(launched: list[str], waiting: int, stop: str) -> str:
        parts = [f"{len(launched)} Fouille(s) lancée(s)" + (" : " + ", ".join(launched) if launched else "")]
        if waiting:
            parts.append(f"{waiting} Fouille(s) déjà en cours")
        if stop:
            parts.append(stop)
        return " ; ".join(parts)

    def _skip(self, planet: str, reason: str) -> None:
        self.bot.log(f"Fouilles : {planet} sautée ({reason}).", event=True)

    async def _launch(self, line: dict, site: str) -> str | None:
        """Prépare et lance une Fouille réglée (`line` : planète, équipe, durée, Mira) vers le site. None : rien n'est lancé."""
        page = self.page
        planet = line["planet"]
        await self._open_excavations()
        await self._select_planet(planet)
        await self._settle(planet)
        if not await self._fill_target(site):
            self._skip(planet, f"cible {site} refusée par le jeu")
            return None
        for name, qty in line["units"].items():
            field = page.get_by_label(f"Quantité {name}", exact=True)
            if not await field.count():
                self._skip(planet, f"unité « {name} » absente")
                return None
            await self._guard()
            await human.click(page, field.first, self.settings)
            await field.first.fill(str(qty))
        if not await self._choose_duration(line["duration"]):
            self._skip(planet, f"durée de {line['duration']} h indisponible")
            return None
        hero = await self._pick_mira() if line["mira"] else ""
        go = page.get_by_role("button", name=LAUNCH_NAME)
        if not await wait_enabled(go):  # le jeu valide l'équipe et les coûts un instant après la saisie
            self._skip(planet, "« Lancer la fouille » reste grisé (unités, énergie ou RT insuffisants, ou site déjà fouillé)")
            return None
        before = await self._counters()
        await self._guard()
        await human.think(self.settings)
        await human.click(page, go.first, self.settings)
        sentence = f"{describe(line)} vers {site}{hero}"
        await self._confirm_launch(before, sentence)
        return sentence

    async def _choose_duration(self, hours: int) -> bool:
        button = self.page.locator("main").get_by_role("button", name=f"{hours} heures", exact=True)
        if not await button.count():
            return False
        if await button.first.get_attribute("aria-pressed") != "true":
            await self._guard()
            await human.click(self.page, button.first, self.settings)
            await asyncio.sleep(0.3)
        return await button.first.get_attribute("aria-pressed") == "true"

    async def _pick_mira(self) -> str:
        """Affecte Mira Venn si elle est disponible ; sinon la Fouille part sans héros (choix du joueur)."""
        page = self.page
        state = await page.evaluate(MARK_MIRA_JS, HERO_ATTRIBUTE)
        if state == "checked":
            return ", avec Mira Venn"
        if state != "marked":
            self.bot.log("Fouilles : Mira Venn n'est pas disponible, la Fouille part sans héros.", event=True)
            return ", sans héros (Mira Venn occupée)"
        try:
            await self._guard()
            await human.click(page, page.locator(f"[{HERO_ATTRIBUTE}]").first, self.settings)
        finally:
            await page.evaluate(CLEAR_MARK_JS, HERO_ATTRIBUTE)
        await asyncio.sleep(0.3)
        if await page.evaluate(MIRA_CHECKED_JS):
            return ", avec Mira Venn"
        self.bot.log("Fouilles : Mira Venn n'a pas pu être sélectionnée, la Fouille part sans héros.", "warn", event=True)
        return ", sans héros"

    async def _confirm_launch(self, before: dict, sentence: str) -> None:
        """Le compteur « Fouilles actives » doit monter après le clic ; sinon le lancement est « non confirmé »."""
        end = time.monotonic() + builder.CONFIRM_SECONDS
        while time.monotonic() < end:
            try:
                now = await self._counters()
            except GameUiError:
                now = None
            if now is not None and now["excavations"][0] > before["excavations"][0]:
                self._unconfirmed = 0
                self.bot.log(f"Fouille lancée : {sentence}.", event=True, kind="launch")
                return
            await asyncio.sleep(0.3)
        self._unconfirmed += 1
        self.bot.log(f"Clic sur « Lancer la fouille » fait ({sentence}) mais le compteur n'a pas bougé.", "warn", event=True, kind="attention")
        if self._unconfirmed >= UNCONFIRMED_LIMIT:
            self.bot.scheduler.update(self.feature_id, {"enabled": False})
            self.bot.log("Fouilles désactivées : deux lancements de suite non confirmés. Vérifiez à l'écran avant de les réactiver.", "error", event=True, kind="attention")
            raise Interrupted("désactivée après deux lancements non confirmés")
