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

# Watch the reported Codex backend regression without changing installed versions.
python3 /home/coder/TRINITY-DOCKER/var/docker/docker_ide/scripts/watch-github-issue.py &

# UnyCloud owns its dependency schedule and verified deployment transaction.
UNYCLOUD_WATCH=/home/coder/TRINITY-DOCKER/var/docker/unycloud/watch-unycloud.sh
if [ -x "$UNYCLOUD_WATCH" ]; then
  "$UNYCLOUD_WATCH" start || echo "UnyCloud watcher failed to start" >&2
fi

for UNYPORT_WATCH in \
  /home/coder/TRINITY-DOCKER/var/docker/docker_unyport/watch-unyport.sh \
  /home/coder/TRINITY-DOCKER/var/docker/docker_demo/watch-demo.sh \
  /home/coder/TRINITY-DOCKER/var/docker/unyport-corporate/watch-corporate.sh; do
  if [ -x "$UNYPORT_WATCH" ]; then
    "$UNYPORT_WATCH" start || echo "UnyPort watcher failed to start: $UNYPORT_WATCH" >&2
  fi
done


exec "$@"
