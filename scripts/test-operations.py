"""Offline transaction probes; no real service, database or SSH calls."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ovh_operations import OperationSuite
from ovh_task_engine import TaskJournal


class SimulatedTarget:
    def __init__(self, apply='success', rollback='success'):
        self.state = 'original'
        self.apply_behavior = apply
        self.rollback_behavior = rollback
        self.calls = []

    def __call__(self, argv, cwd, timeout, *, scope, target):
        self.calls.append({'phase': argv[0], 'cwd': str(cwd), 'scope': scope, 'target': target})
        if argv[0] == 'apply':
            self.state = 'desired' if self.apply_behavior in {'success', 'timeout', 'interrupt'} else 'partial'
            if self.apply_behavior == 'timeout':
                raise TimeoutError('remote command may still have effects')
            if self.apply_behavior == 'interrupt':
                raise KeyboardInterrupt()
            return {'exit_code': 0}
        if argv[0] == 'verify':
            return {'exit_code': 0 if self.state == 'desired' else 1}
        if argv[0] == 'rollback':
            if self.rollback_behavior == 'success':
                self.state = 'original'
                return {'exit_code': 0}
            if self.rollback_behavior == 'timeout':
                raise TimeoutError()
            return {'exit_code': 1}
        if argv[0] == 'verify_rollback':
            return {'exit_code': 0 if self.state == 'original' else 1}
        if argv[0] == 'verify_idle':
            return {'exit_code': 0}
        raise AssertionError('Unexpected fixture command')


class Transactions(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / 'project'
        self.workspace.mkdir()
        self.policy = self.workspace / '.ovh-agent-checks.json'
        self.row = {'name': 'deploy', 'kind': 'service', 'scope': 'local', 'cwd': '.', 'timeout': 5,
                    'apply': ['apply'], 'verify': ['verify'], 'rollback': ['rollback'],
                    'verify_rollback': ['verify_rollback'], 'verify_idle': ['verify_idle']}
        self.configure()

    def configure(self):
        self.policy.write_text(json.dumps({'version': 1, 'operations': {'version': 1, 'plans': [self.row]}}))

    def suite(self, runner=None):
        return OperationSuite(self.workspace, runner=runner, journal=TaskJournal(self.root / 'journal'))

    def test_apply_success_and_verified_repeat_does_not_reapply(self):
        runner = SimulatedTarget()
        suite = self.suite(runner)
        result = suite.execute('deploy')
        self.assertEqual(result['status'], 'applied')
        self.assertTrue(result['verified'])
        self.assertEqual([call['phase'] for call in runner.calls], ['apply', 'verify'])
        again = suite.execute('deploy')
        self.assertFalse(again['executed'])
        self.assertEqual(again['reason'], 'already_applied')
        self.assertEqual(sum(call['phase'] == 'apply' for call in runner.calls), 1)

    def test_failed_postcondition_rolls_back_and_checks_original_state(self):
        runner = SimulatedTarget(apply='bad')
        suite = self.suite(runner)
        result = suite.execute('deploy')
        self.assertEqual(result['status'], 'rolled_back')
        self.assertTrue(result['verified'])
        self.assertEqual(runner.state, 'original')
        self.assertIn('rollback', [call['phase'] for call in runner.calls])
        state = suite.journal.snapshot()
        self.assertEqual(state['unresolved_mutations'], [])
        self.assertEqual(len(state['operations']), 2)

    def test_failed_rollback_blocks_release_and_repetition(self):
        runner = SimulatedTarget(apply='bad', rollback='failure')
        suite = self.suite(runner)
        result = suite.execute('deploy')
        self.assertEqual(result['status'], 'rollback_failed')
        self.assertTrue(result['release_blocked'])
        count = len(runner.calls)
        self.assertEqual(suite.execute('deploy')['reason'], 'reconciliation_required')
        self.assertEqual(len(runner.calls), count)

    def test_timeout_is_unknown_then_probe_reconciles_without_replaying(self):
        runner = SimulatedTarget(apply='timeout')
        suite = self.suite(runner)
        result = suite.execute('deploy')
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual([call['phase'] for call in runner.calls], ['apply'])
        restored = self.suite(runner)
        self.assertEqual(restored.execute('deploy')['reason'], 'reconciliation_required')
        receipt = restored.reconcile(result['operation_id'])
        self.assertEqual(receipt['status'], 'applied')
        self.assertEqual([call['phase'] for call in runner.calls], ['apply', 'verify_idle', 'verify'])
        self.assertFalse(restored.execute('deploy')['executed'])

    def test_interrupt_persists_unknown_and_later_instance_recovers(self):
        runner = SimulatedTarget(apply='interrupt')
        suite = self.suite(runner)
        with self.assertRaises(KeyboardInterrupt):
            suite.execute('deploy')
        entry = suite.journal.snapshot()['operations'][0]
        self.assertEqual(entry['status'], 'unknown')
        new = self.suite(runner)
        self.assertEqual(new.reconcile(entry['id'])['status'], 'applied')
        self.assertEqual(sum(call['phase'] == 'apply' for call in runner.calls), 1)

    def test_crash_after_successful_exit_before_probe_still_blocks_replay(self):
        runner = SimulatedTarget()
        suite = self.suite(runner)
        plan = suite.plans['deploy']
        identifier = suite.journal.start('operation:deploy', suite._arguments(plan), mutation=True, scope=str(self.workspace))
        runner.state = 'desired'
        suite.journal.finish(identifier, status='succeeded', evidence={'exit_code': 0})
        restored = self.suite(runner)
        self.assertEqual(restored.execute('deploy')['reason'], 'reconciliation_required')
        self.assertEqual(runner.calls, [])
        self.assertEqual(restored.reconcile(identifier)['status'], 'applied')
        self.assertEqual([call['phase'] for call in runner.calls], ['verify'])

    def test_policy_changes_invalidate_running_suite_and_old_recovery(self):
        runner = SimulatedTarget(apply='timeout')
        suite = self.suite(runner)
        pending = suite.execute('deploy')
        self.row['verify'] = ['replacement_probe']
        self.configure()
        before = len(runner.calls)
        self.assertEqual(suite.execute('deploy')['reason'], 'policy_changed')
        new = self.suite(runner)
        checked = new.reconcile(pending['operation_id'])
        self.assertEqual(checked['status'], 'unknown')
        self.assertFalse(checked['verified'])
        self.assertEqual(len(runner.calls), before)

    def test_remote_never_falls_back_to_local_and_uses_explicit_remote_cwd(self):
        self.row.update(kind='remote', scope='remote', target='fixture-host', remote_cwd='/srv/application')
        self.configure()
        without_transport = self.suite()
        self.assertEqual(without_transport.execute('deploy')['reason'], 'remote_runner_unavailable')
        self.assertEqual(without_transport.journal.snapshot()['operations'], [])
        runner = SimulatedTarget()
        suite = self.suite(runner)
        self.assertEqual(suite.execute('deploy')['status'], 'applied')
        self.assertTrue(all(call['cwd'] == '/srv/application' and call['scope'] == 'remote'
                            and call['target'] == 'fixture-host' for call in runner.calls))

    def test_invalid_policies_duplicate_keys_and_symlink_are_blocked(self):
        self.row['cwd'] = '../outside'
        self.configure()
        runner = SimulatedTarget()
        self.assertEqual(self.suite(runner).execute('deploy')['status'], 'blocked')
        self.policy.write_text('{"version":1,"version":1}')
        self.assertEqual(self.suite(runner).describe()['reason'], 'duplicate_policy_key')
        self.policy.unlink()
        foreign = self.root / 'outside.json'
        foreign.write_text('{"version":1}')
        self.policy.symlink_to(foreign)
        self.assertEqual(self.suite(runner).describe()['reason'], 'symlink_path')
        self.assertEqual(runner.calls, [])

    def test_verify_scripts_frozen_and_model_boolean_is_not_a_receipt(self):
        script = self.workspace / 'probe.py'
        script.write_text('raise SystemExit(1)\n')
        self.row['verify'] = ['python', 'probe.py']
        self.configure()
        suite = self.suite(SimulatedTarget())
        script.write_text('raise SystemExit(0)\n')
        self.assertEqual(suite.execute('deploy')['reason'], 'protected_file_changed')
        self.row['verify'] = ['verify']
        self.configure()
        suite = self.suite(lambda *args, **kwargs: True)
        result = suite.execute('deploy')
        self.assertEqual(result['status'], 'unknown')
        self.assertFalse(result['verified'])
        self.assertEqual(result['execution']['error'], 'invalid_executor_receipt')
        # Even forged evidence passed directly to the journal triggers real probes.
        checked = suite.journal.reconcile(result['operation_id'], evidence={'verified': True, 'outcome': 'applied'})
        self.assertEqual(checked['verification_status'], 'unverified')

    def test_contradictory_executor_receipt_never_passes_probe(self):
        simulator = SimulatedTarget()
        def runner(argv, *args, **kwargs):
            if argv[0] == 'verify':
                simulator.calls.append({'phase': 'verify'})
                return {'exit_code': 0, 'error': 'transport_failure'}
            return simulator(argv, *args, **kwargs)
        result = self.suite(runner).execute('deploy')
        self.assertEqual(result['status'], 'unknown')
        self.assertFalse(result['verified'])
        self.assertNotIn('rollback', [call['phase'] for call in simulator.calls])

    def test_unavailable_verifier_does_not_trigger_rollback(self):
        simulator = SimulatedTarget(apply='bad')
        def runner(argv, *args, **kwargs):
            if argv[0] == 'verify':
                simulator.calls.append({'phase': 'verify'})
                return {'exit_code': 127, 'output': 'dependency unavailable'}
            return simulator(argv, *args, **kwargs)
        result = self.suite(runner).execute('deploy')
        self.assertEqual(result['status'], 'unknown')
        self.assertNotIn('rollback', [call['phase'] for call in simulator.calls])

    def test_policy_or_proof_input_modified_inside_probe_invalidates_exit_zero(self):
        for changed in ('policy', 'proof'):
            with self.subTest(changed=changed):
                isolated = self.root / changed
                isolated.mkdir()
                policy = isolated / '.ovh-agent-checks.json'
                proof = isolated / 'expected.json'
                proof.write_text('{"version":"original"}')
                plan = {**self.row, 'protect': ['expected.json']}
                policy.write_text(json.dumps({'version': 1, 'operations': {'version': 1, 'plans': [plan]}}))
                calls = []
                def runner(argv, *args, **kwargs):
                    calls.append(argv[0])
                    if argv[0] == 'verify':
                        target = policy if changed == 'policy' else proof
                        target.write_text(target.read_text() + '\n')
                    return {'exit_code': 0}
                suite = OperationSuite(isolated, runner=runner, journal=TaskJournal(self.root / (changed + '-journal')))
                result = suite.execute('deploy')
                self.assertEqual(result['status'], 'unknown')
                self.assertFalse(result['verified'])
                self.assertEqual(calls, ['apply', 'verify'])
                self.assertEqual(suite.describe()['status'], 'blocked')

    def test_active_process_lock_prevents_false_interruption_recovery(self):
        suite = self.suite(SimulatedTarget())
        plan = suite.plans['deploy']
        identifier = suite.journal.start('operation:deploy', suite._arguments(plan), mutation=True, scope=str(self.workspace))
        with suite._exclusive():
            second = self.suite(SimulatedTarget())
            self.assertEqual(second.describe()['reason'], 'operation_in_progress')
        entry = next(row for row in suite.journal.snapshot()['operations'] if row['id'] == identifier)
        self.assertEqual(entry['status'], 'running')

    def test_rollback_timeout_remains_unknown_until_its_own_probe(self):
        runner = SimulatedTarget(apply='bad', rollback='timeout')
        suite = self.suite(runner)
        result = suite.execute('deploy')
        self.assertEqual(result['status'], 'rollback_unknown')
        runner.state = 'original'
        self.assertEqual(suite.reconcile(result['rollback_id'])['status'], 'rolled_back')
        self.assertEqual(suite.reconcile(result['operation_id'])['status'], 'rolled_back')

    def test_unknown_operation_cannot_be_reconciled_without_idle_probe(self):
        self.row.pop('verify_idle')
        self.configure()
        runner = SimulatedTarget(apply='timeout')
        suite = self.suite(runner)
        result = suite.execute('deploy')
        self.assertEqual(runner.state, 'desired')
        self.assertEqual(suite.reconcile(result['operation_id'])['status'], 'unknown')
        self.assertEqual([call['phase'] for call in runner.calls], ['apply'])

    def test_remote_delayed_effects_never_authorize_replay_before_idle(self):
        self.row.update(kind='remote', scope='remote', target='fixture-host', remote_cwd='/srv/app')
        self.configure()
        state = {'value': 'original', 'active': True}
        calls = []
        def delayed(argv, *args, **kwargs):
            phase = argv[0]
            calls.append(phase)
            if phase == 'apply':
                return {'exit_code': None, 'timed_out': True}
            if phase == 'verify_idle':
                return {'exit_code': 1 if state['active'] else 0}
            if phase == 'verify':
                return {'exit_code': 0 if state['value'] == 'desired' else 1}
            if phase == 'verify_rollback':
                return {'exit_code': 0 if state['value'] == 'original' else 1}
            raise AssertionError('Unexpected rollback')
        suite = self.suite(delayed)
        result = suite.execute('deploy')
        self.assertEqual(suite.reconcile(result['operation_id'])['status'], 'unknown')
        self.assertEqual(calls, ['apply', 'verify_idle'])
        state['value'] = 'desired'  # Remote side effect arrives after caller timeout.
        self.assertEqual(suite.reconcile(result['operation_id'])['status'], 'unknown')
        state['active'] = False
        self.assertEqual(suite.reconcile(result['operation_id'])['status'], 'applied')
        self.assertFalse(suite.execute('deploy')['executed'])
        self.assertEqual(calls.count('apply'), 1)


if __name__ == '__main__':
    unittest.main()
