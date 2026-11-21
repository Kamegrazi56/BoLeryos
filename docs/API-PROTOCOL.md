# Protocole du jeu relevé pour le moteur « api »

Source : enregistrement de structure du bot (page Écran › « Exploration de la structure du site »), le 2026-11-20 sur Asterion, plus la capture du 2026-11-15. **Formes uniquement** : aucune valeur, aucun jeton, aucun identifiant. Ne pas y ajouter de données du joueur.

## Authentification
- Supabase sur `auth.aleryos.com` : `GET /auth/v1/user`, appels RPC `POST /rest/v1/rpc/<nom>` (`get_user_profile`, `list_open_universes`, `get_my_universes`). En-têtes : `apikey`, `authorization` (jeton d'accès, environ 1 h, jeton de renouvellement qui tourne).
- La connexion elle-même est protégée par Cloudflare Turnstile : **un humain se connecte** (règle du projet), puis le moteur API reprend les jetons de la session du navigateur.

## API de l'univers : `https://<slug>-api.aleryos.com`
- En-tête `x-aleryos-client-contract` sur tous les appels, plus `authorization`.
- `POST /join` (corps `{}`) → `{universeId, timestamp, state}` : **état complet** du joueur.
- `POST /resolve` (`{stateDelivery: "push_and_return", resolveIntent: "production_expiry"}`) → même forme, état à jour après expiration des productions.
- `GET /spatial-missions` → `{success, revision, generatedAt, missions[]}` (missions en cours : vecteur, origine, cible, vaisseaux, unités, ressources, heures de départ et d'arrivée, type, avancement du forage…).
- Autres lectures vues : `/public/universe-stats`, `/universe/truce`, `/votes/platforms`, `/guidance/progress`, `/premium/steam-launch`, `/elyria/popups/due`.
- Présence : `POST /social/player-presence`, `POST /push-notifications/presence`.

### Forme de `state.planets[]` (13 planètes sur le compte d'essai)
`id, name, type, biome, level, ships{<modèle>: n}, slots, defenses{…9 types}, settings{productionRatio}, buildings{chantierSpatial, mineFer, caserne, hangarFer, centreRecherche, usineRobot, institutFormation, hangarCristal, matriceCoalescence, extracteurCristal, terraformeur, cuveEther, resonanceTower, centraleEnergetique, synthetiseurEther}, resources{fer, cristal, ether, astrium, energy}, components{…}`, files de production.

## WebSocket : `wss://<slug>-api.aleryos.com/ws`
Envoyés : `{type:"AUTH_INIT", token, clientContractVersion}` puis `{type:"ACTION", action, requestId, payload}`.
Reçus : `AUTH_OK {playerId, universeId, topology{universeId, topologyId, manifestHash, geometryVersion}}`, `STATE_UPDATE {universeId, timestamp, state}`.

Actions vues : lectures (`get_announcements`, `get_tutorial_progress`, `get_player_channels`, `get_chat_unread_counts`, `get_player_pacts`, `get_pact_requests`, `get_player_trades`, `get_unread_count`, `federation_echo_shop_catalog`) ; **écritures** : `record_planet_activity {planetId}`, `enqueue_factory_unit {planetId, unitId, count, cost{fer,cristal,ether}, timeStart, timePerUnit, timeEnd, queueOrder, building}`, et (capture du 2026-11-15) `enqueue_building`, `enqueue_shipyard`, `enqueue_factory_component`, `preflight_mission_target` / `preview_mission` / `send_mission`, `donate_alliance_tokens`.

## Points d'attention
- Les **coûts et les durées sont calculés par le client** (`cost`, `timeStart`, `timePerUnit`, `timeEnd` dans `enqueue_factory_unit`) : le moteur API doit les recalculer. Reste à établir si le serveur les revalide.
- Non capturé : recherche, missions quotidiennes, mode Assemblages, usage d'objet.
- Le contrat client (`x-aleryos-client-contract`, `clientContractVersion`) peut changer à chaque mise à jour du jeu.

## Constat du 2026-11-21 : actions par WebSocket refusées hors navigateur
- `POST /join` (lecture de l'état) est accepté avec le jeton repris de la session du navigateur.
- La connexion WebSocket `/ws` d'un client qui n'est pas le navigateur du jeu est **refusée** : HTTP 403, `server: cloudflare`, corps `{"success":false,"errorPayload":{"code":"request_forbidden"}}`. La page du jeu s'y connecte sans difficulté.
- Décision du propriétaire (2026-11-21) : on ne contourne pas ce refus (ni faux en-têtes de navigateur, ni cookies réutilisés pour tromper la protection). Les actions restent dans Chromium ; le moteur « hybride » lit par l'API et agit par le navigateur.
- Pour le rapport de test : la lecture hors navigateur est possible, l'écriture hors navigateur est bloquée.
- Forme observée d'un lancement de vaisseau (envoyée par la page) : `enqueue_shipyard {planetId, modelId, count, method:"direct", itemType:"ship", cost{fer,cristal,ether}, timeStart, timePerUnit, timeEnd, queueOrder, building}` ; le coût de base d'un modèle est `shipModels[].stats.cost`, son temps `stats.baseTime`, et le client envoie un coût inférieur d'environ 10 % (réduction du joueur non expliquée).
