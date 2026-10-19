# Changelog

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
