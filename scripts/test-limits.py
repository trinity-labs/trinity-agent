#!/opt/ovh-agent/bin/python
"""Offline model-window, automatic allowance and explicit-cap regressions."""
from dataclasses import asdict
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ovh_limits import NAMES, UNKNOWN_WINDOW, limit_setting, resolve_limits


class Limits(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            **{'OVH_AGENT_' + name: 'auto' for name in NAMES},
            'OVH_AGENT_CONTEXT_RESERVE_TOKENS': '4096'})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def card(self, window=131072, maximum=32768):
        return {'context_length': window, 'max_completion_tokens': maximum}

    def backend(self):
        with patch.object(sys,'argv',['limits-test','--billing-status']):
            spec=importlib.util.spec_from_file_location('limits_backend',ROOT/'ovh-agent.py')
            backend=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(backend)
        return backend

    def test_auto_follows_provider_window_task_and_actual_overhead(self):
        limits = [resolve_limits(self.card(window), overhead=8000) for window in (32768, 131072, 262144)]
        for field in ('context_tokens','output_tokens','tool_output_chars','memory_chars','preflight_context_chars'):
            self.assertLess(getattr(limits[0],field),getattr(limits[1],field))
            self.assertLess(getattr(limits[1],field),getattr(limits[2],field))
        for result in limits:
            self.assertLessEqual(result.context_tokens+result.output_tokens+result.reserve_tokens+8000,
                                 result.window_tokens)
            self.assertTrue(result.provider_window_known)
        simple = resolve_limits(self.card())
        complex_task = resolve_limits(self.card(), complex_task=True)
        self.assertGreater(complex_task.output_tokens,simple.output_tokens)
        self.assertLess(complex_task.context_tokens,simple.context_tokens)
        self.assertLess(resolve_limits(self.card(),overhead=16000).context_tokens,simple.context_tokens)

    def test_large_windows_grow_excerpt_allowances_sublinearly(self):
        small=resolve_limits(self.card(65536,65536))
        large=resolve_limits(self.card(262144,65536))
        ratio=large.context_tokens/small.context_tokens
        for name in ('summary_tokens','summary_source_chars','tool_output_chars',
                     'memory_chars','shared_memory_chars','preflight_context_chars'):
            with self.subTest(name=name):
                self.assertGreater(getattr(large,name),getattr(small,name))
                self.assertLess(getattr(large,name)/getattr(small,name),ratio*.7)
        self.assertLess(large.tool_output_chars,large.context_tokens*3//4//4)
        self.assertLess(large.preflight_context_chars,large.context_tokens*3//8//4)

    def test_unknown_or_invalid_catalogue_is_bounded_and_identified(self):
        for window in [None, 0, -1, True, 'unknown', float('inf'), 10**20]:
            with self.subTest(window=window):
                result = resolve_limits({'context_length': window},complex_task=True)
                self.assertEqual(result.window_tokens,UNKNOWN_WINDOW)
                self.assertFalse(result.provider_window_known)
                self.assertLessEqual(result.output_tokens,2048)
                self.assertLess(result.context_tokens,UNKNOWN_WINDOW)
        # A known input window is not an invented completion capability.
        result = resolve_limits({'context_length':262144})
        self.assertLessEqual(result.output_tokens,1024)
        self.assertLessEqual(result.summary_tokens,result.summary_generation_tokens//2)

    def test_numeric_values_are_ceilings_and_do_not_expand_provider_limits(self):
        caps = {'CONTEXT_TOKENS':12000,'OUTPUT_TOKENS':800,'COMPLEX_OUTPUT_TOKENS':900,
                'SUMMARY_TOKENS':600,'SUMMARY_SOURCE_CHARS':3000,'SUMMARY_GENERATION_TOKENS':1200,
                'TOOL_OUTPUT_CHARS':1500,'MEMORY_CHARS':700,'SHARED_MEMORY_CHARS':400,
                'PREFLIGHT_CONTEXT_CHARS':1000}
        with patch.dict(os.environ, {'OVH_AGENT_'+key:str(value) for key,value in caps.items()}):
            for complex_task in (False,True):
                result=resolve_limits(self.card(),complex_task=complex_task)
                for key,value in caps.items():
                    if key in {'OUTPUT_TOKENS','COMPLEX_OUTPUT_TOKENS'}:
                        continue
                    self.assertLessEqual(getattr(result,key.lower()),value)
                self.assertLessEqual(result.output_tokens,900 if complex_task else 800)
        with patch.dict(os.environ, {'OVH_AGENT_OUTPUT_TOKENS':str(10**10)}):
            self.assertEqual(resolve_limits(self.card(maximum=512)).output_tokens,512)

    def test_compaction_allowance_follows_remaining_input_and_generation(self):
        full=resolve_limits(self.card(262144,65536))
        small=resolve_limits(self.card(262144,65536),context_budget=5000)
        self.assertEqual(small.context_tokens,5000)
        self.assertLess(small.summary_tokens,full.summary_tokens)
        self.assertLess(small.summary_source_chars,full.summary_source_chars)
        with patch.dict(os.environ,{'OVH_AGENT_SUMMARY_GENERATION_TOKENS':'1024'}):
            limited=resolve_limits(self.card(262144,65536))
            self.assertLessEqual(limited.summary_tokens,512)
            self.assertLessEqual(limited.summary_generation_tokens,1024)

    def test_zero_disables_optional_excerpts_and_malformed_values_are_not_auto(self):
        keys=['MEMORY_CHARS','SHARED_MEMORY_CHARS','PREFLIGHT_CONTEXT_CHARS','TOOL_OUTPUT_CHARS']
        with patch.dict(os.environ,{'OVH_AGENT_'+key:'0' for key in keys}):
            result=resolve_limits(self.card())
            for key in keys:self.assertEqual(getattr(result,key.lower()),0)
        for value in ['automatic','-1','1.5','nan','true']:
            with self.subTest(value=value),patch.dict(os.environ,{'OVH_AGENT_CONTEXT_TOKENS':value}):
                with self.assertRaises(ValueError):resolve_limits(self.card())
        for value in ['auto','']:
            with patch.dict(os.environ,{'OVH_AGENT_CONTEXT_TOKENS':value}):
                self.assertIsNone(limit_setting('OVH_AGENT_CONTEXT_TOKENS'))

    def test_small_model_does_not_allocate_output_over_instructions(self):
        result=resolve_limits(self.card(8192,2048),overhead=7000)
        self.assertEqual(result.output_tokens,0)
        self.assertEqual(result.context_tokens,0)

    def test_auto_does_not_modify_financial_or_execution_policy(self):
        policy={'OVH_AGENT_BUDGET_DAILY_EUR':'2','OVH_AGENT_BUDGET_WEEKLY_EUR':'15',
                'OVH_AGENT_BUDGET_MONTHLY_EUR':'60','OVH_AGENT_REQUEST_LIMIT':'80',
                'OVH_AGENT_SUMMARY_TIMEOUT_SECONDS':'15'}
        with patch.dict(os.environ,policy):
            resolve_limits(self.card(262144,65536),complex_task=True)
            self.assertEqual({key:os.environ[key] for key in policy},policy)

    def test_actual_backend_resolves_auto_and_refuses_insufficient_context(self):
        backend=self.backend()
        self.assertIsNone(backend.CONTEXT_TOKEN_CAP)
        self.assertIsNone(backend.auto_model_card()['context_length'])
        card={'id':'offline-model',**self.card()}
        with patch.object(backend,'fetch_model_catalog',return_value=[card]), \
             patch.object(backend,'model_capabilities',return_value={'reasoning':False}), \
             patch.object(backend,'context_overhead',return_value=7000):
            settings=backend.generation_settings('offline-model','Bonjour')
            self.assertEqual(settings['max_tokens'],resolve_limits(card,overhead=7000).output_tokens)
            card.update(self.card(8192,2048))
            with self.assertRaisesRegex(ValueError,'Fenêtre de contexte'):
                backend.generation_settings('offline-model','Bonjour')

    def test_memory_tool_uses_selected_model_without_journaling_sdk_context(self):
        backend=self.backend()
        cards=[{'id':str(window),**self.card(window)} for window in (32768,262144)]
        invocations=[]
        def invoke(function,*args,**kwargs):
            invocations.append((function.__name__,args,kwargs))
            return function(*args,**kwargs)
        with patch.object(backend,'fetch_model_catalog',return_value=cards), \
             patch.object(backend,'TASK_ENGINE',SimpleNamespace(invoke=invoke)), \
             patch.object(backend,'ConversationMemory') as local, \
             patch.object(backend,'WorkspaceContext') as shared:
            local.return_value.search.return_value='local'
            shared.return_value.search.return_value='shared'
            for card in cards:
                ctx=SimpleNamespace(model=SimpleNamespace(model_name=card['id']))
                self.assertEqual(backend.search_memory_for_context(ctx,'logo'),'local\nshared')
                limits=resolve_limits(card)
                self.assertEqual(local.return_value.search.call_args.args,('logo',limits.memory_chars//2))
                self.assertEqual(shared.return_value.search.call_args.args[-1],limits.shared_memory_chars)
                self.assertEqual(invocations[-1],('search_memory',('logo',),{}))
            tool=next(tool for tool in backend.agent_tools() if tool.name=='search_memory')
            self.assertEqual(set(tool.function_schema.json_schema['properties']),{'query'})

    def test_catalogue_failure_does_not_invent_provider_capacity(self):
        backend=self.backend()
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(backend,'MODEL_CATALOG',None), \
             patch.object(backend,'MODEL_CACHE_FILE',Path(directory)/'missing.json'), \
             patch.dict(os.environ,{'OVH_AI_API_KEY':'offline-placeholder'}), \
             patch.object(backend.urllib.request,'urlopen',side_effect=OSError('offline')):
            card=backend.fetch_model_catalog(force=True)[0]
        self.assertEqual(card['metadata_source'],'fallback')
        self.assertIsNone(card['context_length'])
        self.assertIsNone(card['max_completion_tokens'])
        limits=backend.current_limits(card)
        self.assertFalse(limits.provider_window_known)
        self.assertEqual(limits.window_tokens,UNKNOWN_WINDOW)


if __name__=='__main__':unittest.main()
