"""Offline campaign boundary and progression checks."""
from datetime import datetime,timezone
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('campaign',Path(__file__).with_name('run-evaluation-campaign.py'))
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)


class Campaign(unittest.TestCase):
    def test_progression_and_exact_expiry(self):
        config={'enabled':True,'starts_at':'2026-10-18T00:00:00Z','expires_at':'2026-11-15T20:28:46Z'}
        self.assertEqual(c.plan(config,datetime(2026,10,18,tzinfo=timezone.utc))['level'],2)
        self.assertEqual(c.plan(config,datetime(2026,10,20,tzinfo=timezone.utc))['level'],3)
        self.assertIsNone(c.plan(config,datetime(2026,11,15,20,28,46,tzinfo=timezone.utc)))
        self.assertIsNone(c.plan({**config,'enabled':False},datetime(2026,10,18,tzinfo=timezone.utc)))


if __name__=='__main__': unittest.main()
