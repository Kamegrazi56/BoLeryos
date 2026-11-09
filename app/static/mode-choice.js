// Choix du mode d'une ligne (Population, Assemblages, Composants) : une liste « Quantité / MAX / Effectif à atteindre »
// et, à côté, deux boutons « À chaque passage » / « Une seule fois ». Effectif à atteindre grise ces deux boutons :
// le bot s'arrête de lui-même une fois l'effectif atteint.
(() => {
  const { el } = window.ui;
  const MODES = [["fixed", "Quantité"], ["max", "MAX"], ["target", "Effectif à atteindre"]];
  let groups = 0;

  /**
   * @param {{ name: string, mode?: string, repeat?: boolean, select?: HTMLSelectElement, onChange?: () => void }} options
   *   `select` : liste déjà présente dans la page (formulaire d'ajout) ; sinon une liste est créée.
   * @returns {{ select: HTMLSelectElement, node: HTMLElement, mode: () => string, repeat: () => boolean }}
   */
  function modeChoice({ name, mode = "fixed", repeat = true, select = null, onChange = () => {} }) {
    const list = select || el("select", { ariaLabel: `Mode de ${name}` });
    if (!list.children.length) list.replaceChildren(...MODES.map(([value, text]) => el("option", { value, textContent: text })));
    list.value = MODES.some(([value]) => value === mode) ? mode : "fixed";

    const group = `repeat-choice-${++groups}`;
    let lastRepeat = mode === "target" ? true : repeat !== false; // retrouvé si l'on quitte « Effectif à atteindre »
    const radio = (value, text) => {
      const input = el("input", { type: "radio", name: group, value, ariaLabel: `${text} : ${name}` });
      input.addEventListener("change", () => { lastRepeat = value === "1"; onChange(); });
      return { input, label: el("label", {}, input, " " + text) };
    };
    const each = radio("1", "À chaque passage");
    const once = radio("0", "Une seule fois");
    const node = el("span", { className: "repeat-choice", role: "radiogroup", ariaLabel: `Fréquence de ${name}` }, each.label, once.label);

    function sync() {
      const target = list.value === "target";
      node.classList.toggle("is-disabled", target);
      node.title = target ? "Effectif à atteindre : le bot s'arrête de lui-même une fois l'effectif atteint." : "";
      [each, once].forEach(({ input }) => { input.disabled = target; });
      each.input.checked = !target && lastRepeat;
      once.input.checked = !target && !lastRepeat;
    }
    list.addEventListener("change", () => { sync(); onChange(); });
    sync();
    return { select: list, node, mode: () => list.value, repeat: () => list.value !== "target" && lastRepeat };
  }

  window.modeChoice = modeChoice;
})();
