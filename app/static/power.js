// Interrupteur ON/OFF du bot, présent dans l'en-tête de toutes les pages.
// ON : le bot regarde où en est le site (connexion, choix de l'univers, partie) et ne fait que ce qui manque.
(() => {
  const btn = document.getElementById("power");
  if (!btn) return;
  let running = false;
  let busy = false;

  function paint(a) {
    running = !!a.running;
    btn.className = "power " + (running ? "on" : "off");
    btn.textContent = running ? "Bot : ON" : "Bot : OFF";
    btn.setAttribute("aria-pressed", String(running));
    btn.title = running
      ? (a.waiting ? `En attente : ${a.waiting}` : "Le bot est actif. Cliquer pour l'arrêter.")
      : "Le bot est arrêté. Cliquer pour l'activer : il se connecte si besoin, choisit l'univers, puis travaille.";
  }
  async function poll() {
    try {
      paint((await (await fetch("/api/status?limit=1")).json()).automation);
    } catch { /* le bot redémarre : on réessaie */ }
  }
  btn.addEventListener("click", async () => {
    if (busy) return;
    busy = true;
    btn.disabled = true;
    try {
      if (running) {
        await fetch("/api/automation/stop", { method: "POST" });
      } else {
        if (window.beforePowerOn) await window.beforePowerOn(); // la page Actions enregistre d'abord ses réglages
        await fetch("/api/automation/start", { method: "POST" });
      }
    } finally {
      busy = false;
      btn.disabled = false;
      poll();
    }
  });
  poll();
  setInterval(poll, 2000);
})();
