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
    for BUILTIN_EXTENSION in git github github-authentication; do
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
node /home/coder/TRINITY-DOCKER/var/docker/docker_ide/ovh-branding.cjs

exec "$@"
