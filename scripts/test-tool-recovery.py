"""Regressions for corrected plans and authorized read-only diagnostics."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ovh_execution import RunDependencies, WorkflowController
from ovh_tools import ToolSelection
from pydantic_ai.tools import ToolDefinition


class ToolRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(sys, 'argv', ['test-tool-recovery', '--billing-status']):
            spec = importlib.util.spec_from_file_location('recovery_backend', ROOT/'ovh-agent.py')
            cls.backend = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.backend)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.engine = WorkflowController(self.workspace, self.workspace/'execution', trusted_diagnostics=True)
        self.engine.focus('Lis le fichier demandé.')
        self.deps = RunDependencies(engine=self.engine, tools=ToolSelection('Lis le fichier demandé.'))
        self.addCleanup(self.backend.RUN_CONTEXT.reset, self.backend.RUN_CONTEXT.set(self.deps))
        for name, options in (
                ('WORKSPACE', {'new': self.workspace}),
                ('conversation_dir', {'return_value': self.workspace}),
                ('ensure_conversation', {}),
                ('emit', {'side_effect': self.engine.observe_event}),
                ('request_approval', {'return_value': True})):
            context = patch.object(self.backend, name, **options)
            context.start(); self.addCleanup(context.stop)

    def test_corrected_plan_recovers_after_four_different_invalid_indices(self):
        self.engine.commentary_enabled = self.engine.commentary_pending = True
        for index in (2, 3, 4, 5):
            result = self.engine.invoke(self.backend.update_plan, ['Lire le fichier'], index, 'Je lis le fichier.')
            self.assertTrue(result.startswith('Erreur outil'), result)
        self.assertFalse((self.workspace/'plan.json').exists())
        self.assertTrue(self.engine.commentary_pending)
        result = json.loads(self.engine.invoke(self.backend.update_plan, ['Lire le fichier'], 0, 'Je lis maintenant le fichier.'))
        self.assertEqual(result['current_step'], 0)
        self.assertEqual(json.loads((self.workspace/'plan.json').read_text())['steps'], ['Lire le fichier'])
        self.assertFalse(self.engine.commentary_pending)
        receipts = self.engine.journal.snapshot()['operations']
        self.assertEqual([row['status'] for row in receipts], ['failed'] * 4 + ['succeeded'])
        (self.workspace/'answer.txt').write_text('42')
        self.assertIn('42', self.engine.invoke(self.backend.read_file, 'answer.txt'))

    def test_identical_invalid_plan_loop_is_still_detected(self):
        for _ in range(2):
            self.engine.invoke(self.backend.update_plan, ['Lire'], 9, 'Je lis.')
            self.engine.check_progress()
        for _ in range(2):
            blocked = json.loads(self.engine.invoke(self.backend.update_plan, ['Lire'], 9, 'Je lis.'))
            self.assertEqual(blocked['category'], 'no_progress')
            self.engine.check_progress()
        self.engine.invoke(self.backend.update_plan, ['Lire'], 9, 'Je lis.')
        with self.assertRaisesRegex(RuntimeError, 'mêmes résultats'):
            self.engine.check_progress()

    def visible(self, name):
        self.deps.tools.enabled.add(name)
        return asyncio.run(self.backend.prepare_tool_for_request(
            SimpleNamespace(prompt='Lecture seule, sans Internet.', messages=[], deps=self.deps),
            ToolDefinition(name=name, description='Fixture', parameters_json_schema={})))

    def test_visible_read_only_command_still_refuses_mutation_and_remote_tools(self):
        self.engine.focus('Lecture seule, sans Internet. Aucune modification.')
        definition = self.visible('run_command')
        self.assertIsNotNone(definition)
        self.assertIn('Lecture seule active', definition.description)
        observed = json.loads(self.engine.invoke(self.backend.run_command, 'pwd'))
        self.assertTrue(observed['passed'], observed)
        self.assertIn(str(self.workspace), observed['output'])
        denied = json.loads(self.engine.invoke(self.backend.run_command, 'touch forbidden'))
        self.assertEqual(denied['category'], 'read_only')
        self.assertFalse(denied['executed'])
        self.assertFalse((self.workspace/'forbidden').exists())
        for name in ('run_host_command', 'write_file', 'search_web', 'read_webpage'):
            with self.subTest(tool=name):
                self.assertIsNone(self.visible(name))
        self.assertTrue(self.engine.read_only)

    def test_untrusted_read_only_context_keeps_shell_hidden(self):
        self.engine.focus('Lecture seule. Aucune modification.')
        self.engine.trusted_diagnostics = False
        self.assertIsNone(self.visible('run_command'))
        self.assertIsNotNone(self.visible('read_file'))


if __name__ == '__main__':
    unittest.main()
