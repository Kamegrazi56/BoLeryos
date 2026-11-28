"""Transport de ressources vers une planète qui ne peut pas payer sa construction (Opérations › Transport).

Relevé sur le vrai jeu (2026-11-23, Asterion, lecture seule) :
  - `operations?method=PILLARS|FLEET&mission=TRANSPORT` : bloc 1 « Coordonnées » (champ « Cible » et liste « Vos colonies » :
    un bouton par planète, « <nom> <adresse> »), bloc 2 « Composition » (bouton « Modèles », champs « Quantité <unité> »),
    bloc 3 « Logistique et chargement » (« Capacité de soute », champs « Cargaison : Fer|Cristal|Éther ») ;
  - bouton de lancement `#raid-launch-btn` (Flotte, « LANCEMENT BLOQUÉ » tant qu'il est grisé) ou `#pillars-launch-btn`
    (Piliers, « VORTEX BLOQUÉ »). Par les Piliers l'envoi est instantané ; par la Flotte il prend une place de mission.
Par la Flotte, la composition se fait vaisseau par vaisseau (voir transport_ships.py), pas par modèle enregistré.
Le bot ne touche jamais à « Mobilisation générale », « Réinitialiser » ni aux boutons de Piliers ou de champ de force.
"""

from __future__ import annotations

import asyncio
import math
import random
import re
import time

from . import human
from .builder import UNCONFIRMED_LIMIT, debit_seen, parse_duration
from .excavator import wait_enabled
from .explorations import METHODS
from .explorer import CLEAR_MARK_JS, LAUNCH_STATE_JS, MARK_TARGET_JS, TARGET_ATTRIBUTE, TARGET_VALUE_JS, ExplorationPass
from .numbers import parse_amount
from .planets import GameUiError, Interrupted
from .slots import MISSIONS_COUNT_JS, allowance
from .transport_ships import HOLD_JS, SHIP_ROWS_JS, fit_cargo, parse_available, parse_volume, pick_ships
from .transports import RESOURCES, with_margin

PAGE_WAIT_SECONDS = 10.0
LAUNCH_WAIT_SECONDS = 4.0
TRANSIT_FALLBACK_SECONDS = 1800.0  # temps de vol illisible : on attend 30 minutes avant de renvoyer vers la même planète
HUMAN_EXTRA_SECONDS = (30.0, 300.0)  # délai variable ajouté au temps de vol (comportement humain : 30 s à 5 min)
RETRY_COOLDOWN_SECONDS = 600.0  # après un envoi par les Piliers qui n'a pas suffi : pas de nouvel essai tout de suite
LAUNCH_IDS = {"PILLARS": "#pillars-launch-btn", "FLEET": "#raid-launch-btn"}
BLOCK_GAP_SECONDS = 0.3
SENT_NOTE = "arrivée immédiate"
# Valeur qui suit « Temps de trajet » sur la page (relevé du vrai jeu : « — » tant que la mission n'est pas valide).
TRAVEL_TIME_JS = """() => {
  const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
  const leaves = [...document.querySelectorAll('main *')].filter((e) => e.children.length === 0 && e.getClientRects().length > 0 && text(e));
  const i = leaves.findIndex((e) => /^Temps de trajet$/i.test(text(e)));
  return i >= 0 && leaves[i + 1] ? text(leaves[i + 1]) : null;
}"""
_CLOCK_RE = re.compile(r"^(?:(\d+)\s*[jd]\s*)?(\d{1,3}):(\d{2}):(\d{2})$", re.IGNORECASE)


def parse_travel(text: str | None) -> float | None:
    """« 00:37:14 » → 2234.0 ; « 1j 02:03:04 » ou « 37m 14s » acceptés. None si illisible ou nul (« — »)."""
    raw = (text or "").strip()
    match = _CLOCK_RE.match(raw)
    if match:
        days, hours, minutes, seconds = (int(v or 0) for v in match.groups())
        total = days * 86400.0 + hours * 3600.0 + minutes * 60.0 + seconds
    else:
        total = parse_duration(raw) or 0.0
    return total if total > 0 else None


def transit_wait(travel: float | None) -> float:
    """Attente avant un nouvel envoi vers la même planète : temps de vol + 30 s à 5 min au hasard."""
    return (travel if travel else TRANSIT_FALLBACK_SECONDS) + random.uniform(*HUMAN_EXTRA_SECONDS)


def _closed_piliers(bot, planet: str) -> bool:
    """Les Piliers de la planète sont-ils fermés ? (état lu dans « Gérer les Piliers » ; inconnu : on essaie quand même)."""
    info = (bot.pillars.status().get("planets") or {}).get(planet) or {}
    return info.get("closed") is True


def _amounts(remaining: dict[str, int], stocks: dict[str, float | None]) -> dict[str, int]:
    """Ce que la donneuse peut envoyer : le besoin restant, plafonné par son stock du moment."""
    out: dict[str, int] = {}
    for name in RESOURCES:
        have = stocks.get(name)
        if remaining.get(name, 0) > 0 and have is not None and have >= 1:
            out[name] = min(remaining[name], math.floor(have))
    return out


class TransportPass(ExplorationPass):
    """Envoi des ressources qui manquent à une planète, depuis les planètes donneuses."""

    feature_id = "constructions"  # places de mission : comme les constructions, sans priorité particulière

    def __init__(self, bot) -> None:
        super().__init__(bot, bot.explorations)
        self._until: dict[str, float] = {}  # planète destinataire → heure avant laquelle on n'envoie plus rien
        self._travel: float | None = None  # temps de vol lu avant le dernier lancement spatial

    def cooling(self, planet: str) -> bool:
        return self._until.get(planet, 0.0) > time.monotonic()

    def hold(self, planet: str, seconds: float) -> None:
        self._until[planet] = max(self._until.get(planet, 0.0), time.monotonic() + seconds)

    # ------------------------------------------------------------------ envoi
    async def supply(self, target: str, missing: dict[str, float]) -> dict:
        """Envoie le manque (plus la marge) vers `target`. Retourne {"sent": {ressource: quantité}, "instant": bool, "note": str}.
        Ne lève que `Interrupted` ; une impossibilité est dite dans `note` (et au journal)."""
        s = self.bot.transports.get()
        out = {"sent": {}, "instant": False, "note": ""}
        coords = self._coords(target)
        if not coords:
            out["note"] = f"adresse de {target} inconnue (lancez un scan des planètes)"
            self.bot.log(f"Transport : {out['note']}.", "warn", event=True)
            return out
        remaining = with_margin(missing, s["margin"])
        reasons: list[str] = []
        try:
            for donor in s["donors"]:
                if not any(v > 0 for v in remaining.values()):
                    break
                if donor == target:
                    continue
                done = await self._from_donor(donor, target, coords, remaining, s, reasons)
                if done is None:
                    continue
                method, sent = done
                for name, qty in sent.items():
                    out["sent"][name] = out["sent"].get(name, 0) + qty
                    remaining[name] = max(0, remaining.get(name, 0) - qty)
                out["instant"] = out["instant"] or method == "PILLARS"
                if method == "FLEET":
                    wait = transit_wait(self._travel)
                    self.hold(target, wait)
                    self._log_transit(target, wait)
        except GameUiError as exc:
            reasons.append(str(exc))
            self.bot.log(f"Transport : {exc}.", "warn", event=True)
        out["note"] = " ; ".join(reasons)
        if not out["sent"]:
            self.hold(target, RETRY_COOLDOWN_SECONDS)
        return out

    def _log_transit(self, target: str, wait: float) -> None:
        known = "temps de vol lu" if self._travel else "temps de vol illisible, 30 min comptées"
        self.bot.log(f"Transport spatial vers {target} : prochain envoi dans {round(wait / 60)} min ({known}, plus un délai aléatoire).", event=True)

    async def _from_donor(self, donor: str, target: str, coords: str, remaining: dict[str, int], s: dict,
                          reasons: list[str]) -> tuple[str, dict[str, int]] | None:
        """Essaie la voie choisie, puis le secours spatial si les Piliers sont fermés ou refusent. None : donneuse sautée."""
        attempts = [(s["method"], s["ships"] if s["method"] == "FLEET" else s["models"])]
        if s["method"] == "PILLARS":
            if _closed_piliers(self.bot, donor):
                self.bot.log(f"Transport : Piliers fermés sur {donor}, essai en spatial.", event=True)
                attempts = []
            if s["ships"]:
                attempts.append(("FLEET", s["ships"]))
        for method, choice in attempts:
            sent, why = await self._send(donor, target, coords, remaining, method, choice)
            if sent:
                return method, sent
            reasons.append(f"{donor} ({METHODS[method]}) : {why}")
            if method == "PILLARS":
                self.bot.log(f"Transport : envoi par les Piliers impossible depuis {donor} ({why})"
                             + (", essai en spatial." if s["ships"] else "."), event=True)
        if not attempts:
            reasons.append(f"{donor} : Piliers fermés et aucun vaisseau de secours choisi")
        return None

    async def _send(self, donor: str, target: str, coords: str, remaining: dict[str, int], method: str,
                    choice: list[str]) -> tuple[dict[str, int], str]:
        """Prépare et lance un envoi depuis `donor`. `choice` : modèles (Piliers) ou vaisseaux (Flotte), dans l'ordre d'essai.
        Retourne (quantités envoyées, raison de l'échec)."""
        page = self.page
        if method == "FLEET":
            room, why = await self._room()
            if room <= 0:
                return {}, f"plus de place de mission ({why})"
        await self._open_transport(method)
        await self._select_planet(donor)
        await self._settle(donor)
        wanted = _amounts(remaining, await self._stocks())
        if not wanted:
            return {}, "rien à envoyer (stock de la donneuse insuffisant)"
        if not await self._pick_target(target, coords):
            return {}, f"destination {target} refusée par le jeu"
        go = page.locator(LAUNCH_IDS[method])
        wanted, why = await self._compose(donor, method, choice, wanted, go)
        if not wanted:
            return {}, why
        sent = await self._cargo_values(wanted)
        self._travel = parse_travel(await page.evaluate(TRAVEL_TIME_JS)) if method == "FLEET" else None
        before, rates, at = await self._stocks_timed()
        await self._guard()
        await human.think(self.settings)
        await human.click(page, go.first, self.settings)
        sentence = ", ".join(f"{qty} {name}" for name, qty in sent.items()) + f" de {donor} vers {target} ({METHODS[method]})"
        return await self._confirm_send(before, rates, at, sent, sentence)

    async def _confirm_send(self, before: dict, rates: dict, at: float, sent: dict[str, int], sentence: str) -> tuple[dict[str, int], str]:
        if await self._stock_debited(before, rates, at, sent):
            self._unconfirmed = 0
            self.bot.log(f"Transport lancé : {sentence}.", event=True, kind="launch")
            return sent, ""
        self._unconfirmed += 1
        self.bot.log(f"Clic de lancement fait ({sentence}) mais le débit des ressources n'a pas été constaté.", "warn", event=True, kind="attention")
        if self._unconfirmed >= UNCONFIRMED_LIMIT:
            self.bot.transports.replace({**self.bot.transports.get(), "enabled": False})
            self.bot.log("Transport désactivé : deux envois de suite non confirmés. Vérifiez à l'écran avant de le réactiver.", "error", event=True, kind="attention")
            raise Interrupted("transport désactivé après deux envois non confirmés")
        return {}, "envoi non confirmé"

    async def _compose(self, donor: str, method: str, choice: list[str], wanted: dict[str, int],
                       go) -> tuple[dict[str, int], str]:
        """Compose l'envoi et remplit la cargaison jusqu'à ce que le lancement soit possible.
        Retourne (cargaison prête, raison d'échec) ; cargaison vide = échec."""
        if method == "FLEET":
            return await self._compose_ships(donor, choice, wanted, go)
        for model in choice:
            if not await self._apply_model(model):
                continue
            await self._fill_cargo(wanted)
            if await wait_enabled(go, LAUNCH_WAIT_SECONDS):
                return wanted, ""
        return {}, await self._blocked_reason()

    async def _blocked_reason(self) -> str:
        state = await self.page.evaluate(LAUNCH_STATE_JS)
        shown = " ; ".join(state["buttons"] + state["facts"]) or "rien de lisible"
        return f"lancement impossible avec la composition choisie ; la page affiche : {shown}"

    async def _compose_ships(self, donor: str, chosen: list[str], wanted: dict[str, int], go) -> tuple[dict[str, int], str]:
        """Voie Flotte : le nombre de vaisseaux est calculé pour porter la cargaison (soute lue sur le jeu), dans la limite de
        ceux qui sont disponibles sur la planète à cet instant ; si la soute ne suffit pas, la cargaison est réduite d'autant."""
        page = self.page
        available = parse_available(await page.evaluate(SHIP_ROWS_JS))
        hold: dict[str, int] = {}
        for name in chosen:
            if available.get(name, 0) > 0:
                hold[name] = self.bot.transport_ships.hold_of(name) or await self._measure(name) or 0
        counts, room = pick_ships(chosen, available, hold, sum(wanted.values()))
        if not counts:
            return {}, "aucun des vaisseaux choisis n'est disponible sur la planète (ou leur soute est inconnue)"
        for name, count in counts.items():
            await self._set_quantity(name, count)
        wanted = fit_cargo(wanted, room)
        await self._fill_cargo(wanted)
        if await wait_enabled(go, LAUNCH_WAIT_SECONDS):
            return wanted, ""
        return {}, await self._blocked_reason()

    async def _set_quantity(self, name: str, count: int) -> None:
        field = self.page.get_by_label(f"Quantité {name}", exact=True)
        if not await field.count():
            raise GameUiError(f"champ « Quantité {name} » introuvable")
        await self._guard()
        await human.click(self.page, field.first, self.settings)
        await field.first.fill(str(count) if count else "")

    async def _measure_ship(self, name: str) -> tuple[int | None, int | None]:
        """(capacité de soute, espace libre) d'UN vaisseau : on en met un dans la composition, on lit la page, on le retire."""
        await self._set_quantity(name, 1)
        try:
            end = time.monotonic() + LAUNCH_WAIT_SECONDS
            while time.monotonic() < end:
                hold = await self.page.evaluate(HOLD_JS)
                cargo, free = parse_volume(hold["capacity"]), parse_volume(hold["free"])
                if cargo:
                    return cargo, free
                await asyncio.sleep(0.25)
            return None, None
        finally:
            await self._set_quantity(name, 0)

    async def _measure(self, name: str) -> int | None:
        cargo, free = await self._measure_ship(name)
        return free or cargo

    # ------------------------------------------------------------------ relevé des vaisseaux des planètes donneuses
    async def scan_ships(self, planets: list[str], source: str) -> dict:
        """Relève, planète par planète, les vaisseaux disponibles et la soute d'un vaisseau (lecture seule : rien n'est lancé).
        Ne lève jamais : renvoie {"error": raison} (vide si tout s'est bien passé)."""
        if self.scanning:
            return {"error": "relevé déjà en cours"}
        if not planets:
            return {"error": "Choisissez d'abord au moins une planète donneuse."}
        busy = "une fonctionnalité est en cours, réessayez dans un instant" if self.bot.work.locked() else None
        reason = self.bot.supervisor.game_blocker() or busy
        if reason:
            return {"error": reason[0].upper() + reason[1:]}
        self.scanning = True
        try:
            async with self.bot.work:  # jamais en même temps qu'un passage de fonctionnalité
                await self.bot.claim_bonus()
                problems = await self._scan_each(planets)
            self.bot.log(f"Transport : vaisseaux relevés ({source}) sur {len(planets) - len(problems)} planète(s)"
                         + (f" — {' ; '.join(problems)}" if problems else "") + ".", "warn" if problems else "info", event=True)
            return {"error": " ; ".join(problems)}
        except (Interrupted, GameUiError) as exc:
            self.bot.log(f"Transport : relevé des vaisseaux impossible ({exc}).", "warn", event=True)
            return {"error": f"Relevé impossible : {exc}"}
        finally:
            self.scanning = False

    async def _scan_each(self, planets: list[str]) -> list[str]:
        problems: list[str] = []
        for planet in planets:
            try:
                self.bot.transport_ships.set_planet(planet, await self._read_ships(planet))
            except GameUiError as exc:
                problems.append(f"{planet} : {exc}")  # l'ancien relevé de cette planète est gardé
        return problems

    async def _read_ships(self, planet: str) -> list[dict]:
        await self._open_transport("FLEET")
        await self._select_planet(planet)
        await self._settle(planet)
        available = parse_available(await self.page.evaluate(SHIP_ROWS_JS))
        ships = []
        for name, count in available.items():
            cargo, free = await self._measure_ship(name) if count > 0 else (None, None)  # sans vaisseau, le jeu ne laisse rien saisir
            ships.append({"name": name, "available": count, "cargo": cargo, "free": free})
        return ships

    # ------------------------------------------------------------------ page Opérations › Transport
    async def _open_transport(self, method: str) -> None:
        await self._goto(f"operations?method={method}&mission=TRANSPORT")
        end = time.monotonic() + PAGE_WAIT_SECONDS
        while time.monotonic() < end:
            if await self.page.evaluate(MARK_TARGET_JS, TARGET_ATTRIBUTE):
                await self.page.evaluate(CLEAR_MARK_JS, TARGET_ATTRIBUTE)
                if await self.page.get_by_label("Cargaison : Fer", exact=True).count():
                    return
            await asyncio.sleep(0.25)
        raise GameUiError(f"la page Opérations › {METHODS[method]} › Transport ne s'affiche pas")

    async def _room(self) -> tuple[int, str]:
        count = await self.page.evaluate(MISSIONS_COUNT_JS)
        if not count:
            return 999, ""  # compteur illisible : le jeu refusera de lui-même
        self.bot.slots.set_seen(count[0], count[1])
        return allowance(self.bot, self.feature_id, count[0], count[1])

    async def _pick_target(self, target: str, coords: str) -> bool:
        """Clique sur la planète dans « Vos colonies » ; sinon tape son adresse dans « Cible »."""
        page = self.page
        button = page.locator("main button:visible", has_text=target).filter(has_text=coords)
        if await button.count():
            await self._guard()
            await human.click(page, button.first, self.settings)
            await asyncio.sleep(BLOCK_GAP_SECONDS)
            if await self._target_set(coords):
                return True
        return await self._fill_target(coords) and await self._target_set(coords)

    async def _target_set(self, coords: str) -> bool:
        end = time.monotonic() + PAGE_WAIT_SECONDS / 2
        wanted = coords.replace(" ", "").upper()
        while time.monotonic() < end:
            value = await self.page.evaluate(TARGET_VALUE_JS)
            if value and value.replace(" ", "").upper() == wanted:
                return True
            await asyncio.sleep(0.25)
        return False

    async def _fill_cargo(self, wanted: dict[str, int]) -> None:
        page = self.page
        for name in RESOURCES:
            field = page.get_by_label(f"Cargaison : {name}", exact=True)
            if not await field.count():
                raise GameUiError(f"champ « Cargaison : {name} » introuvable")
            await self._guard()
            await human.click(page, field.first, self.settings)
            await field.first.fill(str(wanted.get(name, 0)) if wanted.get(name) else "")

    async def _cargo_values(self, wanted: dict[str, int]) -> dict[str, int]:
        """Cargaison réellement saisie (le jeu peut la plafonner à la soute) : c'est ce qui part."""
        out: dict[str, int] = {}
        for name in RESOURCES:
            raw = await self.page.get_by_label(f"Cargaison : {name}", exact=True).first.input_value()
            try:
                value = int(parse_amount(raw)) if raw.strip() else 0
            except ValueError:
                value = 0
            if value > 0:
                out[name] = min(value, wanted.get(name, value))
        return out

    async def _stock_debited(self, before: dict, rates: dict, at: float, sent: dict[str, int]) -> bool:
        """Le plus gros envoi doit avoir baissé le stock de la donneuse (la production seule ne l'explique pas)."""
        if not sent:
            return False
        key = max(sent, key=lambda k: sent[k])
        if before.get(key) is None:
            return False
        end = time.monotonic() + 8.0
        while time.monotonic() < end:
            now = (await self._stocks()).get(key)
            if now is not None and debit_seen(before[key], now, sent[key], rates.get(key, 0.0), time.monotonic() - at):
                return True
            await asyncio.sleep(0.3)
        return False
