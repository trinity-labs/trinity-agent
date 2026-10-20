"""Bounded process-local caches; mutable files are validated on every read."""
from collections import OrderedDict
import os
from pathlib import Path
import threading
import time

from ovh_resources import resource_profile


def positive_env(name, default):
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


def memory_index_limits():
    """Dynamic index/document ceilings from the same cgroup-aware RAM policy."""
    profile = resource_profile(CACHE.bytes)
    return profile.index_cache_mb * 1024 * 1024, profile.index_build_bytes, profile.memory_document_chars


class MemoryCache:
    def __init__(self, max_bytes, max_entries=512, adaptive=False):
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.bytes = 0
        self.entries = OrderedDict()
        self.lock = threading.RLock()
        self.hits = self.misses = 0
        self.adaptive = adaptive
        self.next_resize = 0

    def _adapt(self):
        # Called under the cache lock; a single refresh serves concurrent readers.
        if self.adaptive and time.monotonic() >= self.next_resize:
            profile = resource_profile(self.bytes)
            self.max_bytes, self.max_entries = profile.cache_mb * 1024 * 1024, profile.cache_entries
            self.next_resize = time.monotonic() + profile.refresh_seconds
            while self.entries and (self.bytes > self.max_bytes or len(self.entries) > self.max_entries):
                _, removed = self.entries.popitem(last=False)
                self.bytes -= removed[2]

    def refresh_limits(self):
        with self.lock:
            self.next_resize = 0
            self._adapt()

    def get(self, key, signature):
        with self.lock:
            self._adapt()
            entry = self.entries.get(key)
            if entry is not None and entry[0] == signature:
                self.entries.move_to_end(key)
                self.hits += 1
                return entry[1]
            self.misses += 1
            self.discard(key)
            return None

    def discard(self, key):
        with self.lock:
            previous = self.entries.pop(key, None)
            if previous:
                self.bytes -= previous[2]

    def put(self, key, signature, value, size):
        with self.lock:
            self._adapt()
            self.discard(key)
            size = max(1, size)
            if size > self.max_bytes or self.max_entries < 1:
                return
            while self.entries and (self.bytes + size > self.max_bytes or len(self.entries) >= self.max_entries):
                _, removed = self.entries.popitem(last=False)
                self.bytes -= removed[2]
            self.entries[key] = (signature, value, size)
            self.bytes += size

    def clear(self):
        with self.lock:
            self.entries.clear()
            self.bytes = 0


_profile = resource_profile()
CACHE = MemoryCache(_profile.cache_mb * 1024 * 1024, _profile.cache_entries, adaptive=True)


def file_signature(path):
    stat = Path(path).stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def read_text_cached(path, limit=None):
    path = Path(path)
    key = ('text', str(path.absolute()), limit)
    signature = file_signature(path)  # Deleted or replaced files must not hit cache.
    cached = CACHE.get(key, signature)
    if cached is not None:
        return cached
    with path.open(encoding='utf-8') as stream:
        text = stream.read(limit) if limit is not None else stream.read()
    if signature == file_signature(path):
        CACHE.put(key, signature, text, len(text) * 4 + 512)
    return text
