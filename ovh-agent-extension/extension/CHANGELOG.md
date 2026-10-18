# Changelog

## Unreleased

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
