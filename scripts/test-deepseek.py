"""DeepSeek wire-protocol and file-edit regressions, with no paid API calls."""
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

try:
    import httpx2 as httpx
except ImportError:
    import httpx
from openai import AsyncOpenAI
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.usage import UsageLimits

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
with patch.dict(os.environ, {'OVH_AGENT_PROVIDER':'deepseek'}), patch.object(sys, 'argv', ['test', '--billing-status']):
    spec = importlib.util.spec_from_file_location('deepseek_backend', ROOT/'trinity_agent/agent.py')
    a = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = a
    spec.loader.exec_module(a)

RAW = {'id':'deepseek-flash', 'context_window':1048576, 'max_output_tokens':393216,
       'input_modalities':['text','image']}
RATES = {'source': a.ECB_RATES_URL, 'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
         'rates': {'EUR': 1, 'USD': 2, 'CNY': 8}, 'stale': False}


class DeepSeekIntegration(unittest.TestCase):
    def setUp(self):
        rates = patch.object(a, 'fetch_euro_rates', return_value=RATES)
        rates.start()
        self.addCleanup(rates.stop)

    def test_provider_metadata_and_limits_do_not_inherit_ovh_caps(self):
        card = a.normalise_model(RAW)
        self.assertEqual(card['currency'], 'USD')
        self.assertEqual(card['provider'], 'deepseek')
        self.assertEqual(a.AI_API_BASE_URL, 'https://api.deepseek.com')
        self.assertIsNone(a.REQUEST_LIMIT)
        self.assertFalse(a.current_budget_status()['enforced'])
        with patch.dict(os.environ, {'OVH_AGENT_CONTEXT_TOKENS':'4096','OVH_AGENT_OUTPUT_TOKENS':'512'}):
            limits = a.current_limits(card, overhead=1024)
        self.assertEqual(limits.output_tokens, 393216)
        self.assertEqual(limits.context_tokens + limits.output_tokens + 1024, 1048576)
        with patch.object(a, 'fetch_model_catalog', return_value=[card]):
            settings = a.generation_settings('deepseek-flash', 'Fix the file')
            self.assertEqual(a.current_limits().output_tokens,393216)
        self.assertEqual(settings['max_tokens'], 393216)
        self.assertIsNone(settings['timeout'])
        self.assertNotIn('openai_reasoning_effort', settings)

    def test_missing_deepseek_key_never_uses_ovh_key(self):
        with patch.dict(os.environ, {'OVH_AI_API_KEY':'OVH-MUST-NOT-LEAK'}, clear=True):
            with self.assertRaises(Exception) as failure:
                a.get_budgeted_model('deepseek-flash')
        self.assertIn('DEEPSEEK_API_KEY', str(failure.exception))
        self.assertNotIn('OVH-MUST-NOT-LEAK', str(failure.exception))

    def test_balance_is_separate_and_does_not_enforce_spending(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(a, 'ARCHIVE_ROOT', Path(folder)), \
             patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'deepseek-fixture'}):
            payload={'is_available':False,'balance_infos':[{'currency':'USD','total_balance':'0'}]}
            with patch.object(a.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(payload).encode())) as get:
                status=a.fetch_billing_status(force=True)
                self.assertEqual(get.call_args.args[0].full_url, 'https://api.deepseek.com/user/balance')
                self.assertEqual(status['credit_available'],0)
                self.assertTrue(status['available'])
                self.assertFalse(status['account_available'])
                self.assertFalse(a.current_budget_status()['blocked'])
                self.assertEqual(a.fetch_billing_status(),status)
                self.assertEqual(get.call_count,1)
            self.assertEqual(a.billing_file().name,'billing-deepseek.json')
            self.assertEqual(a.router_file().name,'router-deepseek.json')

    def test_cny_and_mixed_balances_are_summed_in_euros_without_double_counting(self):
        yuan = {'currency': 'CNY', 'total_balance': '16', 'granted_balance': '8', 'topped_up_balance': '8'}
        dollar = {'currency': 'USD', 'total_balance': '2', 'granted_balance': '0', 'topped_up_balance': '2'}
        for rows, total, topped_up in [([yuan], 2, 1), ([yuan, dollar], 3, 2)]:
            with self.subTest(rows=rows), tempfile.TemporaryDirectory() as folder, \
                 patch.object(a, 'ARCHIVE_ROOT', Path(folder)), \
                 patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'fixture'}), \
                 patch.object(a.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(
                     {'balance_infos': rows, 'is_available': True}).encode())):
                status = a.fetch_billing_status(force=True)
                self.assertTrue(status['available'])
                self.assertEqual(status['currency'], 'EUR')
                self.assertEqual(status['credit_available'], total)
                self.assertEqual(status['granted_balance'], 1)
                self.assertEqual(status['topped_up_balance'], topped_up)
                self.assertEqual(status['balance_infos'][0]['source_currency'], 'CNY')
                self.assertEqual(status['balance_infos'][0]['source_total_balance'], '16')
                self.assertEqual(status['balance_infos'][0]['total_balance'], 2)

    def test_old_dollar_cache_is_replaced_and_failed_refresh_keeps_only_euro_cache(self):
        payload = {'balance_infos': [{'currency': 'CNY', 'total_balance': '16.34'}], 'is_available': True}
        with tempfile.TemporaryDirectory() as folder, patch.object(a, 'ARCHIVE_ROOT', Path(folder)), \
             patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'fixture'}):
            a.billing_file().write_text(json.dumps({'provider': 'deepseek', 'available': True,
                'currency': 'USD', 'credit_available': 999, 'cached_at': a.time.time()}))
            with patch.object(a.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(payload).encode())) as get:
                converted = a.fetch_billing_status()
            self.assertEqual(get.call_count, 1)
            self.assertAlmostEqual(converted['credit_available'], 16.34 / 8)
            with patch.object(a.urllib.request, 'urlopen', side_effect=OSError('offline')):
                stale = a.fetch_billing_status(force=True)
            self.assertEqual(stale['credit_available'], converted['credit_available'])
            self.assertEqual(stale['currency'], 'EUR')
            self.assertTrue(stale['stale'])

    def test_missing_rate_or_invalid_currency_never_displays_a_partial_or_fake_total(self):
        for rows, rates in [
            ([{'currency': 'CNY', 'total_balance': '16'}], {'rates': {'EUR': 1}}),
            ([{'currency': 'USD', 'total_balance': '2'}, {'currency': 'XYZ', 'total_balance': '3'}], RATES),
            ([{'currency': 'CNY', 'total_balance': 'NaN'}], RATES),
            ([], RATES),
        ]:
            with self.subTest(rows=rows), tempfile.TemporaryDirectory() as folder, \
                 patch.object(a, 'ARCHIVE_ROOT', Path(folder)), \
                 patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'fixture'}), \
                 patch.object(a, 'fetch_euro_rates', return_value=rates), \
                 patch.object(a.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(
                     {'balance_infos': rows, 'is_available': True}).encode())):
                status = a.fetch_billing_status(force=True)
            self.assertFalse(status['available'])
            self.assertNotIn('credit_available', status)

    def test_display_metadata_preserves_native_prices_and_archived_accounting(self):
        usage = {'type': 'usage', 'model': 'deepseek-flash', 'cost_usd': 2, 'currency': 'USD'}
        decorated = a.with_euro_display(usage)
        self.assertEqual(decorated['cost_usd'], 2)
        self.assertEqual(decorated['currency'], 'USD')
        self.assertEqual(decorated['exchange_rates'], RATES)
        self.assertNotIn('exchange_rates', usage)
        archive = {'type': 'archive_loaded', 'messages': [usage]}
        self.assertEqual(a.with_euro_display(archive)['messages'][0], decorated)
        card = a.normalise_model(RAW)
        ovh = {'id': 'gpt-oss-120b', 'provider': 'ovhcloud', 'currency': 'EUR'}
        display = a.with_euro_display({'type': 'models', 'items': [card, ovh]})
        self.assertEqual(display['items'][0]['input_per_million'], card['input_per_million'])
        self.assertEqual(display['items'][0]['exchange_rates'], RATES)
        self.assertNotIn('exchange_rates', display['items'][1])
        self.assertNotIn('exchange_rates', card)

    def test_real_sdk_preserves_reasoning_across_read_edit_verify_and_answer(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name); workspace = root/'project'; workspace.mkdir()
            target = workspace/'app.py'; target.write_text('VALUE = 1\n# user change\n')
            requests = []
            completed_steps = []
            steps = [('not_registered', {})] * 3 + [('read_file', {'path':'app.py'}),
                     ('edit_file', {'path':'app.py','old_text':'VALUE = 1','new_text':'VALUE = 2'}),
                     ('verify_changes', {})]
            def transport(request):
                self.assertEqual(request.url.host, 'api.deepseek.com')
                self.assertEqual(request.headers['authorization'], 'Bearer deepseek-fixture')
                body = json.loads(request.content); requests.append(body)
                number = len(requests)
                previous = [message for message in body['messages'] if message['role']=='assistant']
                self.assertTrue(all(message.get('reasoning_content') for message in previous))
                progress_due = any(tool['function']['name']=='update_plan' and 'explanation' in
                    tool['function']['parameters'].get('required', []) for tool in body.get('tools', []))
                if len(completed_steps) < len(steps):
                    if progress_due:
                        tool, arguments = 'update_plan', {'steps':['Lire', 'Modifier', 'Vérifier'],
                            'explanation':'Je vérifie la valeur et préserve le commentaire existant.'}
                    else:
                        tool, arguments = steps[len(completed_steps)]
                        completed_steps.append(tool)
                    message = {'role':'assistant','content':None,'reasoning_content':f'fixture reasoning {number}',
                        'tool_calls':[{'id':f'call-{number}','type':'function','function':{'name':tool,'arguments':json.dumps(arguments)}}]}
                    finish = 'tool_calls'
                else:
                    message = {'role':'assistant','content':'La valeur demandée est maintenant 2.','reasoning_content':'fixture final'}
                    finish = 'stop'
                return httpx.Response(200, json={'id':f'fixture-{number}','object':'chat.completion','created':1,
                    'model':'deepseek-flash','choices':[{'index':0,'message':message,'finish_reason':finish}],
                    'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}})
            client = AsyncOpenAI(api_key='deepseek-fixture', base_url='https://api.deepseek.com',
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport)))
            provider = DeepSeekProvider(openai_client=client)
            a.RUN_CONTEXT.set(a.RunDependencies())
            def observe(event):
                if a.run_dependencies().engine:
                    a.run_dependencies().engine.observe_event(event)
            values = {'WORKSPACE':workspace,'DATA_ROOT':root/'data','ARCHIVE_ROOT':root/'archive',
                'STATE_ROOT':root/'state','CONVERSATION_ID':'fixture','AGENTS':{}, 'emit':observe}
            from contextlib import ExitStack
            with ExitStack() as stack:
                for key, value in values.items():stack.enter_context(patch.object(a,key,value))
                stack.enter_context(patch.object(a,'DeepSeekProvider',return_value=provider))
                stack.enter_context(patch.object(a,'budget_ledger',side_effect=AssertionError('OVH budget must not run')))
                stack.enter_context(patch.object(a,'fetch_model_catalog',return_value=[a.normalise_model(RAW)]))
                stack.enter_context(patch.object(a,'request_approval',return_value=True))
                a.begin_verification('Change VALUE to 2 in app.py and preserve my comment.')
                result = a.get_agent('deepseek-flash').run_sync('Change VALUE to 2 in app.py and preserve my comment.', deps=a.run_dependencies(),
                    usage_limits=UsageLimits(request_limit=16), model_settings={'max_tokens':393216})
                self.assertIn('2', result.output)
                self.assertFalse(a.run_dependencies().engine.final_status()['incomplete'])
                history = a.history_with_memory(result.all_messages(), 'Read app.py', include_excerpts=False,
                    context_card=a.normalise_model(RAW))
                a.begin_verification('Read app.py to confirm its value.')
                a.get_agent('deepseek-flash').run_sync('Read app.py to confirm its value.', deps=a.run_dependencies(),
                    message_history=history, usage_limits=UsageLimits(request_limit=16))
            self.assertEqual(target.read_text(), 'VALUE = 2\n# user change\n')
            self.assertEqual(len(requests), 9)

    def test_failed_commands_do_not_exhaust_deepseek_but_readonly_is_enforced(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            a.RUN_CONTEXT.set(a.RunDependencies())
            with patch.multiple(a,WORKSPACE=root,DATA_ROOT=root/'data',ARCHIVE_ROOT=root/'archive',
                    STATE_ROOT=root/'state',CONVERSATION_ID='failure-test',emit=lambda event:None), \
                 patch.object(a,'request_approval',return_value=True):
                a.begin_verification('Exécute une commande locale.')
                engine=a.run_dependencies().engine
                arguments={'command':'false','cwd':'.','timeout':120}
                revision=engine._observation_revision('run_command',arguments)
                for _ in range(6):
                    engine.failures.record('run_command',arguments,{'category':'permission_denied'},revision=revision)
                result=engine.invoke(a.run_command,'printf deepseek-ok')
                self.assertIn('deepseek-ok',result)
                engine.read_only=True
                refused=json.loads(engine.invoke(a.run_command,'touch prohibited'))
                self.assertEqual(refused['category'],'read_only')
                self.assertFalse((root/'prohibited').exists())


class EuroReferenceRates(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = patch.object(a, 'DATA_ROOT', Path(self.folder.name))
        root.start()
        self.addCleanup(root.stop)
        a.EUR_RATES_MEMORY = None

    def test_public_ecb_xml_is_cached_and_no_key_is_forwarded(self):
        xml = f'''<Envelope xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
          <Cube><Cube time="{RATES['date']}"><Cube currency="USD" rate="2"/>
          <Cube currency="CNY" rate="8"/></Cube></Cube></Envelope>'''
        with patch.object(a.urllib.request, 'urlopen', return_value=io.BytesIO(xml.encode())) as get:
            data = a.fetch_euro_rates()
            self.assertEqual(data['rates'], RATES['rates'])
            self.assertEqual(data['date'], RATES['date'])
            self.assertFalse(data['stale'])
            self.assertEqual(get.call_args.args, (a.ECB_RATES_URL,))
            a.EUR_RATES_MEMORY = None
            self.assertEqual(a.fetch_euro_rates(), data)
            self.assertEqual(get.call_count, 1)

    def test_outage_uses_dated_cache_but_rejects_older_than_seven_days(self):
        cache = a.DATA_ROOT / 'cache' / 'exchange-rates-eur.json'
        cache.parent.mkdir()
        for days, usable in [(1, True), (8, False), (-1, False)]:
            with self.subTest(days=days):
                a.EUR_RATES_MEMORY = None
                old = {**RATES, 'date': (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d'),
                       'cached_at': a.time.time() - 86400}
                cache.write_text(json.dumps(old))
                with patch.object(a.urllib.request, 'urlopen', side_effect=OSError('offline')) as get:
                    rates = a.fetch_euro_rates()
                    self.assertEqual(a.fetch_euro_rates(), rates)
                    self.assertEqual(get.call_count, 1)
                self.assertTrue(rates['stale'])
                self.assertEqual('CNY' in rates['rates'], usable)

    def test_invalid_rates_are_rejected(self):
        for value in (0, -1, 'NaN', 'Infinity', None, '1e999', '1e-999'):
            with self.subTest(value=value), self.assertRaises((ValueError, a.InvalidOperation)):
                a.validated_euro_rates({**RATES, 'rates': {'USD': 2, 'CNY': value}})

    def test_malformed_cache_and_offline_feed_return_an_explicit_unavailable_rate(self):
        cache = a.DATA_ROOT / 'cache' / 'exchange-rates-eur.json'
        cache.parent.mkdir()
        cache.write_text('[]')
        with patch.object(a.urllib.request, 'urlopen', side_effect=OSError('offline')):
            rates = a.fetch_euro_rates()
        self.assertEqual(rates['rates'], {'EUR': 1})
        self.assertTrue(rates['stale'])
        self.assertIn('indisponible', rates['error'])


if __name__ == '__main__':
    unittest.main()
