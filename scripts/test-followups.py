"""Offline steering tests against the installed Pydantic AI (no credentials/API calls)."""
import ast
import json
import queue
import re
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import Mock, call
from pydantic_ai import Agent
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart, SystemPromptPart, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from ovh_memory import atomic_write
from ovh_request import analyze_request, SourceReview, REVIEW_INSTRUCTIONS
VERIFICATION_GUARD = None
TASK_ENGINE = None
ACTIVE_TOOLS = None

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
    steering_engine = Mock()
    scope['TASK_ENGINE'] = steering_engine
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
    # A correction must invalidate controller evidence too, not merely appear
    # as another model message whose old conclusion could survive compaction.
    assert steering_engine.focus.call_args_list == [call('Use the new requirement'), call('Check the result carefully')]
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
    # Reorder uses the latest persisted list and never wakes a stopped queue.
    scope['FOLLOWUP_CLAIMED'] = None
    scope['FOLLOWUP_RUNNING'] = True
    command('remove', id='claimed')
    while not requests.empty():
        requests.get_nowait()
    for identity, mode in [('q1','queue'), ('s1','steer'), ('q2','queue'), ('s2','steer'), ('q3','queue')]:
        command('add', id=identity, text=identity, mode=mode, model='selected-model', full_access=True)
    original = {item['id']:item for item in scope['read_followups']()}
    scope['FOLLOWUP_RUNNING'] = False
    command('reorder', id='q3', target='q1', position='before')
    command('reorder', id='s1', target='s2', position='after')
    items = scope['read_followups']()
    assert [item['id'] for item in items] == ['q3','q1','q2','s2','s1']
    assert {item['id']:item for item in items} == original  # Text, mode, model and access unchanged.
    assert json.loads((folder / 'followups.json').read_text()) == items
    assert requests.empty()
    # Reload the functions with a fresh process scope: order comes from disk.
    restarted = dict(scope)
    exec(compile(module, str(root / 'ovh-agent.py'), 'exec'), restarted)
    assert restarted['read_followups']() == items
    # Stale IDs, started source/anchor, malformed requests and wrong conversation cannot resurrect tasks.
    for fields in [dict(id='gone',target='q1',position='before'), dict(id='q1',target='gone',position='after'),
                   dict(id='q1',target='q1',position='after'), dict(id='q1',target='q2',position='middle'),
                   dict(id='q1',target='../x',position='before'), dict(id=[],target='q1',position='after'),
                   dict(id='q1',target='q2',position=[])]:
        command('reorder', **fields)
        assert events[-1]['type'] == 'followup_error' and events[-1]['action'] == 'reorder'
        assert scope['read_followups']() == items
    scope['handle_followup']({'action':'reorder','conversation':'other','id':'q1','target':'q2','position':'after'}, requests)
    assert events[-1]['conversation'] == 'other' and scope['read_followups']() == items
    for claimed in ['q1','q2']:
        scope['FOLLOWUP_CLAIMED'] = claimed
        command('reorder', id='q1',target='q2',position='after')
        assert events[-1]['type'] == 'followup_error' and scope['read_followups']() == items
    scope['FOLLOWUP_CLAIMED'] = None
    def failed_write(*args):
        raise OSError('Storage temporarily unavailable')
    scope['atomic_write'] = failed_write
    command('reorder', id='q1', target='q2', position='after')
    assert events[-1]['type'] == 'followup_error' and scope['read_followups']() == items
    scope['atomic_write'] = atomic_write
    scope['FOLLOWUP_RUNNING'] = True
    # Verify both steering application and the actual dispatcher selection use the saved order.
    applied = scope['apply_steering']([])
    assert [p.content for m in applied for p in m.parts if isinstance(p,UserPromptPart)] == ['s2','s1']
    assert steering_engine.focus.call_args_list[-2:] == [call('s2'), call('s1')]
    dispatcher = next(node for node in ast.walk(syntax) if isinstance(node,ast.If)
                      and ast.unparse(node.test) == "request_type == 'next_followup'")
    # Execute the real dispatch branch in a one-iteration loop (it contains continue).
    dispatch_module = ast.Module(body=[ast.For(target=ast.Name(id='_',ctx=ast.Store()),
        iter=ast.Tuple(elts=[ast.Constant(None)],ctx=ast.Load()), body=[dispatcher], orelse=[])],type_ignores=[])
    dispatch_code = compile(ast.fix_missing_locations(dispatch_module),str(root/'ovh-agent.py'),'exec')
    dispatched=[]
    while scope['read_followups']():
        scope.update(request_type='next_followup',request={'conversation':'conversation'},followup_id=None)
        exec(dispatch_code,scope)
        dispatched.append(scope['request']['id'])
        scope['write_followups']([item for item in scope['read_followups']() if item['id'] != scope['followup_id']])
    assert dispatched == ['q3','q1','q2'] and requests.empty()
    # No following a queue-file symlink.
    (folder / 'followups.json').unlink()
    (folder / 'followups.json').symlink_to(archive / 'outside')
    command('list')
    assert events[-1]['type'] == 'followup_error'
    assert not (archive / 'outside').exists()
print('PASS: actual agent/tool loop, reordered steering and dispatcher, restart persistence, stopped queue, stale/claimed tasks, validation and symlink protection')
