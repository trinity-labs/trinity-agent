"""Offline tests for scoped retrieval, environment discovery and installation policy."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ovh_workspace import WorkspaceContext,inventory,install_plan
from ovh_memory import ConversationMemory

class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.work=self.root/'work';self.work.mkdir()
        cache=patch.dict('os.environ',{'OVH_AGENT_MEMORY_INDEX_DIR':str(self.root/'cache')});cache.start();self.addCleanup(cache.stop)
        self.data=self.root/'data';self.ctx=WorkspaceContext(self.data,self.work)
        self.conv=self.ctx.archive/'original';self.conv.mkdir(parents=True)
        self.other=WorkspaceContext(self.data,self.root/'other')
        (self.other.archive/'other-conversation').mkdir(parents=True)
    def test_shared_project_history_is_retrieved_with_exact_source(self):
        ref=ConversationMemory(self.conv).put('Architecture alpha utilise PostgreSQL pour ses transactions.')
        found=self.ctx.search('Architecture PostgreSQL',current='new')
        self.assertIn('original',found);self.assertIn(ref,found)
        self.assertIn('PostgreSQL',self.ctx.read('original',ref))
        self.assertNotIn(ref,self.other.search('Architecture PostgreSQL'))
    def test_paged_search_reaches_older_conversations(self):
        import os
        ref=ConversationMemory(self.conv).put('Ancienne décision architecture PostgreSQL')
        os.utime(self.conv,(1,1))
        for index in range(35):(self.ctx.archive/f'recent-{index}').mkdir()
        self.assertIn(ref,self.ctx.search('PostgreSQL'))
        first=self.ctx.search_page('PostgreSQL')
        second=self.ctx.search_page('PostgreSQL',first['next_offset'])
        self.assertIn(ref,json.dumps(second));self.assertIsNone(second['next_offset'])
    def test_notes_scope_provenance_and_deletion(self):
        note=self.ctx.remember('original','Préférence interface française','user','declared','Message utilisateur initial')
        self.ctx.remember('original','Architecture privée PostgreSQL','project','hypothesis','Proposition non vérifiée')
        found=self.other.search('interface française PostgreSQL')
        self.assertIn('française',found);self.assertIn('declared',found);self.assertNotIn('PostgreSQL',found)
        self.assertIn('française',self.other.search('Réparer compilation TypeScript'))
        self.assertEqual(note['conversation'],'original')
        shutil.rmtree(self.conv)
        self.assertEqual(self.other.search('interface française'),'')
    def test_evaluations_never_enter_paged_or_cross_project_memory(self):
        reference=ConversationMemory(self.conv).put('EvaluationFixtureWrongAnswer')
        self.ctx.remember('original','EvaluationFixturePreference','user','declared','fixture')
        # Warm project caches before changing the metadata.
        self.assertIn(reference,self.ctx.search('EvaluationFixtureWrongAnswer'))
        metadata=self.conv/'meta.json'
        metadata.write_text(json.dumps({'evaluation':True}))
        self.assertNotIn(reference,self.ctx.search('EvaluationFixtureWrongAnswer'))
        self.assertNotIn(reference,json.dumps(self.ctx.search_page('EvaluationFixtureWrongAnswer')))
        self.assertNotIn('EvaluationFixturePreference',self.other.search('unrelated request'))
        self.assertNotIn(self.conv,self.ctx.folders(all_projects=True))
        # Explicit archival reading remains available for evaluation inspection.
        self.assertIn('EvaluationFixtureWrongAnswer',self.ctx.read('original',reference))
        metadata.write_text('{broken')
        self.assertNotIn(self.conv,self.ctx.folders())
        metadata.unlink()
        self.assertIn(self.conv,self.ctx.folders())
    def test_model_cannot_mark_memory_verified_or_escape_conversation(self):
        for conv,status in [('original','verified'),('../outside','declared')]:
            with self.assertRaises(ValueError):self.ctx.remember(conv,'some fact','project',status,'source')
        with self.assertRaises(ValueError):self.ctx.read('../outside','a'*64)
    def test_memory_symlinks_and_budget(self):
        external=self.root/'external';external.mkdir()
        (self.ctx.archive/'link').symlink_to(external,target_is_directory=True)
        self.assertNotIn(self.ctx.archive/'link',self.ctx.folders())
        self.ctx.remember('original','Architecture '+('alpha '*400)[:1900],'project','declared','user')
        self.assertLessEqual(len(self.ctx.search('alpha',budget=300)),300)
        self.assertEqual(self.ctx.search('alpha',budget=0),'')
    def test_profile_is_created_once_and_user_content_preserved(self):
        self.assertIn('Contexte utilisateur',self.ctx.profile())
        p=self.data/'MASTER.md';p.write_text('Répondre en français.')
        self.assertEqual(self.ctx.profile(),'Répondre en français.')
    def test_environment_cache_refresh_and_no_environment_secrets(self):
        with patch.dict('os.environ',{'OVH_AI_API_KEY':'never-include-this','OVH_AGENT_INVENTORY_TTL_SECONDS':'300'}):
            first=inventory(self.work,self.data)
            with patch('ovh_workspace.shutil.which',side_effect=AssertionError('cache should be used')):
                self.assertEqual(first,inventory(self.work,self.data))
            with patch('ovh_workspace.shutil.which',return_value='/mock/tool'):
                refreshed=inventory(self.work,self.data,True)
            self.assertEqual(refreshed['tools']['rg'],'/mock/tool')
            self.assertNotIn('never-include-this',json.dumps(first))
            self.assertIn('not mounted hosts',first['target'])
    def test_install_plans_do_not_use_shell_or_modify_project_manifest(self):
        commands=install_plan(self.work,'python',['pytest==8.3.5'])
        self.assertIn('venv',commands[0]);self.assertIn(str(self.work/'.agent-tools/venv'),commands[0])
        self.assertEqual(commands[-1][-2:],['--','pytest==8.3.5'])
        with patch('ovh_workspace.shutil.which',return_value='/usr/bin/npm'):
            cmd=install_plan(self.work,'npm',['typescript'])[0]
            self.assertIn('--ignore-scripts',cmd);self.assertIn(str(self.work/'.agent-tools/npm'),cmd)
        for package in ['--help','a;reboot','https://evil.test/pkg','../pkg','$(echo bad)']:
            with self.assertRaises(ValueError):install_plan(self.work,'python',[package])
    def test_install_refuses_symlink_tool_directory(self):
        (self.work/'.agent-tools').symlink_to(self.root)
        with self.assertRaises(ValueError):install_plan(self.work,'python',['pytest'])
    def test_project_tools_are_available_to_verification_commands(self):
        from ovh_verification import run_check
        binary=self.work/'.agent-tools/npm/node_modules/.bin/project-check'
        binary.parent.mkdir(parents=True)
        binary.write_text('#!/bin/sh\nprintf "project tool reached\\n"\n');binary.chmod(0o700)
        result=run_check(['project-check'],self.work)
        self.assertTrue(result['passed']);self.assertIn('project tool reached',result['output'])
    def test_dependency_changes_require_new_verification(self):
        from ovh_verification import ChangeGuard
        import shlex
        (self.work/'check.py').write_text('print("passed")\n')
        (self.work/'.ovh-agent-checks.json').write_text(json.dumps({'version':1,'checks':[{'name':'check','argv':['{python}','check.py'],'paths':['*.py'],'protect':['check.py']}]}))
        guard=ChangeGuard(self.work,self.root/'receipts',require_checks=True);guard.observe(self.work)
        guard.execute_configured(shlex.join([sys.executable,'check.py']),self.work)
        self.assertEqual(guard.verify()['status'],'passed');self.assertEqual(len(guard.state['receipts']),1)
        guard.invalidate_environment()
        self.assertEqual(guard.test_summary()['checks'][0]['status'],'stale')
        self.assertEqual(guard.verify()['status'],'passed');self.assertEqual(len(guard.state['receipts']),2)
    def test_single_slow_memory_reader_does_not_block_subsequent_requests(self):
        import threading
        wait=threading.Event()
        with patch.object(WorkspaceContext,'_search',side_effect=lambda *args: wait.wait(3) or ''):
            start=time.monotonic();self.assertIn('incomplète',self.ctx.search('architecture'))
            self.assertLess(time.monotonic()-start,1.5)
            start=time.monotonic();self.assertIn('incomplète',self.ctx.search('architecture'))
            self.assertLess(time.monotonic()-start,.2)
            wait.set()
        import ovh_workspace
        ovh_workspace._reader.join(1)

class BackendTools(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(sys,'argv',['test','--billing-status']):
            spec=importlib.util.spec_from_file_location('workspace_agent',ROOT/'ovh-agent.py')
            cls.a=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.a)
    def test_auto_project_policy_uses_real_receipts_and_refreshes_inventory(self):
        a=self.a
        with tempfile.TemporaryDirectory() as folder, patch.object(a,'WORKSPACE',Path(folder)), patch.object(a,'DATA_ROOT',Path(folder)/'data'), patch.dict('os.environ',{'OVH_AGENT_AUTO_INSTALL':'project'}), patch.object(a,'request_approval',side_effect=AssertionError('automatic project install')), patch.object(a,'run_check',return_value={'passed':True,'exit_code':0,'output':'installed','duration_ms':1}) as run, patch.object(a,'activity_start',return_value='id'), patch.object(a,'activity_end'):
            result=json.loads(a.install_tools('python',['pytest']))
            self.assertEqual(len(result['receipts']),2);self.assertEqual(run.call_count,2)
            self.assertEqual(result['environment']['status'],'observed')
    def test_system_install_requires_approval_under_project_policy(self):
        a=self.a
        with tempfile.TemporaryDirectory() as folder, patch.object(a,'WORKSPACE',Path(folder)), patch.dict('os.environ',{'OVH_AGENT_AUTO_INSTALL':'project'}), patch.object(a,'install_plan',return_value=[['apk','add','git']]), patch.object(a,'request_approval',return_value=False) as approval, patch.object(a,'run_check') as run:
            self.assertEqual(a.install_tools('system',['git'],'system'),'Installation refusée.')
            approval.assert_called_once();run.assert_not_called()
    def test_context_loader_includes_profile_project_and_inventory(self):
        a=self.a
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'AGENTS.md').write_text('Projet exemple uniquement.')
            with patch.object(a,'WORKSPACE',root),patch.object(a,'ALLOWED_ROOT',root),patch.object(a,'DATA_ROOT',root/'data'):
                context=a.load_infrastructure_instructions()
                self.assertIn('Projet exemple uniquement',context);self.assertIn('Profil utilisateur partagé',context);self.assertIn('architecture',context)
    def test_normal_commands_use_project_tools(self):
        a=self.a
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);binary=root/'.agent-tools/npm/node_modules/.bin/agent-local-check'
            binary.parent.mkdir(parents=True);binary.write_text('#!/bin/sh\necho project-command-ok\n');binary.chmod(0o700)
            with patch.object(a,'WORKSPACE',root),patch.object(a,'ALLOWED_ROOT',root),patch.object(a,'VERIFICATION_GUARD',None),patch.object(a,'request_approval',return_value=True),patch.object(a,'activity_start',return_value='id'),patch.object(a,'activity_end'):
                result=a.run_command('agent-local-check')
                evidence=json.loads(result)
                self.assertIn('project-command-ok',evidence['output']);self.assertEqual(evidence['exit_code'],0)
                self.assertTrue(evidence['passed']);self.assertTrue(evidence['executed'])
    def test_backend_evidence_is_shared_with_time_and_limited_scope(self):
        a=self.a
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);context=WorkspaceContext(root/'data',root)
            with patch.object(a,'WORKSPACE',root),patch.object(a,'ARCHIVE_ROOT',context.archive),patch.object(a,'CONVERSATION_ID','evidence'),patch.object(a,'update_metadata'):
                a.archive_event({'type':'verification_check','check':'Transactions PostgreSQL','passed':True,'exit_code':0,'phase':'after'})
            result=context.search('Transactions PostgreSQL',current='new')
            self.assertIn('verified_at_execution',result);self.assertIn('timestamp',result);self.assertIn('recheck current files',result)

if __name__=='__main__':unittest.main()
