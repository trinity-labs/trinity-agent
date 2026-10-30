const { test } = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const { resolveBackendPath } = require("../backend-path");

test("legacy launchers migrate to an available TRINITY launcher", () => {
  for (const [legacy, replacement] of [
    ["ovh-agent.local.sh", "trinity-agent.local.sh"],
    ["ovh-agent.sh", "trinity-agent.sh"],
  ]) {
    const configured = path.join("/srv/agent", legacy);
    const expected = path.join("/srv/agent", replacement);
    assert.equal(resolveBackendPath(configured, candidate => candidate === expected), expected);
  }
});

test("an existing configured launcher and an unknown missing path stay unchanged", () => {
  assert.equal(resolveBackendPath("/custom/agent", () => true), "/custom/agent");
  assert.equal(resolveBackendPath("/missing/custom", () => false), "/missing/custom");
});
