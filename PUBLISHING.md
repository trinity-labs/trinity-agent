# Publishing TRINITY Agent

First Marketplace release: **1.13.3**, published on **2026-10-16** under
[`stopyz.ovh-agent`](https://marketplace.visualstudio.com/items?itemName=stopyz.ovh-agent).

## One-time Marketplace connection

The VS Code Marketplace requires a publisher controlled by the maintainer. The
requested public publisher is `stopyz`; the `trinity` ID in the existing
local Docker manifest is retained for compatibility. Neither proves Marketplace ownership.
Do not enable publishing until the publisher ID has been verified.

1. Sign into [Marketplace publisher management](https://marketplace.visualstudio.com/manage)
   with your Microsoft account and create or select your publisher.
2. Recommended: configure a Microsoft Entra application/identity with a GitHub
   federated credential: issuer `https://token.actions.githubusercontent.com`,
   subject `repo:trinity-labs/trinity-agent:environment:marketplace`, audience
   `api://AzureADTokenExchange`. Authorize that identity as a Contributor on the
   Marketplace publisher, following Microsoft's publishing guide.
3. Set GitHub repository variables `MARKETPLACE_PUBLISHER=stopyz`,
   `AZURE_CLIENT_ID` and `AZURE_TENANT_ID`. The workflow uses Azure login with OIDC
   and `vsce publish --azure-credential`; no paid Azure subscription is requested
   by the workflow (`allow-no-subscriptions: true`).
4. Set `MARKETPLACE_ENABLED=true` only once publisher authorization is ready.
   Push a release tag matching the package version, or dispatch the workflow
   against an existing version tag for the first Marketplace upload.

Legacy alternative: create an Azure DevOps PAT with `Marketplace (Manage)` and
all accessible organizations, save it as the GitHub Actions **secret** `VSCE_PAT`,
and set variable `MARKETPLACE_AUTH_MODE=pat`. Run the release workflow manually
on `main` to verify publisher authorization without publishing a new version;
then enable `MARKETPLACE_ENABLED`. Do not paste credentials into chat
or repository files. Microsoft announces retirement of global PATs on December 1,
2026, so federated Entra authentication is the durable choice.

The current npm-published vsce 4.0.0 supports `--azure-credential`; its upstream
main branch documents a direct `--oidc` option not yet present in that published
CLI. This workflow deliberately uses the supported released interface.

The publisher identity becomes part of the extension ID: choose it before the
first Marketplace release. No publisher account, trust policy or external account
terms are created/accepted by this repository's build scripts.

References: [Microsoft publishing guide](https://code.visualstudio.com/api/working-with-extensions/publishing-extension)
and [Azure login with OIDC](https://github.com/Azure/login#login-with-openid-connect-oidc-recommended).

## Automated releases

Every push to `main` runs tests and produces a downloadable VSIX artifact. A tag
`vX.Y.Z` additionally creates a GitHub release and attaches that exact VSIX. With
the Marketplace variables configured, the same artifact is published automatically.
A mismatched version tag fails before publication. The workflow can also be run
manually against a version tag. Marketplace upload may still require Microsoft
validation before the extension appears in searches.

Packaging also requires the offline backend verification and EUR budget suites
to pass, using the same pinned Pydantic AI dependency as the Docker backend.
Public exports include tracked files and new source paths explicitly allowed by
the deny-by-default `.gitignore`; inspect the export before committing it.
`.env`, `ovh-agent.local.sh`, live archives and the live Compose are excluded.
The generated Compose example retains the €2/day, €15/week and €60/month defaults.
The verification module, project check configuration and regression scripts belong
in the companion backend source release. The Marketplace VSIX contains the client,
so existing deployments must update their backend as well to enforce the new checks.

Example for a new version:

```sh
cd ovh-agent-extension/extension
npm version patch --no-git-tag-version
cd ../..
git add ovh-agent-extension/extension/package.json ovh-agent-extension/extension/package-lock.json
git commit -m 'Release new extension version'
git tag vX.Y.Z
git push origin main
git push origin vX.Y.Z
```

Replace `vX.Y.Z` with the version in `package.json`. Use the maintained source/export
workflow for this installation: commit in `docker_ide`, run `scripts/export-public.py`,
commit the public mirror, then tag and push the public mirror.

The GitHub repository is public and uses standard hosted Ubuntu runners. Packaging
and publishing require no paid service plan. OVH inference and server hosting are
separate costs. Codeberg mirrors the sources and tags; the release workflow runs on GitHub.

## Local packaging

```sh
cd ovh-agent-extension/extension
npm ci --ignore-scripts
npm test
npm run package:vsix
```

Output: `dist/trinity-agent-VERSION.vsix`. Optionally set `MARKETPLACE_PUBLISHER` to
the verified publisher. The package builder uses an explicit file allowlist,
installs only runtime dependencies in a temporary staging directory, includes
licenses and bundles the original TRINITY PNG logo. It excludes Docker secrets, local backups,
custom deployment branding, archives, tests and development tools. Original runtime
SVG icons remain bundled for the UI; the Marketplace app icon is PNG.

This release is a client for the companion self-hosted Linux backend, not a
standalone Python installer. That prerequisite is prominent in the Marketplace README.

## Local automatic updates

The Docker entrypoint starts `scripts/auto-update.py --watch`. Set
`OVH_AGENT_AUTO_UPDATE=false` to disable it, or `OVH_AGENT_UPDATE_INTERVAL=21600`
to select the check interval in seconds (minimum 300). Defaults: enabled, six hours.
Run `python3 scripts/auto-update.py` inside the IDE container for an immediate check.
Messages appear in the container log; checks require public GitHub access, no token.

Each tagged release includes a separate `trinity-agent-local-VERSION.vsix` for the
local `trinity.ovh-agent` identity. The Marketplace package remains `stopyz.ovh-agent`.
The updater checks the GitHub SHA-256 digest, package identity, stable version and
backend source hashes before invoking the code-server installer. It rejects
downgrades, reapplies local branding and leaves running editor sessions untouched.
Reload the editor to activate an installed update. Archives and secrets are not
part of the package and are never overwritten.

Only the extension client is updated automatically. A release with changed backend
sources is deferred until the deployment's `ovh-agent.py`, `ovh_memory.py` and
`ovh-agent.sh` match that release. This avoids silently replacing custom backend
code or installing a client for a different backend. Docker images and Python
dependencies remain deployment-managed. Marketplace installations use the editor's
normal extension update mechanism; this local updater never installs a second ID.

### Branding preservation during local updates

The local updater snapshots the installed name, publisher display name and icons
before replacement, then reapplies them to the new installation. Explicit Docker
branding variables take precedence; an explicitly empty icon variable resets that
icon to its bundled default. Missing or invalid configured assets defer the update
instead of silently resetting the branding. Colors remain controlled by the existing
runtime environment. Keep custom asset files on a persistent mount outside the
versioned extension directory. `OVH_AGENT_PUBLISHER_NAME` optionally overrides the
local publisher display name; otherwise the installed value is preserved.

The branding helper only changes installed manifests, never the source manifest.
This preservation mechanism applies to the supplied Docker/local updater, not to
manual edits in an extension directory managed directly by the Marketplace.
