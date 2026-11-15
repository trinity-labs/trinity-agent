import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import tarfile
import unittest
from unittest.mock import MagicMock, call, patch
import zipfile

spec = importlib.util.spec_from_file_location('updater', Path(__file__).with_name('auto-update.py'))
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


class Validation(unittest.TestCase):
    def test_packager_and_updater_share_backend_contract(self):
        # Catch drift between the independently maintained package and installer:
        # a self-generated fixture alone cannot detect a missing backend module.
        import ast
        import re
        source = (Path(__file__).resolve().parents[1] / 'extension/scripts/package-vsix.cjs').read_text()
        lists = re.findall(r"for \(const name of (\[[^\]]+\])\)", source)
        backend = next(ast.literal_eval(value) for value in lists if 'trinity_agent/agent.py' in value)
        self.assertEqual(set(backend), u.BACKEND_FILES)

    def test_only_explicit_branding_survives_replacement(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old = root / 'trinity.trinity-agent-1.0.0'
            old.mkdir()
            (old / 'logo.svg').write_text('<svg/>')
            (old / 'package.json').write_text(json.dumps(dict(displayName='Customer Agent', icon='logo.svg')))
            (root / 'extensions.json').write_text(json.dumps([dict(identifier=dict(id='trinity.trinity-agent'), relativeLocation=old.name, metadata=dict(publisherDisplayName='Customer'))]))
            stage = root / 'stage'
            stage.mkdir()
            with patch.dict(u.os.environ, {}, clear=True):
                env = u.branding_environment(stage, root)
                self.assertEqual(env['TRINITY_AGENT_NAME'], 'TRINITY Agent')
                self.assertEqual(env['TRINITY_AGENT_PUBLISHER_NAME'], 'TRINITY')
                self.assertEqual(env['TRINITY_AGENT_APP_ICON'], '')
            with patch.dict(u.os.environ, {'TRINITY_AGENT_APP_ICON': str(old / 'logo.svg')}, clear=True):
                env = u.branding_environment(stage, root)
                self.assertEqual(Path(env['TRINITY_AGENT_APP_ICON']).read_text(), '<svg/>')
                (old / 'logo.svg').unlink()
                with self.assertRaisesRegex(ValueError, 'deferred'):
                    u.branding_environment(stage, root)

    def test_versions(self):
        self.assertGreater(u.version('1.14.0'), u.version('1.9.9'))
        self.assertGreater(u.version('1.18.0'), u.version('1.17.99'))
        self.assertGreater(u.version('2.0.0'), u.version('1.99.99'))
        self.assertLess(u.version('1.17.10'), u.version('1.18.0'))
        for invalid in ('../1.0.0', 'v1.0.0', '1.0.0-rc1', None):
            with self.assertRaises(ValueError):
                u.version(invalid)
        self.assertGreater(u.numeric_version('26.51111.21308'), u.numeric_version('26.901.22334'))

    def test_go_archive_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package = root / 'go.tar.gz'
            with tarfile.open(package, 'w:gz') as archive:
                payload = io.BytesIO(b'bad')
                info = tarfile.TarInfo('go/../../outside')
                info.size = 3
                archive.addfile(info, payload)
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                u.safe_extract_go(package, root / 'stage')

    def test_unpublished_local_extension_is_not_updated(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'extensions.json').write_text(json.dumps([
                {'identifier': {'id': 'local.theme'}, 'version': '1.0.0', 'relativeLocation': 'local.theme-1.0.0'},
                {'identifier': {'id': 'trinity.trinity-agent'}, 'version': '5.8.8', 'relativeLocation': 'trinity.trinity-agent-5.8.8'},
            ]))
            for status in (404, 406):
                error = u.urllib.error.HTTPError('url', status, 'missing', {}, None)
                with patch.object(u, 'fetch_json', side_effect=error):
                    self.assertEqual(list(u.marketplace_candidates(root)), [])

    def test_pinned_marketplace_extension_is_not_queried(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'extensions.json').write_text(json.dumps([{
                'identifier': {'id': 'openai.chatgpt'},
                'version': '26.901.22334',
                'relativeLocation': 'openai.chatgpt-26.901.22334-alpine-x64',
                'metadata': {'pinned': True},
            }]))
            with patch.object(u, 'fetch_json') as fetch:
                self.assertEqual(list(u.marketplace_candidates(root)), [])
                fetch.assert_not_called()

    def test_marketplace_updates_are_opt_in(self):
        with patch.dict(u.os.environ, {}, clear=True), \
             patch.object(u, 'marketplace_candidates') as candidates:
            u.update_marketplace_extensions()
            candidates.assert_not_called()

    def test_adminer_rebuilds_only_for_a_new_base_image(self):
        image_id = 'sha256:' + 'a' * 64
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'docker-compose.yml').write_text('services: {}\n')
            state = root / 'state'
            response = MagicMock(status=200)
            response.read.return_value = b'<title>Login - Adminer</title>'
            response.__enter__.return_value = response
            with patch.object(u.subprocess, 'run') as run, \
                 patch.object(u, 'command_output', side_effect=[image_id, 'true']), \
                 patch.object(u.urllib.request, 'urlopen', return_value=response):
                u.update_adminer(root, state)
            compose = ['docker', 'compose', '-f', str(root / 'docker-compose.yml')]
            self.assertEqual(run.call_args_list, [
                call(['docker', 'pull', 'adminer:latest'], check=True, timeout=300),
                call(compose + ['build', '--pull', 'adminer'], check=True, timeout=900),
                call(compose + ['up', '-d', '--no-deps', 'adminer'], check=True, timeout=300),
            ])
            self.assertEqual(state.read_text(), image_id + '\n')

            with patch.object(u.subprocess, 'run') as run, \
                 patch.object(u, 'command_output', return_value=image_id), \
                 patch.object(u.urllib.request, 'urlopen') as urlopen:
                u.update_adminer(root, state)
            run.assert_called_once_with(['docker', 'pull', 'adminer:latest'], check=True, timeout=300)
            urlopen.assert_not_called()

    def test_adminer_update_can_be_disabled(self):
        with patch.dict(u.os.environ, {'TRINITY_ADMINER_AUTO_UPDATE': 'false'}), \
             patch.object(u.subprocess, 'run') as run:
            u.update_adminer()
            run.assert_not_called()

    def test_adminer_waits_until_http_is_ready(self):
        image_id = 'sha256:' + 'b' * 64
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'docker-compose.yml').write_text('services: {}\n')
            response = MagicMock(status=200)
            response.read.return_value = b'Adminer'
            response.__enter__.return_value = response
            with patch.object(u.subprocess, 'run'), \
                 patch.object(u, 'command_output', side_effect=[image_id, 'true']), \
                 patch.object(u.urllib.request, 'urlopen', side_effect=[ConnectionResetError(), response]) as urlopen, \
                 patch.object(u.time, 'sleep') as sleep:
                u.update_adminer(root, root / 'state')
            self.assertEqual(urlopen.call_count, 2)
            sleep.assert_called_once_with(2)

    def test_previous_local_identity_is_discovered_from_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); installed=root/'legacy-agent-4.0.4'; installed.mkdir()
            (installed/'package.json').write_text(json.dumps({'displayName':'TRINITY Agent'}))
            entry={'identifier':{'id':'trinity.legacy-agent'},'version':'4.0.4',
                   'relativeLocation':installed.name,'metadata':{}}
            (root/'extensions.json').write_text(json.dumps([entry]))
            self.assertEqual(u.installed_entry(root),entry)

    def test_package_identity_backend_and_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            backend = {}
            for name in u.BACKEND_FILES:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(name)
                backend[name] = hashlib.sha256(name.encode()).hexdigest()

            def package(publisher='trinity', unsafe=False):
                data = io.BytesIO()
                with zipfile.ZipFile(data, 'w') as z:
                    z.writestr('extension/package.json', json.dumps(dict(publisher=publisher, name='trinity-agent', version='1.14.0')))
                    z.writestr('extension/local-update.json', json.dumps(dict(backend=backend)))
                    if unsafe:
                        z.writestr('../outside', 'bad')
                data.seek(0)
                return data

            u.validate(package(), '1.14.0', root)
            with self.assertRaisesRegex(ValueError, 'identity'):
                u.validate(package('stopyz'), '1.14.0', root)
            with self.assertRaisesRegex(ValueError, 'Unsafe'):
                u.validate(package(unsafe=True), '1.14.0', root)
            (root / 'trinity_agent/agent.py').write_text('modified local backend')
            with self.assertRaisesRegex(ValueError, 'deferred'):
                u.validate(package(), '1.14.0', root)


if __name__ == '__main__':
    unittest.main()
