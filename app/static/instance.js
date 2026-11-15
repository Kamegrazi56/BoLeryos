// Instance de jeu visée par cet onglet. Chargé avant shell.js sur toutes les pages : toutes les requêtes de la page
// (`/api/…`, `/ws/…`) sont adressées à l'instance choisie (`/api/i/<instance>/…`) sans que chaque page ait à s'en soucier.
// Sans choix, c'est la première instance qui répond, comme avant l'arrivée des instances.
(() => {
  const KEY = "boleryos.instance";
  const read = () => { try { return sessionStorage.getItem(KEY) || localStorage.getItem(KEY) || ""; } catch { return ""; } };
  const write = (id) => { try { sessionStorage.setItem(KEY, id); localStorage.setItem(KEY, id); } catch { /* stockage indisponible */ } };
  let selected = read();
  let summary = null;

  /** `/api/status` → `/api/i/<id>/status` ; les routes de gestion (`/api/instances`) et déjà ciblées restent telles quelles. */
  function scope(path, id = selected) {
    if (!id) return path;
    const m = /^\/(api|ws)\/(?!i\/|instances(?:[/?]|$))/.exec(path);
    return m ? `/${m[1]}/i/${id}/${path.slice(m[0].length)}` : path;
  }

  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, init) => nativeFetch(typeof input === "string" ? scope(input) : input, init);

  const NativeWebSocket = window.WebSocket;
  // Un Proxy plutôt qu'une sous-classe : l'objet rendu reste un vrai WebSocket natif (noVNC en vérifie le prototype).
  window.WebSocket = new Proxy(NativeWebSocket, {
    construct(target, args) {
      const [url, protocols] = args;
      const u = new URL(url, location.href);
      u.pathname = scope(u.pathname);
      return protocols === undefined ? new target(u.href) : new target(u.href, protocols);
    },
  });

  const current = () => selected || (summary && summary.default) || "";
  function select(id) {
    selected = id;
    write(id);
  }
  window.Instance = {
    current,
    select,
    scope,
    /** Requête adressée à une instance précise (`compte` : la session de connexion), quelle que soit celle de l'onglet. */
    fetchAs: (id, path, init) => nativeFetch(scope(path, id), init),
    summary: () => summary,
  };

  // ------------------------------------------------------------------------- sélecteur d'instance
  const STATE_LABEL = { stopped: "arrêtée", idle: "démarrage", logging_in: "connexion", logged_in: "connectée", error: "erreur" };
  let selector = null;
  let banner = null;

  function paintSelector() {
    if (!selector || !summary) return;
    const list = summary.instances;
    selector.hidden = list.length < 2 && !selected;
    const wanted = current();
    selector.replaceChildren(...list.map((i) => {
      const o = document.createElement("option");
      o.value = i.id;
      o.textContent = `${i.name} · ${STATE_LABEL[i.state] || i.state}`;
      return o;
    }));
    if (list.some((i) => i.id === wanted)) selector.value = wanted;
  }

  function paintBanner() {
    const top = document.querySelector("header.top");
    if (!top || document.body.dataset.page === "home" || !summary) return;
    const inst = summary.instances.find((i) => i.id === current());
    if (inst && inst.running) { if (banner) { banner.remove(); banner = null; } return; }
    if (!banner) {
      banner = document.createElement("div");
      banner.className = "banner";
      top.after(banner); // sous l'en-tête, hors de la mise en page propre à chaque page
    }
    const text = document.createElement("span");
    text.textContent = inst ? `L'instance « ${inst.name} » est arrêtée : cette page n'affiche rien tant qu'elle ne tourne pas. ` : "Aucune instance : créez-en une depuis l'accueil. ";
    const link = document.createElement(inst ? "button" : "a");
    link.textContent = inst ? "Démarrer" : "Accueil";
    if (inst) link.addEventListener("click", async () => { link.disabled = true; await nativeFetch(`/api/instances/${inst.id}/start`, { method: "POST" }); location.reload(); });
    else link.href = "/";
    banner.replaceChildren(text, link);
  }

  async function refresh() {
    try {
      summary = await (await nativeFetch("/api/instances")).json();
    } catch { return; } // l'application redémarre : on réessaie
    if (selected && !summary.instances.some((i) => i.id === selected)) { // instance supprimée depuis un autre onglet
      select("");
      location.reload();
      return;
    }
    paintSelector();
    paintBanner();
    window.dispatchEvent(new CustomEvent("boleryos:instances", { detail: summary }));
  }

  const header = document.querySelector("header.top");
  if (header) {
    selector = document.createElement("select");
    selector.id = "instance-select";
    selector.className = "instance-select";
    selector.hidden = true;
    selector.setAttribute("aria-label", "Instance de jeu");
    selector.addEventListener("change", () => { select(selector.value); location.reload(); });
    header.insertBefore(selector, document.getElementById("power"));
  }
  refresh();
  setInterval(refresh, 3000);
})();
