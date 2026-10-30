"""Offline public export checks: include new source, exclude local/private data."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PublicExport(unittest.TestCase):
    def test_new_allowlisted_files_export_without_git_staging_or_local_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            target = root / 'public'
            (source / 'scripts').mkdir(parents=True)
            shutil.copy2(ROOT / 'scripts/export-public.py', source / 'scripts/export-public.py')
            shutil.copy2(ROOT / '.gitignore', source / '.gitignore')
            subprocess.run(['git', 'init', '-q', str(source)], check=True)
            subprocess.run(['git', '-C', str(source), 'add', '.gitignore', 'scripts/export-public.py'], check=True)
            new_files = ['trinity_agent/project_memory.py', 'scripts/test-project-memory.py', 'trinity_agent/verification.py', 'trinity_agent/budget.py', 'trinity_agent/budget_model.py',
                         'trinity_agent/context.py', 'trinity_agent/vision.py', 'scripts/test-vision.py', 'trinity_agent/cache.py', 'trinity_agent/resources.py', 'trinity_agent/request.py',
                         '.ovh-agent-checks.json', 'scripts/test-verification.py',
                         'scripts/test-export-public.py', 'scripts/test-resources.py',
                         'ovh-agent-extension/extension/test/activity.test.cjs']
            for relative in new_files:
                file = source / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text('reviewed source\n')
            for relative in ['.env', 'ovh-agent.local.sh', 'private/token',
                             'local-recovery/backup.py', 'archive/conversation.json']:
                file = source / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text('LOCAL_ONLY_MARKER_12345')
            # Local maxima must not change the public Compose's native-EUR defaults.
            (source / '.env').write_text('OVH_AGENT_BUDGET_DAILY_EUR=200\n')
            (source / 'docker-compose.yml').write_text(
                'services:\n  ide:\n    environment:\n'
                '      OVH_AGENT_BUDGET_DAILY_EUR: "${OVH_AGENT_BUDGET_DAILY_EUR:-2}"\n'
                '      OVH_AGENT_BUDGET_WEEKLY_EUR: "${OVH_AGENT_BUDGET_WEEKLY_EUR:-15}"\n'
                '      OVH_AGENT_BUDGET_MONTHLY_EUR: "${OVH_AGENT_BUDGET_MONTHLY_EUR:-60}"\n'
                '      #OVH_AGENT_NAME: "Local Agent"\n'
                '      #OVH_AGENT_PRIMARY_COLOR: "#000000"\n'
                '      #OVH_AGENT_APP_ICON: "./branding/ovh/app.svg"\n'
                '    volumes:\n'
                '      - type: bind\n'
                '        source: /var/lib/trinity/agent-data/codex\n'
                '        target: /root/.codex\n'
                '      - type: bind\n'
                '        source: /var/lib/trinity/agent-data/trinity-agent\n'
                '        target: /root/.ovh-agent\n')
            index = (source / '.git/index').read_bytes()
            result = subprocess.run([sys.executable, str(source / 'scripts/export-public.py')],
                env={**os.environ, 'TRINITY_AGENT_PUBLIC_ROOT':str(target)},
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            for relative in new_files:
                self.assertEqual((target / relative).read_bytes(), (source / relative).read_bytes())
            manifest = json.loads((target / '.public-export-files.json').read_text())
            self.assertEqual(set(manifest), {*new_files, '.gitignore', 'scripts/export-public.py',
                                             'docker-compose.example.yml'})
            self.assertEqual((source / '.git/index').read_bytes(), index)
            self.assertFalse((target / 'docker-compose.yml').exists())
            self.assertFalse((target / '.env').exists())
            self.assertFalse((target / 'ovh-agent.local.sh').exists())
            compose = (target / 'docker-compose.example.yml').read_text()
            for period, amount in [('DAILY',2),('WEEKLY',15),('MONTHLY',60)]:
                self.assertIn(f'OVH_AGENT_BUDGET_{period}_EUR:-{amount}', compose)
            self.assertNotIn('200', compose)
            self.assertIn('OVH_AGENT_NAME:-TRINITY Agent', compose)
            self.assertIn('OVH_AGENT_PRIMARY_COLOR:-#3e3aab', compose)
            self.assertNotIn('./branding/ovh/', compose)
            self.assertIn('${CODEX_DATA_DIR:-./data/codex}', compose)
            self.assertIn('${AGENT_DATA_DIR:-./data/agent}', compose)
            self.assertNotIn('/var/lib/trinity/agent-data/', compose)


if __name__ == '__main__':
    unittest.main()
