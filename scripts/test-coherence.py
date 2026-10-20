"""Offline integration regressions for access, search and controller notices."""
import asyncio
import importlib.util
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
