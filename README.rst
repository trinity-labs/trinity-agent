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

``OVH_AGENT_APP_ICON`` selects a local PNG or SVG for the extension list.
``OVH_AGENT_MENU_ICON`` selects a local SVG for the activity bar (prefer a
monochrome 24x24 vector). Paths must be absolute and accessible inside the
container. Images are limited to 1 MiB; SVGs are restricted to static vectors.
Custom images replace the complete icon; their colors are preserved.
The default application icon continues to follow ``OVH_AGENT_PRIMARY_COLOR``.

Place images in the private, Git-ignored ``branding/`` directory beside the
compose file, then set these variables in the compose project's ``.env``::

    OVH_AGENT_APP_ICON=/home/coder/TRINITY-DOCKER/var/docker/docker_ide/branding/app.png
    OVH_AGENT_MENU_ICON=/home/coder/TRINITY-DOCKER/var/docker/docker_ide/branding/menu.svg

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
Application and activity-bar branding remain independently configurable.
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

The locked, atomically written ledger is
``/root/.ovh-agent/budgets/usage-eur.json`` on the persistent mount. It retains
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

Tracking starts when this feature is activated; earlier spending and external
API calls are not retroactively inferred. These are application spending limits
based on catalogue estimates, separate from OVH account quotas and final bills.
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
file per 1000 characters. Old per-chunk files remain readable. An inverted
lexical index is built lazily, reused in RAM and persisted atomically to
``archive/<workspace>/<conversation>/memory/cache/search-v1.json`` under
``OVH_AGENT_DATA_ROOT`` (normally ``/root/.ovh-agent`` on TRINITY-CLOUD).
The derived index is validated against source index file metadata and the
document list; additions, removals and index edits invalidate it. A missing,
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

* ``OVH_AGENT_RAM_CACHE_MB=1024``: estimated retained cache memory per Python
  process (1 GiB), shared across entries, with LRU eviction and at most 512
  entries. Memory is allocated as entries are cached, not reserved at startup.
* ``OVH_AGENT_INDEX_CACHE_MB=32``: maximum serialized derived index size per
  conversation on disk; larger indexes remain searchable without this snapshot.
* ``OVH_AGENT_ARCHIVE_CACHE_MB=16``: estimated metadata cache memory per extension
  archive store, with at most 512 entries.

Setting a limit to zero disables that cache. Limits account for retained cache
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

* ``OVH_AGENT_PREFLIGHT_CONTEXT_CHARS=24000``: total automatic file-excerpt
  allowance, including labels; zero disables automatic file retrieval.
* ``OVH_AGENT_PREFLIGHT_TIMEOUT_MS=2500``: maximum wait for the file-retrieval
  worker. A slow SSHFS mount returns a partial preparation result so the model
  can proceed. Only one such worker runs at a time; late results are discarded.

The file search examines at most 2000 names and reads at most 120 candidates,
up to 65536 characters per file. These bounds deliberately make retrieval
partial; it is a lexical aid, not an exhaustive repository index or semantic
understanding. The activity log reports the selected-file count and preparation
time. No additional inference call is made to perform this preparation;
selected excerpts do contribute to the subsequent model's input tokens.

The local tuning pass keeps the existing EUR limits (2/day, 15/week, 60/month)
and API settings. It does not publish a Marketplace release. Local experimental
settings must not be copied into public release defaults.

Context management and long tasks
---------------------------------

The agent keeps recent Pydantic AI messages, including tool calls and results.
When switching to a model without tool support, historical tool evidence is
replayed as labelled text, with the original protocol archived first.
It no longer discards history after two turns. Before every model request,
including requests inside a tool loop, it estimates the context size and reserves
space for the model output, infrastructure instructions and tool definitions.
The active input budget is capped at 262144 estimated tokens (256k) by default; set
``OVH_AGENT_CONTEXT_TOKENS`` to change this cap. The actual model context window
still bounds it. Token estimates use UTF-8 bytes and a conservative image
allowance, not each model's exact tokenizer; provider validation remains final.

When necessary, completed older tool rounds are archived and replaced by a
short semantic summary using the selected model without tools. Recent protocol
messages, the latest user input and system instructions are retained. Summaries
are lossy and are explicitly labelled historical. A failed, oversized or
truncated summary falls back to exact excerpts with archive references. The
summary call has a 90-second application timeout and its usage is billed and
reported separately. A cancelled or failed HTTP call may still have consumed
provider tokens that are not available in a returned usage record.

Budgets and cache limits are exposed under ``services.vscode.environment`` in
``docker-compose.yml``. The ``${NAME:-default}`` values accept overrides from the
Compose environment or its ``.env`` file. Recreate the ``vscode`` container to
apply changed environment variables; reloading the VS Code window alone does
not update the environment of an existing container.

* ``OVH_AGENT_CONTEXT_TOKENS=262144``: maximum estimated active history tokens.
* ``OVH_AGENT_CONTEXT_RESERVE_TOKENS=4096``: model-window margin for token
  estimation error; replaces the previous fixed 10% reservation.
* ``OVH_AGENT_OUTPUT_TOKENS=16384``: ordinary completion token ceiling.
* ``OVH_AGENT_COMPLEX_OUTPUT_TOKENS=32768``: complex completion token ceiling.
* ``OVH_AGENT_SUMMARY_TOKENS=4096``: summary allowance, including reference overhead;
  also bounded to one fifth of the active context budget.
* ``OVH_AGENT_SUMMARY_SOURCE_CHARS=96000``: summary source character ceiling;
  also bounded according to the active context budget.
* ``OVH_AGENT_SUMMARY_GENERATION_TOKENS=8192``: minimum output allocation requested
  for reasoning-model summaries, subject to the provider and context limits.
* ``OVH_AGENT_SUMMARY_TIMEOUT_SECONDS=90``: summary application timeout.
* ``OVH_AGENT_TOOL_OUTPUT_CHARS=24000``: threshold for archiving a long tool result
  and replacing it with head/tail excerpts and an exact-source reference.
* ``OVH_AGENT_MEMORY_CHARS=8000``: retrieved memory character budget.
* ``OVH_AGENT_REQUEST_LIMIT=200``: model steps per agent run, increased from 50;
  separate summary calls and transport retries are not counted in this limit.

Output reservations are capped by the provider completion limit, one third of
the model window, and space remaining after instructions plus at least 4096
input tokens. Input, output and instructions share the advertised window minus
the configurable token reserve. The 262144 input and 32768 output ceilings are
not both available on a 262144-token model. Smaller models retain their own
limits. Larger ceilings allow longer or more
expensive runs; they do not force the model to generate that many tokens.

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
For cyber tasks, completed laboratory evidence takes precedence over price and
size when at least three cases were reviewed and at least two passed. Within the
best observed group, cost and size break ties. These rules are heuristic and the
three web lab cases do not establish performance on arbitrary exploits.
Model size is a heuristic, not a guarantee of speed or quality. API clients do
not load or change the model weights or quantization deployed by OVH.

Reasoning controls are model-specific: low for short tasks and medium for complex
ones, only if supported. Completion budgets are 16384 / 32768, bounded by the
model limit. Manual model selection remains explicit.

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
