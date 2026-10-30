"""Offline integration of tool schemas, persistent recovery and final evidence."""
import importlib.util
from contextvars import ContextVar
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
from trinity_agent.execution import WorkflowController, RunDependencies, ExecutionCapability, command_is_read_only, read_only_policy
from pydantic_ai import Agent, ModelRetry, Tool
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
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

    def test_corrected_plan_remains_available_after_repeated_invalid_plans(self):
        for index in range(5):
            with self.engine.execution('update_plan', {'steps': ['invalid'] * 13, 'current_step': index}) as execution:
                if execution.execute:
                    execution.result = json.dumps({'status': 'failed', 'message': 'Too many steps'})
        with self.engine.execution('update_plan', {'steps': ['Check', 'Apply'], 'current_step': 0,
                'explanation': 'Check the observed failure before applying the update.'}) as execution:
            self.assertTrue(execution.execute, execution.result)
            execution.result = json.dumps({'steps': ['Check', 'Apply'], 'current_step': 0})
        self.assertFalse(execution.failed)
        self.assertEqual(self.engine.progress, 0)

    def test_real_backend_nonexecuted_script_retries_without_stale_incomplete_state(self):
        with patch.object(sys, 'argv', ['test-execution', '--billing-status']):
            spec = importlib.util.spec_from_file_location('literal_command_backend', ROOT/'trinity_agent/agent.py')
            backend = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(backend)
        script = self.workspace/'install.sh'
        script.write_text('#!/bin/sh\nprintf ran >> installed\n')
        token = backend.RUN_CONTEXT.set(RunDependencies(engine=self.engine))
        self.addCleanup(backend.RUN_CONTEXT.reset, token)
        with patch.object(backend, 'WORKSPACE', self.workspace), patch.object(
                backend, 'request_approval', return_value=True), patch.object(
                backend, 'read_task_state', return_value={}), patch.object(
                backend, 'emit', side_effect=lambda e: backend.run_dependencies().engine.observe_event(e)):
            first = json.loads(self.engine.invoke(backend.run_command, str(script)))
            self.assertFalse(first['executed'])
            self.assertFalse((self.workspace/'installed').exists())
            rejected, = self.operations()
            self.assertEqual(rejected['status'], 'not_applied')
            script.chmod(0o700)
            restarted = WorkflowController(self.workspace, self.directory)
            backend.run_dependencies().engine = restarted
            result = json.loads(restarted.invoke(backend.run_command, str(script)))
            self.assertTrue(result['passed'], result)
            self.assertEqual((self.workspace/'installed').read_text(), 'ran')
            self.assertFalse(restarted.final_status()['incomplete'])
            self.assertEqual(restarted.final_status()['command_status'], 'passed')
            self.assertFalse(restarted.journal.snapshot()['unresolved_mutations'])
            for bad in ("touch forbidden; echo 'unterminated",
                        "missing_fixture_apply_patch <<'PATCH'\ncontent\nPATCH\n",
                        'missing_fixture_recover operation-id'):
                rejected = json.loads(restarted.invoke(backend.run_command, bad))
                self.assertFalse(rejected['executed'], rejected)
                self.assertFalse((self.workspace/'forbidden').exists())
                self.assertFalse(restarted.journal.snapshot()['unresolved_mutations'])
            result = json.loads(restarted.invoke(backend.run_command, 'true'))
            self.assertTrue(result['passed'])
            self.assertFalse(restarted.final_status()['incomplete'])
            self.assertEqual(restarted.final_status()['command_status'], 'passed')
            script.write_text('#!/bin/sh\nprintf partial >> partial-effect\nexit 126\n')
            failed = json.loads(restarted.invoke(backend.run_command, str(script)))
            self.assertTrue(failed['executed'])
            self.assertEqual(failed['exit_code'], 126)
            blocked = json.loads(restarted.invoke(backend.run_command, str(script)))
            self.assertEqual(blocked['category'], 'recovery_required')
            self.assertEqual(blocked['recovery']['tool'], 'reconcile_operation')
            self.assertIn(blocked['recovery']['arguments']['operation_id'],
                          restarted.journal.snapshot()['unresolved_mutations'])
            self.assertEqual((self.workspace/'partial-effect').read_text(), 'partial')
            self.assertTrue(restarted.final_status()['incomplete'])

    def test_sdk_tool_schema_retains_named_typed_arguments_and_real_execution(self):
        calls, rounds = [], []

        def read_file(path: str, start: int = 1) -> str:
            """Read the requested fixture path from a specific starting line."""
            calls.append((path, start))
            return "timeout is a literal in this source file"


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

        result = Agent(FunctionModel(model), deps_type=RunDependencies, tools=[Tool(read_file)],
                       capabilities=[ExecutionCapability(ContextVar("test_run", default=None))]).run_sync(
                           "Read the fixture", deps=RunDependencies(engine=self.engine))
        self.assertEqual(result.output, "The fixture was read.")
        self.assertEqual(calls, [("app.py", 3)])
        self.assertEqual(self.operations()[-1]["status"], "succeeded")

    def test_native_file_outcome_distinguishes_baseline_from_write_failure(self):
        cases = [
            ('before', True, True, True, 'success'),
            ('after', True, True, True, 'failed'),
            ('before', False, True, True, 'failed'),
            ('before', True, False, True, 'failed'),
            ('before', True, True, False, 'failed'),
        ]
        for number, (phase, completed, write, edit_success, expected) in enumerate(cases):
            with self.subTest(phase=phase, completed=completed, write=write, edit_success=edit_success):
                target = self.workspace / f'case-{number}.txt'
                target.write_text('before')
                engine = WorkflowController(self.workspace, self.root / f'nested-{number}')
                engine.focus('Apply the requested edit and verify it.')
                edit_id, check_id = f'{number:032x}', f'{number + 20:032x}'
                def edit_file(path: str, old_text: str, new_text: str) -> str:
                    engine.observe_event({'type': 'tool_start', 'id': edit_id, 'tool': 'edit'})
                    engine.observe_event({'type': 'verification_status', 'status': 'running', 'phase': phase, 'check': 'regression'})
                    engine.observe_event({'type': 'tool_start', 'id': check_id, 'tool': 'command'})
                    engine.observe_event({'type': 'tool_end', 'id': check_id, 'success': False})
                    if completed:
                        engine.observe_event({'type': 'verification_check', 'phase': phase, 'check': 'regression', 'passed': False, 'exit_code': 1})
                    if write:
                        target.write_text(target.read_text().replace(old_text, new_text))
                    engine.observe_event({'type': 'tool_end', 'id': edit_id, 'success': edit_success})
                    return 'Fichier modifié.'
                def model(messages, info):
                    returned = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
                    if not returned:
                        return ModelResponse(parts=[ToolCallPart('edit_file', {'path': target.name, 'old_text': 'before', 'new_text': 'after'})])
                    self.assertEqual(returned[-1].outcome, expected)
                    return ModelResponse(parts=[TextPart('Observed tool outcome.')])
                Agent(FunctionModel(model), deps_type=RunDependencies, tools=[Tool(edit_file)],
                      capabilities=[ExecutionCapability(ContextVar('nested_check', default=None))]).run_sync(
                          'Apply the edit.', deps=RunDependencies(engine=engine))
                operation = engine.journal.snapshot()['operations'][-1]
                self.assertEqual(target.read_text(), 'after' if write else 'before')
                if expected == 'success':
                    self.assertEqual(operation['execution_status'], 'succeeded')
                    self.assertEqual(operation['verification_outcome'], 'applied')
                    self.assertEqual(operation['failure_category'], 'none')
                    self.assertEqual(engine.progress, 1)


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
from trinity_agent.execution import WorkflowController
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
        self.assertTrue(self.engine.final_status()["incomplete"])

    def test_rejected_write_is_incomplete_until_corrected_but_does_not_poison_next_task(self):
        target = self.workspace / "app.css"
        target.write_text("before")
        def write_file(path: str, content: str) -> str:
            raise ModelRetry("Inspect before modifying.")
        with self.assertRaises(ModelRetry):
            self.engine.invoke(write_file, "app.css", "after")
        self.assertEqual(target.read_text(), "before")
        self.assertTrue(self.engine.final_status()["failed_files"])
        self.assertFalse(self.engine.journal.snapshot()["unresolved_mutations"])
        restarted = WorkflowController(self.workspace, self.directory)
        restarted.focus("Explain this project without editing.")
        self.assertFalse(restarted.final_status()["incomplete"])

    def test_rejected_obsolete_path_does_not_override_verified_postconditions(self):
        target=self.workspace/'current.txt';target.write_text('before')
        policy={'version':1,'checks':[], 'acceptance':{'version':1,'criteria':[
            {'id':'desired','type':'text_contains','path':'current.txt','text':'after'}]}}
        (self.workspace/'.ovh-agent-checks.json').write_text(json.dumps(policy))
        self.engine=WorkflowController(self.workspace,self.directory)
        self.engine.focus('Update the current file; the supplied old path may be obsolete.')
        def write_file(path: str, content: str) -> str:
            return json.dumps({'status':'failed','executed':False,'category':'conflict'})
        self.engine.invoke(write_file,'obsolete.txt','after')
        rejected=self.operations()[-1]['id']
        self.assertTrue(self.engine.final_status()['incomplete'])
        def edit_file(path: str, old_text: str, new_text: str) -> str:
            file=self.workspace/path
            file.write_text(file.read_text().replace(old_text,new_text))
            return 'Fichier modifié'
        self.engine.invoke(edit_file,'current.txt','before','after')
        final=self.engine.final_status()
        self.assertTrue(final['contract_satisfied'])
        self.assertFalse(final['incomplete'],final)
        recorded=next(row for row in self.operations() if row['id']==rejected)
        self.assertEqual(recorded['status'],'not_applied')
        self.assertFalse(recorded['may_have_effects'])
        self.assertFalse((self.workspace/'obsolete.txt').exists())

    def test_readonly_diagnostic_failure_is_not_a_persistent_unknown_mutation(self):
        self.engine = WorkflowController(self.workspace, self.directory, trusted_diagnostics=True)
        def run_command(command: str) -> str:
            return json.dumps({'exit_code': 1, 'passed': False, 'timed_out': False,
                               'executed': True, 'profile': 'approved', 'output': ''})
        self.engine.invoke(run_command, "grep absent app.txt")
        self.assertEqual(self.operations()[-1]["status"], "failed")
        self.assertFalse(self.engine.journal.snapshot()["unresolved_mutations"])
        self.assertFalse(self.engine.final_status()["incomplete"])
        restarted = WorkflowController(self.workspace, self.directory)
        self.assertFalse(restarted.final_status()["incomplete"])
        for command in ("rg --pre=evil pattern .", "rg --pre evil pattern .", "grep x .; touch file",
                        "rg --hostname-bin=evil pattern .", "rg --hostname-bin evil pattern .",
                        "rg --search-zip pattern .", "rg -z pattern .", "rg -niz pattern .",
                        "cat $(touch file)", "find . -delete", "python -c 'print(1)'", "env cat file"):
            self.assertFalse(command_is_read_only(command), command)

    def test_local_http_probe_refusal_is_observation_and_disables_curl_config(self):
        import socket
        with patch.object(sys, 'argv', ['test-execution', '--billing-status']):
            spec=importlib.util.spec_from_file_location('http_probe_backend', ROOT/'trinity_agent/agent.py')
            backend=importlib.util.module_from_spec(spec);spec.loader.exec_module(backend)
        self.engine=WorkflowController(self.workspace,self.directory,trusted_diagnostics=True)
        token=backend.RUN_CONTEXT.set(RunDependencies(engine=self.engine))
        self.addCleanup(backend.RUN_CONTEXT.reset, token)
        # Bound but not listening: the kernel refuses the connection reliably.
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
            config=self.root/'.curlrc'
            marker=self.root/'unexpected-output'
            config.write_text('output = '+str(marker)+'\nurl = http://127.0.0.1:1/\n')
            shadow=self.workspace/'.agent-tools/venv/bin';shadow.mkdir(parents=True)
            (shadow/'curl').write_text('#!/bin/sh\ntouch '+str(marker)+'\n')
            (shadow/'curl').chmod(0o700)
            command=f'curl -s -o /dev/null -w "%{{http_code}}" --max-time 2 http://127.0.0.1:{port}/health'
            self.assertTrue(command_is_read_only(command))
            with patch.object(backend,'WORKSPACE',self.workspace), patch.object(backend,'request_approval',return_value=True), patch.object(backend,'read_task_state',return_value={}), patch.object(backend,'emit',side_effect=self.engine.observe_event), patch.dict('os.environ',{'CURL_HOME':str(self.root),'http_proxy':'http://127.0.0.1:1'}):
                result=json.loads(self.engine.invoke(backend.run_command,command))
            self.assertEqual(result['exit_code'],7,result)
            self.assertTrue(result['executed'])
            self.assertFalse(marker.exists())
            self.assertEqual(self.operations()[-1]['status'],'failed')
            self.assertFalse(self.engine.final_status()['incomplete'])
            self.assertFalse(WorkflowController(self.workspace,self.directory).final_status()['incomplete'])
        for command in ('curl -X POST http://127.0.0.1:80/', 'curl -d value http://127.0.0.1:80/',
                        'curl -L http://127.0.0.1:80/', 'curl -o file http://127.0.0.1:80/',
                        'curl --config file http://127.0.0.1:80/', 'curl http://example.org/',
                        'curl -w "%output{file}" http://127.0.0.1:80/', 'curl http://127.0.0.1:80/ http://127.0.0.1:81/',
                        'curl http://127.0.0.1:80/; touch file', 'curl http://127.0.0.1:{80,81}/'):
            self.assertFalse(command_is_read_only(command),command)

    def test_missing_version_probe_does_not_leave_a_phantom_mutation_after_restart(self):
        from trinity_agent.process import run_process
        self.engine = WorkflowController(self.workspace, self.directory, trusted_diagnostics=True)
        def run_command(command: str) -> str:
            return json.dumps(run_process(['/bin/sh', '-c', command], self.workspace))
        with patch.dict('os.environ', {'PATH': str(self.workspace)}):
            result = json.loads(self.engine.invoke(run_command, 'gh --version'))
        self.assertEqual(result['exit_code'], 127)
        self.assertEqual(self.operations()[-1]['status'], 'failed')
        self.assertFalse(self.engine.journal.snapshot()['unresolved_mutations'])
        self.assertFalse(WorkflowController(self.workspace, self.directory).final_status()['incomplete'])
        for command in ('gh release create v1', 'gh --version; touch changed',
                        'gh --version > changed', 'python3 -c "print(1)"', './unknown --version'):
            self.assertFalse(command_is_read_only(command), command)

    def test_diagnostic_exemption_requires_explicit_trusted_executor_contract(self):
        calls = []
        def run_command(command: str) -> str:
            calls.append(command)
            return 'exit=1\n'
        self.engine.focus("Lecture seule.")
        result = json.loads(self.engine.invoke(run_command, "cat app.txt"))
        self.assertFalse(result['executed'])
        self.assertFalse(calls)
        self.engine.focus("Modifie le projet.")
        self.engine.invoke(run_command, "cat app.txt")
        self.assertTrue(self.engine.journal.snapshot()['unresolved_mutations'])

    def test_policy_and_user_refusals_are_known_nonexecution(self):
        for error in ("approval_denied", "policy_denied", "invalid_command_target"):
            def run_command(command: str) -> str:
                return json.dumps({'exit_code': None, 'passed': False, 'timed_out': False,
                                   'executed': False, 'profile': 'approved', 'error': error})
            self.engine.invoke(run_command, "mutating-command-" + error)
            self.assertEqual(self.operations()[-1]["status"], "not_applied")
        self.assertFalse(self.engine.journal.snapshot()["unresolved_mutations"])

    def test_explicit_polite_correction_can_lift_previous_readonly(self):
        self.assertFalse(read_only_policy("Peux-tu maintenant corriger ce défaut ?", True))
        self.assertTrue(read_only_policy("Analyse les modifications sans rien modifier", False))
        self.assertTrue(read_only_policy("Continue", True))
        for prompt in ("Corrige ton analyse, ne modifie pas les fichiers.",
                       "Peux-tu corriger ton analyse sans modifier les fichiers ?",
                       "Fix your explanation without modifying files."):
            self.assertTrue(read_only_policy(prompt, True))
            self.assertTrue(read_only_policy(prompt, False))
        for prompt in ("Corrige app.py sans modifier les tests",
                       "Modifie chat.css sans modifier extension.js",
                       "Corrige app.py. Ne modifie pas cette configuration ni les autres fichiers.",
                       "Fix app.py without modifying the deployment configuration."):
            self.assertFalse(read_only_policy(prompt, True))
        self.assertTrue(read_only_policy("Lis app.py sans modifier les tests", False))
        self.assertTrue(read_only_policy("Corrige app.py sans modifier app.py", False))

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

    def test_repeated_observations_stop_before_next_paid_model_round(self):
        from pydantic_ai.capabilities import ProcessHistory
        calls = []
        def lookup() -> str:
            """Inspect an unchanged fixture."""
            return 'unchanged fixture'
        def model(messages, info):
            calls.append(messages)
            return ModelResponse(parts=[ToolCallPart('lookup', {}, tool_call_id=str(len(calls)))])
        def before_request(messages):
            self.engine.check_progress()
            return messages
        agent = Agent(FunctionModel(model), deps_type=RunDependencies, tools=[lookup],
                      capabilities=[ExecutionCapability(ContextVar("test_run", default=None)), ProcessHistory(before_request)])
        with self.assertRaisesRegex(RuntimeError, 'même séquence'):
            agent.run_sync('Inspect fixture', deps=RunDependencies(engine=self.engine))
        self.assertEqual(len(calls), 3)
        self.assertTrue(self.engine.incomplete)
        # A user correction or new observed evidence permits progress again.
        self.engine.focus('Inspect a different fixture')
        self.engine.check_progress()
        for i in range(12):
            self.engine.note_observation('lookup', {}, str(i))
            self.engine.check_progress()

    def test_repeated_two_tool_cycle_is_detected_without_an_action_count_cap(self):
        for _ in range(3):
            self.engine.note_observation('lookup', {'path':'a'}, 'same A')
            self.engine.note_observation('lookup', {'path':'b'}, 'same B')
        with self.assertRaisesRegex(RuntimeError, 'même séquence'):
            self.engine.check_progress()
        self.engine.progress += 1
        self.engine.note_observation('write_file', {'path':'a'}, 'changed')
        self.engine.check_progress()

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
            spec = importlib.util.spec_from_file_location("execution_backend", ROOT / "trinity_agent/agent.py")
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
                feedback = '\n'.join(str(getattr(part, 'content', '')) for part in messages[-1].parts)
                self.assertIn('Postconditions non satisfaites.', feedback)
                self.assertIn('"criteria_status": "failed"', feedback)
                self.assertFalse(self.engine.evaluate()['contract_satisfied'])
                return ModelResponse(parts=[ToolCallPart("write_file", {"path": "result.json", "content": '{"ready":true}'})])
            return ModelResponse(parts=[TextPart("The configured JSON postcondition now passes.")])

        agent = Agent(FunctionModel(model), deps_type=RunDependencies, tools=[Tool(write_file)], retries=2,
                      capabilities=[ExecutionCapability(backend.RUN_CONTEXT)])
        agent.output_validator(backend.validate_task_output)
        with patch.object(backend.run_dependencies(), 'verification', None), patch.object(backend.run_dependencies(), 'engine', self.engine):
            result = agent.run_sync("Set the fixture ready to true", deps=backend.run_dependencies())
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
        from trinity_agent.verification import ChangeGuard
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
        from trinity_agent.verification import ChangeGuard
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
        from trinity_agent.verification import ChangeGuard
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
