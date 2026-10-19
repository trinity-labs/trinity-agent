"""Evaluation tests challenge independent oracles, reporting and paid opt-in."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('agent_evaluator', Path(__file__).with_name('evaluate-agent.py'))
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class AgentEvaluationTests(unittest.TestCase):
    def test_production_fixture_suite_is_offline_and_seeded(self):
        report = evaluation.run_campaign(seed=271828)
        self.assertTrue(report['passed'], [(row['id'], row['oracle'], row['error'], row['acceptance'])
                                           for row in report['scenarios'] if not row['passed']])
        self.assertFalse(report['model_performance_measured'])
        self.assertEqual(report['metrics']['cost_eur'], 0)
        self.assertEqual(report['metrics']['false_successes'], 0)
        self.assertEqual(report['metrics']['unsupported_controller_successes'], 0)
        self.assertTrue(next(row for row in report['scenarios'] if row['id'] == 'correction_compaction')['compacted'])
        self.assertTrue(next(row for row in report['scenarios'] if row['id'] == 'interrupted_write')['interruption_triggered'])

    def test_dishonest_model_text_cannot_certify_unchanged_files(self):
        report = evaluation.run_campaign(cases=('multi_file',),
            model_factory=lambda fixture: evaluation.reference_model(fixture, dishonest=True))
        row = report['scenarios'][0]
        self.assertFalse(report['passed'])
        self.assertFalse(row['oracle']['passed'])
        self.assertFalse(row['controller_accepted'])
        self.assertGreater(row['model_false_claim_attempts'], 0)
        self.assertEqual(report['metrics']['false_successes'], 0)

    def test_request_budget_stops_campaign_and_preserves_partial_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'result.json'
            report = evaluation.run_campaign(cases=('multi_file', 'preexisting_change'), request_limit=1, output=path)
            self.assertFalse(report['complete'])
            self.assertEqual(report['stopped_reason'], 'campaign_budget')
            self.assertEqual(report['metrics']['requests'], 1)
            self.assertEqual(json.loads(path.read_text()), report)
            with self.assertRaises(FileExistsError):
                evaluation.run_campaign(cases=('multi_file',), output=path)

    def test_transport_failure_is_not_scored_as_model_incorrectness(self):
        from pydantic_ai.models.function import FunctionModel
        def failing(messages, info):
            raise ConnectionError('controlled provider connection reset')
        report = evaluation.run_campaign(cases=('multi_file', 'preexisting_change'), model_factory=lambda _: FunctionModel(failing))
        self.assertEqual(report['scenarios'][0]['error']['origin'], 'infrastructure')
        self.assertEqual(report['metrics']['infrastructure_failures'], 1)
        self.assertEqual(len(report['scenarios']), 1)
        self.assertEqual(report['stopped_reason'], 'infrastructure_failure')
        self.assertFalse(report['complete'])
        self.assertFalse(report['scenarios'][0]['controller_accepted'])

    def test_compaction_keeps_the_loop_of_reused_provider_clients(self):
        import asyncio
        from pydantic_ai.models.wrapper import WrapperModel
        owner = []
        class LoopBoundProvider(WrapperModel):
            async def request(self, messages, settings, parameters):
                current = asyncio.get_running_loop()
                if not owner:
                    owner.append(current)
                if current is not owner[0]:
                    raise RuntimeError('Provider future attached to a different event loop')
                return await self.wrapped.request(messages, settings, parameters)
        report = evaluation.run_campaign(cases=('multi_file', 'correction_compaction'),
            model_factory=lambda fixture: LoopBoundProvider(evaluation.reference_model(fixture)))
        self.assertTrue(report['passed'], [row['error'] for row in report['scenarios']])

    def test_changed_unrelated_bytes_fail_oracle_even_with_correct_port(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = evaluation.make_fixture(root, 'already_satisfied', 42)
            self.assertTrue(evaluation.oracle(fixture, root)['passed'])
            (root/'user-note.txt').write_text('model overwrote user data')
            result = evaluation.oracle(fixture, root)
            self.assertFalse(result['passed'])
            self.assertIn('user-note.txt', result['mismatches'])

    def test_bad_limits_and_unknown_cases_fail_before_backend_load(self):
        with patch.object(evaluation, 'load_backend', side_effect=AssertionError('must not initialize backend')):
            for parameters in ({'request_limit': 0}, {'token_limit': -1}, {'max_cost_eur': float('nan')},
                               {'cases': ('invented',)}, {'cases': ('multi_file', 'multi_file')}):
                with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                    evaluation.run_campaign(**parameters)


if __name__ == '__main__':
    unittest.main()
