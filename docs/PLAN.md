# Plan de développement de BoLeryos

*Dernière mise à jour : 2026-11-01 (phases 1 à 5 codées ; constructions : liste ordonnée, une ou toutes les planètes, timer d'attente ; D1 et F5 en attente). Ce document fait foi : on le met à jour à chaque phase, et on ne repose pas une question dont la réponse y figure.*

## 1. Objectif

Un bot qui tourne dans Docker (déployé avec Portainer) et se pilote depuis une interface web :
- **page Connexion** : le joueur saisit son identifiant et son mot de passe, voit le site en direct ;
- **page Vision** : l'écran du navigateur du bot en direct, le journal de tout ce qu'il fait, et la possibilité de jouer à la main ;
- **page Actions** : le joueur choisit les actions à automatiser (première cible : **l'amélioration des mines**).

Cadre : test de protections anti-bot **avec l'éditeur** (GEEKOS NEXUS), sur un compte réel. Le règlement du jeu (§12) interdit les bots : l'accord de l'éditeur doit être **conservé par écrit**, et la période du test et l'adresse IP du serveur lui sont communiquées.

## 1 bis. Fonctionnalités demandées par le joueur

| # | Fonctionnalité | Phase | État des connaissances |
|---|---|---|---|
| F1 | **Période horaire d'activité** : le bot n'agit que dans une ou plusieurs plages de la journée. | 2 | Aucune capture nécessaire. |
| F2 | **Connexion automatique** : identifiant, mot de passe et univers enregistrés dans le bot. | 6 | **Fait en mémoire** : interrupteur ON/OFF, connexion et choix de l'univers automatiques, reconnexion si la session expire. **Reste D1** : conserver les identifiants après un redémarrage du conteneur. L'univers est mémorisé (non secret). |
| F3 | **Constructions** selon un **niveau demandé** par le joueur, sur **toutes les planètes**, si les ressources de la planète suffisent. | 3-4 | Pages Industrie et Installations capturées (cartes avec « Améliorer »). |
| F4 | **Recherches** lancées si les ressources suffisent. | 5 | Page capturée : boutons « Rechercher » / « Ressources insuffisantes ». |
| F5 | **Expéditions** en mode spatial ou terrestre, avec des **modèles** et une **durée** choisis par le joueur. | 8 | Seule la page vide d'Opérations est capturée. Captures à faire (voir phase 8). |
| F6 | *(la liste reçue se terminait par une puce vide : à compléter)* | — | — |
| F7 | **Production en nombre** : Population, Chantier spatial (vaisseaux, composants), Appareils spécialisés, Défenses — nombre choisi ou bouton MAX. | 11 | **Codé** d'après l'export du 2026-11-03 ; à vérifier sur le vrai jeu (lecture seule d'abord). Hôpital (Soigner) et « Iris des Piliers » : non faits. |

## 2. Décisions prises (ne plus en discuter)

| Sujet | Décision |
|---|---|
| Technologies | Python 3.13, FastAPI, Playwright (Chromium). Interface en HTML/JS simple, sans framework. |
| Déploiement | Image `ghcr.io/kamegrazi56/boleryos:latest` construite par GitHub Actions ; stack Portainer en *Web editor* (`image:`), port `7654:8000`, aucune variable à déclarer. Réglages techniques dans `app/config.py`. |
| Données du joueur | Saisies dans l'interface, **en mémoire seulement**. Jamais dans l'image, le compose, les variables d'environnement, un volume, le journal ou git. |
| Moteur | **Navigateur piloté** (clics sur l'interface du jeu). Le moteur API/WebSocket direct vient plus tard, derrière la même interface interne. |
| Interface en direct | Flux d'images poussé par Chrome (CDP), envoyé en binaire. Pleine page. « Contrôle manuel » désactivé par défaut. |
| Comportement humain | Pauses aléatoires courtes entre les actions, souris progressive, frappe touche par touche. |
| Hors périmètre | **Aucune résolution de captcha, aucun contournement ni dissimulation du bot** (empreinte, proxys, faux profil d'appareil). Si Turnstile bloque, c'est l'humain qui se connecte. |
| Actions qui dépensent | **Mode « simulation » (à blanc) par défaut** : le bot décide et journalise, sans cliquer. Le mode réel s'active explicitement, avec un plafond. |
| Connaissance du jeu | On **capture avant de coder** (export de structure, relu par le joueur). On ne devine jamais un sélecteur ni un appel. |
| Livraison | Branche `claude/bot-login-setup`. Un commit n'est fait que si tous les tests passent. Fusion sur `main` uniquement sur demande. |

## 3. Règles de travail (pour limiter les questions)

**Je décide seul :** noms, structure du code, détails d'interface, tests, refactorisations, messages du journal.

**Je ne demande que si :** (a) une action dépense des ressources réelles ou est irréversible ; (b) il faut changer une décision du tableau ci-dessus ; (c) il manque une information du jeu qu'une capture peut fournir ; (d) un point de sécurité est en jeu.

**À chaque phase :** livrable décrit ci-dessous, tests verts, README à jour, commit et push, image reconstruite, puis un message court avec **ce qui est vérifié** et **ce qui ne l'est pas**.

## 4. Ce qu'on sait déjà du jeu (export du 2026-11-01 et wiki public)

- **Cartes de bâtiments** (pages `buildings` = Industrie, `facilities` = Installations) : nom, niveau (pastille), coûts, durée, bouton « Améliorer » (attribut `disabled` si le coût n'est pas couvert).
- **Recherches** (page `tech`) : même principe, avec un bouton « Rechercher » ou le libellé « Ressources insuffisantes ».
- **Nombres** : suffixes `k`, `M`, `G` dans les cartes, `Md` (milliard) dans la liste des planètes ; décimale en point (`201.765M`) ou en virgule (`58,035 M`) ; durées `1j 2h`, `12h 22m`, `5m 25s`, `5.00s`.
- **13 planètes** aux stocks très différents. Le sélecteur de planètes liste, pour chacune, le nom, les coordonnées et les stocks de Fer, Cristal et Éther.
- **État du jeu** dans les réponses `POST /join` et `POST /resolve` (niveaux de bâtiments, ressources par planète).
- **Actions** par WebSocket authentifié (`ACTION` : `enqueue_research`, `set_active_planet`…). Aucune action « améliorer un bâtiment » ni « lancer une expédition » capturée.
- **Expéditions** (wiki) : deux modes. *Spatial* : Opérations → Flotte → Exploration, vaisseaux d'après des **modèles**, consomme de l'Éther, retour par une mission. *Terrestre* : Opérations → Piliers → Exploration, consomme des Résonateurs de Trame, les survivants reviennent directement. Durée nominale de **1 à 6 h**. Destination : une **Brèche** repérée dans Galaxie. Il existe des quotas d'exploration. **Risques réels** : rencontre hostile 11 % (vrai combat, pertes possibles), **crash total 1,5 %**, retard de retour 7 %.
- **Règles du wiki** : 1 construction à la fois sans bonus (non vérifié si c'est par joueur ou par planète) ; énergie insuffisante = mines ralenties ; au-delà de la capacité, la production s'arrête.

## 5. Feuille de route

| Phase | Contenu | Critères d'acceptation | Ce qu'il me faut de vous |
|---|---|---|---|
| **0. Socle** *(fait)* | Connexion, vue en direct, contrôle manuel, choix d'univers, export de structure, déploiement Portainer. | Tests verts, image publiée. | — |
| **1. Lire l'état du jeu** *(codé, à valider sur votre compte)* (lecture seule) | Lire bâtiments (Industrie, Installations), recherches, ressources, énergie et **liste des planètes** ; comprendre `k/M/G/Md` ; tableau « État » dans l'interface. | Tests sur pages reconstituées d'après l'export ; le tableau correspond à l'écran du jeu. | Vérifier le tableau sur votre compte (une fois). |
| **2. Planificateur** *(F1, codé)* | Cadre des actions automatiques : intervalle aléatoire, arrêt propre, **plages horaires d'activité**, verrou avec le contrôle manuel, journal, page Actions, export/import des réglages JSON. Action factice. | Le bot n'agit pas hors plage ; réglages rechargeables. | — |
| **3. Constructions, à blanc** *(F3, codé)* | Tableau « niveau demandé » par bâtiment (valeur globale + exceptions par planète). Boucle sur les 13 planètes. Décisions journalisées, aucun clic. | Décisions cohérentes avec l'état affiché, sur plusieurs cycles et plusieurs planètes. | Relire le journal à blanc. |
| **4. Constructions, réel** *(F3, codé, désactivé par défaut)* | Clic « Améliorer » avec délais humains, vérifications avant/après (niveau suivant), plafond par jour, arrêt d'urgence. | Une amélioration réelle réussie, confirmée par le niveau. | **Votre accord explicite.** |
| **5. Recherches** *(F4, codé)* | Même mécanique (à blanc puis réel) avec « Rechercher » ; niveau demandé par technologie ; choix de la planète qui finance. | Une recherche réelle réussie. | Accord explicite pour le réel. |
| **6. Protection de l'interface + connexion automatique** *(F2)* | Mot de passe de l'interface défini à la première utilisation ; identifiants et univers enregistrés (voir D1) ; reconnexion automatique ; si un contrôle anti-bot apparaît, le bot l'indique et attend l'humain. | Redémarrage du conteneur : le bot se reconnecte et choisit l'univers sans intervention (si aucun contrôle anti-bot). | Choix du mode de stockage (D1) et d'un volume `/data`. |
| **7. Files de construction** | Lire les constructions en cours et leur fin ; capture d'une amélioration de **bâtiment** (appel WebSocket). | Jamais deux fois la même amélioration. | Une amélioration lancée à la main pendant l'enregistrement. |
| **8. Expéditions** *(F5)* | Captures → à blanc → réel. Mode spatial/terrestre, modèles et quantités, durée 1-6 h, choix de la Brèche, plafonds. | Une expédition réelle lancée et retrouvée au retour. | **Captures** : Galaxie avec une Brèche sélectionnée ; Opérations → Flotte → Exploration (composition, durée, bouton Lancer) ; idem Piliers → Exploration ; une petite expédition de 1 h lancée à la main pendant l'enregistrement ; Opérations avec une mission en cours ; Journal → Explorations ; les modèles de vaisseaux. **Votre accord explicite** (pertes possibles). |
| **9. Moteur API** *(optionnel)* | Client WebSocket authentifié ; comparaison avec le moteur par clics. Les coûts et durées envoyés par le client ne sont **jamais** modifiés. | Mêmes décisions, même résultat. | Décision d'y aller après la phase 7. |
| **10. Robustesse** | Session expirée, erreurs, HTTPS, notifications (optionnel), navigateur arrêté quand inutile. | 24 h sans intervention en mode à blanc. | — |
| **Transversal : rapport pour l'éditeur** | Constats utiles à la protection (§8). | `docs/RAPPORT-EDITEUR.md`. | Relecture avant envoi. |

## 6. Décisions ouvertes et valeurs par défaut (à valider une fois)

**Deux choix à faire explicitement :**

| # | Sujet | Options | Défaut proposé |
|---|---|---|---|
| **D1** | **Enregistrer l'identifiant et le mot de passe** (F2). Cela change la règle « mémoire seulement ». | **A.** Fichier chiffré dans un volume, clé saisie dans l'interface à chaque démarrage : reconnexion automatique tant que le conteneur tourne, mais pas après un redémarrage sans vous. **B.** Fichier chiffré dans un volume, clé conservée à côté : 100 % automatique, protection faible (quiconque accède au serveur peut tout lire). **C.** Mémoire seulement (actuel). | **B**, avec un **compte de test dédié**, et **refus d'enregistrer tant que l'interface n'a pas de mot de passe**. |
| **D3** | **« Constructions de tout type »** (F3). | **a.** Bâtiments à niveaux (pages Industrie et Installations). **b.** Aussi les productions en quantité : vaisseaux, défenses, appareils spécialisés, unités. | **a** seulement. La production en quantité viendrait plus tard, sur demande. |

**Valeurs par défaut (valables si vous ne dites rien) :**

| # | Sujet | Défaut |
|---|---|---|
| D2 | Plages horaires (F1) | Plusieurs plages par jour, un seul fuseau réglable (défaut : Europe/Paris), tous les jours. Le bot termine l'action en cours à la fin d'une plage, puis s'arrête. |
| D4 | Conflit de ressources entre fonctions | Ordre de priorité réglable ; défaut : recherches, puis constructions, puis expéditions. Réserve d'Éther pour les expéditions réglable (défaut 0). |
| D5 | Recherches (F4) | Niveau demandé par technologie (vide = pas de recherche). Planète qui finance : celle où « Rechercher » est actif avec le plus de ressources, ou une planète désignée. |
| D6 | Expéditions (F5) | Modèles et quantités choisis par le joueur (« tout », un nombre ou un pourcentage de ce qui est disponible) ; durée 1 à 6 h ; nombre maximal de missions simultanées réglable ; **pause automatique de la fonction après un crash ou des pertes** ; à blanc au départ. Règle de choix de la Brèche : à définir après les captures. |
| D7 | Constructions : liste | **Liste ordonnée** d'améliorations (bâtiment + niveau demandé), choisies dans la liste intégrée ; portée : toutes les planètes ou une seule. |
| D8 | Priorité entre bâtiments | **L'ordre choisi par le joueur** : sur chaque planète, la première amélioration de la liste réalisable avec les ressources est lancée. (Les recherches gardent la règle « le moins pesant sur le stock ».) |
| D13 | Timer des constructions en attente | Une planète où rien n'est réalisable est revérifiée toutes les X minutes (défaut 15, ± quelques %), sans repasser sur les autres ; une planète occupée est revue à la fin de sa construction ; objectifs atteints : revérifiée toutes les 6 h ; réglages modifiés : tout est revérifié. Avant chaque revérification de la planète déjà affichée, la page est rechargée pour lire des ressources à jour. |
| D9 | Énergie | Si le bilan est négatif : pas d'amélioration de mine ; Centrale Énergétique d'abord si abordable, sinon journal. |
| D10 | Stockage | Avertissement dans le journal à 95 % de la capacité ; pas d'amélioration automatique des hangars. |
| D11 | Rythme | Un cycle toutes les 3 à 8 minutes (aléatoire), puis délais humains entre les clics. |
| D12 | Maximum par jour et mode | 20 améliorations réelles par jour au maximum, arrêt d'urgence toujours visible. Simulation au départ, réel seulement après votre accord (jamais restauré, jamais activé par import). |
| D17 | Connexion automatique | Comme TBot : identifiants + univers saisis dans Réglages › Compte et univers, le bot se connecte et entre dans l'univers tout seul, puis garde la connexion, **sans interrupteur** ; ON/OFF ne gouverne que le travail. Les plages horaires ne limitent pas la connexion. Refus d'identifiants : une seule tentative ; contrôle anti-bot : l'humain se connecte, le bot reprend seul. Identifiants toujours en mémoire seulement (D1 inchangée). |
| D18 | Production en nombre | Section + élément (listes intégrées ; modèles de vaisseaux lus dans le jeu) + quantité ou « maximum » + comportement **commande unique** (une fois par planète, puis « faite », « Relancer »), **objectif de stock** (stock + file d'attente, on commande la différence ; pas de maximum) ou **répéter à chaque passage**. Portée : toutes les planètes ou une seule. Tout ou rien : on ne lance que si les ressources couvrent la quantité. Vaisseaux : bouton « Construction ». Chaque lancement compte dans le maximum par jour. Un stock ou un coût illisible : rien n'est lancé. |
| D19 | Priorités | Réglages › Priorités : ordre des 6 blocs (constructions, recherches, population, chantier spatial, appareils, défenses) ; le premier est servi avant les suivants au sein d'un même passage. |
| D14 | Interface en 4 pages | **Accueil** (ON/OFF, phrase d'état, « Prêt à démarrer ? », mode, résumé de la simulation, événements), **Écran** (vue en direct, contrôle manuel, exploration), **Réglages** (5 onglets, « Avancé » replié, enregistrement automatique), **Suivi** (journal lisible, état du jeu, planètes suivies). Bandeau de mode permanent. Vocabulaire simple : « Simulation », « fréquence de vérification », « maximum de lancements par jour ». |
| D15 | Niveaux relatifs | Une ligne de la liste peut viser « jusqu'au niveau N » ou « monter de N niveaux » : niveau de départ lu la première fois que le bot voit le bâtiment sur la planète, mémorisé (`/data/automation-state.json`), objectif = départ + N (il ne glisse pas). Listes toutes faites = regroupements de départ, modifiables. |
| D16 | Alertes | Webhook https choisi par le joueur (Discord/Slack/Mattermost : JSON ; ntfy : texte) ; événements « amélioration lancée » et « le bot a besoin de moi » ; adresse stockée à part (`/data/notifications.json`, 0600), jamais exportée ni affichée ; adresses locales refusées. Notifications du navigateur en option. Telegram : non fait. |

## 7. Qualité et sécurité

- Tests : `pytest`, faux site local, **pages reconstituées d'après vos exports** (pseudo et valeurs remplacés). Aucune tentative de connexion n'est envoyée au vrai site par les tests.
- Les sélecteurs reposent sur les **textes et rôles** (« Améliorer », nom du bâtiment), pas sur les classes CSS (ce sont des classes utilitaires susceptibles de changer).
- Le journal ne contient jamais de mot de passe, de jeton ni de frappe clavier. L'export de structure masque valeurs, jetons, cookies, e-mails et identifiants ; il n'est jamais commité (`.gitignore`).
- L'interface reçoit le mot de passe du jeu : elle doit être protégée avant toute exposition hors de votre réseau (phase 8).

## 8. Constats pour l'éditeur (liste vivante)

1. L'action `enqueue_research` envoie depuis le client le **coût, l'heure de début, l'heure de fin et la durée** : le serveur doit les recalculer et ne pas s'y fier (non vérifié côté serveur, et non testé).
2. Le jeton d'authentification est conservé dans le stockage local du navigateur (`aleryos-central-auth-token`) et réutilisé par l'API et le WebSocket.
3. Les actions de jeu passent par un WebSocket qui reçoit ce jeton ; l'API REST attend en plus un en-tête de version de contrat client (`x-aleryos-client-contract`).

## 8 bis. Vérifié et non vérifié (phases 1 à 5)

- **Vérifié** (tests sur un faux jeu reconstitué d'après l'export) : lecture des cartes, ressources, énergie et planètes ; nombres `k/M/G/Md` ; plages horaires (dont plages de nuit) ; décisions ; mode à blanc sans aucun clic ; lancements réels confirmés par le débit des ressources ; plafond quotidien ; arrêts de sécurité ; réglages (mode réel jamais restauré, jamais d'identifiants) ; page Actions.
- **Non vérifié sur le vrai jeu** : changement de planète (clic dans la fenêtre « Planètes »), navigation vers la page Industrie (aucun bouton connu : navigation interne puis rechargement en secours), apparition éventuelle d'une fenêtre de confirmation après « Améliorer », file de construction (une par planète ou par joueur), rafraîchissement du niveau après lancement.
- **Procédure de validation :** « Lire l'état du jeu » → comparer avec l'écran → laisser tourner **à blanc** → relire le journal → seulement ensuite le mode réel, avec un plafond bas (par exemple 1 à 3 par jour) pour le premier essai.

## 9. Risques et inconnues

| Risque | Parade |
|---|---|
| Mise à jour du jeu qui change la page | Sélecteurs par texte/rôle, tests sur pages reconstituées, message clair dans le journal quand un élément manque. |
| Suffixes de nombres autres que K/M/G | Le bot refuse d'agir (et le dit) s'il ne comprend pas un nombre. |
| Pertes réelles en expédition (hostile 11 %, crash 1,5 %) | À blanc d'abord, plafonds, pause automatique après perte, accord explicite avant le réel. |
| File de construction non documentée | Phase 6 avant toute automatisation intensive ; en attendant, 1 amélioration à la fois. |
| Turnstile ou détection du bot | L'humain se connecte ; pas de contournement. Le résultat sert au rapport. |
| Mémoire (un Chromium en permanence) | Navigateur arrêté quand aucun écran n'est ouvert et qu'aucune action n'est active (phase 8). |
