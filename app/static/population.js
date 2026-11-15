// Recrutement de population : une ligne par (population, planète) avec son mode (quantité fixe / MAX / effectif à atteindre),
// sa quantité et ses planètes. Tout le fonctionnement (liste, barre d'outils, tableau des effectifs) est dans line-table.js,
// partagé avec l'onglet Composants ; ici, seulement ce qui est propre aux populations : où lire l'effectif et comment nommer les choses.
// Effectifs actuels : Empire › Inventaire, section « Unités terrestres ».
window.lineTable({
  prefix: "pop",
  api: "/api/populations",
  refreshUrl: "/api/constructions/inventory/refresh",
  key: "population",
  storageKey: "boleryos.populations",
  verb: "Recruter",
  catalogOf: (data) => data.populations.map((p) => ({ id: p.id, label: p.label, group: p.tab })),
  emptyInventory: { planets: [] },
  columnsOf: (inventory) => inventory.planets.filter((p) => p.units).map((p) => ({ name: p.name, coords: p.coords, counts: p.units })),
  planetsOf: (inventory, planets) => [...new Set([...planets, ...inventory.planets.map((p) => p.name)])], // le relevé connaît parfois des planètes que le scan ignore encore
  text: {
    queueEmpty: "Aucune ligne : choisissez l'action des boutons, puis cliquez sur une case du tableau ci-dessous.",
    noColumns: "Aucun effectif relevé pour l'instant.",
    noReading: "Pas encore de relevé : il se fait au prochain passage du recrutement, ou avec le bouton.",
    onPass: "au passage du recrutement",
    column: "Population",
  },
});
