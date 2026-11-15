"""Chromium partagé : un seul processus pour tout le bot, un contexte (session vierge) par session de bot.

Un contexte coûte environ 105 Mo de mémoire, un Chromium complet environ 410 Mo : plusieurs sessions de jeu
tiennent donc dans un seul navigateur. L'hôte lance Chromium à la demande, le relance s'il a disparu (plantage,
fermeture extérieure) et possède l'écran virtuel du mode « navigateur complet » (noVNC).
"""

from __future__ import annotations

import asyncio
import os
import time

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

from .config import Settings
from .display import VirtualDisplay

MEMORY_CACHE_SECONDS = 5.0
_memory_cache: tuple[float, float | None] = (0.0, None)


def _process_memory_kb(folder: str, page_kb: float) -> float:
    """Mémoire d'un processus en ko : part proportionnelle (Pss) si le noyau la donne, sinon mémoire résidente."""
    try:
        with open(f"{folder}/smaps_rollup") as f:
            for line in f:
                if line.startswith("Pss:"):
                    return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    with open(f"{folder}/statm") as f:
        return int(f.read().split()[1]) * page_kb


def chromium_memory_mb(proc_root: str = "/proc") -> float | None:
    """Mémoire résidente de tous les processus Chromium (en Mo), lue dans /proc ; None si indisponible (hors Linux).

    La part proportionnelle (Pss, `smaps_rollup`) répartit les pages partagées entre les processus qui les utilisent ; sans
    elle, on retombe sur la mémoire résidente (`statm`), qui les compte plusieurs fois (borne haute).
    Le résultat est gardé quelques secondes (l'interface le demande à chaque rafraîchissement)."""
    global _memory_cache
    now = time.monotonic()
    if now - _memory_cache[0] < MEMORY_CACHE_SECONDS and proc_root == "/proc":
        return _memory_cache[1]
    total_kb, found = 0.0, False
    page_kb = os.sysconf("SC_PAGE_SIZE") / 1024
    try:
        entries = [e for e in os.listdir(proc_root) if e.isdigit()]
    except OSError:
        return None
    for pid in entries:
        try:
            with open(f"{proc_root}/{pid}/cmdline", "rb") as f:
                command = f.read(512).split(b"\0", 1)[0].lower()
            if b"chrom" not in command and b"headless_shell" not in command:
                continue
            total_kb += _process_memory_kb(f"{proc_root}/{pid}", page_kb)
            found = True
        except (OSError, ValueError, IndexError):
            continue  # processus terminé entre-temps
    value = round(total_kb / 1024, 1) if found else None
    if proc_root == "/proc":
        _memory_cache = (now, value)
    return value


class BrowserHost:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._lock = asyncio.Lock()
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        # Navigateur complet : vraie fenêtre Chromium sur un écran virtuel affiché par noVNC (image Docker)
        self.display: VirtualDisplay | None = (
            VirtualDisplay(settings.vnc_display, settings.vnc_width, settings.vnc_height, settings.vnc_port)
            if settings.vnc and VirtualDisplay.available() else None
        )

    @property
    def connected(self) -> bool:
        return self._browser is not None and self._browser.is_connected()

    @property
    def context_count(self) -> int:
        return len(self._browser.contexts) if self.connected else 0

    async def ensure(self) -> None:
        """Chromium prêt : lancé s'il ne tourne pas, relancé s'il a disparu."""
        async with self._lock:
            if self.connected:
                return
            await self._close_unlocked()  # restes d'un navigateur disparu
            try:
                await self._launch()
            except BaseException:
                await self._close_unlocked()  # pas de Chromium à moitié lancé
                raise

    async def new_context(self, **options) -> BrowserContext:
        await self.ensure()
        assert self._browser is not None
        return await self._browser.new_context(**options)

    async def close(self) -> None:
        """Ferme Chromium (et ses contextes) ; l'écran virtuel reste, l'hôte peut être relancé par `ensure`."""
        async with self._lock:
            await self._close_unlocked()

    async def shutdown(self) -> None:
        """Arrêt définitif : Chromium puis écran virtuel."""
        try:
            await self.close()
        finally:
            if self.display is not None:
                await self.display.stop()

    async def _launch(self) -> None:
        s = self._settings
        self._pw = await async_playwright().start()
        args = ["--no-sandbox"] if s.no_sandbox else []
        # Plusieurs instances partagent ce navigateur : celles dont la fenêtre est derrière une autre (ou sans écran ouvert)
        # ne doivent pas être ralenties, sans quoi leur bot attendrait trop longtemps le jeu.
        args += ["--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                 "--disable-backgrounding-occluded-windows"]
        if self.display is not None:
            # Vraie fenêtre (barre d'adresse, onglets) sur l'écran virtuel, à sa taille exacte
            await self.display.ensure()
            args += ["--window-position=0,0", f"--window-size={self.display.width},{self.display.height}",
                     "--no-first-run", "--no-default-browser-check", "--lang=fr-FR"]
            self._browser = await self._pw.chromium.launch(
                headless=False, executable_path=s.chromium_path, args=args,
                # langue de l'interface de Chromium = celle du jeu : pas de proposition de traduction
                env={**os.environ, "DISPLAY": self.display.name, "LANGUAGE": "fr", "LANG": "fr_FR.UTF-8"},
            )
        else:
            self._browser = await self._pw.chromium.launch(headless=s.headless, executable_path=s.chromium_path, args=args)

    async def _close_unlocked(self) -> None:
        browser, pw = self._browser, self._pw
        self._browser = self._pw = None
        for closer in (browser.close if browser else None, pw.stop if pw else None):
            if closer is not None:
                try:
                    await closer()
                except Exception:  # noqa: BLE001 - déjà fermé ou en cours de fermeture
                    pass
