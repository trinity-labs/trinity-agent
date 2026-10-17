"""Exact Markdown archives plus bounded lexical retrieval, with no LLM calls."""
import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from ovh_cache import CACHE, file_signature, positive_env, read_text_cached

STOP_WORDS = set('avec dans pour les des une que qui est sur pas plus cette comme mais donc tout tous fait faire peux veut veux nous vous elle elles ils aux du de le la un et en à au ce ces il on je tu se ne ou'.split())
INDEX_CACHE_BYTES = positive_env('OVH_AGENT_INDEX_CACHE_MB', 32) * 1024 * 1024

def terms(text):
    return {word for word in re.findall(r'[\w-]{3,}', text.casefold()) if word not in STOP_WORDS}

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
            stream.write(f'- [{digest}](documents/{digest}.md) — {len(text)} caractères\n')
        (self.root / 'JOURNAL.md').chmod(0o600)
        self.write_overview()
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
                    elif event.get('type') in {'tool_end', 'file_change'}:
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
        # Validate every source's metadata, including edits made outside this process.
        paths = sorted(p for p in self.index.glob('*.json') if not p.is_symlink())
        documents = sorted(p.stem for p in self.docs.glob('*.md') if not p.is_symlink())
        stamps = []
        for path in paths:
            try:
                stamps.append((path.name, file_signature(path)))
            except FileNotFoundError:
                pass
        signature = hashlib.sha256(json.dumps([stamps, documents]).encode()).hexdigest()
        key = ('search-index-v1', str(self.root.absolute()))
        cached = CACHE.get(key, signature)
        if cached is not None:
            return cached
        snapshot = self.root / 'cache' / 'search-v1.json'
        entries = None
        try:
            if snapshot.stat().st_size <= INDEX_CACHE_BYTES:
                payload = json.loads(snapshot.read_text(encoding='utf-8'))
                if payload['signature'] == signature:
                    entries = payload['entries']
                    if not isinstance(entries, list) or not all(
                        isinstance(e, dict) and isinstance(e.get('reference'), str)
                        and isinstance(e.get('offset'), int) and isinstance(e.get('text'), str)
                        and isinstance(e.get('tokens'), list) and all(isinstance(t, str) for t in e['tokens'])
                        for e in entries):
                        entries = None
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if entries is None:
            entries = []
            available = set(documents)
            for path in paths:
                try:
                    payload = json.loads(read_text_cached(path))
                    chunks = payload if isinstance(payload, list) else [payload]  # Legacy per-chunk index.
                    for item in chunks:
                        if item['reference'] in available and isinstance(item['text'], str) and isinstance(item['offset'], int):
                            entries.append({**item, 'tokens': sorted(terms(item['text']))})
                except (OSError, ValueError, KeyError, TypeError):
                    continue
            serialized = json.dumps({'signature': signature, 'entries': entries}, ensure_ascii=False)
            try:
                if len(serialized.encode()) <= INDEX_CACHE_BYTES:
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
        value = (entries, inverted)
        # Conservative accounting for strings, dicts, sets and posting integers.
        size = sum(2048 + 4 * len(e['text']) + sum(256 + 4 * len(t) for t in e['tokens']) for e in entries)
        CACHE.put(key, signature, value, size)
        return value

    def search(self, query, budget=2400, limit=3):
        self.bootstrap()
        words = terms(query)
        if not words:
            return ''
        docs, inverted = self.search_index()
        candidates = set().union(*(inverted.get(word, set()) for word in words))
        ranked = []
        for index in candidates:
            item = docs[index]
            matched = words.intersection(item['tokens'])
            if matched:
                score = sum(math.log(1 + len(docs) / len(inverted[w])) for w in matched)
                ranked.append((score, item['reference'], item['offset'], item['text']))
        result = ''
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
