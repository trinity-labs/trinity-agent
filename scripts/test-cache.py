"""Cache correctness, invalidation, legacy archives and restart recovery, offline."""
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ovh_cache import CACHE, MemoryCache, read_text_cached
from ovh_memory import ConversationMemory, atomic_write

cache = MemoryCache(20, max_entries=2)
cache.put('a', 1, 'one', 10)
cache.put('b', 1, 'two', 10)
assert cache.get('a', 1) == 'one'
cache.put('c', 1, 'three', 10)
assert cache.get('b', 1) is None and cache.bytes == 20
assert cache.get('a', 2) is None and cache.bytes == 10
cache.put('big', 1, 'oversized', 30)
assert cache.get('big', 1) is None

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    source = root / 'instructions.md'
    source.write_text('before')
    assert read_text_cached(source) == 'before'
    with patch.object(Path, 'open', side_effect=AssertionError('warm file should not be reopened')):
        assert read_text_cached(source) == 'before'
    # Same-size edits, atomic replacement, prefix budgets and deletion.
    source.write_text('edited')
    assert read_text_cached(source) == 'edited'
    atomic_write(source, 'atomic')
    assert read_text_cached(source) == 'atomic'
    assert read_text_cached(source, 3) == 'ato'
    source.unlink()
    try:
        read_text_cached(source)
        raise AssertionError('Deleted source was cached')
    except FileNotFoundError:
        pass

    memory = ConversationMemory(root / 'conversation')
    text = 'Verification calyx archive originale.\n' * 200
    reference = memory.put(text)
    assert len(list(memory.index.glob('*.json'))) == 1
    assert memory.read(reference, 0, 4000).endswith(text[:4000])
    first = memory.search('calyx')
    assert reference in first
    snapshot = memory.root / 'cache' / 'search-v1.json'
    assert snapshot.exists() and snapshot.stat().st_mode & 0o777 == 0o600
    with patch('ovh_memory.read_text_cached', side_effect=AssertionError('warm index reread')):
        assert memory.search('calyx') == first
    CACHE.clear()
    with patch('ovh_memory.read_text_cached', side_effect=AssertionError('disk index should survive restart')):
        assert memory.search('calyx') == first
    # Disposable legacy fragments cannot override the authoritative document.
    legacy = memory.index / f'{reference}-legacy.json'
    atomic_write(legacy, json.dumps(dict(reference=reference, offset=0, text='legacytoken')))
    assert memory.search('legacytoken') == ''
    atomic_write(legacy, json.dumps(dict(reference=reference, offset=0, text='changedtoken')))
    assert memory.search('legacytoken') == ''
    assert memory.search('changedtoken') == ''
    legacy.unlink()
    assert memory.search('changedtoken') == ''
    # Corrupt derived cache rebuilds from Markdown, never drops history.
    snapshot.write_text('{broken')
    CACHE.clear()
    assert memory.search('calyx') == first
    document = memory.docs / f'{reference}.md'
    document.write_text(text.replace('calyx', 'lotus'))
    assert memory.search('calyx') == ''
    assert 'lotus' in memory.search('lotus')
    assert 'lotus' in memory.read(reference)
    CACHE.clear()
    assert memory.search('calyx') == '' and 'lotus' in memory.search('lotus')
    atomic_write(document, 'Corrected source with orchid.')
    assert memory.search('lotus') == '' and 'orchid' in memory.search('orchid')
    (memory.docs / f'{reference}.md').unlink()
    assert memory.search('orchid') == ''
    # A second conversation cannot reuse the first conversation's entries.
    other = ConversationMemory(root / 'other')
    other.put('independent memory')
    assert other.search('calyx') == ''
    assert other.search('independent')
    with patch('ovh_memory.INDEX_CACHE_BYTES', 1):
        other.put('another document')
        assert other.search('another')
        assert not (other.root / 'cache' / 'search-v1.json').exists()
print('PASS: bounded LRU, authoritative Markdown edits/replacement/deletion, stale legacy rejection, RAM/disk reuse, corruption recovery, conversation isolation')
