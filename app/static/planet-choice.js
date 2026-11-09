// Choix des planètes d'une ligne (Population, Assemblages, Composants) : « Toutes les planètes » (y compris celles
// colonisées plus tard) ou « Planètes choisies » : cases à cocher sur 2 lignes, triées dans l'ordre naturel
// (1, 2, … 12, puis les noms), avec « Tout cocher » / « Tout décocher ».
(() => {
  const { el } = window.ui;
  const order = (names) => [...names].sort((a, b) => a.localeCompare(b, "fr", { numeric: true, sensitivity: "base" }));
  const ALL_TITLE = "Toutes les planètes, y compris celles colonisées plus tard.";
  let groups = 0;

  /** Texte court des planètes d'une ligne ([] = toutes). */
  function planetsText(planets) {
    if (!planets.length) return "Toutes les planètes";
    return planets.length <= 2 ? order(planets).join(", ") : `${planets.length} planètes`;
  }

  /**
   * @param {{ planets: string[], selected?: string[], name: string, onChange?: () => void, compact?: boolean }} options
   *   `compact` : replié derrière un résumé cliquable (lignes de la liste) ; sinon affiché en entier (formulaire d'ajout).
   * @returns {{ node: HTMLElement, value: () => (string[] | null), setPlanets: (planets: string[]) => void }}
   *   `value()` : [] pour toutes, la liste cochée sinon, null si « Planètes choisies » sans aucune case cochée.
   */
  function planetChoice({ planets, selected = [], name, onChange = () => {}, compact = false }) {
    const group = `planet-choice-${++groups}`;
    let names = null; // planètes affichées, dans l'ordre naturel
    let checked = new Set(selected);
    const radio = (value, text, title) => {
      const input = el("input", { type: "radio", name: group, value, ariaLabel: `${text} : ${name}` });
      input.addEventListener("change", changed);
      return { input, label: el("label", { title }, input, " " + text) };
    };
    const all = radio("all", "Toutes les planètes", ALL_TITLE);
    const some = radio("some", "Planètes choisies", "Seulement les planètes cochées ci-dessous.");
    all.input.checked = selected.length === 0;
    some.input.checked = selected.length > 0;

    const grid = el("div", { className: "planet-grid", role: "group", ariaLabel: `Planètes de ${name}` });
    const button = (text, check) => {
      const b = el("button", { type: "button", className: "secondary small", textContent: text, ariaLabel: `${text} : ${name}` });
      b.addEventListener("click", () => {
        checked = new Set(check ? names : []);
        grid.querySelectorAll("input").forEach((box) => { box.checked = check; });
        changed();
      });
      return b;
    };
    const checkAll = button("Tout cocher", true);
    const uncheckAll = button("Tout décocher", false);
    const body = el("div", { className: "planet-choice" },
      el("div", { className: "planet-choice-head" },
        el("span", { className: "repeat-choice", role: "radiogroup", ariaLabel: `Choix des planètes : ${name}` }, all.label, some.label),
        el("span", { className: "planet-tools" }, checkAll, uncheckAll)),
      grid);

    const value = () => (all.input.checked ? [] : checked.size ? (names || []).filter((p) => checked.has(p)) : null);
    const summary = compact ? el("summary", {}) : null;
    function sync() {
      const off = all.input.checked;
      body.classList.toggle("is-all", off);
      grid.querySelectorAll("input").forEach((box) => { box.disabled = off; });
      [checkAll, uncheckAll].forEach((b) => { b.disabled = off; });
      if (summary) {
        const v = value();
        summary.textContent = v === null ? "Cochez des planètes…" : planetsText(v);
        summary.title = v && v.length > 2 ? order(v).join(", ") : "";
      }
    }
    function changed() { sync(); onChange(); }

    function setPlanets(list) {
      const next = order([...new Set([...list, ...checked])]);
      if (names && next.join("\n") === names.join("\n")) return; // rien de neuf : on ne touche pas aux cases
      names = next;
      grid.style.setProperty("--cols", String(Math.max(1, Math.ceil(names.length / 2))));
      grid.replaceChildren(...names.map((p) => {
        const box = el("input", { type: "checkbox", checked: checked.has(p), ariaLabel: `${p} pour ${name}` });
        box.addEventListener("change", () => { if (box.checked) checked.add(p); else checked.delete(p); changed(); });
        return el("label", { title: p }, box, el("span", { textContent: p })); // nom complet au survol s'il est abrégé
      }));
      if (!names.length) grid.replaceChildren(el("span", { className: "hint", textContent: "Aucune planète connue : lancez un scan des planètes." }));
      sync();
    }
    setPlanets(planets);

    const node = compact ? el("details", { className: "planet-picker" }, summary, body) : body;
    sync();
    return { node, value, setPlanets };
  }

  window.planetChoice = planetChoice;
})();
