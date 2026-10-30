"""Read-only repository maps with bounded scope and independently fresh evidence.

This is a source index, not a compiler or a call graph. Python definitions use
AST ranges; JavaScript/TypeScript use a labelled lexical fallback. Source bytes
are re-read before using the parse cache: unchanged timestamps alone cannot make
an old observation current. No source text, literal values, or shell is emitted.
"""
from __future__ import annotations

import ast
from collections import Counter, deque
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat

from trinity_agent.cache import CACHE


SOURCE_EXTENSIONS = {'.py', '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx'}
EXCLUDED = {
    'node_modules', 'vendor', 'venv', 'env', '__pycache__', 'dist', 'build',
    'coverage', 'target', 'archive', 'archives', 'conversations', 'relay-state',
    'local-recovery', 'local-release-review', 'overlay2', 'overlayfs',
    'proc', 'sys', 'dev', 'run', 'secrets', 'credentials',
}
SECRET_NAME = re.compile(r'(?:^\.env(?:\.|$)|secret|credential|password|private.?key|^id_rsa|^id_ed25519)', re.I)
GENERATED_NAME = re.compile(r'(?:\.min\.|\.bundle\.|\.generated\.|\.d\.ts$|\.map$)', re.I)
MAX_FILE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_ENTRIES = 4096
MAX_DIRECTORIES = 128
MAX_DEPTH = 12
MAX_SYMBOLS = 128
MAX_DEPENDENCIES = 64
# Query discovery may inspect more files than it returns, but never the entire
# repository by accident. This is especially important for SSHFS workspaces.
MIN_QUERY_FILES = 64
MAX_QUERY_FILES = 256
_JS_TOKENS = re.compile(
    r'(?P<comment>//[^\n]*|/\*[\s\S]*?\*/)|'
    r'(?P<string>"(?:\\[\s\S]|[^"\\])*"|\'(?:\\[\s\S]|[^\'\\])*\'|`(?:\\[\s\S]|[^`\\])*`)|'
    r'(?P<word>[A-Za-z_$][\w$]*)|(?P<punct>[^\s])'
)


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _permitted(parts):
    return bool(parts) and all(
        part not in {'.', '..'} and not part.startswith('.')
        and part.casefold() not in EXCLUDED and not SECRET_NAME.search(part)
        for part in parts
    ) and not GENERATED_NAME.search(parts[-1])


def _relative(root, raw):
    path = Path(raw)
    if path.is_absolute():
        try:
            path = path.relative_to(root)
        except ValueError:
            return None
    parts = path.parts
    return parts if _permitted(parts) else None


def _open_directory(root_fd, parts):
    """Directory descriptors prevent a replaced ancestor symlink escaping scope."""
    descriptor = os.dup(root_fd)
    try:
        for part in parts:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_fd
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _signature(metadata):
    return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)


def _source_bytes(root_fd, parts):
    directory = _open_directory(root_fd, parts[:-1])
    try:
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                return None, 'not_regular'
            if before.st_size > MAX_FILE_BYTES:
                return None, 'file_size_limit'
            with os.fdopen(os.dup(descriptor), 'rb') as stream:
                content = stream.read(MAX_FILE_BYTES + 1)
            after = os.fstat(descriptor)
            named = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
            if len(content) > MAX_FILE_BYTES:
                return None, 'file_size_limit'
            if _signature(before) != _signature(after) or _signature(after) != _signature(named):
                return None, 'changed_during_read'
            return content, ''
        finally:
            os.close(descriptor)
    finally:
        os.close(directory)


def _inventory(root_fd, requested, coverage, reasons):
    pending = deque(requested)
    visited, found = set(), set()
    while pending:
        parts = pending.popleft()
        if parts in visited:
            continue
        visited.add(parts)
        if len(parts) > MAX_DEPTH:
            reasons.add('depth_limit')
            continue
        if coverage['directories_scanned'] >= MAX_DIRECTORIES:
            reasons.add('directory_limit')
            break
        try:
            directory = _open_directory(root_fd, parts)
        except (NotADirectoryError, OSError):
            # Explicit files are read later through the same no-follow boundary.
            if parts and Path(parts[-1]).suffix.lower() in SOURCE_EXTENSIONS:
                found.add(parts)
            else:
                coverage['unavailable'] += 1
                reasons.add('unavailable_path')
            continue
        coverage['directories_scanned'] += 1
        try:
            children = []
            with os.scandir(directory) as entries:
                for entry in entries:
                    if coverage['entries_scanned'] >= MAX_ENTRIES:
                        reasons.add('entry_limit')
                        break
                    coverage['entries_scanned'] += 1
                    relative = parts + (entry.name,)
                    if not _permitted(relative) or entry.is_symlink():
                        coverage['excluded'] += 1
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            children.append(relative)
                        elif entry.is_file(follow_symlinks=False) and Path(entry.name).suffix.lower() in SOURCE_EXTENSIONS:
                            found.add(relative)
                    except OSError:
                        coverage['unavailable'] += 1
                        reasons.add('unavailable_path')
            pending.extend(sorted(children))
        except OSError:
            coverage['unavailable'] += 1
            reasons.add('unavailable_path')
        finally:
            os.close(directory)
        if 'entry_limit' in reasons:
            break
    return found


def _python_map(source):
    symbols, dependencies = [], []
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError) as error:
        # Broken work-in-progress files remain discoverable without claiming AST
        # accuracy or hiding the syntax error behind a cached successful parse.
        for match in re.finditer(r'^[ \t]*(?:async\s+)?(def|class)\s+(\w+)', source, re.M):
            line = source.count('\n', 0, match.start()) + 1
            symbols.append({'name': match[2], 'kind': match[1], 'line': line, 'end_line': line})
        return {'parser': 'python-lexical-fallback', 'range_kind': 'declaration_line',
                'symbols': symbols, 'dependencies': [], 'partial': True,
                'parse_error': {'kind': type(error).__name__, 'line': getattr(error, 'lineno', None)}}

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.parents = []

        def definition(self, node, kind):
            symbols.append({'name': '.'.join([*self.parents, node.name]), 'kind': kind,
                            'line': node.lineno, 'end_line': node.end_lineno})
            self.parents.append(node.name)
            self.generic_visit(node)
            self.parents.pop()

        def visit_FunctionDef(self, node):
            self.definition(node, 'function')

        def visit_AsyncFunctionDef(self, node):
            self.definition(node, 'async_function')

        def visit_ClassDef(self, node):
            self.definition(node, 'class')

        def visit_Import(self, node):
            for alias in node.names:
                dependencies.append({'module': alias.name, 'line': node.lineno})

        def visit_ImportFrom(self, node):
            dependencies.append({'module': '.' * node.level + (node.module or ''),
                                 'imports': [alias.name for alias in node.names], 'line': node.lineno})

    Visitor().visit(tree)
    return {'parser': 'python-ast', 'range_kind': 'definition_body',
            'symbols': symbols, 'dependencies': dependencies, 'partial': False}


def _js_map(source):
    # Lexical tokens omit comments and strings from definition detection. This
    # intentionally does not pretend to resolve TS types, aliases or dynamic code.
    tokens = []
    line, previous_end = 1, 0
    for token in _JS_TOKENS.finditer(source):
        line += source.count('\n', previous_end, token.start())
        if token.lastgroup != 'comment':
            tokens.append((token.group(), token.lastgroup, line))
        line += token.group().count('\n')
        previous_end = token.end()
    symbols, dependencies = [], []
    for index, (value, kind, line) in enumerate(tokens):
        if kind != 'word':
            continue
        tail = tokens[index + 1:index + 5]
        if value in {'function', 'class', 'const', 'let', 'var', 'interface', 'type'}:
            if tail and tail[0][0] == '*':
                tail = tail[1:]
            if tail and tail[0][1] == 'word':
                symbol_kind = {'const': 'binding', 'let': 'binding', 'var': 'binding'}.get(value, value)
                symbols.append({'name': tail[0][0], 'kind': symbol_kind, 'line': line, 'end_line': tail[0][2]})
        module_token = None
        if value == 'require' and len(tail) >= 2 and tail[0][0] == '(' and tail[1][1] == 'string':
            module_token = tail[1]
        elif value == 'import' and tail:
            if tail[0][1] == 'string':
                module_token = tail[0]
            elif len(tail) >= 2 and tail[0][0] == '(' and tail[1][1] == 'string':
                module_token = tail[1]
        elif value == 'from' and tail and tail[0][1] == 'string':
            module_token = tail[0]
        if module_token and module_token[0].startswith(('"', "'")):
            module = module_token[0][1:-1]
            # Module specifiers are evidence; arbitrary string literals, URLs with
            # credentials/query strings and escaped expressions are never emitted.
            if re.fullmatch(r'[\w@./#$:+-]{1,180}', module) and '://' not in module:
                dependencies.append({'module': module, 'line': line})
    return {'parser': 'javascript-typescript-lexical', 'range_kind': 'declaration_line',
            'symbols': symbols, 'dependencies': dependencies, 'partial': False,
            'limitations': ['lexical_not_semantic', 'dynamic_imports_and_regex_literals_not_resolved']}


def _parse(content, suffix, fingerprint):
    key = ('repository-symbols-v1', suffix, fingerprint)
    cached = CACHE.get(key, fingerprint)
    if cached is not None:
        return json.loads(cached)
    try:
        source = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        return {'parser': 'unavailable', 'symbols': [], 'dependencies': [], 'partial': True, 'reason': 'non_utf8'}
    if '\x00' in source:
        return {'parser': 'unavailable', 'symbols': [], 'dependencies': [], 'partial': True, 'reason': 'binary'}
    if re.search(r'@generated|\bDO NOT EDIT\b', source[:1024], re.I):
        return {'parser': 'unavailable', 'symbols': [], 'dependencies': [], 'partial': True, 'reason': 'generated'}
    result = _python_map(source) if suffix == '.py' else _js_map(source)
    encoded = _encoded(result)
    CACHE.put(key, fingerprint, encoded, len(encoded.encode()) + 256)
    return result


def _terms(query):
    return set(re.findall(r'[\w$]{3,}', query.casefold()))


def _score(text, terms):
    return sum(1 for term in terms if term in text.casefold())


def _dependency_targets(path, dependency, available):
    module = dependency['module']
    if path.endswith('.py'):
        level = len(module) - len(module.lstrip('.'))
        if level:
            parent = list(PurePosixPath(path).parent.parts)
            if level - 1 > len(parent):
                return []
            prefix = parent[:len(parent) - level + 1] if level > 1 else parent
            base = '/'.join([*prefix, module.lstrip('.').replace('.', '/')]).strip('/')
        else:
            base = module.replace('.', '/')
        candidates = [base + '.py', (base + '/__init__.py').lstrip('/')]
        for imported in dependency.get('imports', ()):
            if imported != '*':
                candidates.extend([(base + '/' + imported + '.py').lstrip('/'),
                                   (base + '/' + imported + '/__init__.py').lstrip('/')])
    elif module.startswith('.'):
        base = posixpath.normpath(posixpath.join(str(PurePosixPath(path).parent), module))
        if base == '..' or base.startswith('../'):
            return []
        candidates = [base]
        for extension in ('.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx'):
            candidates.extend([base + extension, base + '/index' + extension])
    else:
        return []
    return sorted(set(candidates).intersection(available))


def _rank_candidates(files, terms):
    """Rank fresh, bounded observations before choosing the returned files.

    Symbol matches must survive a small output budget even when their filename
    is unrelated to the query. One-hop links bring in the implementation or
    callers of a matching module without letting graph popularity replace the
    user's query. This is a lexical dependency heuristic, not semantic search.
    """
    if not terms:
        return sorted(files, key=lambda file: file['path'])
    available = {file['path'] for file in files}
    adjacent = {path: set() for path in available}
    direct = {}
    for file in files:
        path = file['path']
        symbol_score = max((_score(symbol['name'], terms) for symbol in file['symbols']), default=0)
        direct[path] = 4 * _score(path, terms) + 8 * symbol_score
        for dependency in file['dependencies']:
            for target in _dependency_targets(path, dependency, available):
                if target != path:
                    adjacent[path].add(target)
                    adjacent[target].add(path)
    def relevance(file):
        path = file['path']
        neighbors = adjacent[path]
        strongest = max((direct[neighbor] for neighbor in neighbors), default=0)
        return 8 * direct[path] + 2 * strongest
    return sorted(files, key=lambda file: (-relevance(file), file['path']))


def repository_map(workspace: Path, query: str = '', paths: list[str] | None = None,
                   max_files: int = 80, max_chars: int = 24000) -> dict:
    """Return a fresh bounded source map; omitted/unsupported scope stays explicit.

    Budgets cap directory entries, bytes, parse work and serialized output. A
    query may inspect up to 4x max_files (at least 64, at most 256) candidates
    before selecting results; omitted search scope remains explicitly partial.
    No query keeps the cheaper max_files read budget. These limits
    are not wall-clock guarantees for an unresponsive filesystem. An empty paths
    list means repository scope; broad home/system scopes require explicit paths.
    """
    if not isinstance(max_files, int) or not 1 <= max_files <= 200:
        raise ValueError('max_files must be between 1 and 200')
    if not isinstance(max_chars, int) or not 1024 <= max_chars <= 60000:
        raise ValueError('max_chars must be between 1024 and 60000')
    if not isinstance(query, str) or len(query) > 4000:
        raise ValueError('query must be a string of at most 4000 characters')
    if paths is not None and (not isinstance(paths, list) or len(paths) > 64 or
                              any(not isinstance(path, str) or len(path) > 4096 or '\x00' in path for path in paths)):
        raise ValueError('paths must contain at most 64 strings of at most 4096 characters')
    # Reject broad scopes consistently even on hosts without /home/coder.
    root = Path(workspace).resolve()
    broad = {Path('/'), Path('/home'), Path('/home/coder'), Path('/root'), Path.home()}
    if root in broad and not paths:
        raise ValueError('Choose a project or explicit source paths; global home/system scans are disabled')
    root = root.resolve(strict=True)
    reasons, requested = set(), []
    for raw in paths or []:
        relative = _relative(root, raw)
        if relative is None:
            reasons.add('excluded_requested_path')
        else:
            requested.append(relative)
    if not paths:
        requested = [()]
    coverage = Counter(entries_scanned=0, directories_scanned=0, files_considered=0,
                       files_read=0, files_returned=0, bytes_read=0, excluded=0, unavailable=0)
    result = {'scope': {'workspace': str(root), 'paths': ['/'.join(path) or '.' for path in requested],
                        'source_extensions': sorted(SOURCE_EXTENSIONS)},
              'partial': False, 'reasons': [], 'files': [], 'coverage': coverage,
              'observations': {'captured_at': datetime.now(timezone.utc).isoformat(),
                               'fingerprint_algorithm': 'sha256', 'snapshot': 'per_file_not_atomic',
                               'dependencies': 'resolved_only_among_returned_files'}}
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    terms = _terms(query)
    try:
        candidates = _inventory(root_fd, requested, coverage, reasons)
        coverage['files_considered'] = len(candidates)
        ordered = sorted(candidates, key=lambda parts: (-_score('/'.join(parts), terms), '/'.join(parts)))
        if len(ordered) > max_files:
            reasons.add('file_count_limit')
        candidate_limit = min(MAX_QUERY_FILES, max(MIN_QUERY_FILES, max_files * 4)) if terms else max_files
        coverage['candidate_limit'] = candidate_limit
        coverage['candidates_inspected'] = 0
        if len(ordered) > candidate_limit:
            reasons.add('candidate_limit')
        for parts in ordered[:candidate_limit]:
            if coverage['bytes_read'] + MAX_FILE_BYTES > MAX_TOTAL_BYTES:
                reasons.add('total_byte_limit')
                break
            coverage['candidates_inspected'] += 1
            try:
                content, error = _source_bytes(root_fd, parts)
            except OSError:
                content, error = None, 'unavailable_path'
            if content is None:
                coverage['unavailable'] += 1
                reasons.add(error)
                continue
            coverage['files_read'] += 1
            coverage['bytes_read'] += len(content)
            fingerprint = hashlib.sha256(content).hexdigest()
            mapped = _parse(content, Path(parts[-1]).suffix.lower(), fingerprint)
            if mapped.get('reason') in {'binary', 'generated', 'non_utf8'}:
                coverage['excluded'] += 1
                reasons.add(mapped['reason'])
                continue
            if mapped['partial']:
                reasons.add('parse_incomplete')
            mapped.update({'path': '/'.join(parts), 'fingerprint': fingerprint,
                           'symbol_count': len(mapped['symbols']), 'dependency_count': len(mapped['dependencies'])})
            if len(mapped['symbols']) > MAX_SYMBOLS or len(mapped['dependencies']) > MAX_DEPENDENCIES:
                mapped['partial'] = True
                reasons.add('file_detail_limit')
            mapped['symbols'] = sorted(sorted(mapped['symbols'], key=lambda symbol: -_score(symbol['name'], terms))[:MAX_SYMBOLS],
                                       key=lambda symbol: symbol['line'])
            mapped['dependencies'] = mapped['dependencies'][:MAX_DEPENDENCIES]
            result['files'].append(mapped)
    finally:
        os.close(root_fd)
    result['files'] = _rank_candidates(result['files'], terms)[:max_files]
    result['observations']['selection'] = 'bounded_path_symbol_dependency_rank' if terms else 'bounded_path_order'
    # Retain whole entries where possible. A large single file can still expose
    # its highest ranked definitions with an explicit per-file partial flag.
    while len(_encoded(result)) > max_chars - 220 and result['files']:
        reasons.add('output_limit')
        if len(result['files']) > 1:
            result['files'].pop()
        else:
            file = result['files'][0]
            file['partial'] = True
            if file['symbols']:
                file['symbols'].pop()
            elif file['dependencies']:
                file['dependencies'].pop()
            else:
                result['files'].pop()
    available = {file['path'] for file in result['files']}
    for file in result['files']:
        for dependency in file['dependencies']:
            dependency['targets'] = _dependency_targets(file['path'], dependency, available)
    coverage['files_returned'] = len(result['files'])
    result['observations']['fingerprint'] = hashlib.sha256(_encoded([
        (file['path'], file['fingerprint']) for file in result['files']]).encode()).hexdigest()
    result['partial'], result['reasons'] = bool(reasons), sorted(reasons)
    # Dependency targets and metadata add bytes after the first budget pass.
    while len(_encoded(result)) > max_chars and result['files']:
        result['files'].pop()
        reasons.add('output_limit')
        available = {file['path'] for file in result['files']}
        for file in result['files']:
            for dependency in file['dependencies']:
                dependency['targets'] = [path for path in dependency['targets'] if path in available]
        coverage['files_returned'] = len(result['files'])
        result['observations']['fingerprint'] = hashlib.sha256(_encoded([
            (file['path'], file['fingerprint']) for file in result['files']]).encode()).hexdigest()
        result['partial'], result['reasons'] = True, sorted(reasons)
    if len(_encoded(result)) > max_chars:
        # Very long caller-supplied scope names cannot consume an unbounded reply.
        result['scope']['paths'] = []
        result['scope']['workspace'] = '[scope omitted: output limit]'
        result['partial'] = True
        result['reasons'] = sorted(reasons | {'output_limit'})
    return result
