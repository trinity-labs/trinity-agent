#!/opt/ovh-agent/bin/python
"""Offline contracts for native Pydantic execution, concurrency and cancellation."""
import asyncio
from contextvars import ContextVar
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pydantic_ai import Agent, ModelRetry, Tool
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel, DeltaToolCall
from ovh_execution import ExecutionCapability, RunDependencies, WorkflowController


class NativeExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.context = ContextVar('fixture_dependencies', default=None)

    def deps(self, name, **kwargs):
        root = self.root / name
        root.mkdir()
        return RunDependencies(engine=WorkflowController(root, root/'execution'), **kwargs)

    def agent(self, model, tools):
        return Agent(FunctionModel(model), deps_type=RunDependencies, tools=tools,
                     capabilities=[ExecutionCapability(self.context)], retries=1)

    async def test_cached_agent_isolates_dependencies_and_worker_thread_events(self):
        left, right = self.deps('left'), self.deps('right', full_access=True)
        entered = set()
        ready = asyncio.Event()

        async def inspect_repository(path: str) -> str:
            """Read a fixture asynchronously."""
            deps = self.context.get()
            entered.add(path)
            if len(entered) == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), 2)
            deps.external_access_notices.add(path)
            return str(deps.full_access)

        def read_file(path: str) -> str:
            """Read a fixture in the SDK worker thread."""
            deps = self.context.get()
            deps.engine.observe_event({'type': 'tool_end', 'success': path != 'left'})
            return path

        def model(messages, info):
            returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
            if returns:
                return ModelResponse(parts=[TextPart('|'.join(str(p.content) for p in returns))])
            name = messages[0].parts[0].content
            return ModelResponse(parts=[ToolCallPart('inspect_repository', {'path': name}),
                                        ToolCallPart('read_file', {'path': name})])

        agent = self.agent(model, [inspect_repository, read_file])
        results = await asyncio.gather(agent.run('left', deps=left), agent.run('right', deps=right))
        self.assertEqual([r.output for r in results], ['False|left', 'True|right'])
        self.assertEqual(left.external_access_notices, {'left'})
        self.assertEqual(right.external_access_notices, {'right'})
        for deps, outcome in [(left, 'failed'), (right, 'succeeded')]:
            rows = deps.engine.journal.snapshot()['operations']
            self.assertEqual(len(rows), 2)
            self.assertEqual(next(row for row in rows if row['tool'] == 'read_file')['status'], outcome)
            self.assertIsNone(deps.engine.events.get())
        self.assertIsNone(self.context.get())

    async def test_native_hook_enforces_readonly_even_with_a_stale_tool_schema(self):
        deps = self.deps('readonly')
        deps.engine.focus('Lecture seule, aucune modification.')
        target = deps.engine.workspace / 'created.txt'

        def write_file(path: str, content: str) -> str:
            """Create a fixture file."""
            target.write_text(content)
            return 'created'

        def model(messages, info):
            if len(messages) == 1:
                return ModelResponse(parts=[ToolCallPart('write_file', {'path': 'created.txt', 'content': 'bad'})])
            return ModelResponse(parts=[TextPart(str(messages[-1].parts[0].content))])

        result = await self.agent(model, [Tool(write_file, sequential=True)]).run('Inspect', deps=deps)
        self.assertIn('read_only', result.output)
        self.assertFalse(target.exists())

    async def test_cancellation_records_uncertain_mutation_and_cleans_context(self):
        deps = self.deps('cancel')
        started = asyncio.Event()
        target = deps.engine.workspace / 'partial.txt'

        async def run_command(command: str) -> str:
            """Simulate a command interrupted after a real side effect."""
            target.write_text(command)
            started.set()
            await asyncio.Event().wait()
            return 'unreachable'

        def model(messages, info):
            return ModelResponse(parts=[ToolCallPart('run_command', {'command': 'partial effect'})])

        agent = self.agent(model, [Tool(run_command, sequential=True)])
        task = asyncio.create_task(agent.run('Execute', deps=deps))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(target.read_text(), 'partial effect')
        row, = deps.engine.journal.snapshot()['operations']
        self.assertEqual(row['status'], 'unknown')
        self.assertIsNone(deps.engine.events.get())
        self.assertIsNone(self.context.get())

    async def test_native_retry_preserves_a_single_receipt_per_execution(self):
        deps = self.deps('retry')
        attempts = []

        def read_file(path: str) -> str:
            """Read a fixture, requesting correction of an invalid path."""
            attempts.append(path)
            if path == 'wrong':
                raise ModelRetry('Read correct instead.')
            return 'verified content'

        def model(messages, info):
            if len(messages) == 1:
                return ModelResponse(parts=[ToolCallPart('read_file', {'path': 'wrong'})])
            if len(attempts) == 1:
                return ModelResponse(parts=[ToolCallPart('read_file', {'path': 'correct'})])
            return ModelResponse(parts=[TextPart('done')])

        await self.agent(model, [read_file]).run('Read', deps=deps)
        self.assertEqual(attempts, ['wrong', 'correct'])
        rows = deps.engine.journal.snapshot()['operations']
        self.assertEqual([row['status'] for row in rows], ['failed', 'succeeded'])

    async def test_iter_keeps_dependencies_out_of_tool_arguments(self):
        deps = self.deps('iter')

        def read_file(path: str = '.') -> str:
            """Read a fixture with a default argument."""
            self.assertIs(self.context.get(), deps)
            return path

        def model(messages, info):
            self.assertEqual(set(info.function_tools[0].parameters_json_schema['properties']), {'path'})
            if len(messages) == 1:
                return ModelResponse(parts=[ToolCallPart('read_file', {})])
            return ModelResponse(parts=[TextPart('done')])

        agent = self.agent(model, [read_file])
        async with agent.iter('Read', deps=deps) as run:
            async for node in run:
                pass
        self.assertEqual(run.result.output, 'done')
        self.assertIsNone(self.context.get())
        row, = deps.engine.journal.snapshot()['operations']
        self.assertEqual(row['status'], 'succeeded')

    async def test_streamed_tool_rounds_keep_the_same_dependencies(self):
        deps = self.deps('stream')
        calls, streamed = [], []

        def read_file() -> str:
            """Read a streamed fixture."""
            self.assertIs(self.context.get(), deps)
            calls.append('read')
            return '42'

        async def stream(messages, info):
            self.assertIs(self.context.get(), deps)
            if not calls:
                yield 'Checking.'
                yield {0: DeltaToolCall(name='read_file', json_args='{}', tool_call_id='read')}
            else:
                yield 'Result: 42'

        async def events(ctx, source):
            self.assertIs(ctx.deps, deps)
            async for event in source:
                streamed.append(event)

        agent = Agent(FunctionModel(stream_function=stream), deps_type=RunDependencies,
                      tools=[read_file], capabilities=[ExecutionCapability(self.context)])
        result = await agent.run('Read', deps=deps, event_stream_handler=events)
        self.assertEqual(result.output, 'Result: 42')
        self.assertEqual(calls, ['read'])
        self.assertTrue(streamed)
        self.assertEqual(len(deps.engine.journal.snapshot()['operations']), 1)
        self.assertIsNone(self.context.get())


if __name__ == '__main__':
    unittest.main()
