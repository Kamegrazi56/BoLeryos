"""Places de mission : « Missions actives X / Y » est commun à tout le compte (vrai jeu). Exploration (une place par copie),
Fouille, Piratage et Forage occupent chacun une place jusqu'au retour ; seul le Transport par les Piliers est instantané.

Réglages du joueur : un nombre de places à toujours garder libres, et l'ordre de priorité des fonctionnalités qui envoient
des missions. Avant chaque lancement, une fonctionnalité ne peut prendre que :
    Y − X − places gardées libres − ce qui manque encore aux fonctionnalités de priorité supérieure (activées).
Ce qui manque à une fonctionnalité :
  - Fouilles : Fouilles réglées − Fouilles en cours (une Fouille finie qui attend son délai compte comme manquante) ;
  - Forages : places réglées − forages en cours ;
  - Piratage : 1 si Naël n'est pas en mission ;
  - Explorations : les copies de son prochain passage, 0 si le quota du jour est épuisé.
Fichier `<data_dir>/slots.json` : {"reserve", "order", "seen": dernier compteur lu {"active", "max", "at"}}.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import time
from pathlib import Path

FILE_NAME = "slots.json"
MISSION_FEATURES = ("excavations", "explorations", "minings", "hackings")  # ordre par défaut
LABELS = {"excavations": "Fouilles", "explorations": "Explorations", "minings": "Forages", "hackings": "Piratage"}
MAX_RESERVE = 50

# « Missions actives : 12 / 29 » (en-tête d'Opérations ou formulaire de mission) : [actives, maximum] ou null.
MISSIONS_COUNT_JS = """() => {
  const text = (el) => (el.textContent || '').replace(/\\s+/g, ' ').trim();
  for (const leaf of document.querySelectorAll('body *')) {
    if (leaf.children.length || !leaf.getClientRects().length || !/^missions actives/i.test(text(leaf))) continue;
    let box = leaf;
    while (box && !/\\d+\\s*\\/\\s*\\d+/.test(text(box))) box = box.parentElement;
    if (!box) continue;
    const all = text(box);
    const m = /(\\d+)\\s*\\/\\s*(\\d+)/.exec(all.slice(all.toLowerCase().indexOf('missions actives')));
    if (m) return [Number(m[1]), Number(m[2])];
  }
  return null;
}"""


class SlotError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(" ; ".join(errors))
        self.errors = errors


def validate(data) -> dict:
    if not isinstance(data, dict):
        raise SlotError(["Réglages illisibles."])
    errors: list[str] = []
    reserve = data.get("reserve", 0)
    if isinstance(reserve, bool) or not isinstance(reserve, int) or not 0 <= reserve <= MAX_RESERVE:
        errors.append(f"Places à garder libres : un nombre entier de 0 à {MAX_RESERVE}.")
    order = data.get("order", list(MISSION_FEATURES))
    if not isinstance(order, list) or sorted(order) != sorted(MISSION_FEATURES):
        errors.append("Priorités : chaque fonctionnalité une fois (Fouilles, Explorations, Forages, Piratage).")
    if errors:
        raise SlotError(errors)
    return {"reserve": reserve, "order": list(order)}


class SlotSettings:
    def __init__(self, data_dir: str | None) -> None:
        folder = Path(data_dir) if data_dir and os.path.isdir(data_dir) else None
        self._path = folder / FILE_NAME if folder else None
        self._settings = {"reserve": 0, "order": list(MISSION_FEATURES)}
        self._seen: dict = {"active": None, "max": None, "at": None}
        if self._path is not None and self._path.exists():
            try:
                saved = json.loads(self._path.read_text(encoding="utf8"))
                self._settings = validate({k: saved[k] for k in ("reserve", "order") if k in saved})
                if isinstance(saved.get("seen"), dict):
                    self._seen = {**self._seen, **saved["seen"]}
            except (OSError, ValueError, SlotError, TypeError, KeyError):
                pass  # fichier illisible : valeurs par défaut

    @property
    def persistent(self) -> bool:
        return self._path is not None

    def get(self) -> dict:
        return copy.deepcopy(self._settings)

    def seen(self) -> dict:
        return dict(self._seen)

    def replace(self, data) -> dict:
        self._settings = validate(data)
        self._save()
        return self.get()

    def set_seen(self, active: int, maximum: int) -> None:
        self._seen = {"active": active, "max": maximum, "at": time.time()}
        self._save()

    def _save(self) -> None:
        if not self._path:
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".slots-")
            with os.fdopen(fd, "w", encoding="utf8") as f:
                json.dump({**self._settings, "seen": self._seen}, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass


# ------------------------------------------------------------------ besoins des fonctionnalités
def need(bot, feature_id: str, now: float | None = None) -> int:
    """Places qui manquent encore à une fonctionnalité (0 si elle est désactivée ou n'a rien à lancer)."""
    now = time.time() if now is None else now
    if not bot.scheduler.store.get(feature_id)["enabled"]:
        return 0
    if feature_id == "excavations":
        from .excavations import slots as excavation_slots
        s = bot.excavations.get()
        wanted = [w for w in excavation_slots(s, bot.explorer._all_planets()) if w["units"]]
        running = sum(1 for t in bot.excavations.tracked() if t.get("end") and t["end"] > now)
        return max(0, len(wanted) - running)
    if feature_id == "minings":
        s = bot.minings.get()
        if not s["planets"] or not s["resources"]:
            return 0
        running = sum(1 for t in bot.minings.tracked() if t.get("phase") != "terminé")
        return max(0, s["slots"] - running)
    if feature_id == "hackings":
        if not bot.hackings.get()["planet"]:
            return 0
        end = bot.hackings.state()["end"]
        return 0 if end is not None and end > now else 1
    if feature_id == "explorations":
        from .explorations import plans
        s = bot.explorations.get()
        todo = [p for p in plans(s, bot.explorer._all_planets()) if (p["model"] if p["composition"] == "model" else p["units"])]
        total = sum(p["copies"] for p in todo)
        counters = getattr(bot.explorer, "last_counters", None) or {}
        daily, active = counters.get("daily"), counters.get("active")
        if daily:
            total = min(total, max(0, daily[1] - daily[0]))  # quota du jour épuisé : plus rien à réserver
        if active:
            total = min(total, max(0, active[1] - active[0]))
        return total
    return 0


def allowance(bot, feature_id: str, active: int, maximum: int) -> tuple[int, str]:
    """(places que la fonctionnalité peut prendre maintenant, explication si c'est 0 ou peu)."""
    settings = bot.slots.get()
    order = settings["order"]
    higher = order[: order.index(feature_id)] if feature_id in order else []
    reserved = {f: need(bot, f) for f in higher}
    room = maximum - active - settings["reserve"] - sum(reserved.values())
    parts = [f"missions actives {active}/{maximum}"]
    if settings["reserve"]:
        parts.append(f"{settings['reserve']} gardée(s) libre(s)")
    held = [f"{LABELS[f]} {n}" for f, n in reserved.items() if n]
    if held:
        parts.append("réservées aux priorités au-dessus : " + ", ".join(held))
    return max(0, room), ", ".join(parts)
