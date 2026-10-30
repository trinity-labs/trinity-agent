"""Local resource sizing, including container limits; no subprocess or network.

Auto honors numeric environment ceilings. Max replaces the original RAM cache
and workspace reader ceilings with hardware budgets. Explicit archive, index
and memory document ceilings remain in force in both modes. Refreshing does
not allocate the cache.
"""
from dataclasses import asdict, dataclass, replace
import os
import math
from pathlib import Path
import re
import threading
import time

MIB = 1024 * 1024


def _read(path):
    try:
        return Path(path).read_text(encoding='ascii').strip()
    except (OSError, UnicodeError):
        return ''


def _number(text):
    try:
        value = int(text)
        return value if value >= 0 else None
    except (TypeError, ValueError):
        return None


def _cpu_set(text):
    """Count a kernel CPU list without allocating an array for large ranges."""
    ranges = []
    try:
        for item in text.split(','):
            ends = item.split('-')
            lo, hi = int(ends[0]), int(ends[-1])
            if len(ends) > 2 or lo < 0 or hi < lo:
                return None
            ranges.append((lo, hi))
    except ValueError:
        return None
    count, last = 0, -1
    for lo, hi in sorted(ranges):
        count += max(0, hi - max(lo, last + 1) + 1)
        last = max(last, hi)
    return count or None


def _unescape(text):
    return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), text)


def _cgroup_paths():
    memberships = []
    for line in _read('/proc/self/cgroup').splitlines():
        parts = line.split(':', 2)
        if len(parts) == 3:
            memberships.append((set(parts[1].split(',')), Path(parts[2])))
    mounts = []
    for line in _read('/proc/self/mountinfo').splitlines():
        before, separator, after = line.partition(' - ')
        fields, fs = before.split(), after.split()
        if separator and len(fields) >= 5 and len(fs) >= 3 and fs[0] in {'cgroup', 'cgroup2'}:
            mounts.append((Path(_unescape(fields[4])), Path(_unescape(fields[3])), fs))
    # Prefer the process's normal mounts over additional bind mounts of a host.
    mounts.sort(key=lambda row: (not str(row[0]).startswith('/sys/fs/cgroup'), len(str(row[0]))))
    chosen = set()
    for mount, root, fs in mounts:
        if '..' in root.parts:
            continue
        for controllers, member in memberships:
            kinds = {'v2'} if fs[0] == 'cgroup2' and controllers == {''} else (
                controllers & set(fs[2].split(',')) & {'cpu', 'cpuset', 'memory'} if fs[0] == 'cgroup' else set())
            kinds -= chosen
            if not kinds or '..' in member.parts:
                continue
            try:
                leaf = mount / member.relative_to(root)
            except ValueError:
                # Some namespaced setups expose '/' in membership while the
                # bind mount still reports its original hierarchy root.
                if member != Path('/'):
                    continue
                leaf = mount
            chosen.update(kinds)
            current = leaf
            for _ in range(64):
                yield kinds, current
                if current == mount:
                    break
                current = current.parent


@dataclass(frozen=True)
class Hardware:
    cpus: float
    memory_bytes: int
    available_bytes: int


def detect_hardware():
    cpus = float(os.cpu_count() or 1)
    try:
        cpus = min(cpus, len(os.sched_getaffinity(0)) or 1)
    except (AttributeError, OSError):
        pass
    memory = {}
    for line in _read('/proc/meminfo').splitlines():
        key, _, value = line.partition(':')
        parts = value.split()
        number = _number(parts[0]) if parts else None
        if number is not None:
            memory[key] = number * 1024
    # Conservative fallback when procfs is unavailable, without assuming host RAM.
    total = memory.get('MemTotal') or 512 * MIB
    available = memory.get('MemAvailable', memory.get('MemFree', min(total // 4, 128 * MIB)))
    for kinds, directory in _cgroup_paths():
        v2 = 'v2' in kinds
        if v2 or 'cpu' in kinds:
            values = _read(directory / 'cpu.max').split() if v2 else [
                _read(directory / 'cpu.cfs_quota_us'), _read(directory / 'cpu.cfs_period_us')]
            if len(values) == 2:
                quota, period = map(_number, values)
                if quota is not None and period and quota > 0:
                    cpus = min(cpus, quota / period)
        if v2 or 'cpuset' in kinds:
            count = _cpu_set(_read(directory / ('cpuset.cpus.effective' if v2 else 'cpuset.effective_cpus'))
                             or _read(directory / 'cpuset.cpus'))
            if count:
                cpus = min(cpus, count)
        if v2 or 'memory' in kinds:
            usage = _number(_read(directory / ('memory.current' if v2 else 'memory.usage_in_bytes')))
            names = ('memory.max', 'memory.high') if v2 else ('memory.limit_in_bytes',)
            for name in names:
                limit = _number(_read(directory / name))
                if limit is not None:
                    total = min(total, limit)
                    # If usage is unreadable, do not treat the entire limit as free.
                    headroom = max(0, limit - usage) if usage is not None else limit // 4
                    available = min(available, headroom)
    return Hardware(max(.01, cpus), max(0, total), max(0, min(total, available)))


def _ceiling(name, automatic):
    value = _number(os.environ.get(name, '').strip())
    return automatic if value is None else min(value, automatic)


@dataclass(frozen=True)
class ResourceProfile:
    cpu_capacity: float
    memory_mb: int
    available_mb: int
    cache_mb: int
    cache_entries: int
    workers: int
    max_files: int
    file_chars: int
    mode: str = 'auto'
    retrieval_mb: int = 0
    scan_files: int = 2000
    refresh_seconds: int = 30
    index_cache_mb: int = 0
    index_build_bytes: int = 0
    archive_cache_mb: int = 0
    memory_document_chars: int = 0


def _memory_budgets(profile, headroom):
    # A serialized search index expands into strings, dictionaries and postings.
    # Give its live representation at most 1/16 of usable memory, allowing an
    # 8x expansion. These are ceilings; no space is allocated here.
    index_build = max(0, headroom // 128)
    configured = _number(os.environ.get('OVH_AGENT_INDEX_CACHE_MB', '').strip())
    if configured:
        index_build = min(index_build, configured * MIB)
    index_cache = _ceiling('OVH_AGENT_INDEX_CACHE_MB', index_build // MIB)
    # Zero disables the disk snapshot, while bounded transient search remains
    # possible. Document parsing also shares memory with concurrent readers.
    document_chars = _ceiling('OVH_AGENT_MEMORY_DOCUMENT_CHARS',
        min(index_build // 4, max(0, headroom) // (512 * max(1, profile.workers))))
    return replace(profile, index_cache_mb=index_cache, index_build_bytes=index_build,
                   archive_cache_mb=_ceiling('OVH_AGENT_ARCHIVE_CACHE_MB', max(0, headroom) // (64 * MIB)),
                   memory_document_chars=document_chars)


class ResourcePolicy:
    def __init__(self, detect=detect_hardware, interval=30):
        self.detect, self.interval = detect, interval
        self.lock = threading.Lock()
        self.next_refresh = 0
        self.hardware = None
        self.mode = None
        self.snapshot_mode = None
        self.retained_at_snapshot = 0

    def set_mode(self, mode):
        if mode not in ('auto', 'max'):
            raise ValueError('Invalid performance mode')
        with self.lock:
            self.mode = mode
            self.next_refresh = 0

    def profile(self, retained_bytes=0):
        with self.lock:
            now = time.monotonic()
            mode = self.mode or ('max' if os.environ.get('OVH_AGENT_PERFORMANCE_MODE', '').lower() == 'max' else 'auto')
            interval = min(self.interval, 5) if mode == 'max' else self.interval
            if self.hardware is None or now >= self.next_refresh or self.snapshot_mode != mode:
                self.hardware = self.detect()
                self.next_refresh = now + interval
                self.snapshot_mode = mode
                self.retained_at_snapshot = max(0, retained_bytes)
            hw = self.hardware
            # Only credit cache bytes present when available memory was sampled.
            # Crediting newly cached data against an old snapshot double-counts RAM.
            retained = min(self.retained_at_snapshot, max(0, retained_bytes))
        if mode == 'max':
            reserve = min(hw.memory_bytes, max(64 * MIB, hw.memory_bytes // 20))
            usable = max(0, min(hw.memory_bytes - reserve, hw.available_bytes + retained - reserve))
            working = min(usable, max(32 * MIB, math.ceil(hw.cpus) * 8 * MIB, usable // 10))
            workers = max(1, min(math.ceil(hw.cpus), working // (8 * MIB)))
            cache_mb = max(0, usable - working) // MIB
            # Half the working budget retains retrieved chunks, half covers the
            # concurrent parsers (text, tokens, dictionaries and temporary copies).
            chars = working // (2 * workers * 64)
            files = usable // (128 * 1024)
            profile = ResourceProfile(round(hw.cpus, 3), hw.memory_bytes // MIB,
                                   hw.available_bytes // MIB, cache_mb, cache_mb * 1024,
                                   workers, files, chars, mode, working // MIB, files, interval)
            return _memory_budgets(profile, usable)
        reserve = min(256 * MIB, hw.memory_bytes // 10)
        # Account for our existing cache to avoid shrinking just because it filled.
        headroom = max(0, min(hw.memory_bytes, hw.available_bytes + retained) - reserve)
        cache = min(hw.memory_bytes // 5, headroom // 4)
        if hw.available_bytes < reserve:
            cache = min(cache, hw.available_bytes // 8)
        cache_mb = _ceiling('OVH_AGENT_RAM_CACHE_MB', cache // MIB)
        entries = _ceiling('OVH_AGENT_RAM_CACHE_ENTRIES', min(65536, cache_mb * 2))
        workers = max(1, _ceiling('OVH_AGENT_PREFLIGHT_WORKERS',
                                 min(16, max(1, int(hw.cpus)), max(1, hw.available_bytes // (256 * MIB)))))
        # Retrieval still works when cache storage is deliberately disabled.
        memory_mb = cache // MIB
        files = _ceiling('OVH_AGENT_PREFLIGHT_MAX_FILES', min(2000, workers * 120, max(20, memory_mb * 2)))
        chars = _ceiling('OVH_AGENT_PREFLIGHT_FILE_CHARS', min(524288, 65536 * max(1, memory_mb // 128)))
        profile = ResourceProfile(round(hw.cpus, 3), hw.memory_bytes // MIB,
                               hw.available_bytes // MIB, cache_mb, entries, workers, files, chars)
        return _memory_budgets(profile, headroom)


POLICY = ResourcePolicy()


def resource_profile(retained_bytes=0):
    return POLICY.profile(retained_bytes)


def set_performance_mode(mode):
    POLICY.set_mode(mode)


if __name__ == '__main__':
    import json
    print(json.dumps(asdict(resource_profile()), indent=2))
