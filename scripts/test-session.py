"""Offline regression checks for session exclusion and conversation titles."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
with patch.object(sys, 'argv', ['test-session', '--billing-status']):
    spec = importlib.util.spec_from_file_location('session_agent', ROOT / 'ovh-agent.py')
    a = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(a)


class Sessions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.folder = self.root / 'archive' / 'workspace' / 'conversation'
        self.folder.mkdir(parents=True)
        for name, value in {'DATA_ROOT': self.root, 'ARCHIVE_ROOT': self.folder.parent,
                            'CONVERSATION_ID': self.folder.name, 'WORKSPACE': ROOT}.items():
            replacement = patch.object(a, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.meta = self.folder / 'meta.json'
        self.events = self.folder / 'events.jsonl'
        self.events.write_text(json.dumps({'type': 'user', 'text': 'Analyse\n  le projet docker_trinity'}) + '\n')

    def test_plan_before_first_prompt_stays_inside_its_conversation(self):
        with patch.object(a, 'CONVERSATION_ID', ''), patch.object(a, 'POINTER_FILE', self.root/'current'), patch.object(a, 'emit'):
            result = json.loads(a.update_plan(['Inspect', 'Correct', 'Verify']))
            self.assertEqual(result['current_step'], 0)
            self.assertTrue(a.CONVERSATION_ID)
            self.assertTrue((a.conversation_dir()/'plan.json').is_file())
            self.assertFalse((a.ARCHIVE_ROOT/'plan.json').exists())

    def test_snapshot_timeline_retains_commands_and_original_diffs_for_legacy_upgrade(self):
        command = "cwd=/tmp\nprintf '<script>literal</script>\\n'\n" + "# command body\n" * 400
        diff = '--- a/example.js\n+++ b/example.js\n@@ -0,0 +1 @@\n+const html = "<script>literal</script>";\n'
        self.events.write_text('\n'.join(json.dumps(event) for event in [
            {'type': 'tool_start', 'id': 'command', 'tool': 'command', 'detail': command},
            {'type': 'tool_end', 'id': 'command', 'success': True, 'detail': 'Large command output'},
            {'type': 'tool_start', 'id': 'read', 'tool': 'read', 'detail': 'example.js'},
            {'type': 'file_change', 'path': 'example.js', 'diff': diff},
        ]) + '\n')
        timeline = a.read_archive('conversation', timeline_only=True)
        self.assertEqual(timeline[0]['tool'], 'command')
        self.assertEqual(timeline[0]['detail'], command)
        self.assertNotIn('detail', timeline[1])
        self.assertEqual(timeline[2]['tool'], 'read')
        self.assertNotIn('detail', timeline[2])
        self.assertEqual(timeline[3]['path'], 'example.js')
        self.assertEqual(timeline[3]['diff'], diff)
        self.assertEqual(a.read_archive('conversation')[0]['detail'], command)

    def test_conversation_model_restores_metadata_then_latest_legacy_choice(self):
        self.meta.write_text(json.dumps({'model':'Qwen3.8-27B'}))
        self.events.write_text('\n'.join(json.dumps(e) for e in [
            {'type':'model_active','model':'gpt-oss-120b','selection':'auto-balanced'},
            {'type':'model_changed','to_model':'Qwen3.5-9B'},
            {'type':'response','text':'Done'}])+'\n')
        self.assertEqual(a.conversation_model('global-model'), 'Qwen3.8-27B')
        self.meta.write_text('{}')
        self.assertEqual(a.conversation_model('global-model'), 'Qwen3.5-9B')
        with self.events.open('a') as stream:
            stream.write(json.dumps({'type':'model_active','model':'gpt-oss-120b','selection':'auto-balanced'})+'\n')
        self.assertEqual(a.conversation_model('global-model'), a.AUTO_MODEL)
        self.events.write_text('')
        self.assertEqual(a.conversation_model('global-model', {'model':'snapshot-model'}), 'snapshot-model')
        self.assertEqual(a.conversation_model('global-model'), 'global-model')

    def test_opening_conversation_reuses_its_model_and_history_in_next_inference(self):
        self.meta.write_text(json.dumps({'model':'Qwen3.8-27B'}))
        previous=[a.ModelRequest(parts=[a.UserPromptPart(content='Le nom du projet est Neptune.')]),
                  a.ModelResponse(parts=[a.TextPart(content='Je retiens Neptune.')])]
        (self.folder/'messages.json').write_bytes(a.ModelMessagesTypeAdapter.dump_json(previous))
        other=self.folder.parent/'other'
        other.mkdir()
        (other/'meta.json').write_text(json.dumps({'model':'gpt-oss-120b'}))
        (other/'messages.json').write_bytes(a.ModelMessagesTypeAdapter.dump_json([
            a.ModelRequest(parts=[a.UserPromptPart(content='Unrelated conversation context')])]))
        original_load=a.load_history
        received=[]
        def infer(prompt, **kwargs):
            received.append(kwargs['message_history'])
            raise RuntimeError('fixture stops after observing inference context')
        with patch.object(a,'CONVERSATION_ID','other'):
            calls, outputs=self.run_failed_queue([
                {'type':'load_archive','id':'conversation'},
                {'type':'prompt','text':'Continue avec le même projet.'}],
                model_agent=SimpleNamespace(run_sync=infer), history_loader=original_load)
        opened=next(e for e in outputs if e['type']=='archive_loaded' and not e.get('automatic'))
        self.assertEqual(opened['model'],'Qwen3.8-27B')
        active=next(e for e in outputs if e['type']=='model_active')
        self.assertEqual(active['model'],'Qwen3.8-27B')
        self.assertEqual(len(received),1)
        serialized=a.ModelMessagesTypeAdapter.dump_json(received[0]).decode()
        self.assertIn('Le nom du projet est Neptune.',serialized)
        self.assertIn('Je retiens Neptune.',serialized)
        self.assertNotIn('Unrelated conversation context',serialized)

    def test_startup_and_snapshot_restore_conversation_model_over_global_choice(self):
        self.meta.write_text(json.dumps({'model':'Qwen3.8-27B'}))
        outputs=[]
        def inputs(requests):
            requests.put({'type':'load_archive','id':'conversation'})
            requests.put({'type':'shutdown'})
        with ExitStack() as stack:
            mocks={'recover_verification':{},'read_task_state':{'return_value':{}},
                'load_history':{'return_value':[]},
                'read_snapshot':{'return_value':{'html':'<p>saved</p>','model':'obsolete-snapshot-model'}},
                'load_selected_model':{'return_value':'global-model'},'refresh_billing_async':{},
                'current_budget_status':{'return_value':{}},'publish_budget':{},
                'stdin_reader':{'side_effect':inputs},'emit':{'side_effect':outputs.append},
                'write_pointer':{},'list_archives':{'return_value':[]}}
            for name,settings in mocks.items():stack.enter_context(patch.object(a,name,**settings))
            stack.enter_context(patch.object(a,'JSON_MODE',False))
            a.main_json_lines()
        self.assertEqual(next(e['model'] for e in outputs if e['type']=='ready'),'Qwen3.8-27B')
        snapshots=[e for e in outputs if e['type']=='snapshot_loaded']
        self.assertEqual(len(snapshots),2)
        self.assertTrue(all(e['model']=='Qwen3.8-27B' for e in snapshots))

    def test_corrupted_event_does_not_hide_later_events_or_certify_snapshot(self):
        self.events.write_text('\n'.join([json.dumps({'type':'user','text':'before'}),
            '{broken', 'null', '[]', json.dumps({'type':'response','text':'after'})])+'\n')
        for timeline in (False, True):
            events = a.read_archive('conversation', timeline_only=timeline)
            self.assertEqual([event['type'] for event in events], ['user', 'response'])
            self.assertEqual(a.ARCHIVE_READ_REVISIONS['conversation'], '')

    def test_progress_is_archived_and_invalid_updates_preserve_the_plan(self):
        with patch.object(a, 'emit', side_effect=a.archive_event) as emit:
            result = json.loads(a.update_plan(['Inspect', 'Verify'], explanation='  Je vérifie le port actif.  '))
            self.assertEqual(result['explanation'], 'Je vérifie le port actif.')
            event = next(e for e in a.read_archive('conversation') if e['type'] == 'plan_updated')
            self.assertEqual(event['explanation'], result['explanation'])
            plan = (self.folder/'plan.json').read_bytes()
            emit.reset_mock()
            for invalid in ('x' * 4001, None):
                self.assertIn('Erreur outil', a.update_plan(['Changed'], explanation=invalid))
                self.assertEqual((self.folder/'plan.json').read_bytes(), plan)
            emit.assert_not_called()

    def test_activity_finishes_once_and_does_not_replace_another_active_tool(self):
        with patch.object(a, 'emit') as emit, patch.object(a, 'read_task_state', return_value={'status':'running','last_tool':{'id':'another'}}), patch.object(a, 'save_task_state') as save:
            with a.run_dependencies().activity_lock:
                a.run_dependencies().activity_started['fixture'] = 0.0
            a.activity_end('fixture', 'finished')
            a.activity_end('fixture', 'duplicate')
            a.activity_end('', 'invalid')
            self.assertEqual(emit.call_count, 1)
            save.assert_not_called()

    def test_snapshot_size_limit_counts_utf8_bytes(self):
        self.meta.write_text('{}')
        a.save_snapshot('current', False, a.AUTO_MODEL, a.archive_revision())
        snapshot = self.folder/'snapshot.json'
        before = snapshot.read_bytes()
        a.save_snapshot('🙂' * (5 * 1024 * 1024), False, a.AUTO_MODEL, a.archive_revision())
        self.assertEqual(snapshot.read_bytes(), before)

    def run_failed_queue(self, initial, prepare=None, *, model_agent=None, continue_once=False, history_loader=None):
        """Drive the real dispatcher and disk queue without a model/provider."""
        outputs, calls, channels = [], [], {}
        def inputs(requests):
            channels['requests'] = requests
            for request in initial:
                requests.put(request)
        def emit(event):
            outputs.append(event)
            if event['type'] == 'error':
                # Existing stale wakeups remain ahead of shutdown and must be
                # ignored; otherwise a second task runs before the test exits.
                channels['requests'].put({'type': 'prompt', 'text': 'Continue after the error.', 'model': 'fixture-model'}
                    if continue_once and sum(item['type'] == 'error' for item in outputs) == 1 else {'type': 'shutdown'})
            elif continue_once and event['type'] == 'response':
                channels['requests'].put({'type': 'shutdown'})
        def verification(*_):
            a.run_dependencies().verification = SimpleNamespace(state={'status': 'passed'}, require_verified=lambda: {'status': 'not_required'})
        def run_model(prompt, **kwargs):
            calls.append(prompt)
            raise RuntimeError('fixture model failure')
        def preparation(*args, **kwargs):
            if prepare:
                prepare(channels['requests'])
            return args[0], {}
        with ExitStack() as stack:
            mocks = {
                'recover_verification': {}, 'load_history': {'side_effect': history_loader} if history_loader else {'return_value': []},
                'read_snapshot': {'return_value': None}, 'read_archive': {'return_value': []},
                'load_selected_model': {'return_value': 'fixture-model'},
                'refresh_billing_async': {}, 'current_budget_status': {'return_value': {}},
                'record_model_usage': {'return_value': 0.0},
                'publish_budget': {}, 'stdin_reader': {'side_effect': inputs},
                'emit': {'side_effect': emit}, 'write_pointer': {}, 'list_archives': {'return_value': []},
                'save_selected_model': {}, 'is_selectable_model': {'return_value': True},
                'compact_history': {'side_effect': lambda history: (history, {'before_chars': 0})},
                'prepare_task_context': {'side_effect': preparation},
                'begin_verification': {'side_effect': verification},
                'model_supports_vision': {'return_value': True},
                'generation_settings': {'return_value': {}},
                'model_capabilities': {'return_value': {'stream': False}},
                'get_agent': {'return_value': model_agent or SimpleNamespace(run_sync=run_model)},
                'archive_event': {},
            }
            for name, settings in mocks.items():
                stack.enter_context(patch.object(a, name, **settings))
            for name, value in {'FOLLOWUP_RUNNING': False, 'FOLLOWUP_CLAIMED': None,
                                'FOLLOWUP_GENERATION': 0, 'JSON_MODE': False}.items():
                stack.enter_context(patch.object(a, name, value))
            stack.enter_context(patch.object(a.WorkspaceContext, 'answer', return_value=None))
            a.main_json_lines()
            self.assertFalse(a.FOLLOWUP_RUNNING)
        return calls, outputs

    def test_next_user_turn_keeps_tool_evidence_from_a_failed_model_run(self):
        from pydantic_ai import Agent
        from pydantic_ai.models.function import FunctionModel
        from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
        requests, reads = [], []

        def read_fixture() -> str:
            """Return independently identifiable evidence."""
            reads.append(1)
            return 'observed-value-8271'

        def model(messages, info):
            requests.append(messages)
            if len(requests) == 1:
                return ModelResponse(parts=[ToolCallPart('read_fixture', {})])
            if len(requests) == 2:
                raise RuntimeError('fixture provider disconnected')
            evidence = [p.content for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
            self.assertIn('observed-value-8271', evidence)
            prompts = [p.content for m in messages for p in m.parts if isinstance(p, UserPromptPart)]
            self.assertEqual(prompts, ['Read the fixture.', 'Continue after the error.'])
            self.assertIn('[TRINITY interrupted execution]', str(messages))
            return ModelResponse(parts=[TextPart('Evidence retained; no duplicate read.')])

        agent = Agent(FunctionModel(model), tools=[read_fixture])
        _, events = self.run_failed_queue([{'type': 'prompt', 'text': 'Read the fixture.', 'model': 'fixture-model'}],
                                         model_agent=agent, continue_once=True)
        self.assertEqual(reads, [1])
        self.assertEqual(len(requests), 3)
        self.assertEqual(next(e['text'] for e in events if e['type'] == 'response'), 'Evidence retained; no duplicate read.')
        self.assertIn('observed-value-8271', (self.folder/'messages.json').read_text())

    def test_native_interrupted_tail_is_kept_and_not_replayed_on_continuation(self):
        from pydantic_ai import Agent, Tool
        from pydantic_ai.models.function import FunctionModel
        from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
        calls = []

        def commit_once() -> str:
            """Simulate a committed operation."""
            calls.append('committed')
            return 'operation committed'

        def crash_after_commit() -> str:
            """Simulate an unexpected worker failure."""
            raise RuntimeError('worker interrupted')

        def first(messages, info):
            return ModelResponse(parts=[ToolCallPart('commit_once', {}, tool_call_id='committed'),
                                        ToolCallPart('crash_after_commit', {}, tool_call_id='crashed')])

        a.RUN_CONTEXT.set(a.RunDependencies())
        agent = Agent(FunctionModel(first), tools=[Tool(commit_once, sequential=True), Tool(crash_after_commit, sequential=True)])
        with patch.object(a, 'get_agent', return_value=agent):
            with self.assertRaisesRegex(RuntimeError, 'worker interrupted'):
                a.run_model_attempt('fixture', 'Execute once.', message_history=[], model_settings={})
        history = a.interrupted_model_history([], 'Worker interrupted; committed effects must be checked.')
        self.assertEqual(history[-1].state, 'interrupted')
        self.assertEqual(calls, ['committed'])

        def continued(messages, info):
            returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
            self.assertTrue(any(p.tool_call_id == 'committed' and p.content == 'operation committed' for p in returns))
            self.assertTrue(any(p.tool_call_id == 'crashed' and p.outcome == 'interrupted' for p in returns))
            return ModelResponse(parts=[TextPart('Inspect before retrying.')])

        with patch.object(a, 'get_agent', return_value=Agent(FunctionModel(continued), tools=[commit_once])):
            result = a.run_model_attempt('fixture', 'Continue.', message_history=history, model_settings={})
        self.assertEqual(result.output, 'Inspect before retrying.')
        self.assertEqual(calls, ['committed'])

    def test_retry_exhaustion_allows_new_prompt_without_executing_abandoned_call(self):
        from pydantic_ai import Agent
        from pydantic_ai.exceptions import UnexpectedModelBehavior
        from pydantic_ai.models.function import FunctionModel
        from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
        effects = []

        def first(messages, info):
            return ModelResponse(parts=[ToolCallPart('commit_once', {}, tool_call_id='abandoned')])

        a.RUN_CONTEXT.set(a.RunDependencies())
        with patch.object(a, 'get_agent', return_value=Agent(FunctionModel(first), retries=0)):
            with self.assertRaises(UnexpectedModelBehavior):
                a.run_model_attempt('fixture', 'Inspect before editing.', message_history=[], model_settings={})
        history = a.interrupted_model_history([], 'Unknown tool exhausted native retries.')
        # Exercise preparation too: it can discard Pydantic's empty interrupted
        # tail while removing old task context before the next user turn.
        with patch.object(a, 'AI_PROVIDER', 'ovhcloud'), \
             patch.object(a, 'history_with_memory', side_effect=lambda history, *args, **kwargs: history), \
             patch.object(a, 'prompt_component_profile', return_value={}), \
             patch.object(a, 'prepare_request', return_value={'context': '', 'analysis': {'intent': 'edit'},
                          'sources': [], 'files_scanned': 0, 'partial': False}):
            history, _ = a.prepare_task_context(history, 'Continue with the correction.')

        def commit_once() -> str:
            """The formerly hidden tool must not execute on a new user turn."""
            effects.append('committed')
            return 'committed'

        def continued(messages, info):
            returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
            self.assertTrue(any(p.tool_call_id == 'abandoned' and p.outcome == 'interrupted' for p in returns))
            self.assertEqual([p.content for m in messages for p in m.parts if isinstance(p, UserPromptPart)],
                             ['Inspect before editing.', 'Continue with the correction.'])
            return ModelResponse(parts=[TextPart('Ready to continue safely.')])

        with patch.object(a, 'get_agent', return_value=Agent(FunctionModel(continued), tools=[commit_once])):
            result = a.run_model_attempt('fixture', 'Continue with the correction.', message_history=history, model_settings={})
        self.assertEqual(result.output, 'Ready to continue safely.')
        self.assertEqual(effects, [])

    def test_model_handoff_keeps_evidence_without_replaying_pending_calls(self):
        from pydantic_ai import Agent
        from pydantic_ai.models.function import FunctionModel
        from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
        history = [ModelRequest(parts=[UserPromptPart('Fix the saved target.')]),
                   ModelResponse(parts=[ToolCallPart('read_file', {'path': 'target'}, tool_call_id='read')]),
                   ModelRequest(parts=[ToolReturnPart('read_file', 'observed target', tool_call_id='read')]),
                   ModelResponse(parts=[ToolCallPart('commit_once', {}, tool_call_id='pending')], state='interrupted')]
        a.RUN_CONTEXT.set(a.RunDependencies(model_messages=history))
        calls = []

        def commit_once() -> str:
            """A pending action must not be replayed during model handoff."""
            calls.append(1)
            return 'committed'

        def fallback(messages, info):
            self.assertIn('Keep the original constraints.', info.instructions)
            self.assertIn('Reprends depuis les résultats observés', info.instructions)
            prompts = [p.content for m in messages for p in m.parts if isinstance(p, UserPromptPart)]
            self.assertEqual(prompts, ['Fix the saved target.'])
            returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
            self.assertTrue(any(p.content == 'observed target' for p in returns))
            self.assertTrue(any(p.tool_call_id == 'pending' and p.outcome == 'interrupted' for p in returns))
            return ModelResponse(parts=[TextPart('Continue from observed target.')])

        with patch.object(a, 'get_agent', return_value=Agent(FunctionModel(fallback), tools=[commit_once], instructions='Keep the original constraints.')):
            result = a.run_model_attempt('fallback', 'Fix the saved target.', message_history=[], model_settings={}, resume=True)
        self.assertEqual(result.output, 'Continue from observed target.')
        self.assertEqual(calls, [])

    def test_queued_wakeups_do_not_continue_after_first_model_failure(self):
        requests = [{'type': 'followup', 'action': 'add', 'conversation': 'conversation',
                     'id': name, 'text': name, 'mode': 'queue', 'model': 'fixture-model'}
                    for name in ('first', 'second')]
        calls, outputs = self.run_failed_queue(requests)
        self.assertEqual(calls, ['first'])
        self.assertEqual([item['id'] for item in a.read_followups()], ['second'])
        self.assertEqual(a.consumed_followup_ids(), ['first'])
        self.assertEqual(sum(event['type'] == 'error' for event in outputs), 1)
        # An explicit continuation rearms the current generation.
        calls, _ = self.run_failed_queue([
            {'type': 'followup', 'action': 'continue', 'conversation': 'conversation'}])
        self.assertEqual(calls, ['second'])
        self.assertEqual(a.read_followups(), [])

    def test_manual_model_change_emits_a_persistent_conversation_transition(self):
        _, outputs = self.run_failed_queue([
            {'type': 'set_model', 'model': 'Qwen3.8-27B'}, {'type': 'shutdown'}])
        changed = next(event for event in outputs if event['type'] == 'model_changed')
        self.assertEqual(changed['from_model'], 'fixture-model')
        self.assertEqual(changed['to_model'], 'Qwen3.8-27B')
        self.assertEqual(outputs[-1], {'type': 'model_selected', 'model': 'Qwen3.8-27B'})

    def test_followup_received_during_failed_preparation_stays_paused(self):
        def prepare(requests):
            self.assertTrue(a.FOLLOWUP_RUNNING)
            a.handle_followup({'type': 'followup', 'action': 'add', 'conversation': 'conversation',
                'id': 'during-preparation', 'text': 'preserve pending', 'mode': 'queue',
                'model': 'fixture-model'}, requests)
            self.assertTrue(requests.empty(), 'A running preparation cannot schedule another attempt')
            raise ValueError('fixture preparation failure')
        calls, _ = self.run_failed_queue([
            {'type': 'prompt', 'text': 'initial', 'model': 'fixture-model'}], prepare)
        self.assertEqual(calls, [])
        self.assertEqual([item['id'] for item in a.read_followups()], ['during-preparation'])

    def test_corrupt_task_state_blocks_replay_writes_and_startup(self):
        a.write_followups([{'id': 'accepted', 'text': 'accepted once', 'mode': 'queue'}])
        a.consume_followup('accepted', status='running', prompt='accepted once')
        self.assertEqual(a.read_followups(), [])
        path = self.folder / 'task-state.json'
        for broken in ('{broken', '[]', '{"consumed_followups": [1]}'):
            with self.subTest(broken=broken):
                path.write_text(broken)
                with self.assertRaisesRegex(ValueError, 'task-state.json'):
                    a.read_followups()
                with self.assertRaisesRegex(ValueError, 'task-state.json'):
                    a.save_task_state(status='interrupted')
                with patch.object(a, 'emit') as emitted, patch.object(a, 'recover_verification') as recovery, \
                        patch.object(a, 'JSON_MODE', False):
                    a.main_json_lines()
                recovery.assert_not_called()
                self.assertEqual(emitted.call_args.args[0]['type'], 'error')
                self.assertIn('task-state.json', emitted.call_args.args[0]['text'])
                self.assertEqual(path.read_text(), broken)
        with patch.object(a, 'read_text_cached', side_effect=PermissionError('fixture denied')):
            with self.assertRaisesRegex(ValueError, 'task-state.json'):
                a.read_task_state()
        # Only actual absence permits the legacy event recovery.
        path.unlink()
        self.assertEqual(a.read_task_state()['status'], 'interrupted')
        self.assertEqual(a.read_task_state()['prompt'], 'Analyse\n  le projet docker_trinity')

    def test_partial_stream_survives_without_a_queued_webview_snapshot(self):
        a.save_task_state(status='interrupted', prompt='travaille', error='ancien échec',
                          verification_recovery={'status': 'rolled_back'})
        a.save_task_state(status='running',prompt='travaille')
        self.assertNotIn('error', a.read_task_state())
        self.assertNotIn('verification_recovery', a.read_task_state())
        with patch.object(a,'JSON_MODE',True),patch.object(a,'emit'):
            stream=a.ResponseStream();stream.pending='Texte provisoire';stream.flush()
        a.save_task_state(status='interrupted')
        a.archive_event({'type':'model_active','model':'gpt-oss-120b'})
        a.archive_event({'type':'tool_start','id':'read','title':'Lecture','detail':'app.py'})
        restored=a.read_archive('conversation')
        self.assertEqual(restored[-1]['type'],'partial_response')
        self.assertEqual(restored[-1]['text'],'Texte provisoire')
        self.assertTrue(any(e['type']=='tool_start' for e in restored))
        self.assertFalse(any(e['type']=='response' for e in restored))
        a.save_task_state(status='running',prompt='nouvelle demande')
        self.assertFalse((self.folder/'response-draft.json').exists())
        with patch.object(a,'JSON_MODE',True),patch.object(a,'emit'):
            stream=a.ResponseStream();stream.pending='nouveau';stream.flush()
        a.save_task_state(status='completed')
        self.assertFalse((self.folder/'response-draft.json').exists())
        self.assertFalse(any(e['type']=='partial_response' for e in a.read_archive('conversation')))

    def test_interrupted_archive_bypasses_snapshot_and_exposes_resume(self):
        a.save_task_state(status='interrupted',prompt='travaille')
        outputs=[]
        def inputs(requests):
            requests.put({'type':'load_archive','id':'conversation'})
            requests.put({'type':'shutdown'})
        with ExitStack() as stack:
            mocks={'recover_verification':{},'load_history':{'return_value':[]},
                'read_snapshot':{'side_effect':AssertionError('Interrupted HTML may be stale')},
                'load_selected_model':{'return_value':a.AUTO_MODEL},'refresh_billing_async':{},
                'current_budget_status':{'return_value':{}},'publish_budget':{},
                'stdin_reader':{'side_effect':inputs},'emit':{'side_effect':outputs.append},
                'write_pointer':{},'list_archives':{'return_value':[]}}
            for name,settings in mocks.items():stack.enter_context(patch.object(a,name,**settings))
            stack.enter_context(patch.object(a,'JSON_MODE',False))
            a.main_json_lines()
        restored=[e for e in outputs if e['type']=='archive_loaded']
        self.assertEqual(len(restored),2)
        self.assertTrue(all(e['resumable'] for e in restored))

    def test_rejected_archive_keeps_pointer_and_history_for_the_next_prompt(self):
        from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart, TextPart
        initial_history = [ModelRequest(parts=[UserPromptPart('previous request')]),
                           ModelResponse(parts=[TextPart('previous evidence')])]
        broken = self.folder.parent / 'broken'
        broken.mkdir()
        pointer = self.root / 'current'
        (self.folder / 'task-state.json').write_text('{"status":"completed"}')
        for action in ('load_archive', 'resume_archive'):
            for failure in ('invalid_state', 'recovery'):
                with self.subTest(action=action, failure=failure):
                    pointer.write_text('conversation')
                    state = broken / 'task-state.json'
                    state.write_text('{broken' if failure == 'invalid_state' else
                                     '{"status":"interrupted","prompt":"old target task"}')
                    before = state.read_bytes()
                    events, prepared, recovered = [], [], []

                    def inputs(requests):
                        requests.put({'type': action, 'id': 'broken'})
                        requests.put({'type': 'prompt', 'text': 'Continue.', 'model': 'fixture-model'})
                        requests.put({'type': 'shutdown'})

                    def recover():
                        recovered.append(a.CONVERSATION_ID)
                        if a.CONVERSATION_ID == 'broken':
                            raise a.VerificationError('fixture recovery failed')

                    def prepare(history):
                        prepared.append((a.CONVERSATION_ID, history))
                        raise ValueError('fixture preparation reached')

                    with ExitStack() as stack:
                        for name, value in {'CONVERSATION_ID': 'conversation', 'POINTER_FILE': pointer,
                                            'JSON_MODE': False, 'FOLLOWUP_RUNNING': False}.items():
                            stack.enter_context(patch.object(a, name, value))
                        mocks = {
                            'recover_verification': {'side_effect': recover},
                            'load_history': {'return_value': initial_history},
                            'read_snapshot': {'return_value': None}, 'read_archive': {'return_value': []},
                            'load_selected_model': {'return_value': 'fixture-model'},
                            'refresh_billing_async': {}, 'current_budget_status': {'return_value': {}},
                            'publish_budget': {}, 'stdin_reader': {'side_effect': inputs},
                            'emit': {'side_effect': events.append}, 'list_archives': {'return_value': []},
                            'save_selected_model': {}, 'is_selectable_model': {'return_value': True},
                            'update_metadata': {'return_value': False}, 'compact_history': {'side_effect': prepare},
                        }
                        for name, settings in mocks.items():
                            stack.enter_context(patch.object(a, name, **settings))
                        a.main_json_lines()
                        self.assertEqual(a.CONVERSATION_ID, 'conversation')
                    self.assertEqual(pointer.read_text(), 'conversation')
                    self.assertEqual(state.read_bytes(), before)
                    self.assertEqual(prepared, [('conversation', initial_history)])
                    self.assertEqual(len([event for event in events if event['type'] == 'error']), 2)
                    if failure == 'invalid_state':
                        self.assertEqual(recovered, ['conversation'])

    def test_activity_durations_are_monotonic_and_paired_by_id(self):
        outputs = []
        with patch.object(a, 'emit', side_effect=outputs.append), patch.object(a, 'read_task_state', return_value={}), \
                patch.object(a.time, 'monotonic', side_effect=[10.0, 10.1, 10.4, 11.25]):
            first = a.activity_start('read', 'Lecture', 'A')
            second = a.activity_start('command', 'Commande', 'B')
            a.activity_end(second, 'Échec', False)
            a.activity_end(first, 'Lu')
            a.activity_end('missing', 'Sans début')
        self.assertEqual(outputs[2]['id'], second)
        self.assertEqual(outputs[2]['duration_ms'], 300)
        self.assertFalse(outputs[2]['success'])
        self.assertEqual(outputs[3]['duration_ms'], 1250)
        self.assertEqual(len(outputs), 4, 'An unknown activity must not emit an unpaired completion')
        self.assertNotIn(first, a.run_dependencies().activity_started)
        self.assertNotIn(second, a.run_dependencies().activity_started)

    def test_archive_timeline_preserves_timing_and_pairing_without_tool_payloads(self):
        records = [
            {'type':'tool_start', 'id':'a', 'title':'Lecture', 'timestamp':'2026-10-17T19:24:00Z', 'detail':'private path'},
            {'type':'tool_end', 'id':'a', 'duration_ms':9, 'success':True, 'detail':'private output'},
            {'type':'model_active', 'model':'Test'},
            {'type':'usage', 'model':'Test', 'duration_ms':1234, 'purpose':'compaction'},
            {'type':'request_prepared', 'preparation_ms':4},
            {'type':'user', 'steering':True, 'text':'private prompt'},
        ]
        self.events.write_text(''.join(json.dumps(item)+'\n' for item in records))
        timeline = a.read_archive('conversation', timeline_only=True)
        self.assertEqual(timeline[0]['id'], 'a')
        self.assertEqual(timeline[0]['title'], 'Lecture')
        self.assertNotIn('detail', timeline[0])
        self.assertEqual(timeline[1]['duration_ms'], 9)
        self.assertNotIn('detail', timeline[1])
        self.assertEqual(timeline[2]['model'], 'Test')
        self.assertEqual(timeline[3]['duration_ms'], 1234)
        self.assertEqual(timeline[3]['purpose'], 'compaction')
        self.assertEqual(timeline[4]['preparation_ms'], 4)
        self.assertTrue(timeline[5]['steering'])
        self.assertNotIn('text', timeline[5])

    def test_model_and_plan_timing_events_are_archived(self):
        a.archive_event({'type':'model_active','model':'Test'})
        a.archive_event({'type':'plan_updated','duration_ms':5,'steps':['Read']})
        events = [json.loads(line) for line in self.events.read_text().splitlines()]
        self.assertEqual(events[-2]['type'], 'model_active')
        self.assertEqual(events[-1]['duration_ms'], 5)

    def test_titles_recover_missing_empty_invalid_and_placeholder_metadata(self):
        for content in ('{}', '{"title":null}', '{"title":" "}', '{"title":"Nouvelle conversation"}', '{broken', 'null'):
            self.meta.write_text(content)
            a.update_metadata(model='unchanged-model')
            metadata = json.loads(self.meta.read_text())
            self.assertEqual(metadata['title'], 'Analyse le projet docker_trinity')
            self.assertEqual(metadata['model'], 'unchanged-model')
            self.assertEqual(metadata['id'], 'conversation')

    def test_later_messages_and_metadata_updates_preserve_existing_title(self):
        self.meta.write_text(json.dumps({'title': 'Titre personnalisé', 'created_at': 'original'}))
        a.archive_event({'type': 'user', 'text': 'Autre demande'})
        metadata = json.loads(self.meta.read_text())
        self.assertEqual(metadata['title'], 'Titre personnalisé')
        self.assertEqual(metadata['created_at'], 'original')
        self.meta.write_text('{}')
        a.update_metadata()
        self.assertEqual(json.loads(self.meta.read_text())['title'], 'Analyse le projet docker_trinity')

    def test_snapshot_revision_rejects_newly_saved_old_html_and_external_append(self):
        self.meta.write_text('{}')
        snapshot=self.folder/'snapshot.json'
        snapshot.write_text(json.dumps({'html':'old','model':'Qwen3.5-9B'}))
        self.assertIsNone(a.read_snapshot())
        revision=a.archive_revision()
        a.save_snapshot('current',False,a.AUTO_MODEL,revision)
        self.assertEqual(a.read_snapshot()['html'],'current')
        with self.events.open('a') as stream:stream.write(json.dumps({'type':'response','text':'new answer'})+'\n')
        self.assertIsNone(a.read_snapshot())
        previous=snapshot.read_bytes()
        a.save_snapshot('stale',False,'Qwen3.5-9B',revision)
        self.assertEqual(snapshot.read_bytes(),previous)
        a.save_snapshot('legacy',False,'Qwen3.5-9B')
        self.assertEqual(snapshot.read_bytes(),previous)
        a.save_snapshot('new answer',False,a.AUTO_MODEL,a.archive_revision())
        self.assertEqual(a.read_snapshot()['html'],'new answer')

    def test_archive_emission_preserves_selection_and_rendered_revision(self):
        with self.events.open('a') as stream:stream.write(json.dumps({'type':'model_active','model':'gpt-oss-120b','selection':a.AUTO_MODEL,'automatic':True})+'\n')
        timeline=a.read_archive('conversation',timeline_only=True)
        self.assertEqual(timeline[-1]['selection'],a.AUTO_MODEL)
        revision=a.archive_revision()
        messages=a.read_archive('conversation')
        with self.events.open('a') as stream:stream.write(json.dumps({'type':'response','text':'concurrent'})+'\n')
        import io
        output=io.StringIO()
        with patch.object(a,'JSON_MODE',True),patch('sys.stdout',output):
            a.emit({'type':'archive_loaded','id':'conversation','messages':messages})
        self.assertEqual(json.loads(output.getvalue())['archive_revision'],revision)
        self.assertNotEqual(revision,a.archive_revision())

    def test_snapshot_is_emitted_before_model_history_is_deserialized(self):
        outputs=[]
        def inputs(requests):
            requests.put({'type':'load_archive','id':'conversation'})
            requests.put({'type':'shutdown'})
        def history(candidate=None):
            if candidate is not None:
                self.assertTrue(any(e['type']=='snapshot_loaded' and not e.get('automatic') for e in outputs))
            return []
        with ExitStack() as stack:
            mocks={'recover_verification':{},'read_task_state':{'return_value':{}},
                'load_history':{'side_effect':history},'read_snapshot':{'return_value':{'html':'<p>saved</p>','model':a.AUTO_MODEL}},
                'load_selected_model':{'return_value':a.AUTO_MODEL},'refresh_billing_async':{},
                'current_budget_status':{'return_value':{}},'publish_budget':{},
                'stdin_reader':{'side_effect':inputs},'emit':{'side_effect':outputs.append},
                'write_pointer':{},'list_archives':{'return_value':[]}}
            for name,settings in mocks.items():stack.enter_context(patch.object(a,name,**settings))
            stack.enter_context(patch.object(a,'JSON_MODE',False))
            a.main_json_lines()
        self.assertTrue(any(e['type']=='snapshot_loaded' and not e.get('automatic') for e in outputs))

    def test_delayed_first_message_titles_before_failing_context_preparation(self):
        self.events.write_text('')
        self.meta.write_text(json.dumps({'id': 'conversation', 'title': 'Nouvelle conversation',
                                         'created_at': '2025-01-01T00:00:00+00:00'}))
        # Empty-conversation writes must not prevent later naming.
        a.update_metadata(model=a.AUTO_MODEL)
        a.update_metadata()
        outputs = []
        def input_messages(requests):
            requests.put({'type': 'prompt', 'text': '  Vérifie\n  la configuration DNS  '})
            requests.put({'type': 'shutdown'})
        def prepare_context(*args, **kwargs):
            metadata = json.loads(self.meta.read_text())
            self.assertEqual(metadata['title'], 'Vérifie la configuration DNS')
            self.assertEqual(metadata['created_at'], '2025-01-01T00:00:00+00:00')
            archives = [item for item in outputs if item['type'] == 'archives']
            self.assertEqual(archives[-1]['items'][0]['title'], metadata['title'])
            raise OSError('Context temporarily unavailable')
        with ExitStack() as stack:
            mocks = {
                'load_history': {'return_value': []},
                'read_snapshot': {'return_value': None},
                'load_selected_model': {'return_value': a.AUTO_MODEL},
                'save_selected_model': {},
                'refresh_billing_async': {},
                'current_budget_status': {'return_value': {}},
                'publish_budget': {},
                'stdin_reader': {'side_effect': input_messages},
                'emit': {'side_effect': outputs.append},
                'compact_history': {'return_value': ([], {})},
                'prepare_task_context': {'side_effect': prepare_context},
                'get_agent': {'side_effect': AssertionError('No model call expected')},
            }
            for name, settings in mocks.items():
                stack.enter_context(patch.object(a, name, **settings))
            stack.enter_context(patch.object(a, 'JSON_MODE', False))
            stack.enter_context(patch.object(a.run_dependencies(), 'full_access', False))
            a.main_json_lines()
        self.assertTrue(any(item.get('text') == 'Context temporarily unavailable' for item in outputs))
        self.assertEqual(json.loads(self.meta.read_text())['title'], 'Vérifie la configuration DNS')
        a.update_metadata(first_message='Deuxième demande')
        self.assertEqual(json.loads(self.meta.read_text())['title'], 'Vérifie la configuration DNS')

    def test_image_generation_never_enters_reader_or_executor(self):
        outputs=[]
        def input_messages(requests):
            requests.put({'type':'prompt','text':'Génère une image de chat','model':'gpt-oss-120b'})
            requests.put({'type':'shutdown'})
        with ExitStack() as stack:
            mocks={
                'recover_verification':{},'load_history':{'return_value':[]},
                'read_snapshot':{'return_value':None},'load_selected_model':{'return_value':'gpt-oss-120b'},
                'save_selected_model':{},'refresh_billing_async':{},'current_budget_status':{'return_value':{}},
                'publish_budget':{},'stdin_reader':{'side_effect':input_messages},'emit':{'side_effect':outputs.append},
                'is_selectable_model':{'return_value':True},
                'compact_history':{'return_value':([],{'before_chars':0})},
                'prepare_task_context':{'return_value':([],{})},
                'get_agent':{'side_effect':AssertionError('Executor must not be called')},
                'extract_visual_context':{'side_effect':AssertionError('Reader must not be called')},
                'begin_verification':{'side_effect':AssertionError('No mutations expected')},
            }
            for name,settings in mocks.items():stack.enter_context(patch.object(a,name,**settings))
            stack.enter_context(patch.object(a,'JSON_MODE',False))
            stack.enter_context(patch.object(a.run_dependencies(), 'verification',None))
            a.main_json_lines()
        error=next(e for e in outputs if e['type']=='error')
        self.assertIn('aucun outil de génération',error['text'])
        self.assertFalse(any(e['type']=='response' for e in outputs))

    def test_resume_uses_explicit_preferences_and_defaults_to_restricted_access(self):
        for preferences, expected_access, expected_model in (
                ({'full_access': False, 'model': 'selected-model'}, False, 'selected-model'),
                ({'full_access': True, 'model': 'selected-model'}, True, 'selected-model'),
                ({}, False, 'old-model')):
            with self.subTest(preferences=preferences):
                self.meta.write_text(json.dumps({'model':'old-model'}))
                a.save_task_state(status='interrupted', prompt='Corrige app.py', full_access=True,
                                  model='old-model', images=[], last_tool={})
                observed = []
                def inputs(requests):
                    requests.put({'type': 'resume_archive', 'id': 'conversation', **preferences})
                    requests.put({'type': 'shutdown'})
                def prepare(*args, **kwargs):
                    observed.append(a.run_dependencies().full_access)
                    raise ValueError('fixture stops before model')
                with ExitStack() as stack:
                    mocks = {
                        'recover_verification': {}, 'load_history': {'return_value': []},
                        'read_archive': {'return_value': []}, 'read_snapshot': {'return_value': None},
                        'load_selected_model': {'return_value': a.AUTO_MODEL},
                        'refresh_billing_async': {}, 'current_budget_status': {'return_value': {}},
                        'publish_budget': {}, 'stdin_reader': {'side_effect': inputs},
                        'emit': {}, 'write_pointer': {}, 'list_archives': {'return_value': []},
                        'save_selected_model': {}, 'is_selectable_model': {'return_value': True},
                        'compact_history': {'return_value': ([], {})},
                        'prepare_task_context': {'side_effect': prepare},
                        'get_agent': {'side_effect': AssertionError('No model call expected')},
                    }
                    patched = {name: stack.enter_context(patch.object(a, name, **settings))
                               for name, settings in mocks.items()}
                    stack.enter_context(patch.object(a, 'JSON_MODE', False))
                    stack.enter_context(patch.object(a.run_dependencies(), 'full_access', False))
                    a.main_json_lines()
                    self.assertEqual(observed, [expected_access])
                    patched['save_selected_model'].assert_called_once_with(expected_model)

    def test_document_question_is_always_proxied_to_the_ovh_model(self):
        prompt='Comment lancer les tests du projet ?'
        calls,outputs=self.run_failed_queue([
            {'type':'prompt','text':prompt,'model':'fixture-model'}])
        self.assertEqual(calls,[prompt])
        self.assertTrue(any(event['type']=='error' for event in outputs))

    def test_project_memory_precedes_workspace_retrieval(self):
        order=[]
        prepared={'context':'','sources':[],'files_scanned':0,'partial':False,'preparation_ms':0,'analysis':{'query':'Question'}}
        with patch.object(a,'history_with_memory',side_effect=lambda history,query, **kwargs: order.append('memory') or history), \
             patch.object(a,'prepare_request',side_effect=lambda *args, **kwargs: order.append('files') or prepared):
            a.prepare_task_context([],'Question')
        self.assertEqual(order,['memory','files'])

    def test_first_message_fallback_preserves_archived_and_custom_titles(self):
        for title in (None, '', '  Nouvelle conversation  '):
            self.meta.write_text(json.dumps({'title': title}))
            a.update_metadata(first_message='Nouvelle demande')
            self.assertEqual(json.loads(self.meta.read_text())['title'], 'Analyse le projet docker_trinity')
        self.meta.write_text(json.dumps({'title': 'Titre personnalisé'}))
        a.update_metadata(first_message='Nouvelle demande')
        self.assertEqual(json.loads(self.meta.read_text())['title'], 'Titre personnalisé')

    def test_title_written_even_when_memory_indexing_fails(self):
        self.meta.write_text('{}')
        with patch.object(a.ConversationMemory, 'put', side_effect=OSError('index unavailable')):
            with self.assertRaises(OSError):
                a.archive_event({'type': 'user', 'text': 'Later user request'})
        self.assertEqual(json.loads(self.meta.read_text())['title'], 'Analyse le projet docker_trinity')

    def test_empty_and_image_conversations_and_archive_display(self):
        self.events.write_text('')
        self.meta.write_text('{}')
        a.update_metadata()
        self.assertEqual(json.loads(self.meta.read_text())['title'], 'Nouvelle conversation')
        self.events.write_text('invalid\nnull\n' + json.dumps({'type': 'user', 'images': [{}]}) + '\n')
        a.update_metadata()
        self.assertEqual(a.list_archives()[0]['title'], 'Analyse cette image.')
        self.meta.write_text('{}')
        self.assertEqual(a.list_archives()[0]['title'], 'Analyse cette image.')

    def test_concurrent_updates_keep_all_fields_and_atomic_readable_json(self):
        self.meta.write_text('{}')
        stop = threading.Event()
        errors = []
        def read_loop():
            while not stop.is_set():
                try:
                    json.loads(self.meta.read_text())
                except Exception as error:
                    errors.append(error)
        reader = threading.Thread(target=read_loop)
        reader.start()
        try:
            with ThreadPoolExecutor(max_workers=8) as workers:
                list(workers.map(lambda i: a.update_metadata(**{f'field{i}': i}), range(40)))
        finally:
            stop.set()
            reader.join()
        self.assertFalse(errors)
        metadata = json.loads(self.meta.read_text())
        self.assertTrue(all(metadata[f'field{i}'] == i for i in range(40)))
        self.assertEqual(metadata['title'], 'Analyse le projet docker_trinity')

    def test_second_backend_refused_before_any_archive_mutation(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with a.backend_session():
            result = subprocess.run([sys.executable, str(ROOT / 'ovh-agent.py'), '--json-lines'],
                                    env={**os.environ, 'OVH_AGENT_DATA_ROOT': str(self.root),
                                         'OVH_AGENT_WORKSPACE': str(ROOT)},
                                    capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['type'], 'session_busy')
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        with a.backend_session():
            pass  # No stale lock after clean shutdown.

    def test_open_window_on_another_workspace_does_not_block_relay(self):
        other_workspace = self.root / 'other-project'
        other_workspace.mkdir()
        with a.backend_session():
            result = subprocess.run(
                [sys.executable, str(ROOT / 'ovh-agent.py'), '--json-lines'],
                env={**os.environ, 'OVH_AGENT_DATA_ROOT': str(self.root),
                     'OVH_AGENT_WORKSPACE': str(other_workspace)},
                input=json.dumps({'type': 'shutdown'}) + '\n',
                capture_output=True, text=True, timeout=15,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        events = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertTrue(any(event.get('type') == 'ready' for event in events), events)
        self.assertFalse(any(event.get('type') == 'session_busy' for event in events), events)


if __name__ == '__main__':
    unittest.main()
