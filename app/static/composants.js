// Production de composants : même fonctionnement que l'onglet Population (voir line-table.js). Ici, l'effectif de chaque composant sur
// chaque planète vient de l'onglet « Composants » du Chantier spatial (le bot le lit à chaque passage, ou à la demande en visitant
// chaque planète) ; les composants sont rangés par catégorie.
window.lineTable({
  prefix: "cmp",
  api: "/api/components",
  refreshUrl: "/api/components/inventory/refresh",
  key: "item",
  storageKey: "boleryos.components",
  verb: "Produire",
  catalogOf: (data) => data.components.map((c) => ({ id: c.id, label: c.label, group: c.category })),
  emptyInventory: { counts: {} },
  // une colonne par planète connue (scan des planètes) ; une planète jamais relevée montre « — » partout
  columnsOf: (inventory, planets) => planets.map((name) => ({ name, coords: "", counts: (inventory.counts || {})[name] || {} })),
  planetsOf: (inventory, planets) => planets,
  text: {
    queueEmpty: "Aucune ligne : choisissez l'action des boutons, puis cliquez sur une case du tableau ci-dessous.",
    noColumns: "Aucune planète connue : lancez un scan des planètes (Réglages › Compte et univers).",
    noReading: "Pas encore de relevé : il se fait au prochain passage de la production, ou avec le bouton.",
    onPass: "au passage de la production",
    column: "Composant",
  },
});
