"""Écran virtuel du navigateur du bot (mode « navigateur complet »).

Le Chromium du bot tourne avec sa vraie fenêtre (barre d'adresse, onglets) sur un écran virtuel Xvnc. L'interface
l'affiche avec noVNC, comme le conteneur « Chromium » de linuxserver : on navigue et on joue comme dans un vrai
navigateur, au clavier et à la souris, sans passer par des captures d'image.

Sécurité : le serveur VNC n'écoute que sur 127.0.0.1 (aucun port supplémentaire exposé) ; l'interface y accède par le
WebSocket /ws/vnc du bot, qui vérifie l'origine comme les autres pages.
"""

from __future__ import annotations

import asyncio
import shutil


class VirtualDisplay:
    def __init__(self, number: int, width: int, height: int, port: int) -> None:
        self.number, self.width, self.height, self.port = number, width, height, port
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    def available() -> bool:
        return shutil.which("Xvnc") is not None

    @property
    def name(self) -> str:
        return f":{self.number}"

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def ensure(self) -> None:
        async with self._lock:
            if self.running:
                return
            self._proc = await asyncio.create_subprocess_exec(
                "Xvnc", self.name, "-geometry", f"{self.width}x{self.height}", "-depth", "24",
                "-rfbport", str(self.port), "-localhost", "-SecurityTypes", "None", "-AlwaysShared",
                "-AcceptSetDesktopSize=1", "-nolisten", "tcp",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            for _ in range(50):  # le serveur est prêt quand son port répond
                try:
                    _, writer = await asyncio.open_connection("127.0.0.1", self.port)
                    writer.close()
                    await asyncio.sleep(0.2)
                    if self._proc.returncode is not None:  # un autre serveur occupe déjà cet écran
                        raise RuntimeError("l'écran virtuel est déjà utilisé par un autre programme")
                    return
                except OSError:
                    if self._proc.returncode is not None:
                        raise RuntimeError("l'écran virtuel (Xvnc) n'a pas démarré")
                    await asyncio.sleep(0.1)
            raise RuntimeError("l'écran virtuel (Xvnc) ne répond pas")

    async def resize(self, width: int, height: int) -> bool:
        """Change la taille de l'écran virtuel (RandR) : il épouse la zone d'affichage de l'interface."""
        width, height = max(640, min(int(width), 3840)) // 2 * 2, max(400, min(int(height), 2160)) // 2 * 2
        if abs(width - self.width) <= 4 and abs(height - self.height) <= 4:
            return False
        if not self.running or shutil.which("xrandr") is None:
            return False
        mode = f"{width}x{height}"
        env = {"DISPLAY": self.name, "PATH": "/usr/bin:/bin"}

        async def xrandr(*args: str) -> tuple[int, str]:
            proc = await asyncio.create_subprocess_exec("xrandr", *args, env=env, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.STDOUT)
            out, _ = await proc.communicate()
            return proc.returncode or 0, out.decode(errors="replace")

        _, listing = await xrandr()
        output = next((line.split()[0] for line in listing.splitlines() if " connected" in line), "VNC-0")
        if f"   {mode} " not in listing and f"   {mode}\n" not in listing + "\n":
            # mode fictif (Xvnc n'utilise pas les fréquences) : il suffit qu'il existe pour pouvoir le choisir
            await xrandr("--newmode", mode, "60", str(width), str(width + 1), str(width + 2), str(width + 3),
                         str(height), str(height + 1), str(height + 2), str(height + 3))
            await xrandr("--addmode", output, mode)
        code, _ = await xrandr("--output", output, "--mode", mode)
        if code == 0:
            self.width, self.height = width, height
            return True
        return False

    async def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 5)
            except asyncio.TimeoutError:
                proc.kill()
