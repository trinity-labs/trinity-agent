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
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage
from ovh_budget import BudgetLedger, BudgetExceeded
from ovh_budget_model import BudgetedModel

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
