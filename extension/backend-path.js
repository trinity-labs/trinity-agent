const path = require("path");

const LAUNCHERS = new Map([
  ["trinity-agent.local.sh", "scripts/trinity-agent.local.sh"],
  ["trinity-agent.sh", "scripts/trinity-agent.sh"],
]);

function resolveBackendPath(configured, existsSync) {
  const selected = String(configured || "").trim();
  if (!selected || existsSync(selected)) return selected;
  const replacement = LAUNCHERS.get(path.basename(selected));
  if (!replacement) return selected;
  const migrated = path.join(path.dirname(selected), replacement);
  return existsSync(migrated) ? migrated : selected;
}

module.exports = { resolveBackendPath };
