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

    def test_full_catalogue_remains_discoverable_and_bounded(self):
        a=ToolSelection()
        for i in range(40):a.register(f'tool_{i}','metadata '*100)
        self.assertLess(len(CORE),15)
        self.assertEqual(len(a.discover()['activated']),12)
        self.assertTrue(a.discover()['more_available'])
        self.assertEqual(a.discover('tool_39')['activated'][0]['name'],'tool_39')
        self.assertLessEqual(len(a.catalog['tool_0']),260)


if __name__=='__main__':unittest.main()
