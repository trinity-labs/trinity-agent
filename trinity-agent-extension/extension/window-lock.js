"use strict";
const net = require("node:net");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

// Linux abstract sockets disappear when their owning extension host exits,
// including crashes. All windows/publisher variants sharing this data root
// use the same address; no stale lock file or PID reuse is involved.
function acquireWindowLock(dataRoot) {
  if (process.platform !== "linux") return Promise.reject(new Error("The companion agent requires a Linux host."));
  const root = fs.existsSync(dataRoot) ? fs.realpathSync(dataRoot) : path.resolve(dataRoot);
  const identity = crypto.createHash("sha256").update(`${process.getuid()}:${root}`).digest("hex").slice(0, 40);
  return new Promise((resolve, reject) => {
    const server = net.createServer(socket => socket.destroy());
    server.once("error", error => error.code === "EADDRINUSE" ? resolve(null) : reject(error));
    server.listen({path: `\0trinity-agent-window-${identity}`}, () => {
      server.unref();
      resolve(server);
    });
  });
}

function releaseWindowLock(server) {
  return new Promise(resolve => server ? server.close(() => resolve()) : resolve());
}

module.exports = {acquireWindowLock, releaseWindowLock};
