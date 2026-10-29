"""Discovery preserves capabilities while reducing actual model schemas."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ovh_tools import ToolSelection, CORE
from pydantic_ai import Agent, Tool
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.tools import ToolDefinition
from ovh_execution import WorkflowController


class Discovery(unittest.TestCase):
    def test_sdk_runs_build_on_short_delivery_followup_without_discovery(self):
        import tempfile
        import subprocess
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            (workspace / 'source.txt').write_text('existing change')
            (workspace / 'build.py').write_text(
                "from pathlib import Path\nPath('artifact.txt').write_text(Path('source.txt').read_text())\n")
            for prompt in ('ok install et publie', 'ok build', 'oui continue', 'install and publish'):
                with self.subTest(prompt=prompt):
                    selection = ToolSelection(prompt)
                    self.assertTrue(selection.visible('verification_plan'))
                    self.assertTrue(selection.visible('verify_changes'))
                    async def prepare(ctx, definition):
                        selection.register(definition.name, definition.description)
                        return definition if selection.visible(definition.name) else None
                    calls = []
                    def run_command(command: str) -> str:
                        """Build a local fixture."""
                        self.assertEqual(command, 'build')
                        calls.append(command)
                        result = subprocess.run([sys.executable, 'build.py'], cwd=workspace,
                                                capture_output=True, text=True, check=True)
                        return result.stdout or 'exit=0'
                    def model(messages, info):
                        self.assertIn('run_command', {tool.name for tool in info.function_tools})
                        if not calls:
                            return ModelResponse(parts=[ToolCallPart('run_command', {'command': 'build'})])
                        return ModelResponse(parts=[TextPart('Build complete.')])
                    result = Agent(FunctionModel(model), tools=[Tool(run_command, prepare=prepare)]).run_sync(prompt)
                    self.assertEqual(result.output, 'Build complete.')
                    self.assertEqual((workspace / 'artifact.txt').read_text(), 'existing change')
                    self.assertEqual(calls, ['build'])

    def test_sdk_discovers_tool_on_following_round(self):
        selection=ToolSelection('Read a file')
        observed=[]
        async def prepare(ctx,definition):
            selection.register(definition.name,definition.description)
            return definition if selection.visible(definition.name) else None
        def discover_tools(query:str)->str:
            """Activate specialist tools for the next request."""
            return str(selection.discover(query))
        def rare_capability()->str:
            """Return an independent rare observation."""
            observed.append(True)
            return 'verified fixture'
        rounds=[]
        def model(messages,info):
            names={tool.name for tool in info.function_tools};rounds.append(names)
            if len(rounds)==1:
                self.assertNotIn('rare_capability',names)
                return ModelResponse(parts=[ToolCallPart('discover_tools',{'query':'rare_capability'})])
            if len(rounds)==2:
                self.assertIn('rare_capability',names)
                return ModelResponse(parts=[ToolCallPart('rare_capability',{})])
            return ModelResponse(parts=[TextPart('done')])
        agent=Agent(FunctionModel(model),tools=[Tool(fn,prepare=prepare) for fn in (discover_tools,rare_capability)])
        self.assertEqual(agent.run_sync('Observe the rare capability').output,'done')
        self.assertEqual(observed,[True])

    def test_selection_is_per_task_and_multilingual(self):
        a=ToolSelection('CSS : opacité');b=ToolSelection('lecture simple')
        self.assertTrue(a.visible('inspect_styles'));self.assertFalse(b.visible('inspect_styles'))
        b.register('run_host_command','Exécute sur un hôte distant.')
        self.assertEqual(b.discover('ssh')['activated'][0]['name'],'run_host_command')
        self.assertFalse(ToolSelection().visible('run_host_command'))

    def test_visual_requests_expose_required_styles_without_discovery(self):
        for prompt in ('Agrandis le logo', 'Applique une opacity de 0.5',
                       'Corrige l’opacité du header', 'Change la couleur du titre'):
            with self.subTest(prompt=prompt):
                selection=ToolSelection(prompt)
                self.assertTrue(selection.visible('inspect_styles'))
                self.assertTrue(selection.visible('edit_file_batch'))
                self.assertFalse(selection.visible('run_host_command'))

    def test_explicit_tools_are_visible_even_when_registered_after_hint(self):
        selection=ToolSelection('Utilise search_web puis read_webpage.')
        self.assertTrue(selection.visible('search_web'))
        self.assertTrue(selection.visible('read_webpage'))
        self.assertFalse(ToolSelection().visible('read_webpage'))
        selection.hint('Utilise inspect_widget pour le composant.')
        selection.register('inspect_widget','Reads widget state.')
        self.assertTrue(selection.visible('inspect_widget'))
        self.assertFalse(ToolSelection().visible('inspect_widget'))

    def test_sdk_exposes_memory_on_first_round_without_prompt_hints(self):
        selection=ToolSelection('Lis un fichier.')
        async def prepare(ctx,definition):
            selection.register(definition.name,definition.description)
            return definition if selection.visible(definition.name) else None
        def search_memory(query:str)->str:
            return 'fixture'
        def read_memory(path:str)->str:
            return 'fixture'
        def model(messages,info):
            self.assertEqual({tool.name for tool in info.function_tools},{'search_memory','read_memory'})
            return ModelResponse(parts=[TextPart('done')])
        agent=Agent(FunctionModel(model),tools=[Tool(fn,prepare=prepare) for fn in (search_memory,read_memory)])
        self.assertEqual(agent.run_sync('Reprise').output,'done')

    def test_full_catalogue_remains_discoverable_and_bounded(self):
        a=ToolSelection()
        for i in range(40):a.register(f'tool_{i}','metadata '*100)
        self.assertLess(len(CORE),15)
        self.assertEqual(len(a.discover()['activated']),12)
        self.assertTrue(a.discover()['more_available'])
        self.assertEqual(a.discover('tool_39')['activated'][0]['name'],'tool_39')
        self.assertLessEqual(len(a.catalog['tool_0']),260)


class RecoveryDiscovery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        with patch.object(sys, 'argv', ['test-tools', '--billing-status']):
            spec = importlib.util.spec_from_file_location('recovery_discovery_backend', root/'ovh-agent.py')
            cls.backend = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.backend)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root/'project'
        self.workspace.mkdir()
        self.a = self.backend
        self.a.WORKSPACE = self.workspace
        self.a.run_dependencies().engine = WorkflowController(self.workspace, self.root/'execution')
        self.a.run_dependencies().tools = ToolSelection('ok installe')

    def visible(self, name):
        definition = ToolDefinition(name=name, description='Test', parameters_json_schema={})
        return asyncio.run(self.a.prepare_tool_for_request(
            SimpleNamespace(prompt='ok installe', messages=[], deps=self.a.run_dependencies()), definition)) is not None

    def configure_install(self):
        plan = dict(name='install', kind='service', scope='local',
                    apply=['apply'], verify=['verify'], rollback=['rollback'],
                    verify_rollback=['baseline'], verify_idle=['idle'])
        (self.workspace/'.ovh-agent-checks.json').write_text(json.dumps(
            {'version': 1, 'operations': {'version': 1, 'plans': [plan]}}))
        self.calls = []
        def runner(argv, cwd, timeout, **kwargs):
            self.calls.append(argv[0])
            if argv == ['apply']:
                raise TimeoutError('Installation committed, result lost')
            return {'exit_code': 1 if argv == ['baseline'] else 0}
        self.runner = runner
        self.a.run_dependencies().engine = WorkflowController(self.workspace, self.root/'execution', operation_runner=runner)

    def test_agent_uses_bounded_tool_retry_limit_above_two(self):
        a = self.a
        model = FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart('ok')]))
        model_id = 'tool-retry-fixture'
        self.addCleanup(a.AGENTS.pop, model_id, None)
        with patch.object(a, 'get_budgeted_model', return_value=model), \
                patch.object(a, 'model_capabilities', return_value={'tools': False}):
            agent = a.get_agent(model_id)
        self.assertGreaterEqual(a.TOOL_RETRIES, 5)
        self.assertEqual(agent._max_tool_retries, a.TOOL_RETRIES)

    def test_configured_install_is_callable_and_recovery_does_not_install_twice(self):
        self.configure_install()
        a = self.a
        rounds = []
        def model(messages, info):
            rounds.append(1)
            names = {tool.name for tool in info.function_tools}
            self.assertTrue({'execute_operation', 'reconcile_operation', 'workflow_status'} <= names)
            schema = next(tool for tool in info.function_tools if tool.name == 'execute_operation')
            self.assertEqual(schema.parameters_json_schema['properties']['name']['enum'], ['install'])
            if len(rounds) == 1:
                return ModelResponse(parts=[ToolCallPart('execute_operation', {'name': 'install'})])
            if len(rounds) == 2:
                self.assertTrue(a.run_dependencies().engine.transactions.snapshot()['unresolved_mutations'])
                return ModelResponse(parts=[ToolCallPart('execute_operation', {'name': 'install'})])
            if len(rounds) == 3:
                unresolved = a.run_dependencies().engine.transactions.snapshot()['unresolved_mutations']
                self.assertTrue(unresolved)
                self.assertEqual(self.calls.count('apply'), 1)
                return ModelResponse(parts=[ToolCallPart('reconcile_operation', {'operation_id': unresolved[0]})])
            if len(rounds) == 4:
                self.assertFalse(a.run_dependencies().engine.transactions.snapshot()['unresolved_mutations'])
                return ModelResponse(parts=[ToolCallPart('execute_operation', {'name': 'install'})])
            return ModelResponse(parts=[TextPart('Verified')])
        tools = [tool for tool in a.agent_tools() if tool.name in
                 {'workflow_status', 'execute_operation', 'reconcile_operation'}]
        from pydantic_ai.toolsets import FunctionToolset
        agent = Agent(FunctionModel(model), deps_type=a.RunDependencies,
            toolsets=[FunctionToolset(tools).prepared(a.prepare_tools_for_request)],
            capabilities=[a.ExecutionCapability(a.RUN_CONTEXT)])
        self.assertEqual(agent.run_sync('ok installe', deps=a.run_dependencies()).output, 'Verified')
        self.assertEqual(self.calls.count('apply'), 1)
        self.assertIn('idle', self.calls)
        self.assertFalse(a.run_dependencies().engine.transactions.snapshot()['unresolved_mutations'])

    def test_pending_effects_remain_visible_after_restart_and_block_fallback(self):
        self.configure_install()
        self.a.run_dependencies().engine.operations.execute('install')
        self.a.run_dependencies().engine = WorkflowController(self.workspace, self.root/'execution', operation_runner=self.runner)
        self.a.run_dependencies().tools = ToolSelection('continue')
        self.assertTrue(self.visible('reconcile_operation'))
        failure = RuntimeError("Tool 'edit_file' exceeded max retries count of 2")
        with patch.object(self.a, 'AI_PROVIDER', 'ovhcloud'):
            self.assertFalse(self.a.automatic_fallback_reason('gpt-oss-20b', failure, self.a.run_dependencies().engine))
            identifier = self.a.run_dependencies().engine.transactions.snapshot()['unresolved_mutations'][0]
            self.a.run_dependencies().engine.reconcile(identifier)
            self.assertTrue(self.a.automatic_fallback_reason('gpt-oss-20b', failure, self.a.run_dependencies().engine))

    def test_unresolved_command_without_plan_does_not_advertise_execution(self):
        identifier = self.a.run_dependencies().engine.journal.start('run_command', {}, mutation=True)
        self.a.run_dependencies().engine.journal.finish(identifier, status='unknown')
        self.assertTrue(self.visible('reconcile_operation'))
        self.assertFalse(self.visible('execute_operation'))

    def test_visibility_does_not_override_read_only_or_enable_unrelated_tools(self):
        self.configure_install()
        self.a.run_dependencies().engine.focus('Lecture seule. Aucune modification.')
        self.assertFalse(self.visible('execute_operation'))
        self.assertFalse(self.visible('reconcile_operation'))
        self.assertTrue(self.visible('workflow_status'))
        self.assertTrue(self.visible('update_plan'))
        self.assertFalse(self.visible('run_host_command'))
        result = json.loads(self.a.run_dependencies().engine.invoke(self.a.execute_operation, 'install'))
        self.assertEqual(result['category'], 'read_only')
        self.assertEqual(self.calls, [])

    def test_scoped_preservation_keeps_real_write_schema_available(self):
        self.a.run_dependencies().engine.focus('Lecture seule : explique le projet.')
        self.a.run_dependencies().engine.focus('Corrige app.py. Ne modifie pas cette configuration ni les autres fichiers.')
        self.a.run_dependencies().tools=ToolSelection('Corrige app.py.')
        self.assertFalse(self.a.run_dependencies().engine.read_only)
        self.assertTrue(self.visible('edit_file'))
        self.a.run_dependencies().engine.focus('Corrige ton analyse. Ne modifie pas les fichiers.')
        self.assertFalse(self.visible('edit_file'))



class VerificationIntent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(sys,'argv',['test-tools','--billing-status']):
            spec=importlib.util.spec_from_file_location('verification_intent_backend',Path(__file__).resolve().parents[1]/'ovh-agent.py')
            cls.backend=importlib.util.module_from_spec(spec);spec.loader.exec_module(cls.backend)

    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=Path(temporary.name);workspace=root/'project';workspace.mkdir()
        (workspace/'value.py').write_text('value = 1\n')
        (workspace/'check.py').write_text('assert True\n')
        (workspace/'.ovh-agent-checks.json').write_text(json.dumps({'version':1,'checks':[
            {'name':'Fixture','paths':['value.py'],'argv':['{python}','check.py'],'protect':['check.py']}]}))
        self.a=self.backend;self.a.WORKSPACE=workspace;self.a.DATA_ROOT=root/'data'
        self.a.ARCHIVE_ROOT=root/'archive';self.a.STATE_ROOT=root/'state'
        self.a.CONVERSATION_ID='verification-intent';self.a.run_dependencies().full_access=False
        self.a.run_dependencies().engine=self.a.run_dependencies().verification=None
        self.calls=[];self.passed=True
        def runner(argv,cwd,timeout):
            self.calls.append(argv)
            return {'passed':self.passed,'exit_code':0 if self.passed else 1,'timed_out':False,
                    'output':'fixture check','duration_ms':1}
        for name,value in [('run_verified_check',runner),('emit',lambda event:None)]:
            item=patch.object(self.a,name,value);item.start();self.addCleanup(item.stop)

    def test_static_audit_does_not_implicitly_execute_project_code(self):
        self.a.begin_verification('Audit du code en lecture seule : explique value.py.')
        self.a.read_file('value.py')
        result=self.a.validate_task_output('Analyse statique seulement ; comportement non testé.')
        self.assertIn('Analyse statique',result)
        self.assertEqual(self.calls,[])
        with self.assertRaises(self.a.ModelRetry):
            self.a.validate_task_output('Tous les tests réussis.')
        self.assertEqual(self.calls,[])

    def test_explicit_verification_runs_checks_even_without_changes(self):
        self.a.begin_verification('Audite le projet en lecture seule.')
        self.a.read_file('value.py')
        report=json.loads(self.a.verify_changes())
        self.assertEqual(report['status'],'passed')
        self.assertEqual(len(self.calls),1)
        self.a.validate_task_output('Contrôles exécutés.')
        self.assertEqual(len(self.calls),1)

    def test_changed_code_still_requires_passing_postconditions(self):
        self.a.begin_verification('Corrige value.py.')
        self.a.read_file('value.py')
        self.a.edit_file('value.py','value = 1','value = 2')
        self.assertEqual(len(self.calls),1)
        self.passed=False
        with self.assertRaises(self.a.ModelRetry):self.a.validate_task_output('Terminé.')
        self.assertGreater(len(self.calls),1)
        self.assertEqual(self.a.run_dependencies().verification.state['report']['status'],'failed')

    def test_controller_incomplete_verdict_survives_final_validation(self):
        self.a.begin_verification('Corrige value.py.')
        status={'incomplete':True,'unresolved':[],'running':[],'failed_checks':[],
                'failed_files':['operation'],'criteria_status':'failed'}
        with patch.object(self.a.run_dependencies().engine,'final_status',return_value=status):
            with self.assertRaises(self.a.ModelRetry):self.a.validate_task_output('Terminé.')
            verdict=self.a.validate_task_output('Terminé.')
            self.assertTrue(verdict.startswith('Tâche incomplète.'))
            self.assertEqual(self.a.validate_task_output(verdict),verdict)


class ProgressContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        RecoveryDiscovery.setUpClass.__func__(cls)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.a = self.backend
        self.events, self.reads = [], []
        self.engine = WorkflowController(self.root, self.root/'execution')
        self.engine.focus('Inspecte les fichiers et vérifie leur contenu.')
        self.deps = self.a.RunDependencies(engine=self.engine)
        token = self.a.RUN_CONTEXT.set(self.deps)
        self.addCleanup(self.a.RUN_CONTEXT.reset, token)
        for name, value in {'WORKSPACE':self.root, 'conversation_dir':lambda *args:self.root,
                            'ensure_conversation':lambda:None, 'emit':self.observe}.items():
            patched = patch.object(self.a, name, value)
            patched.start(); self.addCleanup(patched.stop)
        (self.root/'fixture.txt').write_text('independent fixture')

    def observe(self, event):
        self.events.append(event)
        self.engine.observe_event(event)

    def agent(self, model, *, prepared=True):
        from pydantic_ai.toolsets import FunctionToolset
        def read_file(path: str) -> str:
            """Read the requested fixture file."""
            self.reads.append(path)
            self.events.append({'type':'actual_read'})
            return (self.root/path).read_text()
        toolset = FunctionToolset([Tool(self.a.update_plan, sequential=True), Tool(read_file)])
        if prepared:
            toolset = toolset.prepared(self.a.prepare_tools_for_request)
        return Agent(FunctionModel(model), deps_type=self.a.RunDependencies, toolsets=[toolset],
                     capabilities=[self.a.ExecutionCapability(self.a.RUN_CONTEXT)], retries=2)

    def test_progress_precedes_work_and_is_required_again_after_a_batch(self):
        rounds = []
        def model(messages, info):
            names = {tool.name for tool in info.function_tools}; rounds.append(names)
            if len(rounds) in (1, 3):
                self.assertIn("un point d'avancement est requis", info.instructions)
                self.assertEqual(info.instructions.count("État d'exécution actuel"), 1)
                self.assertEqual(names, {'update_plan','read_file'})
                schema = next(t for t in info.function_tools if t.name=='update_plan').parameters_json_schema
                self.assertIn('explanation', schema['required'])
                return ModelResponse(parts=[ToolCallPart('update_plan', {'steps':['Lire','Vérifier'],
                    'explanation':'Je lis la configuration.' if len(rounds)==1 else
                    'Les trois lectures concordent. Je contrôle le dernier fichier.'})])
            if len(rounds) in (2, 4):
                self.assertNotIn("État d'exécution actuel", info.instructions or '')
                self.assertIn('read_file', names)
                return ModelResponse(parts=[ToolCallPart('read_file', {'path':'fixture.txt'},
                    tool_call_id=f'read-{len(rounds)}-{i}') for i in range(3 if len(rounds)==2 else 1)])
            return ModelResponse(parts=[TextPart('{"verified":true}')])
        result = self.agent(model).run_sync('Inspecte. Termine avec un JSON.', deps=self.deps)
        self.assertEqual(result.output, '{"verified":true}')
        significant = [e['type'] for e in self.events if e['type'] in {'plan_updated','actual_read'}]
        self.assertEqual(significant, ['plan_updated', *['actual_read']*3, 'plan_updated', 'actual_read'])

    def test_hallucinated_tool_recovers_through_native_sdk_without_mutation(self):
        rounds = []
        def model(messages, info):
            rounds.append(1)
            if len(rounds)==1:
                return ModelResponse(parts=[ToolCallPart('update_plan', {'steps':['Lire'], 'explanation':'Je vérifie le fichier.'})])
            if len(rounds)==2:
                return ModelResponse(parts=[ToolCallPart('invented_apply_patch', {'path':'fixture.txt'})])
            if len(rounds)==3:
                self.assertIn('Unknown tool name', str(messages[-1].parts))
                self.assertIn('read_file', str(messages[-1].parts))
                self.assertEqual(self.reads, [])
                return ModelResponse(parts=[ToolCallPart('read_file', {'path':'fixture.txt'})])
            return ModelResponse(parts=[TextPart('Vérifié.')])
        result=self.agent(model).run_sync('Vérifie le fichier.', deps=self.deps)
        self.assertEqual(result.output, 'Vérifié.')
        self.assertEqual(self.reads, ['fixture.txt'])
        self.assertFalse(self.engine.final_status()['incomplete'])
        self.assertFalse(self.engine.journal.snapshot()['unresolved_mutations'])
        self.assertFalse(any(row['tool']=='invented_apply_patch' for row in self.engine.journal.snapshot()['operations']))

    def test_stale_tool_call_and_blank_comment_cannot_execute_silently(self):
        self.engine.commentary_enabled = self.engine.commentary_pending = True
        rounds = []
        def model(messages, info):
            rounds.append(1)
            if len(rounds)==1:
                return ModelResponse(parts=[ToolCallPart('read_file', {'path':'fixture.txt'})])
            if len(rounds)==2:
                self.assertEqual(self.reads, [])
                return ModelResponse(parts=[ToolCallPart('update_plan', {'steps':['Lire'], 'explanation':'  '})])
            if len(rounds)==3:
                self.assertEqual(self.reads, [])
                return ModelResponse(parts=[ToolCallPart('update_plan', {'steps':['Lire'], 'explanation':'Je vérifie le fichier.'})])
            if len(rounds)==4:
                return ModelResponse(parts=[ToolCallPart('read_file', {'path':'fixture.txt'})])
            return ModelResponse(parts=[TextPart('Vérifié.')])
        self.assertEqual(self.agent(model, prepared=False).run_sync('Vérifie', deps=self.deps).output, 'Vérifié.')
        self.assertEqual(self.reads, ['fixture.txt'])
        self.assertEqual(len(self.engine.journal.snapshot()['operations']), 2)

    def test_silence_persists_across_continue_and_can_be_reversed(self):
        self.engine.focus('Travaille sans commentaires. Termine avec un JSON.')
        self.engine.focus('Continue.')
        def model(messages, info):
            self.assertIn('read_file', {tool.name for tool in info.function_tools})
            return ModelResponse(parts=[TextPart('{"answer":4}')])
        agent = self.agent(model)
        self.assertEqual(agent.run_sync('Continue', deps=self.deps).output, '{"answer":4}')
        self.a.update_plan(['Lire'], explanation='Commentaire à supprimer.')
        self.assertNotIn('explanation', self.events[-1])
        restarted = WorkflowController(self.root, self.root/'execution')
        self.assertTrue(restarted.commentary_silent)
        restarted.focus('Explique ce que tu fais pendant le travail.')
        self.assertFalse(restarted.commentary_silent)

    def test_parallel_calls_wait_for_one_comment_without_exhausting_retries(self):
        rounds = []
        def model(messages, info):
            rounds.append(1)
            if len(rounds)==1:
                return ModelResponse(parts=[ToolCallPart('read_file', {'path':'fixture.txt'},
                    tool_call_id=f'blocked-{i}') for i in range(5)])
            if len(rounds)==2:
                self.assertEqual(self.reads, [])
                return ModelResponse(parts=[ToolCallPart('update_plan', {'steps':['Lire'],
                    'explanation':'Je vérifie les fichiers demandés.'})])
            if len(rounds)==3:
                return ModelResponse(parts=[ToolCallPart('read_file', {'path':'fixture.txt'})])
            return ModelResponse(parts=[TextPart('Vérifié.')])
        self.assertEqual(self.agent(model).run_sync('Inspecte', deps=self.deps).output, 'Vérifié.')
        self.assertEqual(self.reads, ['fixture.txt'])

    def test_repeated_model_rounds_ignoring_progress_stop_before_any_action(self):
        rounds = []
        def model(messages, info):
            rounds.append(1)
            return ModelResponse(parts=[ToolCallPart('read_file', {'path':'fixture.txt'})])
        with self.assertRaisesRegex(RuntimeError, 'sans progrès'):
            self.agent(model).run_sync('Inspecte', deps=self.deps)
        self.assertEqual(len(rounds), 3)
        self.assertEqual(self.reads, [])
        self.assertEqual(self.engine.journal.snapshot()['operations'], [])

    def test_simple_answer_does_not_force_a_plan_or_an_extra_model_request(self):
        calls = []
        def model(messages, info):
            calls.append(1)
            return ModelResponse(parts=[TextPart('4')])
        self.assertEqual(self.agent(model).run_sync('Combien font 2 + 2 ?', deps=self.deps).output, '4')
        self.assertEqual(calls, [1]); self.assertEqual(self.events, [])

    def test_elapsed_time_and_steering_refresh_the_commentary_contract(self):
        self.engine.commentary_enabled = True
        self.a.update_plan(['Lire'], explanation='Je vérifie le fichier.')
        self.assertFalse(self.engine.commentary_due())
        with patch('ovh_execution.time.monotonic', return_value=self.engine.commentary_at+46):
            self.assertTrue(self.engine.commentary_due())
        self.engine.focus('Vérifie plutôt le second fichier.')
        self.assertTrue(self.engine.commentary_due())
        from ovh_request import commentary_silenced
        for prompt in ('Termine uniquement avec un objet JSON.', 'Tu restes silencieux, corrige cela.',
                       'Le modèle travaille sans commentaires. Corrige ce défaut.',
                       'Le code contient `sans commentaires`. Explique le défaut.'):
            self.assertFalse(commentary_silenced(prompt), prompt)


if __name__=='__main__':unittest.main()
