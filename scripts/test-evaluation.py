"""Offline checks for empirical routing, grading and deterministic image fixtures."""
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import ovh_evaluation as e
spec=importlib.util.spec_from_file_location('evaluation',ROOT/'scripts/evaluate-catalog.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
spec=importlib.util.spec_from_file_location('summary',ROOT/'scripts/summarize-evaluation.py')
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)


def protocol():
    return dict(version=1,methodology_version=3,runner_sha256='a'*64,sdk_version='offline-test',
                api='chat',streaming=True,feedback=False,retries=0,workers=1,request_limit=7)


def comparison():
    return {'version':1,'campaign':'offline-campaign','protocol':protocol(),
            'cases':[{'id':name,'sha256':digest*64,
                      'settings':{'max_tokens':1024,'timeout':30,'openai_reasoning_effort':'none'}}
                     for name,digest in [('first','b'),('second','c')]]}


def result(passed=2,errors=0,seconds=1):
    return dict(samples=2,passed=passed,errors=errors,median_seconds=seconds,median_tokens=120,
                median_cost_eur=.0001,comparison=comparison())


class Evaluation(unittest.TestCase):
    def test_domain_selection(self):
        self.assertEqual(e.routing_price_factor(1.8,'10'),10)
        for invalid in ['nan','inf','11',None,'bad']:
            self.assertEqual(e.routing_price_factor(1.8,invalid),1.8)
        for prompt,expected in [('Alpine OpenRC','linux'),('Android adb','android'),('iOS caméra','ios'),('macOS launchctl','macos'),('Windows PowerShell','windows'),('Réseau TLS','network_security'),('Corrige code Python','code')]:
            self.assertEqual(e.routing_domain(prompt),expected)
        self.assertEqual(e.routing_domain('code',image_count=1),'vision')

    def test_accuracy_precedes_latency_and_missing_evidence_falls_back(self):
        candidates=[{'id':'fast-wrong'},{'id':'correct'},{'id':'unknown'}]
        evidence={'models':{'fast-wrong':{'domains':{'code':result(1,0,.2)}},'correct':{'domains':{'code':result(2,0,3)}}}}
        selected,applied=e.prefer_evaluated(candidates,evidence,'code')
        self.assertTrue(applied);self.assertEqual(selected,[{'id':'correct'}])
        self.assertEqual(e.prefer_evaluated(candidates,evidence,'vision'),(candidates,False))
        evidence['models']['correct']['domains']['code']=result(0,2,1)
        selected,applied=e.prefer_evaluated(candidates,evidence,'code')
        self.assertFalse(applied);self.assertNotIn({'id':'correct'},selected)

    def test_evidence_requires_same_endpoint_recent_date_and_valid_numbers(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'model-evaluations.json'
            now=datetime.now(timezone.utc)
            valid={'schema':1,'source':'https://example.test/v1','created_at':now.isoformat(),'models':{'x':{'domains':{'code':result()}}}}
            path.write_text(json.dumps(valid))
            self.assertTrue(e.load_evidence(temp,valid['source'],now))
            valid['schema']=2;path.write_text(json.dumps(valid))
            self.assertTrue(e.load_evidence(temp,valid['source'],now))
            malformed=copy.deepcopy(valid);malformed['models']['x']['domains']['code'].pop('comparison')
            path.write_text(json.dumps(malformed));self.assertFalse(e.load_evidence(temp,valid['source'],now))
            path.write_text(json.dumps(valid))
            self.assertFalse(e.load_evidence(temp,'http://another.test/v1',now))
            self.assertFalse(e.load_evidence(temp,valid['source'],now+timedelta(days=15)))
            self.assertFalse(e.load_evidence(temp,valid['source'],now-timedelta(days=1)))
            valid['models']['x']['domains']['code']['median_seconds']=float('nan')
            path.write_text(json.dumps(valid));self.assertFalse(e.load_evidence(temp,valid['source'],now))

    def test_different_cases_settings_protocols_and_legacy_scores_cannot_rank_models(self):
        candidates=[{'id':'fast'},{'id':'slow'}]
        evidence={'models':{name:{'domains':{'code':result(seconds=seconds)}} for name,seconds in [('fast',1),('slow',5)]}}
        for change in [lambda score:score.pop('comparison'),
                       lambda score:score['comparison'].update(campaign='another-run'),
                       lambda score:score['comparison']['protocol'].update(streaming=False),
                       lambda score:score['comparison']['protocol'].update(feedback=True),
                       lambda score:score['comparison']['protocol'].update(runner_sha256='d'*64),
                       lambda score:score['comparison']['cases'][0].update(sha256='e'*64),
                       lambda score:score['comparison']['cases'][0]['settings'].update(max_tokens=512),
                       lambda score:score['comparison']['cases'][0]['settings'].update(openai_reasoning_effort='high')]:
            altered=copy.deepcopy(evidence);change(altered['models']['fast']['domains']['code'])
            self.assertEqual(e.prefer_evaluated(candidates,altered,'code'),(candidates,False))
        legacy=result();legacy.pop('comparison')
        old={'models':{'fast':{'domains':{'code':legacy}}}}
        self.assertEqual(e.prefer_evaluated(candidates,old,'code'),(candidates,False))
        self.assertEqual(e.measured_latency(candidates[0],old,'code'),float('inf'))
        self.assertEqual(e.measured_settings('fast',old,'code'),{})

    def test_summary_uses_common_campaign_without_selecting_each_models_best_profile(self):
        cases=b.CASES[:2]
        def report(run):
            return {'run_id':run,'date':'2026-10-20T19:00:00Z','completed_at':'2026-10-20T19:01:00Z',
                    'methodology_version':3,'routing_protocol':protocol(),
                    'cases':[{'id':c[0],'domain':c[1],'prompt':c[2],'expected':c[3]} for c in cases]}
        common=report('common');fast_only=report('fast-only')
        grouped=defaultdict(list)
        for run,model,seconds in [('common','fast',3),('common','slow',5),('fast-only','fast',.1)]:
            for case in cases:
                grouped[(model,'code',run)].append({'model':model,'domain':'code','case':case[0],
                    'case_sha256':b.case_digest(case),'settings':{'max_tokens':1024,'timeout':30},
                    'status':'passed','passed':True,'first_passed':True,'seconds':seconds})
        selected=s.comparable_scores([common,fast_only],grouped)
        self.assertEqual(set(selected),{('fast','code'),('slow','code')})
        self.assertEqual(selected[('fast','code')][0]['median_seconds'],3)
        self.assertEqual(e.comparison_key(selected[('fast','code')][0]),e.comparison_key(selected[('slow','code')][0]))
        # Different effective settings, even in one campaign, are not comparable.
        grouped[('slow','code','common')][0]['settings']['max_tokens']=512
        self.assertEqual(s.comparable_scores([common,fast_only],grouped),{})
        grouped[('slow','code','common')][0]['settings']['max_tokens']=1024
        grouped[('slow','code','common')][0]['case_sha256']='f'*64
        self.assertEqual(s.comparable_scores([common,fast_only],grouped),{})
        legacy=copy.deepcopy(common);legacy.pop('routing_protocol');legacy['methodology_version']=2
        self.assertEqual(s.comparable_scores([legacy,fast_only],grouped),{})

    def test_grading_accepts_equivalent_readonly_commands_but_not_fabricated_outputs(self):
        cases={c[0]:c for c in b.CASES}
        passed,_=b.grade(cases['windows_service'],'Réponse:\n```json\n{"service":"Get-Service -Name Spooler", "events":"Get-WinEvent -LogName System"}\n```',[])
        self.assertTrue(passed)
        self.assertFalse(b.grade(cases['android_debug'],'{"devices":["invented-phone"],"logs":["invented-log"]}',[])[0])
        self.assertFalse(b.grade(cases['web_python'],'{"binding":"qmark","source":"https://docs.python.org/3/library/sqlite3.html"}',[])[0])

    def test_code_is_executed_and_dangerous_syntax_rejected(self):
        case=b.CASES[0]
        good='def clamp(x, low, high):\n if low > high: raise ValueError()\n return min(high,max(low,x))'
        self.assertTrue(b.grade(case,json.dumps({'code':good}),[])[0])
        self.assertFalse(b.grade(case,json.dumps({'code':'import os\ndef clamp(x, low, high): return x'}),[])[0])
        self.assertFalse(b.grade(case,json.dumps({'code':'def clamp(x, low, high): return x'}),[])[0])

    def test_real_router_keeps_tools_for_image_edits_and_uses_measured_image_reader(self):
        from unittest.mock import patch
        with patch.object(sys, 'argv', ['test-evaluation', '--billing-status']):
            spec=importlib.util.spec_from_file_location('tested_agent',ROOT/'ovh-agent.py')
            a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
        base={'selectable':True,'currency':'EUR','context_length':131072,'max_completion_tokens':8192,
              'input_per_million':.1,'output_per_million':.1,'free':False,'parameters_b':9}
        cards=[dict(base,id='reader',vision=True),dict(base,id='editor',vision=True)]
        evidence={'models':{'reader':{'domains':{'vision':result(seconds=1)}},'editor':{'domains':{'vision':result(seconds=5)}}}}
        with patch.object(a,'fetch_model_catalog',return_value=cards), patch.object(a,'context_overhead',return_value=0), \
             patch.object(a,'routing_billing_status',return_value={}),patch.object(a,'load_router_state',return_value={}), \
             patch.object(a,'load_evidence',return_value=evidence), \
             patch.object(a,'model_capabilities',side_effect=lambda mid:{'tools':mid=='editor','reasoning':False}):
            chosen,why=a.choose_cost_effective_model('Analyse cette image',0,1,analysis={'intent':'question','needs_workspace':False})
            self.assertEqual(chosen,'reader');self.assertTrue(why['evaluation_applied'])
            chosen,_=a.choose_cost_effective_model('Modifie le code selon cette image',0,1,analysis={'intent':'edit','needs_workspace':True})
            self.assertEqual(chosen,'editor')
            self.assertEqual(a.preferred_vision_model(),'reader')

    def test_progressive_cases_have_deterministic_ground_truth_and_more_context(self):
        basic=b.build_cases(1,18);advanced=b.build_cases(3,18)
        self.assertGreater(len(advanced),len(basic))
        self.assertEqual(advanced,b.build_cases(3,18))
        self.assertNotEqual(b.build_cases(1,19),basic)
        injection=next(c for c in advanced if c[0]=='context_injection')
        self.assertGreater(len(injection[2]),6000)
        answer=json.dumps(injection[3])
        self.assertFalse(b.grade(injection,answer,[])[0])
        self.assertTrue(b.grade(injection,answer,[{'name':'read_fixture','ok':True}])[0])
        self.assertFalse(b.grade(basic[0],'[]',[])[0])

    def test_report_protocol_and_case_fingerprints_capture_real_inputs_without_model_calls(self):
        from types import SimpleNamespace
        args=SimpleNamespace(api='chat',no_stream=False,feedback=False,retries=0,workers=3)
        recorded=b.routing_protocol(args)
        self.assertEqual(recorded['methodology_version'],3)
        self.assertEqual(len(recorded['runner_sha256']),64)
        self.assertTrue(recorded['sdk_version'])
        case=b.CASES[0]
        self.assertEqual(b.case_digest(case),b.case_digest(case))
        self.assertNotEqual(b.case_digest(case),b.case_digest((case[0],case[1],case[2]+' Extra constraint.',case[3])))
        self.assertNotEqual(b.case_digest(b.CASES[-1]),b.case_digest(b.CASES[-2]))

    def test_readonly_path_recovery_is_explicit_and_does_not_change_write_resolution(self):
        from unittest.mock import patch
        with patch.object(sys,'argv',['test','--billing-status']):
            spec=importlib.util.spec_from_file_location('path_agent',ROOT/'ovh-agent.py')
            a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
        with tempfile.TemporaryDirectory() as temp, patch.object(a,'WORKSPACE',Path(temp)), patch.object(a,'emit'), patch.object(a.run_dependencies(), 'verification',None):
            file=Path(temp)/'settings.json';file.write_text('{"port":8457}')
            missing_slash=str(file).lstrip('/')
            answer=a.read_file(missing_slash)
            self.assertIn('8457',answer)
            self.assertIn('slash initial manquant',answer)
            self.assertEqual(a.resolve_path(missing_slash),Path(temp)/missing_slash)

    def test_png_fixtures_and_settings_allowlist(self):
        for layout in (False,True): self.assertTrue(b.png_fixture(layout).startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertNotEqual(b.png_fixture(),b.png_fixture(True))
        evidence={'models':{'x':{'domains':{'vision':result()},'settings':{'reasoning_effort':'none','reasoning_domains':['vision'],'timeout':999,'base_url':'evil'}}}}
        self.assertEqual(e.measured_settings('x',evidence,'vision'),{'reasoning_effort':'none'})
        self.assertEqual(e.measured_settings('x',evidence,'network_security'),{})
        evidence['models']['x']['domains']['vision']['comparison']['cases'][0]['settings']['openai_reasoning_effort']='high'
        self.assertEqual(e.measured_settings('x',evidence,'vision'),{})


if __name__=='__main__': unittest.main()
