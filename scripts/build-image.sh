#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

# code-server's native modules require its declared Node major, which can lag
# the current Node release. Resolve upstream metadata rather than pinning it here.
node_major=$(docker run --rm --pull=always node:alpine node -e '
  const {execFileSync} = require("node:child_process");
  const view = (...args) => JSON.parse(execFileSync("npm", ["view", ...args, "--json"], {encoding: "utf8"}));
  const engines = view("code-server@latest", "engines");
  const versions = view("node@" + engines.node, "version");
  const version = Array.isArray(versions) ? versions.at(-1) : versions;
  console.log(version.split(".")[0]);
')
case "$node_major" in
    ''|*[!0-9]*) echo 'Unable to resolve the Node release required by code-server' >&2; exit 1 ;;
esac
docker compose build --pull --no-cache --build-arg "NODE_IMAGE=node:${node_major}-alpine" vscode
