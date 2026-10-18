Stratégie d'inférence et calcul local
====================================

Synthèse documentaire et application au backend, 17 octobre 2026.
Les sources ci-dessous sont officielles ; cette sélection couvre les mécanismes
utiles à cet agent, sans prétendre couvrir toutes les documentations disponibles.

Objectif : augmenter le nombre de tâches correctement terminées, avec des preuves,
en maîtrisant latence et coût. L'architecture retenue est : analyse de la demande
et de ses relances, récupération locale des éléments utiles, choix d'un modèle
compatible, exécution des outils, vérification du résultat et conservation du
contexte nécessaire à la suite.

Ce que nous retenons des différents fournisseurs
------------------------------------------------

**OpenAI : consignes directes et effort adapté.** Les modèles de raisonnement
bénéficient d'objectifs et contraintes explicites ; demander systématiquement
une longue réflexion étape par étape n'est pas une optimisation universelle.
Le backend conserve un effort faible ou moyen selon la tâche, uniquement quand
le modèle expose ce réglage. Le choix tient désormais compte de l'analyse des
messages précédents : « continue » après une refactorisation reste une tâche
complexe. Les plafonds de sortie restent limités par le modèle et la place
disponible. Source : `raisonnement OpenAI`_.

**Anthropic : sélectionner les informations utiles au moment où elles deviennent
nécessaires.** Un contexte pertinent contient des preuves précises, des décisions
et les éléments manquants à rechercher. Nous conservons les extraits avec leurs
chemins et lignes, puis les relisons avant modification. La sélection locale
utilise maintenant BM25 et favorise les passages complémentaires ; les anciennes
copies de récupération sont exclues. Les outils paginés et la compaction avec
archives exactes existaient déjà. Sources : `contexte Anthropic`_ et `outils
Anthropic`_.

**DeepSeek : respecter le protocole du modèle de raisonnement.** Les règles de
transmission de ``reasoning_content`` entre appels d'outils et la disponibilité
des paramètres de génération dépendent du mode et de la version. Nous conservons
le protocole natif fourni par l'adaptateur ; aucun réglage DeepSeek n'est injecté
dans les requêtes OVH par simple analogie. Une future intégration directe devra
tester les séquences complètes d'outils, et pas seulement une réponse textuelle.
Source : `mode thinking DeepSeek`_.

**Qwen : conserver le format natif des appels d'outils et distinguer les modes.**
La documentation déconseille d'improviser un protocole ReAct à base de chaînes
d'arrêt pour les modèles utilisant leur propre format. La fiche Qwen3.5 montre
aussi que les recommandations d'échantillonnage varient selon la tâche et le
mode. Nous gardons les schémas d'outils structurés et les paramètres réellement
acceptés par l'hébergeur. Il n'existe pas de température universelle à copier
sur toutes les familles. Sources : `outils Qwen`_ et `fiche Qwen3.5`_.

**Google : préserver les éléments opaques du protocole.** Les signatures de
raisonnement Gemini servent à maintenir la continuité entre appels. Elles ne
doivent pas être reconstruites depuis un résumé. Cette règle justifie de tester
la fidélité des historiques dans chaque adaptateur ; elle ne signifie pas que
le backend OVH utilise des signatures Gemini. Source : `thinking Gemini`_.

**OpenAI et DeepSeek : réutiliser les préfixes lorsque le service le permet.**
Le cache de prompts côté fournisseur dépend des préfixes et des caractéristiques
du service. Garder instructions et définitions d'outils stables est une piste
utile ; les données changeantes doivent être placées en conséquence. Ce cache
est distinct du cache RAM local. Aucun gain de facturation ni cache KV distant
n'est promis ici : la disposition actuelle des messages et les capacités OVH
doivent être validées avant cette optimisation. Sources : `cache OpenAI`_ et
`cache DeepSeek`_.

**Évaluer le résultat, pas seulement la réponse.** Les guides OpenAI et Anthropic
recommandent des cas représentatifs, des critères observables et des évaluateurs
automatiques calibrés par des revues humaines. Les tests locaux vérifient les
invariants du logiciel ; une évaluation de qualité du modèle doit aussi vérifier
le résultat obtenu dans l'environnement. Sources : `évaluation OpenAI`_ et
`évaluation Anthropic`_.

Application concrète dans cet agent
-----------------------------------

* ``ovh_request.py`` analyse localement la demande sans appel de modèle. Il
  conserve le sujet des relances, identifie les chemins pertinents et prépare
  des extraits. BM25 réduit l'effet des répétitions et tient compte de la rareté
  des termes et de la longueur des passages, avec k1=1,2 et b=0,75 ; la diversité
  est une heuristique supplémentaire propre à l'agent. Référence : `BM25`_.
* Le nombre de lecteurs indépendants s'adapte aux CPU et à la mémoire utilisables.
  Le pool est réutilisé et plafonné à seize threads, avec une file d'attente bornée,
  même si un montage SSHFS bloque. Une réduction limite les nouveaux travaux
  simultanés ; les lectures déjà engagées se terminent à leur rythme.
  Les résultats tardifs restent attachés à leur demande d'origine. Ces threads
  améliorent surtout les lectures ; le calcul Python ne s'exécute pas
  nécessairement sur tous les cœurs simultanément.
* Le cache conserve textes et index lexical avec une validation des métadonnées
  à chaque lecture. Les modifications, remplacements et suppressions invalident
  les résultats. Le plafond d'entrées configurable évite d'évincer trop tôt des
  centaines de fichiers alors que de la RAM reste disponible.
* Routage et réglages de génération partagent désormais la difficulté issue
  de l'analyse contextuelle. Les capacités, limites et prix du catalogue OVH
  restent déterminants ; la taille du modèle est seulement une heuristique.
  Source des capacités : `catalogue OVH`_.
* Les lectures d'outils indépendantes peuvent déjà se chevaucher ; modifications
  et vérifications s'exécutent comme des barrières. Les couples appel/résultat
  sont préservés pendant la compaction, les sorties volumineuses sont archivées
  et les contrôles applicables aux fichiers modifiés sont exécutés.

Le prompt commun a été renforcé sur le résultat observable, la recherche des
preuves manquantes et les contrôles locaux. Les calculs exacts sont confiés aux
outils. Les boucles de critique, votes entre modèles et architectures multiagents
ne sont pas ajoutés systématiquement : leur coût doit être justifié sur les
tâches visées. Voir `architecture Anthropic`_.

Profil local et ressources
--------------------------

Le profil est désormais automatique, y compris dans ce déploiement. La détection
dans ``ovh_resources.py`` combine nombre de CPU, affinité, quotas CPU et cpusets,
mémoire totale et disponible, limites et consommation des cgroups v1/v2.
Les limites des parents visibles sont aussi prises en compte. Le seuil
``memory.high`` est traité comme une limite de dimensionnement pour éviter le
ralentissement dû à la récupération mémoire. Référence : `cgroups Linux`_.

.. list-table:: Réglages portables et locaux
   :header-rows: 1
   :widths: 60 40

   * - Variable
     - Valeur par défaut
   * - ``OVH_AGENT_RAM_CACHE_MB``
     - auto
   * - ``OVH_AGENT_RAM_CACHE_ENTRIES``
     - auto
   * - ``OVH_AGENT_PREFLIGHT_WORKERS``
     - auto
   * - ``OVH_AGENT_PREFLIGHT_MAX_FILES``
     - auto
   * - ``OVH_AGENT_PREFLIGHT_FILE_CHARS``
     - auto

Une valeur numérique définit un plafond supplémentaire ; une valeur absente ou
vide sélectionne aussi le mode automatique. Zéro désactive le cache ou le scan
concerné ; les lecteurs gardent un minimum d'un thread. Les observations système
sont renouvelées à la demande toutes les 30 secondes. Les accès au cache
réévaluent périodiquement ses plafonds et évincent les entrées LRU si la mémoire
disponible diminue. Les lecteurs ajustent la concurrence sans recréer de pools.

La politique alloue au cache au plus 20 % de la mémoire utilisable et un quart
de la marge disponible, après une réserve système ; en pression forte, cette
allocation diminue davantage. Les objets déjà conservés sont comptés pour éviter
qu'un remplissage normal ne provoque à lui seul une réduction. Le nombre de
lecteurs est limité par CPU et mémoire, avec un maximum de seize. Le scan et la
taille des fichiers suivent ce budget, dans les bornes de l'algorithme. Ces
heuristiques restent vérifiables avec ``python ovh_resources.py`` ; elles
constituent des plafonds estimés, pas une réservation ni une limite de RSS.

Sur la machine observée, le calcul automatique donne actuellement six lecteurs,
720 fichiers et un cache plafonné à environ 4,7 Gio. Une machine plus petite ou
un conteneur limité reçoit un profil inférieur. Si les informations système sont
inaccessibles, le calcul utilise une hypothèse mémoire prudente de 512 Mio au
total et 128 Mio disponibles, plutôt que de supposer un grand serveur.

Le scan élargi ne signifie pas un prompt élargi : la préparation conserve
au maximum huit extraits dans 24 000 caractères. Son attente reste limitée
à 2,5 secondes ; les outils peuvent ensuite poursuivre une lecture ciblée.
Le parcours reste limité aux racines de projet sélectionnées.

Le fichier local ``.env`` est relu par ``ovh-agent.local.sh`` au lancement du
backend, avec les cinq paramètres ci-dessus positionnés sur ``auto``. Les
plafonds de dépenses et de génération ne changent pas.
L'inférence du grand modèle reste hébergée ; la RAM et le CPU locaux servent
à la recherche, aux caches et aux vérifications.

Mesures et limites de la validation
-----------------------------------

``scripts/benchmark-retrieval.py`` crée un corpus temporaire sans données privées
ni requête réseau. Il place deux éléments attendus dans un fichier tardif de
l'inventaire et après les 65536 premiers caractères d'un fichier long.
Les mesures suivantes ont été faites avant le passage au dimensionnement
automatique, avec deux profils fixes et le même moteur de sélection :

.. list-table:: Mesure locale sur ce corpus synthétique
   :header-rows: 1

   * - Profil
     - Fichiers lus
     - Éléments trouvés
     - À froid
     - Médiane de cinq lectures chaudes
   * - Limites précédentes : 120 fichiers, 65536 caractères
     - 120
     - 0/2
     - 181 ms
     - 76 ms
   * - Profil local élargi
     - 242
     - 2/2
     - 325 ms
     - 133 ms

Le profil élargi ajoute 550 caractères de contexte et conserve environ 2,3 Mio
d'objets estimés en cache dans ce cas. Il travaille davantage pour trouver les
preuves manquées. Ce résultat mesure la couverture d'une recherche ciblée et
la réutilisation du cache ; il ne mesure ni la qualité générale des réponses,
ni la vitesse de génération du fournisseur.

Sur le dépôt de l'agent lui-même, une demande ciblant ``ovh_request.py`` a lu
480 fichiers en 1443 ms à froid puis 672 ms à chaud, avec 16 401 caractères
sélectionnés et environ 46 Mio estimés en cache. Le résultat est signalé partiel :
la limite de 480 fichiers ne couvre pas les 1722 candidats de cet inventaire.
Ces deux observations donnent un ordre de grandeur local, pas une mesure p95.

La validation initiale a couvert dix suites hors ligne : budgets, facturation des appels,
sessions, vérifications, compaction, préparation, caches, espaces de travail,
mémoire de projet et intégration d'inférence. Elles incluent les montages lents,
la fraîcheur des fichiers, la concurrence, les relances courtes et les paramètres
propres aux modèles.

La vérification du dimensionnement ajoute des cas de petit hôte, d'affinité CPU,
de quota fractionnaire, de cgroups v1/v2 avec limite parent, de mémoire sous
pression, de réduction/récupération du cache et de concurrence du pool existant.
Sur le dépôt réel, le profil automatique a examiné 720 fichiers en 1919 ms
à froid puis 954 ms à chaud, avec environ 55 Mio de cache effectivement occupés.

Pour comparer des modèles sur des demandes réelles, conserver un jeu séparé de
cas représentatifs et mesurer : réussite vérifiée, coût par tâche réussie,
latences médiane et p95, premier texte visible, tokens et reprises nécessaires.
Une modification de température, un modèle d'embeddings local ou un reranker
devra être comparé à cette base avant généralisation. La présente validation
n'a déclenché aucun appel d'inférence payant.

Reproduction::

    /opt/ovh-agent/bin/python scripts/benchmark-retrieval.py
    /opt/ovh-agent/bin/python scripts/test-request.py
    /opt/ovh-agent/bin/python scripts/test-inference.py
    /opt/ovh-agent/bin/python scripts/test-resources.py

.. _raisonnement OpenAI: https://developers.openai.com/api/docs/guides/reasoning-best-practices
.. _contexte Anthropic: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
.. _outils Anthropic: https://www.anthropic.com/engineering/writing-tools-for-agents
.. _mode thinking DeepSeek: https://api-docs.deepseek.com/guides/thinking_mode/
.. _outils Qwen: https://qwen.readthedocs.io/en/stable/framework/function_call.html
.. _fiche Qwen3.5: https://huggingface.co/Qwen/Qwen3.5-9B
.. _thinking Gemini: https://ai.google.dev/gemini-api/docs/thinking
.. _cache OpenAI: https://developers.openai.com/api/docs/guides/prompt-caching
.. _cache DeepSeek: https://api-docs.deepseek.com/guides/kv_cache/
.. _évaluation OpenAI: https://developers.openai.com/api/docs/guides/evaluation-best-practices
.. _évaluation Anthropic: https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
.. _BM25: https://www.elastic.co/docs/reference/elasticsearch/index-settings/similarity
.. _catalogue OVH: https://www.ovhcloud.com/en/public-cloud/ai-endpoints/catalog/
.. _architecture Anthropic: https://www.anthropic.com/engineering/building-effective-agents
.. _cgroups Linux: https://docs.kernel.org/admin-guide/cgroup-v2.html

Mode local Max
--------------

Le sélecteur Perf. Auto / Perf. Max persiste le choix utilisateur et modifie la
politique du moteur actif sans nouvel appel au modèle. Max remplace les cinq
plafonds manuels de cache/lecture par les ressources effectives : CPU, affinité,
quotas et mémoire disponible, y compris les cgroups parents. Les plafonds fixes
de 16 lecteurs et 2000 noms parcourus disparaissent dans ce mode.

La mémoire est répartie entre une réserve système, le travail de récupération
et le cache partagé, rempli à la demande. Les observations sont renouvelées
toutes les cinq secondes à l'utilisation ; les lecteurs superflus se retirent.
Le cache retient des estimations de taille, pas une réservation physique de RAM.
Le délai de recherche, les limites API et les protections de vérification
restent distincts de ce choix de ressources locales.


Évaluations locales du catalogue et captures
------------------------------------------

``scripts/evaluate-catalog.py --live --output <rapport.json>`` exécute des
cas synthétiques de code, outils, sources web, sécurité système/réseau, Linux,
Windows, macOS, Android, iOS et vision sur les modèles de chat disponibles.
Les modèles spécialisés hors chat et les capacités absentes sont signalés
comme non applicables. Les appels passent par le registre budgétaire habituel.
Les réponses, appels d'outils, captures de référence, tokens et latences sont
sauvegardés dans des conversations « Tests catalogue ». Les tests système ne
modifient aucune machine ; le code Python est vérifié dans un processus séparé.

``scripts/summarize-evaluation.py <rapports.json...> --apply`` produit la synthèse
archivée et ``model-evaluations.json`` dans le répertoire de données local.
Les réponses brutes sont conservées ; la notation technique accepte les commandes
équivalentes et distingue les échecs de format, les erreurs et les cas non
applicables. Deux réussites au minimum et un taux de réussite de 80 % sont
nécessaires pour privilégier un domaine mesuré. La latence départage les candidats
validés dans le groupe de coût compatible. Les preuves expirent après 14 jours
et ne s'appliquent qu'au même endpoint. Elles ne certifient pas une expertise
générale. Le mode sans raisonnement n'est activé que pour les tâches non complexes
des domaines où cette configuration a réussi tous ses cas.

Une question purement visuelle peut utiliser un lecteur d'images sans outils ;
une modification de fichiers exige un modèle disposant également des outils.
Le choix manuel reste prioritaire. Les archives de test sont consultables mais
exclues de la mémoire documentaire du projet.

``scripts/benchmark-agent.py --live --output <rapport.json>`` mesure trois passages
du vrai pipeline (préparation, instructions, schémas d'outils, routage et streaming),
avec modifications et commandes désactivées dans le processus de benchmark.
Il recharge les limites numériques locales pertinentes. Aucune égalité de
performance avec Codex n'est déduite sans exécution comparative équivalente.

Le chat accepte les captures collées avec Ctrl+V / Cmd+V, via les fichiers ou les
éléments du presse-papiers. Le texte associé est conservé. Les préparations sont
sérialisées pour respecter cinq images maximum ; l'envoi attend leur fin.

Validation hors ligne complète : ``python scripts/check-backend.py`` et
``npm --prefix ovh-agent-extension/extension test``. Les benchmarks payants ne
sont jamais lancés par la CI. Le cron Git existant reste inchangé.

La méthode 2 ferme les clients HTTP dans leur boucle, garde la même boucle lors
des répétitions du pipeline et archive les réponses avant et après feedback.
Le score au premier essai gouverne le routage : une réponse corrigée par un
oracle de test ne devient pas artificiellement une réussite sans assistance.
``--level 2`` ajoute les résumés périmés et les sous-réseaux ; ``--level 3`` ajoute
le contexte long, les instructions non fiables dans les outils et les régressions.
``--seed`` varie les données réseau. ``--feedback --retries 2`` autorise deux
corrections avec un retour ciblé sur les champs invalides. Cela ne modifie jamais
les poids des modèles. ``--sampling qwen-coding`` teste température 0,6 et top_p
0,95 ; cette expérience n'est pas promue automatiquement en production.

Les lectures de pages peuvent utiliser ``query`` pour atteindre un passage sans
transmettre toute la navigation. Une occurrence absente n'est pas une preuve.
Les demandes explicites de lecture seule présentent moins de schémas d'outils ;
les tâches d'édition et les réorientations conservent les outils complets.

Une installation peut fournir ``budgets/policy.json`` dans son répertoire de
données. Les limites quotidiennes, hebdomadaires et mensuelles restent exprimées
en EUR ; une enveloppe ``credit`` optionnelle cumule les journées depuis minuit
UTC et expire à une date précise, sans remise à zéro au changement de mois.
Le fichier est relu avant chaque réservation. Les valeurs publiques par défaut
restent inchangées. Le registre est conservateur en cas d'usage API inconnu ;
ses estimations ne constituent pas une facture du fournisseur.

``scripts/run-evaluation-campaign.py --config <fichier-local.json>`` peut être
programmé explicitement sur une installation : verrou anti-chevauchement, un
lancement par jour, vérification préalable du crédit, progression, archivage et
arrêt à l'échéance. ``--dry-run`` vérifie le plan sans appel au modèle. Aucun cron
de campagne ni crédit propre à un client n'est inclus dans la version publique.

Un filtre ``site:`` dans une recherche est vérifié sur les URL réellement
retournées : les résultats hors domaine sont écartés, même si le moteur ignore
son filtre. Une recherche vide est signalée ; elle ne devient pas une source.
Le banc archive les appels et erreurs de validation (sans exposer le raisonnement
interne) et offre ``--no-stream`` pour isoler les problèmes de JSON d'outils.
Les expériences sans streaming ne calibrent pas le chemin normal avec streaming.

Le réglage local optionnel ``OVH_AGENT_ROUTING_PRICE_FACTOR`` (entre 1 et 10)
élargit le groupe de prix dans lequel comparer les latences mesurées. Le registre
budgétaire reste obligatoire. Les tâches complexes conservent une sélection
prudente : des questions élémentaires réussies ne suffisent pas à désigner le
modèle le plus rapide pour une refonte complète. Les résultats partiels, notamment
web, sont indiqués comme non validés dans la raison du routage.

``read_file`` récupère un slash initial omis uniquement si le chemin demandé
n'existe pas et si le fichier corrigé existe dans l'espace autorisé. La réponse
indique explicitement la normalisation. Les outils d'écriture ne changent pas de
chemin de cette façon.

Le mode expérimental ``--api responses`` sert à comparer le contrat API OVH avec
Chat Completions ; il utilise ``store=false`` et ne calibre pas le moteur courant.
Les champs ``type=message`` et ``status=completed`` des retours de fonctions sont
explicites, conformément au schéma OVH. Une erreur HTTP 422 reste une erreur de
compatibilité du protocole, pas une preuve de mauvaise qualité du modèle.


Performances de l'application et réglages d'installation
------------------------------------------------------

``python scripts/benchmark-performance.py --runs 3 --output performance.json``
mesure hors réseau le backend avec état applicatif neuf, puis état réutilisé.
Il mesure aussi la préparation d'une requête dans le processus déjà chargé,
les lectures répétées et une compaction synthétique. Le modèle est simulé :
ces chiffres n'incluent ni latence OVH ni génération réelle. Les caches du
système d'exploitation ne sont pas vidés. Les tokens sont estimés, pas facturés.

Les optimisations publiques portent sur les consignes applicatives et le
comptage linéaire du contexte. Les messages utilisateur, identifiants d'outils,
archives exactes et contrôles de vérification restent conservés. Les budgets
publics ne sont pas réduits pour produire artificiellement de meilleurs chiffres.

Un adaptateur d'installation peut être mesuré séparément avec ``--adapter``
et ses limites numériques avec ``--env-file``. Ces fichiers privés, les données
mesurées, le routage personnalisé et les enveloppes de crédit ne sont pas
embarqués dans le VSIX. Une mesure locale n'est pas une promesse de performance
sur une autre machine. Mettre à jour le backend compagnon avec l'extension.
