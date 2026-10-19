"""Offline integration of tool schemas, persistent recovery and final evidence."""
import importlib.util
import inspect
import json
from pathlib import Path
import subprocess
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ovh_execution import WorkflowController, guarded_tool
from pydantic_ai import Agent, ModelRetry, Tool
from pydantic_ai.messages import ModelResponse, RetryPromptPart, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        self.directory = self.root / "execution"
        self.events = []
        self.engine = WorkflowController(self.workspace, self.directory, publish=self.events.append)
        self.engine.focus("Modify only the requested fixture and verify its outcome.")

    def operations(self, engine=None):
        return (engine or self.engine).journal.snapshot()["operations"]

    def test_sdk_tool_schema_retains_named_typed_arguments_and_real_execution(self):
        calls, rounds = [], []

        def read_file(path: str, start: int = 1) -> str:
            """Read the requested fixture path from a specific starting line."""
            calls.append((path, start))
            return "timeout is a literal in this source file"

        wrapped = guarded_tool(read_file, lambda: self.engine)
        self.assertEqual(inspect.signature(wrapped), inspect.signature(read_file))

        def model(messages, info):
            rounds.append(1)
            if len(rounds) == 1:
                definition = info.function_tools[0]
                self.assertEqual(definition.name, "read_file")
                self.assertEqual(set(definition.parameters_json_schema["properties"]), {"path", "start"})
                self.assertEqual(definition.parameters_json_schema["properties"]["start"]["type"], "integer")
                return ModelResponse(parts=[ToolCallPart("read_file", {"path": "app.py", "start": 3})])
            returned = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
            self.assertIn("literal", returned[-1].content)
            return ModelResponse(parts=[TextPart("The fixture was read.")])

        result = Agent(FunctionModel(model), tools=[Tool(wrapped)]).run_sync("Read the fixture")
        self.assertEqual(result.output, "The fixture was read.")
        self.assertEqual(calls, [("app.py", 3)])
        self.assertEqual(self.operations()[-1]["status"], "succeeded")

    def test_read_file_contents_are_opaque_even_when_they_look_like_errors(self):
        for content in ('timeout', '{"status":"failed"}', 'Erreur: deliberately documented example', 'exit=1\nexample'):
            with self.subTest(content=content):
                def read_file(path: str) -> str:
                    return content
                self.assertEqual(self.engine.invoke(read_file, "app.py"), content)
                self.assertEqual(self.operations()[-1]["status"], "succeeded")

    def test_explicit_failed_read_event_is_not_mistaken_for_source_content(self):
        def read_file(path: str) -> str:
            self.engine.observe_event({"type": "tool_end", "success": False})
            return "No such file"

        self.engine.invoke(read_file, "missing.py")
        self.assertEqual(self.operations()[-1]["status"], "failed")
        self.assertFalse(self.engine.journal.snapshot()["unresolved_mutations"])

    def test_nonzero_command_cannot_become_success_from_output_or_event(self):
        def run_command(command: str, cwd: str = ".") -> str:
            self.engine.observe_event({"type": "tool_end", "success": True})
            return "exit=1\nAll tests passed!"

        self.engine.invoke(run_command, "fixture")
        self.assertEqual(self.operations()[-1]["status"], "unknown")
        self.assertEqual(self.engine.final_status()["command_status"], "failed")
        self.assertTrue(self.engine.final_status()["incomplete"])

    def test_explicit_executor_failure_cannot_be_overridden_by_zero_exit(self):
        def run_command(command: str) -> str:
            self.engine.observe_event({'type': 'tool_end', 'success': False})
            return 'exit=0\nThe child exited but the controlling check failed.'
        self.engine.invoke(run_command, 'fixture')
        self.assertNotEqual(self.operations()[-1]['status'], 'succeeded')

    def test_claimed_write_success_requires_actual_expected_file_content(self):
        target = self.workspace / 'app.txt'
        target.write_text('before')
        def write_file(path: str, content: str) -> str:
            return 'Fichier écrit: app.txt'
        result = self.engine.invoke(write_file, 'app.txt', 'after')
        self.assertEqual(target.read_text(), 'before')
        self.assertEqual(json.loads(result)['category'], 'incorrect_result')
        self.assertTrue(self.engine.final_status()['incomplete'])

    def test_preexisting_identical_file_write_is_verified_without_false_failure(self):
        target = self.workspace / 'app.txt'
        target.write_text('same')
        def write_file(path: str, content: str) -> str:
            target.write_text(content)
            return 'Fichier écrit.'
        self.engine.invoke(write_file, 'app.txt', 'same')
        self.assertEqual(self.operations()[-1]['verification_status'], 'verified')
        self.assertEqual(self.operations()[-1]['verification_outcome'], 'applied')
        self.assertFalse(self.engine.final_status()['incomplete'])

    def test_keyboard_interrupt_is_preserved_and_journaled(self):
        def run_command(command: str) -> str:
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.engine.invoke(run_command, 'fixture')
        self.assertEqual(self.operations()[-1]['status'], 'unknown')

    def test_missing_path_attempts_do_not_block_a_discovered_existing_file(self):
        calls = []
        def read_file(path: str) -> str:
            calls.append(path)
            target = self.workspace / path
            if not target.exists():
                self.engine.observe_event({'type': 'tool_end', 'success': False})
                return 'No such file'
            return target.read_text()
        for path in ('missing-a', 'missing-b', 'missing-c', 'missing-d'):
            self.engine.invoke(read_file, path)
        (self.workspace / 'found.txt').write_text('actual content')
        self.assertEqual(self.engine.invoke(read_file, 'found.txt'), 'actual content')
        self.assertEqual(calls[-1], 'found.txt')

    def test_zero_exit_with_error_words_in_output_is_successful_command(self):
        def run_command(command: str) -> str:
            return "exit=0\nDocumented errors: timeout, permission denied, failure"

        self.engine.invoke(run_command, "show fixture")
        self.assertEqual(self.operations()[-1]["status"], "succeeded")
        self.assertEqual(self.engine.final_status()["command_status"], "passed")

    def test_interrupted_write_is_not_replayed_and_hashes_reconcile_after_restart(self):
        target = self.workspace / "app.txt"
        target.write_text("before")
        calls = []

        def write_file(path: str, content: str) -> str:
            calls.append(path)
            target.write_text(content)
            raise TimeoutError("result was lost after the write")

        # A caught TimeoutError can be reconciled immediately. Abrupt process
        # death cannot run finally/exception handlers: the durable intent and
        # file proof must survive that actual crash window.
        script = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from ovh_execution import WorkflowController
workspace, directory = Path(sys.argv[2]), Path(sys.argv[3])
engine = WorkflowController(workspace, directory)
def write_file(path: str, content: str) -> str:
    (workspace / path).write_text(content)
    os._exit(9)
engine.invoke(write_file, 'app.txt', 'after')
"""
        crashed = subprocess.run([sys.executable, "-c", script, str(ROOT), str(self.workspace), str(self.directory)],
                                 capture_output=True, text=True, timeout=20)
        self.assertEqual(crashed.returncode, 9, crashed.stderr)
        self.assertEqual(self.operations()[-1]["status"], "running")
        identifier = self.operations()[-1]["id"]
        restarted = WorkflowController(self.workspace, self.directory)
        blocked = json.loads(restarted.invoke(write_file, "app.txt", "after"))
        self.assertEqual(blocked["category"], "recovery_required")
        self.assertEqual(calls, [])
        self.assertEqual(target.read_text(), "after")
        reconciled = restarted.reconcile(identifier)
        self.assertEqual(reconciled["status"], "applied")
        self.assertEqual(reconciled["verification_status"], "verified")
        self.assertFalse(restarted.final_status()["incomplete"])

    def test_crash_between_file_return_and_hash_probe_remains_pending(self):
        target = self.workspace / 'app.txt'
        target.write_text('before')
        def write_file(path: str, content: str) -> str:
            target.write_text(content)
            return 'Fichier écrit.'
        with patch.object(self.engine.journal, 'reconcile', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                self.engine.invoke(write_file, 'app.txt', 'after')
        identifier = self.operations()[-1]['id']
        self.assertEqual(self.operations()[-1]['execution_status'], 'succeeded')
        restarted = WorkflowController(self.workspace, self.directory)
        self.assertIn(identifier, restarted.journal.snapshot()['unresolved_mutations'])
        self.assertFalse(json.loads(restarted.invoke(write_file, 'app.txt', 'after'))['executed'])
        self.assertTrue(restarted.final_status()['incomplete'])
        self.assertEqual(restarted.reconcile(identifier)['status'], 'applied')
        self.assertFalse(restarted.final_status()['incomplete'])

    def test_interrupted_write_with_external_change_remains_unknown(self):
        target = self.workspace / "app.txt"
        target.write_text("before")

        def write_file(path: str, content: str) -> str:
            target.write_text(content)
            raise TimeoutError("lost result")

        with self.assertRaises(TimeoutError):
            self.engine.invoke(write_file, "app.txt", "after")
        identifier = self.operations()[-1]["id"]
        target.write_text("concurrent user edit")
        self.assertEqual(self.engine.reconcile(identifier)["status"], "unknown")
        self.assertTrue(self.engine.final_status()["incomplete"])
        self.assertEqual(target.read_text(), "concurrent user edit")

    def test_rejected_file_preflight_is_not_an_unresolved_mutation(self):
        def edit_file(path: str, old_text: str, new_text: str) -> str:
            return "Modification refusée: 0 occurrence(s), exactement 1 attendue."

        self.engine.invoke(edit_file, "missing.py", "absent", "replacement")
        operation = self.operations()[-1]
        self.assertEqual(operation["status"], "not_applied")
        self.assertEqual(operation["verification_status"], "verified")
        self.assertFalse(self.engine.final_status()["incomplete"])

    def test_modelretry_before_file_write_is_reconciled_before_corrected_retry(self):
        target = self.workspace / "app.css"
        target.write_text("before")
        inspected = []

        def write_file(path: str, content: str) -> str:
            if not inspected:
                raise ModelRetry("Inspect styles before editing this existing CSS file.")
            target.write_text(content)
            return "Fichier écrit."

        with self.assertRaises(ModelRetry):
            self.engine.invoke(write_file, "app.css", "after")
        self.assertFalse(self.engine.journal.snapshot()["unresolved_mutations"])
        inspected.append(True)
        self.assertEqual(self.engine.invoke(write_file, "app.css", "after"), "Fichier écrit.")
        self.assertEqual(target.read_text(), "after")
        self.assertFalse(self.engine.final_status()["incomplete"])

    def test_invalid_batch_preflight_does_not_poison_following_valid_edit(self):
        target = self.workspace / "app.css"
        target.write_text("before")

        def edit_file_batch(path: str, replacements: list[dict[str, str]]) -> str:
            if not replacements:
                raise ModelRetry("1 to 16 replacements required. No changes applied.")
            value = target.read_text()
            for replacement in replacements:
                value = value.replace(replacement["before"], replacement["after"])
            target.write_text(value)
            return "Fichier modifié."

        with self.assertRaises(ModelRetry):
            self.engine.invoke(edit_file_batch, "app.css", [])
        self.assertFalse(self.engine.journal.snapshot()["unresolved_mutations"])
        self.engine.invoke(edit_file_batch, "app.css", [{"before": "before", "after": "after"}])
        self.assertEqual(target.read_text(), "after")
        self.assertFalse(self.engine.final_status()["incomplete"])

    def test_nonzero_shell_may_have_effects_even_when_model_claims_rollback(self):
        calls = []

        def run_command(command: str) -> str:
            calls.append(command)
            return "exit=1\nRolled back completely"

        self.engine.invoke(run_command, "change service")
        identifier = self.operations()[-1]["id"]
        # Arbitrary shell operations have no independent transaction probe.
        self.assertEqual(self.engine.reconcile(identifier)["status"], "unknown")
        blocked = json.loads(self.engine.invoke(run_command, "change service"))
        self.assertFalse(blocked["executed"])
        self.assertEqual(calls, ["change service"])

    def test_alternating_failed_arguments_do_not_reset_the_no_progress_limit(self):
        calls = []

        def read_file(path: str) -> str:
            calls.append(path)
            self.engine.observe_event({"type": "tool_end", "success": False})
            return "No such file"

        outputs = [self.engine.invoke(read_file, path) for path in ("a", "b", "a", "b", "a")]
        self.assertEqual(calls, ["a", "b", "a", "b"])
        self.assertEqual(json.loads(outputs[-1])["category"], "no_progress")

    def test_steering_invalidates_cached_evidence_and_retains_user_correction(self):
        self.engine.evaluate()
        self.assertIsNotNone(self.engine.last_acceptance)
        revision = self.engine.goal_revision
        self.engine.focus("Correction: inspect JSON content, not byte sizes.")
        self.assertIsNone(self.engine.last_acceptance)
        self.assertEqual(self.engine.goal_revision, revision + 1)
        self.assertIn("inspect JSON content", self.engine.context())
        restarted = WorkflowController(self.workspace, self.directory)
        self.assertIn("inspect JSON content", restarted.context())
        self.assertFalse(json.loads(restarted.context())["previous_conclusions_valid"])

    def test_compacted_context_preserves_unknown_operations_and_contract_without_values(self):
        policy = {'version': 1, 'acceptance': {'version': 1, 'criteria': [
            {'id': 'expected-setting', 'type': 'json_equals', 'path': 'result.json', 'pointer': '/value',
             'value': 'private-expected-fixture-value'}]}}
        (self.workspace / '.ovh-agent-checks.json').write_text(json.dumps(policy))
        self.engine = WorkflowController(self.workspace, self.directory)
        self.engine.focus('Keep user changes; inspect actual JSON values.')
        self.engine.evaluate()
        identifier = self.engine.journal.start('run_host_command', {'command': 'private-command'}, mutation=True)
        restarted = WorkflowController(self.workspace, self.directory)
        context = json.loads(restarted.context())
        self.assertEqual(context['recovery']['unknown_count'], 1)
        self.assertEqual(context['recovery']['operations'][0]['id'], identifier)
        self.assertEqual(context['contract']['criteria'][0], {'id': 'expected-setting', 'type': 'json_equals'})
        self.assertFalse(context['contract']['natural_language_goal_verified'])
        self.assertEqual(context['evidence']['freshness'], 'previous_process_unverified')
        self.assertEqual(context['evidence']['criteria_status'], 'unverified')
        self.assertIn('Keep user changes', context['user_corrections'][-1])
        self.assertNotIn('private-expected-fixture-value', restarted.context())
        self.assertNotIn('private-command', restarted.context())
        for index in range(4):
            restarted.focus(('Long correction ' + str(index) + ' ') * 1000)
        self.assertLessEqual(len(restarted.context()), 4800)

    def test_corrected_write_clears_only_the_previous_failed_postcondition(self):
        target = self.workspace / 'app.txt'
        target.write_text('before')
        applied = []
        def write_file(path: str, content: str) -> str:
            if applied:
                target.write_text(content)
            return 'Fichier écrit.'
        self.engine.invoke(write_file, 'app.txt', 'after')
        self.assertTrue(self.engine.final_status()['incomplete'])
        applied.append(True)
        self.engine.invoke(write_file, 'app.txt', 'after')
        self.assertFalse(self.engine.final_status()['incomplete'])
        self.assertEqual(target.read_text(), 'after')

    def test_unknown_journal_entry_without_return_is_recovered_not_repeated(self):
        self.engine.journal.start("run_command", {"command": "remote mutation"}, mutation=True,
                                  scope=str(self.workspace))
        calls = []

        def run_command(command: str) -> str:
            calls.append(command)
            return "exit=0\ndone"

        restarted = WorkflowController(self.workspace, self.directory)
        self.assertEqual(self.operations(restarted)[-1]["status"], "unknown")
        self.assertFalse(json.loads(restarted.invoke(run_command, "remote mutation"))["executed"])
        self.assertEqual(calls, [])
        self.assertTrue(restarted.final_status()["incomplete"])

    def test_final_model_success_is_retried_until_independent_criterion_passes(self):
        policy = {"version": 1, "checks": [], "acceptance": {"version": 1, "criteria": [
            {"id": "ready", "type": "json_equals", "path": "result.json", "pointer": "/ready", "value": True}]}}
        (self.workspace / ".ovh-agent-checks.json").write_text(json.dumps(policy))
        (self.workspace / "result.json").write_text('{"ready":false}')
        self.engine = WorkflowController(self.workspace, self.root / "acceptance-execution")
        self.engine.focus("Set ready to true.")
        with patch.object(sys, "argv", ["test-execution", "--billing-status"]):
            spec = importlib.util.spec_from_file_location("execution_backend", ROOT / "ovh-agent.py")
            backend = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(backend)
        rounds = []

        def write_file(path: str, content: str) -> str:
            """Write only the temporary acceptance fixture."""
            self.engine.observe_event({"type": "tool_start"})
            (self.workspace / path).write_text(content)
            self.engine.observe_event({"type": "tool_end", "success": True})
            return "Fichier écrit."

        def model(messages, info):
            rounds.append(1)
            if len(rounds) == 1:
                return ModelResponse(parts=[TextPart("Everything is fixed and verified.")])
            if len(rounds) == 2:
                self.assertTrue(any(isinstance(part, RetryPromptPart) for message in messages for part in message.parts))
                return ModelResponse(parts=[ToolCallPart("write_file", {"path": "result.json", "content": '{"ready":true}'})])
            return ModelResponse(parts=[TextPart("The configured JSON postcondition now passes.")])

        agent = Agent(FunctionModel(model), tools=[Tool(guarded_tool(write_file, lambda: self.engine))], retries=2)
        agent.output_validator(backend.validate_task_output)
        with patch.object(backend, "VERIFICATION_GUARD", None), patch.object(backend, "TASK_ENGINE", self.engine):
            result = agent.run_sync("Set the fixture ready to true")
        self.assertEqual(len(rounds), 3)
        self.assertEqual(result.output, "The configured JSON postcondition now passes.")
        self.assertTrue(self.engine.evaluate()["contract_satisfied"])

    def test_configured_command_failure_is_not_hidden_by_a_success_event(self):
        def run_command(command: str) -> str:
            self.engine.observe_event({'type': 'tool_end', 'success': True})
            return json.dumps({'check_evidence': {'exit_code': 1, 'passed': False}, 'test_summary': {}})
        self.engine.invoke(run_command, 'fixture-check')
        self.assertEqual(self.engine.command_results[-1]['exit_code'], 1)
        self.assertTrue(self.engine.journal.snapshot()['unresolved_mutations'])
        self.assertTrue(self.engine.final_status()['incomplete'])

    def test_configured_red_then_file_correction_then_green_does_not_poison_journal(self):
        from ovh_verification import ChangeGuard
        source = self.workspace / 'value.py'
        source.write_text('VALUE = 1\n')
        check = self.workspace / 'check.py'
        check.write_text("from pathlib import Path\nnamespace={}\nexec(Path('value.py').read_text(), namespace)\nassert namespace['VALUE'] == 2\n")
        policy = {'version': 1, 'checks': [{'name': 'value-check', 'paths': ['value.py'],
                    'argv': ['{python}', 'check.py'], 'protect': ['check.py']}],
                  'acceptance': {'version': 1, 'criteria': [
                    {'id': 'behavior', 'type': 'configured_check', 'check': 'value-check'}]}}
        (self.workspace / '.ovh-agent-checks.json').write_text(json.dumps(policy))
        guard = ChangeGuard(self.workspace, self.root / 'guard')
        guard.observe(source)
        self.engine = WorkflowController(self.workspace, self.root / 'checks-engine', receipts=lambda: guard.state['receipts'])
        def run_command(command: str, cwd: str = '.') -> str:
            receipt = guard.execute_configured(command, self.workspace)
            self.engine.observe_event({'type': 'tool_end', 'success': receipt['passed']})
            evidence = {key: receipt.get(key) for key in ('id', 'check', 'argv', 'cwd', 'exit_code', 'passed', 'timed_out')}
            return json.dumps({'check_evidence': evidence})
        command = shlex.join([sys.executable, 'check.py'])
        self.engine.invoke(run_command, command)
        first = self.operations()[-1]
        self.assertEqual(first['execution_status'], 'failed')
        self.assertEqual(first['verification_status'], 'verified')
        self.assertEqual(self.engine.journal.snapshot()['unresolved_mutations'], [])
        self.assertTrue(self.engine.final_status()['incomplete'])
        def edit_file(path: str, old_text: str, new_text: str) -> str:
            guard.write(source, old_text, new_text)
            return 'Fichier modifié.'
        self.engine.invoke(edit_file, 'value.py', 'VALUE = 1\n', 'VALUE = 2\n')
        green = json.loads(self.engine.invoke(run_command, command))
        self.assertTrue(green['check_evidence']['passed'])
        self.assertEqual(self.engine.journal.snapshot()['unresolved_mutations'], [])
        self.assertEqual(self.engine.final_status()['test_status'], 'passed')
        self.assertFalse(self.engine.final_status()['incomplete'])

    def test_nested_project_check_is_known_execution_without_root_contract(self):
        from ovh_verification import ChangeGuard
        nested = self.workspace / 'nested'
        nested.mkdir()
        source = nested / 'value.py'
        source.write_text('VALUE = 1\n')
        (nested / 'check.py').write_text('raise SystemExit(1)\n')
        (nested / '.ovh-agent-checks.json').write_text(json.dumps({'version': 1, 'checks': [
            {'name': 'nested-behavior', 'paths': ['value.py'], 'argv': ['{python}', 'check.py'], 'protect': ['check.py']}]}))
        guard = ChangeGuard(self.workspace, self.root / 'nested-guard')
        guard.observe(source)
        self.engine = WorkflowController(self.workspace, self.root / 'nested-engine', receipts=lambda: guard.state['receipts'])
        def run_command(command: str, cwd: str = 'nested') -> str:
            receipt = guard.execute_configured(command, nested)
            return json.dumps({'check_evidence': {key: receipt.get(key) for key in
                ('id', 'check', 'argv', 'cwd', 'exit_code', 'passed', 'timed_out')}})
        self.engine.invoke(run_command, shlex.join([sys.executable, 'check.py']))
        self.assertEqual(self.operations()[-1]['execution_status'], 'failed')
        self.assertEqual(self.operations()[-1]['verification_status'], 'verified')
        self.assertEqual(self.engine.journal.snapshot()['unresolved_mutations'], [])
        self.assertFalse(self.engine.acceptance.describe()['configured'])

    def test_timed_out_or_changed_configured_check_does_not_clear_uncertainty(self):
        from ovh_verification import ChangeGuard
        source = self.workspace / 'value.py'
        source.write_text('VALUE = 1\n')
        (self.workspace / 'check.py').write_text('raise SystemExit(1)\n')
        (self.workspace / '.ovh-agent-checks.json').write_text(json.dumps({'version': 1, 'checks': [
            {'name': 'behavior', 'paths': ['value.py'], 'argv': ['{python}', 'check.py'], 'protect': ['check.py']}]}))
        for mode in ('timeout', 'changed', 'forged'):
            with self.subTest(mode=mode):
                def runner(*args):
                    if mode == 'changed':
                        source.write_text('VALUE = 9\n')
                    return {'exit_code': None if mode == 'timeout' else 1, 'passed': False,
                            'timed_out': mode == 'timeout', 'output': 'check failed'}
                guard = ChangeGuard(self.workspace, self.root / ('guard-' + mode), runner=runner)
                guard.observe(source)
                self.engine = WorkflowController(self.workspace, self.root / ('engine-' + mode), receipts=lambda: guard.state['receipts'])
                def run_command(command: str) -> str:
                    receipt = guard.execute_configured(command, self.workspace)
                    evidence = {key: receipt.get(key) for key in ('id', 'check', 'argv', 'cwd', 'exit_code', 'passed', 'timed_out')}
                    if mode == 'forged':
                        evidence['id'] = '0' * 32
                    return json.dumps({'check_evidence': evidence})
                self.engine.invoke(run_command, shlex.join([sys.executable, 'check.py']))
                self.assertEqual(self.operations()[-1]['status'], 'unknown')
                self.assertTrue(self.engine.journal.snapshot()['unresolved_mutations'])

    def test_check_json_printed_by_a_generic_shell_is_not_a_controller_receipt(self):
        def run_command(command: str) -> str:
            return json.dumps({'exit_code': 1, 'passed': False, 'timed_out': False, 'executed': True,
                'profile': 'approved', 'output': json.dumps({'check_evidence': {'exit_code': 1, 'passed': False}})})
        self.engine.invoke(run_command, 'generic fixture command')
        self.assertEqual(self.operations()[-1]['status'], 'unknown')

    def test_structured_process_receipts_record_commands_and_preserve_unknown_effects(self):
        outcomes = [
            {'exit_code': 0, 'passed': True, 'timed_out': False, 'executed': True, 'profile': 'approved', 'output': 'timeout literal'},
            {'exit_code': 1, 'passed': False, 'timed_out': False, 'executed': True, 'profile': 'approved', 'output': 'partial write'},
            {'exit_code': None, 'passed': False, 'timed_out': True, 'executed': True, 'profile': 'approved', 'output': ''}]
        def run_command(command: str) -> str:
            return json.dumps(outcomes.pop(0))
        self.engine.invoke(run_command, 'success')
        self.assertEqual(self.engine.command_results[-1]['exit_code'], 0)
        self.engine.invoke(run_command, 'failure')
        self.assertEqual(self.operations()[-1]['status'], 'unknown')
        self.engine.invoke(run_command, 'timeout')
        self.assertEqual(self.operations()[-1]['status'], 'unknown')
        self.assertTrue(self.engine.command_results[-1]['timed_out'])
        self.assertEqual(len(self.engine.journal.snapshot()['unresolved_mutations']), 2)

    def test_proven_preexecution_refusal_has_no_unknown_mutation(self):
        def run_command(command: str) -> str:
            return json.dumps({'exit_code': None, 'passed': False, 'timed_out': False, 'executed': False,
                'profile': 'workspace', 'error': 'sandbox_unavailable', 'output': ''})
        self.engine.invoke(run_command, 'fixture')
        self.assertEqual(self.operations()[-1]['status'], 'not_applied')
        self.assertEqual(self.engine.journal.snapshot()['unresolved_mutations'], [])

    def test_ambiguous_workspace_start_failure_stays_unknown(self):
        def run_command(command: str) -> str:
            return json.dumps({'exit_code': 1, 'passed': False, 'timed_out': False, 'executed': False,
                'profile': 'workspace', 'error': 'sandbox_setup_failed', 'output': ''})
        self.engine.invoke(run_command, 'fixture')
        self.assertTrue(self.engine.journal.snapshot()['unresolved_mutations'])

    def test_task_journal_never_retains_raw_tool_arguments_or_output(self):
        marker = "fixture-token-must-not-persist-in-action-receipts"

        def run_command(command: str) -> str:
            return "exit=0\n" + marker

        self.engine.invoke(run_command, marker)
        snapshot = json.dumps(self.engine.journal.snapshot())
        self.assertNotIn(marker, snapshot)
        self.assertNotIn(marker.encode(), self.engine.journal.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
