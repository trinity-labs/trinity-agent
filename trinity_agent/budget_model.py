"""Budget enforcement immediately around every model request, including streams."""
from contextlib import asynccontextmanager, closing
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from datetime import datetime, timezone
import re
import asyncio
from openai import APIConnectionError
import hashlib
import math
from pathlib import Path
import sqlite3
import time
from email.utils import parsedate_to_datetime
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.messages import PartDeltaEvent, PartEndEvent, PartStartEvent, ToolCallPart, ToolCallPartDelta


_CHANNEL_SUFFIX = re.compile(r'<\|channel\|>(?:analysis|commentary|final)$')


def normalize_tool_call_names(response, model_request_parameters):
    """Repair an OVH chat-template channel suffix only for advertised tools."""
    available = {
        definition.name
        for definition in (
            *model_request_parameters.function_tools,
            *model_request_parameters.output_tools,
        )
    }
    changed = False
    parts = []
    for part in response.parts:
        if isinstance(part, ToolCallPart):
            base = _CHANNEL_SUFFIX.sub('', part.tool_name)
            if base != part.tool_name and base in available:
                part = replace(part, tool_name=base)
                changed = True
        parts.append(part)
    return replace(response, parts=parts) if changed else response


def _available_tool_names(model_request_parameters):
    return {
        definition.name
        for definition in (
            *model_request_parameters.function_tools,
            *model_request_parameters.output_tools,
        )
    }


def _normalize_tool_name(name, available):
    base = _CHANNEL_SUFFIX.sub('', name)
    return base if base != name and base in available else name


def normalize_stream_event(event, model_request_parameters):
    """Repair malformed tool names before the agent consumes stream events."""
    available = _available_tool_names(model_request_parameters)
    if isinstance(event, (PartStartEvent, PartEndEvent)) and isinstance(event.part, ToolCallPart):
        name = _normalize_tool_name(event.part.tool_name, available)
        if name != event.part.tool_name:
            return replace(event, part=replace(event.part, tool_name=name))
    if isinstance(event, PartDeltaEvent) and isinstance(event.delta, ToolCallPartDelta):
        name = event.delta.tool_name_delta
        if name:
            normalized = _normalize_tool_name(name, available)
            if normalized != name:
                return replace(event, delta=replace(event.delta, tool_name_delta=normalized))
    return event


class NormalizedStream:
    """Transparent stream proxy that sanitizes provider tool-call names."""
    def __init__(self, stream, model_request_parameters):
        self._stream = stream
        self._parameters = model_request_parameters

    def __getattr__(self, name):
        return getattr(self._stream, name)

    def __aiter__(self):
        async def events():
            async for event in self._stream:
                yield normalize_stream_event(event, self._parameters)
        return events()

    def get(self):
        return normalize_tool_call_names(self._stream.get(), self._parameters)

    async def cancel(self):
        return await self._stream.cancel()


def transport_failure(error):
    """Recognize SDK connection failures through model wrappers, not prose."""
    seen = set()
    while isinstance(error, BaseException) and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, APIConnectionError):
            return True
        error = error.__cause__ or error.__context__
    return False


class IncompleteModelResponseError(RuntimeError):
    """Provider ended a turn with internal reasoning but no usable output."""


def require_actionable_response(response):
    actionable = {'text', 'tool-call', 'builtin-tool-call', 'file', 'data'}
    if not any(getattr(part, 'part_kind', '') in actionable for part in response.parts):
        state = getattr(response, 'state', None)
        finish = getattr(response, 'finish_reason', None)
        raise IncompleteModelResponseError(
            f'Réponse modèle incomplète ({state or "état inconnu"}, fin={finish or "absente"}) : '
            'raisonnement interne reçu sans texte ni appel d’outil.')
    return response


class ProviderRateLimiter:
    """Share OVH project/model pacing and 429 cooldowns across local processes.

    Authenticated AI Endpoints allow 400 requests/minute/project/model. A paced
    slot avoids bursts; SQLite serializes callers without holding a lock asleep.
    Only a hash of the project (or credential fallback) is stored, never a key.
    """
    def __init__(self, path, scope, notify=None, *, clock=time.time, sleep=asyncio.sleep):
        self.path = Path(path)
        self.scope = hashlib.sha256(scope.encode()).hexdigest()
        self.notify = notify
        self.clock = clock
        self.sleep = sleep

    def _slot(self, model, cooldown=None):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS limits '
                       '(scope TEXT, model TEXT, next_at REAL, PRIMARY KEY(scope,model))')
            db.execute('BEGIN IMMEDIATE')
            now = self.clock()
            row = db.execute('SELECT next_at FROM limits WHERE scope=? AND model=?',
                             (self.scope, model)).fetchone()
            next_at = row[0] if row else 0.0
            if not math.isfinite(next_at):
                raise ValueError('Invalid OVH rate-limit state')
            if cooldown is not None:
                next_at = max(next_at, now + cooldown)
            elif next_at <= now:
                next_at = now + 60.0 / 400
                db.execute('INSERT OR REPLACE INTO limits VALUES (?,?,?)',
                           (self.scope, model, next_at))
                return 0.0
            else:
                return next_at - now
            db.execute('INSERT OR REPLACE INTO limits VALUES (?,?,?)',
                       (self.scope, model, next_at))
            return max(0.0, next_at - now)

    async def wait(self, model):
        notified = False
        while True:
            delay = await asyncio.to_thread(self._slot, model)
            if delay <= 0:
                return
            if delay >= 1 and not notified and self.notify:
                self.notify(model, delay)
                notified = True
            await self.sleep(min(delay, 30.0))

    async def rejected(self, model, error, attempt):
        # HTTP status is evidence; prose such as "quota" is not.
        cause, seen, limited, retry_after = error, set(), False, None
        while cause is not None and id(cause) not in seen:
            seen.add(id(cause))
            if getattr(cause, 'status_code', None) == 429:
                limited = True
            response = getattr(cause, 'response', None)
            if response is not None and getattr(response, 'status_code', None) == 429:
                limited = True
                retry_after = response.headers.get('retry-after')
            cause = cause.__cause__
        if not limited:
            if not transport_failure(error) or attempt >= 3:
                return False
            # Retry only the unexposed provider request, never an agent turn.
            # The SDK has retries disabled; each attempt keeps its own budget.
            await self.sleep(2.0 ** (attempt - 1))
            return True
        delay = min(60.0, 2.0 ** attempt)
        if retry_after:
            try:
                parsed = float(retry_after)
            except ValueError:
                try:
                    parsed = parsedate_to_datetime(retry_after).timestamp() - self.clock()
                except (ValueError, TypeError, OverflowError):
                    parsed = delay
            if math.isfinite(parsed) and parsed >= 0:
                delay = max(delay, parsed)
        await asyncio.to_thread(self._slot, model, delay)
        # Persist long cooldowns, but do not keep a single task waiting forever.
        return attempt < 3 and delay <= 300


class BudgetedModel(WrapperModel):
    def __init__(self, model, ledger, catalog_card, publish, record=None, rate_limiter=None):
        super().__init__(model)
        self.ledger_factory = ledger
        self.catalog_card = catalog_card
        self.publish = publish
        self.record = record
        self.rate_limiter = rate_limiter

    def begin(self, settings):
        card = self.catalog_card(self.model_name)
        try:
            currency = str(card['currency']).upper()
            incoming = Decimal(str(card['input_per_million'])) / 1000000
            outgoing = Decimal(str(card['output_per_million'])) / 1000000
            window = int(card['context_length'])
            output = int((settings or {})['max_tokens'])
            if currency != 'EUR' or window <= 0 or not 0 < output <= window:
                raise ValueError('Paramètres de coût inconnus')
            if not all(p.is_finite() and p >= 0 for p in (incoming, outgoing)):
                raise ValueError('Tarifs invalides')
            if not (incoming or outgoing) and not card.get('free'):
                raise ValueError('Tarif absent, modèle non déclaré gratuit')
        except (KeyError, TypeError, ValueError, InvalidOperation) as error:
            raise ValueError('Budget : tarif ou limite du modèle inconnu ; appel bloqué.') from error
        # Upper bound for any accepted request within the advertised model window.
        # It does not rely on the approximate context tokenizer or image estimator.
        maximum = window * incoming + output * max(Decimal(0), outgoing - incoming)
        ledger = self.ledger_factory()
        try:
            reservation = ledger.reserve(maximum, metadata={
                'model': self.model_name, 'currency': currency,
                'pricing_source': card.get('pricing_source', ''),
            })
        except Exception as error:
            if hasattr(error, 'status'):
                self.publish(error.status)
            raise
        try:
            self.publish(ledger.status())
        except BaseException:
            # No model request has started. A failed status refresh or UI
            # callback must not leave a permanent charge/reservation behind.
            ledger.settle(reservation, Decimal(0))
            raise
        return ledger, reservation, incoming, outgoing, maximum

    def finish(self, reservation, response=None, *, completed=True):
        ledger, identifier, incoming, outgoing, maximum = reservation
        amount = None
        if response is not None and completed:
            usage = response.usage
            # Missing usage is not evidence that a paid request was free.
            if maximum == 0 or usage.input_tokens > 0 or usage.output_tokens > 0:
                amount = Decimal(usage.input_tokens) * incoming + Decimal(usage.output_tokens) * outgoing
        try:
            status = ledger.settle(identifier, amount)
        finally:
            # Persist per provider request, even when the run, output validator,
            # UI publisher or a later tool fails. Unknown usage is never zero.
            if self.record is not None:
                self.record({
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'request_id': identifier, 'model': self.model_name,
                    'provider_response_id': getattr(response, 'provider_response_id', None),
                    'input_tokens': response.usage.input_tokens if amount is not None else None,
                    'output_tokens': response.usage.output_tokens if amount is not None else None,
                    'cost_eur': float(amount) if amount is not None else None,
                    'reserved_upper_bound_eur': float(maximum),
                    'usage_known': amount is not None, 'completed': completed,
                    'currency': 'EUR',
                }, response)
        self.publish(status)

    async def request(self, messages, model_settings, model_request_parameters):
        for attempt in range(1, 4):
            if self.rate_limiter:
                await self.rate_limiter.wait(self.model_name)
            reservation = self.begin(model_settings)
            response = None
            retry = False
            try:
                response = await self.wrapped.request(messages, model_settings, model_request_parameters)
                response = normalize_tool_call_names(response, model_request_parameters)
                return require_actionable_response(response)
            except Exception as error:
                if self.rate_limiter:
                    retry = await self.rate_limiter.rejected(self.model_name, error, attempt)
                if not retry:
                    raise
            finally:
                # Each attempt has its own reservation. Unknown usage remains
                # conservative; a rejected request is never invented as free.
                self.finish(reservation, response, completed=response is not None)

    @asynccontextmanager
    async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):
        for attempt in range(1, 4):
            if self.rate_limiter:
                await self.rate_limiter.wait(self.model_name)
            reservation = self.begin(model_settings)
            response, completed, entered = None, False, False
            try:
                async with self.wrapped.request_stream(messages, model_settings, model_request_parameters, run_context) as stream:
                    entered = True
                    normalized = NormalizedStream(stream, model_request_parameters)
                    try:
                        yield normalized
                        completed = True
                    finally:
                        response = normalized.get()
                        require_actionable_response(response)
                return
            except Exception as error:
                # Once exposed, a stream may have emitted text/tool calls.
                # Replaying it could duplicate actions; only retry failures before exposure.
                if entered or not self.rate_limiter or not await self.rate_limiter.rejected(self.model_name, error, attempt):
                    raise
            finally:
                self.finish(reservation, response, completed=completed)
