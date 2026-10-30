import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with patch.dict(os.environ, {
    'TRINITY_AGENT_PROVIDER':'openai',
    'TRINITY_OPENAI_API_KEY':'openai-fixture',
    'TRINITY_OPENAI_MONTHLY_CREDIT_USD':'100',
}):
    import trinity_agent.agent as agent


class OpenAIProviderTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            'TRINITY_OPENAI_API_KEY':'openai-fixture',
            'TRINITY_OPENAI_MONTHLY_CREDIT_USD':'100',
        })
        self.environment.start()

    def tearDown(self):
        self.environment.stop()

    def test_codex_uses_responses_api_with_reviewed_limits_and_price(self):
        model = agent.get_budgeted_model('gpt-5.3-codex')
        self.assertEqual(type(model).__name__, 'BudgetedModel')
        self.assertEqual(type(model.wrapped).__name__, 'OpenAIResponsesModel')
        card = agent.normalise_model({'id':'gpt-5.3-codex'}, 'openai')
        self.assertEqual(card['context_length'], 400000)
        self.assertEqual(card['max_completion_tokens'], 128000)
        self.assertEqual(card['input_per_million'], 1.75)
        self.assertEqual(card['output_per_million'], 14.0)
        self.assertTrue(agent.model_capabilities('gpt-5.3-codex')['tools'])

    def test_display_currency_keeps_native_accounting_and_accepts_ecb_currencies(self):
        rates = agent.validated_euro_rates({'source': agent.ECB_RATES_URL,
            'date': datetime.now(timezone.utc).date().isoformat(),
            'rates': {'USD': 2, 'GBP': .8}, 'cached_at': 1, 'stale': False})
        self.assertEqual(rates['rates']['GBP'], .8)
        with patch.object(agent, 'DISPLAY_CURRENCY', 'GBP'), patch.object(agent, 'fetch_euro_rates', return_value=rates):
            displayed = agent.with_currency_display({'type':'usage','provider':'openai',
                'currency':'USD','cost_usd':2})
        self.assertEqual(displayed['currency'], 'USD')
        self.assertEqual(displayed['cost_usd'], 2)
        self.assertEqual(displayed['display_currency'], 'GBP')
        self.assertEqual(displayed['exchange_rates']['rates']['GBP'], .8)

    def test_monthly_allowance_uses_observed_local_tokens_and_cost(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(agent, 'DATA_ROOT', Path(folder)):
            target = Path(folder)/'archive'/'workspace'/'conversation'
            target.mkdir(parents=True)
            now = datetime.now(timezone.utc).isoformat()
            (target/'events.jsonl').write_text(json.dumps({'type':'usage','timestamp':now,
                'provider':'openai','input_tokens':1000,'output_tokens':200,'requests':1,'cost_usd':0.00455})+'\n')
            status = agent.fetch_billing_status()
        self.assertEqual(status['input_tokens'], 1000)
        self.assertEqual(status['output_tokens'], 200)
        self.assertAlmostEqual(status['current_usage'], .00455)
        self.assertAlmostEqual(status['credit_remaining'], 99.99545)

    def test_default_allowance_excludes_legacy_usage_without_provider(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(agent, 'DATA_ROOT', Path(folder)):
            target = Path(folder)/'archive'/'workspace'/'conversation'
            target.mkdir(parents=True)
            now = datetime.now(timezone.utc).isoformat()
            (target/'events.jsonl').write_text(json.dumps({'type':'usage','timestamp':now,
                'input_tokens':999999,'output_tokens':999999,'cost_usd':99})+'\n')
            with patch.dict(os.environ, {'TRINITY_OPENAI_API_KEY':'openai-fixture'}):
                os.environ.pop('TRINITY_OPENAI_MONTHLY_CREDIT_USD', None)
                status = agent.fetch_billing_status()
        self.assertEqual(status['credit_available'], 100)
        self.assertEqual(status['credit_remaining'], 100)
        self.assertEqual(status['input_tokens'], 0)
        self.assertEqual(status['output_tokens'], 0)

    def test_model_panel_can_collect_both_other_provider_catalogues(self):
        payloads = [
            {'data':[{'id':'gpt-oss-120b','owned_by':'openai','pricing':{'currency_unit':'EUR','prompt':'0.000001','completion':'0.000002'}}]},
            {'data':[{'id':'deepseek-flash','context_window':1048576,'max_output_tokens':393216}]},
        ]
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): pass
        def open_url(request, timeout=0):
            return Response(json.dumps(payloads.pop(0)).encode())
        with tempfile.TemporaryDirectory() as folder, patch.object(agent, 'DATA_ROOT', Path(folder)), \
             patch.dict(os.environ, {'TRINITY_OVHCLOUD_API_KEY':'ovh-fixture',
                                      'TRINITY_DEEPSEEK_API_KEY':'deepseek-fixture'}), \
             patch.object(agent.urllib.request, 'urlopen', side_effect=open_url), \
             patch.object(agent, 'SECONDARY_CATALOG', None), patch.object(agent, 'SECONDARY_CATALOG_EXPIRES', 0):
            cards, note = agent.fetch_secondary_catalog(True)
        self.assertEqual({card['provider'] for card in cards}, {'ovhcloud','deepseek'})
        self.assertEqual(note, '')


if __name__ == '__main__':
    unittest.main()
