#!/usr/bin/env python3
"""Update the local client only, preserving publisher, branding and backend compatibility."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import urllib.error
import urllib.parse
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXTENSIONS = Path.home() / '.local/share/code-server/extensions'
API = 'https://api.github.com/repos/trinity-labs/trinity-agent/releases/latest'
PREFIX = 'https://github.com/trinity-labs/trinity-agent/releases/download/'
GO_RELEASES = 'https://go.dev/dl/?mode=json'
OPEN_VSX = 'https://open-vsx.org/api'
GO_ROOT = Path('/usr/local/go')
ADMINER_IMAGE = 'adminer:latest'
ADMINER_PROJECT = Path('/home/coder/TRINITY-DOCKER/var/docker/docker_postgres')
ADMINER_STATE = Path('/root/.config/trinity/adminer-base-image-id')
BACKEND_FILES = {'trinity_agent/__main__.py', 'trinity_agent/agent.py', 'trinity_agent/providers.py', 'trinity_agent/memory.py', 'trinity_agent/workspace.py', 'trinity_agent/project_memory.py', 'trinity_agent/context.py', 'trinity_agent/vision.py', 'trinity_agent/cache.py', 'trinity_agent/resources.py', 'trinity_agent/limits.py', 'trinity_agent/request.py', 'trinity_agent/budget.py', 'trinity_agent/budget_model.py', 'trinity_agent/evaluation.py', 'trinity_agent/verification.py', 'trinity_agent/instructions.py', 'trinity_agent/task_engine.py', 'trinity_agent/acceptance.py', 'trinity_agent/repository.py', 'trinity_agent/execution.py', 'trinity_agent/operations.py', 'trinity_agent/tools.py', 'trinity_agent/process.py', 'scripts/trinity-agent.sh'}


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise ValueError('Invalid stable version')
    return tuple(map(int, value.split('.')))


def fetch(url, limit):
    request = urllib.request.Request(url, headers={'User-Agent': 'TRINITY-Agent-Updater', 'Accept': 'application/json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Download exceeds size limit')
    return data


def fetch_json(url, limit=2 * 1024 * 1024):
    return json.loads(fetch(url, limit))


def numeric_version(value):
    """Compare stable numeric versions without assuming exactly three fields."""
    if not isinstance(value, str) or not re.fullmatch(r'\d+(?:\.\d+)+', value):
        raise ValueError('Invalid stable version')
    return tuple(map(int, value.split('.')))


def command_output(argv, timeout=30):
    return subprocess.run(argv, check=True, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=timeout).stdout.strip()


def validate(package, expected, root=ROOT):
    with zipfile.ZipFile(package) as archive:
        if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
            raise ValueError('Package exceeds expanded size limit')
        for item in archive.infolist():
            parts = item.filename.replace('\\', '/').split('/')
            if item.filename.startswith('/') or '..' in parts or (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Unsafe package entry')
        manifest = json.loads(archive.read('extension/package.json'))
        if (manifest.get('publisher'), manifest.get('name'), manifest.get('version')) != ('trinity', 'trinity-agent', expected):
            raise ValueError('Unexpected extension identity/version')
        backend = json.loads(archive.read('extension/local-update.json'))['backend']
        if set(backend) != BACKEND_FILES:
            raise ValueError('Missing backend compatibility metadata')
        for name, digest in backend.items():
            if hashlib.sha256((root / name).read_bytes()).hexdigest() != digest:
                raise ValueError('Update deferred: companion backend differs; update deployment sources first')


def installed_entry(extensions=EXTENSIONS):
    entries = json.loads((extensions / 'extensions.json').read_text())
    for entry in entries:
        if entry['identifier']['id'] == 'trinity.trinity-agent':
            return entry
    # One-time migration from an earlier local identity. Identify it from its
    # installed manifest instead of retaining that vendor-prefixed ID in code.
    for entry in entries:
        installed = extensions / Path(entry.get('relativeLocation', '')).name
        try:
            manifest = json.loads((installed / 'package.json').read_text())
        except (OSError, ValueError):
            continue
        if manifest.get('displayName') == 'TRINITY Agent' and entry.get('identifier', {}).get('id', '').startswith('trinity.'):
            return entry
    raise ValueError('Local TRINITY Agent is not installed')


def current():
    entry = installed_entry()
    return entry['version'], entry['identifier']['id']


def installed_extensions(extensions=EXTENSIONS):
    return json.loads((extensions / 'extensions.json').read_text())


def extension_entry(extension_id, extensions=EXTENSIONS):
    return next((entry for entry in installed_extensions(extensions)
                 if entry.get('identifier', {}).get('id') == extension_id), None)


def latest_go_release():
    machine = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(platform.machine())
    if not machine:
        raise ValueError('Unsupported Go architecture: ' + platform.machine())
    releases = fetch_json(GO_RELEASES)
    release = next((item for item in releases if item.get('stable')), None)
    if not release or not re.fullmatch(r'go\d+(?:\.\d+)+', release.get('version', '')):
        raise ValueError('No stable Go release found')
    binary = next((item for item in release.get('files', [])
                   if item.get('os') == 'linux' and item.get('arch') == machine
                   and item.get('kind') == 'archive'), None)
    if not binary or not re.fullmatch(r'[a-f0-9]{64}', binary.get('sha256', '')):
        raise ValueError('Stable Go archive metadata is incomplete')
    expected = f"{release['version']}.linux-{machine}.tar.gz"
    if binary.get('filename') != expected:
        raise ValueError('Unexpected Go archive name')
    return release['version'][2:], binary


def safe_extract_go(package, destination):
    with tarfile.open(package, 'r:gz') as archive:
        members = archive.getmembers()
        if sum(member.size for member in members) > 750 * 1024 * 1024:
            raise ValueError('Go archive exceeds expanded size limit')
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != 'go':
                raise ValueError('Unsafe Go archive entry')
            if member.issym() or member.islnk():
                target = Path(member.linkname)
                if target.is_absolute() or '..' in target.parts:
                    raise ValueError('Unsafe Go archive link')
        archive.extractall(destination, filter='data')


def validate_go(root, expected):
    go = root / 'bin/go'
    output = command_output([str(go), 'version'])
    if f'go{expected} ' not in output:
        raise ValueError('Go staging version check failed')
    with tempfile.TemporaryDirectory(prefix='trinity-go-smoke-') as directory:
        source = Path(directory) / 'main.go'
        source.write_text('package main\nfunc main() {}\n')
        subprocess.run([str(go), 'build', '-o', str(Path(directory) / 'smoke'), str(source)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120,
                       env={**os.environ, 'GOROOT': str(root)})


def update_go(go_root=GO_ROOT):
    if os.environ.get('TRINITY_GO_AUTO_UPDATE', 'true').lower() not in ('true', '1', 'yes'):
        return
    installed_text = command_output([str(go_root / 'bin/go'), 'version'])
    match = re.search(r'\bgo(\d+(?:\.\d+)+)\b', installed_text)
    if not match:
        raise ValueError('Unable to determine installed Go version')
    installed = match.group(1)
    target, archive = latest_go_release()
    if numeric_version(target) <= numeric_version(installed):
        print('Go auto-update: already current (' + installed + ')', flush=True)
        return
    url = 'https://go.dev/dl/' + archive['filename']
    data = fetch(url, 100 * 1024 * 1024)
    if hashlib.sha256(data).hexdigest() != archive['sha256']:
        raise ValueError('Go archive integrity check failed')
    parent = go_root.parent
    previous = parent / (go_root.name + '.previous')
    with tempfile.TemporaryDirectory(prefix='.trinity-go-', dir=parent) as directory:
        directory = Path(directory)
        package = directory / archive['filename']
        package.write_bytes(data)
        safe_extract_go(package, directory)
        staged = directory / 'go'
        validate_go(staged, target)
        previous_new = parent / (go_root.name + '.previous.new')
        if previous_new.exists():
            shutil.rmtree(previous_new)
        # A directory inherited from the container's lower overlay layer cannot
        # be renamed (EXDEV). Copy it first, then retain that validated copy as
        # the rollback toolchain before replacing the live tree.
        shutil.copytree(go_root, previous_new, symlinks=True)
        if previous.exists():
            shutil.rmtree(previous)
        previous_new.rename(previous)
        try:
            shutil.rmtree(go_root)
            staged.rename(go_root)
            validate_go(go_root, target)
        except Exception:
            if go_root.exists():
                shutil.rmtree(go_root)
            shutil.copytree(previous, go_root, symlinks=True)
            raise
    print(f'Go auto-update: installed {target}; previous toolchain kept at {previous}', flush=True)


def marketplace_candidates(extensions=EXTENSIONS):
    for entry in installed_extensions(extensions):
        extension_id = entry.get('identifier', {}).get('id', '')
        installed = entry.get('version', '')
        if entry.get('metadata', {}).get('pinned') is True:
            continue
        if extension_id == 'trinity.trinity-agent' or not re.fullmatch(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+', extension_id):
            continue
        namespace, name = extension_id.split('.', 1)
        url = f'{OPEN_VSX}/{urllib.parse.quote(namespace)}/{urllib.parse.quote(name)}/latest'
        try:
            release = fetch_json(url)
        except urllib.error.HTTPError as error:
            # Open VSX returns either 404 or 406 for a local/unpublished ID,
            # depending on its current content-negotiation path.
            if error.code in (404, 406):
                continue
            raise
        target = release.get('version', '')
        if release.get('verified') is not True:
            continue
        try:
            newer = numeric_version(target) > numeric_version(installed)
        except ValueError:
            continue
        if newer:
            yield extension_id, installed, target


def update_marketplace_extensions(extensions=EXTENSIONS):
    # Marketplace extensions are user-controlled. Keep this opt-in so a local
    # rollback or the editor's "Auto Update" switch cannot be silently undone.
    if os.environ.get('TRINITY_EXTENSIONS_AUTO_UPDATE', 'false').lower() not in ('true', '1', 'yes'):
        return
    updates = list(marketplace_candidates(extensions))
    if not updates:
        print('Extension auto-update: already current', flush=True)
        return
    for extension_id, installed, target in updates:
        before = extension_entry(extension_id, extensions)
        installed_dir = extensions / Path(before.get('relativeLocation', '')).name
        with tempfile.TemporaryDirectory(prefix='trinity-extension-rollback-') as directory:
            directory = Path(directory)
            registry_backup = directory / 'extensions.json'
            shutil.copy2(extensions / 'extensions.json', registry_backup)
            extension_backup = directory / 'extension'
            if installed_dir.is_dir():
                shutil.copytree(installed_dir, extension_backup, symlinks=True)
            try:
                subprocess.run(['code-server', '--install-extension', extension_id, '--force'],
                               check=True, timeout=180)
                after = extension_entry(extension_id, extensions)
                if not after:
                    raise ValueError('Extension installation verification failed: ' + extension_id)
                updated = numeric_version(after.get('version', '0.0')) > numeric_version(installed)
            except Exception:
                current_entry = extension_entry(extension_id, extensions)
                if current_entry:
                    current_dir = extensions / Path(current_entry.get('relativeLocation', '')).name
                    if current_dir != installed_dir and current_dir.is_dir():
                        shutil.rmtree(current_dir)
                shutil.copy2(registry_backup, extensions / 'extensions.json')
                if extension_backup.is_dir() and not installed_dir.exists():
                    shutil.copytree(extension_backup, installed_dir, symlinks=True)
                raise
        if updated:
            print(f"Extension auto-update: {extension_id} {installed} -> {after['version']}; reload to activate", flush=True)
        else:
            print(f'Extension auto-update: no newer compatible build for {extension_id} ({installed})', flush=True)


def update_adminer(project=ADMINER_PROJECT, state=ADMINER_STATE):
    if os.environ.get('TRINITY_ADMINER_AUTO_UPDATE', 'true').lower() not in ('true', '1', 'yes'):
        return
    if not (project / 'docker-compose.yml').is_file():
        raise ValueError('Adminer compose project is missing: ' + str(project))
    subprocess.run(['docker', 'pull', ADMINER_IMAGE], check=True, timeout=300)
    base_id = command_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', ADMINER_IMAGE])
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', base_id):
        raise ValueError('Invalid Adminer base image ID')
    try:
        previous = state.read_text().strip()
    except FileNotFoundError:
        previous = ''
    if previous == base_id:
        print('Adminer auto-update: already current (' + base_id[7:19] + ')', flush=True)
        return
    compose = ['docker', 'compose', '-f', str(project / 'docker-compose.yml')]
    subprocess.run(compose + ['build', '--pull', 'adminer'], check=True, timeout=900)
    subprocess.run(compose + ['up', '-d', '--no-deps', 'adminer'], check=True, timeout=300)
    if command_output(['docker', 'inspect', '--format', '{{.State.Running}}', 'trinity-adminer']) != 'true':
        raise ValueError('Adminer container is not running after rebuild')
    request = urllib.request.Request('http://192.168.4.3:8000/', headers={'User-Agent': 'TRINITY-Adminer-Updater'})
    last_error = None
    for attempt in range(15):
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read(256 * 1024)
                if response.status == 200 and b'Adminer' in body:
                    last_error = None
                    break
                last_error = ValueError('Adminer returned an invalid HTTP response')
        except (OSError, urllib.error.URLError) as error:
            last_error = error
        if attempt < 14:
            time.sleep(2)
    if last_error is not None:
        raise ValueError('Adminer HTTP validation failed') from last_error
    state.parent.mkdir(parents=True, exist_ok=True)
    temporary = state.with_suffix('.new')
    temporary.write_text(base_id + '\n')
    temporary.replace(state)
    print('Adminer auto-update: rebuilt from ' + base_id[7:19], flush=True)


def branding_environment(directory, extensions=EXTENSIONS):
    """Stage explicit branding assets; absent variables restore bundled TRINITY defaults."""
    env = dict(os.environ)
    entry = installed_entry(extensions)
    installed = extensions / Path(entry['relativeLocation']).name
    env.setdefault('TRINITY_AGENT_NAME', 'TRINITY Agent')
    env.setdefault('TRINITY_AGENT_PUBLISHER_NAME', 'TRINITY')
    for key in ('TRINITY_AGENT_APP_ICON', 'TRINITY_AGENT_MENU_ICON'):
        configured = env.get(key)
        if configured:
            source = Path(configured)
        else:
            env[key] = ''
            continue
        if not source.is_file() or source.stat().st_size > 1024 * 1024:
            raise ValueError('Update deferred: branding asset missing or too large: ' + key)
        copy = Path(directory) / (key + source.suffix)
        copy.write_bytes(source.read_bytes())
        env[key] = str(copy)
    env['TRINITY_AGENT_BRANDING_STRICT'] = 'true'
    return env


def update():
    installed, previous_id = current()
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
        subprocess.run(['node', str(ROOT / 'branding/trinity-branding.cjs')], env=branding, check=True, timeout=30)
        subprocess.run(['code-server', '--install-extension', str(package), '--force'], check=True, timeout=180)
        subprocess.run(['node', str(ROOT / 'branding/trinity-branding.cjs')], env=branding, check=True, timeout=30)
        if previous_id != 'trinity.trinity-agent':
            subprocess.run(['code-server', '--uninstall-extension', previous_id], check=True, timeout=180)
    if current()[0] != target:
        raise ValueError('Installation verification failed')
    print('Auto-update: installed ' + target + '; reload the editor to activate. No task interrupted.', flush=True)


def main():
    if os.environ.get('TRINITY_AGENT_AUTO_UPDATE', 'true').lower() not in ('true', '1', 'yes'):
        return
    # flock is released even after crashes. Multiple entrypoints cannot race.
    with open('/tmp/trinity-agent-auto-update.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        try:
            interval = max(300, int(os.environ.get('TRINITY_AGENT_UPDATE_INTERVAL', '21600')))
        except ValueError:
            interval = 21600
        while True:
            failed = False
            for label, job in (('TRINITY Agent', update),
                               ('Go', update_go),
                               ('extensions', update_marketplace_extensions),
                               ('Adminer', update_adminer)):
                try:
                    job()
                except Exception as error:
                    failed = True
                    print(f'Auto-update ({label}): {error}', file=sys.stderr, flush=True)
            if '--watch' not in sys.argv:
                return 1 if failed else 0
            time.sleep(interval)


if __name__ == '__main__':
    sys.exit(main())
