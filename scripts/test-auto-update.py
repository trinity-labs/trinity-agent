import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('updater', Path(__file__).with_name('auto-update.py'))
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)


class Validation(unittest.TestCase):
    def test_versions(self):
        self.assertGreater(u.version('1.14.0'), u.version('1.9.9'))
        for invalid in ('../1.0.0', 'v1.0.0', '1.0.0-rc1', None):
            with self.assertRaises(ValueError):
                u.version(invalid)

    def test_package_identity_backend_and_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            backend = {}
            for name in u.BACKEND_FILES:
                (root / name).write_text(name)
                backend[name] = hashlib.sha256(name.encode()).hexdigest()

            def package(publisher='trinity', unsafe=False):
                data = io.BytesIO()
                with zipfile.ZipFile(data, 'w') as z:
                    z.writestr('extension/package.json', json.dumps(dict(publisher=publisher, name='ovh-agent', version='1.14.0')))
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
            (root / 'ovh-agent.py').write_text('modified local backend')
            with self.assertRaisesRegex(ValueError, 'deferred'):
                u.validate(package(), '1.14.0', root)


if __name__ == '__main__':
    unittest.main()
