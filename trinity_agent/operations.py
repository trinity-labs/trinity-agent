"""Configured transactions for specific effects, never a universal shell rollback.

At task start .trinity-agent-checks.json may contain::

    "operations": {"version": 1, "plans": [{
        "name": "deploy", "kind": "service", "scope": "local",
        "cwd": ".", "timeout": 30,
        "apply": ["python", "deploy.py"],
        "verify": ["python", "probe.py", "new"],
        "rollback": ["python", "restore.py"],
        "verify_rollback": ["python", "probe.py", "original"],
        "protect": ["deployment-policy.json"]
    }]}

All four argv lists are required. Probe exit 0 proves its configured condition,
1 disproves it, anything else is an unavailable probe. This does not establish
arbitrary user intent. cwd is relative and confined to the workspace. Referenced
local scripts and explicit protect files are frozen with the policy. A remote
plan requires target plus a controller-injected runner; it is never silently
executed on the local machine. No SSH/password/key policy is changed here.
Remote plans also require an explicit absolute remote_cwd; cwd remains the local
policy directory. Remote scripts are not inspected through an SSHFS path here.
Transitive imports, interpreters, system dependencies and indirectly read files
are not discovered automatically; task-specific proof inputs belong in protect.

runner(argv, cwd, timeout, *, scope, target) returns an executor-owned dictionary
with exit_code and optional timed_out/output/duration_ms. A boolean or model text
is not an executor receipt. Timeout/cancellation may leave effects: no automatic
apply or rollback is attempted until independently reconciled.
An optional verify_idle argv proves no work from the prior operation remains in
flight. It is required to reconcile an interrupted/unknown execution, including
an apparently correct current state: delayed effects may still be pending.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading

from trinity_agent.task_engine import JournalError, TaskJournal
from trinity_agent.verification import run_check

try:
    import fcntl
except ImportError:
    fcntl = None


class OperationError(RuntimeError):
    pass


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise OperationError('duplicate_policy_key')
        result[key] = value
    return result


class OperationSuite:
    """A frozen plan registry with durable intent and controller-owned probes.

    The optional journal should be a dedicated TaskJournal in the conversation's
    state directory. Recovery and execution take a nonblocking process lock, so
    another live executor cannot be mistaken for an interrupted operation.
    """

    def __init__(self, workspace, runner=None, journal=None):
        self.workspace = Path(workspace).resolve(strict=True)
        self.runner = runner
        self.lock = threading.RLock()
        self.plans = {}
        self.protected = {}
        self._latest_probes = {}
        self.error = None
        self.policy = None
        primary_policy = 'config/trinity-agent-checks.json'
        self.policy_name = primary_policy if (self.workspace / primary_policy).is_file() else '.trinity-agent-checks.json'
        directory = Path.home() / '.trinity-agent' / 'operation-journals' / _hash(str(self.workspace))[:24]
        self.journal = journal if journal is not None else TaskJournal(directory)
        previous = self.journal.verifier
        def dispatch(operation, evidence):
            if operation.get('tool', '').startswith(('operation:', 'rollback:')):
                return self._verify_operation(operation)
            if previous:
                return previous(operation, evidence)
            return {'verified': False, 'outcome': 'unknown', 'receipt': {'reason': 'unknown_operation'}}
        try:
            with self._exclusive():
                self.policy = self._snapshot(self.policy_name, maximum=262144, missing=True)
                if self.policy is not None:
                    content = self._read(self.policy_name, maximum=262144)
                    if self._snapshot(self.policy_name, maximum=262144) != self.policy:
                        raise OperationError('policy_changed_during_read')
                    document = json.loads(content, object_pairs_hook=_strict_pairs,
                                          parse_constant=lambda value: (_ for _ in ()).throw(OperationError('nonfinite_policy_value')))
                    self._configure(document)
                self.journal.recover()
        except (ValueError, TypeError, OSError, OperationError, JournalError) as error:
            self.error = str(error) if isinstance(error, OperationError) else type(error).__name__
        if self.error is None:
            self.journal.verifier = dispatch

    @contextmanager
    def _exclusive(self):
        if fcntl is None:
            raise OperationError('process_lock_unavailable')
        with self.lock:
            path = self.journal.directory / 'transactions.lock'
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise OperationError('unsafe_lock')
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise OperationError('operation_in_progress') from error
                try:
                    yield
                finally:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _path(self, raw, *, directory=False):
        if not isinstance(raw, str) or not raw or len(raw) > 4096 or '\x00' in raw:
            raise OperationError('invalid_path')
        relative = Path(raw)
        if relative.is_absolute() or '..' in relative.parts:
            raise OperationError('outside_workspace')
        candidate = self.workspace
        for part in relative.parts:
            candidate /= part
            if candidate.is_symlink():
                raise OperationError('symlink_path')
        if not candidate.resolve().is_relative_to(self.workspace):
            raise OperationError('outside_workspace')
        if directory and not candidate.is_dir():
            raise OperationError('cwd_unavailable')
        return candidate

    def _read(self, relative, *, maximum):
        path = self._path(relative)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
                raise OperationError('unsafe_or_oversized_policy_file')
            with os.fdopen(os.dup(descriptor), 'rb') as stream:
                content = stream.read(maximum + 1)
            after = os.fstat(descriptor)
            if len(content) > maximum or self._identity(before) != self._identity(after):
                raise OperationError('policy_changed_during_read')
            if self._identity(after) != self._identity(path.stat(follow_symlinks=False)):
                raise OperationError('policy_changed_during_read')
            return content
        finally:
            os.close(descriptor)

    @staticmethod
    def _identity(info):
        return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]

    def _snapshot(self, relative, *, maximum=1048576, missing=False):
        try:
            content = self._read(relative, maximum=maximum)
            return {'identity': self._identity(self._path(relative).stat()),
                    'sha256': hashlib.sha256(content).hexdigest()}
        except FileNotFoundError:
            if missing:
                return None
            raise OperationError('protected_file_missing')

    def _configure(self, document):
        if not isinstance(document, dict) or document.get('version') != 1:
            raise OperationError('invalid_policy_version')
        section = document.get('operations', {'version': 1, 'plans': []})
        if not isinstance(section, dict) or set(section) != {'version', 'plans'} or section['version'] != 1:
            raise OperationError('invalid_operations_section')
        if not isinstance(section['plans'], list) or len(section['plans']) > 32:
            raise OperationError('invalid_plan_list')
        for row in section['plans']:
            allowed = {'name', 'kind', 'scope', 'target', 'cwd', 'remote_cwd', 'timeout', 'apply', 'verify', 'rollback', 'verify_rollback', 'verify_idle', 'protect'}
            if not isinstance(row, dict) or set(row) - allowed:
                raise OperationError('unknown_plan_fields')
            name = row.get('name')
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,48}', name) or name in self.plans:
                raise OperationError('invalid_or_duplicate_plan_name')
            if row.get('kind') not in {'file', 'service', 'database', 'remote'} or row.get('scope') not in {'local', 'remote'}:
                raise OperationError('invalid_plan_scope')
            if row['kind'] == 'remote' and row['scope'] != 'remote':
                raise OperationError('remote_kind_requires_remote_scope')
            target = row.get('target')
            if row['scope'] == 'remote' and (not isinstance(target, str) or not re.fullmatch(r'[A-Za-z0-9_.@:-]{1,200}', target)):
                raise OperationError('remote_target_required')
            if row['scope'] == 'local' and target is not None:
                raise OperationError('unexpected_remote_target')
            remote_cwd = row.get('remote_cwd')
            if row['scope'] == 'remote' and (not isinstance(remote_cwd, str) or len(remote_cwd) > 1024
                    or '\x00' in remote_cwd or '\\' in remote_cwd or not Path(remote_cwd).is_absolute()
                    or '..' in Path(remote_cwd).parts):
                raise OperationError('absolute_remote_cwd_required')
            if row['scope'] == 'local' and remote_cwd is not None:
                raise OperationError('unexpected_remote_cwd')
            cwd = row.get('cwd', '.')
            directory = self._path(cwd, directory=True)
            timeout = row.get('timeout', 30)
            if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 600:
                raise OperationError('invalid_timeout')
            plan = {'name': name, 'kind': row['kind'], 'scope': row['scope'], 'target': target,
                    'cwd': cwd, 'remote_cwd': remote_cwd, 'timeout': timeout,
                    'directory_identity': [directory.stat().st_dev, directory.stat().st_ino]}
            protect = row.get('protect', [])
            if not isinstance(protect, list) or len(protect) > 16 or not all(isinstance(value, str) for value in protect):
                raise OperationError('invalid_protected_paths')
            protected = set(protect)
            phases = ['apply', 'verify', 'rollback', 'verify_rollback']
            if 'verify_idle' in row:
                phases.append('verify_idle')
            for phase in phases:
                argv = row.get(phase)
                if not isinstance(argv, list) or not 1 <= len(argv) <= 64 or any(
                    not isinstance(value, str) or not value or len(value) > 4096 or '\x00' in value for value in argv
                ):
                    raise OperationError('invalid_argv')
                if row['scope'] == 'remote' and '{python}' in argv:
                    raise OperationError('explicit_remote_interpreter_required')
                plan[phase] = [sys.executable if value == '{python}' else value for value in argv]
                for index, value in enumerate(argv):
                    # Freeze code files, not data destinations which the apply
                    # command is expected to change. Extra inputs use protect.
                    if row['scope'] == 'remote' or Path(value).is_absolute() or value.startswith('-'):
                        continue
                    if Path(value).suffix.lower() in {'.py', '.js', '.cjs', '.mjs', '.sh'} or (index == 0 and '/' in value):
                        relative = (Path(cwd) / value).as_posix()
                        if self._path(relative).exists():
                            protected.add(relative)
            for relative in protected:
                self.protected[relative] = self._snapshot(relative)
            self.plans[name] = plan

    def _assert_policy(self):
        if self.error:
            raise OperationError(self.error)
        if self._snapshot(self.policy_name, maximum=262144, missing=True) != self.policy:
            raise OperationError('policy_changed')
        for relative, snapshot in self.protected.items():
            if self._snapshot(relative) != snapshot:
                raise OperationError('protected_file_changed')
        for plan in self.plans.values():
            directory = self._path(plan['cwd'], directory=True)
            current = directory.stat()
            if [current.st_dev, current.st_ino] != plan['directory_identity']:
                raise OperationError('cwd_changed')

    def _arguments(self, plan, parent=None):
        return {'plan': plan['name'], 'policy': self.policy, 'protected': self.protected,
                'plan_fingerprint': _hash(plan), 'parent': parent}

    def describe(self):
        try:
            self._assert_policy()
            error = None
        except (OperationError, OSError) as failure:
            error = str(failure) if isinstance(failure, OperationError) else type(failure).__name__
        return {'status': 'blocked' if error else 'ready', 'reason': error,
                'plans': [{'name': plan['name'], 'kind': plan['kind'], 'scope': plan['scope'],
                           'target': plan['target'], 'timeout': plan['timeout'],
                           'interruption_probe': 'verify_idle' in plan,
                           'available': not error and (plan['scope'] == 'local' or self.runner is not None)}
                          for plan in self.plans.values()],
                'limits': ['Only configured conditions are verified.',
                           'Rollback covers only the declared plan, never arbitrary shell effects.',
                           'A timeout may have effects and requires reconciliation before repetition.']}

    def _run(self, plan, phase):
        self._assert_policy()
        if plan['scope'] == 'remote' and self.runner is None:
            raise OperationError('remote_runner_unavailable')
        try:
            if self.runner is None:
                result = run_check(plan[phase], self._path(plan['cwd'], directory=True), plan['timeout'])
            else:
                cwd = Path(plan['remote_cwd']) if plan['scope'] == 'remote' else self._path(plan['cwd'], directory=True)
                result = self.runner(list(plan[phase]), cwd, plan['timeout'],
                                     scope=plan['scope'], target=plan['target'])
        except TimeoutError:
            result = {'exit_code': None, 'timed_out': True}
        except Exception as error:
            result = {'exit_code': None, 'error': type(error).__name__}
        # A probe may replace its own code/configuration before returning 0.
        # Its exit status is unusable if frozen proof inputs changed in flight.
        try:
            self._assert_policy()
        except (OperationError, OSError) as error:
            return {'exit_code': None, 'timed_out': False,
                    'error': str(error) if isinstance(error, OperationError) else 'policy_unavailable'}
        # No output/arguments are persisted: they may contain deployment secrets.
        if not isinstance(result, dict) or isinstance(result.get('exit_code'), bool) or (
            result.get('exit_code') is not None and not isinstance(result.get('exit_code'), int)
        ):
            return {'exit_code': None, 'error': 'invalid_executor_receipt', 'timed_out': False}
        error = str(result.get('error', ''))
        # Transport/controller failures outweigh a successful child exit. A
        # mixed receipt must not certify a deployment or authorize rollback.
        contradictory = (bool(error) or result.get('passed') is False or
                         result.get('success') is False or result.get('verified') is False or
                         result.get('status') in {'failed', 'error', 'unknown', 'blocked'})
        if contradictory:
            return {'exit_code': None, 'timed_out': result.get('timed_out') is True,
                    'error': 'executor_receipt_failure'}
        return {'exit_code': result.get('exit_code'), 'timed_out': result.get('timed_out') is True,
                'error': error if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,80}', error) else 'executor_error' if error else '',
                'output_sha256': hashlib.sha256(str(result.get('output', '')).encode()).hexdigest()}

    def _probe(self, plan, phase):
        result = self._run(plan, phase)
        return result, None if result['timed_out'] or result['exit_code'] not in {0, 1} else result['exit_code'] == 0

    def _operation_plan(self, operation):
        pieces = operation.get('tool', '').split(':')
        if not ((len(pieces) == 2 and pieces[0] == 'operation') or
                (len(pieces) == 3 and pieces[0] == 'rollback' and re.fullmatch(r'[0-9a-f]{32}', pieces[2]))):
            raise OperationError('unknown_operation')
        parent = pieces[2] if pieces[0] == 'rollback' and len(pieces) == 3 else None
        if pieces[0] == 'rollback' and not parent:
            raise OperationError('invalid_rollback_record')
        plan = self.plans.get(pieces[1])
        if not plan or operation.get('arguments_hash') != _hash(self._arguments(plan, parent)):
            raise OperationError('operation_policy_mismatch')
        if operation.get('scope_hash') != _hash(plan['target'] or str(self.workspace)):
            raise OperationError('operation_scope_mismatch')
        return plan, parent

    def _verify_operation(self, operation):
        self._latest_probes.pop(operation['id'], None)
        try:
            self._assert_policy()
            plan, parent = self._operation_plan(operation)
            uncertain = operation.get('execution_status') in {'unknown', 'cancelled', 'partial'}
            if not parent:
                rollback_tool = 'rollback:' + plan['name'] + ':' + operation['id']
                uncertain = uncertain or any(row['tool'] == rollback_tool and
                    row.get('execution_status') in {'unknown', 'cancelled', 'partial'}
                    for row in self.journal.snapshot()['operations'])
            idle = None
            if uncertain:
                if 'verify_idle' not in plan:
                    self._latest_probes[operation['id']] = {'reason': 'execution_quiescence_unproven'}
                    return {'verified': False, 'outcome': 'unknown',
                            'receipt': {'reason': 'execution_quiescence_unproven'}}
                idle, stopped = self._probe(plan, 'verify_idle')
                if stopped is not True:
                    self._latest_probes[operation['id']] = {'reason': 'execution_may_be_active'}
                    return {'verified': False, 'outcome': 'unknown',
                            'receipt': {'reason': 'execution_may_be_active', 'idle': idle}}
            phase = 'verify_rollback' if parent else 'verify'
            probe, matched = self._probe(plan, phase)
            receipt = {'phase': phase, 'probe': probe, 'policy': self.policy['sha256'] if self.policy else None}
            if idle is not None:
                receipt['idle'] = idle
            if matched:
                return {'verified': True, 'outcome': 'rolled_back' if parent else 'applied', 'receipt': receipt}
            if parent or matched is None:
                return {'verified': False, 'outcome': 'unknown', 'receipt': receipt}
            baseline, baseline_match = self._probe(plan, 'verify_rollback')
            receipt['baseline'] = baseline
            self._latest_probes[operation['id']] = {'desired': matched, 'baseline': baseline_match}
            if baseline_match:
                outcome = 'not_applied'
                rollback_tool = 'rollback:' + plan['name'] + ':' + operation['id']
                for row in self.journal.snapshot()['operations']:
                    if row['tool'] == rollback_tool and row.get('verification_outcome') == 'rolled_back' and row.get('verification_status') == 'verified':
                        outcome = 'rolled_back'
                return {'verified': True, 'outcome': outcome, 'receipt': receipt}
            return {'verified': False, 'outcome': 'unknown', 'receipt': receipt}
        except (OperationError, OSError) as error:
            reason = str(error) if isinstance(error, OperationError) else type(error).__name__
            self._latest_probes[operation['id']] = {'reason': reason}
            return {'verified': False, 'outcome': 'unknown', 'receipt': {'reason': reason}}

    def _report(self, operation, **extra):
        return {'status': operation['status'], 'operation_id': operation['id'],
                'verified': operation.get('verification_status') == 'verified',
                'reason': self._latest_probes.get(operation['id'], {}).get('reason'),
                'retry_automatically': False, **extra}

    def reconcile(self, operation_id):
        """Run frozen independent probes only; never apply or rollback here."""
        try:
            with self._exclusive():
                self._assert_policy()
                result = self.journal.reconcile(operation_id, evidence={})
                return self._report(result, executed=False)
        except (OperationError, JournalError, OSError) as error:
            return {'status': 'blocked', 'executed': False, 'reason': str(error), 'retry_automatically': False}

    def execute(self, name):
        operation_id = None
        try:
            with self._exclusive():
                self._assert_policy()
                plan = self.plans.get(name)
                if plan is None:
                    raise OperationError('unknown_plan')
                if plan['scope'] == 'remote' and self.runner is None:
                    raise OperationError('remote_runner_unavailable')
                tool = 'operation:' + name
                previous = [row for row in self.journal.snapshot()['operations'] if row['tool'] == tool]
                for row in previous:
                    # A crash after exit 0 but before verification also leaves
                    # effects unresolved; execution success cannot allow replay.
                    if row.get('verification_status') != 'verified':
                        return {'status': 'blocked', 'operation_id': row['id'], 'executed': False,
                                'reason': 'reconciliation_required', 'retry_automatically': False}
                if previous and previous[-1].get('verification_outcome') == 'applied':
                    checked = self.journal.reconcile(previous[-1]['id'], evidence={})
                    if checked['status'] == 'applied':
                        return self._report(checked, executed=False, reason='already_applied')
                    if checked.get('verification_status') != 'verified':
                        return self._report(checked, executed=False, reason='reconciliation_required')
                operation_id = self.journal.start(tool, self._arguments(plan), mutation=True,
                                                  scope=plan['target'] or str(self.workspace), requires_verification=True)
                apply = self._run(plan, 'apply')
                uncertain = apply['timed_out'] or apply['exit_code'] is None
                self.journal.finish(operation_id, status='unknown' if uncertain else 'succeeded' if apply['exit_code'] == 0 else 'failed',
                                    evidence=apply)
                if uncertain:
                    return {'status': 'unknown', 'operation_id': operation_id, 'executed': True,
                            'verified': False, 'execution': apply, 'retry_automatically': False}
                checked = self.journal.reconcile(operation_id, evidence={})
                if checked['verification_status'] == 'verified':
                    return self._report(checked, executed=True, execution=apply)
                # Reuse the controller-owned observations from this exact
                # reconciliation; don't repeat checks to inflate evidence.
                probes = self._latest_probes.get(operation_id, {})
                if probes.get('desired') is not False or probes.get('baseline') is not False:
                    return self._report(checked, executed=True, reason='postcondition_unknown', execution=apply)
                self._assert_policy()
                rollback_id = self.journal.start('rollback:' + name + ':' + operation_id,
                    self._arguments(plan, operation_id), mutation=True, scope=plan['target'] or str(self.workspace),
                    requires_verification=True)
                try:
                    rollback = self._run(plan, 'rollback')
                except BaseException:
                    self.journal.finish(rollback_id, status='unknown', evidence={'interrupted': True})
                    raise
                rollback_unknown = rollback['timed_out'] or rollback['exit_code'] is None
                self.journal.finish(rollback_id, status='unknown' if rollback_unknown else 'succeeded' if rollback['exit_code'] == 0 else 'failed', evidence=rollback)
                if rollback_unknown:
                    return {'status': 'rollback_unknown', 'operation_id': operation_id, 'rollback_id': rollback_id,
                            'verified': False, 'executed': True, 'release_blocked': True, 'retry_automatically': False}
                restored = self.journal.reconcile(rollback_id, evidence={})
                if restored['verification_status'] != 'verified':
                    return {'status': 'rollback_failed', 'operation_id': operation_id, 'rollback_id': rollback_id,
                            'verified': False, 'executed': True, 'release_blocked': True, 'retry_automatically': False}
                checked = self.journal.reconcile(operation_id, evidence={})
                return self._report(checked, executed=True, execution=apply, rollback_id=rollback_id)
        except (OperationError, JournalError, OSError) as error:
            return {'status': 'blocked', 'operation_id': operation_id, 'executed': operation_id is not None,
                    'reason': str(error), 'retry_automatically': False}
        except BaseException:
            if operation_id:
                current = next(row for row in self.journal.snapshot()['operations'] if row['id'] == operation_id)
                if current['status'] in {'running', 'prepared'}:
                    self.journal.finish(operation_id, status='unknown', evidence={'interrupted': True})
            raise
