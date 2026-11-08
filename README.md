# BoLeryos

Bot d'automatisation pour Aleryos, destiné aux **tests de protection anti-bot réalisés avec l'éditeur** (GEEKOS NEXUS). Il tourne dans Docker et se pilote depuis une interface web.

> Le règlement du jeu (§12) interdit les bots sur les comptes de joueurs. Ce projet ne doit être utilisé que dans le cadre de l'accord avec l'éditeur, de préférence avec un compte de test dédié.

> Plan de développement, décisions prises et feuille de route : [`docs/PLAN.md`](docs/PLAN.md).

## Pages

Interface au style inspiré d'Aleryos (espace profond, panneaux translucides, accents cyan et violet).

| Page | Rôle |
|---|---|
| `/` **Accueil** | État de la connexion, univers, **planètes scannées** (nom, coordonnées, stocks) et derniers événements. |
| `/ecran` **Écran** | Vue en direct du navigateur du bot, contrôle manuel (jouer soi-même dans la même vue), choix d'univers et exploration de la structure du site. |
| `/reglages` **Réglages** | 7 onglets : **Compte et univers** (identifiants, univers, délai de connexion, scan des planètes), **Fonctionnalités** (interrupteur, **minuteur entre X et Y minutes**, prochain passage en direct et ordre de **priorité** de chaque fonctionnalité) **Constructions** (liste ordonnée « bâtiment + niveau cible », toutes les planètes ou une seule) et **Recherches** (liste ordonnée « recherche + niveau cible » des 23 recherches du Laboratoire, et **planète chargée des recherches** : une seule recherche à la fois, payée par cette planète ; lancement non vérifié sur le vrai jeu) et **Population** (une ligne par population recrutable : quantité fixe ou MAX, **à chaque passage ou une seule fois** (une fois par planète ; modifier la ligne la remet à zéro), ou effectif à atteindre ; planète au choix ou toutes, chacune traitée séparément ; liste ordonnée = priorité ; recrutement non vérifié sur le vrai jeu), **Assemblages** (bouton **Scanner les modèles** + une ligne par modèle de vaisseau : quantité fixe ou MAX, à chaque passage ou une seule fois, ou effectif à atteindre ; planètes : toutes, une seule ou plusieurs ; construit **uniquement avec des ressources**, mode Construction ; non vérifié sur le vrai jeu) et **Composants** (37 composants en 6 catégories, mêmes réglages ; non vérifié sur le vrai jeu). Enregistrement automatique. |
| `/suivi` **Suivi** | **Journal** lisible (événements importants ; détails techniques en option). |

Les anciennes adresses `/vision` et `/actions` redirigent vers `/ecran` et `/reglages`. Des pastilles **?** expliquent les réglages moins évidents.

### Navigateur complet (page Écran)

Dans l'image Docker, le Chromium du bot tourne avec sa **vraie fenêtre** (onglets, barre d'adresse) sur un écran virtuel, affiché dans la page **Écran** par **noVNC** : on navigue et on joue comme dans un vrai navigateur, au clavier et à la souris, comme avec le conteneur « Chromium » de linuxserver. C'est le même navigateur que celui qu'utilise le bot.

- Le navigateur occupe **toute la page sous le bandeau du haut**. Dans ce bandeau : « Contrôle manuel », « Plein écran », « Outils & journal » (panneau escamotable : page affichée, univers, exploration de la structure, journal, déconnexion).
- **Regarder** : par défaut, la vue est en lecture seule (le bot peut agir). La **molette fait toujours défiler la page du jeu**, même sans contrôle manuel ; clics et clavier restent bloqués.
- **Contrôle manuel** : cochez la case pour cliquer, taper, faire défiler, utiliser la barre d'adresse ou les onglets ; le bot se met en pause tant qu'elle est cochée (et la décoche tout seul quand plus aucun écran n'est ouvert).
- **Plein écran** : bouton au-dessus de la vue (Échap pour sortir). L'écran virtuel prend toujours la taille de la zone d'affichage : image nette, sans bandes vides.
- Rien de plus à exposer : le serveur VNC n'écoute que dans le conteneur, la page passe par le port du bot (7654) et sa vérification d'origine ; la protection `ui_user` / `ui_password` s'applique aussi.
- Le bot travaille dans le premier onglet : un onglet ouvert à la main n'est pas vu par le bot.
- Toute personne qui accède à l'interface peut agir sur le compte du jeu : protégez-la (`ui_user` / `ui_password`).

### Vue par images (hors Docker)

Sans écran virtuel (tests, développement), la page Écran affiche un flux d'images poussé par Chrome (CDP `Page.startScreencast`) avec un contrôle manuel simplifié ; les mouvements de souris en attente y sont fusionnés pour qu'un clic parte tout de suite.

### Choix de l'univers

Après la connexion, la page « Choisissez votre univers » du jeu est lue par le bot : un bouton par univers apparaît au-dessus de l'écran, et un clic le fait entrer (avec les délais humains). La détection repose pour l'instant sur le seul libellé observé (« Reprendre la partie ») et sur une structure supposée d'après une capture d'écran : à confirmer avec l'export ci-dessous. En attendant, le contrôle manuel permet de cliquer soi-même.

### Exploration de la structure du site

Le panneau « Exploration de la structure du site » (désactivé par défaut) enregistre, pendant que vous naviguez, la structure des pages (balises, attributs, tous les textes visibles : niveaux, coûts, durées…), les chemins d'API et la **forme** des réponses JSON et des messages WebSocket. Il exporte un fichier `boleryos-structure.json`.

- Conservés : chemins (identifiants remplacés par `{id}`), noms de paramètres, **noms** d'en-têtes, de cookies et de clés de stockage, types des valeurs, courts libellés alphabétiques (ex. `open`).
- Jamais conservés : valeurs de cookies, d'en-têtes et de stockage, valeurs de paramètres d'URL, mots de passe et saisies, e-mails, jetons, identifiants. Des tests le vérifient.
- Les textes visibles à l'écran (titres, boutons, pseudo) et les courts libellés peuvent contenir des informations de jeu : relisez le fichier avant de le partager. Ne le commitez pas dans un dépôt public.

## Connexion automatique et scan des planètes

Le bot ne lance **aucune action de jeu** : constructions, recherches, production, expéditions, espionnage, attaques, priorités, horaires, sauvegarde des réglages d'automatisation et alertes ont été supprimés pour repartir de zéro. Il se connecte, entre dans l'univers, reste connecté et lit la liste des planètes.

Dans **Réglages › Compte et univers**, saisissez l'identifiant, le mot de passe et l'univers (champ avec suggestions), puis **« Enregistrer et se connecter »** : le bot se connecte **tout seul**, entre dans l'univers indiqué et **reste connecté** (il se reconnecte si la session expire). Le champ « Connexion : … » montre l'avancement en direct. « Oublier le compte » ou « Se déconnecter » (page Écran) arrête la connexion automatique. La session du navigateur est conservée dans le volume `/data` : un redémarrage du conteneur ne déconnecte pas.

| Écran du site | Ce que fait le bot |
|---|---|
| Page de connexion | Se connecte avec les identifiants mémorisés. |
| Choix de l'univers | Entre dans l'univers choisi (cliquer sur un bouton d'univers le mémorise aussi). |
| Déjà dans une partie | Saute la connexion et le choix de l'univers. |
| Session expirée | Se reconnecte tout seul. |
| Fenêtre « Journal de développement » | La ferme avant chaque clic ou saisie. |

**Scan des planètes :** une fois par connexion, dès que le bot est dans la partie, il ouvre la liste des planètes du jeu, lit nom, coordonnées et stocks (Fer, Cristal, Éther), puis la referme. Le bouton **« Scanner les planètes »** (Réglages) la relance à la demande ; le résultat s'affiche aussi sur l'Accueil.

**Garde-fous :** si le site **refuse** les identifiants, le bot n'essaie qu'**une fois** (risque de blocage du compte) et l'indique ; si la connexion n'aboutit pas (contrôle anti-bot ?), 3 essais espacés (1 min, 5 min) puis il demande de se connecter à la main et **reprend seul** dès que le site est connecté ; le contrôle manuel le met en pause.

**Identifiants :** enregistrés par le bot dans le volume `/data` (décision D1 « B », demandée le 05/11/2026) : l'identifiant dans `account.json`, le mot de passe **chiffré** (Fernet) avec une clé aléatoire gardée à côté dans `account.key` (droits 0600). La clé protège contre une lecture du seul fichier, pas contre une copie de tout le dossier : utilisez un compte de test et protégez l'interface (`UI_USER` / `UI_PASSWORD`). Jamais dans le journal, ni dans les réglages exportés ; le mot de passe n'est jamais renvoyé par l'API (le champ reste vide et indique « enregistré »). Au redémarrage ils sont relus mais le bot **ne se connecte pas seul** : « Relancer la connexion ». « Oublier le compte » efface les deux fichiers. Sans volume `/data`, ils restent en mémoire seulement.


**Préférences :** univers choisi et délai de connexion sont enregistrés dans le volume Docker `boleryos-data` (`/data/preferences.json`, non secret ; l'ancien `automation.json` est repris au premier démarrage).

## Fonctionnalités et interrupteur général (Réglages › Fonctionnalités)

- **Bot : ON / OFF** (en haut de chaque page) : à OFF, aucune fonctionnalité ne tourne ; le bot reste pourtant connecté et scanne les planètes. Il repasse **toujours à OFF** après un redémarrage.
- Chaque fonctionnalité a son interrupteur et son **minuteur** : après chaque passage, le bot attend une durée tirée au hasard entre le minimum et le maximum (le minuteur redémarre à la **fin** du passage). On ne peut pas activer sans minuteur. À l'activation (bot sur ON), le premier passage a lieu tout de suite. Pour les Explorations, le minuteur s'appelle « Vague d'explorations toutes les X à Y minutes ». Exceptions : **Fouilles** et **Piratage** n'ont pas de minuteur — ils se relancent tout seuls à la fin de chaque mission, plus le délai choisi dans leur onglet, avec une vérification de secours toutes les 30 minutes.
- **Un seul passage à la fois.** Si plusieurs sont dues, la plus haute de la liste de priorités passe d'abord ; un passage commencé n'est jamais interrompu (même si le bot passe à OFF) ; quelques secondes séparent deux passages.
- Un passage est repoussé tant que le bot n'est pas dans une partie ou que le contrôle manuel est actif.
- Activations, minuteurs et priorités sont enregistrés dans `/data/features.json` et survivent au redémarrage.
- **« Constructions de bâtiments »** (réglée dans l'onglet Constructions) : à chaque passage, pour chaque planète visée, le bot prend la **première ligne** de la liste dont le niveau actuel est sous le niveau cible et dont le bouton « Améliorer » est actif, **clique une fois** (dépense de vraies ressources) et vérifie que le stock a baissé. Une ligne dont le niveau cible est atteint (sur **toutes** les planètes visées) est **retirée automatiquement** de la liste ; il en va de même pour les recherches et pour les lignes de population qui ne se répètent pas (« une seule fois », « effectif à atteindre »), jamais pour celles « à chaque passage ». Au plus **une construction par planète et par passage** ; le même bâtiment n'est pas recliqué avant la fin présumée de sa construction (durée lue sur la carte, +10 %) ; le contrôle manuel l'interrompt ; après **deux lancements de suite dont le débit n'est pas constaté**, la fonctionnalité se désactive. **Non vérifié sur le vrai jeu** : tout a été testé sur un faux jeu reconstitué d'après les pages lues le 06/11 ; commencez par une seule planète et une seule ligne, en regardant la page Écran.
- **« Assemblages de vaisseaux »** et **« Production de composants »** (Chantier spatial, onglets Assemblages et Composants) : le bot lit la page `/shipyard`. Les **modèles de vaisseaux** sont lus par un **scan** (une fois par connexion, après le scan des planètes, et à la demande avec le bouton « Scanner les modèles ») car ils se créent dans le jeu à tout moment. Pour chaque planète visée (toutes, une seule ou plusieurs), le bot applique la première ligne de chaque élément : le vaisseau est d'abord remis en mode **Construction** (ressources, jamais « Assemblages »), puis le bot clique sur MAX pour connaître le maximum payable, saisit `min(voulu, payable)` et clique sur « Lancer » (ou « Produire » pour un composant) ; il vérifie ensuite le débit des ressources. Pour « effectif à atteindre », les unités déjà commandées mais pas encore terminées sont comptées. Avant chaque planète, le bot vérifie que la page affiche bien les chiffres de la nouvelle planète. Validé sur le vrai jeu le 07/11 (un >Lentus et un Chasseur lancés par le bot).
- **« Explorations »** (onglet Exploration, une seule configuration) : moyen **Piliers ou Flotte**, composition par **modèle du jeu** ou **quantité par unité**, **copies** (1 à 10) et **durée** (1 à 6 h), planètes toutes / une / plusieurs, rythme **nombre fixe par planète et par passage** ou **autant que possible**. **Une fois par jour** (et avec le bouton « Refaire le relevé du jour »), le bot relève pour chaque planète la **brèche la plus proche** — il recentre la Galaxie sur la planète, dézoome jusqu'à voir une brèche et **lit les positions réelles** que la carte reçoit du serveur (aucune requête ajoutée) ; en secours seulement, il classe les brèches listées par une formule tirée des adresses — ainsi que les **modèles** Piliers et Flotte. À chaque passage, il lit les compteurs **« Explorations quotidiennes »** et **« Explorations actives »** (communs au compte) et ne lance rien si le quota ou les places sont épuisés ; une planète à qui il manque des unités, de l'énergie ou de la Trame est **sautée** et la suivante est servie. Le lancement est confirmé par la hausse du compteur quotidien. Le bot ne touche jamais à « Supprimer » (modèle), au rappel de flotte, à « Mobilisation générale » ni aux boutons de champ de force et de piliers. Validée sur le vrai jeu le 07/11 (1 exploration lancée par le bot depuis Egide).
- **« Fouilles »** (onglet Fouilles) : une **ligne** par planète de départ et par durée — **équipe** choisie unité par unité (Soldat, Rempart de Fer, Spectre du Rift, Surchargeur Plasmique, Faucheur Cinétique, Scientifique, Mineur ; **1 à 5 000 unités**), **durée** 3, 6, 12 ou 24 h, **nombre de places**, et **Mira Venn** sur une seule ligne (partie sans héros si elle est déjà en mission). Le joueur choisit un **délai avant relance** (minimum et maximum en minutes). **Une fois par jour** (et avec « Refaire le relevé du jour »), le bot relève les **emplacements libres les plus proches** de chaque planète de départ (positions réelles de la carte, formule des adresses en secours) et le nombre maximum de Fouilles actives. À chaque passage, il lit les Fouilles dans « Missions en cours » (fin réelle, retards compris, Fouilles lancées à la main adoptées par leur ligne) ; une place se libère à la **fin de sa Fouille plus un délai tiré au hasard** entre le minimum et le maximum, et la Fouille suivante part vers le site libre le plus proche **sans Fouille en cours**. Il respecte « Fouilles actives » et « Missions actives », et confirme chaque lancement par la hausse du compteur. Une Fouille **ne peut pas être rappelée**. **Pas encore lancée par le bot sur le vrai jeu** (lancement manuel observé le 07/11).
- **« Piratage (expérience de Naël) »** (onglet Piratage) : réglages **planète de départ** et **délai avant relance** (minimum et maximum en minutes, choisis par le joueur). But : faire gagner de l'expérience à **Naël Soryn**. Une fois par jour, le bot relève les **Errants les plus proches** de la planète (positions réelles de la carte) en gardant ceux dont le bouton Drone n'est pas grisé (ratio ×2). À chaque passage, il lit « Missions en cours » et l'état de Naël ; dès que le piratage précédent (30 minutes) est fini **et** que le délai tiré au hasard est écoulé, il pirate de nouveau **le même Errant**. Si le jeu refuse cet Errant (piliers désactivés…), le suivant le plus proche devient la cible. Un piratage ne s'annule pas et l'Errant est prévenu ; les rapports ne sont pas lus. **Pas encore lancé par le bot sur le vrai jeu** (lancement manuel observé le 07/11).
- **« Dons de jetons d'alliance »** (onglet Alliance + minuteur « Donner des jetons toutes les X à Y minutes ») : à chaque passage, le bot ouvre Alliance › Techs et donne **tous** ses jetons, **1** ou **2** (au choix) à la technologie marquée « priorité de dons de l'alliance », et à aucune autre ; il ne change jamais la priorité. Le don est confirmé par la baisse du compteur de jetons (un jeton revient environ toutes les 2 h, 6 au plus).
- **Missions** (automatique, sans réglage) : **une fois par jour** (date française), le bot ouvre l'onglet Missions et clique sur chaque « Récupérer » (missions quotidiennes, hebdomadaires et mensuelles atteintes ; les onglets Quêtes et Événements ne sont pas touchés). Cela se fait (A) quand vous mettez le bot sur **OFF** — après le passage en cours — et (B) quand le **planning** termine la **dernière plage de la journée**, juste avant la déconnexion. Le résultat est noté dans le journal.
- **Planning** (onglet Planning) : plages horaires actives pour chaque jour de la semaine (heure française), plusieurs par jour, une plage pouvant passer minuit ; décalage aléatoire de X à Y minutes, avant ou après, au début et à la fin de chaque plage. Le bouton ON/OFF reste le maître : sur **ON** avec « Utiliser le planning », le bot n'agit que pendant une plage ; en dehors, le passage en cours se termine, puis le bot **se déconnecte du jeu** et ne fait rien jusqu'à la plage suivante, où il se reconnecte seul. Un jour sans plage est inactif. Sur OFF, en contrôle manuel ou pendant une campagne d'espionnage (manuelle), le planning ne s'applique pas.
- **Bonus d'activité** (automatique, sans réglage) : au début de chaque passage d'une fonctionnalité, de la campagne d'espionnage et des relevés manuels, le bot regarde si le bouton « Bonus » (en bas à gauche) est affiché — il n'apparaît que lorsqu'un gain est disponible, toutes les 2 à 4 h — et clique dessus, quelle que soit la planète active (qui reçoit les ressources). Le message du jeu (« Bonus récupéré … ») est noté dans le journal.
- **Espionnage des Errants** (onglet Espionnage, **manuel**, hors de l'ordonnanceur) : réglages **planète**, **minimum de Fer, Cristal et Éther**, et **nombre de ressources (1, 2 ou 3)** qui doivent atteindre leur minimum. Le bouton « Lancer l'espionnage » met le bot sur **OFF** (les autres automatismes restent arrêtés jusqu'à ce que vous les relanciez), ouvre la Galaxie centrée sur la planète au **zoom ×17,5**, charge toute la liste « Fenêtre visible » (« Afficher la suite ») et clique sur **Drone** pour chaque planète d'Errant **[AI]** (un Drone grisé = hors de portée, sauté). Un rapport est **gardé** (« Fermer le message ») si assez de ressources atteignent leur minimum, sinon **supprimé** (« Supprimer le message ») ; un rapport illisible est gardé. Les Errants dont les **piliers sont désactivés** vont dans la liste « Piliers fermés », vidée à chaque lancement. Trois refus de suite pour une autre raison arrêtent la campagne. L'écran n'affiche que le **nombre de cibles retenues**. **Pas encore lancée par le bot sur le vrai jeu.**
- La fonctionnalité « Test (ne fait rien) » n'agit pas dans le jeu : elle sert à vérifier minuteurs et priorités, et sera retirée quand la première vraie fonctionnalité arrivera.

## Données du joueur

- Aucun identifiant ni mot de passe dans l'image Docker, `docker-compose.yml`, les variables d'environnement ou un volume.
- Ils sont envoyés par l'interface, utilisés une fois pour remplir le formulaire du jeu, puis oubliés. Seule la session du navigateur (cookies) reste en mémoire, jusqu'à la déconnexion ou l'arrêt du conteneur.
- Le journal ne contient jamais le mot de passe, et l'identifiant y est masqué (`jo***`).

## Lancer

**Avec Portainer (Web editor)** : Stacks > Add stack > Web editor, coller le contenu de `docker-compose.yml`, puis Deploy. Aucune variable à déclarer.

L'image `ghcr.io/kamegrazi56/boleryos:latest` est construite et publiée automatiquement par GitHub Actions (`.github/workflows/docker-publish.yml`) à chaque mise à jour du code. Pour mettre à jour le bot : attendre la fin du workflow dans l'onglet Actions du dépôt, puis redéployer la stack dans Portainer (`pull_policy: always` télécharge la dernière image).

Première publication : le paquet est créé en privé. Sur GitHub, aller dans le profil > Packages > `boleryos` > Package settings > Change visibility > Public, pour que Portainer puisse le télécharger sans identifiants.

**Avec Docker en ligne de commande** :

```bash
docker compose up --build
```

Puis ouvrir `http://<adresse-du-serveur>:7654`. Le port se change dans `docker-compose.yml` (`"7654:8000"` : port du serveur à gauche, port du conteneur à droite).

**Attention** : l'interface reçoit le mot de passe du jeu et n'est accessible qu'en HTTP. Ne l'exposez pas sur internet sans protection (réglages `ui_user` / `ui_password` ci-dessous, et idéalement un reverse proxy HTTPS).

## Réglages

Ils se modifient directement dans `app/config.py` (aucune donnée de joueur) :

| Champ | Rôle | Défaut |
|---|---|---|
| `login_url` | Page contenant le formulaire de connexion (URL de test de l'éditeur possible) | `https://aleryos.com/fr` |
| `ui_user` / `ui_password` | Protège l'interface du bot par mot de passe (HTTP Basic) | désactivé |
| `human_mode` | Comportement humain : pauses courtes et aléatoires entre les actions, souris déplacée progressivement, frappe touche par touche | `True` |
| `human_delay_min` / `human_delay_max` | Pause entre deux actions (secondes) | `0.4` / `1.4` |
| `key_delay_min` / `key_delay_max` | Délai entre deux touches (secondes) | `0.05` / `0.18` |
| `show_cursor` | Point rouge à l'endroit de la souris dans la vue en direct | `True` |
| `screenshot_interval` | Intervalle entre deux images de la vue en direct (secondes) | `0.4` |
| `login_timeout` | Attente maximale du résultat de la connexion, en secondes | `30` |
| `email_selector`, `password_selector`, `submit_selector` | Sélecteurs du formulaire | relevés sur le site |

Les mêmes réglages peuvent aussi être surchargés par variables d'environnement (`DATA_DIR`, `LOGIN_URL`, `UI_USER`, `UI_PASSWORD`, `LOGIN_TIMEOUT`, `EMAIL_SELECTOR`, `PASSWORD_SELECTOR`, `SUBMIT_SELECTOR`), mais ce n'est pas nécessaire.

Ne mettez jamais un mot de passe réel dans `app/config.py` si le dépôt est public.

## Connexion et Cloudflare Turnstile

Le site protège la connexion avec Cloudflare Turnstile. Le bot **ne résout pas** de captcha. Pour tester les actions du jeu, utiliser un environnement de test fourni par l'éditeur (`LOGIN_URL` ou règle d'exception). Si Turnstile bloque, le journal l'indique et la page Écran montre l'écran bloquant (le navigateur reste ouvert après un échec).

La connexion est considérée comme réussie quand le champ mot de passe disparaît ; l'échec est détecté via un élément `role="alert"` ou un délai dépassé. Ces critères sont à affiner en observant la vraie page après connexion.

## Développement

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m playwright install chromium   # ou CHROMIUM_PATH=/chemin/vers/chromium
python -m pytest
```

Les tests utilisent un faux site de connexion local (`tests/mock_site.py`) : aucune tentative n'est envoyée au vrai site.
