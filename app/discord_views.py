"""Textes que le bot envoie sur Discord pour « voir » une instance : prochains passages, flottes, planètes, fonctionnalités.

Lecture seule : ces fonctions ne touchent ni au jeu ni aux réglages, elles mettent en phrases ce que le bot sait déjà.
"""

from __future__ import annotations

import time

from . import planning
from .fleets import when

MAX_LINES = 20  # au-delà, on résume (Discord limite un message à 2000 caractères)
RESULT_CHARS = 80


def span(seconds: float) -> str:
    """Une durée lisible : « 45 s », « 12 min », « 1 h 05 », « 2 j 3 h »."""
    s = int(max(0, seconds))
    if s < 60:
        return f"{s} s"
    minutes = s // 60
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d}" if hours < 48 else f"{hours // 24} j {hours % 24} h"


def amount(value) -> str:
    """Une quantité avec des espaces entre les milliers (« ? » si le jeu ne l'a pas donnée)."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "?"
    return f"{int(value):,}".replace(",", " ")


def activity(act: dict) -> str:
    """Ce que fait le bot, en une phrase (même lecture que le bandeau de l'interface)."""
    state = act.get("state")
    if state == "running":
        return f"en cours : {act['feature']['label']}"
    if state == "stopping":
        return f"termine : {act['feature']['label']}"
    if state == "blocked":
        return f"en attente : {act['reason']}"
    if state == "idle":
        nxt = act.get("next")
        return f"au repos, prochain : {nxt['label']}" if nxt else "au repos, rien de prévu"
    return ""


def _planning_line(bot) -> str:
    settings = bot.planning.get()
    if not settings["enabled"]:
        return "Planning : non utilisé (le bot est toujours actif)."
    st = planning.state(settings)
    if st["active"]:
        return f"Planning : plage active jusqu'à {st['until']:%H:%M}."
    return f"Planning : hors plage, reprise {planning.describe(st['next'])}."


def _due_text(item: dict) -> str:
    if item["running"]:
        return "en cours"
    if item["next_in_s"] is None:
        return "pas de passage prévu"
    text = f"dans {span(item['next_in_s'])}"
    return f"{text} (attend : {item['paused']})" if item.get("paused") else text


def next_passes(bot) -> str:
    """Prochain passage de chaque fonctionnalité activée, du plus proche au plus lointain."""
    data = bot.scheduler.summary()
    enabled = [f for f in data["features"] if f["enabled"]]
    state = "ON" if data["bot_on"] else "OFF"
    lines = [f"Bot {state}" + (f", {activity(data['activity'])}" if data["bot_on"] else ""), _planning_line(bot)]
    if not enabled:
        return "\n".join([*lines, "Aucune fonctionnalité activée."])
    if not data["bot_on"]:
        names = ", ".join(f["label"] for f in enabled)
        return "\n".join([*lines, f"Aucun passage tant que le bot est sur OFF. Activées : {names}."])
    far = float("inf")
    for item in sorted(enabled, key=lambda f: far if f["next_in_s"] is None else f["next_in_s"]):
        lines.append(f"• {item['label']} : {_due_text(item)}")
    return "\n".join(lines)


def _feature_rule(item: dict) -> str:
    if item["self_timed"]:
        return "relance automatique"
    low, high = item["min_minutes"], item["max_minutes"]
    return f"toutes les {low:g} à {high:g} min" if low is not None and high is not None else "minuteur non réglé"


def _feature_last(item: dict) -> str:
    if item["running"]:
        return "en cours"
    if not item["last_at"]:
        return "aucun"
    result = item["last_result"][:RESULT_CHARS] + ("…" if len(item["last_result"]) > RESULT_CHARS else "")
    return f"il y a {span(time.time() - item['last_at'])} : {'' if item['last_ok'] else 'ÉCHEC, '}{result}"


def features_status(bot) -> str:
    """Les fonctionnalités activées (règle de minuteur, dernier passage) puis les noms de celles qui sont éteintes."""
    data = bot.scheduler.summary()
    enabled = [f for f in data["features"] if f["enabled"]]
    off = [f["label"] for f in data["features"] if not f["enabled"]]
    lines = [f"• {f['label']} — {_feature_rule(f)} ; dernier passage : {_feature_last(f)}" for f in enabled]
    if not enabled:
        lines.append("Aucune fonctionnalité activée.")
    if off:
        lines.append("Désactivées : " + ", ".join(off) + ".")
    return "\n".join(lines)


def _place(side: dict) -> str:
    return " ".join(x for x in ((side or {}).get("name"), (side or {}).get("coords")) if x) or "?"


def _fleet_line(fleet: dict, now: float) -> str:
    left = fleet.get("arrival_at")
    arrival = f"arrive dans {span(left - now)}" if left else "durée inconnue"
    recall = " — rappelable" if fleet.get("can_recall") else ""
    return f"• {fleet.get('kind') or 'Flotte'} · {_place(fleet.get('origin'))} → {_place(fleet.get('destination'))} : {arrival}{recall}"


def fleets_in_flight(bot) -> str:
    """Flottes du dernier relevé des Capteurs (celles du bot exclues), puis les retours programmés."""
    snap, now = bot.fleets.snapshot(), time.time()
    if not snap["read_at"]:
        return "Aucun relevé de flottes pour l'instant : relancez avec l'option « actualiser »."
    flying = [f for f in snap["fleets"] if not f["excluded"] and (not f.get("arrival_at") or f["arrival_at"] > now)]
    flying.sort(key=lambda f: f.get("arrival_at") or float("inf"))
    lines = [f"Flottes en vol (relevé il y a {span(now - snap['read_at'])}) :"]
    lines += [_fleet_line(f, now) for f in flying[:MAX_LINES]] or ["Aucune flotte en vol."]
    if len(flying) > MAX_LINES:
        lines.append(f"… et {len(flying) - MAX_LINES} autre(s).")
    pending = bot.fleets.pending()
    if pending:
        lines.append("Retours programmés :")
        lines += [f"• {r['fleet']['label']} le {when(r['at'])}" for r in pending[:MAX_LINES]]
    return "\n".join(lines)


def planets_stocks(bot) -> str:
    """Planètes du dernier scan avec leurs stocks de Fer, Cristal et Éther."""
    scan = bot.supervisor.scan_summary()
    if not scan["planets"]:
        why = f" ({scan['error']})" if scan["error"] else ""
        return f"Aucune planète relevée pour l'instant{why} : relancez avec l'option « actualiser »."
    age = f" (relevé il y a {span(time.time() - scan['scanned_at'])})" if scan["scanned_at"] else ""
    lines = [f"{len(scan['planets'])} planète(s){age} :"]
    for p in scan["planets"][:MAX_LINES]:
        lines.append(f"• {p['name']} {p['coords']} : Fer {amount(p['fer'])} · Cristal {amount(p['cristal'])} · Éther {amount(p['ether'])}")
    if len(scan["planets"]) > MAX_LINES:
        lines.append(f"… et {len(scan['planets']) - MAX_LINES} autre(s).")
    return "\n".join(lines)
