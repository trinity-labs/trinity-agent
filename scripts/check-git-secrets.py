#!/usr/bin/env python3
"""Refuse staged credentials without printing their values."""
import os
import re
import subprocess
from pathlib import Path

root = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip())
known = set()
for directory in [Path('/etc/trinity/docker-trinity'), Path('/home/coder/TRINITY-DOCKER/etc/trinity/docker-trinity')]:
    if directory.is_dir():
        for file in directory.iterdir():
            # The billing base URL is public configuration, not a credential.
            if file.is_file() and file.name != "TRINITY_OVHCLOUD_API_BASE_URL":
                value = file.read_bytes().strip()
                if len(value) >= 8:
                    known.add(value)
patterns = [
    rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----',
    rb'\b(?:ghp_|gho_|github_pat_)[A-Za-z0-9_]{20,}',
    rb'\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}',
    rb'https?://[^\s/@:]+:[^\s/@]+@',
]
failed = []
files = subprocess.check_output(['git', 'diff', '--cached', '--name-only', '--diff-filter=ACMR', '-z']).split(b'\0')
for raw in files:
    if not raw:
        continue
    name = os.fsdecode(raw)
    payload = subprocess.check_output(['git', 'show', ':' + name])
    parts = Path(name).parts
    if any(part in {'.env', 'auth.json', 'node_modules', 'sessions', 'archive', 'secrets'} for part in parts):
        failed.append(name)
    elif any(value in payload for value in known) or any(re.search(pattern, payload) for pattern in patterns):
        failed.append(name)
if failed:
    print('Commit refused: potential secret/private data in:')
    for name in failed:
        print(name)
    raise SystemExit(1)
print('Staged credential scan passed (' + str(len([f for f in files if f])) + ' files).')
