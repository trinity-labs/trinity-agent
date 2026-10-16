"""Offline steering tests against the installed Pydantic AI (no credentials/API calls)."""
import ast
import json
import queue
import re
import sys
import tempfile
import threading
from pathlib import Path
from pydantic_ai import Agent
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from ovh_memory import atomic_write

names = {'read_followups', 'write_followups', 'publish_followups', 'handle_followup', 'apply_steering'}
syntax = ast.parse((root / 'ovh-agent.py').read_text())
module = ast.Module(body=[node for node in syntax.body if isinstance(node, ast.FunctionDef) and node.name in names], type_ignores=[])
events, task = [], {'prompt': 'Original task'}
with tempfile.TemporaryDirectory() as temporary:
    archive = Path(temporary)
    folder = archive / 'conversation'
    folder.mkdir()
    scope = dict(globals(), ARCHIVE_ROOT=archive, CONVERSATION_ID='conversation', AUTO_MODEL='auto-balanced',
                 FOLLOWUP_LOCK=threading.RLock(), FOLLOWUP_RUNNING=True, FOLLOWUP_CLAIMED=None,
                 conversation_dir=lambda: folder, emit=lambda data: events.append(data),
                 archive_event=lambda data: events.append(data), read_task_state=lambda: dict(task),
                 save_task_state=lambda **changes: task.update(changes))
    exec(compile(module, str(root / 'ovh-agent.py'), 'exec'), scope)
    requests = queue.Queue()
    def command(action, **fields):
        scope['handle_followup']({'action': action, 'conversation': 'conversation', **fields}, requests)
    command('add', id='later', text='Next task', mode='queue')
    assert requests.empty()  # Never run a second task concurrently.
    command('edit', id='later', text='Edited task')
    assert scope['read_followups']()[0]['text'] == 'Edited task'
    # Persisted across process restarts; only explicit continue wakes a paused queue.
    scope['FOLLOWUP_RUNNING'] = False
    command('list')
    assert requests.empty()
    command('continue')
    assert requests.get_nowait()['type'] == 'next_followup'
    scope['FOLLOWUP_RUNNING'] = True
    command('add', id='steer', text='Use the new requirement', mode='steer')
    calls = []
    def work() -> str:
        """A fake tool that accepts steering while running."""
        command('add', id='during-tool', text='Check the result carefully', mode='steer')
        return 'Tool done'
    def model(messages, info):
        calls.append(messages[:])
        if len(calls) == 1:
            assert any(isinstance(p, UserPromptPart) and p.content == 'Use the new requirement' for m in messages for p in m.parts)
            return ModelResponse(parts=[ToolCallPart('work', {}, tool_call_id='test-tool')])
        assert any(isinstance(p, ToolReturnPart) for m in messages for p in m.parts)
        assert any(isinstance(p, UserPromptPart) and p.content == 'Check the result carefully' for m in messages for p in m.parts)
        return ModelResponse(parts=[TextPart('Complete')])
    agent = Agent(FunctionModel(model), tools=[work], capabilities=[ProcessHistory(scope['apply_steering'])])
    result = agent.run_sync('Initial request')
    assert result.output == 'Complete'
    assert [i['id'] for i in scope['read_followups']()] == ['later']
    assert task['prompt'].endswith('Check the result carefully')
    assert sum(e['type'] == 'steer_applied' for e in events) == 2
    assert any(isinstance(p, UserPromptPart) and p.content == 'Check the result carefully' for m in result.all_messages() for p in m.parts)
    # Late steer (final model response already received) is dispatched as a continuation.
    scope['FOLLOWUP_RUNNING'] = False
    command('add', id='late', text='Late correction', mode='steer')
    assert requests.get_nowait()['type'] == 'next_followup'
    command('remove', id='late')
    command('remove', id='later')
    assert scope['read_followups']() == []
    # Invalid input and stale conversation never enter the queue.
    command('add', id='../escape', text='Bad', mode='steer')
    command('add', id='big', text='x' * 16001, mode='queue')
    scope['handle_followup']({'action':'add','conversation':'other','id':'wrong','text':'Bad','mode':'queue'}, requests)
    assert scope['read_followups']() == []
    assert events[-1]['type'] == 'followup_error'
    command('add', id='claimed', text='Already dispatching', mode='queue')
    scope['FOLLOWUP_CLAIMED'] = 'claimed'
    command('edit', id='claimed', text='Too late')
    assert scope['read_followups']()[0]['text'] == 'Already dispatching'
    # No following a queue-file symlink.
    (folder / 'followups.json').unlink()
    (folder / 'followups.json').symlink_to(archive / 'outside')
    command('list')
    assert events[-1]['type'] == 'followup_error'
    assert not (archive / 'outside').exists()
print('PASS: actual agent/tool loop, steering history, late continuation, queue persistence/edit/remove, validation and symlink protection')
