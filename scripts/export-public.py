#!/usr/bin/env python3
"""Export only reviewed source files, never live IDE data or credentials."""
import os
import re
import subprocess
from pathlib import Path

source = Path(__file__).resolve().parents[1]
target = Path(os.environ.get('TRINITY_AGENT_PUBLIC_ROOT', str(source.parent / 'trinity-agent')))
if target.resolve() == source.resolve():
    raise SystemExit('Export destination must differ from the running IDE')
paths = subprocess.check_output(['git', '-C', str(source), 'ls-files', '-z']).decode().split('\0')
secrets = set()
for directory in [Path('/etc/trinity/docker-trinity'), Path('/home/coder/TRINITY-DOCKER/etc/trinity/docker-trinity')]:
    if directory.is_dir():
        for file in directory.iterdir():
            if file.is_file() and file.name != 'OVH_API_BASE_URL':
                value = file.read_bytes().strip()
                if len(value) >= 8:
                    secrets.add(value)
prepared = {}
for relative in paths:
    if not relative or relative == 'docker-compose.yml':
        continue
    file = source / relative
    if file.is_symlink() or not file.resolve().is_relative_to(source):
        raise SystemExit('Unsafe source path: ' + relative)
    if not file.exists():
        continue
    content = file.read_bytes()
    if any(value in content for value in secrets):
        raise SystemExit('Credential detected in: ' + relative)
    if re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----|\b(?:ghp_|github_pat_)[A-Za-z0-9_]{20,}|https?://[^\s/@:]+:[^\s/@]+@', content):
        raise SystemExit('Potential credential detected in: ' + relative)
    prepared[relative] = (content, file.stat().st_mode & 0o777)
# Publish a parameterized deployment example instead of the live host configuration.
compose = (source / 'docker-compose.yml').read_text()
compose = re.sub(r'\d+\.\d+\.\d+\.\d+:\d+:8080', '${IDE_BIND_ADDRESS:-127.0.0.1}:${IDE_PORT:-5734}:8080', compose)
compose = compose.replace('/mnt/SSHFS/TRINITY-CLOUD/mnt/ENTREPRISE/SERVER/TRINITY/codex', '${CODEX_DATA_DIR:-./data/codex}')
compose = compose.replace('/mnt/SSHFS/TRINITY-CLOUD/mnt/ENTREPRISE/SERVER/TRINITY/ovh-agent', '${AGENT_DATA_DIR:-./data/agent}')
compose = '\n'.join(line for line in compose.splitlines() if '/mnt/SSHFS/' not in line) + '\n'
compose = compose.replace('/etc/trinity/docker-trinity/', '${AGENT_SECRETS_DIR:-./secrets}/')
compose = compose.replace('/var/docker/docker_ide/', './')
if re.search(r'\b(?:10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)\b', compose):
    raise SystemExit('Private address in deployment example')
if any(value in compose.encode() for value in secrets):
    raise SystemExit('Credential in deployment example')
prepared['docker-compose.example.yml'] = (compose.encode(), 0o644)
# Refuse symlink destinations; never traverse a destination supplied by an archive.
target.mkdir(parents=True, exist_ok=True)
for relative, (content, mode) in prepared.items():
    destination = target / relative
    if destination.is_symlink() or not destination.resolve().is_relative_to(target.resolve()):
        raise SystemExit('Unsafe destination: ' + relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() or destination.read_bytes() != content:
        destination.write_bytes(content)
    destination.chmod(mode)
print('Public source export:', len(prepared), 'files; live compose and private data excluded.')
