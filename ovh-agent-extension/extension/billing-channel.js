"use strict";
const {randomUUID} = require("node:crypto");

// The backend handles billing on its own thread and already enforces its TTL.
// Reusing that channel avoids Python/SDK startup and forced API refresh on polls.
// Correlation IDs prevent a late timed-out reply from completing the next poll.
class BillingChannel {
  constructor(timeoutMs = 30000) {
    this.timeoutMs = timeoutMs;
  }

  request(send, force = false) {
    if (this.pending) return this.pending.promise;
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    const timer = setTimeout(() => this.cancel(new Error('L’API de facturation ne répond pas.')), this.timeoutMs);
    const id = randomUUID();
    this.pending = {promise, resolve, reject, timer, id};
    try {
      send({type: 'billing_status', request_id: id, refresh: force, silent: !force});
    } catch (error) {
      this.cancel(error);
    }
    return promise;
  }

  receive(message) {
    if (!this.pending || message.request_id !== this.pending.id) return false;
    const pending = this.pending;
    this.pending = undefined;
    clearTimeout(pending.timer);
    pending.resolve(message);
    return true;
  }

  cancel(error = new Error('Canal de facturation interrompu.')) {
    if (!this.pending) return;
    const pending = this.pending;
    this.pending = undefined;
    clearTimeout(pending.timer);
    pending.reject(error);
  }
}
module.exports = {BillingChannel};
