"""Replay delivery failures against the production tools, without model calls."""
import asyncio
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pydantic_ai.tools import ToolDefinition


class DeliveryRecovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(sys, 'argv', ['delivery-test', '--billing-status']):
            spec = importlib.util.spec_from_file_location('delivery_test_backend', ROOT/'trinity_agent/agent.py')
            cls.backend = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.backend)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root/'project'
        self.workspace.mkdir()
        self.a = self.backend
        token = self.a.RUN_CONTEXT.set(self.a.RunDependencies(full_access=True))
        self.addCleanup(self.a.RUN_CONTEXT.reset, token)
        for key, value in {
            'WORKSPACE': self.workspace, 'DATA_ROOT': self.root/'data',
            'ARCHIVE_ROOT': self.root/'archive', 'STATE_ROOT': self.root/'state',
            'CONVERSATION_ID': 'delivery-test',
            'emit': lambda event: None,
        }.items():
            mock = patch.object(self.a, key, value)
            mock.start()
            self.addCleanup(mock.stop)

    def tool(self, name):
        function = next(tool.function for tool in self.a.agent_tools() if tool.name == name)
        def call(*args, **kwargs):
            return self.a.run_dependencies().engine.invoke(function, *args, **kwargs)
        return call

    def test_existing_document_cannot_be_truncated_by_creation_tool(self):
        target = self.workspace/'CHANGELOG.md'
        original = '# Changelog\n\n' + ''.join(f'## Version {i}\nDetails {i}\n\n' for i in range(100))
        target.write_text(original)
        self.a.begin_verification('Ajoute une entrée au changelog et conserve son historique.')
        result = json.loads(self.tool('write_file')('CHANGELOG.md', '# Changelog\n\nNew\n...unchanged...'))
        self.assertFalse(result['executed'])
        self.assertEqual(target.read_text(), original)
        self.assertIn('edit_file', result['message'])
        self.tool('edit_file')('CHANGELOG.md', '# Changelog\n\n', '# Changelog\n\nNew\n\n')
        self.assertEqual(target.read_text(), '# Changelog\n\nNew\n\n' + original[len('# Changelog\n\n'):])
        self.assertEqual(json.loads(self.a.verify_changes())['status'], 'passed')
        self.assertFalse(self.a.run_dependencies().engine.final_status()['incomplete'])

    def test_new_file_and_identical_retry_remain_supported(self):
        self.a.begin_verification('Crée une note dans le projet.')
        write = self.tool('write_file')
        write('note.txt', 'complete content\n')
        write('note.txt', 'complete content\n')
        self.assertEqual((self.workspace/'note.txt').read_text(), 'complete content\n')
        self.assertFalse(self.a.run_dependencies().engine.journal.snapshot()['unresolved_mutations'])

    def test_root_search_prefers_sources_but_explicit_archive_remains_searchable(self):
        subprocess.run(['git', 'init', '-q', str(self.workspace)], check=True)
        (self.workspace/'.gitignore').write_text('recovery/\n')
        (self.workspace/'source.py').write_text('def delivery_probe(): pass\n')
        (self.workspace/'recovery').mkdir()
        (self.workspace/'recovery/obsolete.py').write_text('def delivery_probe(): pass\n')
        current = self.a.search_text('delivery_probe')
        self.assertIn('source.py', current)
        self.assertNotIn('obsolete.py', current)
        self.assertIn('obsolete.py', self.a.search_text('delivery_probe', 'recovery'))

    def test_command_duration_is_explicit_and_real_timeout_remains_enforced(self):
        self.a.begin_verification('Exécute le contrôle du projet.')
        command = shlex.join([sys.executable, '-c',
            "import time;from pathlib import Path;time.sleep(1.2);Path('finished.txt').write_text('done')"])
        short = json.loads(self.a.run_command(command, timeout=1))
        self.assertTrue(short['timed_out'])
        self.assertFalse((self.workspace/'finished.txt').exists())
        longer = json.loads(self.a.run_command(command, timeout=3))
        self.assertTrue(longer['passed'])
        self.assertEqual((self.workspace/'finished.txt').read_text(), 'done')
        invalid = json.loads(self.a.run_command('touch invalid.txt', timeout=0))
        self.assertFalse(invalid['executed'])
        self.assertFalse((self.workspace/'invalid.txt').exists())

    def test_delivery_inspection_is_visible_and_callable_in_read_only_mode(self):
        self.a.begin_verification('Vérifie verify_extension_delivery en lecture seule.')
        definition = ToolDefinition(name='verify_extension_delivery', description='Inspect delivery', parameters_json_schema={})
        visible = asyncio.run(self.a.prepare_tool_for_request(
            SimpleNamespace(prompt='lecture seule', messages=[], deps=self.a.run_dependencies()), definition))
        self.assertIsNotNone(visible)
        calls = []
        def verify_extension_delivery():
            calls.append('inspection')
            return json.dumps({'complete': True})
        result = json.loads(self.a.run_dependencies().engine.invoke(verify_extension_delivery))
        self.assertTrue(result['complete'])
        self.assertEqual(calls, ['inspection'])

    def test_diagnostic_after_failed_command_keeps_answer_and_uncertainty(self):
        self.a.begin_verification('Exécute la commande demandée.')
        command = shlex.join([sys.executable, '-c',
            "from pathlib import Path;Path('partial.txt').write_text('partial');raise SystemExit(1)"])
        self.tool('run_command')(command)
        pending = self.a.run_dependencies().engine.journal.snapshot()['unresolved_mutations']
        self.assertTrue(pending)
        self.a.begin_verification('Explique le résultat en lecture seule.')
        answer = 'Le fichier partial.txt existe ; la commande a échoué.'
        result = self.a.validate_task_output(answer)
        self.assertIn(answer, result)
        self.assertIn('historique', result.lower())
        self.assertEqual(self.a.run_dependencies().engine.journal.snapshot()['unresolved_mutations'], pending)
        self.assertTrue(self.a.run_dependencies().engine.final_status()['incomplete'])
        self.assertEqual(self.a.validate_task_output(result), result)
        self.a.begin_verification('Installe et publie le projet.')
        self.assertTrue(self.a.validate_task_output('Terminé.').startswith('Tâche incomplète.'))


if __name__ == '__main__':
    unittest.main()
