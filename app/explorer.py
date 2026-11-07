"""Exploration : relevé du jour (brèche la plus proche de chaque planète, modèles du jeu) et lancement des explorations.

Relevé sur le vrai jeu (2026-11-07) :
  - Galaxie : la carte est dessinée (canvas) ; à chaque zoom ou recentrage, la page reçoit du serveur la fenêtre visible
    (`/map/continuous/window`) : en mode DETAIL, chaque objet a son type (`BREACH` pour une brèche), son adresse et sa position
    réelle (`positionMilli`). Le bot ne fait que LIRE ces réponses, comme le ferait un joueur qui regarde la carte ; il n'envoie
    aucune requête de plus. En secours seulement, la brèche est choisie par une formule tirée des adresses (même zone A–Z).
  - Opérations › Piliers|Flotte › Exploration : champ « Cible » (la saisie « M795037 » s'affiche « M79 50 37 » et l'adresse validée
    apparaît dessous), champs « Quantité <unité> », listes « Copies » et « Durée nominale », bouton « Lancer l'exploration »
    (sinon « Aucune unité sélectionnée. », désactivé) ; fenêtre « Modèles de Piliers|Flotte » avec « Appliquer le modèle ».
  - Compteurs « Explorations quotidiennes » et « Explorations actives » communs au compte (+1 environ 3 s après le lancement).
Le bot ne touche JAMAIS à « Supprimer » (modèle), « Préparer le retour de la flotte », « Mobilisation générale »,
« Activer le champ de force » ni « Désactiver les piliers ».
"""

from __future__ import annotations

import asyncio
import math
import re
import time

from . import builder, human
from .builder import UNCONFIRMED_LIMIT, ConstructionPass
from .explorations import MAX_PER_PASS, METHODS
from .numbers import norm
from .planets import GameUiError, Interrupted

WINDOW_PATH = "/map/continuous/window"
RESPONSE_WAIT_SECONDS = 4.0
ZOOM_STEPS = 6  # dézooms au plus pour trouver une brèche autour d'une planète
PAGE_WAIT_SECONDS = 10.0
TARGET_ATTRIBUTE = "data-bot-target"
# Secours : unités de carte par pas d'adresse (X, Y), mesurées sur le vrai jeu (5,16 à 5,30 et 4,35 à 4,69 selon la région).
FORMULA_SCALE = (5.2, 4.5)
ADDRESS_RE = re.compile(r"^([A-Z])(\d)(\d) (\d{2}) (\d{2})$")
LAUNCH_NAME = re.compile(r"^Lancer l.exploration", re.I)

_PRELUDE_JS = """
const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
const ADDRESS = /^[A-Z]\\d{2} \\d{2} \\d{2}$/;
const visible = (el) => el.getClientRects().length > 0;
function targetInput() {
  // La page Opérations garde les formulaires des autres missions, cachés (vu sur le vrai jeu) : seul le champ visible compte.
  for (const label of document.querySelectorAll('main *')) {
    if (label.children.length !== 0 || text(label) !== 'Cible' || !visible(label)) continue;
    let box = label;
    while (box && !box.querySelector('input[type=text], input:not([type])')) box = box.parentElement;
    const input = box && [...box.querySelectorAll('input[type=text], input:not([type])')].find(visible);
    if (input) return input;
  }
  return null;
}
function modelsDialog() {
  return [...document.querySelectorAll('[role=dialog]')].find((d) => /^Modèles de/i.test(d.getAttribute('aria-label') || '')) || null;
}
function modelRows(dialog) {
  return [...dialog.querySelectorAll('button[title="Appliquer le modèle"]')].map((button) => {
    let row = button;
    // Une ligne n'a que deux boutons (Appliquer, Supprimer) : on s'arrête avant l'en-tête de la fenêtre (vu sur le vrai jeu
    // avec un seul modèle Flotte, qui était lu « Nouveau modèle (unités actuelles) »).
    while (row.parentElement && row.parentElement !== dialog && row.parentElement.querySelectorAll('button').length <= 2) row = row.parentElement;
    // Le nom est le texte juste avant « N Unités » (avec un seul modèle, le titre « Modèles enregistrés » est dans le même bloc).
    const leaves = [...row.querySelectorAll('*')].filter((e) => e.children.length === 0 && text(e) !== '').map(text);
    const at = leaves.findIndex((t) => /^\\d[\\d\\s\\u00a0\\u202f]*\\s*Unités?$/i.test(t));
    return { row, button, name: at > 0 ? leaves[at - 1] : '', units: at >= 0 ? Number(leaves[at].replace(/\\D/g, '')) : null };
  });
}
"""

COUNTERS_JS = (
    "() => {"
    + _PRELUDE_JS
    + """
  const out = {};
  for (const [key, label] of [['daily', 'explorations quotidiennes'], ['active', 'explorations actives']]) {
    const leaf = [...document.querySelectorAll('main *')].find((e) => e.children.length === 0 && text(e).toLowerCase().startsWith(label));
    let box = leaf;
    while (box && !/\\d+\\s*\\/\\s*\\d+/.test(text(box))) box = box.parentElement;
    const tail = box ? text(box).slice(Math.max(0, text(box).toLowerCase().indexOf(label))) : '';
    const m = /(\\d+)\\s*\\/\\s*(\\d+)/.exec(tail);
    out[key] = m ? [Number(m[1]), Number(m[2])] : null;
  }
  return out;
}"""
)

MARK_TARGET_JS = (
    "(attribute) => {"
    + _PRELUDE_JS
    + """
  document.querySelectorAll('[' + attribute + ']').forEach((e) => e.removeAttribute(attribute));
  const input = targetInput();
  if (!input) return false;
  input.setAttribute(attribute, '1');
  return true;
}"""
)

# Adresses affichées sous le champ Cible une fois la saisie validée par le jeu (le champ lui-même n'est pas compté).
TARGET_SHOWN_JS = (
    "() => {"
    + _PRELUDE_JS
    + """
  const input = targetInput();
  let box = input;
  while (box && box.parentElement && !/^Cible/.test(text(box))) box = box.parentElement;
  if (!box) return [];
  return [...box.querySelectorAll('*')].filter((e) => e.children.length === 0 && e.tagName !== 'INPUT' && ADDRESS.test(text(e))).map(text);
}"""
)

MODELS_JS = (
    "() => {"
    + _PRELUDE_JS
    + """
  const dialog = modelsDialog();
  return dialog ? modelRows(dialog).map((r) => ({ name: r.name, units: r.units })) : null;
}"""
)

MARK_MODEL_JS = (
    "(args) => {"
    + _PRELUDE_JS
    + """
  const [wanted, attribute] = args;
  const norm = (s) => s.replace(/[’‘]/g, "'").toLowerCase().replace(/\\s+/g, ' ').trim();
  document.querySelectorAll('[' + attribute + ']').forEach((e) => e.removeAttribute(attribute));
  const dialog = modelsDialog();
  if (!dialog) return false;
  const row = modelRows(dialog).find((r) => norm(r.name) === wanted);
  if (!row) return false;
  row.button.setAttribute(attribute, '1');
  return true;
}"""
)

UNITS_SUM_JS = "() => [...document.querySelectorAll('main input[aria-label^=\"Quantité \"]')].reduce((s, i) => s + (Number(i.value) || 0), 0)"

# Secours : lignes « Brèches <adresse> » de l'onglet Ensemble de la Galaxie.
BREACH_ROWS_JS = (
    "() => {"
    + _PRELUDE_JS
    + """
  const out = [];
  for (const b of document.querySelectorAll('button')) {
    const m = /^Brèches?\\s*([A-Z]\\d{2} \\d{2} \\d{2})$/.exec(text(b));
    if (m && !out.includes(m[1])) out.push(m[1]);
  }
  return out;
}"""
)

CLEAR_MARK_JS = "(attribute) => document.querySelectorAll('[' + attribute + ']').forEach((e) => e.removeAttribute(attribute))"


# ------------------------------------------------------------------------------ positions
def parse_address(address: str | None) -> tuple[str, int, int, int, int] | None:
    """« M69 38 06 » → ("M", 6, 9, 38, 6) : zone, rangée, colonne, puis position dans la case."""
    m = ADDRESS_RE.match((address or "").strip())
    return (m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5))) if m else None


def formula_position(address: str) -> tuple[str, float, float] | None:
    """Position estimée dans sa zone (secours) : X = colonne × 100 + 1er nombre, Y = rangée × 100 + 2e nombre, mis à l'échelle."""
    parts = parse_address(address)
    if parts is None:
        return None
    letter, row, col, n1, n2 = parts
    return letter, (col * 100 + n1) * FORMULA_SCALE[0], (row * 100 + n2) * FORMULA_SCALE[1]


def formula_distance(a: str, b: str) -> float | None:
    """Distance estimée entre deux adresses de la même zone ; None si les zones diffèrent (disposition des lettres inconnue)."""
    pa, pb = formula_position(a), formula_position(b)
    if pa is None or pb is None or pa[0] != pb[0]:
        return None
    return math.hypot(pa[1] - pb[1], pa[2] - pb[2])


def nearest_from_objects(objects: list, planet_address: str | None, bounds) -> dict | None:
    """Brèche la plus proche dans une fenêtre DETAIL de la carte, par les positions réelles envoyées par le serveur."""
    breaches = [o for o in objects if isinstance(o, dict) and o.get("kind") == "BREACH" and o.get("address")
                and isinstance(o.get("positionMilli"), list) and len(o["positionMilli"]) == 2]
    if not breaches:
        return None
    own = next((o for o in objects if isinstance(o, dict) and planet_address and o.get("address") == planet_address
                and isinstance(o.get("positionMilli"), list) and len(o["positionMilli"]) == 2), None)
    if own is not None:
        px, py = own["positionMilli"][0] / 1000, own["positionMilli"][1] / 1000
    elif isinstance(bounds, list) and len(bounds) == 2:  # la fenêtre est centrée sur la planète
        px, py = (bounds[0][0] + bounds[1][0]) / 2000, (bounds[0][1] + bounds[1][1]) / 2000
    else:
        return None

    def distance(o: dict) -> float:
        return math.hypot(o["positionMilli"][0] / 1000 - px, o["positionMilli"][1] / 1000 - py)

    best = min(breaches, key=distance)
    return {"address": best["address"], "x": best["positionMilli"][0] / 1000, "y": best["positionMilli"][1] / 1000,
            "distance": round(distance(best), 1), "source": "carte"}


def compact(address: str) -> str:
    """« M79 50 37 » → « M795037 » (saisie du champ Cible)."""
    return address.replace(" ", "")


# ------------------------------------------------------------------------------ passage
class ExplorationPass(ConstructionPass):
    """Relevé du jour puis lancement des explorations, planète par planète, dans les limites du compte."""

    feature_id = "explorations"

    def __init__(self, bot, store) -> None:
        super().__init__(bot)
        self.store = store
        self.scanning = False

    # ------------------------------------------------------------------ planètes
    def _targets(self, settings: dict) -> list[str]:
        if settings["planets"]:
            return list(settings["planets"])
        names = [p["name"] for p in self.bot.supervisor.scanned_planets]
        return names or self.bot.supervisor.prefs.get()["catalog"]["planets"]

    def _coords(self, planet: str) -> str | None:
        return next((p.get("coords") for p in self.bot.supervisor.scanned_planets if p["name"] == planet), None)

    # ------------------------------------------------------------------ relevé du jour
    async def scan(self, source: str) -> dict:
        """Relevé à la demande (brèches + modèles). Ne lève jamais : l'échec est dans `error`."""
        if self.scanning:
            return self.store.daily()
        busy = "une fonctionnalité est en cours, réessayez dans un instant" if self.bot.work.locked() else None
        reason = self.bot.supervisor.game_blocker() or busy
        if reason:
            self.store.set_daily_error(reason[0].upper() + reason[1:])
            return self.store.daily()
        self.scanning = True
        try:
            async with self.bot.work:  # jamais en même temps qu'un passage de fonctionnalité
                await self._daily_scan(self.store.get(), source)
        except (Interrupted, GameUiError) as exc:
            self.store.set_daily_error(f"Relevé impossible : {exc}")
            self.bot.log(f"Exploration : relevé impossible ({exc}).", "warn", event=True)
        finally:
            self.scanning = False
        return self.store.daily()

    async def _daily_scan(self, settings: dict, source: str) -> None:
        breaches: dict = {}
        problems: list[str] = []
        for planet in self._targets(settings):
            try:
                breaches[planet] = await self._find_breach(planet)
                await self._remember(planet)
            except GameUiError as exc:
                problems.append(f"{planet} : {exc}")
                self.bot.log(f"Exploration : pas de brèche relevée pour {planet} ({exc}).", "warn", event=True)
        models: dict = {}
        previous = self.store.daily()["models"]
        for method, label in METHODS.items():
            try:
                models[method] = await self._read_models(method)
            except GameUiError as exc:
                problems.append(f"modèles {label} : {exc}")
                models[method] = previous.get(method, [])
        self.store.set_daily(breaches, models, source, " ; ".join(problems))
        found = ", ".join(f"{p} → {b['address']}" for p, b in breaches.items()) or "aucune"
        self.bot.log(f"Exploration : relevé du jour ({source}) — brèches : {found} ; modèles : "
                     + ", ".join(f"{METHODS[m]} {len(v)}" for m, v in models.items()) + ".", event=True)

    async def _find_breach(self, planet: str) -> dict:
        """Brèche la plus proche de la planète : carte recentrée sur elle, puis dézooms jusqu'à voir au moins une brèche."""
        coords = self._coords(planet)
        found = await self._search_map(planet, lambda objects, bounds: nearest_from_objects(objects, coords, bounds))
        if found is not None:
            return found
        fallback = await self._formula_breach(coords)
        if fallback is not None:
            return fallback
        raise GameUiError("aucune brèche trouvée autour de la planète")

    async def _search_map(self, planet: str, pick):
        """Galaxie recentrée sur la planète, puis dézooms : renvoie le premier résultat non vide de `pick(objets, bornes)`
        sur une fenêtre détaillée de la carte (positions réelles reçues du serveur), ou None."""
        page = self.page
        if page.url.split("?")[0].rstrip("/").rsplit("/", 1)[-1] != "galaxy":
            await self._goto("galaxy")
        end = time.monotonic() + PAGE_WAIT_SECONDS
        while time.monotonic() < end and not await page.get_by_role("button", name="Dézoomer", exact=True).count():
            await asyncio.sleep(0.25)
        await self._select_planet(planet)
        await self._settle(planet)
        pending: list[asyncio.Future] = []

        def on_response(response) -> None:
            if WINDOW_PATH in response.url:
                pending.append(asyncio.ensure_future(response.json()))

        page.on("response", on_response)
        try:
            action, seen_detail = "Recentrer la carte", False
            for _ in range(ZOOM_STEPS + 2):
                before = len(pending)
                await self._click_named(action)
                projection = await self._next_window(pending, before)
                if projection is None:  # pas de nouvelle fenêtre (déjà centrée) : on élargit
                    action = "Dézoomer"
                    continue
                objects = projection.get("objects") or []
                if projection.get("mode") == "DETAIL" or objects:
                    seen_detail = True
                    found = pick(objects, projection.get("boundsMilli"))
                    if found:
                        return found
                    action = "Dézoomer"
                    continue
                if not seen_detail and action != "Zoomer":  # vue d'ensemble trop éloignée : on se rapproche une fois
                    action = "Zoomer"
                    continue
                break
        finally:
            page.remove_listener("response", on_response)
        return None

    async def _next_window(self, pending: list, before: int) -> dict | None:
        end = time.monotonic() + RESPONSE_WAIT_SECONDS
        while time.monotonic() < end:
            if len(pending) > before and all(f.done() for f in pending[before:]):
                for future in reversed(pending[before:]):
                    try:
                        data = future.result()
                    except Exception:  # noqa: BLE001 — réponse illisible : on regarde la précédente
                        continue
                    if isinstance(data, dict) and isinstance(data.get("projection"), dict):
                        return data["projection"]
                return None
            await asyncio.sleep(0.1)
        return None

    async def _formula_breach(self, coords: str | None) -> dict | None:
        """Secours : brèches listées dans l'onglet Ensemble, classées par la formule des adresses (même zone d'abord)."""
        rows = await self._eval(BREACH_ROWS_JS)
        if not rows:
            return None
        ranked = sorted(rows, key=lambda a: (formula_distance(coords or "", a) is None, formula_distance(coords or "", a) or 0.0))
        best = ranked[0]
        distance = formula_distance(coords or "", best)
        return {"address": best, "x": None, "y": None, "distance": None if distance is None else round(distance, 1), "source": "formule"}

    async def _read_models(self, method: str) -> list[dict]:
        await self._open_operations(method)
        rows = await self._open_models_dialog()
        await self._close_models_dialog()
        return [r for r in rows if r["name"]]

    # ------------------------------------------------------------------ page Opérations
    async def _open_operations(self, method: str) -> None:
        await self._goto(f"operations?method={method}&mission=EXPLORATION")
        end = time.monotonic() + PAGE_WAIT_SECONDS
        while time.monotonic() < end:
            if await self.page.evaluate(MARK_TARGET_JS, TARGET_ATTRIBUTE) and (await self._eval(COUNTERS_JS)).get("daily"):
                await self.page.evaluate(CLEAR_MARK_JS, TARGET_ATTRIBUTE)
                return
            await asyncio.sleep(0.25)
        raise GameUiError("la page Opérations › Exploration ne s'affiche pas")

    async def _counters(self) -> dict:
        raw = await self._eval(COUNTERS_JS)
        if not raw.get("daily") or not raw.get("active"):
            raise GameUiError("compteurs d'exploration illisibles")
        return raw

    async def _open_models_dialog(self) -> list[dict]:
        await self._click_named("Modèles")
        end = time.monotonic() + PAGE_WAIT_SECONDS / 2
        while time.monotonic() < end:
            rows = await self.page.evaluate(MODELS_JS)
            if rows is not None:
                return rows
            await asyncio.sleep(0.2)
        raise GameUiError("la fenêtre des modèles ne s'ouvre pas")

    async def _close_models_dialog(self) -> None:
        dialog = self.page.locator('[role=dialog][aria-label^="Modèles de"]')
        if await dialog.count():
            close = dialog.first.get_by_role("button", name="Fermer", exact=True)
            if await close.count():
                await human.click(self.page, close.first, self.settings)

    # ------------------------------------------------------------------ lancement
    async def run_pass(self) -> str:
        s = self.store.get()
        if (s["composition"] == "model" and not s["model"]) or (s["composition"] == "manual" and not s["units"]):
            return "composition non réglée (onglet Exploration), rien à lancer"
        planets = self._targets(s)
        if not planets:
            return "aucune planète connue : lancez un scan des planètes (Réglages › Compte et univers)"
        launched: list[str] = []
        skipped = 0
        stop = ""
        try:
            if self.store.daily_due():
                await self._daily_scan(s, "quotidien")
            breaches = self.store.daily()["breaches"]
            await self._open_operations(s["method"])
            limit = MAX_PER_PASS if s["rhythm"] == "max" else s["per_pass"]
            for planet in planets:
                breach = breaches.get(planet)
                if not breach:
                    skipped += 1
                    self.bot.log(f"Exploration : aucune brèche connue pour {planet}, planète sautée.", "warn", event=True)
                    continue
                for _ in range(limit):
                    stop = self._quota_reached(await self._counters(), s["copies"])
                    if stop:
                        break
                    outcome = await self._launch(planet, breach, s)
                    if outcome is None:
                        skipped += 1
                        break
                    launched.append(outcome)
                await self._remember(planet)
                if stop:
                    break
        except Interrupted as exc:
            return self._summary(launched, skipped, f"interrompu ({exc})")
        except GameUiError as exc:
            self.bot.log(f"Exploration : {exc}.", "warn", event=True)
            return self._summary(launched, skipped, f"arrêté ({exc})")
        return self._summary(launched, skipped, stop)

    @staticmethod
    def _quota_reached(counters: dict, copies: int) -> str:
        (daily, daily_max), (active, active_max) = counters["daily"], counters["active"]
        if daily_max - daily < copies:
            return f"quota quotidien atteint ({daily}/{daily_max})"
        if active_max - active < copies:
            return f"plus de place pour une exploration active ({active}/{active_max})"
        return ""

    @staticmethod
    def _summary(launched: list[str], skipped: int, stop: str) -> str:
        parts = [f"{len(launched)} lancement(s)" + (" : " + ", ".join(launched) if launched else "")]
        parts.append(f"{skipped} planète(s) sautée(s)")
        if stop:
            parts.append(stop)
        return " ; ".join(parts)

    def _skip(self, planet: str, reason: str) -> None:
        self.bot.log(f"Exploration : {planet} sautée ({reason}).", event=True)

    async def _launch(self, planet: str, breach: dict, s: dict) -> str | None:
        """Prépare et lance une vague d'exploration depuis la planète. None : planète sautée (il manque quelque chose)."""
        page = self.page
        await self._open_operations(s["method"])
        await self._select_planet(planet)
        await self._settle(planet)
        if not await self._fill_target(breach["address"]):
            self._skip(planet, f"cible {breach['address']} refusée par le jeu")
            return None
        if s["composition"] == "model":
            if not await self._apply_model(s["model"]):
                self._skip(planet, f"modèle « {s['model']} » introuvable")
                return None
        else:
            for name, qty in s["units"].items():
                field = page.get_by_label(f"Quantité {name}", exact=True)
                if not await field.count():
                    self._skip(planet, f"unité « {name} » absente")
                    return None
                await self._guard()
                await human.click(page, field.first, self.settings)
                await field.first.fill(str(qty))
        if not await page.evaluate(UNITS_SUM_JS):
            self._skip(planet, "aucune unité sélectionnée")
            return None
        if not await self._choose("Copies", s["copies"]):
            self._skip(planet, f"{s['copies']} copie(s) impossible(s) (places actives)")
            return None
        if not await self._choose("Durée nominale", s["duration"]):
            self._skip(planet, f"durée de {s['duration']} h indisponible")
            return None
        go = page.get_by_role("button", name=LAUNCH_NAME)
        if not await go.count() or not await go.first.is_enabled():
            self._skip(planet, "pas assez d'unités, d'énergie ou de Trame")
            return None
        before = await self._counters()
        await self._guard()
        await human.think(self.settings)
        await human.click(page, go.first, self.settings)
        sentence = f"{s['copies']} exploration(s) {METHODS[s['method']]} depuis {planet} vers {breach['address']}"
        await self._confirm(before, sentence)
        return sentence

    async def _fill_target(self, address: str) -> bool:
        page = self.page
        if not await page.evaluate(MARK_TARGET_JS, TARGET_ATTRIBUTE):
            raise GameUiError("champ Cible introuvable")
        try:
            target = page.locator(f"[{TARGET_ATTRIBUTE}]").first
            await self._guard()
            await human.click(page, target, self.settings)
            await target.fill(compact(address))
        finally:
            await page.evaluate(CLEAR_MARK_JS, TARGET_ATTRIBUTE)
        end = time.monotonic() + PAGE_WAIT_SECONDS / 2
        while time.monotonic() < end:
            if address in await page.evaluate(TARGET_SHOWN_JS):
                return True
            await asyncio.sleep(0.25)
        return False

    async def _apply_model(self, name: str) -> bool:
        page = self.page
        await self._open_models_dialog()
        if not await page.evaluate(MARK_MODEL_JS, [norm(name), TARGET_ATTRIBUTE]):
            await self._close_models_dialog()
            return False
        try:
            await self._guard()
            await human.click(page, page.locator(f"[{TARGET_ATTRIBUTE}]").first, self.settings)
        finally:
            await page.evaluate(CLEAR_MARK_JS, TARGET_ATTRIBUTE)
        await asyncio.sleep(0.3)
        await self._close_models_dialog()  # le jeu la ferme d'habitude lui-même
        return True

    async def _choose(self, label: str, value: int) -> bool:
        select = self.page.get_by_label(label, exact=True)
        if not await select.count():
            raise GameUiError(f"liste « {label} » introuvable")
        options = await select.first.evaluate("(s) => [...s.options].map((o) => o.value)")
        if str(value) not in options:
            return False
        await self._guard()
        await select.first.select_option(str(value))
        return True

    async def _confirm(self, before: dict, sentence: str) -> None:
        """Le compteur quotidien doit monter après le clic ; sinon le lancement est « non confirmé »."""
        end = time.monotonic() + builder.CONFIRM_SECONDS
        while time.monotonic() < end:
            try:
                now = await self._counters()
            except GameUiError:
                now = None
            if now is not None and now["daily"][0] > before["daily"][0]:
                self._unconfirmed = 0
                self.bot.log(f"Exploration lancée : {sentence}.", event=True, kind="launch")
                return
            await asyncio.sleep(0.3)
        self._unconfirmed += 1
        self.bot.log(f"Clic sur « Lancer l'exploration » fait ({sentence}) mais le compteur n'a pas bougé.", "warn", event=True, kind="attention")
        if self._unconfirmed >= UNCONFIRMED_LIMIT:
            self.bot.scheduler.update(self.feature_id, {"enabled": False})
            self.bot.log("Exploration désactivée : deux lancements de suite non confirmés. Vérifiez à l'écran avant de la réactiver.", "error", event=True, kind="attention")
            raise Interrupted("désactivée après deux lancements non confirmés")
