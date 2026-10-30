"""RLM experiment contract tests. Docker integration is explicit, never implicit."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('rlm_experiment', Path(__file__).with_name('experiment-rlm.py'))
rlm = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = rlm
spec.loader.exec_module(rlm)


class RlmTests(unittest.TestCase):
    def test_infrastructure_and_harness_failures_are_not_model_scores(self):
        self.assertEqual(rlm.failure_origin(TimeoutError('provider timed out')), 'infrastructure')
        self.assertEqual(rlm.failure_origin(TypeError('bad adapter')), 'harness')
        self.assertEqual(rlm.failure_origin(ValueError('invalid model JSON')), 'model_or_protocol')

    def test_structured_python_response_requires_bounded_code(self):
        self.assertEqual(rlm.CodeStep(code='print(1)').model_dump(), {'code': 'print(1)'})
        for value in ({}, {'code': ''}, {'code': 'x'*8001}):
            with self.assertRaises(ValueError):
                rlm.CodeStep(**value)

    def test_quotes_are_checked_against_named_sources(self):
        docs = [{'name': 'a.md', 'text': 'Exact evidence.'}]
        self.assertTrue(rlm.validate_answer([{'document': 'a.md', 'quote': 'Exact evidence.'}], docs))
        for answer in ([], [{'document': 'b.md', 'quote': 'Exact evidence.'}],
                       [{'document': 'a.md', 'quote': 'fabricated'}],
                       [{'document': 'a.md', 'quote': ''}], [{'document': [], 'quote': 'x'}]):
            self.assertFalse(rlm.validate_answer(answer, docs))

    def test_fixture_seeds_have_distinct_hidden_expectations(self):
        left, question, expected = rlm.fixture(13)
        right, other_question, other_expected = rlm.fixture(29)
        self.assertNotEqual(question, other_question)
        self.assertNotEqual(expected, other_expected)
        self.assertTrue(rlm.validate_answer(expected, left))
        self.assertGreater(sum(len(x['text']) for x in left), 150000)
        self.assertFalse(rlm.validate_answer(expected, right))

    def test_external_corpus_is_explicit_bounded_and_not_auto_discovered(self):
        for docs in ([], [{'name': 'x', 'text': 'a'}]*2,
                     [{'name': 'x', 'text': 'a'*4_000_001}], [{'name': 'x', 'text': 1}]):
            with self.assertRaises(ValueError):
                rlm.validate_documents(docs)

    def test_sandbox_configuration_has_no_host_access(self):
        command = rlm.DockerContext([], Mock()).command()
        self.assertEqual(command[command.index('--network')+1], 'none')
        self.assertEqual(command[command.index('--cap-drop')+1], 'ALL')
        self.assertIn('--read-only', command)
        self.assertIn('no-new-privileges', command)
        self.assertNotIn('--privileged', command)
        self.assertNotIn('-v', command)
        self.assertNotIn('--mount', command)
        self.assertNotIn('-e', command)

    def test_forged_worker_requests_cannot_bypass_host_subcall_budget(self):
        child = Mock(return_value='ok')
        sandbox = rlm.DockerContext([], child, max_subcalls=1)
        sandbox.send = Mock()
        sandbox.receive = Mock(side_effect=[{'type': 'query', 'prompt': 'one'},
                                            {'type': 'query', 'prompt': 'two'}])
        with self.assertRaises(rlm.ExperimentLimit):
            sandbox.execute('print(1)')
        child.assert_called_once_with('one')

    def test_subcall_prompt_length_checked_before_provider(self):
        child = Mock()
        sandbox = rlm.DockerContext([], child)
        sandbox.send = Mock()
        sandbox.receive = Mock(return_value={'type': 'query', 'prompt': 'x'*12001})
        with self.assertRaises(rlm.ExperimentLimit):
            sandbox.execute('print(1)')
        child.assert_not_called()

    def test_root_only_receives_metadata_before_inspecting_context(self):
        docs = [{'name': 'a.md', 'text': 'PRIVATE SOURCE BODY'}]
        complete = Mock(return_value=json.dumps({'answer': [{'document': 'a.md', 'quote': 'PRIVATE SOURCE BODY'}]}))
        with patch.object(rlm, 'DockerContext') as sandbox:
            sandbox.return_value.__enter__.return_value.subcalls = 0
            result = rlm.run_rlm(docs, 'Question', complete)
        self.assertTrue(result['citations_valid'])
        self.assertNotIn('PRIVATE SOURCE BODY', complete.call_args.args[1])
        self.assertIn('a.md', complete.call_args.args[1])

    def test_sdk_usage_and_shared_root_child_limit(self):
        from pydantic_ai import Agent
        from pydantic_ai.models.function import FunctionModel
        from pydantic_ai.messages import ModelResponse, TextPart
        from pydantic_ai.usage import RequestUsage
        model = rlm.LiveModel.__new__(rlm.LiveModel)
        fake = FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart('ok')],
            usage=RequestUsage(input_tokens=17, output_tokens=3)))
        model.agents = {role: Agent(fake) for role in ('root', 'child')}
        model.settings = {'max_tokens': 100}
        model.request_limit, model.requests, model.tokens, model.records = 2, 0, 0, []
        self.assertEqual(model('root', 'question'), 'ok')
        self.assertEqual(model('child', 'excerpt'), 'ok')
        self.assertEqual(model.tokens, 40)
        self.assertEqual(len(model.records), 2)
        with self.assertRaises(rlm.ExperimentLimit):
            model('root', 'one too many')

    def test_native_calls_and_valid_json_text_share_the_action_contract(self):
        from pydantic_ai import Agent
        from pydantic_ai.models.function import FunctionModel
        from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
        for native in (False, True):
            def respond(messages, info):
                return ModelResponse(parts=[ToolCallPart('inspect_context', {'code': 'print(1)'})] if native else
                    [TextPart('{"code":"print(1)"}')])
            agent = Agent(FunctionModel(respond), output_type=rlm.output_types()['root'], retries=0)
            result = agent.run_sync('Inspect context').output
            text = result if isinstance(result, str) else result.model_dump_json()
            self.assertEqual(rlm.parse_response(text), {'code': 'print(1)'})

    def test_refuses_overwriting_evidence_and_live_custom_corpus_without_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'report.json'
            target.write_text('old')
            with self.assertRaises(FileExistsError):
                rlm.run_experiment(output=target)
            self.assertEqual(target.read_text(), 'old')
            with self.assertRaises(ValueError):
                rlm.run_experiment(output=Path(directory)/'new.json', corpus=[{'name': 'a', 'text': 'x'}])


@unittest.skipUnless(os.environ.get('TRINITY_RLM_TEST_IMAGE'), 'Explicit local sandbox image required')
class DockerIntegration(unittest.TestCase):
    def test_real_worker_state_subcalls_and_isolation(self):
        child = Mock(return_value='child receipt')
        with rlm.DockerContext([{'name': 'a', 'text': 'source'}], child,
                              image=os.environ['TRINITY_RLM_TEST_IMAGE']) as sandbox:
            first = sandbox.execute("x = context[0]['text']; print(llm_query(x))")
            self.assertIn('child receipt', first['text'])
            second = sandbox.execute("import os; print(x); print(os.path.exists('/root/.trinity-agent')); print(os.environ.get('TRINITY_OVHCLOUD_API_KEY'))")
            self.assertEqual(second['text'], 'source\nFalse\nNone\n')
            child.assert_called_once_with('source')

    def test_infinite_loop_times_out_and_container_is_removed(self):
        sandbox = rlm.DockerContext([], Mock(), image=os.environ['TRINITY_RLM_TEST_IMAGE'], timeout=1)
        with self.assertRaises(rlm.ExperimentLimit), sandbox:
            sandbox.execute('while True: pass')
        self.assertIsNotNone(sandbox.process.poll())


if __name__ == '__main__':
    unittest.main()
