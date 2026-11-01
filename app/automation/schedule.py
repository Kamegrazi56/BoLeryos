"""Plages horaires d'activité du bot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Window:
    start: time
    end: time
    days: frozenset[int]  # 0 = lundi … 6 = dimanche ; pour une plage de nuit, jour du DÉBUT


def parse_hhmm(text: str) -> time:
    hours, minutes = str(text).split(":")
    return time(int(hours), int(minutes))


def windows_from_settings(raw: list[dict]) -> list[Window]:
    return [Window(parse_hhmm(w["start"]), parse_hhmm(w["end"]), frozenset(w["days"])) for w in raw]


def is_active(now: datetime, windows: list[Window]) -> bool:
    """`now` est une heure locale (déjà dans le bon fuseau)."""
    t, weekday = now.time().replace(second=0, microsecond=0), now.weekday()
    for w in windows:
        if w.start == w.end:  # même heure : toute la journée
            if weekday in w.days:
                return True
        elif w.start < w.end:
            if weekday in w.days and w.start <= t < w.end:
                return True
        else:  # plage de nuit (ex. 22:00 → 06:00)
            if (weekday in w.days and t >= w.start) or (((weekday - 1) % 7) in w.days and t < w.end):
                return True
    return False


def next_change(now: datetime, windows: list[Window]) -> tuple[bool, datetime | None]:
    """(actif maintenant, prochain changement d'état) — recherche à la minute sur 8 jours."""
    current = is_active(now, windows)
    probe = now.replace(second=0, microsecond=0)
    for _ in range(8 * 24 * 60):
        probe += timedelta(minutes=1)
        if is_active(probe, windows) != current:
            return current, probe
    return current, None


def local_now(timezone: str) -> datetime:
    return datetime.now(ZoneInfo(timezone))
