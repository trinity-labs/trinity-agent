"""Cache correctness, invalidation, legacy archives and restart recovery, offline."""
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trinity_agent.cache import CACHE, MemoryCache, read_text_cached
from trinity_agent.memory import ConversationMemory, atomic_write

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
    with patch('trinity_agent.memory.read_text_cached', side_effect=AssertionError('warm index reread')):
        assert memory.search('calyx') == first
    CACHE.clear()
    with patch('trinity_agent.memory.read_text_cached', side_effect=AssertionError('disk index should survive restart')):
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
    with patch.dict(os.environ, {'OVH_AGENT_INDEX_CACHE_MB':'0'}):
        other.put('another document')
        assert other.search('another')
        assert not (other.root / 'cache' / 'search-v1.json').exists()
    # Index construction and document reads also shrink with available RAM;
    # disabling its disk cache alone must not leave unbounded transient work.
    bounded = ConversationMemory(root / 'bounded')
    for number in range(10):
        bounded.put(f'calyx {number} ' + 'long archive ' * 200)
    bounded.bootstrap()
    with patch('trinity_agent.memory.memory_index_limits', return_value=(0, 4096, 100)), \
         patch('trinity_agent.memory.read_text_cached', wraps=read_text_cached) as reads:
        entries, _, partial = bounded.search_index()
        assert partial and entries and max(len(entry['text']) for entry in entries) <= 100
        assert len(json.dumps(entries).encode()) <= 4096
        assert reads.call_args_list and all(call.kwargs['limit'] <= 101 for call in reads.call_args_list)
        assert 'Recherche mémoire partielle' in bounded.search('absent')
    # Resource changes invalidate a warm index. A zero budget must not read
    # documents, and a partial lookup must not claim the archive was empty.
    with patch('trinity_agent.memory.memory_index_limits', return_value=(0, 0, 0)), \
         patch('trinity_agent.memory.read_text_cached', side_effect=AssertionError('zero budget read')):
        assert bounded.search_index() == ([], {}, True)
        assert 'Recherche mémoire partielle' in bounded.search('calyx')
        assert len(bounded.search('calyx', budget=30)) <= 30
        assert bounded.search('calyx', budget=0) == ''
    assert 'long archive' in bounded.read(entries[0]['reference'])
    with patch('trinity_agent.memory.memory_index_limits', return_value=(0, 8192, 1000)):
        grown, _, _ = bounded.search_index()
        assert max(len(entry['text']) for entry in grown) > 100
    # A truncated document cannot acquire the authority of a complete receipt.
    receipt = {'type':'verification_check','check':'calyx verification','passed':True,'exit_code':0,
               'timestamp':'2026-10-20T19:00:00Z'}
    receipt_text = json.dumps(receipt)
    tail = ConversationMemory(root / 'truncated-receipt')
    tail.put(receipt_text + ' untrusted suffix')
    (tail.folder / 'events.jsonl').write_text(receipt_text + '\n')
    with patch('trinity_agent.memory.memory_index_limits', return_value=(0, 4096, len(receipt_text))):
        assert all(not entry['execution_evidence'] for entry in tail.search_index()[0])
    # Bootstrap old event-only archives without losing executed receipts or
    # turning the accompanying assistant conclusion into automatic evidence.
    archived = root / 'event-only'
    archived.mkdir()
    (archived / 'events.jsonl').write_text('\n'.join(json.dumps(event) for event in [
        {'type':'response','text':'Logo agrandi à 36px et contrôlé.'},
        {'type':'verification_check','check':'logo CSS','passed':False,'exit_code':1,
         'timestamp':'2026-10-20T19:00:00Z'},
    ]))
    imported = ConversationMemory(archived)
    evidence = imported.search('logo', automatic=True)
    assert 'verification_check' in evidence and '36px' not in evidence
    assert '36px' in imported.search('36px')
print('PASS: bounded LRU, authoritative Markdown edits/replacement/deletion, stale legacy rejection, RAM/disk reuse, corruption recovery, conversation isolation')
