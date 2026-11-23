"""Retours de flottes : lecture des Capteurs interstellaires et rappel d'une flotte à l'heure voulue.

Ce que l'on sait du vrai jeu (exports du 20-21/11, panneau fermé donc sans contenu visible) : le bouton « Capteurs » du
lanceur en haut ouvre le panneau `[data-testid=sensors-overlay-panel]` (onglets Tous / Attaques / Espionnage / Logistique) ;
chaque mouvement est une carte `.rounded-2xl` avec `fleet-radar-left-endpoint` (départ), `fleet-radar-route-direction`,
`fleet-radar-right-endpoint` (destination), un bouton « Localiser dans la galaxie » et un bouton « Préparer le retour de la
flotte » (aria-label).
Carte lue sur le vrai jeu (23/11, enregistrement de structure) : nom de mission (« Exploration »), référence « #102-03165 »,
unités « 17000 U », « Arrivée: 08:27:06 », compte à rebours « 00:22:11 », puis Origine / Destination (libellé, nom du lieu,
bouton d'adresse, nom du joueur). Le panneau affiche « Silence spatial » avant l'arrivée des cartes.
Fenêtre « Préparer le retour de la flotte » lue sur le vrai jeu (23/11) : « Confirmer le retour. Si vous confirmez maintenant, la
flotte reviendra le 23/11/2026 09:41:16. » ; boutons « Annuler » et « Confirmer le retour ».
Vrai jeu (23/11) : le clic de confirmation est pris en compte mais la fenêtre reste ouverte (on juge donc sur l'état de la flotte). NON VÉRIFIÉ : si l'heure de la fenêtre avance en direct. Le bot ne clique jamais sur une
confirmation qu'il ne reconnaît pas avec certitude.
"""

from __future__ import annotations

import asyncio
import random
import re
import time

from playwright.async_api import Error as PlaywrightError

from . import human
from .builder import ConstructionPass
from .fleets import (ABANDON_SECONDS, EXCLUDED, build_fleet, decide, match_fleet, parse_clock, when)
from .planets import GameUiError, Interrupted

PANEL = "[data-testid=sensors-overlay-panel]"
PAGE_WAIT_SECONDS = 10.0
DIALOG_WAIT_SECONDS = 8.0
CARDS_SETTLE_SECONDS = 3.0  # le panneau s'affiche avant ses cartes : on attend que leur nombre ne bouge plus
CARDS_SETTLE_MAX_SECONDS = 10.0
CONFIRM_WAIT_SECONDS = 10.0
# Délai entre la lecture de l'heure de retour et le moment où le jeu reçoit le clic (relecture toutes les 0,5 s, clic, réseau) :
# l'arrivée avance de 2 s par seconde, d'où cette avance. Mesuré sur le faux jeu (≈ 1,5 s de délai) ; le vrai jeu s'affiche dans la note.
CLICK_LEAD_SECONDS = 3.0
POLL_SECONDS = 0.5
MARK = "data-bot-fleet"
LAUNCHER_NAME = re.compile(r"^Capteurs", re.I)
CANCEL_NAME = re.compile(r"^(annuler|fermer|non|×|x)$", re.I)
CONFIRM_HINT = re.compile(r"retour|rappel|confirm|valider|ordonner", re.I)

# Cartes du panneau Capteurs : null si le panneau n'est pas affiché.
CARDS_JS = """() => {
  const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
  const panel = document.querySelector('[data-testid="sensors-overlay-panel"]');
  if (!panel || !panel.getClientRects().length) return null;
  const leaves = (root) => [...root.querySelectorAll('*')].filter((e) => !e.children.length && text(e)).map(text);
  const ADDRESS = /^[A-Z]\\d{2} \\d{2} \\d{2}$/;
  const LABEL = /^(Origine|Destination)$/;  // libellés de colonne vus sur le vrai jeu (22/11), pas des noms de lieu
  const side = (box, id) => {
    const e = box.querySelector('[data-testid="' + id + '"]');
    const parts = e ? leaves(e) : [];
    const next = e && e.nextElementSibling ? text(e.nextElementSibling) : '';  // le bouton d'adresse suit son bloc (export du 21/11)
    const all = [...parts, next];
    return { name: parts.find((t) => !ADDRESS.test(t) && !LABEL.test(t)) || '', coords: all.find((t) => ADDRESS.test(t)) || '' };
  };
  const out = [];
  panel.querySelectorAll('[data-testid="fleet-radar-left-endpoint"]').forEach((left, index) => {
    const box = left.closest('.rounded-2xl');
    if (!box || !box.getClientRects().length) return;
    const own = leaves(box);
    const clock = own.map((t) => /^(?:(\\d+)\\s*j\\s*)?(\\d+):(\\d{2}):(\\d{2})$/.exec(t)).find(Boolean);
    const recall = box.querySelector('button[aria-label="Préparer le retour de la flotte"]');
    out.push({
      index, kind: own[0] || '', text: text(box), can_recall: !!recall && !recall.disabled,
      units: own.find((t) => /^[\\d\\s.,]+\\s*U$/.test(t)) || '',
      left: clock ? (Number(clock[1] || 0) * 86400 + Number(clock[2]) * 3600 + Number(clock[3]) * 60 + Number(clock[4])) : null,
      origin: side(box, 'fleet-radar-left-endpoint'), destination: side(box, 'fleet-radar-right-endpoint'),
    });
  });
  return out;
}"""

# Marque le bouton de retour de la n-ième carte du panneau ; rend vrai si trouvé.
MARK_RECALL_JS = """([index, attr]) => {
  document.querySelectorAll('[' + attr + ']').forEach((e) => e.removeAttribute(attr));
  const panel = document.querySelector('[data-testid="sensors-overlay-panel"]');
  const left = panel && [...panel.querySelectorAll('[data-testid="fleet-radar-left-endpoint"]')][index];
  const box = left && left.closest('.rounded-2xl');
  const button = box && box.querySelector('button[aria-label="Préparer le retour de la flotte"]');
  if (!button || button.disabled) return false;
  button.setAttribute(attr, '1');
  return true;
}"""

# Fenêtre ouverte par « Préparer le retour » (dernière fenêtre visible hors panneau) : textes, durées et boutons.
DIALOG_JS = """() => {
  const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
  // Le panneau Capteurs lui-même ne compte pas : il reste ouvert après la confirmation et ferait croire que la fenêtre n'est pas fermée.
  const d = [...document.querySelectorAll('[role=dialog]')].filter((e) => e.getClientRects().length && !e.matches('[data-testid="sensors-overlay-panel"]')).pop();
  if (!d) return null;
  const leaves = [...d.querySelectorAll('*')].filter((e) => !e.children.length && text(e));
  // Vrai jeu (23/11) : « Si vous confirmez maintenant, la flotte reviendra le 23/11/2026 09:41:16. » (heure du navigateur : on
  // la convertit ici, dans la page, pour ne pas supposer de fuseau).
  const m = /reviendra le (\\d{2})\\/(\\d{2})\\/(\\d{4}) (\\d{2}):(\\d{2}):(\\d{2})/.exec(text(d));
  const returnsAt = m ? new Date(+m[3], +m[2] - 1, +m[1], +m[4], +m[5], +m[6]).getTime() / 1000 : null;
  return { text: text(d).slice(0, 400), returns_at: returnsAt, durations: leaves.map((e) => ({ text: text(e), label: text(e.parentElement || e) })),
           buttons: [...d.querySelectorAll('button')].filter((b) => b.getClientRects().length && !b.disabled).map(text) };
}"""

CLEAR_JS = "(attribute) => document.querySelectorAll('[' + attribute + ']').forEach((e) => e.removeAttribute(attribute))"


def return_duration(dialog: dict | None, now: float | None = None) -> float | None:
    """Durée de retour (secondes) annoncée par la fenêtre.

    Vrai jeu : la fenêtre donne l'HEURE d'arrivée (« reviendra le JJ/MM/AAAA HH:MM:SS »), la durée est l'écart avec maintenant.
    À défaut, on cherche une durée affichée : la seule, ou celle dont le libellé parle du retour."""
    returns_at = (dialog or {}).get("returns_at")
    if isinstance(returns_at, (int, float)):
        # Le jeu tronque l'heure à la seconde : l'arrivée réelle est en moyenne une demi-seconde plus tard que l'heure affichée.
        return max(0.0, returns_at + 0.5 - (time.time() if now is None else now))
    found = [(parse_clock(d["text"]), d["label"]) for d in (dialog or {}).get("durations", [])]
    found = [(s, label) for s, label in found if s is not None]
    if len(found) == 1:
        return found[0][0]
    tagged = [s for s, label in found if re.search(r"retour|trajet|dur[ée]e", label, re.I)]
    return tagged[0] if len(tagged) == 1 else None


def confirm_name(dialog: dict | None) -> str | None:
    """Bouton de confirmation : celui dont le texte parle de retour, sinon l'unique bouton qui n'annule pas. Jamais un choix au hasard."""
    buttons = [b for b in (dialog or {}).get("buttons", []) if not CANCEL_NAME.match(b)]
    hinted = [b for b in buttons if CONFIRM_HINT.search(b)]
    if len(hinted) == 1:
        return hinted[0]
    return buttons[0] if len(buttons) == 1 else None


class FleetPass(ConstructionPass):
    """Relève les flottes en vol (à la demande) et fait les retours programmés au bon moment."""

    feature_id = "fleets"

    def __init__(self, bot, store) -> None:
        super().__init__(bot)
        self.store = store
        self.scanning = False
        self._armed: tuple[float, float] | None = None  # point du bouton de confirmation où la souris attend (voir `_arm`)

    def next_due(self) -> float | None:
        return self.store.next_check()

    # ------------------------------------------------------------------ panneau Capteurs
    async def _open_panel(self, settle: bool = True) -> list[dict]:
        page = self.page
        cards = await page.evaluate(CARDS_JS)
        if cards is not None:
            return cards
        launcher = page.get_by_role("button", name=LAUNCHER_NAME)
        if not await launcher.count():
            await self._goto("empire")
            end = time.monotonic() + PAGE_WAIT_SECONDS
            while not await launcher.count() and time.monotonic() < end:
                await asyncio.sleep(0.25)
        if not await launcher.count():
            raise GameUiError("bouton « Capteurs » introuvable")
        await self._guard()
        await human.click(page, launcher.first, self.settings)
        end = time.monotonic() + PAGE_WAIT_SECONDS
        while time.monotonic() < end:
            cards = await page.evaluate(CARDS_JS)
            if cards is not None:
                if settle:
                    return await self._settled_cards()
                await asyncio.sleep(0.5)  # lecture rapide (retour programmé : chaque seconde compte)
                return await page.evaluate(CARDS_JS) or []
            await asyncio.sleep(0.25)
        raise GameUiError("le panneau des Capteurs ne s'affiche pas")

    async def _settled_cards(self) -> list[dict]:
        """Cartes du panneau ouvert, lues une fois leur nombre stable.

        Constaté sur le vrai jeu (23/11) : le panneau affiche « Silence spatial » avant que les cartes arrivent ; une lecture
        trop tôt rend 0 flotte alors que des mouvements sont en vol. On relit toutes les 0,5 s et on garde la lecture dès
        que le nombre n'a plus changé pendant CARDS_SETTLE_SECONDS."""
        start = stable_since = time.monotonic()
        cards = await self.page.evaluate(CARDS_JS) or []
        while time.monotonic() - start < CARDS_SETTLE_MAX_SECONDS:
            await asyncio.sleep(0.5)
            latest = await self.page.evaluate(CARDS_JS) or []
            if len(latest) != len(cards):
                stable_since = time.monotonic()
            cards = latest
            if time.monotonic() - stable_since >= CARDS_SETTLE_SECONDS:
                break
        return cards

    async def _close_panel(self) -> None:
        launcher = self.page.get_by_role("button", name=LAUNCHER_NAME)
        if await self.page.evaluate(CARDS_JS) is not None and await launcher.count():
            await human.click(self.page, launcher.first, self.settings)

    async def scan(self, source: str = "manuel") -> dict:
        """Relève les flottes en vol. Lecture seule. Ne lève jamais : renvoie {"error": raison} en cas d'échec."""
        if self.scanning:
            return {"error": "relevé déjà en cours"}
        busy = "une fonctionnalité est en cours, réessayez dans un instant" if self.bot.work.locked() else None
        reason = self.bot.supervisor.game_blocker() or busy
        if reason:
            return {"error": reason[0].upper() + reason[1:]}
        self.scanning = True
        try:
            async with self.bot.work:
                await self.bot.claim_bonus()
                cards = await self._open_panel()
                if self.bot.status()["explore"]["recording"]:
                    await self.bot.snapshot_now()  # panneau ouvert : garde sa vraie structure pour l'exploration
                now = time.time()
                fleets = [build_fleet(c, now) for c in cards]
                self.store.set_snapshot(fleets, now)
                await self._close_panel()
                usable = sum(1 for f in fleets if f["can_recall"] and not f["excluded"])
                self.bot.log(f"Flottes ({source}) : {len(fleets)} mouvement(s) lu(s), {usable} rappelable(s) par le bot.", event=True)
                return {"error": "", "fleets": len(fleets)}
        except (Interrupted, GameUiError) as exc:
            self.bot.log(f"Flottes : relevé impossible ({exc}).", "warn", event=True)
            return {"error": f"Relevé impossible : {exc}"}
        finally:
            self.scanning = False

    # ------------------------------------------------------------------ passage
    async def run_pass(self) -> str:
        now = time.time()
        due = [r for r in self.store.pending() if (r.get("next_check") or 0) <= now]
        if not due:
            nxt = self.store.next_check()
            return "aucun retour de flotte à faire maintenant" + (f" (prochain contrôle le {when(nxt)})" if nxt else "")
        summary: list[str] = []
        try:
            for recall in due:
                summary.append(await self._handle(recall))
            await self._close_panel()
        except Interrupted as exc:
            return " ; ".join([*summary, f"interrompu ({exc})"])
        except GameUiError as exc:
            self.bot.log(f"Flottes : {exc}.", "warn", event=True)
            return " ; ".join([*summary, f"arrêté ({exc})"])
        return " ; ".join(summary)

    def _finish(self, recall: dict, state: str, note: str) -> str:
        self.store.update(recall["id"], state=state, note=note)
        level = "info" if state == "done" else "warn"
        self.bot.log(f"Flottes : retour de {recall['fleet']['label']} — {note}.", level, event=True, kind="launch" if state == "done" else "attention")
        return f"{recall['fleet']['label']} : {note}"

    async def _handle(self, recall: dict) -> str:
        """Un contrôle d'un retour programmé : relit la durée de retour du jeu, puis clique, attend ou abandonne."""
        if time.time() > recall["at"] + ABANDON_SECONDS:
            return self._finish(recall, "abandoned", f"abandonné : l'heure voulue ({when(recall['at'])}) est passée sans rappel")
        cards = await self._open_panel(settle=False)  # lecture rapide d'abord : le délai de l'attente complète fausserait l'heure
        fleet = match_fleet([build_fleet(c, time.time()) for c in cards], recall["fleet"]["key"], recall["fleet"].get("arrival_at"))
        if fleet is None:  # cartes peut-être pas encore toutes arrivées : on attend qu'elles se stabilisent, puis on cherche à nouveau
            cards = await self._settled_cards()
            fleet = match_fleet([build_fleet(c, time.time()) for c in cards], recall["fleet"]["key"], recall["fleet"].get("arrival_at"))
        if fleet is None:
            return self._finish(recall, "failed", "flotte introuvable dans les Capteurs (déjà rentrée ou arrivée ?)")
        if EXCLUDED.search(fleet["text"]) or not fleet["can_recall"]:
            return self._finish(recall, "failed", "cette flotte ne peut plus être rappelée")
        dialog = await self._open_return_dialog(fleet["index"])
        try:
            return await self._decide(recall, dialog)
        finally:
            await self._cancel_dialog()

    async def _open_return_dialog(self, index: int) -> dict:
        page = self.page
        if not await page.evaluate(MARK_RECALL_JS, [index, MARK]):
            raise GameUiError("bouton « Préparer le retour de la flotte » introuvable")
        await self._guard()
        try:
            await human.click(page, page.locator(f"[{MARK}]").first, self.settings)
        finally:
            await page.evaluate(CLEAR_JS, MARK)
        end = time.monotonic() + DIALOG_WAIT_SECONDS
        while time.monotonic() < end:
            dialog = await page.evaluate(DIALOG_JS)
            if dialog:
                return dialog
            await asyncio.sleep(0.2)
        raise GameUiError("la fenêtre de retour de la flotte ne s'affiche pas")

    async def _cancel_dialog(self) -> None:
        page = self.page
        self._armed = None
        dialog = await page.evaluate(DIALOG_JS)
        if dialog is None:
            return
        for name in ("Annuler", "Fermer"):
            button = page.locator("[role=dialog]").last.get_by_role("button", name=name, exact=True)
            if await button.count():
                await human.click(page, button.first, self.settings)
                return

    async def _decide(self, recall: dict, dialog: dict) -> str:
        reads: list[list[float]] = list(recall.get("reads", []))
        margin = self.store.margin()
        armed_once = False
        deadline: float | None = None  # attente surveillée : l'écart se réduit d'au moins 1 s par seconde, sinon la durée affichée est figée
        while True:
            now = time.time()
            seconds = return_duration(dialog, now)
            if seconds is None:
                self.bot.log(f"Flottes : durée de retour illisible dans la fenêtre du jeu (« {dialog['text'][:200]} »).", "warn", event=True)
                return self._finish(recall, "failed", "durée de retour illisible dans la fenêtre du jeu")
            reads.append([now, now + seconds])
            action, value = decide(now, seconds, recall["at"], margin + CLICK_LEAD_SECONDS, reads)
            if action == "late":
                return self._finish(recall, "failed", f"trop tard : un rappel immédiat arriverait le {when(now + seconds)}, {int(value)} s après l'heure voulue")
            if action == "click":
                return await self._confirm(recall, dialog, now + seconds, now)
            if action == "wait":
                self.store.update(recall["id"], next_check=value, reads=reads)
                return f"{recall['fleet']['label']} : contrôle suivant le {when(value)}"
            if not armed_once:
                armed_once = True
                await self._arm(dialog)
            deadline = deadline or now + value + 30
            if now > deadline:
                return self._finish(recall, "failed", "la durée de retour affichée par le jeu n'évolue pas : impossible de viser l'heure")
            await asyncio.sleep(POLL_SECONDS)  # « hold » : on reste dans la fenêtre et on relit la durée chaque seconde
            await self._guard()
            dialog = await self.page.evaluate(DIALOG_JS)
            if dialog is None:
                raise GameUiError("la fenêtre de retour s'est fermée pendant l'attente")

    async def _arm(self, dialog: dict) -> None:
        """Pendant l'attente, amène la souris sur le bouton de confirmation : le clic final part alors sans trajet de souris.
        Le trajet « humain » prend 1 à 2 s, soit 2 à 4 s d'écart sur l'arrivée (elle avance de 2 s par seconde)."""
        name = confirm_name(dialog)
        if name is None:
            return
        button = self.page.locator("[role=dialog]").last.get_by_role("button", name=name, exact=True)
        box = await button.first.bounding_box() if await button.count() else None
        if box is None:
            return
        x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
        y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
        await self.page.mouse.move(x, y, steps=random.randint(10, 25))
        self._armed = (x, y)

    async def _armed_click(self, button) -> bool:
        """Clic immédiat au point où la souris attend, s'il tombe toujours sur le bouton. Faux : le clic normal prendra le relais."""
        point, self._armed = self._armed, None
        if point is None:
            return False
        try:
            if not await button.first.evaluate("(el, [x, y]) => el.contains(document.elementFromPoint(x, y))", list(point)):
                return False
            await self.page.mouse.click(point[0], point[1], delay=random.randint(40, 120))
        except PlaywrightError:
            return False
        return True

    async def _click_retry(self, button) -> None:
        """Clic de confirmation ; la page du jeu se redessine parfois sous le curseur : un second essai, puis l'erreur."""
        for attempt in (1, 2):
            try:
                await human.click(self.page, button.first, self.settings)
                return
            except PlaywrightError as exc:
                if attempt == 2:
                    raise GameUiError(f"clic de confirmation impossible ({type(exc).__name__})") from exc
                await asyncio.sleep(0.2)

    async def _still_recallable(self, recall: dict) -> bool:
        """Après la confirmation : la même flotte (même trajet) est-elle encore rappelable ? Alors le rappel n'a rien fait.
        Constaté sur le vrai jeu (23/11) : le jeu prend le rappel (carte « Retour ») même quand la fenêtre reste affichée ; seul
        l'état réel de la flotte fait foi, pas la fermeture de la fenêtre. On rouvre le panneau s'il a disparu."""
        await asyncio.sleep(0.5)
        cards = await self._open_panel(settle=False)
        fleets = [build_fleet(c, time.time()) for c in cards]
        still = match_fleet(fleets, recall["fleet"]["key"], recall["fleet"].get("arrival_at"))
        return bool(still and still["can_recall"])

    def _done_note(self, recall: dict, clicked: float, read_at: float, arrival: float) -> str:
        return (f"rappel confirmé le {when(clicked)} ({clicked - read_at:.1f} s après la lecture de l'heure), "
                f"arrivée prévue vers {when(arrival)} (voulue : {when(recall['at'])})")

    async def _confirm(self, recall: dict, dialog: dict, arrival: float, read_at: float) -> str:
        name = confirm_name(dialog)
        if name is None:
            return self._finish(recall, "failed", f"bouton de confirmation du retour non reconnu (boutons : {', '.join(dialog.get('buttons', [])) or 'aucun'})")
        page = self.page
        button = page.locator("[role=dialog]").last.get_by_role("button", name=name, exact=True)
        await self._guard()
        clicked = time.time()
        if not await self._armed_click(button):
            await self._click_retry(button)
        end = time.monotonic() + CONFIRM_WAIT_SECONDS
        while time.monotonic() < end:
            if await page.evaluate(DIALOG_JS) is None:
                break
            await asyncio.sleep(0.3)
        # Fenêtre fermée ou non : on juge sur l'état réel de la flotte (une fenêtre restée ouverte ne prouve pas l'échec).
        if await self._still_recallable(recall):
            return self._finish(recall, "failed", "le jeu n'a pas pris en compte le retour (la flotte est toujours en vol vers sa destination)")
        return self._finish(recall, "done", self._done_note(recall, clicked, read_at, arrival))
