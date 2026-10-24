"""Offline integration regressions for access, search and controller notices."""
import asyncio
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
with patch.object(sys, 'argv', ['coherence-test', '--billing-status']):
    spec = importlib.util.spec_from_file_location('coherence_agent', ROOT / 'ovh-agent.py')
    a = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(a)


class CoherenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / 'project'
        self.workspace.mkdir()
        for name, value in {'WORKSPACE': self.workspace, 'ALLOWED_ROOT': self.root,
                            'FULL_ACCESS': False, 'VERIFICATION_GUARD': None,
                            'TASK_ENGINE': None}.items():
            override = patch.object(a, name, value)
            override.start()
            self.addCleanup(override.stop)
        for name in ('emit', 'activity_start', 'activity_end'):
            override = patch.object(a, name)
            override.start()
            self.addCleanup(override.stop)

    def test_search_finds_explicitly_targeted_ignored_installation(self):
        subprocess.run(['git', 'init', '-q', str(self.workspace)], check=True)
        (self.workspace / '.gitignore').write_text('*\n')
        installed = self.workspace / 'extensions' / 'installed'
        installed.mkdir(parents=True)
        (installed / 'webview.js').write_text('const selector = "header-app-logo";\n')
        dependencies = installed / 'node_modules'
        dependencies.mkdir()
        (dependencies / 'vendor.js').write_text('header-app-logo vendor-sentinel\n')
        (installed / '.env').write_text('header-app-logo secret-sentinel\n')
        result = a.search_text('header-app-logo', str(installed))
        self.assertIn('webview.js:1', result)
        self.assertNotIn('vendor-sentinel', result)
        self.assertNotIn('secret-sentinel', result)

    def test_invalid_catalog_row_does_not_replace_healthy_models_or_become_free(self):
        valid = {'id':'healthy-model', 'context_length':8192,
            'pricing':{'prompt':'0.000001','completion':'0.000002'}}
        rows = [None, {'id':'bad-limit','context_length':[]}, valid]
        for value in ('abc', 'NaN', 'Infinity', '-1', True, {}):
            rows.append({'id':'bad-price-'+str(value), 'pricing':{'prompt':value,'completion':0}})
        with patch.object(a, 'MODEL_CATALOG', None), patch.object(a, 'MODEL_CACHE_FILE', self.root/'catalog.json'), \
             patch.object(a, 'AI_API_BASE_URL', 'https://fixture.invalid/v1'), patch.object(a, 'model_metadata', return_value={}), \
             patch.dict(os.environ, {'OVH_AI_API_KEY':'fixture'}), \
             patch.object(a.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps({'data':rows}).encode())):
            cards = a.fetch_model_catalog(force=True)
        self.assertEqual([card['id'] for card in cards], ['healthy-model'])
        self.assertFalse(cards[0]['free'])
        self.assertEqual(cards[0]['input_per_million'], 1)

    def test_failed_operation_invalidates_previous_acceptance(self):
        engine = SimpleNamespace(last_acceptance={'passed':True}, observe_event=lambda event: None,
            operations=SimpleNamespace(execute=lambda name: (_ for _ in ()).throw(OSError('lost receipt'))))
        with patch.object(a, 'TASK_ENGINE', engine):
            self.assertIn('lost receipt', a.execute_operation('fixture'))
        self.assertIsNone(engine.last_acceptance)

    def test_command_guard_distinguishes_arguments_from_executables(self):
        from ovh_process import command_is_destructive
        blocked = ['rm -rf  /', '/bin/rm -f -r -- /', 'sudo rm -rf /',
            'true; mkfs.ext4 /dev/example', 'shutdown -h now', 'dd if=/dev/zero of=/dev/example',
            "sh -c 'reboot'", ':(){ :|:& };:']
        allowed = ["echo 'do not reboot'", 'rm -rf /tmp/fixture',
            'rg shutdown service.py', "printf '%s' 'mkfs.ext4'", 'cat reboot-notes.txt']
        for command in blocked:
            self.assertTrue(command_is_destructive(command), command)
        for command in allowed:
            self.assertFalse(command_is_destructive(command), command)

    def test_web_trickle_is_bounded_between_reads_and_does_not_fill_a_large_read(self):
        class Response(io.BytesIO):
            headers = SimpleNamespace(get_content_charset=lambda:'utf-8')
            def read(self, *args):
                raise AssertionError('A filling read can wait indefinitely on a trickle')
            def read1(self, size):
                return b'x'
        with patch.object(a.urllib.request, 'urlopen', return_value=Response()), \
             patch.object(a.time, 'monotonic', side_effect=[0, 1, 16, 31, 46]):
            with self.assertRaisesRegex(ValueError, 'délai'):
                a.fetch_web_document('https://fixture.invalid/page')

    def test_search_falls_back_safely_without_ripgrep(self):
        source=self.workspace/'source.py';source.write_text('PORTABLE_SENTINEL = True\n')
        hidden=self.workspace/'.env';hidden.write_text('PORTABLE_SENTINEL=secret\n')
        dependency=self.workspace/'node_modules';dependency.mkdir()
        (dependency/'vendor.js').write_text('PORTABLE_SENTINEL vendor\n')
        with patch.object(a.subprocess,'run',side_effect=FileNotFoundError):
            result=a.search_text('PORTABLE_SENTINEL')
        self.assertIn('source.py:1',result)
        self.assertNotIn('secret',result)
        self.assertNotIn('vendor',result)

    def test_automatic_retrieval_shares_explicit_file_access_boundary(self):
        sibling = self.root / 'neighbor'
        sibling.mkdir()
        source = sibling / 'private.py'
        source.write_text('AUDIT_SENTINEL = "neighbor project"\n')
        prompt = f'Analyse le code dans `{source}`'
        with patch.object(a, 'history_with_memory', side_effect=lambda history, *args, **kwargs: history):
            with self.assertRaises(a.ModelRetry):
                a.resolve_path(str(source))
            history, details = a.prepare_task_context([], prompt)
            self.assertNotIn('AUDIT_SENTINEL', str(history))
            self.assertNotIn(str(source), [item['path'] for item in details['sources']])
            with patch.object(a, 'FULL_ACCESS', True):
                history, details = a.prepare_task_context([], prompt)
                self.assertIn('AUDIT_SENTINEL', str(history))
                self.assertIn(str(source), [item['path'] for item in details['sources']])

    def test_command_refusal_records_no_execution(self):
        with patch.object(a, 'request_approval', return_value=False), \
                patch.object(a, 'run_process', side_effect=AssertionError('Must never launch')):
            for result in (a.run_command('printf harmless'),
                           a.run_host_command('TRINITY-EDGE', 'xl list'),
                           a.run_host_command('unknown', 'hostname'),
                           a.run_command('mkfs /fixture')):
                value = json.loads(result)
                self.assertIs(value['executed'], False)
                self.assertIs(value['passed'], False)
                self.assertIsNone(value['exit_code'])

    def test_receipt_failure_after_configured_check_is_not_a_nonexecution_receipt(self):
        guard = SimpleNamespace(observe=lambda path: None)
        def failed_receipt(*args):
            raise OSError('Fixture: check ran, receipt storage failed')
        guard.execute_configured = failed_receipt
        with patch.object(a, 'VERIFICATION_GUARD', guard):
            result = a.run_command('configured-check')
        self.assertIn('receipt storage failed', result)
        self.assertNotIn('"executed": false', result)

    def test_diagnostics_never_execute_project_path_shadows_or_ripgrep_hooks(self):
        fixture = self.workspace / 'file.txt'
        fixture.write_text('pattern\n')
        marker = self.workspace / 'unexpected-mutation'
        project_bin = self.workspace / '.agent-tools' / 'venv' / 'bin'
        project_bin.mkdir(parents=True)
        shadow = project_bin / 'cat'
        shadow.write_text('#!/bin/sh\nprintf mutation > unexpected-mutation\n')
        shadow.chmod(0o755)
        hook = self.workspace / 'hook.sh'
        hook.write_text('#!/bin/sh\nprintf mutation > unexpected-mutation\n/bin/cat "$1"\n')
        hook.chmod(0o755)
        config = self.workspace / 'ripgrep-config'
        config.write_text(f'--pre={hook}\n')
        with patch.dict(os.environ, {'RIPGREP_CONFIG_PATH': str(config),
                                     'PATH': str(project_bin) + ':' + os.environ['PATH']}), \
                patch.object(a, 'request_approval', return_value=True):
            for command in ('cat file.txt', 'rg pattern file.txt'):
                result = json.loads(a.run_command(command))
                self.assertTrue(result['passed'], result)
                self.assertIn('pattern', result['output'])
                self.assertFalse(marker.exists(), command)

    def test_checkpoint_never_replaces_user_during_compaction(self):
        request = 'Inspecte EXACT-USER-REQUEST ; ne modifie aucune donnée.'
        history = [a.ModelRequest(parts=[a.UserPromptPart(content=request)])]
        history += [a.ModelResponse(parts=[a.TextPart(content='Earlier evidence. ' * 1000)])]
        guard = SimpleNamespace(checkpoints=set(), started_at=time.monotonic() - 100000)
        ctx = SimpleNamespace(usage=SimpleNamespace(requests=0))
        with patch.object(a, 'VERIFICATION_GUARD', guard):
            marked = a.task_checkpoint(ctx, history)
        self.assertEqual(a.recent_prompts_from_messages(marked), request)
        memory = a.ConversationMemory(self.root / 'memory')
        compacted, stats = asyncio.run(a.compact_context(marked, memory, 4000))
        self.assertTrue(stats['compacted'])
        self.assertTrue(any(isinstance(part, a.UserPromptPart) and part.content == request
                            for message in compacted for part in message.parts))
        with patch.object(a, 'history_with_memory', side_effect=lambda history, *args, **kwargs: history):
            prepared, _ = a.prepare_task_context(compacted, 'Bonjour')
        self.assertNotIn('[TRINITY task checkpoint]', str(prepared))


if __name__ == '__main__':
    unittest.main()
