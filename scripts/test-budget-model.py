#!/opt/ovh-agent/bin/python
"""Exercise real Pydantic model/tool/stream boundaries without calling a provider."""
import asyncio
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, PartStartEvent, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.usage import RequestUsage
from ovh_budget import BudgetLedger, BudgetExceeded
from ovh_budget_model import BudgetedModel, NormalizedStream
from ovh_budget_model import normalize_tool_call_names

# Some OVH chat templates occasionally append an OpenAI channel marker to a
# function name. Strip it only when the base name is an advertised tool.
parameters = ModelRequestParameters(function_tools=[ToolDefinition(name='read_file', parameters_json_schema={})])
malformed = ModelResponse(parts=[ToolCallPart('read_file<|channel|>commentary', {})])
assert normalize_tool_call_names(malformed, parameters).parts[0].tool_name == 'read_file'
unknown = ModelResponse(parts=[ToolCallPart('not_advertised<|channel|>commentary', {})])
assert normalize_tool_call_names(unknown, parameters).parts[0].tool_name == 'not_advertised<|channel|>commentary'

class MalformedStream:
    async def __aiter__(self):
        yield PartStartEvent(index=0, part=ToolCallPart('read_file<|channel|>commentary', {'path': 'README.md'}, tool_call_id='call-1'))
    def get(self):
        return malformed

async def streamed_tool_name():
    stream = NormalizedStream(MalformedStream(), parameters)
    events = [event async for event in stream]
    assert events[0].part.tool_name == 'read_file'
    assert events[0].part.args == {'path': 'README.md'}
    assert events[0].part.tool_call_id == 'call-1'
    assert stream.get().parts[0].tool_name == 'read_file'

asyncio.run(streamed_tool_name())

card = dict(currency='EUR', input_per_million=1, output_per_million=2, context_length=1000)
settings = {'max_tokens': 50}
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    ledger = BudgetLedger(root/'usage.json', {'daily': '.0012', 'weekly': 15, 'monthly': 60})
    events, calls = [], []
    def lookup():
        """Return a synthetic value."""
        return '42'
    def model(messages, info):
        calls.append(messages)
        parts = [ToolCallPart('lookup', {}, tool_call_id='one')] if len(calls) == 1 else [TextPart('Done')]
        return ModelResponse(parts=parts, usage=RequestUsage(input_tokens=100, output_tokens=20))
    wrapped = BudgetedModel(FunctionModel(model), lambda: ledger, lambda _: card, events.append)
    agent = Agent(wrapped, tools=[lookup])
    assert agent.run_sync('Inspect.', model_settings=settings).output == 'Done'
    assert len(calls) == 2
    state = ledger.status()
    assert state['periods']['daily']['spent'] == .00028
    assert state['periods']['daily']['reserved'] == 0
    try:
        agent.run_sync('Another request.', model_settings=settings)
        raise AssertionError('Call crossed the available reservation budget')
    except BudgetExceeded:
        pass
    assert len(calls) == 2 and events[-1]['blocked']
    # Reusing this same model for a separate summarizer cannot bypass the budget.
    try:
        Agent(wrapped).run_sync('Summarize.', model_settings=settings)
        raise AssertionError('Summary bypassed budget')
    except BudgetExceeded:
        pass

    async def stream(messages, info):
        yield 'Streaming result.'
    streaming_ledger = BudgetLedger(root/'stream.json', {'daily': 2})
    streaming = BudgetedModel(FunctionModel(stream_function=stream), lambda: streaming_ledger, lambda _: card, events.append)
    async def consume(model):
        async with Agent(model).run_stream('Hello', model_settings=settings) as result:
            return await result.get_output()
    assert asyncio.run(consume(streaming)) == 'Streaming result.'
    assert 0 < streaming_ledger.status()['periods']['daily']['spent'] < .00105
    assert streaming_ledger.status()['pending_requests'] == 0
    async def failed_stream(messages, info):
        yield 'Partial'
        raise OSError('transport interrupted')
    uncertain = BudgetedModel(FunctionModel(stream_function=failed_stream), lambda: streaming_ledger, lambda _: card, events.append)
    before = Decimal(str(streaming_ledger.status()['periods']['daily']['spent']))
    try:
        asyncio.run(consume(uncertain))
        raise AssertionError('Expected transport interruption')
    except OSError:
        pass
    after = Decimal(str(streaming_ledger.status()['periods']['daily']['spent']))
    assert after - before == Decimal('.00105')
    # No price must never be interpreted as free.
    missing = BudgetedModel(FunctionModel(model), lambda: ledger, lambda _: {}, events.append)
    try:
        Agent(missing).run_sync('No price.', model_settings=settings)
        raise AssertionError('Missing price was accepted')
    except ValueError as error:
        assert 'tarif' in str(error)
    # Known free model still works when paid budget cannot fit another call.
    free = BudgetedModel(FunctionModel(model), lambda: ledger,
                         lambda _: {**card, 'input_per_million': 0, 'output_per_million': 0, 'free': True},
                         events.append)
    assert Agent(free).run_sync('Free.', model_settings=settings).output == 'Done'

    # USD prices are rejected, never relabelled or converted into euros.
    dollars = BudgetedModel(FunctionModel(model), lambda: ledger,
                            lambda _: {**card, 'currency': 'USD'}, events.append)
    try:
        Agent(dollars).run_sync('Wrong currency.', model_settings=settings)
        raise AssertionError('USD price accepted as EUR')
    except ValueError:
        pass
print('PASS: per-request limits, summaries, streams, uncertain charges, native EUR prices and USD rejection')

# Provider observations remain separate from durable local request accounting.
from ovh_budget import OvhBalanceLedger, BudgetLedgerError
with tempfile.TemporaryDirectory() as directory:
    now = datetime.now(timezone.utc)
    snapshot = dict(available=True, currency='EUR', credit_currency='EUR', project='fixture',
                    current_usage=2.1, credit_available=200, last_update=now.isoformat(),
                    cached_at=now.timestamp(), usage_period={'from':now.replace(day=1,hour=0,minute=0,second=0,microsecond=0).isoformat()})
    root = Path(directory)
    ledger = OvhBalanceLedger.from_provider(root/'ovh.json', {'daily':8,'monthly':197}, root/'policy.json', lambda:snapshot, now=lambda:now)
    def failed_provider(messages, info):
        raise OSError('fixture transport failed')
    wrapped = BudgetedModel(FunctionModel(failed_provider), lambda:ledger, lambda _:card, lambda _:None)
    try:
        Agent(wrapped).run_sync('Fixture only.', model_settings=settings)
        raise AssertionError('Expected provider error')
    except OSError:
        pass
    state=ledger.status()
    assert state['pending_requests']==0 and state['periods']['daily']['spent']==0
    assert state['periods']['monthly']['spent']==2.1 and state['provider_balance']==197.9
    assert state['periods']['daily']['local_spent']==.00105
    token=ledger.reserve('.001')
    ledger.settle(token,'50')
    assert ledger.status()['periods']['monthly']['spent']==2.1
    assert ledger.status()['periods']['daily']['accounted_spent']==50.00105
    assert ledger.status()['blocked']

    # Failure after reservation, before any model call, cancels instead of
    # charging an uncertain request. Exercise both request entry points.
    for stream_mode in (False, True):
        provider_reads=[];model_calls=[]
        def billing():
            provider_reads.append(True)
            return snapshot if len(provider_reads)==1 else {'available':False}
        pending=OvhBalanceLedger.from_provider(root/('orphan-'+str(stream_mode)+'.json'),
            {'daily':8,'monthly':197},root/'absent.json',billing,now=lambda:now)
        def tracked(messages,info):
            model_calls.append(True)
            return ModelResponse(parts=[TextPart('unexpected')])
        async def tracked_stream(messages,info):
            model_calls.append(True)
            yield 'unexpected'
        guarded=BudgetedModel(FunctionModel(tracked,stream_function=tracked_stream),
            lambda:pending,lambda _:card,lambda _:None)
        try:
            if stream_mode:
                asyncio.run(consume(guarded))
            else:
                Agent(guarded).run_sync('Fixture only.',model_settings=settings)
            raise AssertionError('Expected preflight billing failure')
        except BudgetLedgerError:
            pass
        data=json.loads(pending.path.read_text())
        assert not model_calls and data['reservations']=={}
        assert data['ovh']['local']['spent']['daily'][now.date().isoformat()]=='0'

    callback_ledger=BudgetLedger(root/'callback.json',{'daily':1})
    def broken_ui(_):
        raise RuntimeError('fixture UI unavailable')
    guarded=BudgetedModel(FunctionModel(model),lambda:callback_ledger,lambda _:card,broken_ui)
    try:
        guarded.begin(settings)
        raise AssertionError('Expected UI callback failure')
    except RuntimeError:
        pass
    assert callback_ledger.status()['pending_requests']==0
    assert callback_ledger.status()['periods']['daily']['spent']==0
print('Delayed provider accounting, uncertain local charges and pre-send reservation cleanup: OK')

# Billing evidence survives run failure, instead of depending on a final answer.
from pydantic_ai.usage import UsageLimits
from pydantic_ai.exceptions import UsageLimitExceeded
with tempfile.TemporaryDirectory() as directory:
    ledger = BudgetLedger(Path(directory)/'receipts.json', {'daily': 1})
    receipts, responses = [], []
    def record(receipt, response):
        receipts.append(receipt)
        responses.append(response)
    def looping(messages, info):
        return ModelResponse(parts=[ToolCallPart('lookup', {}, tool_call_id=str(len(receipts)))],
                             usage=RequestUsage(input_tokens=100, output_tokens=20))
    metered = BudgetedModel(FunctionModel(looping), lambda:ledger, lambda _:card, lambda _:None, record=record)
    try:
        Agent(metered, tools=[lookup]).run_sync('Fixture loop', model_settings=settings,
                                              usage_limits=UsageLimits(request_limit=2))
        raise AssertionError('Expected SDK request exhaustion')
    except UsageLimitExceeded:
        pass
    assert len(receipts) == 2 and len({r['request_id'] for r in receipts}) == 2
    assert all(r['usage_known'] and r['completed'] for r in receipts)
    assert sum(r['cost_eur'] for r in receipts) == .00028
    assert all(r is not None for r in responses)
    receipts.clear()
    def final(messages, info):
        return ModelResponse(parts=[TextPart('Done')], usage=RequestUsage(input_tokens=100, output_tokens=20))
    agent = Agent(BudgetedModel(FunctionModel(final), lambda:ledger, lambda _:card, lambda _:None, record=record))
    @agent.output_validator
    def failed_validation(output):
        raise RuntimeError('Verification failed after paid generation')
    try:
        agent.run_sync('Fixture validation', model_settings=settings)
        raise AssertionError('Expected validation failure')
    except RuntimeError:
        pass
    assert len(receipts) == 1 and receipts[0]['cost_eur'] == .00014
    receipts.clear()
    interrupted = BudgetedModel(FunctionModel(stream_function=failed_stream), lambda:ledger, lambda _:card,
                                lambda _:None, record=record)
    try:
        asyncio.run(consume(interrupted))
        raise AssertionError('Expected interrupted stream')
    except OSError:
        pass
    assert len(receipts) == 1 and receipts[0]['cost_eur'] is None
    assert receipts[0]['completed'] is False and receipts[0]['usage_known'] is False
    assert receipts[0]['reserved_upper_bound_eur'] == .00105
print('PASS: durable request receipts on request exhaustion, failed validation and interrupted stream')


# Failed-attempt summaries include only costs not already shown by vision,
# compaction or a successful primary model before fallback.
import importlib.util
with patch.object(sys, 'argv', ['request-receipt-test', '--billing-status']):
    spec = importlib.util.spec_from_file_location('request_receipt_backend', Path(__file__).resolve().parents[1]/'ovh-agent.py')
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
with tempfile.TemporaryDirectory() as directory:
    folder = Path(directory)
    backend.run_dependencies().request_receipts.clear(); backend.run_dependencies().usage_reported.clear()
    events = []
    original_emit = backend.emit
    def capture(event):
        events.append(dict(event)); original_emit(event)
    with patch.object(backend, 'JSON_MODE', False), patch.object(backend, 'emit', side_effect=capture), \
         patch.object(backend, 'conversation_dir', return_value=folder), patch.object(backend, 'ensure_conversation'), \
         patch.object(backend, 'record_model_usage', side_effect=lambda model,i,o:(i+o*2)/1e6):
        for i in range(2):
            backend.record_provider_request({'request_id':str(i),'model':'fixture','input_tokens':100,
                'output_tokens':20,'cost_eur':.00014,'usage_known':True,'reserved_upper_bound_eur':.00105}, None)
        backend.emit({'type':'usage','model':'fixture','input_tokens':100,'output_tokens':20,'requests':1,'cost_eur':.00014})
        backend.report_unreported_usage()
        backend.report_unreported_usage()
    reports=[e for e in events if e.get('purpose')=='interrupted_attempt']
    assert len(reports)==1 and reports[0]['requests']==1 and reports[0]['cost_eur']==.00014
    saved=[json.loads(line) for line in (folder/'model-requests.jsonl').read_text().splitlines()]
    assert len(saved)==2 and saved[1]['request_id']=='1'
print('PASS: request journal persists independently; interrupted costs are reported once without duplicate totals')

# OVH's authenticated project/model rate limit must cover tools, summaries and
# streams across processes, independently of financial budget reservations.
from ovh_budget_model import OVHRateLimiter
from pydantic_ai.exceptions import ModelHTTPError
from unittest.mock import AsyncMock, patch
from contextlib import asynccontextmanager
from email.utils import format_datetime
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
import subprocess

async def rate_limit_regressions(root):
    now = [1000.0]
    async def advance(delay):
        now[0] += delay
    gate = OVHRateLimiter(root/'rate.sqlite3', 'project-a', clock=lambda: now[0], sleep=advance)
    other = OVHRateLimiter(root/'rate.sqlite3', 'project-a', clock=lambda: now[0], sleep=advance)
    assert gate._slot('model-a') == 0
    assert abs(other._slot('model-a') - .15) < 1e-8
    assert other._slot('model-b') == 0
    assert OVHRateLimiter(root/'rate.sqlite3', 'project-b', clock=lambda: now[0])._slot('model-a') == 0
    for _ in range(400):
        await gate.wait('model-a')
    assert now[0] >= 1059.999999  # 401 requests span at least one minute.

    rejected = ModelHTTPError(429, 'test', {'error': 'rate limit'})
    cause = RuntimeError('provider rejection')
    cause.status_code = 429
    class Response:
        status_code = 429
        headers = {'retry-after': '7'}
    cause.response = Response()
    rejected.__cause__ = cause
    assert await gate.rejected('retry', rejected, 1)
    assert abs(gate._slot('retry') - 7) < 1e-8
    cause.response.headers = {'retry-after': format_datetime(datetime.fromtimestamp(now[0], timezone.utc) + timedelta(seconds=20))}
    assert await gate.rejected('date', rejected, 1)
    assert 19 <= gate._slot('date') <= 20
    cause.response.headers = {'retry-after': '600'}
    assert not await gate.rejected('long', rejected, 1)
    assert abs(gate._slot('long') - 600) < 1e-8  # Persist, never shorten server cooldown.
    cause.response.headers = {'retry-after': 'invalid'}
    assert await gate.rejected('invalid', rejected, 1)
    assert gate._slot('invalid') == 2
    assert not await gate.rejected('plain-error', RuntimeError('HTTP 429 quota in prose'), 1)
    assert not await gate.rejected('last-attempt', rejected, 3)

    # Competing independent instances serialize their slots.
    with ThreadPoolExecutor(max_workers=8) as workers:
        outcomes = list(workers.map(lambda _: other._slot('concurrent'), range(16)))
    assert outcomes.count(0) == 1
    command = [sys.executable, '-c',
        'from ovh_budget_model import OVHRateLimiter; import sys; '
        'print(OVHRateLimiter(sys.argv[1], "project-a", clock=lambda: 1000.0)._slot("cross-process"))',
        str(root/'rate.sqlite3')]
    first = subprocess.Popen(command, stdout=subprocess.PIPE, text=True)
    second = subprocess.Popen(command, stdout=subprocess.PIPE, text=True)
    results = [float(first.communicate()[0]), float(second.communicate()[0])]
    assert first.returncode == second.returncode == 0 and results.count(0) == 1

    ledger = BudgetLedger(root/'retry-budget.json', {'daily': 1})
    records = []
    response = ModelResponse(parts=[TextPart('ok')], usage=RequestUsage(input_tokens=100, output_tokens=20))
    inner = FunctionModel(lambda messages, info: response)
    limiter = OVHRateLimiter(root/'attempts.sqlite3', 'project-a', clock=lambda: now[0], sleep=advance)
    outer = BudgetedModel(inner, lambda: ledger, lambda _: card, lambda _: None,
                          record=lambda event, response: records.append(event), rate_limiter=limiter)
    no_header = ModelHTTPError(429, 'test', {})
    with patch.object(inner, 'request', AsyncMock(side_effect=[no_header, no_header, response])) as request:
        assert await outer.request([], settings, ModelRequestParameters()) == response
        assert request.await_count == 3
    assert len(records) == 3 and [r['completed'] for r in records] == [False, False, True]
    assert not records[0]['usage_known'] and not records[1]['usage_known']
    assert ledger.status()['periods']['daily']['reserved'] == 0
    assert abs(ledger.status()['periods']['daily']['spent'] - .00224) < 1e-9
    with patch.object(inner, 'request', AsyncMock(side_effect=RuntimeError('transport'))) as request:
        try:
            await outer.request([], settings, ModelRequestParameters())
            raise AssertionError('transport error swallowed')
        except RuntimeError as error:
            assert str(error) == 'transport'
        assert request.await_count == 1

    from openai import APIConnectionError
    import httpx2 as httpx
    disconnected = APIConnectionError(request=httpx.Request('POST', 'https://example.invalid'))
    with patch.object(inner, 'request', AsyncMock(side_effect=[disconnected, disconnected, response])) as request:
        assert await outer.request([], settings, ModelRequestParameters()) == response
        assert request.await_count == 3
    with patch.object(inner, 'request', AsyncMock(side_effect=disconnected)) as request:
        try:
            await outer.request([], settings, ModelRequestParameters())
            raise AssertionError('unbounded transport retry')
        except APIConnectionError:
            pass
        assert request.await_count == 3

    class Stream:
        def get(self): return response
    stream_calls = []
    @asynccontextmanager
    async def opening(*args):
        stream_calls.append(1)
        if len(stream_calls) == 1:
            raise no_header
        yield Stream()
    with patch.object(inner, 'request_stream', opening):
        async with outer.request_stream([], settings, ModelRequestParameters()) as stream:
            assert stream.get() == response
    assert len(stream_calls) == 2
    @asynccontextmanager
    async def opened(*args):
        stream_calls.append(1)
        yield Stream()
    before = len(stream_calls)
    with patch.object(inner, 'request_stream', opened):
        try:
            async with outer.request_stream([], settings, ModelRequestParameters()):
                raise disconnected  # No replay once the stream has reached its consumer.
        except APIConnectionError:
            pass
    assert len(stream_calls) == before + 1

    waiting = asyncio.Event()
    async def blocked_sleep(delay):
        waiting.set()
        await asyncio.Event().wait()
    blocked = OVHRateLimiter(root/'cancel.sqlite3', 'project-a', clock=lambda: now[0], sleep=blocked_sleep)
    blocked._slot(inner.model_name, 60)
    untouched = BudgetLedger(root/'cancel-budget.json', {'daily': 1})
    cancellable = BudgetedModel(inner, lambda: untouched, lambda _: card, lambda _: None, rate_limiter=blocked)
    task = asyncio.create_task(cancellable.request([], settings, ModelRequestParameters()))
    await waiting.wait()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert untouched.status()['periods']['daily']['spent'] == 0
    assert untouched.status()['periods']['daily']['reserved'] == 0

with tempfile.TemporaryDirectory() as directory:
    asyncio.run(rate_limit_regressions(Path(directory)))
print('OVH pacing, cross-process sharing, 429/Retry-After, bounded retries, streams and cancellation: OK')
