TRINITY Agent
=============

Independent AI coding and system-administration assistant for VS Code and
code-server, using OVHcloud AI Endpoints and a local Python tool runner.

This is an independent implementation inspired by coding-assistant workflows.
It is not an OpenAI product and is not affiliated with or endorsed by OpenAI.
This repository does not contain OpenAI/Codex proprietary extension code.
OpenAI, Codex and OVHcloud names and logos belong to their respective owners;
the source-code license does not grant trademark rights.

Features
--------

* Model selection, cost-aware routing and OVHcloud balance display.
* French, English, German and Spanish UI; browser language or Docker override.
* Local conversation archives, deletion and interrupted-task resumption.
* Bounded active context with searchable Markdown memory.
* File tools, shell tools and explicitly configured SSH administration.
* Configurable branding, history display and AI API endpoint.
* HTML sanitization, CSP and validation of webview messages.

Repository layout
-----------------

``ovh-agent-extension/extension/`` contains the VS Code extension and tests.
``ovh-agent.py`` and ``ovh_memory.py`` contain the Python agent and memory layer.
``ovh-agent.sh`` loads credentials from server secret files.
``ovh-branding.cjs`` applies deployment branding before code-server starts.
``Dockerfile`` describes the development container.
``docker-compose.example.yml`` is a parameterized deployment example, not the
private production Compose file. Review its mounts before using it.

Deployment assumptions
----------------------

This first public release is a source export of a running administration IDE,
not a one-click installer. Several backend/SSH paths and the host allowlist
currently follow the TRINITY installation layout. Adapt those settings and the
agent instructions to your own environment. Merely copying the example Compose
is insufficient to install and register the extension in a fresh code-server.

The production layout keeps source files under ``/var/docker/docker_ide`` on
the Docker host and exposes them inside the IDE through its host-root mount.
Install the extension from ``ovh-agent-extension/extension``; its production
Node dependencies are installed with ``npm ci --omit=dev``. The image creates
``/opt/ovh-agent`` and installs the pinned Pydantic AI dependency.
Persistent data directories must exist before starting Docker bind mounts.

The example retains powerful administration mounts, including the host root
and Docker socket. An agent with full access inherits those privileges.
Run only in infrastructure you control and authorize; the IDE is not a sandbox
against a hostile model, user, or local process. HTML filtering does not prevent
prompt injection from influencing an authorized tool run. Use approval mode and
reduce mounts/credentials for less-trusted work.

Configuration
-------------

* ``OVH_AGENT_NAME``: public extension name.
* ``OVH_AGENT_PRIMARY_COLOR``: primary color, ``#RRGGBB``.
* ``OVH_AGENT_LANG``: ``auto``, ``fr``, ``en``, ``de`` or ``es``.
* ``OVH_AGENT_SHOW_MOUNT_POINT``: ``false`` removes the history banner entirely.
* ``OVH_AI_BASE_URL``: AI endpoint base URL.
* ``OVH_API_BASE_URL``: separate billing API base URL, loaded from server secrets.

AI and billing credentials are read from ``/run/secrets`` or server secret
files. Never commit tokens, passwords, auth.json, .env, SSH keys, conversation
archives, model caches, IDE user profiles, or installed extensions.

Development checks
------------------

From ``ovh-agent-extension/extension``::

    npm ci
    npm test
    npm audit --omit=dev

Security tests cover stored HTML attacks, unsafe links, local symlink escapes,
webview message validation and preservation of ordinary conversation formatting.
These tests are targeted regression checks, not a certification or full audit.

Executable verification of agent changes
----------------------------------------

Request analysis distinguishes documentary questions from local diagnostics.
Local code reviews and debugging use inspected files, logs and executable checks.
Questions about external documentation, versions or reported upstream behaviour
require official documentation first, then issues/discussions in the official
repository if needed. Dates, versions and issue status must be checked; a
third-party issue is not a maintainer confirmation. The same distinction applies
to steering messages, with fresh evidence for the new challenge.

The output validator rejects a documentary challenge answered without an official-
documentation search attempt, with search snippets alone, or without citing a
URL actually read during that review. Empty documentation searches require an
issue-search fallback. An unavailable/empty source is not counted as evidence;
if no page can be read, the backend adds an explicit incomplete-verification
notice. An explicit offline instruction disables both web tools for that request.
Search classification expresses a source preference, not a certification of
ownership or relevance: these still require assessment. Detection is heuristic,
with general instructions covering other phrasings. Web content is treated as
untrusted data, and private logs/secrets must not be sent to the search engine.

``edit_file`` and ``write_file`` now preserve the original working file (including
uncommitted user edits), reject invalid Python, JavaScript, JSON, TOML and shell
syntax before writing, and record the exact resulting file hashes. Unsupported
formats are explicitly reported as lacking a syntax check.

Before the first edit, the backend runs the applicable reference checks. Before
accepting the final answer, it runs them again. A passing assertion by the model
does not bypass this gate. With an explicit project policy, a changed file with
no applicable configured check, a failed check, or a stale result prevents completion. Identical invalid edit
attempts are blocked after two failures. Read-only code audits can conclude with
observed test failures; this is distinguished from successful checks.
An audit without configured tests can conclude after actual local inspection,
with an explicit notice that these observations do not verify runtime behaviour.

Configure a trusted project's checks in ``.ovh-agent-checks.json``::

    {
      "version": 1,
      "checks": [{
        "name": "Budget regressions",
        "paths": ["src/budget.py"],
        "argv": ["{python}", "tests/test_budget.py"],
        "protect": ["tests/test_budget.py"],
        "timeout": 120
      }]
    }

``paths`` are project-relative glob patterns; ``argv`` is an argument array,
without implicit shell expansion. ``{python}`` selects the backend interpreter.
``cwd`` defaults to the project root and must remain within it. Timeouts are
between 1 and 300 seconds. Check configuration and protected reference files are
frozen when the project is first observed during a task. Editing these references
does not make an existing verification pass; add a separate regression test
instead of weakening one. A package with a usable ``test`` script defaults to
``bun run test`` when it has a Bun lockfile, otherwise ``npm run test``. Other
projects can use tracked backups and syntax/content verification to start work.
Such receipts explicitly report missing functional coverage; they do not claim
that tests ran. Adding a project policy makes its coverage requirements mandatory.

Checks obey the existing command approval mode, execute locally with the current
user's permissions, and must be chosen to avoid unwanted external effects.
The repository's configured checks use simulated providers and require no paid
model inference. Verification does not alter API budgets, model selection, or
token limits.

Receipts record the command, working directory, exit status, elapsed time and a
bounded output tail. The activity log and archives show these results separately
from model text. State and original file backups live in the conversation's
``verification/<run-id>/`` directory. Failed/interrupted tracked edits are restored
when handled, or recovered on reopening/resuming the conversation after a crash.
Conflicting external content or permission changes are preserved and reported.
There is no repository-wide reset.

This is a correctness gate for file tools, not an operating-system sandbox.
Arbitrary shell/SSH mutations, services, dependencies not observed by the agent,
and semantic claims outside the configured checks are not automatically proven
or rolled back. A green result means only that the recorded checks passed on
the recorded files. Tests themselves must assert the intended behavior; no
mechanism here guarantees that every model diagnosis is correct.

Offline regression suite::

    /opt/ovh-agent/bin/python scripts/test-verification.py

Automatic public backup
-----------------------

The production host runs ``/etc/trinity/sync-docker-git.sh`` daily at 07:30
in the host's timezone. Before syncing this repository, it exports the allowed
source files from the production source tree using ``scripts/export-public.py``.
The live Compose, secrets, conversations and installed dependencies are excluded.
The public mirror is pushed to Codeberg and GitHub without force-pushing.

The export uses a source allowlist in ``.gitignore``. New source files must be
explicitly allowed and added to the local source repository before export.
Both initial and automatic commits use a staged-file credential check. When
cloning for development, enable it with::

    git config core.hooksPath scripts/hooks

The scanner is an additional guard, not a guarantee that every possible secret
format will be recognized. Review staged changes before publishing.

License and third-party notices
-------------------------------

Original project code is licensed under 0BSD (see LICENSE). This license does
not require attribution or publication of modifications. It does not replace
the separate licenses of dependencies or grant rights to third-party marks.


DOMPurify is vendored with its license in
``ovh-agent-extension/extension/media/DOMPurify.LICENSE``.
Other dependencies and their exact versions are recorded in package-lock.json
and the Dockerfile; their licenses continue to apply. Downloaded packages,
including optional development tools, retain their own licenses and ownership.

Custom icons
------------

``OVH_AGENT_APP_ICON`` selects the light-theme installed application and header logo.
``OVH_AGENT_MENU_ICON`` selects the monochrome VS Code navigation logo.
``OVH_AGENT_DARK_THEME_ICON`` selects the application header logo in dark and
high-contrast themes.
Paths must be absolute and accessible inside the container. Images are limited
to 1 MiB; SVGs are restricted to static vectors. Header image colors are
preserved; VS Code colors the navigation asset from its alpha channel. Defaults
use the purple header in light themes and the bundled white mark in dark themes.

Place images in the private, Git-ignored ``branding/`` directory beside the
compose file, then set these variables in the compose project's ``.env``::

    OVH_AGENT_APP_ICON=/home/coder/TRINITY-DOCKER/var/docker/docker_ide/branding/app.png
    OVH_AGENT_MENU_ICON=/home/coder/TRINITY-DOCKER/var/docker/docker_ide/branding/menu.svg
    OVH_AGENT_DARK_THEME_ICON=/home/coder/TRINITY-DOCKER/var/docker/docker_ide/branding/theme-dark.svg

Recreate the service with ``docker compose up -d --no-build vscode`` and reload
the browser. Empty or invalid settings restore the default icons. Content-based
asset filenames refresh the icon cache when a file changes. Custom images are
excluded from the public export, whose manifest uses the bundled defaults.

Interface icons
---------------

Controls use the outline variants of Bootstrap Icons (MIT), except the trash2-fill deletion icon. Only the history
branding logo retains Font Awesome Free hexagon-nodes (CC BY 4.0), at 25% opacity.
The configuration key is rotated 135 degrees with a local vertical mirror so its teeth face down. Licenses are included in media/.
Only the selected SVG vectors are bundled and minified; no webfont or CDN is used.
The locally installed application icon and header switch between the configured
light and dark sets. The published Marketplace package keeps its purple identity.
The Activity Bar uses the supplied white TRINITY logo directly as its alpha mask,
which VS Code tints for the active theme; this preserves its transparent symbol
cut-outs.
Native command icons have light/dark variants; the native status bar uses text.

Rebuild the versioned assets with::

    cd ovh-agent-extension/extension
    npm ci
    npm run build:icons
    npm test

Bootstrap Icons, Font Awesome and Terser are development-only dependencies.

Conversation timestamps
-----------------------

Message, file-change, activity and approval blocks display a small timestamp
at 33% opacity, formatted in the browser locale and timezone. ISO timestamps
are preserved in saved HTML. Older snapshots recover dates from archived events;
when a date is unavailable the interface displays a dash, never the reopening time.
The Stop square becomes filled while a draft contains text or a task is running.


Steering and queued follow-ups
-----------------------------

While a task runs, the composer defaults to **Mettre en attente** (queue).
Select **Réorienter** (steer) to inject a text instruction before the next
model request. The current network call or tool is allowed to finish; steering
does not cancel or roll back a running command. A late steering instruction
becomes the next continuation if the final response has already arrived.

Pending messages can be edited, removed or promoted to steering from the queue.
Escape leaves edit mode while keeping the draft. Queue messages run sequentially
after successful completion. An error or Stop pauses execution; after restarting,
use **Exécuter la file d’attente** explicitly. Stop does not discard pending work.
The existing interrupted-task Resume command remains available separately.

The queue is stored atomically in ``followups.json`` in each conversation's
persistent archive directory, with at most 20 text messages of 16,000 characters
and 400 KB in total. Pending messages consume no API tokens. Images remain
supported for ordinary prompts; queued messages and steering are text-only.
Consumed steering is recorded in the transcript and interrupted-task state.
The controls use the configured branding and French/English/German/Spanish locale.

Offline backend verification (using the runtime Pydantic AI environment)::

    /opt/ovh-agent/bin/python scripts/test-followups.py

Extension UI, bridge validation and sanitization tests::

    cd ovh-agent-extension/extension
    npm test


Estimated remaining credit
--------------------------

The balance indicator subtracts OVH's current project consumption from the
available project credit in the same billing currency. It recomputes this
estimate from each OVH snapshot, without mutating the original credit or
accumulating debits on refresh. Current project consumption can include other
Public Cloud services. Local per-model estimates use native OVH EUR prices and
are not subtracted again from a provider snapshot that already includes them.

Amounts are displayed to two decimal places (cents). The old 0.10 rounding
mechanism has been removed; the backend retains unrounded values.
``OVH_AGENT_BILLING_CACHE_SECONDS=60`` controls the billing cache TTL and the
requested UI refresh interval, and is exposed in Compose. OVH may publish
consumption later. The UI labels the result as an
estimate and shows the OVH update time, stale cache state or calculation errors.
The usage and credit endpoints are separate snapshots; invoice processing can
temporarily affect their alignment. Some credits also have product or validity
restrictions, so this figure is not a guarantee of spendable AI Endpoints credit.

This follows the separation between current usage and credits used for monthly
invoices in the `OVHcloud Public Cloud FAQ <https://www.ovhcloud.com/fr/public-cloud/faq/>`_
and the `OVHcloud Cloud API schema <https://eu.api.ovh.com/1.0/cloud.json>`_.

Daily, weekly and monthly spending limits
----------------------------------------

The Compose variables ``OVH_AGENT_BUDGET_DAILY_EUR=2``,
``OVH_AGENT_BUDGET_WEEKLY_EUR=15`` and ``OVH_AGENT_BUDGET_MONTHLY_EUR=60`` limit
new model requests for the entire installation sharing ``OVH_AGENT_DATA_ROOT``.
They cover all conversations, workspaces and projects, including compaction
requests and every model step in a tool loop. Zero disables the corresponding
limit. Periods are UTC calendar days, weeks starting Monday and calendar months.
An existing ``budgets/policy.json`` overrides these environment defaults and is
reloaded for each model request. Inspect the effective limits in ``budget_status``
before comparing them with Compose; upgrading preserves operator policy.

Before a request, ``BudgetedModel`` atomically reserves a conservative maximum
cost from the model's context/output limits and native OVH EUR prices. It
blocks the request when any period cannot cover that reservation. After a
complete response, reported token usage replaces the reservation with its
actual locally calculated cost. Decimal amounts retain fractional cents and
are added without per-call rounding; only the UI displays two decimal places.
An unknown price or unsupported currency blocks paid requests. Native prices
come from ``metadata.usage_information.pricing`` in the AI Endpoints catalogue,
matching the `official France EUR prices, excluding VAT <https://www.ovhcloud.com/fr/public-cloud/prices/>`_.
There is no exchange-rate service or currency conversion. Historical USD
events remain labelled USD and are not silently reinterpreted as EUR.

The provider-backed, locked and atomically written ledger is
``/root/.ovh-agent/budgets/usage-ovh-eur.json`` on the persistent mount. The legacy
``usage-eur.json`` ledger remains separate. The active ledger retains
90 days of period counters and open reservations. Repeated settlement does
not charge twice. A corrupt or inaccessible ledger blocks requests instead of
resetting counters. Reservations are shared across processes on this IDE host;
the mount must support locking for other deployments to share that guarantee.

An incomplete response, timeout or missing usage conservatively charges the
reservation. A killed process leaves its reservation pending; it does not
expire automatically because OVH may have billed the request. Pending
reservations also count after a period reset; successful settlement is charged
to the UTC period in which it completes. SDK HTTP retries are disabled so a
hidden retry cannot bypass a reservation. Ordinary agent tool/model correction
rounds remain possible and each model request obtains its own reservation.

Local accounting starts when this version first uses the active ledger. Its
opening baseline is the provider spending already observed for each period.
Subsequent completed calls add their locally calculated cost even when OVH has
not yet reported them. The enforced amount is the greater of provider spending
and the durable local counter, plus outstanding reservations. A provider increase
above that counter raises its baseline before more local calls are charged.
The provider balance displayed to the user remains the provider's figure.

``budget_status`` distinguishes ``spent`` (provider), ``local_spent`` (calls
accounted since activation), ``local_opening``, ``provider_adjustment`` and
``accounted_spent`` (the amount used for enforcement). This deliberately
conservative accounting cannot reconstruct unreported calls before activation,
attribute every external service charge, or certify the final OVH invoice.
If provider consumption arrives before a local call settles, the two cannot
always be matched and enforcement can overestimate spending until period reset.
Failed preparation before sending any model request releases its reservation;
uncertain failures after sending retain the conservative charge.

These are application spending limits based on catalogue estimates and observed
provider charges, separate from OVH account quotas and final bills.
The balance stays visible; the separate budget label is hidden. Backend limits
continue to apply to every inference request.

Local performance caches
------------------------

The Python backend shares a bounded LRU cache for unchanged instruction files,
memory documents, catalogue/billing/router files, plan/task state and lexical
search indexes. Every file read validates device/inode, size, modification and
change timestamps before reusing text. No extra freshness delay is added to
the existing catalogue/billing TTLs; filesystem metadata visibility still
depends on the underlying mount. Cached JSON is parsed into fresh objects for
mutable backend state.

New memory documents use one grouped index file per document instead of one
file per 1000 characters. Legacy archives remain readable, while obsolete
index fragments do not override the current Markdown documents. An inverted
lexical index is built lazily, reused in RAM and persisted atomically to
``archive/<workspace>/<conversation>/memory/cache/search-v1.json`` under
``OVH_AGENT_DATA_ROOT`` (normally ``/root/.ovh-agent`` on a persistent mount).
The derived index is validated against the source documents and their metadata;
document edits and deletions invalidate stale excerpts. A missing,
corrupt or unwritable derived cache falls back to rebuilding from the sources.
Deleting a conversation also deletes its derived disk cache.

The extension caches parsed archive metadata, task state and legacy completion
status, validating file metadata on each listing. The current conversation
pointer is read once per listing. Original archives remain the source of truth.
Conversation titles use the first user message without an extra model call.
The first accepted message names an empty conversation immediately, regardless
of how long it has been open, before context preparation or model routing.
The history is refreshed at once and the title survives preparation failures.
Missing, empty or placeholder titles are recovered from archived events;
existing titles are preserved. Metadata writes are atomic and serialized across
backend threads so readers never see a half-written JSON document.

Only one Agent window per Linux user and data directory can own the installation
on a given Linux host/network namespace. Other VS Code windows show an explicit
locked view with a retry button and cannot dispatch actions or delete archives.
Linux abstract sockets release ownership automatically if the extension host
crashes; a separate backend lock also excludes a surviving orphaned process.
Starting a new conversation waits for the previous backend to stop. These locks
do not coordinate separate containers/hosts that merely share a data mount.

Adjustable limits (restart the backend/extension after changing environment):

* ``OVH_AGENT_RAM_CACHE_MB=auto`` and ``OVH_AGENT_RAM_CACHE_ENTRIES=auto``:
  retained cache size and entry count adapt to usable memory, including cgroup
  limits and current usage. A numeric value is an optional upper bound. Cache
  access reevaluates its limits periodically and evicts LRU entries when they
  shrink. Memory is allocated as entries are cached, not reserved at startup.
* ``OVH_AGENT_INDEX_CACHE_MB=auto``: serialized index and transient construction
  budgets follow available memory. Partial indexing is identified; exact source
  documents remain explicitly readable.
* ``OVH_AGENT_ARCHIVE_CACHE_MB=auto``: metadata-cache allowance follows available
  memory and container limits, with periodic eviction when the allowance shrinks.

In Auto mode, setting a limit to zero disables that cache. Limits account for retained cache
data, not total process RSS or temporary allocations during indexing. Existing
large histories are upgraded lazily; source archives are neither deleted nor
duplicated wholesale. Commands, tool side effects and model answers are not
replayed from cache. API secrets are not added to these caches. These changes
accelerate local preparation/retrieval, not the model's token generation rate.

Local request preparation
-------------------------

``ovh_request.py`` classifies the request locally (intent, domain, scope, images
and likely verification needs), without a model call. Short follow-ups can reuse
references from the last four user messages for retrieval. The original user
request is sent unchanged; classification does not change model selection,
reasoning settings, output limits or monetary budgets.

Explicit file paths and recent file-tool paths focus a lexical search within
at most two project directories. The global ``/home/coder`` workspace and mounted
machine roots are not recursively searched by default. Without a known project,
the agent can discover files using its normal tools. Public documentation
questions without a local reference skip the file search.

Files are divided into overlapping line ranges, ranked using identifiers,
request terms and common French/English UI terms. At most eight excerpts are
preloaded, with exact paths and line numbers; previous automatic excerpts are
removed before each user turn. Cached chunks validate file metadata before
reuse. Dependencies, generated directories, common secret filenames and paths
outside the workspace are excluded from automatic retrieval. This filename
filter is not a content-level secret detector. Excerpts are labelled partial,
untrusted data; normal file-reading tools remain available for full inspection.

* ``OVH_AGENT_PREFLIGHT_CONTEXT_CHARS=auto``: total automatic file-excerpt
  allowance, including labels; zero disables automatic file retrieval.
* ``OVH_AGENT_PREFLIGHT_TIMEOUT_MS=2500``: maximum wait for the file-retrieval
  worker. A slow SSHFS mount returns a partial preparation result so the model
  can proceed. Only one such worker runs at a time; late results are discarded.

Auto sizes the candidate and per-file limits to the hardware, with at most
2000 names visited. Max sizes all three limits to available memory instead.
Retrieval remains partial; it is a lexical aid, not an exhaustive repository
index or semantic understanding. The activity log reports the selected-file count and preparation
time. No additional inference call is made to perform this preparation;
selected excerpts do contribute to the subsequent model's input tokens.

Automatic sizing does not change the operator’s EUR policy. Compose supplies
example defaults (2/day, 15/week, 60/month); an existing budget policy remains
authoritative. Local experimental settings must not become public release defaults.

Context management and long tasks
---------------------------------

The agent keeps recent Pydantic AI messages, including tool calls and results.
When switching to a model without tool support, historical tool evidence is
replayed as labelled text, with the original protocol archived first.
It no longer discards history after two turns. Before every model request,
including requests inside a tool loop, it estimates the context size and reserves
space for the model output, infrastructure instructions and tool definitions.
With ``OVH_AGENT_CONTEXT_TOKENS=auto``, the active input budget follows the
selected model’s advertised window after reserving output, instructions, tools
and the estimation margin. An integer adds an explicit ceiling. Unknown model
metadata uses a conservative, labelled fallback rather than an unlimited window. Token estimates use UTF-8 bytes and a conservative image
allowance, not each model's exact tokenizer; provider validation remains final.

When necessary, completed older tool rounds are archived and replaced by a
short semantic summary using the selected model without tools. Recent protocol
messages, the latest user input and system instructions are retained. Summaries
are lossy and are explicitly labelled historical. A failed, oversized or
truncated summary falls back to exact excerpts with archive references. The
summary call has a 15-second application timeout and its usage is billed and
reported separately. A cancelled or failed HTTP call may still have consumed
provider tokens that are not available in a returned usage record.

Budgets and cache limits are exposed under ``services.vscode.environment`` in
``docker-compose.yml``. The ``${NAME:-default}`` values accept overrides from the
Compose environment or its ``.env`` file. Recreate the ``vscode`` container to
apply changed environment variables; reloading the VS Code window alone does
not update the environment of an existing container.

* ``OVH_AGENT_CONTEXT_TOKENS=auto``: active-history allowance derived from the model window.
* ``OVH_AGENT_CONTEXT_RESERVE_TOKENS=4096``: explicit margin for token-estimation error.
* ``OVH_AGENT_OUTPUT_TOKENS=auto`` and ``OVH_AGENT_COMPLEX_OUTPUT_TOKENS=auto``:
  completion allowances based on model capacities and task complexity.
* ``OVH_AGENT_SUMMARY_TOKENS=auto``, ``OVH_AGENT_SUMMARY_SOURCE_CHARS=auto`` and
  ``OVH_AGENT_SUMMARY_GENERATION_TOKENS=auto``: compaction allowances derived from
  the usable context and completion limits.
* ``OVH_AGENT_TOOL_OUTPUT_CHARS=auto``: allowance before archiving a long result
  and returning head/tail excerpts with an exact-source reference.
* ``OVH_AGENT_MEMORY_CHARS=auto`` and ``OVH_AGENT_SHARED_MEMORY_CHARS=auto``:
  retrieval allowances derived from the context, not from the machine’s RAM.
* ``OVH_AGENT_SUMMARY_TIMEOUT_SECONDS=15``: explicit summary timeout.
* ``OVH_AGENT_REQUEST_LIMIT=200``: explicit upper bound on model steps per run;
  separate summary calls and transport retries are not counted in this bound.

For adaptive allowances, ``auto`` or an omitted value computes a finite budget;
an integer supplies an optional lower ceiling. Unknown or malformed explicit
settings do not silently turn into an unlimited allocation. These computed
values are ceilings, not a request to fill the entire context or produce a long
answer. Provider validation and the account’s monetary limits still apply.

The local deployment launcher reloads its supported ``.env`` overrides when a
backend process starts. It accepts the same adaptive values and can replace
stale inherited container limits. A process already running keeps its existing
configuration: finish its task before restarting or reloading its IDE window.
The general Docker deployment still requires container recreation to change its
environment. Numeric execution deadlines, step limits and financial authorizations
are separate policies; they do not represent hardware capacity.

Histories already truncated by older versions cannot reconstruct tool protocol
that was never saved; the existing text archives remain searchable.

Exact pre-compaction protocol snapshots are stored under
``context-checkpoints/``. Long tool output is stored in the existing searchable
memory before shortening its visible portion; ``search_memory`` and
``read_memory`` recover the exact text. Context is saved atomically at model
boundaries for interruption recovery. Archived images remain in protocol
snapshots; text retrieval does not reconstruct their visual content.

``read_file`` reads bounded line ranges and identifies the next range.
``read_webpage`` accepts a character offset and length, archives the fetched
text, and supplies a memory reference for subsequent ranges. ``update_plan``
records up to 12 steps and the current step in ``plan.json``; plans are model
reported progress, not independent proof that a task or its tests succeeded.
The UI displays plan changes, estimated context usage, compaction mode and cost.

An indivisible current request that still exceeds the budget is rejected with
an actionable error instead of being silently truncated. Compaction is a client
feature; it does not expand a model's context window or implement server-side
conversation storage. No gzip or ZIP input is substituted for text tokens.

Verification::

    /opt/ovh-agent/bin/python scripts/test-context.py

This covers complete tool pairs, repeated tool rounds in one user turn,
current-request preservation, exact archives, summary fallback, oversized
prompts, persistent plans, and an actual offline Pydantic AI loop.

OVH inference tuning
--------------------

The cross-provider strategy, official references, local CPU/RAM profile and
offline retrieval measurements are documented in `INFERENCE.rst <INFERENCE.rst>`_.
Short follow-ups retain the original task difficulty for routing and supported
reasoning settings. Local retrieval uses BM25 with complementary excerpts and
a reusable pool of readers; it does not add model calls.

Retrieval defaults to ``auto`` for ``OVH_AGENT_PREFLIGHT_WORKERS``,
``OVH_AGENT_PREFLIGHT_MAX_FILES`` and ``OVH_AGENT_PREFLIGHT_FILE_CHARS``, as do
``OVH_AGENT_RAM_CACHE_MB`` and ``OVH_AGENT_RAM_CACHE_ENTRIES``. Derived indexes,
archive metadata caches and indexed document sizes also support ``auto`` through
``OVH_AGENT_INDEX_CACHE_MB``, ``OVH_AGENT_ARCHIVE_CACHE_MB`` and
``OVH_AGENT_MEMORY_DOCUMENT_CHARS``. Their explicit ceilings (including zero
for caches) remain effective in both Auto and Max. Sizing uses CPU
affinity, cgroup v1/v2 quotas and parent limits, total memory and available memory.
Hardware observations refresh on demand every 30 seconds. Numeric values remain
optional upper bounds; omitted or empty values also select automatic sizing.
The reusable reader pool adjusts concurrency without accumulating pools on slow
mounts. Selected context remains bounded by
``OVH_AGENT_PREFLIGHT_CONTEXT_CHARS=auto``. Inspect the current calculated
profile with ``python ovh_resources.py`` using the backend's environment.

Local Auto / Max performance modes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use **Perf. Auto / Perf. Max** beside the chat access control. The selection is
saved in the VS Code user setting ``ovhAgent.performanceMode`` and applies to
an existing backend without interrupting a task or starting a new model call.
When editing the VS Code setting directly, restart the backend to apply it.
An explicit setting overrides ``OVH_AGENT_PERFORMANCE_MODE=auto|max``; Auto
remains the distribution default. The selected mode and computed resource
allowances are included in backend status and retrieval diagnostics.

Auto honors the five optional numeric CPU/cache/retrieval ceilings. Max ignores
those five ceilings (including zero), removes the fixed 16-reader, 2000-name,
65536-cache-entry and 524288-character caps, and uses the CPU affinity/quota and
available RAM detected on the current host/container. It reserves 5% of effective
memory, at least 64 MiB when possible; roughly 10% of the remaining usable budget
is for retrieval working data (at least 32 MiB and 8 MiB per CPU when available),
and the rest is available for the shared cache. No RAM is allocated merely by
selecting Max. The cache fills on demand; worker threads grow with useful work
and surplus idle readers retire when the effective allowance decreases.

Max refreshes resource observations every five seconds on access. Cached memory
is credited only against the matching hardware snapshot; new cache entries do
not inflate a stale free-memory reading. File results and simultaneous parsers
share the retrieval allowance. The estimates concern retained objects, not a
hard limit on process RSS. OS/cgroup constraints and memory pressure still reduce
allowances, including to zero for scanning/cache on an exhausted host.

Max changes local resource allocation only. API spending budgets, model context
and output limits, access approvals, path exclusions, verification and the
interactive retrieval timeout still apply. More local work can find evidence
outside Auto's scan bounds; it does not make remote model generation faster and
can increase preparation time. Revert to Auto at any time from the same selector.

All chat models share incremental streaming (text batches every 80 ms),
completion limits bounded by the runtime catalog, and first-visible-text/total
latency measurements. A truncated response is marked interrupted, not completed.
Catalog data is cached for five minutes; billing refresh is outside routing.

Discovery metadata comes from the public, unauthenticated Catalog API:
https://catalog.endpoints.ai.ovh.net/rest/v1/models_v2

Discovery slugs, names and aliases are matched to runtime /v1/models IDs.
The discovery service supplies availability, total parameters, quantization,
modalities, tools, reasoning, supported reasoning efforts, response formats,
licence and rate limits. Runtime /v1/models supplies precise token ceilings.
Model costs use native EUR prices from discovery, verified against the official
France price list. Historical USD costs retain their currency; no conversion
is performed. Documented MoE active parameter counts supplement the catalog.
Snapshots and per-model OpenAPI schemas provide fallback information.

All models marked available are considered for automatic routing if their tools,
vision and estimated context fit. Unavailable models are not routed automatically.
The catalog estimate does not replace provider-side context validation.
Measured routing preferences require at least two eligible models from the same
evaluation campaign, with matching cases, protocol and effective settings.
Historical aggregates without this provenance remain readable but do not rank
models or provide a measured-latency claim. Evidence expires after 14 days.
For cyber tasks, completed laboratory evidence takes precedence over price and
size when at least three cases were reviewed and at least two passed. Within the
best observed group, cost and size break ties. These rules are heuristic and the
three web lab cases do not establish performance on arbitrary exploits.
Model size is a heuristic, not a guarantee of speed or quality. API clients do
not load or change the model weights or quantization deployed by OVH.

Reasoning controls are model-specific: low for short tasks and medium for complex
ones, only if supported. Completion budgets adapt to the model window and advertised output limit;
explicit numeric ceilings remain supported. Manual model selection remains explicit.

Documentation audit (2026-10-16):

The complete text and code examples of all 32 pages in the AI Endpoints
navigation section have been read, including tutorials, integrations, the
overview and the linked Hermes guide. The AI Notebooks coding-assistant guide
was read separately. Coverage was checked against the official documentation
repository: all 30 ai-endpoints-*.mdx guides are included; the ai-endpoints
subdirectory contains only the overview. This is not a claim to have read every
external site linked by these guides.

Index: https://docs.ovhcloud.com/en/llms/public-cloud-ai-and-machine-learning/llms.txt

The local audit, source URLs, document hashes, per-page findings and outstanding
integration checks are recorded in
``/home/coder/.diag/ovh-endpoints-docs/audit-20261016.json``.

Core API references:

* https://docs.ovhcloud.com/en/guides/public-cloud/ai-machine-learning/ai-endpoints-catalog-api
* https://docs.ovhcloud.com/en/guides/public-cloud/ai-machine-learning/ai-endpoints-responses-api
* https://docs.ovhcloud.com/en/guides/public-cloud/ai-machine-learning/ai-endpoints-structured-output

The agent retains Chat Completions, which passed the streamed tool loop tests.
Responses needs store=false, client-managed history and custom function tools;
server state, previous_response_id and built-in tools are not supported by OVH.
Its newer route does not itself guarantee lower latency or universal compatibility.
Images are supplied as base64 data, never remote image URLs. Native JSON schema
support is advertised per model; json_object does not enforce an output schema.
JSON schemas must still be validated locally and must not be mistaken for proof
that the report's facts or suggested exploit are correct. No JSON format is
forced on normal chat. JSON schema and streaming combinations on Responses need
separate model-specific validation. The structured-output guide's prose mentions
"streaming", but the wire API parameter is "stream".

Additional findings from the full documentation review:

* Authenticated quotas are per project and model, normally 400 requests/minute.
  The Function Calling tutorial's "No Rate Limit" label contradicts the
  capabilities and troubleshooting guides.
* Request body limits (normally 2 MB, 10 MB for VLMs) are separate from token
  limits. Base64 image data and the full serialized history count toward size.
* ``parallel_tool_calls=false`` is documented as unsupported. MCP integration
  runs in the client application; it is not a built-in Responses tool.
* Mistral Nemo and Small 3.2 remain in their documented retirement grace period
  until 2026-11-04, despite ``available=false`` in discovery. The current UI
  still filters them out; distinguishing manual grace-period access from
  automatic routing remains an identified integration correction.
* Batch is asynchronous, accepts 24/48/72-hour completion windows, and retains
  output/error files for 15 days. It does not share the synchronous service's
  no-storage behavior. Virtual-model queries can change the resolved model,
  price and context; record actual model IDs when using them.
* Some tutorial code is outdated or inconsistent. In particular, verify
  Pydantic AI signatures against the installed SDK and do not copy artificial
  150 ms per-chunk sleeps from the LangChain examples.

Verification:

* python scripts/test-inference.py (offline)
* python scripts/test-followups.py (offline)
* node --test ovh-agent-extension/extension/test/*.test.cjs
* python scripts/evaluate-cyber-models.py --live --output /path/to/report.json

The optional live evaluation generates answers to three synthetic lab prompts
(SQLi, DOM XSS, path traversal), executes no generated code and requires an API
key. It can incur inference charges. Refusals, errors/truncation and technical
correctness must be reviewed separately. A three-case sample is not a universal
"uncensored" score. There is no documented OVH switch guaranteeing zero refusal.

Restart the extension backend after an update to load Python changes.

Affichage et maîtrise des tours d'outils
--------------------------------------

``OVH_AGENT_EMOJI_MODE`` configure la prose et les messages intermédiaires :
Les libellés du journal d’activité restent sans emojis et utilisent les icônes SVG.
0 supprime les emojis décoratifs, 1 autorise uniquement ✅ et ➡️ (défaut),
2 autorise tous les emojis avec parcimonie, 3 demande un usage expressif.
Le code, les commandes et les citations littérales restent intacts. Les modes
2 et 3 diffèrent par la consigne de génération ; leur fréquence n'est pas un quota.
Le backend transmet le mode à la webview, y compris avec le lanceur local.

Le curseur utilise la couleur primary. Les mentions de vérification et le
rappel de responsabilité sont à 50 % d'opacité. Les lignes des diffs portent
leur fond vert/rouge sur toute la largeur, y compris sous le code. Les icônes
suivent l'action (recherche, lecture, édition, commande) et les tirets cadratins
de la prose affichée sont remplacés par des tirets simples.

``verification_plan(path)`` expose les commandes de contrôle, leurs chemins
couverts et les fichiers protégés avant modification. Une écriture sans contrôle
applicable ou sur un contrôle de référence est refusée immédiatement, sans
attendre la validation finale. Les nouveaux scripts ``scripts/test-budget*.py``
sont couverts par les contrôles EUR existants ; leur réussite propre reste à
vérifier séparément, les tests existants ne prouvent pas leur pertinence.

``OVH_AGENT_TASK_CHECKPOINT_SECONDS`` vaut 300 par défaut : un point d'avancement
invite à vérifier et conclure après ce délai, puis un autre à l'approche de la
limite d'appels. Ce délai est une consigne, pas une interruption forcée ni une
garantie de durée. Les limites API et les plafonds EUR ne sont pas augmentés.
Les lectures indépendantes peuvent partager un tour ; écritures, commandes,
planification et vérifications constituent des barrières séquentielles.

Le routage automatique conserve le modèle précédent s'il reste dans le groupe
compatible, respecte l'heuristique de capacité pour les tâches complexes et ne
coûte pas plus de 10 % au-dessus du candidat choisi. Ce critère d'affinité ne
constitue pas une mesure de qualité et n'affecte pas la sélection manuelle.

Références examinées : runtime public Codex
https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/parallel.rs
(parallélisme et exclusion des actions incompatibles), documentation Pydantic AI
https://pydantic.dev/docs/ai/core-concepts/agent/#usage-limits
(limites d'appels). Aucun gain chiffré d'inférence OVH n'est revendiqué sans mesure.

Bilans de tests fondés sur les résultats
--------------------------------------

Le backend décompte les résultats complets reconnus de unittest, node:test et
pytest. Une ligne PASS, un nombre d'assertions ou un résumé tronqué ne fournit
pas un nombre de tests : le décompte reste indisponible. Il s'agit des cas
signalés par les lanceurs (qui peuvent inclure des tests ignorés), pas d'une
mesure de couverture ni nécessairement de tests uniques entre plusieurs suites.

Les contrôles configurés exécutés par ``verify_changes`` ou par ``run_command``
avec exactement leurs arguments et répertoire configurés produisent des preuves
structurées : commande, répertoire, sortie, durée, code de sortie et décompte.
La commande configurée est exécutée directement, sans interprétation shell.
Les autres commandes shell restent des sorties d'outils ; elles ne constituent
pas des preuves de décompte dans ce bilan. Les dernières exécutions remplacent
les anciennes pour le même contrôle. Un contrôle exécuté avant modification
n'est pas ajouté aux résultats finaux. Une modification des fichiers suivis
invalide la preuve ; les dépendances non observées ne sont pas couvertes.

Le tableau final est généré par le backend et conservé dans la conversation.
La syntaxe est distinguée des tests fonctionnels : chaque fichier modifié porte
un reçu sur son contenu final ou un statut de contrôle indisponible. Un format
sans validateur, comme CSS par défaut, ne peut pas être annoncé « syntaxe vérifiée ».
Une commande de validation syntaxique existante peut être déclarée avec
``kind: "syntax"`` dans ``.ovh-agent-checks.json`` ; elle reste soumise aux mêmes
protections et exigences de fraîcheur que les autres contrôles.
Les totaux explicites non justifiés et certaines conclusions absolues sont
refusés par le validateur. Cette détection textuelle reste limitée : elle ne
prouve pas la véracité de toute phrase du modèle. Le tableau des résultats fait
référence ; des contrôles réussis ne prouvent pas l'absence de défauts ailleurs.


Projets, mémoire partagée et environnement
-----------------------------------------

Le bouton projet de l’en-tête et la commande « Choisir ou créer un projet »
permettent de sélectionner un dossier existant ou de créer un dossier avec un
``AGENTS.md`` initial. Le choix est conservé dans l’état du workspace VS Code.
Le dossier actif est transmis au backend, aux commandes et à la recherche de
code. Les conversations restent regroupées par chemin canonique du projet.
Une conversation terminée peut être rattachée à un autre projet depuis ce menu ;
les conversations interrompues doivent d’abord être terminées. Le changement
est bloqué pendant une tâche. Le périmètre autorisé reste ``/home/coder``.

``OVH_AGENT_DATA_ROOT/MASTER.md`` contient les préférences utilisateur explicites,
communes à tous les projets. Le menu projet ou la commande « Modifier le contexte
utilisateur partagé » l’ouvre dans l’éditeur. Le fichier est créé seulement s’il
est absent. Les instructions racine et projet utilisent dans cet ordre de repli
``AGENT.md``, ``AGENTS.md``, ``MASTER.md``, ``master.md``. Les données privées ne
font pas partie de l’export public.

``remember_context`` enregistre des notes datées, sourcées et rattachées à leur
conversation. Les portées sont ``project`` et ``user`` ; les statuts acceptés
sont ``declared`` et ``hypothesis``. Le modèle ne peut pas s’attribuer le statut
vérifié. Les reçus produits par les contrôles backend sont aussi indexés avec
leur date, leur résultat et leur périmètre ; un contrôle réussi est marqué
``verified_at_execution``, sans certifier l’état futur du projet.
Les préférences utilisateur explicitement exprimées peuvent être
partagées partout ; les décisions techniques et archives restent dans le projet.
L’injection automatique conserve uniquement les préférences ``user/declared``
sourcées et les reçus structurés de contrôles exécutés. Elle exclut les anciennes
réponses du modèle, les hypothèses et les transcriptions, même si elles contiennent
une affirmation de réussite. Les préférences restent disponibles pendant une
modification ; les reçus historiques ne prouvent pas l’état actuel.
``search_memory`` recherche aussi les conversations voisines, et
``read_project_memory`` permet de relire la source exacte dans le projet actif.
La suppression d’une conversation supprime également ses notes partagées :
aucune copie permanente globale de ces notes n’est créée.

La recherche automatique sélectionne des extraits, sans appel LLM supplémentaire.
Elle utilise un index du projet borné en documents et en octets, et examine au
plus 100 conversations pour les notes explicites. L’attente est bornée à 1,1 seconde et un unique lecteur
peut rester bloqué sur un montage ; la demande continue alors sans ces extraits.
Cette injection automatique n’est pas exhaustive. L’outil ``search_project_memory``
permet ensuite de parcourir les conversations éligibles du projet par pages de 20,
y compris les plus anciennes ; ``next_offset`` indique la page suivante.
Les archives d’évaluation et les métadonnées invalides sont exclues de ces recherches
et des notes partagées ; une archive d’évaluation reste consultable explicitement.
Les souvenirs sont
des données historiques, pas des instructions prioritaires ni une preuve actuelle.
``OVH_AGENT_SHARED_MEMORY_CHARS=auto`` adapte les extraits partagés au contexte ;
un entier ajoute un plafond, zéro les désactive.

L’inventaire local décrit OS, noyau, architecture, libc, UID, conteneur probable,
écriture du dossier, exécutables et outils installés dans le projet. Il n’exécute
pas les paquets trouvés et ne copie pas les variables d’environnement. Un chemin
exécutable n’est pas une preuve de version ou de bon fonctionnement : vérifier
les versions utiles pendant la tâche. ``OVH_AGENT_INVENTORY_TTL_SECONDS=300``
contrôle son cache ; il est rafraîchi après installation ou commande introuvable.
``inspect_environment(host=...)`` interroge explicitement une cible SSH autorisée,
au lieu de confondre les fichiers montés avec le système qui exécute les commandes.

``install_tools`` utilise des arguments structurés, sans shell : environnements
Python dans ``.agent-tools/venv``, outils npm dans ``.agent-tools/npm`` (scripts npm
d’installation désactivés), ou paquets système via apk/apt-get/dnf disponibles.
Les noms et versions exactes sont autorisés ; URL, chemins et options arbitraires
sont refusés. Les répertoires d’outils et leurs commandes absolues figurent dans
l’inventaire ; ils sont ajoutés au PATH des commandes et vérifications du projet. Les journaux retournent code de sortie, durée et sortie effective.
Il n’y a pas de téléchargement ni d’installation lors de la simple découverte.
Une installation invalide les anciens reçus de vérification de la tâche : les
contrôles doivent être rejoués avant de certifier le nouvel environnement.

``OVH_AGENT_AUTO_INSTALL`` règle l’automatisation :

* ``project`` (défaut) : dépendances nécessaires isolées au projet sans confirmation ;
* ``system`` : autorise aussi les installations système dans l’IDE sans confirmation ;
* ``off`` : passe par le mécanisme habituel d’autorisation.

Le mode Accès complet conserve son comportement : il dispense de confirmation.
Les installations distantes restent des commandes SSH explicitement ciblées,
soumises au mode d’accès. Une installation système peut nécessiter des privilèges,
un index de paquets actualisé et une stratégie de persistance propre à la cible.
Ces réglages ne changent ni les budgets ni les plafonds de requêtes API.


Mémoire Markdown par projet
---------------------------

Les notes durables et les archives restent des fichiers Markdown lisibles.
Aucun appel LLM supplémentaire n'est nécessaire pour leur indexation ou leur
agrégation. Le sélecteur de projet propose « Modifier la mémoire du projet »
et « Agréger les mémoires des projets » (sélection multiple).

Sous ``OVH_AGENT_DATA_ROOT`` :

* ``MASTER.md`` : préférences utilisateur communes aux projets.
* ``memory/projects/<id>/NOTES.md`` : notes manuelles conservées sans réécriture.
* ``archive/<id>/<conversation>/memory/notes/*.md`` : souvenirs structurés,
  avec portée, statut déclaré/hypothèse, source et date dans leur en-tête.
* ``memory/projects/<id>/MEMORY.md`` : vue générée des notes et de six extraits
  récents exacts (1 200 caractères chacun), régénérée après les réponses finales
  et lors d'un export explicite.
* ``memory/projects/<id>/INDEX.md`` : liens vers les sources indexées.
* ``memory/AGGREGATE.md`` : export des seuls projets sélectionnés ; il n'est
  pas injecté automatiquement dans les conversations d'autres projets.

Modifier NOTES.md ou les notes sources, pas les vues générées. Les anciens
``shared-notes.json`` sont importés une fois en Markdown et conservés pour
récupération. Le marqueur ``.migrated`` empêche de réimporter une note supprimée.
Les archives complètes existantes restent dans ``memory/documents/*.md``.
Les souvenirs sont historiques et ne certifient pas l'état actuel du projet.

L'index lexical utilise le cache RAM LRU borné existant et un index JSON gzip
reconstruisible sur disque local : ``OVH_AGENT_MEMORY_INDEX_DIR`` (défaut
``$XDG_CACHE_HOME/ovh-agent/project-memory`` ou ``~/.cache/ovh-agent/project-memory``).
Le Compose expose ce chemin et ``OVH_AGENT_MEMORY_SCAN_SECONDS`` (5 secondes).
Les sources peuvent rester sur le montage partagé tandis que cet index reste
local ; aucun changement du système de fichiers ni déplacement des archives.
Une écriture par l'agent invalide immédiatement l'index. Les modifications
manuelles sont découvertes au prochain balayage ; un extrait dont la source a
changé ou disparu est écarté dès sa prochaine lecture, même avant ce balayage.

``OVH_AGENT_INDEX_CACHE_MB=auto`` dimensionne l’index dérivé selon la mémoire
disponible ; un entier ajoute un plafond en Mio (zéro
supprime sa persistance disque). Le cache RAM reste borné globalement par
``OVH_AGENT_RAM_CACHE_MB``. Les notes passent avant les transcriptions ; au
la taille indexée par document s’adapte au budget disponible
(``OVH_AGENT_MEMORY_DOCUMENT_CHARS=auto``, entier = plafond) et la recherche renvoie
cinq extraits. Une limite atteinte est signalée comme partielle. Les outils de
relecture et de recherche paginée donnent accès aux sources complètes. Le
lecteur partagé conserve son délai d'attente de 1,1 seconde ; une première
indexation lente peut donc bénéficier à la demande suivante.

La suppression d'une conversation ou son rattachement à un autre projet
invalide les index concernés et retire les vues générées et l'agrégat courant.
Les notes manuelles du projet sont préservées. Une copie exportée ailleurs par
l'utilisateur reste indépendante. Les budgets API ne sont pas modifiés.

Export local sans modèle, depuis le dossier du backend::

    python3 ovh_project_memory.py --workspace /chemin/projet-a --workspace /chemin/projet-b

Ajouter ``--data-root /chemin/memoire`` pour une autre installation.


Classement des conversations et documentation du projet
-------------------------------------------------------

L'historique laisse les conversations hors projet par défaut. Le bouton de
classement d'une conversation permet de choisir son projet ou « Aucun projet ».
Seuls les projets enregistrés par l'utilisateur apparaissent en dossiers ;
la découverte d'archives n'en crée pas. Les choix sont conservés séparément
dans ``project-conversations.json``, sans déplacer les archives ni changer
le contexte d'exécution de la conversation. Les anciens rattachements,
identifiés par leur dossier d'origine conservé dans les métadonnées, restent
visibles dans leur projet. Un choix explicite « Aucun projet » est prioritaire
et conservé même pour ces anciens rattachements. Les autres conversations
restent visibles en tête d'historique, sous « Conversations hors projet ».
Les fichiers Markdown du dossier
s'ajoutent automatiquement à la liste à chaque actualisation de l'historique.

Chaque projet se déplie avec un chevron, sans bouton « Ouvrir ». Son icône de
dossier est pleine ; le bouton crayon permet de modifier son titre et sa
couleur, conservés dans ``projects.json`` même après changement de contexte.
Sans couleur personnalisée, le dossier et son sélecteur de couleur reprennent
la couleur primaire de l’interface. Le compteur indique le nombre de conversations
associées au projet, indépendamment du nombre de documents Markdown.

Les archives restent
sur le stockage persistant ``archive/<identifiant-projet>/<conversation>`` ;
elles ne sont pas écrites dans le dépôt de code. Le contexte change avec le
projet lors de l'ouverture d'une conversation. La sélection est conservée par
espace de travail dans ``active-projects.json``.

Les fichiers ``*.md`` directement à la racine du projet sont affichés avec leur
chemin et accessibles dans VS Code. Ils sont aussi indexés par la mémoire du
projet, avec relecture via ``read_project_memory`` et une référence ``root-...``.
Leur contenu reste dans les fichiers d'origine : la vue ``MEMORY.md`` en reprend
au plus 4 000 caractères par document et conserve les liens vers les sources.
Une documentation n'est pas une preuve que le code actuel fonctionne.

``REGRESSIONS.md`` regroupe les 40 contrôles historiques les plus récents des
sources indexées : résultat, date, conversation, code de sortie et source.
Les affirmations des réponses de modèle ne sont pas comptées comme des contrôles.
L'agent peut relire cette vue avec ``read_project_memory("project", "regressions")``.
Ces résultats servent à cibler de nouvelles vérifications, pas à les remplacer.

Un rattachement conserve les messages, pièces jointes, notes et journaux.
Les références actives à des vérifications terminées sont détachées en conservant
leur ancien identifiant et le journal historique : une nouvelle tâche doit
vérifier le nouveau périmètre. Un journal nécessitant encore une restauration
bloque le déplacement. Les anciens plans locaux de reclassement ne sont plus
appliqués automatiquement à l'ouverture de la fenêtre.
