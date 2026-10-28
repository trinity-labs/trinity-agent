"""Native SDK recovery after unavailable tools, without bypassing permissions."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ovh_execution import ExecutionCapability, RunDependencies, WorkflowController
from pydantic_ai import Agent, Tool
from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel


class ToolFallbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(sys, 'argv', ['test-tool-fallback', '--billing-status']):
            spec = importlib.util.spec_from_file_location('fallback_backend', ROOT/'ovh-agent.py')
            cls.backend = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.backend)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        (self.workspace/'data.json').write_text('{"total":42}\n')
        self.engine = WorkflowController(self.workspace, self.workspace/'execution')
        self.engine.focus('Lis data.json et vérifie le total.')
        self.deps = RunDependencies(engine=self.engine)

    def run_model(self, model, *, approved=True, tools=None):
        a = self.backend
        agent = Agent(FunctionModel(model), deps_type=RunDependencies,
            tools=tools or [Tool(a.run_command), Tool(a.read_file)],
            capabilities=[ExecutionCapability(a.RUN_CONTEXT)])
        agent.output_validator(a.validate_task_output)
        with patch.object(a, 'WORKSPACE', self.workspace), patch.object(
                a, 'request_approval', return_value=approved), patch.object(
                a, 'read_task_state', return_value={}), patch.object(
                a, 'emit', side_effect=self.engine.observe_event):
            return agent.run_sync(self.engine.goal, deps=self.deps)

    def test_missing_executable_cannot_end_task_before_native_file_alternative(self):
        rounds = []
        def model(messages, info):
            rounds.append(1)
            if len(rounds) == 1:
                return ModelResponse(parts=[ToolCallPart('run_command', {'command': 'trinity_missing_fixture_json data.json'})])
            if len(rounds) == 2:
                return ModelResponse(parts=[TextPart('Programme absent, je ne peux pas terminer.')])
            if len(rounds) == 3:
                self.assertIn('dernier échec', str(messages[-1].parts))
                return ModelResponse(parts=[ToolCallPart('read_file', {'path': 'data.json'})])
            return ModelResponse(parts=[TextPart('Le total vérifié est 42.')])
        result = self.run_model(model)
        self.assertEqual(len(rounds), 4)
        self.assertEqual(result.output, 'Le total vérifié est 42.')
        operations = self.engine.journal.snapshot()['operations']
        self.assertEqual([(r['tool'], r['status']) for r in operations],
                         [('run_command', 'not_applied'), ('read_file', 'succeeded')])
        self.assertFalse(self.engine.final_status()['incomplete'])

    def test_unknown_tool_and_invalid_arguments_recover_through_sdk(self):
        for call in (ToolCallPart('invented_tool', {}), ToolCallPart('read_file', {})):
            with self.subTest(tool=call.tool_name):
                self.engine.focus('Lis data.json et vérifie le total.')
                rounds = []
                def model(messages, info):
                    rounds.append(1)
                    if len(rounds) == 1:
                        return ModelResponse(parts=[call])
                    if len(rounds) == 2:
                        return ModelResponse(parts=[TextPart('Impossible de continuer.')])
                    if len(rounds) == 3:
                        return ModelResponse(parts=[ToolCallPart('read_file', {'path': 'data.json'})])
                    return ModelResponse(parts=[TextPart('42')])
                self.assertEqual(self.run_model(model).output, '42')
                self.assertEqual(len(rounds), 4)

    def test_recovery_is_bounded_when_model_still_cannot_continue(self):
        rounds = []
        def model(messages, info):
            rounds.append(1)
            if len(rounds) == 1:
                return ModelResponse(parts=[ToolCallPart('run_command', {'command': 'trinity_missing_fixture_json data.json'})])
            return ModelResponse(parts=[TextPart('Blocage persistant : programme absent.')])
        self.assertIn('Blocage persistant', self.run_model(model).output)
        self.assertEqual(len(rounds), 3)
        self.assertEqual(len(self.engine.journal.snapshot()['operations']), 1)

    def test_read_only_denial_does_not_request_an_alternative_write(self):
        self.engine.focus('Lecture seule. Aucune modification.')
        rounds = []
        def model(messages, info):
            rounds.append(1)
            if len(rounds) == 1:
                return ModelResponse(parts=[ToolCallPart('run_command', {'command': 'touch forbidden'})])
            return ModelResponse(parts=[TextPart('Écriture refusée par la consigne de lecture seule.')])
        self.run_model(model)
        self.assertEqual(len(rounds), 2)
        self.assertFalse((self.workspace/'forbidden').exists())
        self.assertEqual(self.engine._validation_retries, 0)

    def test_failed_mutation_remains_uncertain_without_replay(self):
        rounds = []
        def model(messages, info):
            rounds.append(1)
            if len(rounds) == 1:
                return ModelResponse(parts=[ToolCallPart('run_command', {'command': 'printf partial >> counter; exit 1'})])
            return ModelResponse(parts=[TextPart('La commande a échoué après une écriture.')])
        self.assertTrue(self.run_model(model).output.startswith('Tâche incomplète.'))
        self.assertEqual(len(rounds), 2)
        self.assertEqual((self.workspace/'counter').read_text(), 'partial')
        self.assertEqual(len(self.engine.journal.snapshot()['unresolved_mutations']), 1)

    def test_progress_and_discovery_do_not_resolve_a_failure(self):
        messages = [ModelRequest(parts=[
            ToolReturnPart('read_file', 'Erreur outil : chemin introuvable.', outcome='failed'),
            ToolReturnPart('discover_tools', '{"activated": ["read_file"]}'),
            ToolReturnPart('update_plan', '{"explanation": "Je continue"}')])]
        self.assertTrue(ExecutionCapability.unfinished_tool(messages))
        messages.append(ModelRequest(parts=[ToolReturnPart('read_file', 'Fichier : data.json\n42')]))
        self.assertFalse(ExecutionCapability.unfinished_tool(messages))

    def test_denied_and_historical_failures_do_not_trigger_recovery(self):
        for content in ('Erreur outil : accès refusé (private).',
                        json.dumps({'error': 'approval_denied'}),
                        json.dumps({'error': 'policy_denied'}),
                        json.dumps({'category': 'recovery_required'})):
            with self.subTest(content=content):
                self.assertFalse(ExecutionCapability.unfinished_tool([
                    ModelRequest(parts=[ToolReturnPart('run_command', content, outcome='failed')])]))
        self.assertFalse(ExecutionCapability.unfinished_tool([
            ModelRequest(parts=[ToolReturnPart('read_file', 'missing', outcome='failed')]),
            ModelRequest(parts=[UserPromptPart('Nouvelle demande indépendante')])]))


if __name__ == '__main__':
    unittest.main()
