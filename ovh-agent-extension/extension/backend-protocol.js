"use strict";

// Keep partial frames as fragments: appending and splitting the entire pending
// snapshot for every pipe chunk otherwise repeatedly copies the same large text.
// The stream has already decoded UTF-8; this limit is measured in JS characters.
class NdjsonDecoder {
  constructor(limit = 64 * 1024 * 1024) {
    this.limit = limit;
    this.reset();
  }

  reset() {
    this.fragments = [];
    this.blocks = [];
    this.bufferedChars = 0;
    this.discarding = false;
  }

  feed(chunk, diagnostic) {
    const lines = [];
    let start = 0;
    while (start < chunk.length) {
      const boundary = chunk.indexOf("\n", start);
      const end = boundary < 0 ? chunk.length : boundary;
      if (!this.discarding) {
        const length = end - start;
        if (this.bufferedChars + length > this.limit) {
          this.fragments = [];
          this.blocks = [];
          this.bufferedChars = 0;
          this.discarding = true;
          diagnostic({type: "diagnostic", text: "Trame backend trop volumineuse : affichage ignoré."});
        } else if (length) {
          this.fragments.push(chunk.slice(start, end));
          this.bufferedChars += length;
          // Tiny writes must not retain millions of array entries. Join small
          // groups once, without copying the already assembled prefix again.
          if (this.fragments.length === 1024) {
            this.blocks.push(this.fragments.join(""));
            this.fragments = [];
          }
        }
      }
      if (boundary < 0) break;
      if (!this.discarding) lines.push(this.blocks.concat(this.fragments).join(""));
      // A rejected frame is discarded through its newline, never interpreted as
      // a fresh event halfway through. The next complete frame remains usable.
      this.reset();
      start = boundary + 1;
    }
    return lines;
  }
}
module.exports = {NdjsonDecoder};
