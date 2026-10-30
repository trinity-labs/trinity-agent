const path = require("path");

const LEGACY_LAUNCHERS = new Map([
  ["ovh-agent.local.sh", "trinity-agent.local.sh"],
  ["ovh-agent.sh", "trinity-agent.sh"],
]);

function resolveBackendPath(configured, existsSync) {
  const selected = String(configured || "").trim();
  if (!selected || existsSync(selected)) return selected;
  const replacement = LEGACY_LAUNCHERS.get(path.basename(selected));
  if (!replacement) return selected;
  const migrated = path.join(path.dirname(selected), replacement);
  return existsSync(migrated) ? migrated : selected;
}

module.exports = { resolveBackendPath };
