"""Discovery preserves capabilities while reducing actual model schemas."""
import asyncio
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ovh_tools import ToolSelection, CORE
from pydantic_ai import Agent, Tool
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart


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


if __name__=='__main__':unittest.main()
