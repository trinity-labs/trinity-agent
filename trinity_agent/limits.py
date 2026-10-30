"""Finite, model-aware context allowances. No I/O, allocation or monetary policy.

``auto`` and an omitted value compute an allowance from the current model and
task. Integers are optional ceilings. Unknown metadata uses an explicitly
identified conservative window; it never advertises a discovered capability.
"""
from dataclasses import dataclass
from math import isqrt
import os

UNKNOWN_WINDOW = 32768
MAX_KNOWN_WINDOW = 1048576  # Bound malformed/future catalogue metadata.
NAMES = (
    'CONTEXT_TOKENS', 'OUTPUT_TOKENS', 'COMPLEX_OUTPUT_TOKENS', 'SUMMARY_TOKENS',
    'SUMMARY_SOURCE_CHARS', 'SUMMARY_GENERATION_TOKENS', 'TOOL_OUTPUT_CHARS',
    'MEMORY_CHARS', 'SHARED_MEMORY_CHARS', 'PREFLIGHT_CONTEXT_CHARS',
)


def limit_setting(name):
    """Return None for automatic sizing; reject malformed explicit settings."""
    value = os.environ.get(name, 'auto').strip()
    if value in {'', 'auto'}:
        return None
    if not value.isascii() or not value.isdigit():
        raise ValueError(f'{name} : auto ou un plafond entier positif attendu.')
    return int(value)


def _positive(value):
    try:
        number = int(value)
        return number if not isinstance(value, bool) and number > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


@dataclass(frozen=True)
class ContextLimits:
    window_tokens: int
    provider_window_known: bool
    reserve_tokens: int
    context_tokens: int
    output_tokens: int
    summary_tokens: int
    summary_source_chars: int
    summary_generation_tokens: int
    tool_output_chars: int
    memory_chars: int
    shared_memory_chars: int
    preflight_context_chars: int


def resolve_limits(card=None, *, overhead=0, complex_task=False, settings=None, context_budget=None):
    card = card or {}
    settings = settings or {}
    provider_limits_only = card.get('provider') == 'deepseek'
    def ceiling(short, automatic):
        name = 'TRINITY_AGENT_' + short
        value = None if provider_limits_only else settings[name] if name in settings else limit_setting(name)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f'{name} : plafond entier positif attendu.')
        return max(0, automatic if value is None else min(value, automatic))
    advertised = _positive(card.get('context_length'))
    known = advertised is not None and advertised <= MAX_KNOWN_WINDOW
    window = advertised if known else UNKNOWN_WINDOW
    reserve = settings.get('TRINITY_AGENT_CONTEXT_RESERVE_TOKENS')
    if reserve is None:
        raw = os.environ.get('TRINITY_AGENT_CONTEXT_RESERVE_TOKENS', '4096')
        if not str(raw).isascii() or not str(raw).isdigit():
            raise ValueError('TRINITY_AGENT_CONTEXT_RESERVE_TOKENS : entier attendu.')
        reserve = int(raw)
    available = max(0, window - max(0, reserve) - max(0, int(overhead)))
    maximum = _positive(card.get('max_completion_tokens'))
    if provider_limits_only:
        reserve = 0
        available = max(0, window - max(0, int(overhead)))
    # Keep input room, including on small models; output is a ceiling, never a
    # target length. Unknown completion metadata does not grant a huge response.
    automatic_output = window // (8 if complex_task else 16)
    output_bound = min(automatic_output, maximum or (2048 if complex_task else 1024),
                       window // 3, max(0, available - 2048))
    if provider_limits_only:
        output_bound = min(maximum or automatic_output, max(0, available - 1))
    output = ceiling('COMPLEX_OUTPUT_TOKENS' if complex_task else 'OUTPUT_TOKENS', output_bound)
    context = ceiling('CONTEXT_TOKENS', max(0, available - output))
    if context_budget is not None:
        context = min(context, max(0, int(context_budget)))
    # Excerpts grow with the square root of usable input, not a fraction of a
    # large model window. These are maxima: retrieval still selects relevant
    # material, and no caller should pad input to consume an allowance.
    scale = isqrt(context)
    summary = ceiling('SUMMARY_TOKENS', min(scale * 8, context // 5))
    generated = ceiling('SUMMARY_GENERATION_TOKENS', min(max(summary * 2, 256),
                        maximum or 2048, max(0, window - reserve - 1024)))
    summary = min(summary, generated // 2)
    return ContextLimits(window, known, reserve, context, output, summary,
        ceiling('SUMMARY_SOURCE_CHARS', min(scale * 256, context * 2, max(0, window - generated - reserve) * 3)),
        generated, ceiling('TOOL_OUTPUT_CHARS', min(scale * 48, context * 3 // 4)),
        ceiling('MEMORY_CHARS', min(scale * 16, context // 8)),
        ceiling('SHARED_MEMORY_CHARS', min(scale * 8, context // 16)),
        ceiling('PREFLIGHT_CONTEXT_CHARS', min(scale * 32, context * 3 // 8)))
