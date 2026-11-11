"""Forages de planètes minières : recherche, suivi des forages en cours et lancements, place par place.

Relevé sur le vrai jeu (2026-11-08, forage Egide → M68 18 22, 1 Foreuse + 200 000 Mineurs) :
  - carte : chaque planète minière arrive avec `kind` MINING et `miningResourceType` fer|cristal|ether (lu par le bot, validé
    par le joueur : pas de sonde) ;
  - Opérations › Flotte › Forage (`operations?method=FLEET&mission=MINING`) : champ « Cible », « Quantité Foreuse orbitale »
    (1), « Quantité Mineur », bouton « Lancer la flotte » ; après le clic : « Calcul serveur en cours », puis « Flotte en route :
    mission Forage. » ;
  - « Missions en cours » : (1) aller « Forage · Arrivée … · compte à rebours · Origine <planète> · Destination Planete miniere
    <adresse> », (2) « Forage · Extraction en cours » (aucune heure de fin : soute pleine ou planète épuisée, retour tout seul),
    (3) « Retour · Arrivée … · Origine Inconnue <adresse minière> · Destination <planète> ».
Une planète minière ne reçoit jamais deux forages. Le bot ne rappelle jamais une flotte.
"""

from __future__ import annotations

import asyncio
import math
import random
import re
import time

from . import human
from .builder import UNCONFIRMED_LIMIT
from .excavator import stable_read, wait_enabled, wait_missions_loaded
from .explorer import CLEAR_MARK_JS, MARK_TARGET_JS, TARGET_ATTRIBUTE, ExplorationPass
from .minings import RESOURCES
from .planets import GameUiError, Interrupted

MINING_PATH = "operations?method=FLEET&mission=MINING"
PAGE_WAIT_SECONDS = 10.0
CONFIRM_SECONDS = 25.0  # « Calcul serveur en cours » dure une dizaine de secondes sur le vrai jeu
LAUNCH_NAME = re.compile(r"^Lancer la flotte", re.I)

# Cartes « Forage » et « Retour » de « Missions en cours » : origine et destination (nom + adresse), temps restant, extraction.
CARDS_JS = """() => {
  const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
  const own = (el) => [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join(' ').replace(/\\s+/g, ' ').trim();
  const ADDRESS = /^[A-Z]\\d{2} \\d{2} \\d{2}$/;
  const out = [];
  const seen = new Set();
  for (const e of document.querySelectorAll('main *')) {
    const kind = own(e);
    if ((kind !== 'Forage' && kind !== 'Retour') || e.closest('button') || !e.getClientRects().length) continue;
    let box = e;
    while (box && !(/Origine/.test(text(box)) && /Destination/.test(text(box)))) box = box.parentElement;
    if (!box || seen.has(box)) continue;
    seen.add(box);
    const leaves = [...box.querySelectorAll('*')].map(own).filter(Boolean);
    const o = leaves.findIndex((t) => /^Origine:?$/.test(t)), d = leaves.findIndex((t) => /^Destination:?$/.test(t));
    if (o < 0 || d < 0) continue;
    const side = (from, to) => {
      const part = leaves.slice(from + 1, to < 0 ? undefined : to);
      return { name: part.find((t) => !ADDRESS.test(t)) || '', address: part.find((t) => ADDRESS.test(t)) || '' };
    };
    const clock = leaves.map((t) => /^(?:(\\d+)\\s*j\\s*)?(\\d+):(\\d{2}):(\\d{2})$/.exec(t)).find(Boolean);
    out.push({
      kind, origin: side(o, d), destination: side(d, -1),
      extracting: leaves.some((t) => /Extraction en cours/i.test(t)),
      left: clock ? (Number(clock[1] || 0) * 86400 + Number(clock[2]) * 3600 + Number(clock[3]) * 60 + Number(clock[4])) : null,
    });
  }
  return out;
}"""

SENT_JS = """() => [...document.querySelectorAll('body *')].some((e) => !e.children.length && e.getClientRects().length
  && /Flotte en route\\s*:\\s*mission Forage/i.test((e.textContent || '').trim()))"""


def draw_delay(settings: dict) -> float:
    return random.uniform(settings["delay_min"], settings["delay_max"]) * 60


def _forage_card(cards: list[dict], planet: str, site: str) -> dict | None:
    return next((c for c in cards if c["kind"] == "Forage" and c["destination"]["address"] == site and c["origin"]["name"] == planet), None)


def _return_card(cards: list[dict], planet: str, site: str) -> dict | None:
    return next((c for c in cards if c["kind"] == "Retour" and c["origin"]["address"] == site and c["destination"]["name"] == planet), None)


def sync_minings(tracked: list[dict], cards: list[dict], settings: dict, now: float) -> tuple[list[dict], list[dict]]:
    """Met à jour les forages suivis d'après « Missions en cours ». Renvoie (suivis, forages terminés ce passage).

    - « Forage » : aller ou extraction, fin inconnue (la flotte revient seule, soute pleine ou planète épuisée) ;
    - « Retour » : fin = arrivée chez soi ; la place se libère à fin + délai tiré au hasard ;
    - absent : terminé au plus tard maintenant ; la place reste occupée jusqu'à fin + délai ;
    - un forage du jeu que le bot ne suivait pas (lancé à la main, bot redémarré) depuis une planète réglée est adopté."""
    out: list[dict] = []
    finished: list[dict] = []
    for t in tracked:
        delay = t.get("delay", 0.0)
        back, going = _return_card(cards, t["planet"], t["site"]), _forage_card(cards, t["planet"], t["site"])
        if back is not None:
            end = now + (back["left"] or 0)
            out.append({**t, "phase": "retour", "end": end, "ready": end + delay})
        elif going is not None:
            out.append({**t, "phase": "extraction" if going["extracting"] else "aller", "end": None, "ready": None})
        else:
            end = min(t["end"], now) if t.get("end") else now
            entry = {**t, "phase": "terminé", "end": end, "ready": end + delay}
            if now < entry["ready"]:
                out.append(entry)
            else:
                finished.append(entry)
    # Adoption : seulement les cartes « Forage » vers une planète minière (une carte « Retour » peut être une autre flotte).
    known = {(t["planet"], t["site"]) for t in out + finished}
    for c in cards:
        planet, site = c["origin"]["name"], c["destination"]["address"]
        if (c["kind"] != "Forage" or not re.search(r"mini", c["destination"]["name"], re.I) or planet not in settings["planets"]
                or not site or (planet, site) in known):
            continue
        known.add((planet, site))
        out.append({"planet": planet, "site": site, "resource": "", "phase": "extraction" if c["extracting"] else "aller",
                    "end": None, "ready": None, "delay": draw_delay(settings)})
    return out, finished


def pick_site(objects: list, planet_address: str | None, bounds, resources: list[str], busy: set[str], preferred: list[str]) -> dict | None:
    """Planète minière de la bonne ressource sans forage en cours : celle d'avant si elle existe encore, sinon la plus proche."""
    mines = [o for o in objects if isinstance(o, dict) and o.get("kind") == "MINING" and o.get("address")
             and str(o.get("miningResourceType", "")).lower() in resources and o["address"] not in busy
             and isinstance(o.get("positionMilli"), list) and len(o["positionMilli"]) == 2]
    if not mines:
        return None
    again = next((o for o in mines if o["address"] in preferred), None)
    if again is not None:
        return {"address": again["address"], "resource": again["miningResourceType"].lower()}
    own = next((o for o in objects if isinstance(o, dict) and planet_address and o.get("address") == planet_address
                and isinstance(o.get("positionMilli"), list)), None)
    if own is not None:
        px, py = own["positionMilli"][0] / 1000, own["positionMilli"][1] / 1000
    elif isinstance(bounds, list) and len(bounds) == 2:
        px, py = (bounds[0][0] + bounds[1][0]) / 2000, (bounds[0][1] + bounds[1][1]) / 2000
    else:
        return None
    best = min(mines, key=lambda o: math.hypot(o["positionMilli"][0] / 1000 - px, o["positionMilli"][1] / 1000 - py))
    return {"address": best["address"], "resource": best["miningResourceType"].lower()}


class MiningPass(ExplorationPass):
    """Forages des planètes minières, place par place, relancés à la fin du forage précédent + le délai du joueur."""

    feature_id = "minings"

    def next_due(self) -> float | None:
        upcoming = [t["ready"] for t in self.store.tracked() if t.get("ready") and t["ready"] > time.time()]
        return min(upcoming) if upcoming else None  # en extraction : fin inconnue, vérification de secours

    async def _open_mining(self) -> None:
        await self._goto(MINING_PATH)
        end = time.monotonic() + PAGE_WAIT_SECONDS
        while time.monotonic() < end:
            if await self.page.evaluate(MARK_TARGET_JS, TARGET_ATTRIBUTE):
                await self.page.evaluate(CLEAR_MARK_JS, TARGET_ATTRIBUTE)
                await wait_missions_loaded(self.page)
                return
            await asyncio.sleep(0.25)
        raise GameUiError("la page Opérations › Flotte › Forage ne s'affiche pas")

    async def _cards(self) -> list[dict]:
        async def read():
            return await self.page.evaluate(CARDS_JS)

        cards, _ok = await stable_read(read, lambda value: True)
        return cards

    async def run_pass(self) -> str:
        s = self.store.get()
        if not s["planets"] or not s["resources"]:
            return "aucune planète de départ ou ressource réglée (onglet Forages), rien à lancer"
        launched: list[str] = []
        try:
            await self._open_mining()
            cards = await self._cards()
            tracked, finished = sync_minings(self.store.tracked(), cards, s, time.time())
            self.store.set_tracked(tracked)
            busy = {t["site"] for t in tracked}
            busy |= {c["destination"]["address"] if c["kind"] == "Forage" else c["origin"]["address"] for c in cards}
            preferred = [f["site"] for f in finished]  # relancer sur la même planète minière si elle existe encore
            for _ in range(max(0, s["slots"] - len(tracked))):
                room, why = await self._mission_room()  # places gardées libres et priorités (onglet Places de mission)
                if room <= 0:
                    self.bot.log(f"Forages : plus de place de mission ({why}).", event=True)
                    return self._summary(launched, f"plus de place de mission ({why})")
                planet = min(s["planets"], key=lambda p: sum(1 for t in tracked if t["planet"] == p))
                site = await self._find_site(planet, s, busy, preferred)
                if site is None:
                    self.bot.log(f"Forages : aucune planète minière ({', '.join(RESOURCES[r] for r in s['resources'])}) libre autour de {planet}.", event=True)
                    break
                if not await self._launch(planet, site["address"], s):
                    break
                delay = draw_delay(s)
                tracked.append({"planet": planet, "site": site["address"], "resource": site["resource"], "phase": "aller",
                                "end": None, "ready": None, "delay": delay})
                self.store.set_tracked(tracked)
                busy.add(site["address"])
                launched.append(f"{planet} → {site['address']} ({RESOURCES.get(site['resource'], site['resource'])})")
        except Interrupted as exc:
            return self._summary(launched, f"interrompu ({exc})")
        except GameUiError as exc:
            self.bot.log(f"Forages : {exc}.", "warn", event=True)
            return self._summary(launched, f"arrêté ({exc})")
        return self._summary(launched, "")

    def _summary(self, launched: list[str], stop: str) -> str:
        tracked = self.store.tracked()
        parts = [f"{len(launched)} forage(s) lancé(s)" + (" : " + ", ".join(launched) if launched else "")]
        parts.append(f"{sum(1 for t in tracked if t['phase'] != 'terminé')} en cours")
        if stop:
            parts.append(stop)
        return " ; ".join(parts)

    async def _find_site(self, planet: str, s: dict, busy: set[str], preferred: list[str]) -> dict | None:
        coords = self._coords(planet)
        return await self._search_map(planet, lambda objects, bounds: pick_site(objects, coords, bounds, s["resources"], busy, preferred))

    async def _launch(self, planet: str, site: str, s: dict) -> bool:
        page = self.page
        await self._open_mining()
        await self._select_planet(planet)
        await self._settle(planet)
        if not await self._fill_target(site):
            self.bot.log(f"Forages : cible {site} refusée par le jeu ({self._target_debug}).", "warn", event=True)
            return False
        for label, value in (("Quantité Foreuse orbitale", "1"), ("Quantité Mineur", str(s["miners"]))):
            field = page.get_by_label(label, exact=True)
            if not await field.count():
                self.bot.log(f"Forages : champ « {label} » absent.", "warn", event=True)
                return False
            if await field.first.input_value() != value:
                await self._guard()
                await human.click(page, field.first, self.settings)
                await field.first.fill(value)
        go = page.get_by_role("button", name=LAUNCH_NAME)
        if not await wait_enabled(go, 8.0):
            self.bot.log(f"Forages : « Lancer la flotte » reste grisé depuis {planet} (Foreuse, Mineurs, Éther ou places de forage ?).", "warn", event=True)
            return False
        await self._guard()
        await human.think(self.settings)
        await human.click(page, go.first, self.settings)
        return await self._confirm(planet, site)

    async def _confirm(self, planet: str, site: str) -> bool:
        end = time.monotonic() + CONFIRM_SECONDS
        while time.monotonic() < end:
            if await self.page.evaluate(SENT_JS) or _forage_card(await self.page.evaluate(CARDS_JS), planet, site):
                self._unconfirmed = 0
                self.bot.log(f"Forage lancé : {planet} → planète minière {site}.", event=True, kind="launch")
                return True
            await asyncio.sleep(0.5)
        self._unconfirmed += 1
        self.bot.log(f"Clic sur « Lancer la flotte » fait ({planet} → {site}) sans confirmation du jeu.", "warn", event=True, kind="attention")
        if self._unconfirmed >= UNCONFIRMED_LIMIT:
            self.bot.scheduler.update(self.feature_id, {"enabled": False})
            self.bot.log("Forages désactivés : deux lancements de suite non confirmés. Vérifiez à l'écran avant de les réactiver.", "error", event=True, kind="attention")
            raise Interrupted("désactivée après deux lancements non confirmés")
        return False
