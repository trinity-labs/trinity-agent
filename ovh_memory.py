"""Exact Markdown archives plus bounded lexical retrieval, with no LLM calls."""
import hashlib
import heapq
import json
import math
import os
import re
import tempfile
from pathlib import Path
from ovh_cache import CACHE, file_signature, read_text_cached, memory_index_limits

STOP_WORDS = set('avec dans pour les des une que qui est sur pas plus cette comme mais donc tout tous fait faire peux veut veux nous vous elle elles ils aux du de le la un et en à au ce ces il on je tu se ne ou'.split())
EVIDENCE_EVENT_BYTES = 2 * 1024 * 1024

def terms(text):
    return {word for word in re.findall(r'[\w-]{3,}', text.casefold()) if word not in STOP_WORDS}

def _check_payload(text):
    try:
        event = json.loads(text)
    except (ValueError, TypeError):
        return None
    valid = (isinstance(event, dict) and event.get('type') == 'verification_check'
            and isinstance(event.get('timestamp'), str) and bool(event['timestamp'].strip())
            and isinstance(event.get('check'), str) and bool(event['check'].strip())
            and type(event.get('passed')) is bool and type(event.get('exit_code')) is int
            and event.get('executed') is not False
            and (event['passed'] is False or event['exit_code'] == 0))
    return event if valid else None


def _receipt_key(event):
    # archive_event appends these descriptive annotations to the memory copy,
    # after writing the original controller event. They confer no authority.
    payload = {key:value for key,value in event.items()
               if key not in {'evidence_status','evidence_scope'}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _event_signature(folder):
    folder = Path(folder)
    path = folder / 'events.jsonl'
    try:
        if folder.is_symlink() or path.is_symlink():
            return None
        return file_signature(path)
    except OSError:
        return None


def controller_receipts(folder):
    """Read actual controller events, never infer origin from document content.

    The recent event tail bounds work on long archives. Older evidence remains
    available by explicit historical tools without automatic promotion.
    """
    folder = Path(folder)
    signature = _event_signature(folder)
    if signature is None:
        return frozenset()
    key = ('controller-memory-receipts-v1', str(folder.absolute()))
    cached = CACHE.get(key, signature)
    if cached is not None:
        return cached
    path = folder / 'events.jsonl'
    try:
        with path.open('rb') as stream:
            stream.seek(0, os.SEEK_END)
            start = max(0, stream.tell() - EVIDENCE_EVENT_BYTES)
            stream.seek(start)
            if start:
                stream.readline()  # Discard a possibly partial first event.
            raw = stream.read(EVIDENCE_EVENT_BYTES)
        if _event_signature(folder) != signature:
            return frozenset()
        recorded = frozenset(_receipt_key(event) for line in raw.decode('utf-8', errors='replace').splitlines()
                             if (event := _check_payload(line)) is not None)
    except OSError:
        return frozenset()
    CACHE.put(key, signature, recorded, 512 + len(recorded) * 160)
    return recorded


def execution_evidence(text, folder, recorded=None):
    """Require both a typed check and its exact source in controller events."""
    event = _check_payload(text)
    if event is None:
        return False
    return _receipt_key(event) in (controller_receipts(folder) if recorded is None else recorded)

def atomic_write(path, text):
    fd, temporary = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

class ConversationMemory:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.root = self.folder / 'memory'
        self.docs = self.root / 'documents'
        self.index = self.root / 'index'

    def put(self, text):
        if not text.strip():
            return ''
        digest = hashlib.sha256(text.encode()).hexdigest()
        self.docs.mkdir(parents=True, exist_ok=True)
        self.index.mkdir(parents=True, exist_ok=True)
        document = self.docs / (digest + '.md')
        if document.exists():
            return digest
        # Index first; a document becomes visible only after its complete write.
        chunks = []
        for offset in range(0, len(text), 1000):
            chunks.append({
                'reference': digest, 'offset': offset, 'text': text[offset:offset + 1200],
            })
        atomic_write(self.index / f'{digest}.json', json.dumps(chunks, ensure_ascii=False))
        atomic_write(document, text)
        with (self.root / 'JOURNAL.md').open('a', encoding='utf-8') as stream:
            stream.write(f'- [{digest}](documents/{digest}.md) - {len(text)} caractères\n')
        (self.root / 'JOURNAL.md').chmod(0o600)
        self.write_overview()
        from ovh_project_memory import ProjectMemory
        project=ProjectMemory.for_conversation(self.folder)
        if project: project.changed()
        return digest

    def write_overview(self):
        recent = sorted(self.docs.glob('*.md'), key=lambda p:p.stat().st_mtime, reverse=True)[:6]
        lines = ['# Mémoire de la conversation', '',
                 'Les documents conservent le texte exact ; aucun résumé généré ne remplace les sources.',
                 'Les archives décrivent un état passé. Vérifier les fichiers avant une action.',
                 'Utiliser search_memory pour trouver un passage, puis read_memory avec sa référence et son offset.',
                 'Le JOURNAL.md liste tous les documents. Les instructions récentes priment sur les anciennes.', '',
                 '## Derniers documents', '']
        lines.extend(f'- [{p.stem}](documents/{p.name})' for p in recent)
        atomic_write(self.root / 'CONTEXTE.md', '\n'.join(lines)+'\n')

    def bootstrap(self):
        marker = self.root / '.imported-markdown-v2'
        if marker.exists():
            return
        events = self.folder / 'events.jsonl'
        if events.exists():
            with events.open(encoding='utf-8') as stream:
                for line in stream:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get('type') in {'user', 'response'}:
                        self.put(str(event['type']) + ': ' + str(event.get('text', '')))
                    elif event.get('type') in {'tool_end', 'file_change', 'verification_check'}:
                        self.put(json.dumps(event, ensure_ascii=False))
        legacy = self.folder / 'context-memory.txt'
        if legacy.exists():
            self.put(legacy.read_text(encoding='utf-8'))
        # Retain chunks written by the previous retrieval implementation.
        for path in list(self.root.glob('*.json')):
            try:
                self.put(json.loads(path.read_text(encoding='utf-8'))['text'])
            except (ValueError, KeyError):
                continue
        self.root.mkdir(parents=True, exist_ok=True)
        self.write_overview()
        marker.touch(mode=0o600)

    def read(self, reference, offset=0, length=3000):
        if not re.fullmatch(r'[a-f0-9]{64}', reference):
            raise ValueError('Référence mémoire invalide.')
        path = self.docs / (reference + '.md')
        if path.is_symlink() or not path.is_file():
            raise ValueError('Document mémoire introuvable.')
        if offset < 0 or length < 1:
            raise ValueError('Offset positif et longueur supérieure à zéro requis.')
        text = read_text_cached(path)
        end = min(len(text), offset + min(length, 4000))
        return f'[Document {reference}, caractères {offset}:{end}/{len(text)}]\n' + text[offset:end]

    def excerpt(self, text, budget):
        if len(text) <= budget:
            return text
        reference = self.put(text)
        marker = f'\n[Suite conservée intégralement : read_memory(reference="{reference}", offset={{offset}}). Ne pas déduire le contenu omis.]'
        count = max(0, budget - len(marker) - 12)
        return text[:count] + marker.format(offset=count)

    def search_index(self):
        # Markdown documents are authoritative. Legacy fragment indexes can
        # outlive a source edit and must never resurrect a corrected statement.
        index_cache_bytes, max_index, document_chars = memory_index_limits()
        max_paths = max_index // 512
        paths = heapq.nsmallest(max_paths + 1, (p for p in self.docs.glob('*.md')
                       if not p.is_symlink() and re.fullmatch(r'[a-f0-9]{64}', p.stem)))
        partial = len(paths) > max_paths
        paths = paths[:max_paths]
        stamps = []
        for path in paths:
            try:
                stamps.append((path.name, file_signature(path)))
            except FileNotFoundError:
                pass
        signature = hashlib.sha256(json.dumps(['markdown-v5', stamps, _event_signature(self.folder),
            index_cache_bytes, max_index, document_chars, partial]).encode()).hexdigest()
        key = ('search-index-v5', str(self.root.absolute()))
        cached = CACHE.get(key, signature)
        if cached is not None:
            return cached
        snapshot = self.root / 'cache' / 'search-v1.json'
        entries = None
        try:
            if index_cache_bytes and snapshot.stat().st_size <= min(index_cache_bytes, max_index):
                payload = json.loads(snapshot.read_text(encoding='utf-8'))
                if payload['signature'] == signature and type(payload.get('partial')) is bool:
                    entries = payload['entries']
                    if not isinstance(entries, list) or not all(
                        isinstance(e, dict) and isinstance(e.get('reference'), str)
                        and isinstance(e.get('offset'), int) and isinstance(e.get('text'), str)
                        and isinstance(e.get('execution_evidence'), bool)
                        and isinstance(e.get('tokens'), list) and all(isinstance(t, str) for t in e['tokens'])
                        for e in entries):
                        entries = None
                    else:
                        partial = payload['partial']
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if entries is None:
            entries = []
            recorded = controller_receipts(self.folder) if document_chars and max_index else frozenset()
            used = 256  # JSON envelope and separators.
            for path in paths:
                try:
                    read_limit = min(document_chars, max(0, max_index - used) // 4)
                    if not read_limit:
                        partial = True
                        break
                    before = file_signature(path)
                    raw = read_text_cached(path, limit=read_limit + 1)
                    text = raw[:read_limit]
                    partial = partial or len(raw) > read_limit
                    if before != file_signature(path):
                        partial = True
                        continue
                    observed = len(raw) <= read_limit and execution_evidence(text, self.folder, recorded)
                    for offset in range(0, len(text), 1000):
                        chunk = text[offset:offset + 1200]
                        entry = {'reference': path.stem, 'offset': offset,
                                        'text': chunk, 'tokens': sorted(terms(chunk)),
                                        'execution_evidence': observed}
                        size = len(json.dumps(entry, ensure_ascii=False).encode()) + 2
                        if used + size > max_index:
                            partial = True
                            break
                        used += size
                        entries.append(entry)
                    else:
                        continue
                    break
                except (OSError, ValueError, KeyError, TypeError):
                    partial = True
                    continue
            serialized = json.dumps({'signature': signature, 'entries': entries, 'partial': partial}, ensure_ascii=False)
            try:
                if index_cache_bytes and len(serialized.encode()) <= min(index_cache_bytes, max_index):
                    snapshot.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    atomic_write(snapshot, serialized)
                else:
                    snapshot.unlink(missing_ok=True)
            except OSError:
                pass  # Derived cache failure must not disable archive retrieval.
        inverted = {}
        for index, item in enumerate(entries):
            for word in item['tokens']:
                inverted.setdefault(word, set()).add(index)
        value = (entries, inverted, partial)
        # Conservative accounting for strings, dicts, sets and posting integers.
        size = sum(2048 + 4 * len(e['text']) + sum(256 + 4 * len(t) for t in e['tokens']) for e in entries)
        CACHE.put(key, signature, value, size)
        return value

    def search(self, query, budget=2400, limit=3, *, automatic=False):
        if budget <= 0:
            return ''
        self.bootstrap()
        words = terms(query)
        if not words:
            return ''
        docs, inverted, partial = self.search_index()
        candidates = set().union(*(inverted.get(word, set()) for word in words))
        ranked = []
        for index in candidates:
            item = docs[index]
            if automatic and not item['execution_evidence']:
                continue
            matched = words.intersection(item['tokens'])
            if matched:
                score = sum(math.log(1 + len(docs) / len(inverted[w])) for w in matched)
                ranked.append((score, item['reference'], item['offset'], item['text']))
        result = ('[Recherche mémoire partielle : limite de ressources atteinte ; '
                  'consulter le JOURNAL.md et read_memory pour les sources omises.]\n' if partial else '')[:budget]
        seen = set()
        for _, reference, offset, text in sorted(ranked, reverse=True):
            if text in seen:
                continue
            seen.add(text)
            header = f'\n[Historique : {reference}, offset={offset}; read_memory pour la suite]\n'
            remaining = budget - len(result)
            if remaining <= len(header) + 40 or len(seen) > limit:
                break
            excerpt = text[:remaining-len(header)-25]
            result += header + excerpt + ('\n[Extrait partiel]\n' if len(excerpt)<len(text) else '\n')
        return result
