"""Offline regression tests for OVH request settings, caching and streamed tool rounds."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch
from pydantic_ai import Agent
from pydantic_ai.models.function import FunctionModel, DeltaToolCall
from pydantic_ai.messages import PartStartEvent, PartDeltaEvent, TextPart, TextPartDelta, ThinkingPart

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
sys.argv.append('--billing-status')  # Import without creating a user conversation.
spec = importlib.util.spec_from_file_location('agent', root / 'ovh-agent.py')
a = importlib.util.module_from_spec(spec)
# This suite asserts published defaults, not the operator's local token caps.
# Isolate import-time settings; never alter the running installation policy.
with patch.dict(os.environ, {
    "OVH_AGENT_CONTEXT_TOKENS": "262144", "OVH_AGENT_CONTEXT_RESERVE_TOKENS": "4096",
    "OVH_AGENT_OUTPUT_TOKENS": "16384", "OVH_AGENT_COMPLEX_OUTPUT_TOKENS": "32768",
}):
    spec.loader.exec_module(a)
# These heuristic fixtures must not consume installation-specific benchmark data.
a.load_evidence = lambda *args, **kwargs: {}
events = []
a.emit = events.append
assert a.normalize_model_selection('auto') == a.AUTO_MODEL
assert a.normalize_model_selection(a.AUTO_MODEL) == a.AUTO_MODEL
assert a.normalize_model_selection('gpt-oss-120b') == 'gpt-oss-120b'
catalog = [{'id': 'gpt-oss-20b', 'context_length': 131072, 'max_completion_tokens': 12000}]
with patch.object(a, 'fetch_model_catalog', return_value=catalog):
    assert a.generation_settings('gpt-oss-20b', 'Bonjour')['openai_reasoning_effort'] == 'low'
    settings = a.generation_settings('gpt-oss-20b', 'Audit sécurité Docker')
    assert settings == {'max_tokens': 12000, 'openai_reasoning_effort': 'medium'}
    assert 'openai_reasoning_effort' not in a.generation_settings('Meta-Llama-3_3-70B-Instruct', 'Bonjour')

# Generous defaults remain bounded by provider output and total-window limits.
with patch.object(a, 'context_overhead', return_value=8000):
    card = {'context_length': 262144, 'max_completion_tokens': 65536}
    assert a.output_token_budget(card, False, 8000) == 16384
    assert a.output_token_budget(card, True, 8000) == 32768
    assert a.output_token_budget({**card, 'max_completion_tokens': 4096}, True, 8000) == 4096
    for window in (32768, 65536, 131072, 262144):
        reserved = a.output_token_budget({'context_length': window}, True, 8000)
        budget = min(a.CONTEXT_TOKEN_CAP, a.usable_context_window(window) - reserved - 8000)
        assert reserved <= window // 3 and budget >= 4096
        assert reserved + budget + 8000 + a.CONTEXT_RESERVE_TOKENS <= window
    assert a.CONTEXT_TOKEN_CAP == 262144
    assert a.auto_model_card()['context_length'] == a.CONTEXT_TOKEN_CAP
    assert budget > 200000  # The largest window is no longer held to the old 98304 cap.
    assert a.output_token_budget({'context_length': 4096}, True, 8000) == 0
with patch.dict(os.environ, {'OVH_AGENT_TEST_BUDGET': 'bad'}):
    assert a.budget_setting('OVH_AGENT_TEST_BUDGET', 200) == 200
with patch.dict(os.environ, {'OVH_AGENT_TEST_BUDGET': '-1'}):
    assert a.budget_setting('OVH_AGENT_TEST_BUDGET', 200) == 1
with patch.dict(os.environ, {'OVH_AGENT_TEST_BUDGET': '300'}):
    assert a.budget_setting('OVH_AGENT_TEST_BUDGET', 200) == 300

with tempfile.TemporaryDirectory() as folder:
    a.MODEL_CACHE_FILE = Path(folder) / 'catalog.json'
    a.MODEL_CACHE_FILE.write_text(json.dumps({'updated_at': a.now_iso(), 'source': a.MODEL_API_URL, 'schema_version': a.CATALOG_SCHEMA_VERSION, 'models': catalog}))
    a.MODEL_CATALOG = None
    with patch.object(a.urllib.request, 'urlopen', side_effect=AssertionError('Unexpected network call')):
        assert a.fetch_model_catalog()[0]['id'] == catalog[0]['id']
        assert a.fetch_model_catalog()[0]['id'] == catalog[0]['id']

with patch.dict(os.environ, {'OVH_CLOUD_PROJECT': 'test'}), patch.object(a, 'read_billing_cache', return_value={'project': 'other', 'cached_at': time.time()}), patch.object(a, 'refresh_billing_async') as refresh:
    assert a.routing_billing_status()['available'] is False
    refresh.assert_called_once_with({'silent': True})

async def text_events():
    yield PartStartEvent(index=0, part=ThinkingPart('private reasoning'))
    yield PartStartEvent(index=1, part=TextPart('Bon'))
    yield PartDeltaEvent(index=1, delta=TextPartDelta('jour'))
stream = a.ResponseStream()
asyncio.run(stream.handle(None, text_events()))
assert ''.join(e['text'] for e in events) == 'Bonjour'
assert stream.first_text_ms is not None

events.clear()
calls = []
def lookup() -> str:
    """Read a harmless test value."""
    calls.append('tool')
    return '42'
async def model(messages, info):
    if not calls:
        yield 'Je vérifie.'
        yield {0: DeltaToolCall(name='lookup', json_args='{}', tool_call_id='call1')}
    else:
        yield 'Résultat : '
        yield '42'
stream = a.ResponseStream()
agent = Agent(FunctionModel(stream_function=model), tools=[lookup])
result = agent.run_sync('Test', event_stream_handler=stream.handle)
assert result.output == 'Résultat : 42'
assert calls == ['tool']
assert ''.join(e['text'] for e in events) == 'Je vérifie.\n\nRésultat : 42'
assert result.usage.requests == 2
print('PASS: settings, catalog cache, nonblocking billing, visible-only streaming, tool cycle and final output')

# All documented chat families use their own capabilities, including vision-only Qwen.
for mid, caps in a.DOCUMENTED_MODEL_CAPABILITIES.items():
    with patch.object(a, 'fetch_model_catalog', return_value=[]):
        settings = a.generation_settings(mid, 'Audit Docker')
    assert ('openai_reasoning_effort' in settings) == caps['reasoning'], mid
assert not a.model_capabilities('Qwen2.5-VL-72B-Instruct')['tools']
# Newly documented models outside the original allowlist are eligible for routing.
for mid in ['Qwen3.8-27B', 'Qwen3.6-27B', 'Qwen3.5-397B-A17B', 'Meta-Llama-3_3-70B-Instruct', 'Mistral-Nemo-Instruct-2407']:
    card = a.normalise_model({'id': mid, 'context_length': 131072,
                             'pricing': {'currency_unit': 'EUR', 'prompt': '0.000001', 'completion': '0.000001'}})
    with patch.object(a, 'fetch_model_catalog', return_value=[card]), patch.object(a, 'load_router_state', return_value={}), patch.object(a, 'routing_billing_status', return_value={}):
        selected, _ = a.choose_cost_effective_model('Audit Xen', 1000, 0)
        assert selected == mid
print('PASS: all 10 model profiles, reasoning isolation and expanded automatic routing')

assert a.MODEL_SPECS['gpt-oss-120b']['parameters_b'] == 117
assert a.MODEL_SPECS['gpt-oss-120b']['active_parameters_b'] == 5.1
assert a.MODEL_SPECS['Qwen3.5-397B-A17B']['active_parameters_b'] == 17
assert len(a.MODEL_SPECS) == 10
assert a.is_complex_task('PoC de vulnérabilité')
# Weight influences complex-task routing within the configured cost envelope.
cards = [a.normalise_model({'id': mid, 'context_length': 131072,
         'pricing': {'currency_unit': 'EUR', 'prompt': '0.000001', 'completion': '0.000001'}})
         for mid in ['gpt-oss-20b', 'gpt-oss-120b']]
with patch.object(a, 'fetch_model_catalog', return_value=cards), patch.object(a, 'load_router_state', return_value={}), patch.object(a, 'routing_billing_status', return_value={}):
    assert a.choose_cost_effective_model('Refactor plusieurs fichiers', 100, 0)[0] == 'gpt-oss-120b'
    assert a.choose_cost_effective_model('Bonjour', 100, 0)[0] == 'gpt-oss-20b'
print('PASS: documented total/active parameters and weight-aware routing')

# A short continuation carries task difficulty into both routing and generation.
analysis = a.analyze_request('Continue', ['Refactorise notre architecture dans plusieurs fichiers'])
from dataclasses import asdict
with patch.object(a, 'fetch_model_catalog', return_value=cards), patch.object(a, 'load_router_state', return_value={}), patch.object(a, 'routing_billing_status', return_value={}):
    model, routing = a.choose_cost_effective_model('Continue',100,0,analysis=asdict(analysis))
    assert model == 'gpt-oss-120b' and routing['complex_task']
    assert a.generation_settings(model,'Continue',asdict(analysis))['openai_reasoning_effort'] == 'medium'
    assert a.generation_settings(model,'Bonjour',asdict(a.analyze_request('Bonjour')))['openai_reasoning_effort'] == 'low'
    assert 'openai_reasoning_effort' not in a.generation_settings('Meta-Llama-3_3-70B-Instruct','Continue',asdict(analysis))
assert a.analyze_request('Fais une synthèse des stratégies officielles').complexity == 'complex'
print('PASS: contextual follow-up difficulty, shared routing/generation policy and provider-specific reasoning')

# Discovery uses a slug, runtime uses a name, aliases are case-insensitive.
a.index_discovery_catalog([{'id': 'new-slug', 'name': 'New-Model', 'available': False,
    'metadata': {'aliases': ['publisher/New-Model'], 'model_specs': {
        'nb_of_billion_parameters': 42, 'quantization': 'fp8', 'capabilities': {
        'function_calling': False, 'reasoning': True, 'reasoning_efforts': ['low'],
        'streaming': False, 'input_modality': ['text'], 'response_format': ['json_schema']}},
        'publishing_information': {'licence_name': 'Example'}}}])
card = a.enrich_catalog_model({'id': 'New-Model', 'selectable': True, 'vision': True})
assert card['selectable'] is False
assert card['parameters_b'] == 42
assert card['vision'] is False
assert a.DISCOVERY_MODELS['publisher/new-model']['name'] == 'New-Model'
a.install_catalog_capabilities([card])
with patch.object(a, 'fetch_model_catalog', return_value=[card]):
    assert 'openai_reasoning_effort' not in a.generation_settings('New-Model', 'Audit sécurité')
assert a.model_capabilities('New-Model')['tools'] is False
assert a.model_capabilities('New-Model')['response_formats'] == ['json_schema']
a.index_discovery_catalog([])
print('PASS: discovery IDs/aliases, availability, explicit false capabilities and supported reasoning values')

# Cold Qwen requests must use the freshly discovered API, not stale GPT-style
# defaults. Reasoning support is distinct from adjustable reasoning effort.
qwen_card = {'id': 'Qwen3.6-27B', 'catalog_source': a.DISCOVERY_API_URL,
             'supports_tools': True, 'supports_reasoning': True,
             'supports_streaming': True, 'reasoning_efforts': [],
             'context_length': 262144, 'max_completion_tokens': 32768}
def fresh_qwen_catalog():
    a.install_catalog_capabilities([qwen_card])
    return [qwen_card]
with patch.dict(a.MODEL_CAPABILITIES, clear=True), patch.object(a, 'fetch_model_catalog', side_effect=fresh_qwen_catalog):
    for prompt in ['Bonjour', 'Audit sécurité Docker']:
        assert 'openai_reasoning_effort' not in a.generation_settings(qwen_card['id'], prompt)
    qwen_card = {**qwen_card, 'id': 'Qwen3.8-27B', 'reasoning_efforts': ['none', 'low', 'medium', 'xhigh']}
    assert a.generation_settings(qwen_card['id'], 'Bonjour')['openai_reasoning_effort'] == 'low'
    assert a.generation_settings(qwen_card['id'], 'Audit sécurité Docker')['openai_reasoning_effort'] == 'medium'
print('PASS: cold Qwen discovery before generation and no invented effort for explicit empty capabilities')

# Completed cyber evidence outranks size/cheapness, while single/manual choices remain available.
cyber_cards = [a.normalise_model({'id': mid, 'context_length': 262144,
               'pricing': {'currency_unit': 'EUR', 'prompt': '0.000001', 'completion': '0.000001'}})
               for mid in ['gpt-oss-120b', 'Qwen3.6-27B']]
with patch.object(a, 'fetch_model_catalog', return_value=cyber_cards), patch.object(a, 'load_router_state', return_value={}), patch.object(a, 'routing_billing_status', return_value={}):
    assert a.choose_cost_effective_model('Audit de vulnérabilité et PoC', 100, 0)[0] == 'Qwen3.6-27B'
assert a.CYBER_EVALUATIONS['Qwen3.5-9B']['errors'] == 3
assert a.CYBER_EVALUATIONS['Qwen3.5-9B']['refusals'] == 0
print('PASS: cyber lab evidence before size; timeouts remain separate from refusal')

# Native EUR discovery prices replace, rather than relabel, runtime USD prices.
a.index_discovery_catalog([{'id':'gpt-oss-20b','name':'gpt-oss-20b','available':True,
    'metadata':{'usage_information':{'pricing':[
        {'price_unit':'million_input_tokens','price':.04},
        {'price_unit':'million_output_tokens','price':.15}]}}}])
raw = {'id':'gpt-oss-20b','context_length':131072,
       'pricing':{'currency_unit':'USD','prompt':'0.00000005','completion':'0.00000018'}}
euro = a.enrich_catalog_model(a.normalise_model(raw))
assert euro['currency']=='EUR' and euro['input_per_million']==.04 and euro['output_per_million']==.15
assert euro['pricing_source']==a.DISCOVERY_API_URL
a.index_discovery_catalog([])
assert a.enrich_catalog_model(a.normalise_model(raw))['currency']=='USD'
print('PASS: native EUR price source, no USD relabelling without EUR catalog data')

# Checkpoints reserve time for validation without rewriting history every round.
from types import SimpleNamespace
from pydantic_ai.exceptions import UsageLimitExceeded
baseline = [a.ModelRequest(parts=[a.UserPromptPart(content='Audit du projet')])]
guard = SimpleNamespace(started_at=time.monotonic()-301, checkpoints=set())
with patch.object(a, 'VERIFICATION_GUARD', guard), patch.object(a, 'TASK_CHECKPOINT_SECONDS', 300), patch.object(a, 'REQUEST_LIMIT', 80):
    checkpoint = a.task_checkpoint(SimpleNamespace(usage=SimpleNamespace(requests=10)), baseline)
    assert len(checkpoint) == 2 and checkpoint[0] is baseline[0]
    assert 'verify_changes' in checkpoint[-1].parts[0].content
    assert a.task_checkpoint(SimpleNamespace(usage=SimpleNamespace(requests=11)), checkpoint) is checkpoint
    final = a.task_checkpoint(SimpleNamespace(usage=SimpleNamespace(requests=71)), checkpoint)
    assert len(final) == 3 and '9 appels' in final[-1].parts[0].content
    assert a.task_checkpoint(SimpleNamespace(usage=SimpleNamespace(requests=72)), final) is final
    message = a.task_error_message(UsageLimitExceeded('The next request would exceed the request_limit of 80'))
    assert '80 appels modèle' in message and 'budget en euros' in message
for mode, marker in [(0,'Aucun emoji'),(1,'seuls emojis'),(2,'parcimonie'),(3,'fréquemment')]:
    assert marker in a.emoji_instructions(mode)

# Affinity cannot retain a weaker model on complex work or override availability.
with patch.object(a, 'fetch_model_catalog', return_value=cards), patch.object(a, 'load_router_state', return_value={}), patch.object(a, 'routing_billing_status', return_value={}):
    assert a.choose_cost_effective_model('Audit du projet',100,0,affinity_model='gpt-oss-20b')[0] == 'gpt-oss-120b'
    selected, route = a.choose_cost_effective_model('Bonjour',100,0,affinity_model='gpt-oss-120b')
    assert selected == 'gpt-oss-120b' and route['affinity_retained']
    assert not a.choose_cost_effective_model('Bonjour',100,0,affinity_model='missing')[1]['affinity_retained']
print('PASS: validation checkpoints, emoji policies, readable limits and bounded model affinity')

# Verify actual Pydantic scheduling: reads overlap, writes/tests cannot race them.
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
flags = {tool.name: tool.sequential for tool in a.agent_tools()}
assert flags['edit_file'] and flags['run_command'] and flags['verify_changes']
assert not flags['read_file'] and not flags['search_web']
async def scheduling_test():
    started = set()
    completed = []
    both_reading = asyncio.Event()
    async def read_file():
        """Read a fixture."""
        started.add('read_file')
        if len(started) == 2: both_reading.set()
        await asyncio.wait_for(both_reading.wait(), 1)
        completed.append('read_file')
        return 'read'
    async def list_files():
        """List a fixture."""
        started.add('list_files')
        if len(started) == 2: both_reading.set()
        await asyncio.wait_for(both_reading.wait(), 1)
        completed.append('list_files')
        return 'listed'
    async def write_file():
        """Write a fixture."""
        assert set(completed) == {'read_file', 'list_files'}
        completed.append('write_file')
        return 'written'
    async def verify_changes():
        """Check the fixture."""
        assert completed[-1] == 'write_file'
        completed.append('verify_changes')
        return 'checked'
    def model(messages, info):
        if not completed:
            return ModelResponse(parts=[ToolCallPart(name, {}, tool_call_id=name) for name in ['read_file','list_files','write_file','verify_changes']])
        return ModelResponse(parts=[TextPart('Done')])
    tools = [a.Tool(fn, sequential=flags[fn.__name__]) for fn in [read_file,list_files,write_file,verify_changes]]
    result = await Agent(FunctionModel(model), tools=tools).run('Check scheduling')
    assert result.output == 'Done' and completed[-1] == 'verify_changes'
asyncio.run(scheduling_test())
print('PASS: independent reads overlap; mutations and checks execute as barriers')

# Read-only tasks expose a smaller schema set; editing and steered tasks keep tools.
from types import SimpleNamespace
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.messages import ModelRequest, UserPromptPart
async def check_prepared_tools():
    prompt='Lis uniquement settings.json. Aucune modification ni recherche Internet.'
    ctx=SimpleNamespace(prompt=prompt,messages=[ModelRequest(parts=[UserPromptPart(prompt)])])
    assert await a.prepare_tool_for_request(ctx,ToolDefinition(name='read_file')) is not None
    for name in ['run_command','write_file','install_tools','search_web']:
        assert await a.prepare_tool_for_request(ctx,ToolDefinition(name=name)) is None
    ctx.messages.append(ModelRequest(parts=[UserPromptPart('Modifie maintenant le fichier')]))
    assert await a.prepare_tool_for_request(ctx,ToolDefinition(name='write_file')) is not None
asyncio.run(check_prepared_tools())
print('PASS: read-only tool schemas and steering restoration')

# A read-only task is an execution permission, including in full filesystem access.
# Neutral steering, tool discovery and a stale advertised schema cannot grant it.
from ovh_execution import WorkflowController, guarded_tool, read_only_policy
from pydantic_ai.messages import ToolReturnPart
for readonly_prompt in ('Lecture seule : audite ce dépôt.', 'Read only: inspect the repository.', 'Inspecte le projet sans le modifier.'):
    async def check_unscoped_readonly():
        ctx = SimpleNamespace(prompt=readonly_prompt, messages=[ModelRequest(parts=[UserPromptPart(readonly_prompt)])])
        with patch.object(a, 'TASK_ENGINE', None), patch.object(a, 'ACTIVE_TOOLS', a.ToolSelection(readonly_prompt)):
            assert await a.prepare_tool_for_request(ctx, ToolDefinition(name='edit_file')) is None
            ctx.messages.append(ModelRequest(parts=[UserPromptPart('Continue.')]))
            assert await a.prepare_tool_for_request(ctx, ToolDefinition(name='edit_file')) is None
    asyncio.run(check_unscoped_readonly())
assert read_only_policy('Ne modifie pas les fichiers.', True)
assert read_only_policy('Continue.', True)
assert not read_only_policy('Corrige app.py sans modifier les tests', True)
assert not read_only_policy('Now implement the fix', True)

# The unmodified evaluator prompt authorizes a check even when no edit is needed.
# Its requested final report must not silently turn that task into read-only.
no_op_prompt = ('Laboratoire autorisé : mets le port à 12345 dans current.json et PORT à 12345 dans app.py. '
    'Préserve les données utilisateur, les autres fichiers et les contrôles de référence. '
    'Vérifie le résultat avec les contrôles existants. Réponse française concise. '
    'Si les valeurs sont déjà correctes, vérifie et indique Aucune modification effectuée.')
assert not read_only_policy(no_op_prompt)
assert read_only_policy(no_op_prompt, True)  # A report alone cannot lift an earlier restriction.
for report_prompt in ('Vérifie puis indique « Aucune modification effectuée ».',
                      'Check the result and report "No changes made".'):
    assert not read_only_policy(report_prompt)
    assert read_only_policy(report_prompt, True)
for restriction in ('Aucune modification.', 'Aucune modification autorisée.',
                    'Lecture seule. Indique « Aucune modification effectuée ».',
                    'Read only. Report "No changes made".'):
    assert read_only_policy(restriction)
async def check_no_op_tools():
    ctx = SimpleNamespace(prompt=no_op_prompt, messages=[ModelRequest(parts=[UserPromptPart(no_op_prompt)])])
    with patch.object(a, 'TASK_ENGINE', None), patch.object(a, 'ACTIVE_TOOLS', a.ToolSelection(no_op_prompt)):
        assert await a.prepare_tool_for_request(ctx, ToolDefinition(name='verify_changes')) is not None
asyncio.run(check_no_op_tools())

with tempfile.TemporaryDirectory() as readonly_folder:
    workspace = Path(readonly_folder) / 'project'
    workspace.mkdir()
    state = Path(readonly_folder) / 'state'
    engine = WorkflowController(workspace, state, allow_external=True)
    engine.focus('Lecture seule : audite ce dépôt.')
    engine.focus('Continue.')
    engine = WorkflowController(workspace, state, allow_external=True)
    engine.focus('Poursuis la lecture.')
    assert engine.read_only
    called = []
    def write_file(path: str, content: str) -> str:
        """Write the temporary fixture when explicitly authorized."""
        called.append(path)
        (workspace / path).write_text(content)
        return 'Fichier écrit.'
    wrapped = guarded_tool(write_file, lambda: engine)
    assert json.loads(wrapped('blocked.txt', 'forbidden'))['category'] == 'read_only'
    assert not called and not (workspace / 'blocked.txt').exists()
    for tool_name in ('edit_file_batch', 'execute_operation', 'run_host_command', 'install_tools', 'verify_changes'):
        def forbidden_mutator():
            raise AssertionError('A read-only mutation was executed')
        forbidden_mutator.__name__ = tool_name
        assert json.loads(engine.invoke(forbidden_mutator))['executed'] is False
    for use_prepare in (True, False):
        rounds = []
        def readonly_model(messages, info):
            rounds.append(1)
            if len(rounds) == 1:
                assert ('write_file' in {tool.name for tool in info.function_tools}) is not use_prepare
                # Deliberately request the hidden tool or a stale advertised tool.
                return ModelResponse(parts=[ToolCallPart('write_file', {'path': 'blocked.txt', 'content': 'forbidden'})])
            if not use_prepare:
                returned = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
                assert json.loads(returned[-1].content)['category'] == 'read_only'
            return ModelResponse(parts=[TextPart('No changes made.')])
        tool = a.Tool(wrapped, prepare=a.prepare_tool_for_request if use_prepare else None)
        with patch.object(a, 'TASK_ENGINE', engine), patch.object(a, 'ACTIVE_TOOLS', a.ToolSelection('Lecture seule')):
            result = Agent(FunctionModel(readonly_model), tools=[tool], retries=1).run_sync('Lecture seule : audite ce dépôt.')
        assert result.output == 'No changes made.' and not called and not (workspace / 'blocked.txt').exists()
    engine.focus('Oui, modifie maintenant authorized.txt')
    assert not engine.read_only
    assert wrapped('authorized.txt', 'explicitly allowed') == 'Fichier écrit.'
    assert (workspace / 'authorized.txt').read_text() == 'explicitly allowed'
    engine.focus('Ne modifie pas les fichiers.')
    assert engine.read_only
print('PASS: persistent read-only execution authorization, hidden/stale SDK tools and explicit write steering')
