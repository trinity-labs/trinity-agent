"""Run every offline backend suite; never call a paid model endpoint."""
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def main():
    failed = []
    started = time.monotonic()
    suites = sorted((ROOT / 'scripts').glob('test-*.py'))
    if not suites:
        raise SystemExit('No backend tests found')
    for suite in suites:
        result = subprocess.run([sys.executable, str(suite)], cwd=ROOT,
                                env={**os.environ, 'OVH_AGENT_PROVIDER': 'ovhcloud',
                                     'PYDANTIC_AI_NO_BANNER': '1'})
        if result.returncode:
            failed.append(suite.name)
    print(f'{len(suites) - len(failed)}/{len(suites)} suites passed in {time.monotonic()-started:.1f}s', flush=True)
    if failed:
        print('FAILED: ' + ', '.join(failed), file=sys.stderr)
    return bool(failed)


if __name__ == '__main__':
    sys.exit(main())
