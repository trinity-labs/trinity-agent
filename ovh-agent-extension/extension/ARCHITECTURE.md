# Architecture et contribution

Ce guide décrit le code public de l’extension et son protocole avec le backend Python. Les préférences d’installation, consignes privées et politiques de crédit restent hors du VSIX et du dépôt public.

## Parcours d’une demande

```mermaid
sequenceDiagram
    participant U as Chat (webview)
    participant E as Extension host
    participant B as Backend Python
    participant M as Fournisseur du modèle
    U->>E: prompt / images / modèle / accès
    E->>E: validMessage + verrou de fenêtre
    E->>B: ensureAgent puis NDJSON sur stdin
    B->>B: contexte, budget, approbations et vérifications
    B->>M: requête d’inférence
    M-->>B: texte / appels d’outils
    B-->>E: NDJSON sur stdout
    E->>E: cadrage borné puis dispatch
    E-->>U: delta regroupé (50 ms), HTML assaini
    U->>U: DOMPurify puis rendu; état différé (150 ms)
    B-->>E: response complète
    E-->>U: réponse finale, sans delta redondant
    U->>U: coloration et sauvegarde immédiate de l’état
    U->>E: snapshot différé (350 ms), ID de conversation
    E->>B: snapshot si la conversation est toujours courante
```

Les événements d’outils, d’approbation et d’erreur conservent leur ordre. Seul un `delta` en attente est remplacé par la `response` complète. Un arrêt ou une erreur restitue le dernier delta. Les archives du backend sont la référence : l’état HTML du navigateur sert à restaurer l’affichage, pas à prouver une exécution ni à alimenter les autorisations.

## Responsabilités des modules

| Fichier | Responsabilité |
| --- | --- |
| `extension.js` | Activation VS Code, dispatch des messages validés, cycle de vie du processus, orchestration des projets. |
| `security.js` | Schémas du pont webview, liens locaux autorisés, échappement HTML. |
| `backend-protocol.js` | Découpage NDJSON, taille maximale et reprise après une ligne rejetée. Ne lance aucune action. |
| `markdown-renderer.js` | Markdown puis assainissement HTML ; cache exact d’un rendu par provider. |
| `billing-channel.js` | Une requête de solde à la fois, corrélation des réponses, délai et annulation. |
| `webview-html.js` | Document initial, CSP, sélection des ressources et clés de cache navigateur. |
| `archive-store.js` | Lecture/listage/suppression des conversations et cache de métadonnées. |
| `project-store.js` | Registre des projets, classement, lectures bornées et protection des chemins de stockage. |
| `window-lock.js` | Propriétaire unique du backend par stockage et compte système. |
| `media/chat.js` | État d’interface, événements utilisateur, rendu, activités, pièces jointes et sauvegardes d’affichage. |
| `media/safe-html.js` | Frontière DOMPurify pour tout HTML reçu ou restauré. |
| `media/i18n.js` | Traduction des libellés applicatifs, sans réécriture du code ou des messages utilisateur. |
| `../../ovh-agent.py` | Inférence, outils, persistance des événements et contrôles d’exécution. |

`chat.js` reste le contrôleur partagé de l’interface : éviter de déplacer ses variables globales dans plusieurs scripts interdépendants sans interface explicite et tests de cycle de vie. Les modules de transport et de rendu sont isolés parce qu’ils ont des entrées/sorties testables.

## Appels supprimés, regroupés ou conservés

| Chemin | Politique et justification | Invalidation / limite |
| --- | --- | --- |
| Solde avec backend actif | Utiliser son thread de facturation ; ne pas relancer Python, recharger les secrets ou forcer l’API à chaque poll. | Cache fournisseur géré par le backend ; clic utilisateur = `refresh: true`. |
| Solde sans backend | Conserver le processus ponctuel `--billing-status` : consulter le solde ne démarre pas une conversation. | Pas de cache de crédits supplémentaire dans l’extension. |
| Réponse finale identique | Réutiliser le dernier rendu et le DOM du run ; pas de second parse/sanitize complet inutile. | Comparaison exacte du texte/HTML ; nouveau contenu = nouveau rendu. |
| Cache Markdown | Une entrée en RAM par provider. Aucun résultat de modèle n’est réutilisé comme réponse. | Source ≤ 128 Ki caractères, HTML ≤ 512 Ki caractères ; remplacement, dépassement ou fermeture effacent l’entrée. |
| Deltas | Regrouper le rendu sur 50 ms, sans différer les événements terminaux. | Le texte est accumulé ; une réponse finale complète remplace le delta en attente. |
| État webview | Ne pas sérialiser tout l’historique pour chaque delta ; délai de 150 ms. | Événement non-stream, navigation ou fin : annulation du délai et sauvegarde immédiate. Un crash pendant ce délai peut perdre le dernier affichage, pas les événements backend déjà archivés. |
| Snapshot backend | Débounce de 350 ms conservé ; vérifier l’ID avant envoi. | Un changement de conversation empêche la sauvegarde dans l’ancienne archive. |
| Historique | Regrouper les notifications sur 75 ms ; lire l’état au moment du scan. | Pas de TTL sur la liste des dossiers ; une action explicite peut lancer un scan immédiat. |
| Métadonnées d’archive | Cache borné existant, invalidé par identité/taille/horodatage du fichier. | Les protections de chemin restent vérifiées ; aucune autorisation en cache. |
| Explorateur VS Code | Rafraîchir après modification de fichier ou commande potentiellement mutante. | Une réponse de lecture seule ne lance plus ce rafraîchissement ; erreur/idle traités aussi après mutation possible. |
| Ressources navigateur | URL stable fondée sur les fichiers, nonce CSP distinct à chaque document. | Une modification de ressource change sa révision. |

Les clés, décisions d’approbation, vérifications de chemins, résultats d’outils mutables et réponses d’inférence ne sont pas mis en cache par ces changements. Une amélioration du rendu n’accélère pas nécessairement le fournisseur du modèle et ne réduit pas ses tokens.

## Ouverture des conversations

Le clic ouvre immédiatement la vue avec le titre et un voile de chargement teinté de la couleur principale. Le voile utilise les couleurs VS Code, un indicateur accessible et respecte la préférence de mouvement réduit. Il ne conserve pas le texte d’une autre conversation.

Deux aperçus HTML au maximum (256 K caractères chacun) sont conservés dans la mémoire de la webview, avec une clé projet/conversation/date de modification. Ils sont assainis à la restitution et restent couverts par le loader jusqu’au chargement autoritatif. Un aperçu ne restaure ni permissions ni contexte du modèle. Les réponses portant un autre identifiant sont ignorées pendant l’ouverture ; un échec retire le loader et réaffiche les archives.

Un snapshot est utilisable uniquement si sa révision (taille et mtime_ns du journal) correspond aux événements. Les anciens snapshots sans révision sont reconstruits. La webview renvoie la révision effectivement rendue ; une sauvegarde obsolète est refusée, même si sa date est récente. Cette révision sert à la cohérence du cache, pas à une décision de sécurité. Le modèle et le choix Auto sont restaurés depuis les événements ; une réponse tardive du catalogue ne les écrase pas.

Lorsqu’un snapshot valide existe, le backend l’envoie avant de désérialiser l’historique du modèle. Sa boucle traite les prompts suivants seulement après cette désérialisation. Sans snapshot, le rendu initial reste reconstruit depuis les événements ; le squelette est visible pendant cette opération.

## Protocole de facturation

Le frontend transmet `billingRefresh` pour le polling et `billing` pour l’action explicite. Avec un processus actif, l’extension envoie `billing_status` avec un `request_id` unique, `refresh` et `silent`. Le backend renvoie le même identifiant. Une réponse tardive ne peut pas terminer une nouvelle requête. Une fermeture du processus annule l’attente ; le délai maximum est de 30 secondes. Déployer le backend de la même release : un ancien backend qui ne renvoie pas l’identifiant n’est pas compatible avec ce chemin corrélé.

## Développement et minification

```sh
npm ci
npm test
npm run build:frontend
npm run package:vsix
```

Les sources `media/chat.js`, `media/i18n.js` et `media/safe-html.js` restent lisibles et constituent la référence. Terser produit les `.min.js` et leurs source maps, sans renommer les API de premier niveau partagées. `npm run build:frontend` sert à tester localement les assets générés ; le packaging les reconstruit toujours dans son répertoire temporaire, pour éviter d’embarquer un ancien build. Dans un checkout sans assets générés, la webview charge les sources. Après une édition locale, reconstruire les assets ou supprimer les trois `.min.js` générés pour revenir au mode source.

Le VSIX contient aussi les sources et les maps pour le diagnostic. Le gain porte sur le JavaScript chargé par la webview ; la taille ZIP totale n’est pas nécessairement réduite. Les bibliothèques déjà minifiées ne sont pas retraitées. Aucun CDN n’est ajouté et la CSP n’est pas élargie.

## Vérifier une contribution

- Expliquer dans les commentaires **pourquoi** un cache, un délai ou un contournement est nécessaire ; décrire son invalidation et sa limite.
- Ajouter un test de comportement pour toute modification du protocole, de la sécurité ou de l’ordre des événements. Les tests de performance comptent les opérations évitées plutôt que d’imposer une durée fragile en CI.
- Exécuter `npm test` ; les tests couvrent aussi le frontend minifié et les source maps. Pour un changement backend, exécuter `python scripts/check-backend.py` depuis la racine du dépôt avec l’environnement Python du projet.
- Ajouter tout nouveau module à la liste blanche de `scripts/package-vsix.cjs` et à celle d’export Git. Vérifier le contenu du VSIX après génération.
- Ne pas publier les consignes, archives ou secrets locaux. Garder le même numéro de version dans `package.json`, `package-lock.json` et le tag de release.

### Délai de compression du contexte

Le résumé auxiliaire utilise le modèle actif, sans outils, avec un délai par défaut de 15 secondes (`OVH_AGENT_SUMMARY_TIMEOUT_SECONDS`). Le délai couvre la réponse complète. En cas d’échec, le backend émet un statut explicite puis utilise les extraits locaux ; il annonce ensuite la reprise. Le protocole complet est archivé avant compression. Le délai et le type d’erreur sont enregistrés, sans attribuer automatiquement la lenteur au fournisseur. Annuler l’appel local ne garantit pas l’annulation de sa facturation distante. `scripts/test-context.py` vérifie hors réseau le timeout réel, l’annulation de la coroutine, le secours et la conservation exacte de l’archive.

### Preuves et contexte des modifications

Pour une demande classée `edit`, le préchargement recherche le code actuel. Les réponses assistant historiques, leurs blocs de raisonnement et les résumés antérieurs au format v2 sont retirés du contexte actif ; le protocole exact reste dans `context-checkpoints/`. Les demandes utilisateur et les paires appel/résultat d’outil sont conservées. La recherche automatique d’extraits historiques est désactivée pour ces modifications ; les outils de mémoire restent disponibles explicitement. Ce choix évite de réinjecter comme preuve une réponse erronée du modèle précédent. Les résumés v2 attribuent les déclarations et refusent les blocs de code/pseudo-appels ; ce filtre ne certifie pas leur exactitude sémantique.

Le garde de sortie refuse une annonce de tests réussis sans reçu d’exécution, même si `verify_changes` indique `not_required`. Une demande de modification sans écriture suivie doit annoncer explicitement « Aucune modification effectuée » et expliquer le blocage ou l’état déjà conforme. Ce garde ne prouve pas à lui seul que l’explication est exacte. Les tests de régression reproduisent les demandes de catalogue, les réponses contaminées entre modèles, les faux bilans et la conservation des archives.

### Visual reader and text executor

When the selected executor cannot read images, `ovh_vision.py` performs one
tool-free, budgeted visual extraction (60 seconds maximum, 4096 output tokens).
The default reader is Qwen3.6-27B; `OVH_AGENT_VISION_READER` is an explicit operator
override. It must advertise vision and native JSON schema in the effective provider
capabilities. There is no silent fallback to a reader with weaker test results.
Native structured output constrains format; it does not certify visual accuracy.
The reader gets only the current task and images, without conversation history
or workspace tools. Auto selects the executor independently of image capability;
a native vision executor still receives images directly. The header identifies
the executor; the status and a separate `usage` event (`purpose: vision`) identify
the reader and its cost. `vision_reading` archives the extracted observations.

The executor receives the original instruction plus explicitly untrusted visual
observations, including uncertainty. Invalid, incomplete, unreadable or truncated
extraction stops the task before execution. This validates the handoff format,
not the truth of every observation. Historical binary attachments are not replayed
to text-only models; original images remain in the conversation archive. There is
no cross-conversation image cache. Local budgets and model policy remain separate
from this public application behavior. Regression tests: `scripts/test-vision.py`.

Direct image generation/retouch requests are excluded from this path. The current
extension has no configured image-generation tool: it reports that limitation
without calling the visual reader or pretending to generate pixels. This is
separate from creating container images or implementing UI code from a screenshot.

### Interrupted view restoration

Closing the view stops its backend. Unfinished tasks therefore restore as
interrupted, with an explicit Resume action; reopening does not execute tools
automatically. Interrupted archives bypass cached HTML and reconstruct recorded
model/tool activity. Tool detail previews are bounded to 4000 characters.
`ResponseStream` saves a separate, bounded `response-draft.json` at most once per
second (also at stream completion), since stdin snapshot commands wait behind
inference. A new attempt or successful completion clears that draft. Recovered
text remains visibly interrupted, never a verified final response. Abrupt process
termination may lose the last second of streamed text; tool journals remain the
source for recorded actions.

Selecting a history folder or choosing a project records the explicit destination
for the next new conversation (`ovhAgent.newConversationProject`). It does not
move the current conversation. New Conversation starts in that folder and records
the history assignment when the backend acknowledges `cleared`, after creating
the archive directory. Without an explicit selection, it uses the IDE workspace.
