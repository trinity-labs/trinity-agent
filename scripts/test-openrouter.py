import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['TRINITY_AGENT_PROVIDER'] = 'openrouter'
os.environ['TRINITY_OPENROUTER_API_KEY'] = 'fixture'
os.environ['TRINITY_AGENT_DATA_ROOT'] = '/tmp/trinity-openrouter-unit'

import trinity_agent.agent as agent


class OpenRouterTests(unittest.TestCase):
    def model(self, prompt='0', completion='0'):
        return {'id': 'particle-ai/particle-1:free', 'canonical_slug': 'particle-ai/particle-1',
                'context_length': 1048576,
                'architecture': {'input_modalities': ['text'], 'output_modalities': ['text']},
                'supported_parameters': ['tools', 'reasoning', 'response_format'],
                'top_provider': {'max_completion_tokens': 131072},
                'pricing': {'prompt': prompt, 'completion': completion}}

    def test_free_tool_model_is_selectable(self):
        card = agent.normalise_model(self.model(), 'openrouter')
        self.assertTrue(card['free'])
        self.assertTrue(card['selectable'])
        self.assertEqual(card['input_per_million'], 0)

    def test_any_nonzero_price_is_rejected(self):
        with self.assertRaises(ValueError):
            agent.normalise_model(self.model(prompt='0.000001'), 'openrouter')
        with self.assertRaises(ValueError):
            agent.normalise_model(self.model(completion='0.000001'), 'openrouter')

    def test_free_tier_must_be_verified(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self): return b'{"data":{"is_free_tier":false}}'
        with patch('urllib.request.urlopen', return_value=Response()):
            status = agent.fetch_billing_status(force=True)
        self.assertFalse(status['available'])
        self.assertIn('error', status)


if __name__ == '__main__':
    unittest.main()
