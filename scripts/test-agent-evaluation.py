"""Evaluation tests challenge independent oracles, reporting and paid opt-in."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

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
        request = report['scenarios'][0]['model_requests'][0]
        self.assertFalse(request['response_received'])
        self.assertEqual(request['error_type'], 'ConnectionError')
        self.assertTrue(all(value is None for value in request['usage'].values()))
        self.assertGreaterEqual(request['latency_ms'], 0)

    def test_live_settings_default_to_production_and_only_explicit_values_override(self):
        production = {'max_tokens': 8192, 'openai_reasoning_effort': 'low', 'temperature': .2}
        backend = SimpleNamespace(generation_settings=Mock(return_value=production),
            model_capabilities=Mock(return_value={'reasoning': True, 'reasoning_efforts': ['none', 'low']}))
        analysis = {'domain': 'code', 'complexity': 'standard'}
        settings = evaluation.case_settings(backend, 'fixture-model', 'fixture request', analysis)
        backend.generation_settings.assert_called_once_with('fixture-model', 'fixture request', analysis)
        self.assertEqual(settings, {**production, 'timeout': 45})
        overridden = evaluation.case_settings(backend, 'fixture-model', 'fixture request', analysis,
            reasoning_effort='none', max_output_tokens=4096)
        self.assertEqual(overridden, {**settings, 'openai_reasoning_effort': 'none', 'max_tokens': 4096})
        self.assertEqual(production['max_tokens'], 8192)
        with self.assertRaises(ValueError):
            evaluation.case_settings(backend, 'fixture-model', '', analysis, reasoning_effort='high')
        # An explicit empty schema is different from an unknown effort enum.
        backend.model_capabilities.return_value['reasoning_efforts'] = []
        with self.assertRaises(ValueError):
            evaluation.case_settings(backend, 'fixture-model', '', analysis, reasoning_effort='low')

    def test_optional_usage_distinguishes_missing_zero_and_partial_coverage(self):
        from pydantic_ai.messages import ModelResponse
        from pydantic_ai.usage import RequestUsage
        missing = evaluation.response_usage(ModelResponse(parts=[], usage=RequestUsage(input_tokens=20, output_tokens=5)))
        present = evaluation.response_usage(ModelResponse(parts=[], usage=RequestUsage(input_tokens=20, output_tokens=5,
            cache_read_tokens=0, cache_write_tokens=3, details={'reasoning_tokens': 0})))
        self.assertIsNone(missing['reasoning_tokens'])
        self.assertIsNone(missing['cache_read_tokens'])
        self.assertEqual(present['reasoning_tokens'], 0)
        self.assertEqual(present['cache_read_tokens'], 0)
        summary = evaluation.optional_usage_summary([{'usage': missing}, {'usage': present}])
        self.assertEqual(summary['reasoning_tokens'],
            {'observed_tokens': 0, 'requests_with_counter': 1, 'attempted_requests': 2})
        self.assertEqual(summary['cache_write_tokens']['observed_tokens'], 3)

    def test_recovery_keeps_settings_and_records_every_request(self):
        report = evaluation.run_campaign(cases=('interrupted_write',), reasoning_effort='none', max_output_tokens=4096)
        row = report['scenarios'][0]
        self.assertTrue(report['passed'], row['error'])
        self.assertTrue(row['interruption_triggered'])
        self.assertEqual(row['generation_settings'],
            {'max_tokens': 4096, 'openai_reasoning_effort': 'none', 'timeout': 45})
        self.assertGreater(len(row['model_requests']), 4)
        self.assertEqual(len(row['model_requests']), len(row['schemas']))
        for request, schema in zip(row['model_requests'], row['schemas']):
            self.assertEqual(request['settings_fingerprint'], row['settings_fingerprint'])
            self.assertEqual(request['schema_fingerprint'], schema['fingerprint'])
            self.assertTrue(request['response_received'])
            self.assertGreaterEqual(request['latency_ms'], 0)
        self.assertEqual(row['prompt_analysis']['paths'], ['current.json', 'app.py'])

    def test_cli_forwards_optional_settings_without_implicit_override(self):
        for flags, expected in (([], (None, None)),
                               (['--reasoning-effort', 'none', '--max-output-tokens', '4096'], ('none', 4096))):
            with self.subTest(flags=flags), patch.object(evaluation.sys, 'argv', ['evaluate-agent.py', '--output', 'unused.json', *flags]), \
                    patch.object(evaluation, 'run_campaign', return_value={'passed': True}) as campaign, \
                    patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(evaluation.main(), 0)
                self.assertEqual((campaign.call_args.kwargs['reasoning_effort'],
                                  campaign.call_args.kwargs['max_output_tokens']), expected)

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
                               {'max_output_tokens': 0}, {'max_output_tokens': True}, {'reasoning_effort': 'invented'},
                               {'cases': ('invented',)}, {'cases': ('multi_file', 'multi_file')}):
                with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                    evaluation.run_campaign(**parameters)


if __name__ == '__main__':
    unittest.main()
