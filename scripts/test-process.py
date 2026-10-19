"""Process boundaries: real child cleanup, flood limits and sandbox fail-closed."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ovh_process as runner


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_python(self, source, **kwargs):
        return runner.run_process([sys.executable, '-c', source], self.root, **kwargs)

    def assert_child_stopped(self, pid):
        # A reparented zombie is already unable to run or mutate; PID 1 owns its
        # eventual reaping. Do not confuse existence in /proc with a live child.
        for _ in range(50):
            try:
                state = Path(f'/proc/{pid}/stat').read_text().split(') ', 1)[1].split()[0]
            except (FileNotFoundError, ProcessLookupError):
                # Linux may reap the process after open() but before read();
                # ESRCH, like ENOENT, means the observed child is already gone.
                return
            if state == 'Z':
                return
            time.sleep(.01)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.fail('Child remained alive after command cleanup')

    def test_reaped_child_during_proc_read_is_stopped(self):
        with patch.object(Path, 'read_text', side_effect=ProcessLookupError), patch.object(os, 'kill') as kill:
            self.assert_child_stopped(12345)
            kill.assert_not_called()
        # Unrelated access errors still fail instead of certifying cleanup.
        with patch.object(Path, 'read_text', side_effect=PermissionError), self.assertRaises(PermissionError):
            self.assert_child_stopped(12345)

    def test_success_and_exit_status_are_real(self):
        result = self.run_python('import os; os.write(1,b"OUT\\n"); os.write(2,b"ERR\\n")')
        self.assertTrue(result['passed'])
        self.assertTrue(result['executed'])
        self.assertEqual(result['exit_code'], 0)
        self.assertIn('OUT', result['output'])
        self.assertIn('ERR', result['output'])
        failed = self.run_python('raise SystemExit(7)')
        self.assertFalse(failed['passed'])
        self.assertEqual(failed['exit_code'], 7)

    def test_argv_is_not_interpolated_by_shell(self):
        argument = '$(touch DO_NOT_CREATE); $HOME'
        result = runner.run_process([sys.executable, '-c', 'import sys; print(sys.argv[1])', argument], self.root)
        self.assertEqual(result['output'].strip(), argument)
        self.assertFalse((self.root / 'DO_NOT_CREATE').exists())

    def test_inherited_credentials_are_not_passed_or_logged(self):
        names = ['OVH_AI_API_KEY', 'OVH_APPLICATION_KEY', 'OVH_CONSUMER_KEY', 'OPENAI_API_KEY',
                 'ANTHROPIC_API_KEY', 'GH_TOKEN', 'GITHUB_TOKEN', 'VSCE_PAT', 'NPM_TOKEN',
                 'SSH_AUTH_SOCK', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_VALUE_0', 'GIT_ASKPASS',
                 'BASH_ENV', 'ENV', 'LD_PRELOAD', 'PRIVATE_KEY', 'AZURE_DEVOPS_EXT_PAT', 'HTTP_AUTHORIZATION']
        fake = {name: 'PRIVATE_FIXTURE_DO_NOT_LEAK' for name in names}
        fake['PROJECT_MODE'] = 'test'
        with patch.dict(os.environ, fake):
            result = self.run_python('import os,json; print(json.dumps(dict(os.environ)))')
        self.assertTrue(result['passed'])
        environment = json.loads(result['output'])
        self.assertTrue(all(name not in environment for name in names))
        self.assertEqual(environment['PROJECT_MODE'], 'test')
        self.assertNotIn('PRIVATE_FIXTURE_DO_NOT_LEAK', json.dumps(result))

    def test_timeout_kills_shell_and_ordinary_descendants(self):
        source = ('import subprocess,sys,time; child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
                  'print(child.pid,flush=True); time.sleep(60)')
        result = self.run_python(source, timeout=.25)
        self.assertTrue(result['timed_out'])
        self.assertFalse(result['passed'])
        self.assertIsNotNone(result['exit_code'])
        self.assert_child_stopped(int(result['output'].strip()))

    def test_background_child_stops_even_when_shell_exits_successfully(self):
        result = runner.run_process(['/bin/sh', '-c', 'sleep 60 & echo $!'], self.root)
        self.assertTrue(result['passed'], result)
        self.assert_child_stopped(int(result['output'].strip()))

    def test_closed_output_does_not_bypass_timeout(self):
        result = self.run_python('import os,time; os.close(1); os.close(2); time.sleep(60)', timeout=.1)
        self.assertTrue(result['timed_out'])
        self.assertFalse(result['passed'])

    def test_large_successful_output_retains_a_bounded_tail(self):
        result = self.run_python('import os; os.write(1,b"x"*200000+b"END_OF_OUTPUT")', output_limit=128)
        self.assertTrue(result['passed'], result)
        self.assertTrue(result['output_truncated'])
        self.assertLessEqual(len(result['output']), 128)
        self.assertTrue(result['output'].endswith('END_OF_OUTPUT'))

    def test_output_flood_is_terminated_before_unbounded_capture(self):
        result = self.run_python('import os\nwhile True: os.write(1,b"flood"*13108)', output_limit=256)
        self.assertFalse(result['passed'])
        self.assertEqual(result['error'], 'output_limit_exceeded')
        self.assertFalse(result['timed_out'])
        self.assertLessEqual(result['output_bytes'], 16 * 1024 * 1024 + 1)
        self.assertLessEqual(len(result['output']), 256)
        self.assertIsNotNone(result['exit_code'])

    def test_startup_failure_returns_no_secret_arguments(self):
        with patch.object(runner.subprocess, 'Popen', side_effect=OSError('PRIVATE_FIXTURE_DO_NOT_LEAK')):
            result = runner.run_process(['/missing', 'PRIVATE_FIXTURE_DO_NOT_LEAK'], self.root)
        self.assertFalse(result['executed'])
        self.assertFalse(result['passed'])
        self.assertEqual(result['error'], 'process_start_failed')
        self.assertNotIn('PRIVATE_FIXTURE_DO_NOT_LEAK', json.dumps(result))

    def test_invalid_parameters_never_launch(self):
        with patch.object(runner.subprocess, 'Popen', side_effect=AssertionError('Invalid command launched')):
            for kwargs in ({'timeout':0}, {'timeout':float('inf')}, {'profile':'automatic'}, {'output_limit':0}):
                with self.subTest(kwargs=kwargs):
                    result = runner.run_process(['/bin/true'], self.root, **kwargs)
                    self.assertFalse(result['executed'])
                    self.assertEqual(result['error'], 'invalid_process_arguments')

    def test_sandbox_command_has_only_one_writable_host_mount(self):
        argv = runner.sandbox_argv(['/bin/sh', '-c', 'true'], self.root, self.root, '/usr/bin/bwrap')
        writable = [argv[index+1:index+3] for index, value in enumerate(argv) if value == '--bind']
        self.assertEqual(writable, [[str(self.root), str(self.root)]])
        self.assertIn('--unshare-all', argv)
        self.assertIn('--unshare-user', argv)
        self.assertIn('--disable-userns', argv)
        self.assertNotIn('--share-net', argv)
        self.assertNotIn('--dev-bind', argv)
        self.assertNotIn('--not-a-security-boundary', argv)
        self.assertNotIn('/etc', argv)
        self.assertEqual(argv[-4:], ['--', '/bin/sh', '-c', 'true'])

    def test_sandbox_rejects_cwd_outside_workspace(self):
        with patch.object(runner.shutil, 'which', return_value='/usr/bin/bwrap'):
            result = runner.run_process(['/bin/true'], self.root.parent, profile='workspace', workspace=self.root)
        self.assertFalse(result['executed'])
        self.assertEqual(result['error'], 'sandbox_setup_failed')

    def test_missing_sandbox_never_falls_back_to_approved(self):
        with patch.object(runner.shutil, 'which', return_value=None), patch.object(
                runner.subprocess, 'Popen', side_effect=AssertionError('Sandbox fallback attempted')):
            result = runner.run_process(['/bin/true'], self.root, profile='workspace', workspace=self.root)
        self.assertFalse(result['executed'])
        self.assertEqual(result['error'], 'sandbox_unavailable')

    def test_failed_sandbox_setup_cannot_run_payload_locally(self):
        # /bin/false stands in for a sandbox executable that refuses setup. The
        # payload would write a marker if incorrectly retried without isolation.
        with patch.object(runner.shutil, 'which', return_value='/bin/false'):
            result = self.run_python('open("DO_NOT_CREATE","w").write("ran")', profile='workspace', workspace=self.root)
        self.assertFalse(result['executed'])
        self.assertFalse(result['passed'])
        self.assertFalse((self.root / 'DO_NOT_CREATE').exists())
        self.assertEqual(result['error'], 'sandbox_setup_failed')

    def test_real_workspace_sandbox_when_namespaces_are_available(self):
        workspace = self.root / 'project'
        workspace.mkdir()
        outside = self.root / 'outside.txt'
        outside.write_text('PRIVATE_FIXTURE')
        source = ('import os,pathlib,socket; '
                  f'assert not pathlib.Path({str(outside)!r}).exists(); '
                  'assert {name for _,name in socket.if_nameindex()} <= {"lo"}; '
                  'pathlib.Path("inside.txt").write_text("ok")')
        result = runner.run_process([sys.executable, '-c', source], workspace,
                                    profile='workspace', workspace=workspace, timeout=5)
        unsupported = (result.get('error') == 'sandbox_unavailable' or any(
            message in result['output'].lower() for message in ('no permissions to create a new namespace',
            'creating new namespace failed: operation not permitted', 'user namespaces are not enabled')))
        if not result['executed'] and unsupported:
            self.assertFalse((workspace / 'inside.txt').exists())
            self.skipTest('OS sandbox unavailable here: ' + result['error'] + '; no fallback or host configuration changed')
        self.assertTrue(result['passed'], result)
        self.assertEqual((workspace / 'inside.txt').read_text(), 'ok')


if __name__ == '__main__':
    unittest.main()
