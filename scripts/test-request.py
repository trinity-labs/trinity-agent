"""Offline tests for local retrieval, freshness, scope and slow SSHFS fallback."""
import importlib.util
from contextlib import ExitStack
from types import SimpleNamespace
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ovh_request as r
from ovh_cache import CACHE
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.messages import (
    ModelRequest, ModelResponse, SystemPromptPart, UserPromptPart,
    TextPart, ToolCallPart, ToolReturnPart,
)
from pydantic_ai.models.function import FunctionModel


class Preparation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.allowed = Path(self.temp.name)
        self.project = self.allowed / 'machine' / 'project'
        self.project.mkdir(parents=True)
        (self.project / 'package.json').write_text('{}')
        self.source = self.project / 'chat.css'
        self.source.write_text('.archive-icon:hover { color: green; opacity: .33; }\n')
        CACHE.clear()

    def retrieve(self, prompt, recent=(), **kwargs):
        return r.retrieve_context(r.analyze_request(prompt), recent, self.project, self.allowed, **kwargs)

    def test_actual_catalog_edit_requests_prepare_workspace(self):
        for prompt in ('Modernise la vue des modèles', 'Passe les bouton du catalogue de modèle en non fill',
                       'Rends les boutons transparents'):
            with self.subTest(prompt=prompt):
                a = r.analyze_request(prompt)
                self.assertEqual((a.intent, a.domain, a.needs_workspace), ('edit', 'ui', True))
        for prompt in ('Comment moderniser la vue des modèles ?', 'Passe en revue le catalogue',
                       'Lis la vue sans modifier les boutons'):
            self.assertNotEqual(r.analyze_request(prompt).intent, 'edit')

    def test_analysis_and_chained_followups_leave_original_request_intact(self):
        previous = ['Refactorise notre extension dans `chat.css`', 'Continue', 'ok aussi ça']
        analysis = r.analyze_request('oui continue', previous, image_count=1)
        self.assertEqual((analysis.domain, analysis.intent, analysis.scope), ('ui', 'edit', 'project'))
        self.assertEqual(analysis.complexity, 'complex')
        self.assertTrue(analysis.followup and analysis.needs_workspace and analysis.needs_vision)
        self.assertIn('chat.css', analysis.paths)
        self.assertTrue(analysis.query.startswith('oui continue'))
        self.assertIn('Refactorise', analysis.query)
        self.assertEqual(r.analyze_request('Diminue l’opacité de notre icône').domain, 'ui')
        security = r.analyze_request('Recherche sur la documentation CVE-2026-12345')
        self.assertTrue(security.needs_web)
        self.assertFalse(security.needs_workspace)

    def test_public_question_does_not_inspect_workspace(self):
        with patch.object(r, 'retrieve_context', side_effect=AssertionError('Filesystem not needed')):
            result = r.prepare_request('Explique Docker', [], self.project, self.allowed)
        self.assertFalse(result['analysis']['needs_workspace'])
        self.assertEqual(result['context'], '')

    def test_readonly_and_offline_negations_do_not_request_edits_or_web(self):
        analysis = r.analyze_request('Lis settings.json. Aucune modification ni recherche Internet.')
        self.assertEqual(analysis.intent, 'question')
        self.assertTrue(analysis.needs_workspace)
        self.assertFalse(analysis.web_allowed or analysis.needs_web)
        followup = r.analyze_request('Continue sans modifier les fichiers', ['Modifie notre extension'])
        self.assertNotEqual(followup.intent, 'edit')
        self.assertEqual(r.analyze_request('Modifie chat.css sans modifier extension.js').intent, 'edit')

    def test_targeted_excerpts_are_exact_and_warm_cache_is_reused(self):
        prompt = 'Modifie le survol de l’icône dans `chat.css`'
        first = self.retrieve(prompt)
        self.assertTrue(first['context'].startswith(r.CONTEXT_PREFIX))
        self.assertIn(self.source.read_text().strip(), first['context'])
        self.assertIn({'path': str(self.source), 'start': 1, 'end': 1}, first['sources'])
        with patch.object(r, 'read_text_cached', side_effect=AssertionError('Warm chunks reloaded')):
            second = self.retrieve(prompt)
        self.assertEqual(first['context'], second['context'])

    def test_edit_replace_delete_and_raced_read_do_not_replay_stale_code(self):
        prompt = 'Modifie `chat.css`'
        self.retrieve(prompt)
        self.source.write_text('.archive-icon:hover { color: coral; opacity: .33; }\n')
        self.assertIn('coral', self.retrieve(prompt)['context'])
        replacement = self.project / 'replacement.css'
        replacement.write_text('.archive-icon { color: blue; }')
        replacement.replace(self.source)
        self.assertIn('blue', self.retrieve(prompt)['context'])
        self.source.unlink()
        self.assertNotIn('chat.css', [Path(s['path']).name for s in self.retrieve(prompt)['sources']])
        self.source.write_text('before')
        CACHE.clear()
        def raced_read(path, limit):
            path.write_text('after')
            return 'before'
        with patch.object(r, 'read_text_cached', side_effect=raced_read):
            self.assertEqual(r._file_chunks(self.source), [])

    def test_secrets_generated_files_and_outside_symlinks_are_excluded(self):
        (self.project / '.env.json').write_text('secretfixture')
        (self.project / 'credentials.json').write_text('secretfixture')
        (self.project / 'node_modules').mkdir()
        (self.project / 'node_modules' / 'generated.js').write_text('secretfixture')
        with tempfile.TemporaryDirectory() as outside:
            foreign = Path(outside) / 'outside.css'
            foreign.write_text('secretfixture')
            (self.project / 'outside.css').symlink_to(foreign)
            (self.project / 'linked.json').symlink_to(self.project / 'credentials.json')
            result = self.retrieve('Modifie notre secretfixture dans `credentials.json` et `outside.css`')
        self.assertNotIn('secretfixture', result['context'])
        self.assertFalse(result['sources'])

    def test_global_workspace_does_not_walk_all_mounted_machines(self):
        with patch.object(r.os, 'walk', side_effect=AssertionError('Global traversal')):
            result = r.retrieve_context(r.analyze_request('Améliore notre extension'), [], self.allowed, self.allowed)
        self.assertFalse(result['sources'])
        machine_file = self.allowed / 'machine' / 'notes.md'
        machine_file.write_text('request fixture')
        self.assertIsNone(r._project_root(machine_file, self.allowed, self.allowed))

    def test_recent_tools_focus_search_and_sources_match_line_numbers(self):
        lines = [f'/* line {i}: archive icon hover opacity */' for i in range(180)]
        self.source.write_text('\n'.join(lines))
        result = r.retrieve_context(r.analyze_request('Corrige notre icône au survol'),
                                    [str(self.source)], self.allowed, self.allowed, max_chars=1700)
        self.assertTrue(result['sources'])
        self.assertLessEqual(len(result['context']), 1700)
        for source in result['sources']:
            self.assertIn('\n'.join(lines[source['start'] - 1:source['end']]), result['context'])
        self.assertFalse(self.retrieve('Modifie `chat.css`', max_chars=20)['sources'])

    def test_large_inventory_is_bounded(self):
        inventory = [(str(self.project), [], [f'file{i}.py' for i in range(2500)])]
        with patch.object(r.os, 'walk', return_value=iter(inventory)), \
             patch.dict(os.environ, {'OVH_AGENT_PREFLIGHT_MAX_FILES':'120'}):
            result = self.retrieve('Modifie notre extension dans `chat.css`')
        self.assertLessEqual(result['files_scanned'], 120)
        self.assertTrue(result['partial'])
        self.assertTrue(result['sources'])

    def test_larger_local_profile_finds_late_files_and_long_file_evidence(self):
        for index in range(240):
            (self.project / f'unit_{index:03}.py').write_text(
                'def route_recovery():\n    return "nebula_recovery handling"\n' if index == 220
                else 'def unrelated():\n    return "ordinary data"\n')
        long = self.project / 'long.py'
        long.write_text('# ordinary filler\n' * 6000 + 'def nebula_recovery():\n    return "late_evidence"\n')
        from ovh_resources import Hardware, ResourcePolicy, MIB
        policy = ResourcePolicy(lambda: Hardware(4, 8192*MIB, 6144*MIB))
        with patch.object(r, 'resource_profile', side_effect=policy.profile), \
             patch.dict(os.environ, {'OVH_AGENT_PREFLIGHT_MAX_FILES':'480','OVH_AGENT_PREFLIGHT_FILE_CHARS':'524288'}):
            result = self.retrieve('Corrige notre nebula_recovery')
        self.assertIn('handling', result['context'])
        self.assertIn('late_evidence', result['context'])
        self.assertLessEqual(len(result['context']), 24000)
        self.assertEqual(result['retrieval']['method'], 'bm25-diverse')

    def test_truncated_line_is_not_presented_as_a_complete_source_line(self):
        self.source.write_text('complete line\n' + 'incomplete ' * 20)
        with patch.dict(os.environ, {'OVH_AGENT_PREFLIGHT_FILE_CHARS':'50'}):
            chunks = r._file_chunks(self.source)
        self.assertEqual(chunks[0][:3], (1, 1, 'complete line'))

    def test_max_searches_beyond_two_thousand_names_without_expanding_model_context(self):
        from ovh_resources import ResourcePolicy, Hardware, MIB
        policy=ResourcePolicy(lambda:Hardware(4,8192*MIB,6144*MIB))
        policy.set_mode('max')
        for index in range(2010):
            (self.project/f'unit_{index:04}.py').write_text('ordinary = True\n')
        (self.project/'zz_evidence.py').write_text('nebula_recovery = "late_search_evidence"\n')
        with patch.object(r,'resource_profile',side_effect=policy.profile):
            result=self.retrieve('Corrige notre nebula_recovery',deadline=time.monotonic()+10)
        self.assertGreater(result['files_scanned'],2000)
        self.assertIn('late_search_evidence',result['context'])
        self.assertLessEqual(len(result['context']),24000)
        self.assertEqual(result['retrieval']['resources']['mode'],'max')

    def test_parallel_readers_overlap_independent_reads_and_sort_results(self):
        paths = []
        for index in range(4):
            path = self.project / f'reader{index}.py';path.write_text('fixture');paths.append((path, 0))
        pool = r.ChunkReaders(4)
        barrier = threading.Barrier(4)
        def read(path):
            barrier.wait(timeout=1)
            return [(1,1,path.name,r.word_counts(path.name))]
        with patch.object(r, '_file_chunks', side_effect=read):
            loaded, partial = pool.read(paths, self.project, self.allowed, time.monotonic()+2)
        self.assertFalse(partial)
        self.assertEqual([row[1] for row in loaded], [row[0] for row in paths])

    def test_stalled_readers_have_bounded_queues_and_never_return_old_results(self):
        pool = r.ChunkReaders(2)
        release, entered = threading.Event(), threading.Event()
        def slow(path):
            entered.set();release.wait(2)
            return [(1,1,'obsolete',r.word_counts('obsolete'))]
        try:
            with patch.object(r, '_file_chunks', side_effect=slow):
                for _ in range(3):
                    start = time.monotonic()
                    loaded, partial = pool.read([(self.source,0)]*20, self.project, self.allowed, start+.02)
                    self.assertLess(time.monotonic()-start,.4)
                    self.assertTrue(partial)
                    self.assertFalse(loaded)
                    self.assertLessEqual(pool.jobs.qsize(),4)
                self.assertTrue(entered.is_set())
                release.set();pool.jobs.join()
        finally:
            release.set()
        loaded, partial = pool.read([(self.source,0)],self.project,self.allowed,time.monotonic()+1)
        self.assertFalse(partial)
        self.assertNotIn('obsolete',str(loaded))

    def test_bm25_does_not_prefer_repetition_over_rare_exact_evidence(self):
        (self.project/'repeated.py').write_text(('archive icon hover '*90+'\n')*20)
        (self.project/'specific.py').write_text('def reconcile_project():\n    return "archive icon hover nebula_recovery"\n')
        result = self.retrieve('Corrige notre archive icon hover nebula_recovery')
        self.assertEqual(Path(result['sources'][0]['path']).name,'specific.py')

    def test_recovery_copies_are_not_mixed_with_current_code(self):
        recovery=self.project/'local-recovery';recovery.mkdir()
        (recovery/'chat.css').write_text('outdated_rare_fixture')
        self.assertNotIn('outdated_rare_fixture',self.retrieve('Corrige notre outdated_rare_fixture')['context'])

    def test_disabled_retrieval_and_reader_failure_keep_agent_available(self):
        with patch.dict(os.environ, {'OVH_AGENT_PREFLIGHT_CONTEXT_CHARS': '0'}), \
             patch.object(r, 'retrieve_context', side_effect=AssertionError('Disabled reader')):
            self.assertFalse(r.prepare_request('Modifie `chat.css`', [], self.project, self.allowed)['sources'])
        with patch.object(r, 'retrieve_context', side_effect=OSError('Unavailable mount')):
            result = r.prepare_request('Modifie `chat.css`', [], self.project, self.allowed)
        self.assertTrue(result['partial'])
        self.assertEqual(result['context'], '')

    def test_slow_mount_bounds_wait_and_does_not_accumulate_workers(self):
        entered, release = threading.Event(), threading.Event()
        def slow(*args):
            entered.set()
            release.wait(2)
            return {'context': 'late stale data', 'sources': [], 'files_scanned': 1, 'partial': False}
        try:
            with patch.dict(os.environ, {'OVH_AGENT_PREFLIGHT_TIMEOUT_MS': '20'}), \
                 patch.object(r, 'retrieve_context', side_effect=slow) as reader:
                started = time.monotonic()
                first = r.prepare_request('Modifie `chat.css`', [], self.project, self.allowed)
                second = r.prepare_request('Modifie `chat.css`', [], self.project, self.allowed)
                self.assertLess(time.monotonic() - started, .5)
                self.assertTrue(entered.is_set())
                self.assertTrue(first['partial'] and second['partial'])
                self.assertEqual(reader.call_count, 1)
        finally:
            release.set()
            r._worker.join(2)
        self.assertEqual(first['context'], '')
        self.assertNotIn('late stale', r.prepare_request('Modifie `chat.css`', [], self.project, self.allowed)['context'])

    def test_cleaning_preserves_mixed_messages_tool_pairs_and_original_user_text(self):
        original = 'Modifie le survol sans changer le reste.'
        user = UserPromptPart(original)
        history = [ModelRequest(parts=[SystemPromptPart(r.CONTEXT_PREFIX + ' old'), user]),
                   ModelResponse(parts=[ToolCallPart('read_file', {'path': str(self.source)}, 'call1')]),
                   ModelRequest(parts=[ToolReturnPart('read_file', 'old file', 'call1')]),
                   ModelResponse(parts=[TextPart('Done')])]
        cleaned = r.strip_prepared_context(history)
        self.assertEqual(cleaned[0].parts, [user])
        self.assertIs(cleaned[1], history[1])
        self.assertIs(cleaned[2], history[2])
        self.assertEqual(len(history[0].parts), 2)
        self.assertEqual(r.recent_context(cleaned), ([original], [str(self.source)]))

    def test_backend_integration_preserves_prompt_and_protocol_without_network(self):
        with patch.object(sys, 'argv', ['test-request', '--billing-status']):
            spec = importlib.util.spec_from_file_location('request_test_agent', ROOT / 'ovh-agent.py')
            backend = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(backend)
        history = [ModelRequest(parts=[SystemPromptPart(r.CONTEXT_PREFIX + ' obsolete'),
                                       UserPromptPart('Modifie `chat.css`')]),
                   ModelResponse(parts=[ToolCallPart('read_file', {'path': str(self.source)}, 'call1')]),
                   ModelRequest(parts=[ToolReturnPart('read_file', 'previous code', 'call1')]),
                   ModelResponse(parts=[TextPart('Done')])]
        prompt = 'oui continue'
        with patch.object(backend, 'WORKSPACE', self.project), patch.object(backend, 'ALLOWED_ROOT', self.allowed), \
             patch.object(backend, 'history_with_memory', side_effect=lambda messages, query, **kwargs: messages) as memory:
            prepared, details = backend.prepare_task_context(history, prompt)
        self.assertIn('chat.css', memory.call_args.args[1])
        self.assertTrue(details['sources'])
        self.assertNotIn('query', details['analysis'])
        def model(messages, info):
            parts = [part for message in messages for part in message.parts]
            self.assertEqual([p.content for p in parts if p.part_kind == 'user-prompt'][-1], prompt)
            self.assertEqual([p.tool_call_id for p in parts if p.part_kind == 'tool-call'], ['call1'])
            self.assertEqual([p.tool_call_id for p in parts if p.part_kind == 'tool-return'], ['call1'])
            contexts = [p.content for p in parts if p.part_kind == 'system-prompt' and p.content.startswith(r.CONTEXT_PREFIX)]
            self.assertEqual(len(contexts), 1)
            self.assertNotIn('obsolete', contexts[0])
            return ModelResponse(parts=[TextPart('verified')])
        result = Agent(FunctionModel(model)).run_sync(prompt, message_history=prepared)
        self.assertEqual(result.output, 'verified')


class SourceEvidence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(sys, 'argv', ['test-request', '--billing-status']):
            spec = importlib.util.spec_from_file_location('source_review_agent', ROOT / 'ovh-agent.py')
            cls.backend = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.backend)

    def setUp(self):
        self.review = r.SourceReview(required=True)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.guard = SimpleNamespace(source_review=self.review, require_verified=lambda: {'status':'not_required'})
        self.stack.enter_context(patch.object(self.backend, 'VERIFICATION_GUARD', self.guard))
        self.stack.enter_context(patch.object(self.backend, 'activity_start', return_value='web'))
        self.stack.enter_context(patch.object(self.backend, 'activity_end'))
        self.stack.enter_context(patch.object(self.backend, 'conversation_dir', return_value=Path('/unused-test-archive')))
        self.stack.enter_context(patch.object(self.backend.ConversationMemory, 'put', return_value='source-reference'))
        self.source = 'https://docs.example.invalid/reference'
        self.rss = ('<rss><channel><item><title>Official reference</title><link>' + self.source + '</link>'
                    '<description>Search snippet only</description></item></channel></rss>').encode()

    def test_challenges_trigger_research_and_keep_relevant_previous_context(self):
        for prompt in ['Tu es sûr ?', 'Donc Qwen s’était trompé ?', 'Pourquoi cette erreur ?',
                       'Je doute de cette réponse', "C’est faux", 'Are you sure?',
                       'You are wrong', 'Erreur : HTTP 429', 'Est-ce normal ?',
                       "T’as vraiment lu toute la doc ?", 'Tu mens !',
                       'Tu confirmes ?', 'Corrige ta réponse incorrecte']:
            with self.subTest(prompt=prompt):
                analysis = r.analyze_request(prompt, ['Explique les limites API OVHcloud'])
                self.assertTrue(analysis.fact_check and analysis.needs_web)
                self.assertIn('OVHcloud', analysis.query)
                self.assertFalse(analysis.needs_workspace)
        analysis = r.analyze_request('Tu es sûr ?', ['Corrige notre code dans `app.py`'])
        self.assertTrue(analysis.needs_workspace)
        self.assertIn('app.py', analysis.paths)
        self.assertEqual(analysis.intent, 'research')

    def test_offline_request_and_ui_error_label_do_not_start_web_review(self):
        for prompt in ['Vérifie cette erreur sans internet', 'Tu es sûr ? Reste hors ligne.',
                       'Are you sure? Do not browse.', 'Ajoute un message erreur dans le bouton',
                       'Augmente la taille des icônes', 'Ajoute des tests pour les entrées incorrectes']:
            with self.subTest(prompt=prompt):
                analysis = r.analyze_request(prompt)
                self.assertFalse(analysis.fact_check)
                self.assertFalse(analysis.needs_web)

    def test_explicit_offline_mode_blocks_both_web_tools(self):
        self.guard.source_review = r.SourceReview(allowed=False)
        with patch.object(self.backend, 'fetch_web_document') as fetch:
            with self.assertRaises(ModelRetry):
                self.backend.search_web('private project error')
            with self.assertRaises(ModelRetry):
                self.backend.read_webpage(self.source)
            fetch.assert_not_called()

    def test_steering_challenge_resets_evidence_and_offline_instruction_disables_web(self):
        self.review.record_search('official','old topic',1)
        self.review.record_page(self.source,42)
        item = {'id':'steer','mode':'steer','text':'Tu es sûr ?'}
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.backend, 'FOLLOWUP_RUNNING', True))
            stack.enter_context(patch.object(self.backend, 'read_followups', return_value=[item]))
            for name in ['write_followups','publish_followups','archive_event','save_task_state','emit']:
                stack.enter_context(patch.object(self.backend,name))
            stack.enter_context(patch.object(self.backend,'read_task_state',return_value={}))
            messages=self.backend.apply_steering([])
            self.assertTrue(self.guard.source_review.required)
            self.assertFalse(self.guard.source_review.pages)
            self.assertTrue(any(isinstance(p,SystemPromptPart) and p.content.startswith(r.REVIEW_PREFIX)
                                for message in messages for p in message.parts))
            with self.assertRaises(ModelRetry):
                self.backend.validate_task_output('Old source: '+self.source)
            item['text']='Sans internet, reste hors ligne.'
            self.backend.apply_steering(messages)
            self.assertFalse(self.guard.source_review.allowed)
            self.assertEqual(self.backend.validate_task_output('Constat local uniquement.'),'Constat local uniquement.')

    def test_official_first_then_page_read_and_real_citation_are_enforced(self):
        with patch.object(self.backend, 'fetch_web_document', return_value=(self.rss, 'utf-8')) as fetch:
            with self.assertRaises(ModelRetry):
                self.backend.search_web('API failure', source_type='issues')
            fetch.assert_not_called()
            with self.assertRaises(ModelRetry):
                self.backend.validate_task_output('It is certainly correct.')
            self.backend.search_web('API failure')
            self.assertIn('official+documentation', fetch.call_args.args[0])
            with self.assertRaisesRegex(ModelRetry, 'extraits'):
                self.backend.validate_task_output('The snippet confirms it.')
            with self.assertRaisesRegex(ModelRetry, 'Consulte'):
                self.backend.search_web('API failure', source_type='issues')
        with patch.object(self.backend, 'fetch_web_document', return_value=(b'<p>Exact documented limit.</p>', 'utf-8')):
            result = self.backend.read_webpage(self.source)
            self.assertIn('Exact documented limit.', result)
        with self.assertRaisesRegex(ModelRetry, 'URL'):
            self.backend.validate_task_output('Source: https://invented.invalid/')
        with self.assertRaisesRegex(ModelRetry, 'URL'):
            self.backend.validate_task_output('Source: ' + self.source + '-never-read')
        answer = 'Correction fondée sur [la référence](' + self.source + ').'
        self.assertEqual(self.backend.validate_task_output(answer), answer)
        with patch.object(self.backend, 'fetch_web_document', return_value=(self.rss, 'utf-8')) as fetch:
            self.backend.search_web('project API failure', source_type='issues')
            self.assertIn('site%3Agithub.com', fetch.call_args.args[0])

    def test_empty_documentation_search_requires_issue_fallback(self):
        with patch.object(self.backend, 'fetch_web_document', return_value=(b'<rss><channel/></rss>', 'utf-8')):
            self.backend.search_web('project error')
            with self.assertRaisesRegex(ModelRetry, 'issues'):
                self.backend.validate_task_output('No evidence found.')
            self.backend.search_web('project error', source_type='issues')
        result = self.backend.validate_task_output('Aucune source trouvée.')
        self.assertTrue(result.startswith('Vérification externe incomplète'))
        self.assertEqual(self.backend.validate_task_output(result), result)

    def test_network_failure_is_visible_and_is_not_counted_as_evidence(self):
        with patch.object(self.backend, 'fetch_web_document', side_effect=OSError('network unavailable')):
            self.assertIn('Échec', self.backend.search_web('project error'))
            self.assertIn('Impossible', self.backend.read_webpage(self.source))
        self.assertFalse(any(p['characters'] for p in self.review.pages))
        result = self.backend.validate_task_output('Vérification impossible pour le moment.')
        self.assertTrue(result.startswith('Vérification externe incomplète'))

    def test_search_discards_results_that_ignore_an_explicit_site_filter(self):
        rss=b'<rss><channel><item><title>Irrelevant</title><link>https://example.test/unrelated</link></item></channel></rss>'
        with patch.object(self.backend,'fetch_web_document',return_value=(rss,'utf-8')):
            result=self.backend.search_web('site:developer.android.com POST_NOTIFICATIONS')
        self.assertIn('Aucun résultat web conforme',result)
        self.assertNotIn('https://example.test/unrelated',result)
        self.assertEqual(self.review.searches[-1]['results'],0)

    def test_empty_page_and_out_of_range_excerpt_do_not_satisfy_read_gate(self):
        self.review.record_search('official', 'query', 1)
        for html, offset in [(b'<script>hidden</script>',0),(b'<p>Short</p>',1000)]:
            with patch.object(self.backend, 'fetch_web_document', return_value=(html, 'utf-8')):
                self.backend.read_webpage(self.source, offset=offset)
        self.assertTrue(self.backend.validate_task_output('Source: ' + self.source).startswith('Vérification externe incomplète'))

    def test_web_query_finds_evidence_after_navigation_without_inventing_a_match(self):
        page=('<nav>'+'navigation '*2000+'</nav><main>Current paramstyle is qmark.</main>').encode()
        with patch.object(self.backend,'fetch_web_document',return_value=(page,'utf-8')):
            result=self.backend.read_webpage(self.source,length=400,query='paramstyle')
            self.assertIn('Current paramstyle is qmark.',result)
            self.assertLess(len(result),1200)
            missing=self.backend.read_webpage(self.source,query='nonexistent')
            self.assertIn('non trouvé',missing)
        self.assertEqual(self.review.pages[-1]['characters'],0)

    def test_review_instructions_are_injected_then_removed_for_the_next_request(self):
        history = [ModelRequest(parts=[UserPromptPart('Explique Docker')])]
        with patch.object(self.backend, 'history_with_memory', side_effect=lambda messages,query, **kwargs:messages):
            prepared, details = self.backend.prepare_task_context(history, 'Tu es sûr ?')
            self.assertTrue(details['analysis']['fact_check'])
            self.assertTrue(any(isinstance(p,SystemPromptPart) and p.content.startswith(r.REVIEW_PREFIX)
                                for message in prepared for p in message.parts))
            clean, details = self.backend.prepare_task_context(prepared, 'Bonjour')
            self.assertFalse(details['analysis']['fact_check'])
            self.assertFalse(any(isinstance(p,SystemPromptPart) and p.content.startswith(r.REVIEW_PREFIX)
                                 for message in clean for p in message.parts))
        fresh = r.SourceReview()
        self.assertEqual(fresh.validate('Bonjour'), 'Bonjour')
        self.assertFalse(fresh.searches or fresh.pages)

    def test_pydantic_loop_retries_an_unresearched_answer_then_accepts_read_source(self):
        count = 0
        def model(messages, info):
            nonlocal count
            count += 1
            if count == 1:
                return ModelResponse(parts=[TextPart('My original answer was right.')])
            if count == 2:
                self.assertIn('recherche documentaire', str(messages[-1]))
                return ModelResponse(parts=[ToolCallPart('search_web', {'query':'project API'}, 'search')])
            if count == 3:
                return ModelResponse(parts=[ToolCallPart('read_webpage', {'url':self.source}, 'read')])
            return ModelResponse(parts=[TextPart('Correction : ' + self.source)])
        agent = Agent(FunctionModel(model), tools=[self.backend.search_web,self.backend.read_webpage], retries=2)
        agent.output_validator(self.backend.validate_task_output)
        with patch.object(self.backend, 'fetch_web_document', side_effect=[(self.rss,'utf-8'),(b'<p>Evidence</p>','utf-8')]) as fetch:
            result = agent.run_sync('Tu es sûr ?')
        self.assertEqual(result.output, 'Correction : ' + self.source)
        self.assertEqual(count, 4)
        self.assertEqual(fetch.call_count, 2)


if __name__ == '__main__':
    unittest.main()
