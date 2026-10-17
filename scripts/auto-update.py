#!/usr/bin/env python3
"""Update the local client only, preserving publisher, branding and backend compatibility."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = Path.home() / '.local/share/code-server/extensions'
API = 'https://api.github.com/repos/trinity-labs/trinity-agent/releases/latest'
PREFIX = 'https://github.com/trinity-labs/trinity-agent/releases/download/'
BACKEND_FILES = {'ovh-agent.py', 'ovh_memory.py', 'ovh_context.py', 'ovh_cache.py', 'ovh_request.py', 'ovh_budget.py', 'ovh_budget_model.py', 'ovh-agent.sh'}


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise ValueError('Invalid stable version')
    return tuple(map(int, value.split('.')))


def fetch(url, limit):
    request = urllib.request.Request(url, headers={'User-Agent': 'TRINITY-Agent-Updater', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Download exceeds size limit')
    return data


def validate(package, expected, root=ROOT):
    with zipfile.ZipFile(package) as archive:
        if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
            raise ValueError('Package exceeds expanded size limit')
        for item in archive.infolist():
            parts = item.filename.replace('\\', '/').split('/')
            if item.filename.startswith('/') or '..' in parts or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Unsafe package entry')
        manifest = json.loads(archive.read('extension/package.json'))
        if (manifest.get('publisher'), manifest.get('name'), manifest.get('version')) != ('trinity', 'ovh-agent', expected):
            raise ValueError('Unexpected extension identity/version')
        backend = json.loads(archive.read('extension/local-update.json'))['backend']
        if set(backend) != BACKEND_FILES:
            raise ValueError('Missing backend compatibility metadata')
        for name, digest in backend.items():
            if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
                raise ValueError('Update deferred: companion backend differs; update deployment sources first')


def current():
    entries = json.loads((EXTENSIONS / 'extensions.json').read_text())
    for entry in entries:
        if entry['identifier']['id'] == 'trinity.ovh-agent':
            return entry['version']
    raise ValueError('Local trinity.ovh-agent is not installed; no automatic migration')


def branding_environment(directory, extensions=EXTENSIONS):
    """Save installed branding before replacement; configured assets must remain available."""
    env = dict(os.environ)
    entry = next(e for e in json.loads((extensions / 'extensions.json').read_text())
                 if e['identifier']['id'] == 'trinity.ovh-agent')
    installed = extensions / Path(entry['relativeLocation']).name
    manifest = json.loads((installed / 'package.json').read_text())
    env.setdefault('OVH_AGENT_NAME', manifest['displayName'])
    env.setdefault('OVH_AGENT_PUBLISHER_NAME', entry.get('metadata', {}).get('publisherDisplayName', 'TRINITY'))
    icons = {'OVH_AGENT_APP_ICON': manifest.get('icon'),
             'OVH_AGENT_MENU_ICON': manifest.get('contributes', {}).get('viewsContainers', {}).get('activitybar', [{}])[0].get('icon')}
    for key, relative in icons.items():
        configured = env.get(key)
        if configured:
            source = Path(configured)
        elif key not in env and relative:
            source = installed / relative
            if not source.resolve().is_relative_to(installed.resolve()):
                raise ValueError('Unsafe installed branding path')
        else:
            continue  # Explicit empty value requests the bundled default.
        if not source.is_file() or source.stat().st_size > 1024 * 1024:
            raise ValueError('Update deferred: branding asset missing or too large: ' + key)
        copy = Path(directory) / (key + source.suffix)
        copy.write_bytes(source.read_bytes())
        env[key] = str(copy)
    env['OVH_AGENT_BRANDING_STRICT'] = 'true'
    return env


def update():
    installed = current()
    release = json.loads(fetch(API, 2 * 1024 * 1024))
    tag = release.get('tag_name', '')
    if release.get('draft') or release.get('prerelease') or not tag.startswith('v'):
        raise ValueError('Not a stable release')
    target = tag[1:]
    if version(target) <= version(installed):
        print('Auto-update: already current (' + installed + ')', flush=True)
        return
    name = 'trinity-agent-local-' + target + '.vsix'
    asset = next((a for a in release['assets'] if a['name'] == name), None)
    if not asset:
        raise ValueError('Local release asset not available yet')
    url = asset['browser_download_url']
    if url != PREFIX + tag + '/' + name:
        raise ValueError('Unexpected asset source')
    digest = asset.get('digest', '')
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', digest):
        raise ValueError('GitHub asset SHA-256 missing')
    data = fetch(url, 30 * 1024 * 1024)
    if 'sha256:' + hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('Release asset integrity check failed')
    with tempfile.TemporaryDirectory(prefix='trinity-update-') as directory:
        package = Path(directory) / name
        package.write_bytes(data)
        validate(package, target)
        branding = branding_environment(directory)
        # Validate/sanitize custom assets before modifying the installed extension.
        subprocess.run(['node', str(ROOT / 'ovh-branding.cjs')], env=branding, check=True, timeout=30)
        subprocess.run(['code-server', '--install-extension', str(package), '--force'], check=True, timeout=180)
        subprocess.run(['node', str(ROOT / 'ovh-branding.cjs')], env=branding, check=True, timeout=30)
    if current() != target:
        raise ValueError('Installation verification failed')
    print('Auto-update: installed ' + target + '; reload the editor to activate. No task interrupted.', flush=True)


def main():
    if os.environ.get('OVH_AGENT_AUTO_UPDATE', 'true').lower() not in ('true', '1', 'yes'):
        return
    # flock is released even after crashes. Multiple entrypoints cannot race.
    with open('/tmp/trinity-agent-auto-update.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        try:
            interval = max(300, int(os.environ.get('OVH_AGENT_UPDATE_INTERVAL', '21600')))
        except ValueError:
            interval = 21600
        while True:
            try:
                update()
            except Exception as error:
                print('Auto-update: ' + str(error), file=sys.stderr, flush=True)
                if '--watch' not in sys.argv:
                    return 1
            if '--watch' not in sys.argv:
                return 0
            time.sleep(interval)


if __name__ == '__main__':
    sys.exit(main())
