"""Offline repository-map checks: scope, evidence freshness and bounded work."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ovh_repository as repository
from ovh_cache import CACHE


class RepositoryMapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        CACHE.clear()

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return target

    def files(self, **kwargs):
        return {entry['path']: entry for entry in repository.repository_map(self.root, **kwargs)['files']}

    def test_multifile_python_ranges_and_dependency_edges(self):
        source = 'from .worker import act\n\nclass Handler:\n    async def run(self):\n        return act()\n'
        self.write('pkg/main.py', source)
        self.write('pkg/worker.py', 'def act():\n    return 42\n')
        result = repository.repository_map(self.root)
        files = {entry['path']: entry for entry in result['files']}
        self.assertFalse(result['partial'])
        handler = files['pkg/main.py']
        self.assertEqual(handler['parser'], 'python-ast')
        self.assertEqual(handler['symbols'], [
            {'name': 'Handler', 'kind': 'class', 'line': 3, 'end_line': 5},
            {'name': 'Handler.run', 'kind': 'async_function', 'line': 4, 'end_line': 5},
        ])
        self.assertEqual(handler['dependencies'][0]['targets'], ['pkg/worker.py'])
        self.assertEqual(handler['fingerprint'], hashlib.sha256(source.encode()).hexdigest())
        self.assertEqual(result['observations']['snapshot'], 'per_file_not_atomic')
        self.assertNotIn('return 42', json.dumps(result))

    def test_relative_python_imports_and_unresolved_dependencies_are_not_missing_claims(self):
        self.write('pkg/sub/main.py', 'from .. import worker\nimport external_provider\n')
        self.write('pkg/worker.py', 'def work():\n    pass\n')
        files = self.files()
        dependencies = files['pkg/sub/main.py']['dependencies']
        self.assertEqual(dependencies[0]['targets'], ['pkg/worker.py'])
        self.assertEqual(dependencies[1]['targets'], [])
        self.assertNotIn('missing', json.dumps(dependencies))

    def test_javascript_fallback_ignores_strings_and_comments(self):
        self.write('ui/main.js', '// function fabricated() {}\n'
                   'const text = "function phantom() {}";\n'
                   'import { work } from "./worker.js";\n'
                   'export async function start() { return work(); }\n'
                   'const dep = require("fs");\n')
        self.write('ui/worker.js', 'export function work() {}\n')
        files = self.files()
        main = files['ui/main.js']
        self.assertEqual(main['parser'], 'javascript-typescript-lexical')
        self.assertEqual(main['range_kind'], 'declaration_line')
        self.assertEqual([symbol['name'] for symbol in main['symbols']], ['text', 'start', 'dep'])
        self.assertEqual(main['dependencies'][0]['targets'], ['ui/worker.js'])
        self.assertIn('lexical_not_semantic', main['limitations'])

    def test_cached_parse_requires_current_bytes_even_if_size_and_mtime_match(self):
        source = self.write('source.py', 'def before():\n    pass\n')
        first = self.files()['source.py']
        with patch.object(repository, '_python_map', side_effect=AssertionError('warm AST parsed again')):
            warm = self.files()['source.py']
        self.assertEqual(warm['fingerprint'], first['fingerprint'])
        before = source.stat()
        source.write_text('def after_():\n    pass\n')
        os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
        updated = self.files()['source.py']
        self.assertEqual(updated['symbols'][0]['name'], 'after_')
        self.assertNotEqual(updated['fingerprint'], first['fingerprint'])
        source.unlink()
        self.assertEqual(self.files(), {})

    def test_excluded_sources_and_symlinks_never_read(self):
        self.write('source.py', 'def useful():\n    pass\n')
        for name in ('node_modules/dependency.js', '.local-agent/private.py', 'archive/log.py',
                     'credentials.py', '.env.py', 'secrets/token.py', 'dist/generated.js', 'frontend.min.js'):
            self.write(name, 'def DO_NOT_READ_PRIVATE_FIXTURE():\n    pass\n')
        with tempfile.TemporaryDirectory() as outside:
            foreign = Path(outside) / 'outside.py'
            foreign.write_text('def DO_NOT_READ_PRIVATE_FIXTURE():\n    pass\n')
            (self.root / 'link.py').symlink_to(foreign)
            (self.root / 'linked').symlink_to(Path(outside), target_is_directory=True)
            result = repository.repository_map(self.root)
            self.assertEqual([file['path'] for file in result['files']], ['source.py'])
            explicit = repository.repository_map(self.root, paths=['link.py', 'linked/outside.py',
                                                                  'credentials.py', '../outside.py'])
            self.assertEqual(explicit['files'], [])
            self.assertTrue(explicit['partial'])
        self.assertNotIn('DO_NOT_READ_PRIVATE_FIXTURE', json.dumps(result))

    def test_generated_markers_binary_and_oversized_files_are_explicit(self):
        self.write('generated.py', '# DO NOT EDIT: generated\ndef generated():\n    pass\n')
        (self.root / 'binary.py').write_bytes(b'\0binary')
        self.write('large.py', '#' * (repository.MAX_FILE_BYTES + 1))
        self.write('valid.py', 'def valid():\n    pass\n')
        result = repository.repository_map(self.root)
        self.assertEqual([file['path'] for file in result['files']], ['valid.py'])
        self.assertTrue({'generated', 'binary', 'file_size_limit'}.issubset(result['reasons']))

    def test_entry_file_and_output_limits_are_visible(self):
        for index in range(30):
            self.write(f'source_{index:03}.py', f'def function_{index}():\n    pass\n')
        with patch.object(repository, 'MAX_ENTRIES', 8):
            bounded = repository.repository_map(self.root)
        self.assertLessEqual(bounded['coverage']['entries_scanned'], 8)
        self.assertIn('entry_limit', bounded['reasons'])
        file_bound = repository.repository_map(self.root, max_files=3)
        self.assertEqual(file_bound['coverage']['files_read'], 3)
        self.assertIn('file_count_limit', file_bound['reasons'])
        result = repository.repository_map(self.root, max_chars=1200)
        self.assertLessEqual(len(repository._encoded(result)), 1200)
        self.assertIn('output_limit', result['reasons'])
        self.assertEqual(result['coverage']['files_returned'], len(result['files']))

    def test_explicit_scope_does_not_widen_and_query_prioritizes_path(self):
        for index in range(10):
            self.write(f'component_{index:03}.py', 'def generic():\n    pass\n')
        self.write('zzz/recovery.py', 'def recover():\n    pass\n')
        result = repository.repository_map(self.root, query='recovery', max_files=1)
        self.assertEqual(result['files'][0]['path'], 'zzz/recovery.py')
        selected = repository.repository_map(self.root, paths=['zzz/recovery.py'])
        self.assertEqual([file['path'] for file in selected['files']], ['zzz/recovery.py'])
        self.assertEqual(selected['coverage']['directories_scanned'], 0)

    def test_symbol_match_survives_filename_and_result_cutoff(self):
        # The relevant implementation has no query word in its filename and
        # sorts after the old max_files cutoff. Comments are not declarations.
        for index in range(40):
            self.write(f'component_{index:03}.py', '# QuantumResolver is mentioned here\ndef generic():\n    pass\n')
        target = self.write('zzz_impl.py', 'class QuantumResolver:\n    pass\n')
        result = repository.repository_map(self.root, query='QuantumResolver', max_files=1)
        self.assertEqual([file['path'] for file in result['files']], ['zzz_impl.py'])
        self.assertEqual(result['files'][0]['fingerprint'], hashlib.sha256(target.read_bytes()).hexdigest())
        self.assertGreater(result['coverage']['files_read'], 1)
        self.assertEqual(result['coverage']['files_returned'], 1)

    def test_dependency_neighbors_precede_unrelated_files(self):
        for index in range(10):
            self.write(f'aa_unrelated_{index}.py', 'def unrelated():\n    pass\n')
        self.write('endpoint.py', 'from worker import calculate\ndef serve_endpoint():\n    return calculate()\n')
        self.write('worker.py', 'def calculate():\n    return 42\n')
        result = repository.repository_map(self.root, query='endpoint', max_files=2)
        self.assertEqual([file['path'] for file in result['files']], ['endpoint.py', 'worker.py'])
        self.assertEqual(result['files'][0]['dependencies'][0]['targets'], ['worker.py'])

    def test_reverse_dependency_is_considered_without_path_match(self):
        self.write('aa_other.py', 'def other():\n    pass\n')
        self.write('implementation.py', 'def TranslatePayload():\n    pass\n')
        self.write('zzz_caller.py', 'from implementation import TranslatePayload\n')
        result = repository.repository_map(self.root, query='TranslatePayload', max_files=2)
        self.assertEqual([file['path'] for file in result['files']], ['implementation.py', 'zzz_caller.py'])

    def test_query_discovery_has_an_explicit_separate_work_limit(self):
        for index in range(20):
            self.write(f'unit_{index:03}.py', 'def ordinary():\n    pass\n')
        self.write('zzz.py', 'def undiscovered():\n    pass\n')
        with patch.object(repository, 'MIN_QUERY_FILES', 4), patch.object(repository, 'MAX_QUERY_FILES', 8):
            result = repository.repository_map(self.root, query='undiscovered', max_files=2)
        self.assertEqual(result['coverage']['candidate_limit'], 8)
        self.assertEqual(result['coverage']['candidates_inspected'], 8)
        self.assertLessEqual(result['coverage']['files_read'], 8)
        self.assertEqual(result['coverage']['files_returned'], 2)
        self.assertIn('candidate_limit', result['reasons'])
        self.assertTrue(result['partial'])
        self.assertNotIn('zzz.py', {file['path'] for file in result['files']})

    def test_query_cache_does_not_preserve_stale_symbol_rank(self):
        before = self.write('aaa.py', 'def target():\n    pass\n')
        after = self.write('zzz.py', 'def absent():\n    pass\n')
        self.assertEqual(next(iter(self.files(query='target', max_files=1))), 'aaa.py')
        stamps = [before.stat(), after.stat()]
        before.write_text('def absent():\n    pass\n')
        after.write_text('def target():\n    pass\n')
        for path, stamp in zip([before, after], stamps):
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assertEqual(next(iter(self.files(query='target', max_files=1))), 'zzz.py')

    def test_query_discovery_preserves_total_byte_and_output_budgets(self):
        for index in range(40):
            self.write(f'part_{index:03}.py', 'def ordinary():\n    pass\n' + '#' * 140 + '\n')
        with patch.object(repository, 'MAX_FILE_BYTES', 256), patch.object(repository, 'MAX_TOTAL_BYTES', 1024):
            result = repository.repository_map(self.root, query='ordinary', max_files=2, max_chars=1024)
        self.assertLessEqual(result['coverage']['bytes_read'], 1024)
        self.assertLess(result['coverage']['files_read'], 40)
        self.assertIn('total_byte_limit', result['reasons'])
        self.assertLessEqual(len(repository._encoded(result)), 1024)

    def test_query_cannot_widen_explicit_scope_or_read_cached_exclusions(self):
        self.write('selected.py', 'def ordinary():\n    pass\n')
        self.write('elsewhere.py', 'def FindNeedle():\n    pass\n')
        self.write('credentials.py', 'def FindNeedle():\n    pass\n')
        self.files(query='FindNeedle')  # Warm cache must not extend authorization.
        calls = []
        original = repository._source_bytes
        def observe(descriptor, parts):
            calls.append('/'.join(parts))
            return original(descriptor, parts)
        with patch.object(repository, '_source_bytes', side_effect=observe):
            result = repository.repository_map(self.root, paths=['selected.py'], query='FindNeedle', max_files=1)
        self.assertEqual(calls, ['selected.py'])
        self.assertEqual([file['path'] for file in result['files']], ['selected.py'])

    def test_corrupt_python_is_labelled_and_does_not_reuse_previous_ast(self):
        source = self.write('source.py', 'def valid():\n    pass\n')
        self.files()
        source.write_text('def unfinished(:\n    pass\n')
        result = repository.repository_map(self.root)
        self.assertTrue(result['partial'])
        self.assertEqual(result['files'][0]['parser'], 'python-lexical-fallback')
        self.assertEqual(result['files'][0]['parse_error']['kind'], 'SyntaxError')

    def test_nonglobal_scope_and_parameter_validation(self):
        with patch.object(repository.os, 'scandir', side_effect=AssertionError('global traversal')):
            with self.assertRaises(ValueError):
                repository.repository_map(Path('/home/coder'))
        for parameters in ({'max_files': 0}, {'max_chars': 10}, {'paths': ['x'] * 65}, {'query': 'q' * 4001}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                repository.repository_map(self.root, **parameters)

    def test_raced_read_is_not_returned_as_fresh_evidence(self):
        self.write('source.py', 'def earlier():\n    pass\n')
        original = repository.os.fstat
        count = 0
        def changed_stat(descriptor):
            nonlocal count
            count += 1
            result = original(descriptor)
            if count == 2:
                from types import SimpleNamespace
                return SimpleNamespace(st_dev=result.st_dev, st_ino=result.st_ino, st_mode=result.st_mode,
                                       st_size=result.st_size, st_mtime_ns=result.st_mtime_ns + 1,
                                       st_ctime_ns=result.st_ctime_ns)
            return result
        with patch.object(repository.os, 'fstat', side_effect=changed_stat):
            result = repository.repository_map(self.root, paths=['source.py'])
        self.assertEqual(result['files'], [])
        self.assertIn('changed_during_read', result['reasons'])


if __name__ == '__main__':
    unittest.main()
