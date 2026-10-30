# Changelog

## 5.3.8

- Add subtle horizontal spacing around mount-point storage units, with slightly more room after each unit.

## 5.3.6

- Keep mount-point storage values and their dimmed units on one line.

## 5.3.5

- Dim storage units in the mount-point usage display while keeping the numeric values fully visible.

## 5.3.4

- Restore Marketplace upgrade compatibility by publishing to the historical `stopyz.ovh-agent` identifier while keeping the local `trinity.trinity-agent` identity.

## 5.3.3

- Switch the Activity Bar logo automatically: purple in light themes and white in dark themes.

## 5.3.2

- Restore the TRINITY Activity Bar icon after the extension identifier refactor by shipping the requested menu artwork through a VS Code-compatible SVG asset.

## 5.3.1

- Display OpenAI catalogue prices in the configured currency and install the current UI instead of leaving the IDE on an older package.
- Render mount storage units compactly, for example `512MB / 51.2GB`.

## 5.1.0

- Activate OpenAI Codex with a provider-specific key and a real Responses API validation.
- Default the OpenAI monthly allowance to 100 USD outside Compose and exclude legacy usage events that do not identify OpenAI.

## 5.0.0

- Refactor TRINITY Agent around provider-specific API configuration and add OpenAI Codex through the Responses API.
- Rename generic settings, paths, runtime files and extension identifiers from the legacy vendor prefix to TRINITY.

## 4.0.4

- Show TRINITY data size beside total used filesystem space, for example `515 MB / 52.1 GB`.

## 4.0.3

- Display filesystem usage with international byte symbols such as `GB` instead of locale-specific `Go`.

## 4.0.2

- Restore extension activation by including the backend launcher path resolver in the VSIX.
- Reject release packages that omit a required runtime module.

## 4.0.1

- Enforce the VSIX installation boundary during packaging: extension files must remain under the VS Code extension directory, while backend modules, launchers and verification configuration are rejected from both Marketplace and local packages.
- Publish a Marketplace upgrade after the repository layout cleanup so existing installations receive the corrected package lineage.

## 4.0.0

- Remove all remaining `trinity-*` entries from the repository root: the Python entry point now lives in `trinity_agent`, launchers live in `scripts`, and the VS Code source lives in `extension`.
- Migrate saved root launcher paths to `scripts/` while preserving conversation startup for existing installations.
- Update packaging, CI, public export, auto-update, tests and documentation for the final source layout.

## 3.1.0

- Migrate saved `trinity-agent.sh` and `trinity-agent.local.sh` backend paths to the renamed TRINITY launchers when the legacy file no longer exists.
- Restore conversation opening immediately for existing installations that retained the old backend path in VS Code settings.

## 3.0.0

- Remove the remaining legacy `trinity-agent` source names from the repository root: launchers, extension sources, branding and verification configuration now use TRINITY names and dedicated folders.
- Update Docker startup, release workflows, package integrity metadata, tests and documentation for the renamed paths.

## 2.0.0

- Move the Python backend from root-level legacy provider-prefixed modules into the `trinity_agent` package with concise module names.
- Keep `trinity_agent/__main__.py` as a compatibility launcher while updating deployment integrity checks, public exports, tests and documentation for the package layout.

## 1.17.100

- Keep conversation loading attached to the requested archive so delayed errors from the previous backend cannot cancel a newer open.
- Release the loading interface on every correlated backend failure, including startup, process exit and control-write errors, so opening can be retried immediately.

## 1.17.99

- Clear folder and conversation highlights when clicking anywhere outside their rows, including the Conversations header and mount-point display.

## 1.17.98

- Keep the workspace-scoped backend lock regression portable across local and hosted Linux runners.

## 1.17.97

- Scope backend exclusion to the active workspace so an idle TRINITY Agent window no longer blocks one-shot work on another project.
- Preserve same-project write exclusion and clarify its busy-state diagnostic.

## 1.17.96

- Clear the selected conversation folder and its persisted next-conversation destination when clicking outside folder rows.

## 1.17.95

- Bound malformed tool-call retries for every provider so the agent can recover with another available tool instead of looping.
- Restore the DeepSeek context-summary deadline and local fallback when model compaction stalls.
- Isolate offline autonomy evaluation from the installation's selected provider for reproducible evidence.

## 1.17.94

- Present emoji modes as clear unbulleted lines with coded values in both READMEs.

## 1.17.93

- Apply 0.33 opacity consistently to decorative icons in webview popups.

## 1.17.92

- Make archive storage usage part of the mount-point display so disabling the mount-point setting hides both the path and used size while retaining the Conversations heading.

## 1.17.91

- Refresh the README and Marketplace preview with the current TRINITY Agent conversation screen.

## 1.17.90

- Keep the chat-icon project counters covered by the complete drag-and-drop test suite.

## 1.17.89

- Replace the conversation word in project counters with a compact chat bubble icon while retaining accessible labels.

## 1.17.88

- Render the TRINITY Agent header title in pure white in dark and high-contrast themes.

## 1.17.87

- Add a plus action after each project pencil to create and open a conversation directly in that folder.

## 1.17.86

- Use the black TRINITY logo for the editor title sidebar button in light themes, with its white counterpart in dark themes.

## 1.17.85

- Add a bold TRINITY button to editor title bars that opens TRINITY Agent directly in the sidebar.

## 1.17.84

- Replace the activity-bar menu logo with the bolder TRINITY variant while preserving the dedicated light and dark header logos.

## 1.17.83

- Replace the activity-bar menu icon with the dedicated TRINITY hexagonal logo without changing the light/dark application logos.
- Use dedicated primary and white TRINITY header logos in light and dark themes at full opacity.
- Refresh the versioned workbench stylesheet URL when local branding changes so existing browsers do not keep stale logo opacity rules.

## 1.17.82

- Add a subtle background and hover/keyboard-focus feedback to the centered action collapse button in light and dark themes.
- Make added and removed diff colors slightly brighter while keeping the light and dark themes readable.
- Highlight diff code with bundled highlight.js while preserving line numbers and copied patches; upgrade older snapshots and approval cards and keep oversized diffs readable.
- Keep executed commands visible alongside results after success, errors and reopening conversations, including long commands and older snapshots.
- Show used filesystem space beside the archive mount path, with a subtle separator.

## 1.17.81

- Use the folder-aware primary color for DeepSeek balance amounts, matching OVHcloud.
- Restore each conversation’s selected model and provider when reopening it, with its saved context for the next request.
- Recover conversation navigation after backend errors and session rejection; preserve model choices made while a conversation loads.
- Keep authorized work available when a follow-up merely describes an unchanged result; preserve actual read-only instructions.

## 1.17.80

- Include both CNY and USD DeepSeek balances, converting and summing them in euros with dated ECB reference rates. Preserve native amounts and avoid counting granted credit twice.
- Show DeepSeek model prices and conversation costs in euros, retain native accounting, reject obsolete dollar-only balance caches, and expose exchange-rate outages without inventing a conversion.

## 1.17.79

- Request one bounded recovery attempt before accepting a final answer after a failed or unavailable tool. Corrected calls and available native alternatives remain usable; authorization denials and uncertain mutations are never bypassed.
- Keep corrected progress plans callable after invalid step indices, while retaining repeated-loop detection.
- Expose approved local diagnostics during read-only tasks, with execution-time restrictions on writes, shell composition and remote calls.

## 1.17.78

- Raise the bounded tool-retry limit from 2 to 8, configurable with `TRINITY_AGENT_TOOL_RETRIES`, so a model can correct an invalid `run_command` call instead of failing the turn with `exceeded max retries count of 2`.

## 1.17.77

- Retry interrupted OVH connections before exposing a response, with a separate budget reservation for each bounded attempt. Automatic routing can switch away from GPT-OSS after connection failure, without replaying mutations or started streams.

- Régulation OVH partagée entre processus à 400 requêtes par minute, par projet et modèle, avec attente visible avant les appels.
- Reprise bornée des refus HTTP 429 en respectant `Retry-After`, sans rejouer un flux déjà commencé ni contourner les réservations de budget.

## 1.17.76

- Une recherche documentaire interrompue ne relance plus un parcours illimité des montages ; les chemins complets déjà observés sont conservés.

- Logos de la liste des extensions de code-server uniformisés à 36 px, centrés dans leur conteneur flex et affichés avec une opacité de 0,66 par le branding du déploiement.
- Les sondes HTTP locales strictement reconnues restent des diagnostics même si la connexion est refusée ; les commandes avec effets potentiels conservent leur journal de mutation.

- Vérification avant lancement des commandes shell mal formées, outils absents appelés par heredoc et commandes Cargo sans manifeste : le journal conserve une preuve de non-exécution et permet la correction de l’appel.
- Les refus avant lancement ne sont plus comptés comme des commandes exécutées au résultat inconnu. Les opérations aux effets incertains conservent leur blocage et indiquent l’outil de réconciliation disponible.
- Le point d’avancement requis est indiqué au modèle avant l’appel d’outil, sans masquer les outils ni forcer `tool_choice`. Le rappel est retiré dès qu’il est satisfait.

## 1.17.75

- Require visible progress before tool work and at work checkpoints, respecting explicit silence requests. Keep long command status visible without accumulating notices.
- Execute literal commands directly so an OS refusal before launch is recorded as not applied and can be retried after correction. Preserve uncertainty for commands that started, timed out or failed after effects.

## 1.17.74

- Select the local extension icon for the active editor theme before installation caches its manifest. Keep the public Marketplace icon unchanged.

- Explain planned actions, findings and checks during tool work through the existing task plan. Progress remains visible during execution and can be restored from conversation archives, including when a provider omits intermediate assistant text.

## 1.17.73

- Keep correction tools available when a follow-up reports that no prior changes were applied, while preserving current prohibitions and inherited read-only restrictions.

## 1.17.72

- Match the conversation loading message and its light background to the selected folder color, with readable text in light and dark themes and the default primary color outside folders.

## 1.17.71

- Solde du fournisseur sélectionné visible immédiatement depuis le cache et actualisé au démarrage.
- Réponses de l’ancien fournisseur ignorées.
- Message de chargement avec texte primaire et fond primaire léger.

## 1.17.70

- Recognize affirmative edit requests after a comma while preserving global restrictions and scoped prohibitions.
- Preserve verification phase markers and exclude completed pre-edit baseline failures from native file-tool outcomes, while retaining post-edit, incomplete-check and actual write failures.

## 1.17.69

- Close abandoned tool calls with native history repair before a new user turn, preserving evidence without replaying earlier effects.
- Keep the previous conversation, persisted pointer and history when an archive cannot be validated or recovered; read snapshots after recovery.
- Recognize affirmative edit requests after introductory context while retaining scoped restrictions and explanatory questions.
- Include instruction and tool-schema input in automatic routing cost estimates.

## 1.17.68

- Keep interrupted-session regression tests independent of provider credentials and billing catalogues, so the same scenarios run offline in local and GitHub environments.

## 1.17.67

- Resolve the latest stable Python, npm and container dependencies during updates and builds; refresh bundled browser libraries and test the resolved set before packaging.
- Resolve Node compatibility from the latest code-server metadata during image builds, and stop immediately if installation fails.
- Preserve distinct warning and error icons when rebuilding the bundled assets.
- Report controller failures through Pydantic AI’s native tool-failure channel while preserving execution receipts and argument-validation retries.
- Retain interrupted model messages and rollback evidence for the next conversation turn, and carry observations across automatic model handoffs without replaying unanswered tool calls.

## 1.17.66

- Apply folder colors only after Save, together with the title and path. Keep color choices inside the form until confirmation and discard unsaved choices on Close or an outside click.

## 1.17.65

- Close a folder editor when clicking outside its form, while preserving the clicked action, keyboard focus, other folder editors and pending history refreshes.

## 1.17.64

- Complete hover, keyboard focus and disabled feedback for the retry button on the window-lock screen, and keep the delete-dialog icon readable in both themes.

## 1.17.63

- Add a red Delete button to the project editor, with an accessible confirmation that explicitly includes all conversations inside the folder. Keep project files, manual notes and conversations assigned elsewhere. Perform deletion outside the extension host and report partial failures accurately.
- Complete button hover and keyboard focus feedback in light and dark themes, preserve disabled states, and fix the action-collapse chevron hover.
- Keep source observations intact while they fit the working context budget; archive older observations only when space is needed.

## 1.17.62

- Treat plain version probes for known development tools as read-only diagnostics. A missing CLI no longer leaves an unresolved mutation that blocks task completion or resumption; shell composition and mutating commands retain their existing checks.

## 1.17.61

- Report pre-existing check failures at the first affected edit, including the actual local host, executable and termination signal; retain diagnostics in the verification plan without repeating them on every write.
- Preserve existing test definitions when editing files, while allowing version changes, formatting, comments and restoration of the original check. Rewritten checks no longer certify their own replacement.
- Add autonomy scenarios for runtime configuration repair and weakened-test detection, with independent file assertions.

## 1.17.60

- Share read-only intent classification between request analysis and execution; preserve scoped file restrictions without turning explicit execution requests into web research.
- Clarify that command timeout arguments use seconds in the native tool schemas.

## 1.17.59

- Pass typed run dependencies in the offline startup benchmark used by release CI.

## 1.17.58

- Pass task services, permissions, usage and execution receipts through typed Pydantic AI run dependencies instead of shared task globals.
- Use native capability hooks and prepared toolsets for execution, tool visibility, retries, streaming and cancellation while retaining recovery journals.
- Isolate concurrent tool events and private delivery state by run; preserve existing model-visible tool schemas.
- Remove duplicate final output validation and cover shared-agent isolation, cancellation, retries and streaming with offline native-runtime tests.

## 1.17.57

- Open conversations in the active project without restarting the backend or scanning its entire archive catalogue.
- Restore action histories with indexed timing lookups, cached date formatters and one final summary per run.
- Restore saved conversation HTML directly from a sanitized DOM fragment and save webview state once.
- Render Markdown only for archived replies and errors, preserving the complete action history.

## 1.17.56

- Preserve the latest user-selected model across catalogue refreshes, conversation restoration, provider restarts and delayed backend replies.
- Persist model choices during archive loading and webview reloads without replacing saved conversation content.

## 1.17.55

- Keep the history view open after selecting a model in the catalogue, including delayed automatic conversation restoration.

## 1.17.54

- Move archive catalogue scans to a worker so slow project storage does not block the VS Code extension host.
- Coalesce archive refreshes and discard results from a previous workspace.
- Restore action histories with one state save and one scroll, avoiding repeated full-history serialization.
- Restore the active project without scanning all its conversations during extension activation.

## 1.17.53

- Highlight the selected catalogue model with a subtle primary-color background that follows the theme.

## 1.17.52

- Select the clicked model and immediately close the catalogue, preserving the selection across delayed replies.

## 1.17.51

- Reduce repeated history input while retaining user corrections and archived observations.
- Pause repeated tool cycles with unchanged results before another model request.
- Persist OVH request usage before task validation and report failed-attempt costs without double counting.

## 1.17.50

- Allow tracked reference-file edits without an environment override, retaining backups and validation.
- Collapse action journals using their bottom chevron, background or outside click, including restored history.
- Allow ⭕ for errors alongside ✅ and ➡️ in default list formatting.

## 1.17.49 — 2026-10-24

- Restore validation-retry assertions against the pinned Pydantic AI message contract so the release pipeline can publish the call-limit and diagnostic presentation changes.

## 1.17.48 — 2026-10-24

- Size diagnostic icons to the surrounding text in live messages, status notices and archived conversations.

## 1.17.47 — 2026-10-24

- Keep the diagnostic icon bundle within its compressed size budget while retaining the bundled license references. Includes the unlimited-call default and warning/error styling from 1.17.46.

## 1.17.46 — 2026-10-24

- Disable the default model-call count ceiling; `TRINITY_AGENT_REQUEST_LIMIT=0` allows unlimited calls.
- Show release blocks with an orange Bootstrap exclamation triangle and errors with a red exclamation diamond, at font weight 500, including archived messages.

## 1.17.44 — 2026-10-24

- Add a collapse chevron button at the bottom of the activity journal block to fold/unfold the action details.

## 1.17.43 — 2026-10-24

- Serve the requested preview screenshot as a versioned Marketplace asset and link the packaged README directly to it.
- Keep the repository preview and export allowlist on the same bundled image; publish this correction as a new version.

## 1.17.42 — 2026-10-24

- Replace the preview screenshot in the repository and extension README with the local image `img/trinity_agent_screenshot.png`.

## 1.17.41 — 2026-10-24

- Correction : `normalise_model()` reçoit maintenant le fournisseur explicite dans `fetch_model_catalog()` pour un marquage cohérent.
- Optimisations diverses `.py` et `.sh` : syntaxe validée sur 162 fichiers Python et 11 scripts shell.

## 1.17.40 — 2026-10-24

- List both API catalogues in the model panel: the active provider's models plus the other provider's, tagged per API and read from its last cache when its key is not loaded, so OVHcloud and DeepSeek models are selectable in one place.
- Switch the inference provider automatically when a model from the other API is selected; a running task keeps its provider until idle, then the restarted backend applies the chosen model before the next message.

## 1.17.39 — 2026-10-24

- Remove the read-only preview dialog: clicking a conversation while another task is generating now records the click and opens that conversation as soon as the task ends, with no popup.
- Stop reading a correction that names or refuses the read-only mechanism ("je ne veux pas de mécanisme de lecture seule", "retire le popup d’aperçu en lecture seule") as a read-only instruction, while an explicit directive ("Lecture seule : audite le dépôt", "sans rien modifier") still blocks writes.

## 1.17.38 — 2026-10-24

- Read a conversation in a read-only dialog while another task is generating; opening it for real waits for the next idle instead of being refused.
- Report the last output line and elapsed seconds every 10 s during a long command instead of only its start and end.
- Render resume continuations as one divider; the full instruction stays in the model history but is no longer archived as a repeated user prompt.
- Keep the documented fallback for files no suite covers (backup plus syntax/content validation) instead of refusing the edit; new files still require a declared suite, and reference check files stay read-only unless `TRINITY_AGENT_ALLOW_PROTECTED_EDITS=1`.
- Name the real provider in the model catalogue panel and its price footer.

## 1.17.37 — 2026-10-24

- Cache the DeepSeek account balance before the first conversation exists, so a refresh no longer reports a cache error next to a valid balance.
- Never replay a model persisted under the other provider: an unavailable selection now routes automatically instead of blocking the next message.
- Name the provider's own image reader and explain its limits instead of reporting a missing OVHcloud model.
- Add a "Choose the inference provider" command and restart an idle backend when `trinityAgent.provider` changes; an active task finishes before the switch.

## 1.17.36 — 2026-10-24

- Add the official DeepSeek provider with separate credentials, catalogue, USD estimates and account balance. Preserve reasoning across tool calls and resumed conversations.
- Disable local inference budgets, request-count/token ceilings, tool-failure cutoffs and model request timeouts for DeepSeek. Remove the SDK's two-retry cutoff; retain provider capacities and file verification.
- Prevent whole-file replacement through `write_file`; existing files use exact edits, preserving unrelated user content. Allow explicit command deadlines for long builds.
- Preserve frozen verification policies on recovery, reject changed reference inputs and keep read-only diagnostics available after historical uncertain operations.
- Fix activity completion, archive recovery, malformed catalogue rows and stale verification state; add regression checks for these failures and the DeepSeek tool protocol.

## 1.17.35 — 2026-10-24

- Keep editing tools available when a request preserves only specific files; explicit global read-only requests still prevent edits.
- Preserve unresolved operations across retries and keep workflow tools available while work is pending.
- Accept recovery from a failed edit only when it had no side effects and the independent acceptance checks pass.
- Avoid running project test suites for static audits and make incomplete-task validation idempotent.
- Load explicitly requested text files as bounded source context and evaluate autonomy scenarios through the production context, tool and validation pipeline.

## 1.17.34 — 2026-10-23

- Add an optional official GEPA experiment using fixed TRINITY file checks, disjoint optimization and final scenarios, and a shared OVH request/cost budget.
- Add a depth-one RLM prototype that explores external document context through isolated Python and bounded subordinate calls, with direct-context comparison and source-quote checks.
- Document both experiments in the repository and extension README. Neither experiment changes chat routing, user permissions, production instructions or model weights automatically.

## 1.17.33 — 2026-10-23

- Deduplicate successfully loaded instruction sources before automatic memory retrieval, while preserving explicit access and skipped-document fallback.
- Distinguish current project files from historical evidence and refresh known Markdown sources immediately after edits.
- Preserve execution tools for explicit build/install requests that prohibit source edits; global read-only requests still block execution.
- Read documentation pages up to a bounded 4 MB and prefer article content over navigation; missing query terms remain unavailable evidence.
- Add regressions for the actual SDK context, cache invalidation, instruction aliases, oversized sources and documentation extraction.

## 1.17.32 — 2026-10-23

- Remove inherited code-server parent IPC identity from command processes so CLI operations can run independently.
- Treat tag and release creation during delivery as execution of existing work, while retaining verification for requested source edits.

## 1.17.31 — 2026-10-23

- Return explicit, structured failures for missing or non-unique edit arguments, with a bounded suggestion for rereading the target.
- Preserve exact-match edits while allowing the tool loop to correct its arguments without consuming SDK exception retries.

## 1.17.30 — 2026-10-23

- Invalidate verification receipts when declared wildcard inputs are added, removed or modified, including changes during a check.
- Preserve existing behavior outside the requested change and reuse successful checks only while their recorded inputs remain unchanged.

## 1.17.29 — 2026-10-23

- Restore model-catalog prices to their previous 10px font size and apply the intended font weight of 500.

## 1.17.28 — 2026-10-23

- Keep command execution and verification tools available for short build, installation and publication requests.
- Treat delivery follow-ups as execution of existing work, without requiring an unrelated source edit.
- Give execution-specific validation feedback instead of prompting the model to rewrite an already completed change.

## 1.17.27 — 2026-10-23

- Escalate automatically from an economical model to gpt-oss-120b when a tool call cannot be serialized and no mutation has started.
- Keep failed edit attempts interrupted instead of recording their explanatory response as completed work.
- Set model-catalog prices to the requested 500px display size.

## 1.17.26 — 2026-10-23

- Update the bundled Markdown renderer to markdown-it 15.0.3 and clear the production dependency audit.

## 1.17.25 — 2026-10-23

- Normalize malformed OVH Harmony channel suffixes before streamed tool calls reach the agent.
- Keep model selection synchronized in the header after routing or a manual model change.
- Reduce permanent prompt and tool-schema overhead while preserving on-demand capabilities.
- Recognize requested outcomes as edits and return actionable feedback for incomplete edit calls.
- Keep light and dark branding assets synchronized with the active editor theme.

## 1.17.24 — 2026-10-23

- Prefix every remaining README section heading with the spaced black-square marker and use the same heading typography throughout.

## 1.17.23 — 2026-10-23

- Use the validated Marketplace asset URL in the locally installed extension details because code-server does not resolve the bundled relative README image path.

## 1.17.22 — 2026-10-23

- Load the bundled preview image directly in the locally installed extension README while keeping the Marketplace-hosted asset for public releases.

## 1.17.21 — 2026-10-23

- Display the exact supplied AI workspace screenshot in both the extension README and the repository README.

## 1.17.20 — 2026-10-23

- Replace the Marketplace preview with the exact user-supplied AI workspace screenshot.

## 1.17.19 — 2026-10-23

- Serve the preview screenshot as an addressable Marketplace asset so VS Code does not depend on an external image host.

## 1.17.18 — 2026-10-23

- Serve the preview screenshot through the Marketplace-compatible repository image path.

## 1.17.17 — 2026-10-23

- Replace the history preview with the requested white-logo screenshot, renamed folders and neutral conversation titles while preserving folder colors.
- Serve the preview from the immutable release tag through jsDelivr so Marketplace and web clients can display it reliably.

## 1.17.16 — 2026-10-22

- Keep the published Marketplace icon purple while switching the locally installed application icon to the white set in dark themes.
- Translate the remaining French sections of the extension README into English.

## 1.17.15 — 2026-10-22

- Preserve the TRINITY symbol cut-outs in the dark VS Code Activity Bar instead of rendering the white logo as a solid hexagon.

## 1.17.14 — 2026-10-22

- Keep the installed extension's Marketplace identity icon purple in every theme while retaining the white dark-theme logo in the application header and VS Code navigation.

## 1.17.13 — 2026-10-22

- Restore the purple logo set for light themes and use the supplied white TRINITY set for dark and high-contrast themes across the header, local application icon and VS Code navigation.
- Add `TRINITY_AGENT_DARK_THEME_ICON` as the shared dark-set branding override while retaining independent light application and navigation overrides.

## 1.17.12 — 2026-10-22

- Replace the preview's baked checkerboard corners with a real antialiased alpha channel while preserving the requested screenshot content.
- Use the supplied white TRINITY mark as the default application icon.
- Persist the six-CPU Docker allocation and default local execution to maximum performance.

## 1.17.11 — 2026-10-22

- Install ripgrep explicitly in release CI so diagnostic-path security tests run against the required trusted system binary.
- Use the requested dark conversation-history preview exactly as supplied in the README.

## 1.17.10 — 2026-10-22

- Keep bounded project text search available on systems without ripgrep while preserving hidden-file, dependency and symlink exclusions.

## 1.17.9 — 2026-10-22

- Preserve automatic project Markdown discovery on systems without ripgrep by using a bounded, symlink-safe filesystem fallback.

## 1.17.8 — 2026-10-22

- Require the exported Git release to be pushed before local or Marketplace installation so README assets always resolve to the matching public version.

## 1.17.7 — 2026-10-22

- Refresh the conversation-history preview with neutral French project and conversation names, a fictional archive path, and a transparent rounded frame.

## 1.17.6 — 2026-10-22

- Keep automatic selection cost-first without a preferred model family. Comparable local measurements and task capacity continue to constrain the eligible economic pool.
- Retry an automatically selected Qwen provider timeout with `gpt-oss-120b` only when the execution journal proves that no mutation occurred, preventing duplicate side effects.

## 1.17.5 — 2026-10-22

- Keep the permanent model prompt bounded: expose the complete Markdown collection through its index instead of repeating every path on every model request.
- Load web-research policy only for requests that need web evidence, and leave environment discovery to the dedicated inspection tool.
- Report token counts by prompt component in request telemetry so future prompt growth is measurable without recording prompt contents.

## 1.17.4 — 2026-10-20

- Proxy every user prompt to an OVH model again. Project Markdown retrieval still prepares authoritative context, but no documentary answer bypasses inference locally.
- Clear stale error and rollback fields after a successful retry while retaining the original failure in the event journal.

## 1.17.3 — 2026-10-20

- Confirm that model-change dividers display the exact TRINITY Agent catalogue identifiers selected by the user, such as `gpt-oss-120b` and `Qwen3.8-27B`; Codex product names are never substituted.

## 1.17.2 — 2026-10-20

- Display a persistent inline divider when the user changes models during an existing conversation, including the previous and next model and a context-preservation explanation.
- Restore model-change dividers in archived conversations without treating automatic routing or initial selection as a manual switch.

## 1.17.1 — 2026-10-20

- Restore automatic project Markdown recall after the 1.17.0 historical-memory filter excluded authoritative documents.
- Read descriptive `.md` files recursively across the selected project while excluding dependencies, generated trees, caches, recovery copies and agent archive storage.
- Keep historical assistant prose excluded and retain exact project-document references for the whole conversation.

## 1.17.0 — 2026-10-20

- Size context, completion, summary and retrieval budgets from the selected model; size caches from available resources. Support explicit numeric ceilings and preserve monetary and execution limits. Show a dynamic Auto context label.
- Recognize French action requests such as “applique” and preserve explicit user preferences while excluding unverified conversation prose from automatic memory retrieval. Full history remains explicitly searchable.
- Activate tools named in task guidance immediately; discover style tools for logo and opacity requests.
- Require fresh syntax evidence for syntax-success claims and report unavailable validators explicitly, including CSS.
- Use comparable evaluation cohorts for measured routing preferences, with matched cases, protocol and settings.
- Preserve the requested 28 px header logo and apply effective 0.5 opacity consistently across themes.
- Extend backend and interface regressions. Deploy the companion backend with this client.

## 1.16.0 — 2026-10-20

- Preserve pending prompts during backend restoration; reconcile stopped follow-ups without duplicate execution, and wait for new-conversation acknowledgement before accepting another prompt.
- Keep project selection valid after a path change. Distinguish active permissions from the preference for the next request; resume with explicit current access and model choices.
- Track refused file changes as incomplete work, accept honest no-change answers, and distinguish diagnostic failures or commands denied before launch from uncertain mutations.
- Respect project access boundaries during automatic retrieval, find readable sources inside explicitly selected Git-ignored installations, and keep controller reminders separate from user requests.
- Treat local diagnostics and audits as workspace tasks; require external research for documentary claims rather than every local error.
- Allow backed-up edits with syntax/content verification when no functional policy exists, while preserving explicit project checks and reporting the missing functional coverage.
- Exclude evaluation conversations consistently from ordinary memory and rebuild search fragments when their source documents change.
- Account for completed local model calls before delayed provider billing catches up; release reservations when preparation fails before any model request. Preserve existing operator limits.
- Rebuild frontend assets and extend offline regressions for these transitions. Update the companion backend together with this client.

## 1.15.4 — 2026-10-19

- Keep the model catalogue open across delayed conversation loads and preserve a newer explicit model selection.
- Use light, transparent catalogue controls while retaining a visible active-model state.
- Restore bundled TRINITY branding when local overrides are absent and merge branding translations without deleting unrelated labels.
- Add bounded interactive multiple-choice questions, including a free-text choice, when the agent genuinely needs user input.

## 1.15.3 — 2026-10-19

- Handle a process disappearing during the cleanup test’s `/proc` read; retain failures for unrelated access errors.
- Includes the Qwen optimizations below. CI stopped candidate 1.15.2 before Marketplace publication.

## 1.15.2 — 2026-10-19

- Apply discovered Qwen capabilities before the first request; distinguish reasoning support from adjustable effort.
- Exclude old thinking blocks from compaction summaries while preserving complete archives and reasoning in active tool rounds.
- Measure model settings, reasoning/cache usage and request latency explicitly in the bounded evaluation runner.

## 1.15.1 — 2026-10-19

- Reject global repository scans consistently on hosts where `/home/coder` does not exist, while retaining strict validation of actual project paths.
- Includes the 1.15.0 changes below. CI stopped that candidate before Marketplace publication; its tag is retained for audit.

## 1.15.0 — 2026-10-19

- Separate command completion, fresh check receipts and independently verified task criteria; show their scope in live and restored conversations.
- Persist tool intentions, uncertain outcomes, user corrections and recovery evidence; reject blind replay and false successful writes.
- Add declared recovery plans for files, services, databases and explicit remote targets, with independent postcondition and rollback probes.
- Activate specialized tool schemas on demand and rank repository symbols before truncating the context.
- Bound command output and child-process lifetime; provide an optional fail-closed workspace sandbox without changing the default access policy.
- Add reproducible adversarial evaluations and a budgeted real-model runner with independent oracles, provenance, tokens, latency and cost reporting.
- Preserve pooled provider connections across text → image → text turns.
- Keep compliant no-op tasks verifiable and invalidate check receipts when observed inputs change. Update the companion backend together with this client.

## 1.14.5 — 2026-10-18

- Remove header background overrides in light and dark themes; use the installed app branding logo while preserving theme opacity.
- Restore interrupted conversations and selected project placement; improve full-width diff highlighting.
- Support external file tools in full-access mode with archived notices, absolute file paths and verified rollback; keep restricted mode boundaries.
- Add hybrid image reading before code reasoning, with separate image-generation intent handling.
- Include accumulated context, budgeting, archive and packaging fixes validated by the offline regression suites.

## 1.14.4 — 2026-10-18

- Extend edit-context isolation to historical reasoning blocks as well as visible assistant text. Preserve exact archived messages and tool protocol; regression covers cross-model reasoning contamination.
- Includes the 1.14.3 fixes below. The 1.14.3 publication workflow was cancelled during final review; 1.14.4 is the replacement candidate.

## 1.14.3 — 2026-10-18

- Recognize French catalogue styling requests as edits and retrieve current workspace sources.
- For edit requests, archive and remove unverified historical assistant prose and legacy summaries from active context; retain user messages and exact tool results. Explicit memory tools remain available.
- Reject summaries containing simulated tool calls/code and reject unsupported test-success claims even when no checks ran. Require a tracked edit or an explicit, truthful no-change outcome for edit requests.
- Search readable sources by default, bound long lines/results and distinguish missing search matches from absent features.
- Restore archive model selection and validate snapshot revisions; show an immediate theme-aware archive loader. Reduce auxiliary summary timeout to 15 seconds and report local fallback/recovery.

- Base budget enforcement on OVH Public Cloud consumption and remaining credit, instead of local token estimates. Requires the companion backend update and configured OVH billing access.
- Release completed and failed request reservations without converting unknown usage into spending. Keep concurrent reservations separate from provider consumption.
- Preserve the legacy ledger for audit; use a separate provider-observation ledger. Daily and weekly limits track observed OVH changes and explicitly mark incomplete history. Monthly consumption comes from OVH.
- Reject unavailable, stale, mismatched-currency or wrong-month billing data; retain the configured budget limits and credit expiry. OVH reporting delay still applies.

## 1.14.2 — 2026-10-18

- Reuse the running backend for billing polls, preserving its cache and explicit forced refresh; correlate replies and reject late results. Update the companion backend with this release.
- Replace repeated partial-frame splitting with bounded fragment assembly; coalesce archive scans and skip Explorer refresh for read-only answers.
- Avoid duplicate final Markdown/DOM work, keep one bounded exact render in memory, and debounce streamed webview-state serialization while saving terminal state immediately.
- Extract protocol, billing, Markdown and webview-document modules; document message flow, cache limits/invalidation and contribution checks in ARCHITECTURE.md.
- Build minified frontend scripts and source maps from readable sources in the isolated VSIX stage. Keep the original sources for debugging and test the generated frontend.

## 1.14.1 — 2026-10-18

- Reject symlinks in archive/storage ancestors before reading, moving or deleting data; read regular storage files through bounded, no-follow descriptors. Private storage must use a real directory, not a symlink. These checks do not sandbox another process running under the same OS account.
- Bound backend frames and diagnostics; recover framing after oversized output, handle stdin errors, and use an escaped preview for unusually large Markdown responses. Storage reads are limited to 32 MiB; full responses remain in backend archives.
- Coalesce streamed Markdown updates over 50 ms, defer syntax highlighting until completion, and bypass grapheme segmentation for prose without emojis.
- Reuse static webview resource URLs while preserving a fresh CSP nonce; index translated labels once. Reject image inputs above 32 MiB before decoding.
- Add regressions for symlink escapes, bounded reads, malformed/oversized streams, ordered final flushing and resource-cache/CSP separation.

## 1.14.0 — 2026-10-18

- Reduce repeated application instructions without removing verification, permission, source-checking or host-isolation rules; keep user instructions intact.
- Make context compaction count protocol units once, preserving the same boundaries, current request and exact archives.
- Avoid redundant tool-request analysis, unused reasoning-setting lookups and repeated routing budget calculations.
- Add an offline cold/warm backend benchmark and regression coverage. Measurements separate fresh application state, restart with existing state and an already-running process; they do not measure provider cold starts.
- Keep installation-specific model preferences, RAM-before-SQLite caching, private instructions and credit policies outside the public release.


- Show a live duration on every activity row, record tool and context timings on the backend, and restore recorded durations from archives; distinguish missing historical measurements and interrupted actions.

- Add persistent Auto/Max local performance selection, live resource resizing, hardware-sized Max caches/readers/search, bounded retrieval memory and retirement of surplus readers; preserve API budgets and verification.

- Reorder pending queued/steering tasks by drag and drop or Alt + arrow keys, persist their order without resuming a stopped queue, and simplify the composer by removing the queue/steer selector; per-task steering and Stop remain available.

- Reorder conversation folders by drag and drop or Alt + arrow keys, move conversations between folders or outside projects, and persist organization with drop indicators and automatic scrolling.

- Search project Markdown and history before workspace retrieval/inference; quote exact documented questions locally with provenance, zero API usage and normal archival, falling back on ambiguity or verification requests.

- Index root project Markdown with source paths, expose memory documents in history and generate a dated verification history without treating old results as current proof.

- Group conversation history into collapsible folders with automatically saved colors, compact controls and Markdown lists collapsed by default. Folder organization does not change conversation context; opening an archive restores its workspace and a new conversation returns to the initial IDE workspace.

- Accept pasted folder paths with Enter in the project selector, with editable validation errors and preserved active project on cancellation.

- Keep archive/model panels below the project label and full header; use the Bootstrap folder-plus icon for project selection.

- Mémoire projet Markdown : notes éditables, vues sourcées, agrégation explicite de plusieurs projets et index lexical local/RAM invalidé lors des changements. Budgets inchangés.

- Projets persistants, création de dossier avec AGENTS.md et rattachement des conversations terminées.
- Profil utilisateur MASTER.md, recherche entre conversations et notes sourcées avec portée utilisateur/projet.
- Inventaire système local/SSH et installations de dépendances selon une politique configurable, budgets inchangés.

- Generate test summaries from execution receipts, preserve unknown counts, reject unsupported totals and deduplicate reruns; expose counts in archived check details.

- Configure emoji levels (0-3), action-specific activity icons, primary cursor and 50% opacity notices; highlight complete diff lines and normalize prose dashes.
- Discover verification commands before editing and reject uncovered/protected writes immediately; add validation checkpoints, sequential mutation barriers and bounded model affinity without increasing budget limits.

- Prioritize official documentation and then repository issues when users report errors or question an answer, including steering; require read-source citations or an explicit incomplete-verification notice, and respect offline requests.
- Require executable verification before accepting tracked backend file changes, with syntax checks, original-file backups and recovery of unverified edits.
- Show actual verification receipts and scoped results in the activity log and conversation archives.
- Include newly added allowlisted backend modules in public source exports; keep local settings, credentials and archives excluded.
- Run backend verification and EUR budget regressions before release packaging. Public budget defaults remain €2/day, €15/week and €60/month.
- Requires updating the companion backend sources alongside the client; the Marketplace extension does not install the backend.

## 1.13.9

- Add European and interface-language flags at the beginning of the README.
- Display the local publisher name as TRINITY while preserving the extension identity.

## 1.13.8

- Correct the pinned checkout action for the local update release package.

## 1.13.7

- Add a separate local release package and configurable Docker automatic updates, with integrity and backend compatibility checks.

## 1.13.6

- Include an edited demonstration screenshot in the Marketplace and local README.
- Align the extension summary with the README features and backend requirements.
- Preserve bundled TRINITY logos when applying default local branding.

## 1.13.5

- Add purple and black square markers to Marketplace README headings.

## 1.13.4

- Enable verified automated Marketplace releases through GitHub Actions.
- Validate publisher authorization and make release retries safe.

## 1.13.3

- Marketplace package uses the stopyz publisher.
- User timestamps appear outside message bubbles; assistant timestamps align left.

## 1.13.2

- Persistent follow-up queue and safe steering of active tasks.
- Queue editing/removal, interruption recovery and four interface languages.
- Softer archive trash icon color.
- Marketplace packaging, dedicated PNG icon and automated release workflow.
- Configurable companion backend launcher; explicit missing-backend guidance.

## 1.13.1

- Self-hosted TRINITY Agent interface, archived conversations, model selection, context compaction, branding and tool approvals.
