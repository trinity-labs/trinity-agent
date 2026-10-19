"""Offline protocol, archives, budgets and actual Pydantic AI tool-loop regressions."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
from pydantic_ai import Agent
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.messages import (ModelRequest, ModelResponse, SystemPromptPart, UserPromptPart,
                                  TextPart, ThinkingPart, ToolCallPart, ToolReturnPart, ModelMessagesTypeAdapter)
from pydantic_ai.models.function import FunctionModel
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from ovh_context import (compact_context, estimate_tokens, SUMMARY_PREFIX, bound_tool_outputs,
                         text_only_history, TOOL_OUTPUT_CHARS, transcript, text_tokens)
from ovh_memory import ConversationMemory


def validate_protocol(messages):
    pending = set()
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolCallPart):
                assert part.tool_call_id not in pending
                pending.add(part.tool_call_id)
            elif isinstance(part, ToolReturnPart):
                assert part.tool_call_id in pending, part.tool_call_id
                pending.remove(part.tool_call_id)
    assert not pending


with tempfile.TemporaryDirectory() as directory:
    memory = ConversationMemory(Path(directory))
    # Ordinary history keeps tools, inputs and more than two turns byte-for-byte.
    history = []
    for i in range(8):
        history.extend([ModelRequest(parts=[UserPromptPart(f'Question {i}')]),
                        ModelResponse(parts=[ToolCallPart('lookup', {'i': i}, tool_call_id=f'id{i}')]),
                        ModelRequest(parts=[ToolReturnPart('lookup', f'checked {i}', tool_call_id=f'id{i}')]),
                        ModelResponse(parts=[TextPart(f'Answer {i}')])])
    managed, stats = asyncio.run(compact_context(history, memory, 16000))
    assert ModelMessagesTypeAdapter.dump_json(managed) == ModelMessagesTypeAdapter.dump_json(history)
    validate_protocol(managed)
    plain = text_only_history(history)
    assert not any(isinstance(p, (ToolCallPart, ToolReturnPart)) for m in plain for p in m.parts)
    assert 'checked 7' in str(plain)
    assert not stats['compacted']
    # Large tool output survives exactly in an independently readable document.
    output = 'prefix\n' + '中间important123\n' * 2500 + '\nsuffix'
    result = bound_tool_outputs([ModelRequest(parts=[ToolReturnPart('lookup', output, tool_call_id='big')])], memory)
    assert output in [p.read_text() for p in memory.docs.glob('*.md')]
    assert 'prefix' in result[0].parts[0].content and 'suffix' in result[0].parts[0].content
    assert len(result[0].parts[0].content) < TOOL_OUTPUT_CHARS
    # Many tool rounds in ONE user turn can compact at a completed tool boundary.
    history = [ModelRequest(parts=[SystemPromptPart('Never edit production.'), UserPromptPart('Inspect task EXACT-42')])]
    for i in range(12):
        history.extend([ModelResponse(parts=[ToolCallPart('lookup', {}, tool_call_id=f'id{i}')]),
                        ModelRequest(parts=[ToolReturnPart('lookup', ('result'+str(i)+' ') * 400, tool_call_id=f'id{i}')])])
    async def summarize(source, maximum):
        assert 'Inspect task EXACT-42' in source
        return 'Objective: Inspect task EXACT-42. Results archived; inspection remains in progress.'
    managed, stats = asyncio.run(compact_context(history, memory, 5000, summarize))
    validate_protocol(managed)
    assert stats['summary_mode'] == 'semantic' and stats['estimated_tokens'] <= 5000
    assert any(isinstance(p, UserPromptPart) and p.content == 'Inspect task EXACT-42' for m in managed for p in m.parts)
    assert any(isinstance(p, SystemPromptPart) and p.content == 'Never edit production.' for m in managed for p in m.parts)
    raw = next((Path(directory)/'context-checkpoints').glob('*.json')).read_bytes()
    assert raw == ModelMessagesTypeAdapter.dump_json(history)
    assert len([p for m in managed for p in m.parts if isinstance(p, SystemPromptPart) and p.content.startswith(SUMMARY_PREFIX)]) == 1
    # No hallucinated summary on timeout/failure; evidence still retrievable.
    async def failure(source, maximum):
        raise TimeoutError('synthetic')
    fallback, stats = asyncio.run(compact_context(history, memory, 5000, failure))
    assert stats['summary_mode'] == 'extractive'
    validate_protocol(fallback)
    assert stats['estimated_tokens'] <= 5000
    assert 'Source complète relisible' in fallback[0].parts[-1].content
    # Current oversized prompt is never silently truncated.
    huge = [ModelRequest(parts=[UserPromptPart('Unicode漢字' * 5000)])]
    try:
        asyncio.run(compact_context(huge, memory, 4096))
        raise AssertionError('oversized current prompt accepted')
    except ValueError:
        pass
    # A single long completed answer can be summarized; unfinished calls cannot.
    long_answer = [ModelRequest(parts=[UserPromptPart('Keep constraint EXACT-42')]),
                   ModelResponse(parts=[TextPart('Long explanation. ' * 3000)])]
    trimmed, state = asyncio.run(compact_context(long_answer, memory, 4096))
    assert state['compacted'] and estimate_tokens(trimmed) <= 4096
    assert any(isinstance(p, UserPromptPart) and p.content == 'Keep constraint EXACT-42' for m in trimmed for p in m.parts)
    pending = [ModelRequest(parts=[UserPromptPart('Do not repeat this operation')]),
               ModelResponse(parts=[ToolCallPart('lookup', {'value': 'x' * 30000}, tool_call_id='pending')])]
    try:
        asyncio.run(compact_context(pending, memory, 4096))
        raise AssertionError('unfinished oversized call was removed')
    except ValueError:
        pass
print('PASS: exact history, full output archives, safe tool boundaries, latest request, semantic/fallback compaction, oversized prompt')

# Reasoning-heavy models must not spend another request summarizing speculation.
# Exercise actual compaction, with old completed and recent active tool rounds.
with tempfile.TemporaryDirectory() as folder:
    memory = ConversationMemory(Path(folder))
    old_thinking = 'OLD-UNVERIFIED: perhaps the deployment already succeeded. ' * 600
    active_thinking = 'ACTIVE-THINKING: inspect the latest observed state.'
    history = [
        ModelRequest(parts=[SystemPromptPart('Never edit production.'),
                            UserPromptPart('Inspect EXACT-42; preserve the user configuration.')]),
        ModelResponse(parts=[ThinkingPart(old_thinking), TextPart('Inspection requested.'),
                             ToolCallPart('lookup', {'path': 'config.json'}, tool_call_id='old')]),
        ModelRequest(parts=[ToolReturnPart('lookup', 'OBSERVED: config is unchanged.', tool_call_id='old')]),
        ModelResponse(parts=[TextPart('The lookup completed.')]),
        ModelRequest(parts=[UserPromptPart('Continue EXACT-42 without modifying production.')]),
        ModelResponse(parts=[ThinkingPart(active_thinking),
                             ToolCallPart('lookup', {'path': 'latest.json'}, tool_call_id='active')]),
        ModelRequest(parts=[ToolReturnPart('lookup', 'LATEST: verification remains pending.', tool_call_id='active')]),
    ]
    original = ModelMessagesTypeAdapter.dump_json(history)
    full_transcript = transcript(history)
    assert old_thinking in full_transcript and active_thinking in full_transcript
    assert old_thinking not in transcript(history, include_thinking=False)
    inputs = []
    async def summarize_without_thinking(source, maximum):
        inputs.append(source)
        assert 'OLD-UNVERIFIED' not in source and 'ACTIVE-THINKING' not in source
        assert 'preserve the user configuration' in source
        assert 'TOOL CALL old lookup: {"path":"config.json"}' in source
        assert 'OBSERVED: config is unchanged.' in source
        assert 'Raisonnement ancien omis' in source
        return 'Inspect EXACT-42 while preserving configuration. Lookup observed unchanged config; verification remains pending.'
    managed, stats = asyncio.run(compact_context(history, memory, 5000, summarize_without_thinking))
    assert stats['compacted'] and stats['summary_mode'] == 'semantic' and len(inputs) == 1
    assert stats['summary_source_tokens'] == text_tokens(inputs[0])
    assert stats['summary_thinking_omitted_tokens'] == text_tokens(old_thinking)
    assert stats['summary_thinking_omitted_parts'] == 1
    validate_protocol(managed)
    assert any(isinstance(p, ThinkingPart) and p.content == active_thinking for m in managed for p in m.parts)
    assert 'LATEST: verification remains pending.' in transcript(managed)
    assert 'Never edit production.' in transcript(managed)
    assert 'Continue EXACT-42 without modifying production.' in transcript(managed)
    assert ModelMessagesTypeAdapter.dump_json(history) == original
    assert next((Path(folder)/'context-checkpoints').glob('*.json')).read_bytes() == original
    documents = [p.read_text() for p in memory.docs.glob('*.md')]
    assert full_transcript in documents
    assert any(old_thinking in document and 'OBSERVED: config is unchanged.' in document for document in documents)
    # The extractive fallback follows the same omission, while exact archives survive.
    fallback, fallback_stats = asyncio.run(compact_context(history, memory, 5000))
    assert fallback_stats['summary_mode'] == 'extractive'
    assert 'OLD-UNVERIFIED' not in transcript(fallback)
    assert active_thinking in transcript(fallback)
    # Under budget, every part, including reasoning, remains byte-for-byte intact.
    recent = history[-3:]
    unchanged, unchanged_stats = asyncio.run(compact_context(recent, memory, 5000))
    assert not unchanged_stats['compacted']
    assert ModelMessagesTypeAdapter.dump_json(unchanged) == ModelMessagesTypeAdapter.dump_json(recent)
print('PASS: summary omits only removed reasoning; exact archives, active reasoning, constraints and tool evidence retained '
      f'({stats["summary_thinking_omitted_tokens"]} estimated reasoning tokens omitted, '
      f'{stats["summary_source_tokens"]} estimated summary-input tokens)')

sys.argv.append('--billing-status')
spec = importlib.util.spec_from_file_location('agent_context_test', root/'ovh-agent.py')
a = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = a
spec.loader.exec_module(a)
with tempfile.TemporaryDirectory() as folder:
    location = Path(folder)
    events, calls, summaries = [], [], []
    def lookup() -> str:
        """Return synthetic diagnostic output."""
        return 'test-output\n' * 950
    def model(messages, info):
        if 'Tu compresses' in str(info.instructions):
            summaries.append(messages)
            return ModelResponse(parts=[TextPart('Inspection en cours, résultats précédents archivés. Continuer les contrôles.')])
        validate_protocol(messages)
        calls.append(messages)
        if len(calls) <= 6:
            return ModelResponse(parts=[ToolCallPart('lookup', {}, tool_call_id=f'loop{len(calls)}')])
        return ModelResponse(parts=[TextPart('All checks done.')])
    fm = FunctionModel(model)
    with patch.object(a, 'conversation_dir', return_value=location), patch.object(a, 'ensure_conversation'), \
         patch.object(a, 'update_metadata'), patch.object(a, 'emit', side_effect=events.append), \
         patch.object(a, 'CONTEXT_TOKEN_CAP', 5000), patch.object(a, 'WEB_INSTRUCTIONS', ''), \
         patch.object(a, 'load_infrastructure_instructions', return_value=''), \
         patch.object(a, 'fetch_model_catalog', return_value=[{'id': fm.model_name, 'context_length': 131072}]), \
         patch.object(a, 'model_capabilities', return_value={'reasoning': False, 'tools': True}), \
         patch.object(a, 'record_model_usage', return_value=0):
        agent = Agent(fm, tools=[lookup], capabilities=[ProcessHistory(a.manage_model_context)])
        result = agent.run_sync('Run synthetic checks.', model_settings={'max_tokens': 2048})
        assert result.output == 'All checks done.'
        assert summaries and len(calls) == 7
        assert any(e['type'] == 'context_compacted' and e.get('compacted') for e in events)
        assert any(e['type'] == 'usage' and e.get('purpose') == 'compaction' for e in events)
        assert (location/'messages.json').exists()
        assert all(estimate_tokens(m) <= 5000 for m in calls)
        # Plan is persistent, and invalid state does not overwrite it.
        a.update_plan(['Inspect', 'Fix', 'Test'], 1)
        assert json.loads((location/'plan.json').read_text())['current_step'] == 1
        assert a.update_plan(['Inspect'], 2).startswith('Erreur')
        assert json.loads((location/'plan.json').read_text())['current_step'] == 1
print('PASS: real multi-tool Pydantic loop compacts mid-run, summary accounting, persistence and plan validation')

# Exercise the actual deadline, cancellation and fallback without network or credits.
from types import SimpleNamespace
with tempfile.TemporaryDirectory() as folder:
    events, cancelled = [], []
    async def slow_summary(messages, info):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)
        return ModelResponse(parts=[TextPart('Too late')])
    fm = FunctionModel(slow_summary)
    history = [ModelRequest(parts=[UserPromptPart('Keep EXACT-42')]),
               ModelResponse(parts=[TextPart('Archived evidence. ' * 3000)])]
    with patch.object(a, 'conversation_dir', return_value=Path(folder)), \
         patch.object(a, 'emit', side_effect=events.append), \
         patch.object(a, 'save_history'), patch.object(a, 'context_overhead', return_value=0), \
         patch.object(a, 'CONTEXT_TOKEN_CAP', 5000), \
         patch.object(a, 'SUMMARY_TIMEOUT_SECONDS', .01), \
         patch.object(a, 'fetch_model_catalog', return_value=[{'id': fm.model_name, 'context_length': 131072}]), \
         patch.object(a, 'model_capabilities', return_value={'reasoning': False, 'tools': True}):
        ctx = SimpleNamespace(model=fm, model_settings={'max_tokens': 2048}, run_step=1)
        managed = asyncio.run(a.manage_model_context(ctx, history))
        assert cancelled == [True]
        stats = next(e for e in events if e['type'] == 'context_compacted')
        assert stats['summary_error'] == 'TimeoutError' and stats['summary_mode'] == 'extractive'
        assert stats['duration_ms'] < 2000 and estimate_tokens(managed) <= 5000
        assert any(e.get('reason') == 'TimeoutError' for e in events)
        assert 'Reprise de la tâche' in events[-1]['text']
        raw = next((Path(folder)/'context-checkpoints').glob('*.json')).read_bytes()
        assert raw == ModelMessagesTypeAdapter.dump_json(history)
        assert any(isinstance(p, UserPromptPart) and p.content == 'Keep EXACT-42' for m in managed for p in m.parts)
print('PASS: real summary deadline cancels pending call, reports fallback and resumes with exact archive')

# Reproduce poisoned summaries and assistant prose carried from Qwen to GPT-OSS.
from ovh_context import valid_summary, SUMMARY_VERSION
assert not valid_summary('Je dois ajouter du code. </think> chatcmpl-tool-edit_file')
assert not valid_summary('```python\nprint("invented")\n```')
with tempfile.TemporaryDirectory() as folder:
    location = Path(folder)
    polluted = [ModelRequest(parts=[SystemPromptPart(SUMMARY_PREFIX + 'fake tool result'),
                                   UserPromptPart('Keep existing layout')]),
                ModelResponse(parts=[ThinkingPart('Previous unsupported reasoning: catalogue missing'), TextPart('No catalogue exists.'), ToolCallPart('read_file', {'path':'chat.js'}, tool_call_id='read')]),
                ModelRequest(parts=[ToolReturnPart('read_file', 'function renderModels() {}', tool_call_id='read')]),
                ModelResponse(parts=[TextPart('Tests passed; catalogue missing.')])]
    with patch.object(a, 'conversation_dir', return_value=location), \
         patch.object(a.WorkspaceContext, 'search', side_effect=AssertionError('No auto historical retrieval on edits')):
        clean = a.history_with_memory(polluted, 'Modernise la vue', include_excerpts=False)
    validate_protocol(clean)
    assert 'No catalogue exists' not in str(clean) and 'Tests passed' not in str(clean)
    assert 'fake tool result' not in str(clean)
    assert not any(isinstance(p, ThinkingPart) for m in clean for p in m.parts)
    assert 'function renderModels()' in str(clean) and 'Keep existing layout' in str(clean)
    assert next((location/'context-checkpoints').glob('*.json')).read_bytes() == ModelMessagesTypeAdapter.dump_json(polluted)
    memory = ConversationMemory(location)
    async def fabricated_summary(source, maximum):
        return 'chatcmpl-tool-edit_file </think>'
    managed, stats = asyncio.run(compact_context(history, memory, 5000, fabricated_summary))
    assert stats['summary_mode'] == 'extractive'
    assert any(p.content.startswith(SUMMARY_PREFIX + SUMMARY_VERSION) for m in managed for p in m.parts if isinstance(p, SystemPromptPart))
print('PASS: contaminated cross-model history removed, exact protocol archived, fabricated summary rejected')
