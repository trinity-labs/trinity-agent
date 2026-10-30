#!/usr/bin/env sh
set -eu

if [ -z "${PASSWORD:-}" ] && [ -r /run/secrets/PASSWORD_VSCODE ]; then
  PASSWORD="$(cat /run/secrets/PASSWORD_VSCODE)"
  export PASSWORD
fi

if [ "$#" -eq 0 ]; then
  set -- code-server
elif [ "${1#-}" != "$1" ]; then
  set -- code-server "$@"
fi

if [ "${CODE_SERVER_DISABLE_BUILTIN_GIT:-1}" = "1" ]; then
  DISABLED_BUILTIN_EXTENSIONS_DIR="/tmp/code-server-disabled-builtin-extensions"
  mkdir -p "$DISABLED_BUILTIN_EXTENSIONS_DIR"
  for BUILTIN_EXTENSIONS_DIR in \
    /usr/local/lib/node_modules/code-server/lib/vscode/extensions \
    /usr/lib/code-server/lib/vscode/extensions; do
    [ -d "$BUILTIN_EXTENSIONS_DIR" ] || continue
    # Keep github-authentication: account services await this provider even when Git is disabled.
    for BUILTIN_EXTENSION in git github; do
      if [ -d "$BUILTIN_EXTENSIONS_DIR/$BUILTIN_EXTENSION" ]; then
        mv "$BUILTIN_EXTENSIONS_DIR/$BUILTIN_EXTENSION" \
          "$DISABLED_BUILTIN_EXTENSIONS_DIR/$BUILTIN_EXTENSION" 2>/dev/null || true
      fi
      if [ -d "$BUILTIN_EXTENSIONS_DIR/$BUILTIN_EXTENSION.disabled" ]; then
        mv "$BUILTIN_EXTENSIONS_DIR/$BUILTIN_EXTENSION.disabled" \
          "$DISABLED_BUILTIN_EXTENSIONS_DIR/$BUILTIN_EXTENSION.disabled" 2>/dev/null || true
      fi
    done
  done
fi

# Apply the public extension name before code-server scans its manifests.
node /home/coder/TRINITY-DOCKER/var/docker/docker_ide/branding/trinity-branding.cjs

# One updater per container; installs on disk without interrupting the editor.
python3 /home/coder/TRINITY-DOCKER/var/docker/docker_ide/scripts/auto-update.py --watch &

# Optional private relay: durable queue, one supervisor, no public-package dependency.
trinity_relay=/home/coder/TRINITY-DOCKER/var/docker/docker_ide/.local-agent/relay.py
if [ -f "$trinity_relay" ] && [ -f "${trinity_relay%/*}/relay-state/enabled" ]; then
  /opt/trinity-agent/bin/python "$trinity_relay" --watch >/dev/null 2>&1 &
fi

exec "$@"
