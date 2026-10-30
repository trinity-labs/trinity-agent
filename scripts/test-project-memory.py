"""Offline checks for Markdown authority, local indexes and explicit aggregation."""
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from trinity_agent.cache import CACHE
from trinity_agent.memory import ConversationMemory
from trinity_agent.workspace import WorkspaceContext
from trinity_agent.project_memory import ProjectMemory, aggregate, notes, markdown_note

class MarkdownMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.data=self.root/'data';self.work=self.root/'work';self.work.mkdir()
        env=patch.dict(os.environ,{'TRINITY_AGENT_MEMORY_INDEX_DIR':str(self.root/'cache')});env.start();self.addCleanup(env.stop)
        CACHE.clear();self.ctx=WorkspaceContext(self.data,self.work)
        self.folder=self.ctx.archive/'old';self.folder.mkdir(parents=True)
        self.memory=ProjectMemory(self.data,self.ctx.id,self.work)
    def remember(self,text='Décision PostgreSQL choisie pour les transactions'):
        return self.ctx.remember('old',text,'project','declared','demande utilisateur')

    def test_slow_mount_keeps_complete_partial_paths_without_rewalking(self):
        source=self.work/'known.md';source.write_text('Observed local source')
        with patch('trinity_agent.project_memory.subprocess.run',side_effect=subprocess.TimeoutExpired(
                ['rg'],2,output=b'known.md\ntruncated')), patch('trinity_agent.project_memory.os.walk') as walk:
            self.assertEqual(self.memory.root_documents(refresh=True),[source])
            walk.assert_not_called()
    def test_registered_empty_project_does_not_abort_export(self):
        empty='a'*20
        (self.data/'projects.json').write_text(json.dumps([{'id':empty}]))
        result=aggregate(self.data,[self.ctx.id,empty])
        self.assertIn('Aucune mémoire ni conversation',Path(result['file']).read_text())
        self.assertTrue(result['projects'][-1]['empty'])
        with self.assertRaisesRegex(ValueError,'Projet inconnu'):
            aggregate(self.data,['b'*20])

    def test_evaluation_archives_are_not_project_knowledge(self):
        self.remember('Benchmark deliberately contains a wrong answer')
        (self.folder/'meta.json').write_text(json.dumps({'evaluation':True}))
        self.assertFalse(any(row[1]=='old' for row in self.memory.sources()))
        (self.folder/'meta.json').write_text(json.dumps({'evaluation':False}))
        self.assertTrue(any(row[1]=='old' for row in self.memory.sources()))
    def test_notes_are_markdown_sources_with_provenance(self):
        note=self.remember();file=self.folder/'memory/notes'/(note['id']+'.md')
        self.assertIn('scope: "project"',file.read_text());self.assertEqual(file.stat().st_mode&0o777,0o600)
        self.assertFalse((self.folder/'shared-notes.json').exists())
        self.assertIn('PostgreSQL',self.ctx.read('old','note-'+note['id']))
    def test_legacy_import_preserves_edits_and_does_not_resurrect_deleted_notes(self):
        note=self.remember();note['text']='Ancien contenu';directory=self.folder/'memory/notes'
        shutil.rmtree(directory);(self.folder/'shared-notes.json').write_text(json.dumps([note]))
        self.assertEqual(notes(self.folder)[0]['text'],'Ancien contenu')
        file=directory/(note['id']+'.md');file.write_text(file.read_text().replace('Ancien contenu','Nouvelle décision'))
        self.assertEqual(notes(self.folder)[0]['text'],'Nouvelle décision')
        file.unlink();self.assertEqual(notes(self.folder),[])
    def test_manual_notes_survive_exports_and_are_searchable(self):
        self.memory.initialise();file=self.memory.root/'NOTES.md';file.write_text('Architecture Orchidée : stockage PostgreSQL.')
        report=self.memory.materialize();self.assertIn('Orchidée',Path(report['memory']).read_text())
        self.assertEqual(file.read_text(),'Architecture Orchidée : stockage PostgreSQL.')
        self.assertIn('Orchidée',self.memory.search('Orchidée'))
        self.assertIn('Orchidée',self.ctx.read('project','notes'))
    def test_warm_lookup_does_not_scan_sources_or_read_document_bodies(self):
        ref=ConversationMemory(self.folder).put('Architecture zéphyr PostgreSQL.');self.memory.index()
        with patch('trinity_agent.project_memory.SCAN_SECONDS',10**10):
            self.memory.index()
            with patch.object(self.memory,'sources',side_effect=AssertionError('No source scan on warm hit')):
                self.assertIn(ref,self.memory.search('zéphyr'))
    def test_disk_index_reuses_unchanged_bodies_after_ram_clear(self):
        ConversationMemory(self.folder).put('Quartz architecture PostgreSQL');self.memory.index();CACHE.clear()
        from trinity_agent.project_memory import read_text_cached
        def guarded(file,*args,**kwargs):
            if Path(file).suffix=='.md':raise AssertionError('Unchanged Markdown should not be reread')
            return read_text_cached(file,*args,**kwargs)
        with patch('trinity_agent.project_memory.read_text_cached',side_effect=guarded):
            self.assertIn('Quartz',self.memory.search('Quartz'))
    def test_new_documents_invalidate_warm_cache(self):
        self.memory.index();ref=ConversationMemory(self.folder).put('Nouveau choix magnolia')
        self.assertIn(ref,self.memory.search('magnolia'))
    def test_deleted_source_cannot_be_returned_from_warm_cache(self):
        ref=ConversationMemory(self.folder).put('Contenu sensible eucalyptus');self.memory.index()
        (self.folder/'memory/documents'/(ref+'.md')).unlink()
        self.assertEqual(self.memory.search('eucalyptus'),'')
    def test_manual_edit_excludes_stale_excerpt_then_rescan_finds_new_text(self):
        self.memory.initialise();file=self.memory.root/'NOTES.md';file.write_text('Contrat acacia');self.memory.index()
        file.write_text('Contrat baobab');self.assertNotIn('acacia',self.memory.search('acacia'))
        self.memory.index(refresh=True);self.assertIn('baobab',self.memory.search('baobab'))
    def test_corrupt_disk_index_is_rebuilt(self):
        ConversationMemory(self.folder).put('Référence jasmin');self.memory.index();CACHE.clear()
        self.memory.index_file.write_bytes(b'broken gzip')
        self.assertIn('jasmin',self.memory.search('jasmin'))
    def test_aggregation_only_contains_selected_projects_and_preserves_links(self):
        self.remember('Choix azalée [doc officielle](https://example.org/doc)')
        other=WorkspaceContext(self.data,self.root/'other');(other.archive/'other').mkdir(parents=True)
        other.remember('other','Décision camélia','project','declared','user')
        exported=aggregate(self.data,[self.ctx.id]);text=Path(exported['file']).read_text()
        self.assertIn('azalée',text);self.assertNotIn('camélia',text);self.assertIn('](https://example.org/doc)',text)
        self.assertIn('projects/'+self.ctx.id+'/',text)
        text=Path(aggregate(self.data,[self.ctx.id,other.id])['file']).read_text();self.assertIn('camélia',text)
        self.assertNotIn('camélia',self.memory.search('camélia'))
    def test_symlink_notes_and_parent_escape_are_rejected(self):
        self.memory.initialise();file=self.memory.root/'NOTES.md';file.unlink();file.symlink_to(self.root/'outside')
        with self.assertRaises(ValueError):self.memory.read('project','notes')
        file.unlink();self.remember();shutil.rmtree(self.folder/'memory');(self.folder/'memory').symlink_to(self.root)
        with self.assertRaises(ValueError):self.memory.read('old','note-'+'a'*20)
        self.assertEqual(self.memory.search('PostgreSQL'),'')
    def test_deletion_during_index_build_does_not_publish_stale_index(self):
        ConversationMemory(self.folder).put('Secret primevère');original=self.memory.sources
        def changed():
            yield from original();shutil.rmtree(self.folder);self.memory.changed()
        with patch.object(self.memory,'sources',side_effect=changed): result=self.memory.index()
        self.assertTrue(result['partial']);self.assertFalse(self.memory.index_file.exists())
        self.assertEqual(self.memory.search('primevère'),'')
    def test_aggregation_rejects_concurrent_source_change(self):
        self.remember();original=self.memory.__class__.materialize
        def changed(instance):
            report=original(instance);instance.changed();return report
        with patch.object(ProjectMemory,'materialize',changed),self.assertRaises(RuntimeError):aggregate(self.data,[self.ctx.id])
        self.assertFalse((self.data/'memory/AGGREGATE.md').exists())
    def test_partial_documents_are_explicit_and_full_source_is_preserved(self):
        ref=ConversationMemory(self.folder).put('alpha '*12000)
        with patch.dict(os.environ,{'TRINITY_AGENT_MEMORY_DOCUMENT_CHARS':'64000'}):
            report=self.memory.materialize();self.assertTrue(report['partial'])
        self.assertEqual((self.folder/'memory/documents'/(ref+'.md')).stat().st_size,72000)
    def test_long_archive_is_searchable_beyond_its_first_64k_characters(self):
        ref=ConversationMemory(self.folder).put(('filler '*12000)+'Décision finale hortensia')
        self.assertIn(ref,self.memory.search('hortensia'))
        self.assertIn('hortensia',self.memory.search('hortensia'))

    def test_zero_disk_cache_keeps_retrieval_available(self):
        ConversationMemory(self.folder).put('Décision lilas')
        with patch.dict(os.environ,{'TRINITY_AGENT_INDEX_CACHE_MB':'0'}):
            self.assertIn('lilas',self.memory.search('lilas'))
            self.assertFalse(self.memory.index_file.exists())

    def test_valid_json_with_invalid_schema_is_rebuilt(self):
        ConversationMemory(self.folder).put('Décision iris');self.memory.index();CACHE.clear()
        self.memory.index_file.write_bytes(gzip.compress(b'[]'))
        self.assertIn('iris',self.memory.search('iris'))

    def test_generated_views_are_invalidated_without_deleting_manual_notes(self):
        self.remember();aggregate(self.data,[self.ctx.id]);self.memory.invalidate()
        self.assertTrue((self.memory.root/'NOTES.md').exists())
        for file in [self.memory.root/'MEMORY.md',self.memory.root/'INDEX.md',self.memory.index_file,self.data/'memory/AGGREGATE.md']:
            self.assertFalse(file.exists())

    def test_root_markdown_is_sourced_searchable_editable_and_isolated(self):
        file=self.work/'codex.md';file.write_text('Architecture genévrier : contrôler les transactions.')
        other=self.work/'escape.md';other.symlink_to(self.root/'outside.md');(self.root/'outside.md').write_text('Source externe interdite')
        found=self.memory.search('genévrier');self.assertIn(str(file),found)
        reference='root-'+__import__('hashlib').sha256(file.name.encode()).hexdigest()[:20]
        self.assertIn('genévrier',self.ctx.read('project',reference))
        report=self.memory.materialize();text=Path(report['memory']).read_text()
        self.assertIn(file.as_uri(),text);self.assertNotIn('Source externe interdite',text)
        self.assertIn(file.as_uri(),Path(aggregate(self.data,[self.ctx.id])['file']).read_text())
        file.write_text('Nouvelle architecture sapin');self.assertNotIn('genévrier',self.memory.search('genévrier'))
        self.memory.index(refresh=True);self.assertIn('sapin',self.memory.search('sapin'))
        file.unlink();self.assertNotIn('sapin',self.memory.search('sapin'))

    def test_nested_project_markdown_is_automatic_memory_but_dependencies_are_excluded(self):
        docs=self.work/'docs'/'architecture';docs.mkdir(parents=True)
        source=docs/'OVERVIEW.md';source.write_text('Topologie amarante documentée pour ce projet.')
        dependency=self.work/'node_modules'/'package';dependency.mkdir(parents=True)
        (dependency/'README.md').write_text('Secret dépendance vermillon')
        result=self.ctx.search('topologie amarante',budget=3000,automatic=True)
        self.assertIn('docs/architecture/OVERVIEW.md',result)
        reference=self.memory.document_reference(source)
        self.assertIn('amarante',self.ctx.read('project',reference))
        self.assertNotIn('vermillon',self.ctx.search('vermillon',budget=3000,automatic=True))

    def test_instruction_exclusion_keeps_index_and_distinct_sources_intact(self):
        instruction = self.work/'AGENTS.md'; instruction.write_text('Cobalt instruction exact text.')
        documentation = self.work/'DESIGN.md'; documentation.write_text(instruction.read_text())
        alias = self.root/'instruction-alias'; alias.symlink_to(instruction)
        # Equal content does not imply equal provenance. Canonical path exclusion
        # must remove only the source already supplied as instructions.
        result = self.ctx.search('cobalt instruction', automatic=True, exclude_paths={alias})
        self.assertNotIn(str(instruction), result)
        self.assertIn(str(documentation), result)
        self.assertIn('statut=current_source', result)
        self.assertIn('type=project-document', result)
        self.assertIn(str(instruction), self.ctx.search('cobalt instruction'))
        self.assertIn('Cobalt', self.ctx.read('project', self.memory.document_reference(instruction)))
        self.assertTrue(all(row['status']=='current_source' for row in self.memory.index()['rows'] if row['kind']=='project-document'))
        documentation.write_text('Cobalt updated source, not an old conversation.')
        self.assertIn('updated source', self.ctx.search('cobalt', automatic=True, exclude_paths={alias}))

    def test_project_markdown_falls_back_when_ripgrep_is_unavailable(self):
        source=self.work/'DESCRIPTION.md';source.write_text('Contrat indigo du projet.')
        dependency=self.work/'node_modules'/'package';dependency.mkdir(parents=True)
        (dependency/'README.md').write_text('Secret dépendance écarlate')
        with patch('trinity_agent.project_memory.subprocess.run',side_effect=FileNotFoundError):
            documents=self.memory.root_documents(refresh=True)
        self.assertIn(source,documents)
        self.assertFalse(any('node_modules' in str(file) for file in documents))

    def test_automatic_memory_keeps_project_markdown_and_excludes_assistant_prose(self):
        (self.work/'DESCRIPTION.md').write_text('Contrat cobalt du projet.')
        ConversationMemory(self.folder).put('response: ancien contrat cobalt inventé par assistant')
        result=self.ctx.search('contrat cobalt',budget=3000,automatic=True)
        self.assertIn('DESCRIPTION.md',result)
        self.assertNotIn('inventé par assistant',result)

    def test_verification_history_uses_receipts_and_remains_historical(self):
        ConversationMemory(self.folder).put(json.dumps({'type':'verification_check','check':'Transactions','passed':False,'exit_code':1,'timestamp':'2026-10-17T19:00:00Z'}))
        ConversationMemory(self.folder).put('response: Tous les tests réussissent')
        report=self.memory.materialize();text=Path(report['regressions']).read_text()
        self.assertIn('échoué',text);self.assertIn('2026-10-17',text);self.assertIn('Code de sortie : 1',text)
        self.assertNotIn('Tous les tests réussissent',text)
        self.assertIn('verification_check',self.memory.search('régressions'))
        self.assertIn('échoué',self.ctx.read('project','regressions'))

    def test_automatic_recall_accepts_executed_failure_but_not_claims_or_refusals(self):
        memory=ConversationMemory(self.folder)
        event={'type':'verification_check','check':'logo CSS',
            'passed':False,'exit_code':1,'timestamp':'2026-10-20T19:00:00Z'}
        (self.folder/'events.jsonl').write_text(json.dumps(event)+'\n')
        failed=memory.put(json.dumps(event))
        for invalid in ({'passed':True,'exit_code':1}, {'passed':True,'exit_code':True},
                        {'passed':False,'exit_code':None}, {'passed':True,'exit_code':0,'executed':False}):
            memory.put(json.dumps({'type':'verification_check','check':'logo invalid',
                'timestamp':'2026-10-20T19:00:00Z',**invalid}))
        memory.put('response: Tous les contrôles du logo réussissent à 36px.')
        self.memory.initialise()
        (self.memory.root/'NOTES.md').write_text('Le logo est à 36px selon une ancienne réponse.')
        result=self.memory.search('logo',automatic=True)
        self.assertIn(failed,result);self.assertNotIn('invalid',result);self.assertNotIn('36px',result)
        self.assertIn('36px',self.memory.search('logo 36px'))

    def test_exact_documented_question_is_answered_locally_and_updated(self):
        question='Comment lancer les tests du projet ?'
        file=self.work/'SOLUTIONS.md'
        file.write_text('# Solutions\n\n## '+question+'\n\nUtiliser `python3 -m unittest`.\n\n## Autre rubrique\nIgnorer.\n')
        result=self.ctx.answer(question)
        self.assertEqual(result['answer'],'Utiliser `python3 -m unittest`.')
        self.assertEqual(result['source'],str(file));self.assertIn('aucune nouvelle vérification',result['text'])
        file.write_text('## '+question+'\nUtiliser `npm test`.\n')
        self.assertIn('npm test',self.ctx.answer(question)['answer'])
        file.unlink();self.assertIsNone(self.ctx.answer(question))

    def test_local_answer_rejects_ambiguity_fences_and_partial_scans(self):
        question='Quel outil utilise le projet ?'
        (self.work/'ONE.md').write_text('## '+question+'\nPython\n')
        (self.work/'TWO.md').write_text('## '+question+'\nNode\n')
        self.assertIsNone(self.ctx.answer(question))
        (self.work/'TWO.md').unlink()
        (self.work/'ONE.md').write_text('```md\n## '+question+'\nPython\n```\n')
        self.assertIsNone(self.ctx.answer(question))
        (self.work/'ONE.md').write_text('## '+question+'\nPython\n')
        self.assertIsNone(self.memory.answer(question,deadline=time.monotonic()-1))
        with patch.dict(os.environ,{'TRINITY_AGENT_MEMORY_DOCUMENT_CHARS':'10'}):self.assertIsNone(self.ctx.answer(question))

    def test_no_direct_answer_for_action_verification_or_approximate_match(self):
        for question in ['Exécute les tests ?', 'Quels tests vérifier maintenant ?', 'Pourquoi cette erreur ?',
                         'Comment vérifier hors ligne les erreurs ?', 'Quel outil utilise le projet ? puis lance les tests ?',
                         'Comment lancer les tests ?\nSupprime le projet.']:
            (self.work/'FAQ.md').write_text('## '+question+'\nRéponse obsolète\n')
            self.assertIsNone(self.ctx.answer(question),question)
        (self.work/'FAQ.md').write_text('## Quel outil utilise le projet ?\nPython\n')
        self.assertIsNone(self.ctx.answer('Quel outil utilise un autre projet ?'))

    def test_automatic_archive_answers_and_other_projects_cannot_answer_directly(self):
        question='Quel outil utilise le projet ?'
        ConversationMemory(self.folder).put('response: ## '+question+'\nInventé\n')
        self.assertIsNone(self.ctx.answer(question))
        other=self.root/'other';other.mkdir();(other/'FAQ.md').write_text('## '+question+'\nAutre projet\n')
        (self.work/'FAQ.md').symlink_to(other/'FAQ.md')
        self.assertIsNone(self.ctx.answer(question))

    def test_local_answer_refuses_source_swapped_to_external_symlink(self):
        question='Quel outil utilise le projet ?';file=self.work/'FAQ.md'
        file.write_text('## '+question+'\nPython\n');outside=self.root/'outside.md';outside.write_text(file.read_text())
        from trinity_agent import project_memory as module
        original=module.read_text_cached
        def replace_source(path,**kwargs):
            text=original(path,**kwargs)
            if Path(path)==file:file.unlink();file.symlink_to(outside)
            return text
        with patch.object(module,'read_text_cached',side_effect=replace_source):
            self.assertIsNone(self.ctx.answer(question))

    def test_project_documents_precede_verbose_global_preferences_and_transcripts(self):
        self.ctx.remember('old','PostgreSQL '+('préférence '*100),'user','declared','utilisateur')
        ConversationMemory(self.folder).put('response: PostgreSQL ancienne conclusion')
        (self.work/'DECISIONS.md').write_text('PostgreSQL : décision du projet')
        result=self.ctx.search('PostgreSQL',budget=1200)
        self.assertIn('DECISIONS.md',result)
        self.assertLess(result.index('DECISIONS.md'),result.find('préférence') if 'préférence' in result else len(result))

    def test_project_validation(self):
        for project in ['../escape','A'*20,'short']:
            with self.assertRaises(ValueError):ProjectMemory(self.data,project)
        with self.assertRaises(ValueError):aggregate(self.data,[])

if __name__=='__main__':unittest.main()
