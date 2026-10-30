import os
import unittest
from unittest.mock import patch

from trinity_agent.providers import PROVIDERS, selected_provider


class ProviderConfigurationTests(unittest.TestCase):
    def test_each_api_has_its_own_key_catalog_base_and_credit(self):
        self.assertEqual(set(PROVIDERS), {'ovhcloud','deepseek','openai'})
        names=set()
        for provider in PROVIDERS.values():
            names.add(provider.api_key_env)
            self.assertTrue(provider.base_url_env.startswith('TRINITY_'))
            self.assertTrue(provider.monthly_credit_env.startswith('TRINITY_'))
            self.assertTrue(provider.default_model)
        self.assertEqual(len(names),3)

    def test_api_specific_limits_override_shared_defaults(self):
        with patch.dict(os.environ,{'TRINITY_AGENT_PROVIDER':'openai','TRINITY_AGENT_CONTEXT_TOKENS':'1000',
                                    'TRINITY_OPENAI_CONTEXT_TOKENS':'2000'},clear=False):
            self.assertEqual(selected_provider().setting('CONTEXT_TOKENS'),'2000')

    def test_unknown_provider_is_rejected(self):
        with patch.dict(os.environ,{'TRINITY_AGENT_PROVIDER':'unknown'},clear=False):
            with self.assertRaises(ValueError):selected_provider()


if __name__=='__main__':unittest.main()
