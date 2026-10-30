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
| `../../trinity_agent/agent.py` | Inférence, outils, persistance des événements et contrôles d’exécution. |

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
npm run dependencies:latest
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

Le résumé auxiliaire utilise le modèle actif, sans outils, avec un délai par défaut de 15 secondes (`TRINITY_AGENT_SUMMARY_TIMEOUT_SECONDS`). Le délai couvre la réponse complète. En cas d’échec, le backend émet un statut explicite puis utilise les extraits locaux ; il annonce ensuite la reprise. Le protocole complet est archivé avant compression. Le délai et le type d’erreur sont enregistrés, sans attribuer automatiquement la lenteur au fournisseur. Annuler l’appel local ne garantit pas l’annulation de sa facturation distante. `scripts/test-context.py` vérifie hors réseau le timeout réel, l’annulation de la coroutine, le secours et la conservation exacte de l’archive.

### Preuves et contexte des modifications

Pour une demande classée `edit`, le préchargement recherche le code actuel. Les réponses assistant historiques, leurs blocs de raisonnement et les résumés antérieurs au format v2 sont retirés du contexte actif ; le protocole exact reste dans `context-checkpoints/`. Les demandes utilisateur et les paires appel/résultat d’outil sont conservées. La recherche automatique d’extraits historiques est désactivée pour ces modifications ; les outils de mémoire restent disponibles explicitement. Ce choix évite de réinjecter comme preuve une réponse erronée du modèle précédent. Les résumés v2 attribuent les déclarations et refusent les blocs de code/pseudo-appels ; ce filtre ne certifie pas leur exactitude sémantique.

Le garde de sortie refuse une annonce de tests réussis sans reçu d’exécution, même si `verify_changes` indique `not_required`. Une demande de modification sans écriture suivie doit annoncer explicitement « Aucune modification effectuée » et expliquer le blocage ou l’état déjà conforme. Ce garde ne prouve pas à lui seul que l’explication est exacte. Les tests de régression reproduisent les demandes de catalogue, les réponses contaminées entre modèles, les faux bilans et la conservation des archives.

### Visual reader and text executor

When the selected executor cannot read images, `trinity_agent/vision.py` performs one
tool-free, budgeted visual extraction (60 seconds maximum, 4096 output tokens).
The default reader is Qwen3.6-27B; `TRINITY_AGENT_VISION_READER` is an explicit operator
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
for the next new conversation (`trinityAgent.newConversationProject`). It does not
move the current conversation. New Conversation starts in that folder and records
the history assignment when the backend acknowledges `cleared`, after creating
the archive directory. Without an explicit selection, it uses the IDE workspace.

Automatic archive restoration preserves an optimistic prompt already sent while
the backend was starting and does not release the running-request state. A new
conversation must be acknowledged before submission is enabled. Changing a
project path remaps both the persisted selection and any pending destination.

Access preference and running-task access are separate UI state. A resume request
contains explicit `full_access` and `model` values; missing access defaults to
restricted mode. Stopped follow-ups retain their client ID. The backend records
accepted IDs atomically with the resumable task before removing queue entries;
duplicate submissions return `already_started` without executing again.


### Model policy and executable controls

The companion backend defines shared semantic guidance in `trinity_agent/instructions.py`.
Tool descriptions explain inputs and outputs; controllers enforce permissions,
budgets, tracked-edit verification and receipt freshness. Deployment-specific
policy stays outside the public extension. Do not duplicate a controller's
algorithm in the model prompt: expose its result and recovery instructions.

A successful tool exit establishes execution, not fulfillment of the user's
intent. File rollback does not roll back arbitrary shell, network, database or
service effects. Acceptance tests must exercise the requested behavior and keep
independent cases unavailable to the agent under evaluation. Record partial
results, interrupted mutations and infrastructure errors separately from model
quality scores.

Refusal before launching a command is a structured `executed: false` result.
Conservatively recognized diagnostic commands can fail without becoming unknown
mutations; arbitrary commands and interrupted mutations still require evidence
before replay. A refused file write remains incomplete in its current task until
a subsequent successful write resolves it. Absence of recorded writes is not
proof that a requested change was completed.

An explicit project verification policy remains mandatory. A project without
such a policy can start with tracked backups and syntax/content verification;
receipts identify missing functional coverage rather than claiming test success.
Periodic controller checkpoints use system context, never fabricated user turns,
and are discarded when preparing a new task.

Automatic file retrieval uses the same effective workspace boundary as explicit
file tools. Text search includes Git-ignored readable files under the selected
path while excluding dependency directories and generated bundles. Evaluation
archives are ineligible for normal cross-conversation memory, including user-scope
notes. Search fragments are rebuilt when their Markdown sources change.

## Contrôleur d’exécution et autonomie vérifiable

Le modèle choisit les actions et formule une réponse. Le contrôleur conserve les
preuves et décide si une conclusion de réussite est recevable. Les mécanismes
ci-dessous sont publics ; aucun réglage privé de crédit, de relais ou de déploiement
n’est exporté avec eux.

| Module backend | Responsabilité et limite |
| --- | --- |
| `trinity_agent/execution.py` | Enveloppe des outils, corrections utilisateur persistantes, erreurs, état final et liaison des contrôleurs. Un contenu lu est opaque : le mot « erreur » dans un fichier n’est pas un échec de lecture. |
| `trinity_agent/task_engine.py` | Journal SQLite transactionnel avec synchronisation durable ; intention avant exécution, résultat inconnu après interruption, réconciliation par callback de confiance. Arguments et sorties sont hachés dans ce journal. |
| `trinity_agent/acceptance.py` | Contrat déclaratif figé au début du tour, postconditions relues, reçus des contrôles vérifiés et détection des preuves périmées. |
| `trinity_agent/operations.py` | Plans nommés pour fichiers, services, bases de données et opérations distantes : application, sondes, retour arrière et contrôle de restauration. Chaque déploiement doit fournir ses propres commandes et sondes. |
| `trinity_agent/repository.py` | Carte bornée : AST Python, analyse lexicale JS/TS, dépendances et empreintes du contenu. Cache de parsing réutilisé uniquement pour les mêmes octets. |
| `scripts/evaluate-autonomy.py` | Évaluation reproductible avec postconditions indépendantes, tâches générées, interruptions, concurrence et faux succès. L’exécuteur par défaut est déterministe : il ne mesure pas un modèle. |

Les outils `inspect_repository`, `workflow_status`, `verify_task_acceptance`,
`execute_operation` et `reconcile_operation` exposent ces mécanismes au modèle.
Le journal réside dans `execution/` de la conversation. Le verrou de session du
backend exclut un ancien worker avant la reprise ; les transactions disposent
également d’un verrou de processus. Un résultat inconnu n’autorise pas un rejeu
aveugle. Les écritures de fichiers peuvent être réconciliées par leurs empreintes
avant/après ; une commande shell quelconque n’a pas de rollback universel.

Les états restent distincts : exécution réussie, contrôles réussis, contrat
satisfait. Sans contrat indépendant, `task_fulfilled` reste `null`. Les critères
proposés par le modèle sont signalés `model_proposed` et ne certifient pas la
réussite. Un contrat satisfait porte seulement sur son périmètre déclaré :
`natural_language_goal_verified` reste faux. Un résultat incertain ou un contrat
non satisfait conserve la tâche comme interrompue et suspend la file suivante.
Une tentative de correction finale est permise ; le moteur ne boucle pas pour
fabriquer une preuve absente.

### Déclarer un contrat

Le fichier existant `config/trinity-agent-checks.json` peut contenir une section optionnelle :

```json
{
  "version": 1,
  "checks": [],
  "acceptance": {
    "version": 1,
    "criteria": [
      {"id": "configured-port", "type": "json_equals", "path": "config.json", "pointer": "/port", "value": 8080},
      {"id": "legacy-removed", "type": "file_absent", "path": "legacy.json"}
    ]
  }
}
```

Types disponibles : `file_exists`, `file_absent`, `file_sha256`, `text_contains`,
`text_absent`, `json_equals` et `configured_check`. Ce dernier référence par
`check` le nom d’un contrôle de la section `checks`. Un reçu doit correspondre à
son argv, dossier, phase, date et empreintes actuelles. Les scripts et protections
déclarés du contrôle sont figés ; leurs dépendances transitives doivent aussi
être déclarées si elles font partie de la preuve. Modifier la politique pendant
le tour invalide son contrat, sans accepter automatiquement de nouveaux critères.

### Déclarer une opération récupérable

La section optionnelle `operations` contient `version: 1` et `plans`. Chaque plan
possède `name`, `kind` (`file`, `service`, `database` ou `remote`), `scope`
(`local` ou `remote`), `cwd`, `timeout`, et quatre listes argv : `apply`, `verify`,
`rollback`, `verify_rollback`. `protect` ajoute des fichiers de preuve à figer.
Le code source contient un exemple complet et les règles du schéma.

Les sondes renvoient 0 si la postcondition est vraie, 1 si elle est fausse ; tout
autre résultat reste inconnu. Après une interruption, une sonde supplémentaire
`verify_idle` doit prouver que l’exécution a cessé avant toute réconciliation ;
sans cette preuve, même un état momentanément attendu reste inconnu. Un échec de restauration est bloquant. Une cible
distante exige `target`, un `remote_cwd` absolu distinct du dossier local et un
exécuteur autorisé avec vérification des clés SSH. Aucun chemin SSHFS ne choisit
implicitement une machine d’exécution. Les commandes, migrations, transactions
SQL et sondes de santé restent propres au service ; déclarer un plan ne rend pas
ses effets arbitraires automatiquement réversibles.

### Évaluer sans confondre tests et performance du modèle

Depuis la racine du backend :

```sh
python scripts/check-backend.py
python scripts/evaluate-autonomy.py --seed 20261019 --output /tmp/autonomy-report.json
```

La première commande exécute les suites offline. La seconde sauvegarde scénarios,
latence, récupération, conflits, fausses conclusions et origine des erreurs.
Aucun appel payant par défaut. Un exécuteur injectable permet de tester un modèle
sur les mêmes postconditions cachées ; son coût et ses tokens ne doivent pas
être déduits des mesures de l’exécuteur déterministe. Les tests ne certifient ni
une autonomie générale ni l’absence de régressions en production.

Les adaptations locales utilisent ce même journal plutôt qu’un second suivi
concurrent. Elles conservent leurs règles propres de livraison, de CSS et de
publication. Une modification Python est chargée au prochain démarrage du
backend ; elle ne nécessite pas de reconstruire le frontend. Les manifestes de
mise à jour doivent néanmoins inclure tous les nouveaux modules publics.

### Outils différés, contexte et exécution bornée (1.15)

`trinity_agent/tools.py` conserve un noyau de fichiers, commandes et contrôles. Les familles
spécialisées sont activées par le besoin explicite ou par `discover_tools` lors
suivant ; la sélection est propre au tour, sans modifier les autorisations.
`trinity_agent/repository.py` classe les candidats par chemins, symboles et dépendances
avant la limite finale. Les empreintes invalident le cache de parsing. Le contexte
du contrôleur est borné à 4 800 caractères : portée, corrections, critères et
opérations inconnues. Une observation issue d'un ancien processus n'est jamais
présentée comme une validation actuelle.

`trinity_agent/process.py` supervise les commandes et contrôles configurés : sortie conservée
sur 16 Ko, arrêt au-delà de 16 Mio émis, expiration et arrêt du groupe de processus.
Les secrets hérités de l'environnement sont filtrés. Le profil par défaut
`approved` conserve la politique d'autorisation et **n'est pas un confinement OS** ;
un processus hostile qui se détache peut échapper au groupe. Le profil explicite
`TRINITY_AGENT_COMMAND_PROFILE=workspace` exige bubblewrap et les namespaces Linux,
isole le réseau et monte seulement le workspace en écriture ainsi que les chemins
d'exécution autorisés en lecture. Il refuse l'exécution si cette capacité manque.
Les contrôles déclarés et transports SSH restent des exécutions approuvées ; le
profil workspace concerne l'outil de commande locale, pas toute l'application.
Un timeout SSH ne prouve jamais l'arrêt de la commande distante.

Les reçus de contrôle couvrent les entrées observées, sujets littéraux déclarés,
changements et fichiers protégés ; ajouter une entrée observée invalide le reçu.
Déclarer les dépendances de preuve reste nécessaire. Un contrôle échoué a un
résultat connu, mais ne constitue pas une preuve de réussite. Les effets d'une
commande shell arbitraire peuvent rester inconnus et nécessiter réconciliation.

### Campagnes réelles et comparaison du coût

`scripts/evaluate-agent.py` exerce les vrais outils backend dans des fixtures
isolées, avec oracle indépendant, modifications concurrentes, données utilisateur
à préserver, injection dans une source, corrections et tâches déjà satisfaites.
Le défaut utilise un modèle simulé et n'effectue aucun appel payant :

```sh
python scripts/evaluate-agent.py --seed 20261019 --output /tmp/agent-offline.json
python scripts/evaluate-agent.py --real-model gpt-oss-120b \
  --cases multi_file,already_satisfied,source_injection,correction_compaction \
  --request-limit 48 --token-limit 120000 --max-cost-eur 0.10 \
  --output /tmp/agent-live.json
```

Chaque fichier de sortie doit être neuf. Le rapport conserve empreintes des
sources, modèle, paramètres, résultats par cas et mesures des schémas. Les limites
de campagne s'ajoutent au registre de facturation réel. `--eager-tools` fournit un
comparateur sur les mêmes outils de fixture. Les gardes artificiels restent
séparés des tâches du modèle ; un échec fournisseur ne compte pas comme une
réussite ni comme une erreur de raisonnement. Quelques tâches réussies ne mesurent
pas une supériorité sur les agents concurrents.

Les choix ont été comparés aux sources primaires :
[réparation validée Codex](https://developers.openai.com/cookbook/examples/codex/build_iterative_repair_loops_with_codex),
[hooks Claude Code](https://code.claude.com/docs/en/hooks),
[persistance OpenHands](https://docs.openhands.dev/sdk/guides/convo-persistence) et
[carte de dépôt Aider](https://aider.chat/docs/repomap.html).
Il s'agit d'une étude ciblée des mécanismes documentés, pas d'un audit exhaustif
ni d'un accès aux implémentations propriétaires.


### Réglages Qwen et coût des résumés (1.15.2)

Le catalogue est chargé avant le choix des paramètres, y compris à froid. Une liste
`reasoning_efforts` explicitement vide n'autorise pas les réglages GPT `low/medium` :
raisonnement disponible et effort réglable sont deux capacités distinctes. Le
catalogue OVH annonce actuellement ces niveaux pour Qwen3.8, pas pour Qwen3.5/3.6.
L'OpenAPI générique ne prouve pas l'effet d'un paramètre sur chaque modèle.
Aucun `chat_template_kwargs` non annoncé n'est injecté.

Pendant une compaction nécessaire, seuls les anciens blocs de raisonnement sont
exclus de la source envoyée au résumeur ; instructions, réponses visibles et
résultats d'outils restent disponibles. Le protocole complet est archivé et le
raisonnement des tours d'outils conservés reste intact. Cela économise la source
du résumé, sans désactiver le raisonnement du modèle ni certifier sa qualité.

Le banc `evaluate-agent.py` utilise les paramètres de production par défaut.
`--reasoning-effort` et `--max-output-tokens` permettent une comparaison explicite,
avec la même configuration à la reprise. Les compteurs absents restent inconnus.
Les réglages issus de petits essais locaux ne deviennent pas des valeurs par défaut
universelles ; les tâches complexes conservent leur politique de raisonnement.

Références : [API OVH](https://oai.endpoints.kepler.ai.cloud.ovh.net/doc/Qwen3.6-27B/openapi.json),
[Qwen3.6](https://huggingface.co/Qwen/Qwen3.6-27B),
[Qwen3.8](https://huggingface.co/Qwen/Qwen3.8-27B).
