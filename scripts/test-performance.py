"""Deterministic context performance invariants, not wall-clock assertions."""
import asyncio
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import ovh_context as c
from ovh_memory import ConversationMemory
from pydantic_ai.messages import ModelRequest,ModelResponse,UserPromptPart,SystemPromptPart,ToolCallPart,ToolReturnPart


def reference_drop(messages,budget):
    system,body,_=c.separate_system(messages);units=c.split_units(body)
    latest=next((m for m in reversed(body) if any(isinstance(p,UserPromptPart) for p in m.parts)),None)
    user=replace(latest,parts=[p for p in latest.parts if isinstance(p,UserPromptPart)]) if latest else None
    prefix=[ModelRequest(parts=system)] if system else [];kept=list(units);count=0
    while kept:
        tail=[m for u in kept for m in u]
        pinned=[user] if user and not any(m is latest for m in tail) else []
        if c.estimate_tokens(prefix+pinned+tail)+min(c.context_limits(budget).summary_tokens,budget//5)<=int(budget*.7):break
        kept.pop(0);count+=1
    return count


class ContextPerformance(unittest.TestCase):
    def test_linear_counting_keeps_same_protocol_boundary_and_latest_user(self):
        for user_index in (0,40,79):
            history=[ModelRequest(parts=[SystemPromptPart('Explicit rules remain unchanged.')])]
            for i in range(80):
                if i==user_index:history.append(ModelRequest(parts=[UserPromptPart('Latest instruction: preserve files.')]))
                history.extend([ModelResponse(parts=[ToolCallPart('read_file',{'path':str(i)},tool_call_id=str(i))]),
                    ModelRequest(parts=[ToolReturnPart('read_file','evidence '*160,tool_call_id=str(i))])])
            expected=reference_drop(history,6000);original=c.estimate_tokens;counted=0
            def counted_tokens(messages):
                nonlocal counted
                counted+=len(messages)
                return original(messages)
            with tempfile.TemporaryDirectory() as tmp,patch.object(c,'estimate_tokens',side_effect=counted_tokens):
                output,stats=asyncio.run(c.compact_context(history,ConversationMemory(Path(tmp)),6000))
            self.assertEqual(stats['dropped_turns'],expected)
            self.assertLess(counted,len(history)*9)
            self.assertTrue(any(isinstance(p,UserPromptPart) and 'preserve files' in p.content for m in output for p in m.parts))
            self.assertLessEqual(original(output),6000)

if __name__=='__main__':unittest.main()
