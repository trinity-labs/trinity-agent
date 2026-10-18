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

    def test_partial_stream_survives_without_a_queued_webview_snapshot(self):
        a.save_task_state(status='running',prompt='travaille')
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
        self.assertIsNone(outputs[4]['duration_ms'])
        self.assertNotIn(first, a.ACTIVITY_STARTED)
        self.assertNotIn(second, a.ACTIVITY_STARTED)

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
        def prepare_context(*args):
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
            stack.enter_context(patch.object(a, 'FULL_ACCESS', False))
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
            stack.enter_context(patch.object(a,'VERIFICATION_GUARD',None))
            a.main_json_lines()
        error=next(e for e in outputs if e['type']=='error')
        self.assertIn('aucun outil de génération',error['text'])
        self.assertFalse(any(e['type']=='response' for e in outputs))

    def test_local_document_answer_archives_without_any_model_or_compaction(self):
        workspace=self.root/'project';workspace.mkdir()
        prompt='Comment lancer les tests du projet ?'
        (workspace/'FAQ.md').write_text('## '+prompt+'\nUtiliser `python3 -m unittest`.\n')
        self.events.write_text('');outputs=[]
        def input_messages(requests):
            requests.put({'type':'prompt','text':prompt});requests.put({'type':'shutdown'})
        def output(event):
            outputs.append(event);a.archive_event(event)
        with ExitStack() as stack:
            stack.enter_context(patch.object(a,'WORKSPACE',workspace))
            mocks={
                'recover_verification':{},'load_history':{'return_value':[]},
                'read_snapshot':{'return_value':None},'load_selected_model':{'return_value':a.AUTO_MODEL},
                'save_selected_model':{},'refresh_billing_async':{},'current_budget_status':{'return_value':{}},
                'publish_budget':{},'stdin_reader':{'side_effect':input_messages},'emit':{'side_effect':output},
                'schedule_export':{},'compact_history':{'side_effect':AssertionError('No compaction')},
                'prepare_task_context':{'side_effect':AssertionError('No broader retrieval')},
                'get_agent':{'side_effect':AssertionError('No model call')},
                'choose_cost_effective_model':{'side_effect':AssertionError('No routing')},
                'begin_verification':{'side_effect':AssertionError('No verification performed')},
            }
            for name,settings in mocks.items():stack.enter_context(patch.object(a,name,**settings))
            stack.enter_context(patch.object(a,'JSON_MODE',False))
            a.main_json_lines()
        response=next(e for e in outputs if e['type']=='response')
        self.assertIn('python3 -m unittest',response['text'])
        self.assertEqual(response['source'],'project-memory')
        usage=next(e for e in outputs if e['type']=='usage')
        self.assertEqual((usage['requests'],usage['cost_eur']),(0,0))
        self.assertTrue((self.folder/'messages.json').exists())
        events=[json.loads(line) for line in self.events.read_text().splitlines()]
        self.assertEqual(sum(e['type']=='user' for e in events),1)
        self.assertEqual(sum(e['type']=='response' for e in events),1)
        self.assertEqual(a.read_task_state()['status'],'completed')

    def test_project_memory_precedes_workspace_retrieval(self):
        order=[]
        prepared={'context':'','sources':[],'files_scanned':0,'partial':False,'preparation_ms':0,'analysis':{'query':'Question'}}
        with patch.object(a,'history_with_memory',side_effect=lambda history,query, **kwargs: order.append('memory') or history), \
             patch.object(a,'prepare_request',side_effect=lambda *args: order.append('files') or prepared):
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
                                    env={**os.environ, 'OVH_AGENT_DATA_ROOT': str(self.root)},
                                    capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['type'], 'session_busy')
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        with a.backend_session():
            pass  # No stale lock after clean shutdown.


if __name__ == '__main__':
    unittest.main()
