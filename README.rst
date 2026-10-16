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

Interface controls use Font Awesome Free (CC BY 4.0 icons), attributed in
``media/FontAwesome.LICENSE.txt``. The build selects 28 solid icons and minifies
a shared renderer; no webfont, CDN or Font Awesome runtime is loaded.
Application and activity-bar branding logos remain independently configurable.
VS Code's native status bar displays the brand name as text, since it does not
support custom SVG icons. Native command icons include light/dark SVG variants.

To rebuild after changing the selection::

    cd ovh-agent-extension/extension
    npm ci
    npm run build:icons
    npm test

Font Awesome and Terser are build-only development dependencies. Generated
assets are versioned and work with production dependencies alone.
