# Changelog

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
- Add `OVH_AGENT_DARK_THEME_ICON` as the shared dark-set branding override while retaining independent light application and navigation overrides.

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

- Confirm that model-change dividers display the exact OVH Agent catalogue identifiers selected by the user, such as `gpt-oss-120b` and `Qwen3.8-27B`; Codex product names are never substituted.

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
