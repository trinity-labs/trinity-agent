"""Tool execution, persistent uncertainty and independent acceptance.

The model chooses actions, but cannot write controller receipts. Generic shell
side effects remain uncertain after failure; only frozen probes can reconcile
configured transactions. File tools additionally retain before/after hashes.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from _thread import LockType
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import shlex
import stat
import threading
import time
import unicodedata
from typing import Any, TYPE_CHECKING

from pydantic_ai import RunContext, ToolFailed
from pydantic_ai.capabilities import AbstractCapability

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage
    from ovh_verification import ChangeGuard
    from ovh_tools import ToolSelection

from ovh_memory import atomic_write
from ovh_task_engine import TaskJournal, FailureTracker, classify_failure
from ovh_acceptance import AcceptanceSuite
from ovh_request import read_only_policy


@dataclass
class RunDependencies:
    """Mutable services and receipts owned by one Pydantic AI run."""
    verification: ChangeGuard | None = None
    engine: WorkflowController | None = None
    tools: ToolSelection | None = None
    full_access: bool = False
    external_access_notices: set[str] = field(default_factory=set)
    activity_started: dict[str, float] = field(default_factory=dict)
    activity_lock: LockType = field(default_factory=threading.Lock)
    request_receipts: list[dict] = field(default_factory=list)
    usage_reported: dict[str, dict] = field(default_factory=dict)
    services: dict[str, Any] = field(default_factory=dict)
    model_messages: list[ModelMessage] | None = None


@dataclass
class ToolExecution:
    execute: bool = True
    result: Any = None
    failed: bool = False


class ExecutionCapability(AbstractCapability[RunDependencies]):
    """Let Pydantic own scheduling while TRINITY records actual side effects."""

    def __init__(self, context: ContextVar[RunDependencies | None]):
        self.context = context

    async def wrap_run(self, ctx: RunContext[RunDependencies], *, handler):
        if not isinstance(ctx.deps, RunDependencies):
            raise TypeError('TRINITY runs require explicit RunDependencies.')
        token = self.context.set(ctx.deps)
        try:
            return await handler()
        finally:
            self.context.reset(token)

    async def wrap_tool_execute(self, ctx: RunContext[RunDependencies], *, call, tool_def, args, handler):
        engine = ctx.deps.engine
        if engine is None:
            return await handler(args)
        # Native validation has already produced the arguments; native execution
        # retains sync/async dispatch, sequential barriers and cancellation.
        with engine.execution(tool_def.name, args) as execution:
            if execution.execute:
                execution.result = await handler(args)
        # Preserve the exact evidence on Pydantic's failure channel without
        # spending the argument-correction retry budget or duplicating receipts.
        if execution.failed:
            result = execution.result
            raise ToolFailed(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False))
        return execution.result

MUTATIONS = frozenset({'edit_file', 'write_file', 'edit_file_batch', 'run_command',
                      'run_host_command', 'install_tools', 'remember_context'})
CONTROL = frozenset({'workflow_status', 'inspect_task_execution', 'verify_task_acceptance',
                     'reconcile_operation', 'execute_operation'})
FILE_TOOLS = frozenset({'edit_file', 'write_file', 'edit_file_batch'})
READ_ONLY_TOOLS = frozenset({'read_file', 'list_files', 'search_text', 'read_memory', 'search_memory',
    'search_project_memory', 'read_project_memory', 'inspect_repository', 'workflow_status',
    'inspect_task_execution', 'verify_task_acceptance', 'discover_tools', 'search_web', 'read_webpage',
    'inspect_styles', 'inspect_json', 'inspect_conversation_archives', 'inspect_environment', 'read_local_skill',
    'verification_plan', 'verify_extension_delivery', 'ask_user_questions', 'update_plan'})


def command_is_read_only(command):
    """Recognize a deliberately small set of local diagnostics.

    This is not a shell sandbox. Anything involving shell composition, expansion,
    executable hooks, remote execution or an unknown program stays a mutation.
    """
    if not isinstance(command, str) or re.search(r'[\n\r;&|<>`$()]', command):
        return False
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    if len(argv) == 2 and argv[1] == '--version' and argv[0] in {
            'git', 'gh', 'node', 'npm', 'bun', 'python', 'python3', 'docker', 'code-server'}:
        return True
    if not argv or argv[0] not in {'pwd', 'ls', 'stat', 'cat', 'head', 'tail', 'wc', 'grep', 'rg', 'test', '['}:
        return False
    if argv[0] == 'rg':
        for arg in argv[1:]:
            if arg == '--':
                break
            if (arg.split('=', 1)[0] in {'--pre', '--hostname-bin', '--search-zip'}
                    or arg.startswith('-') and not arg.startswith('--') and 'z' in arg[1:]):
                return False
    return not (argv[0] == '[' and argv[-1] != ']')


class WorkflowController:
    def __init__(self, workspace, directory, *, publish=None, receipts=None,
                 allow_external=False, operation_runner=None, trusted_diagnostics=False):
        self.workspace = Path(workspace).resolve()
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.publish = publish or (lambda event: None)
        self.receipts = receipts or (lambda: [])
        self.allow_external = allow_external
        # Only the application executor can promise direct argv, trusted system
        # executables and disabled external hooks. A tool name is not that proof.
        self.trusted_diagnostics = trusted_diagnostics is True
        self.acceptance = AcceptanceSuite(self.workspace)
        self.proofs = self.directory / 'file-proofs'
        self.proofs.mkdir(exist_ok=True, mode=0o700)
        self.command_proofs = self.directory / 'command-proofs'
        self.command_proofs.mkdir(exist_ok=True, mode=0o700)
        self.journal = TaskJournal(self.directory / 'actions', verifier=self._verify_action)
        # Backend session ownership excludes a previous worker before construction.
        self.journal.recover()
        from ovh_operations import OperationSuite
        self.transactions = TaskJournal(self.directory / 'transactions')
        self.operations = OperationSuite(self.workspace, runner=operation_runner, journal=self.transactions)
        # Journals persist for recovery across requests. Fallback safety must
        # inspect only operations started by this controller/request.
        self.initial_action_ids = {row['id'] for row in self.journal.snapshot()['operations']}
        self.initial_transaction_ids = {row['id'] for row in self.transactions.snapshot()['operations']}
        self.events = ContextVar('trinity_tool_events', default=None)
        self.lock = threading.RLock()
        self.failures = FailureTracker()
        self.progress = 0
        self.recent_observations = []
        self.focus_file = self.directory / 'focus.json'
        try:
            previous = json.loads(self.focus_file.read_text())
            self.goal_revision = int(previous['revision'])
            self.corrections = previous.get('corrections', [])[-4:]
            previous_read_only = previous.get('read_only')
            if not isinstance(previous_read_only, bool):
                previous_read_only = False
                for correction in self.corrections:
                    previous_read_only = read_only_policy(correction, previous_read_only)
        except (OSError, ValueError, KeyError, TypeError):
            self.goal_revision, self.corrections = 0, []
            previous_read_only = False
        self.goal = ''
        self.last_acceptance = None
        self.previous_acceptance = None
        try:
            saved = self.directory / 'acceptance.json'
            if saved.stat().st_size <= 1024 * 1024:
                previous_report = json.loads(saved.read_text())
                if isinstance(previous_report, dict):
                    self.previous_acceptance = previous_report
        except (OSError, ValueError, TypeError):
            pass
        self.incomplete = False
        self.command_results = []
        self.current_file_attempts = {}
        self._validation_retries = 0
        self.read_only = previous_read_only

    def focus(self, prompt):
        with self.lock:
            self.read_only = read_only_policy(prompt, self.read_only)
            self.goal_revision += 1
            self.recent_observations.clear()
            self.goal = str(prompt)[-4000:]
            self.corrections = (self.corrections + [self.goal])[-4:]
            self.last_acceptance = None
            self._validation_retries = 0
            # Persist user corrections separately so compaction cannot revive a
            # previous assistant conclusion as current evidence.
            atomic_write(self.focus_file, json.dumps({
                'revision': self.goal_revision, 'goal': self.goal,
                'corrections': self.corrections, 'previous_conclusions_valid': False,
                'read_only': self.read_only,
                'updated_at': time.time()}, ensure_ascii=False))

    def note_observation(self, name, arguments, result):
        # Compare observed results, not model claims of progress. Keep only hashes
        # so repeated large command outputs do not grow the controller itself.
        signature = hashlib.sha256(json.dumps([name, arguments, str(result), self.progress,
            self.goal_revision], sort_keys=True, default=str).encode()).hexdigest()
        with self.lock:
            self.recent_observations = (self.recent_observations + [signature])[-9:]

    def check_progress(self):
        with self.lock:
            recent = list(self.recent_observations)
        for width in (1, 2, 3):
            if len(recent) >= width * 3 and recent[-width:] * 3 == recent[-width * 3:]:
                self.incomplete = True
                raise RuntimeError('Tâche suspendue : la même séquence d’outils produit trois fois '
                    'les mêmes résultats sans progrès. Aucun nouvel appel modèle lancé ; '
                    'les résultats et coûts sont conservés pour une reprise explicite.')

    def observe_event(self, event):
        rows = self.events.get()
        if rows is not None and event.get('type') in {'tool_start', 'tool_end', 'tool_error'}:
            rows.append(dict(event))
        elif rows is not None and event.get('type') in {'verification_status', 'verification_check'}:
            rows.append({key: event.get(key) for key in ('type', 'status', 'phase', 'check')})

    @staticmethod
    def _file_content(path):
        if path.is_symlink():
            raise ValueError('Lien symbolique non réconciliable.')
        if not path.exists():
            return None
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > 8 * 1024 * 1024:
                raise ValueError('Fichier hors limite de preuve.')
            with os.fdopen(os.dup(descriptor), 'rb') as stream:
                content = stream.read(8 * 1024 * 1024 + 1)
            identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            if (len(content) > 8 * 1024 * 1024 or identity(before) != identity(os.fstat(descriptor))
                    or identity(before) != identity(path.stat(follow_symlinks=False))):
                raise ValueError('Fichier modifié pendant sa vérification.')
            return content
        finally:
            os.close(descriptor)

    @classmethod
    def _hash_file(cls, path):
        content = cls._file_content(path)
        return None if content is None else hashlib.sha256(content).hexdigest()

    def _file_proof(self, name, arguments):
        if name not in FILE_TOOLS:
            return None
        path = Path(arguments['path'])
        path = (self.workspace / path).absolute() if not path.is_absolute() else path
        if path.is_symlink():
            return None
        path = path.resolve()
        if not self.allow_external and not path.is_relative_to(self.workspace):
            return None
        try:
            content = self._file_content(path)
            before = None if content is None else hashlib.sha256(content).hexdigest()
            if name == 'write_file':
                expected = arguments['content'].encode('utf-8')
            else:
                if before is None:
                    return {'path': str(path), 'before': None, 'expected': None, 'valid': False}
                source = content.decode('utf-8')
                replacements = arguments.get('replacements', []) if name == 'edit_file_batch' else [
                    {'before': arguments['old_text'], 'after': arguments['new_text']}]
                updated = source
                for change in replacements:
                    old = change.get('old_text', change.get('before'))
                    new = change.get('new_text', change.get('after'))
                    if not isinstance(old, str) or not old or not isinstance(new, str) or updated.count(old) != 1:
                        return {'path': str(path), 'before': before, 'expected': before, 'valid': False}
                    updated = updated.replace(old, new, 1)
                expected = updated.encode('utf-8')
            return {'path': str(path), 'before': before, 'valid': True,
                    'expected': hashlib.sha256(expected).hexdigest()}
        except (OSError, UnicodeError, ValueError, KeyError):
            return None

    def _verify_file(self, operation, parameters):
        # Parameters from the model cannot choose the expected state or verdict.
        try:
            proof = json.loads((self.proofs / (operation['id'] + '.json')).read_text())
            path = Path(proof['path'])
            if not self.allow_external and not path.resolve().is_relative_to(self.workspace):
                raise ValueError('Hors périmètre courant.')
            current = self._hash_file(path)
            outcome = ('applied' if proof.get('valid', proof['before'] != proof['expected']) and current == proof['expected'] else
                       'not_applied' if current == proof['before'] else 'unknown')
            return {'verified': outcome != 'unknown', 'outcome': outcome,
                    'receipt': {'path': str(path), 'sha256': current, 'scope': 'file_content_only'}}
        except (OSError, ValueError, KeyError):
            return {'verified': False, 'outcome': 'unknown', 'receipt': {}}

    def _verify_action(self, operation, parameters):
        if operation['tool'] in {'run_command', 'run_host_command'}:
            try:
                proof = json.loads((self.command_proofs / (operation['id'] + '.json')).read_text())
                if (proof.get('arguments_hash') == operation['arguments_hash']
                        and proof.get('kind') in {'configured_check_execution', 'execution_not_started'}):
                    return {'verified': True,
                        'outcome': 'not_applied' if proof['kind'] == 'execution_not_started' else 'applied',
                        'receipt': {'scope': proof['kind'], 'source_receipt_id': proof.get('receipt_id'),
                                    'exit_code': proof.get('exit_code'), 'filesystem_effects_verified': False}}
            except (OSError, ValueError, KeyError, TypeError):
                pass
            return {'verified': False, 'outcome': 'unknown', 'receipt': {}}
        return self._verify_file(operation, parameters)

    def _command_proof(self, name, result, events):
        """Recognize controller receipts, never an exit code quoted by a shell.

        A configured assertion failure is a known check outcome, not an unknown
        deployment. This proves execution completion only: test success comes
        from the current ChangeGuard receipt, and arbitrary shell effects still
        have no generic rollback or reconciliation.
        """
        if name not in {'run_command', 'run_host_command'}:
            return None
        try:
            data = json.loads(result) if isinstance(result, str) else result
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        if (data.get('executed') is False and data.get('exit_code') is None
                and data.get('passed') is False and data.get('timed_out') is False
                and data.get('profile') in {'approved', 'workspace'}
                and data.get('error') in {'sandbox_unavailable', 'invalid_process_arguments', 'process_start_failed',
                                          'approval_denied', 'policy_denied', 'invalid_command_target'}):
            return {'kind': 'execution_not_started', 'exit_code': None}
        evidence = data.get('check_evidence')
        if name != 'run_command' or not isinstance(evidence, dict):
            return None
        identifier = evidence.get('id')
        if not isinstance(identifier, str) or not re.fullmatch(r'[0-9a-f]{32}', identifier):
            return None
        observed = next((row for row in reversed(self.receipts())
                         if isinstance(row, dict) and row.get('id') == identifier), None)
        keys = ('id', 'check', 'argv', 'cwd', 'exit_code', 'passed', 'timed_out')
        if (not observed or any(observed.get(key) != evidence.get(key) for key in keys)
                or type(observed.get('exit_code')) is not int or observed.get('timed_out')
                or observed.get('phase') != 'manual' or observed.get('changed_during_check')
                or observed.get('environment_changed') or observed.get('error')
                or observed.get('passed') is not (observed['exit_code'] == 0)):
            return None
        if observed['passed'] and any(row.get('success') is False or row.get('type') == 'tool_error' for row in events):
            return None
        if not self._fresh_check_receipt(observed):
            return None
        return {'kind': 'configured_check_execution', 'receipt_id': identifier, 'exit_code': observed['exit_code']}

    def _fresh_check_receipt(self, receipt):
        # ChangeGuard discovers checks in nested/external authorized projects;
        # root-only acceptance criteria must not silently reclassify those tests
        # as arbitrary shell deployments. Validate the actual controller-owned
        # receipt and its bounded fingerprints without rerunning a test.
        try:
            observed_at = datetime.fromisoformat(receipt.get('timestamp', ''))
            if (observed_at.tzinfo is None or observed_at < self.acceptance.started_at
                    or observed_at > datetime.now(timezone.utc)):
                return False
            fingerprints = receipt.get('fingerprints')
            if not isinstance(fingerprints, dict) or not 1 <= len(fingerprints) <= 512:
                return False
            total, policy_present = 0, False
            for raw, expected in fingerprints.items():
                if not isinstance(raw, str) or (expected is not None and
                        (not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected))):
                    return False
                path = Path(raw)
                if not path.is_absolute() or (not self.allow_external and not path.resolve().is_relative_to(self.workspace)):
                    return False
                if path.exists():
                    total += path.stat().st_size
                if total > 24_000_000 or self._hash_file(path) != expected:
                    return False
                policy_present |= path.name == '.ovh-agent-checks.json' and expected is not None
            return policy_present
        except (OSError, ValueError, TypeError):
            return False

    @staticmethod
    def _outcome(name, result, events):
        # A completed baseline check describes the state before the write.
        # Its nested command failures are not failures of the file operation.
        baseline_start = None
        baseline_events = set()
        if name in FILE_TOOLS:
            for index, row in enumerate(events):
                if row.get('type') == 'verification_status' and row.get('status') == 'running':
                    baseline_start = (index, row.get('check')) if row.get('phase') == 'before' else None
                elif row.get('type') == 'verification_check':
                    if (baseline_start is not None and row.get('phase') == 'before'
                            and row.get('check') == baseline_start[1]):
                        baseline_events.update(range(baseline_start[0], index))
                    baseline_start = None
        ended = [row for index, row in enumerate(events)
                 if row.get('type') == 'tool_end' and index not in baseline_events]
        explicit_failure = any(row.get('success') is False for row in ended) or any(row.get('type') == 'tool_error' for row in events)
        text = str(result)
        # Only parse the command protocol prefix, never arbitrary source/file
        # contents. A file containing "timeout" is still a successful read.
        match = re.match(r'^(?:host=\S+ )?exit=(-?\d+)\n', text)
        if name in {'run_command', 'run_host_command'} and match:
            command = {'exit_code': int(match[1]), 'output': text[match.end():]}
            if explicit_failure:
                command['success'] = False
            return classify_failure(command), {'exit_code': command['exit_code']}
        if name == 'read_file' and not explicit_failure:
            return 'none', None
        try:
            structured = json.loads(text) if text.startswith('{') else result
        except (TypeError, ValueError):
            structured = None
        if name == 'run_command' and isinstance(structured, dict) and isinstance(structured.get('check_evidence'), dict):
            receipt = dict(structured['check_evidence'])
            if explicit_failure:
                receipt['success'] = False
            return classify_failure(receipt), {key: receipt.get(key) for key in ('exit_code', 'passed', 'timed_out')}
        if name in {'run_command', 'run_host_command'} and isinstance(structured, dict) and 'exit_code' in structured:
            receipt = dict(structured)
            if explicit_failure or receipt.get('error'):
                receipt['success'] = False
            return classify_failure(receipt), {key: receipt.get(key) for key in ('exit_code', 'passed', 'timed_out', 'executed')}
        if isinstance(structured, dict) and (
                structured.get('passed') is False or structured.get('complete') is False
                or structured.get('status') in {'failed', 'blocked', 'unknown', 'error', 'timeout'}):
            explicit_failure = True
        if re.match(r'^(?:Erreur(?: outil| mémoire)?\s*:|Modification refusée|Écriture refusée|Commande (?:refusée|interrompue)|Accès refusé)', text):
            explicit_failure = True
        if explicit_failure:
            category = classify_failure(structured if isinstance(structured, dict) else text)
            return 'incorrect_result' if category == 'none' else category, None
        return 'none', None

    def _observation_revision(self, name, arguments):
        revision = [self.progress]
        # A real file discovered after several wrong guesses must remain usable.
        # Only target metadata invalidates the failed-call budget; unrelated reads
        # and paraphrasing the same nonexistent path do not count as progress.
        if name in {'read_file', 'list_files', 'inspect_json', 'inspect_styles'} and isinstance(arguments.get('path'), str):
            path = self.workspace / arguments['path']
            try:
                if not self.allow_external and not path.resolve().is_relative_to(self.workspace):
                    raise PermissionError('Outside current observation scope')
                info = path.stat()
                revision += [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
            except OSError:
                revision.append(None)
        return json.dumps(revision)

    def invoke(self, function, *args, **kwargs):
        """Direct Python execution shares the same receipts as native AI hooks."""
        bound = inspect.signature(function).bind(*args, **kwargs)
        bound.apply_defaults()
        with self.execution(function.__name__, dict(bound.arguments)) as execution:
            if execution.execute:
                execution.result = function(*args, **kwargs)
        return execution.result

    @contextmanager
    def execution(self, name: str, arguments: dict):
        """One receipt boundary for both synchronous and asynchronous tools."""
        diagnostic = (self.trusted_diagnostics and name == 'run_command'
                      and command_is_read_only(arguments.get('command')))
        # Enforce authorization at execution, including stale/hallucinated tool
        # calls and private wrappers. Hiding a JSON schema is only a UI measure.
        if self.read_only and name not in READ_ONLY_TOOLS and not diagnostic:
            yield ToolExecution(False, json.dumps({'status': 'blocked', 'category': 'read_only', 'executed': False,
                'tool': name, 'message': 'Consigne utilisateur de lecture seule active. Aucune action modificatrice exécutée.'}, ensure_ascii=False), failed=True)
            return
        if name in CONTROL:
            if name == 'execute_operation':
                self.last_acceptance = None
            execution = ToolExecution()
            yield execution
            execution.failed = self._outcome(name, execution.result, [])[0] != 'none'
            self.note_observation(name, arguments, execution.result)
            return
        revision = self._observation_revision(name, arguments)
        if self.failures.before_call(name, arguments, revision=revision)['blocked']:
            result = json.dumps({'status': 'blocked', 'category': 'no_progress', 'executed': False,
                'tool': name, 'message': 'Échecs répétés sans progrès vérifié. Inspecte la cause ou répare avant de répéter.'}, ensure_ascii=False)
            self.note_observation(name, arguments, result)
            yield ToolExecution(False, result, failed=True)
            return
        mutation = name in MUTATIONS and not diagnostic
        if mutation:
            # Failed commands can still change files; cached evidence is stale
            # as soon as a potentially mutating executor is entered.
            self.last_acceptance = None
        try:
            operation = self.journal.start(name, arguments, mutation=mutation,
                                           scope=str(arguments.get('host') or self.workspace),
                                           requires_verification=name in FILE_TOOLS)
        except (ValueError, RuntimeError) as error:
            yield ToolExecution(False, json.dumps({'status': 'blocked', 'category': 'recovery_required',
                               'executed': False, 'message': str(error)}, ensure_ascii=False), failed=True)
            return
        proof = self._file_proof(name, arguments)
        if proof is not None:
            atomic_write(self.proofs / (operation + '.json'), json.dumps(proof))
            self.current_file_attempts[proof['path']] = operation
        events = []
        token = self.events.set(events)
        execution = ToolExecution()
        try:
            yield execution
            result = execution.result
            category, command = self._outcome(name, result, events)
            failed = category != 'none'
            command_proof = self._command_proof(name, result, events)
            if command_proof is not None:
                entry = next(row for row in self.journal.snapshot()['operations'] if row['id'] == operation)
                atomic_write(self.command_proofs / (operation + '.json'), json.dumps(
                    {**command_proof, 'arguments_hash': entry['arguments_hash']}))
            status = 'unknown' if failed and mutation else 'failed' if failed else 'succeeded'
            if failed and command_proof is not None:
                status = 'failed'
            self.journal.finish(operation, status=status, detail=category,
                                evidence={'category': category, 'goal_revision': self.goal_revision})
            if command_proof is not None:
                status = self.journal.reconcile(operation, evidence={})['status']
            if name in FILE_TOOLS:
                # Only actual before/after fingerprints can prove no file effect;
                # absence of an activity event alone proves nothing.
                verified = self.journal.reconcile(operation, evidence={})
                status = verified['status']
                if not failed and verified.get('verification_outcome') != 'applied':
                    failed, category = True, 'incorrect_result'
                    result = json.dumps({'status': 'failed', 'category': category, 'operation_id': operation,
                        'executed': True, 'message': 'Le contenu réellement observé ne correspond pas à l’écriture annoncée. Relis le fichier avant correction.'}, ensure_ascii=False)
            if command is not None:
                self.command_results = (self.command_results + [command])[-64:]
            self.failures.record(name, arguments,
                                 {'category': category} if failed else {'exit_code': 0},
                                 revision=revision, succeeded=not failed)
            if not failed and mutation:
                self.last_acceptance = None
                if proof and proof['expected'] != proof['before'] and self._verify_file({'id': operation}, {})['outcome'] == 'applied':
                    self.progress += 1
            self.note_observation(name, arguments, result)
            self.publish({'type': 'workflow_action', 'operation': operation, 'tool': name,
                          'status': status, 'category': category})
            execution.result = result
            execution.failed = failed
        except BaseException as error:
            # Cancellation cannot prove that a remote command or write did not run.
            current = next(row for row in self.journal.snapshot()['operations'] if row['id'] == operation)
            if current['status'] in {'running', 'prepared'}:
                self.journal.finish(operation, status='unknown' if mutation else 'failed',
                                    detail=type(error).__name__, evidence={'category': classify_failure(error)})
                if name in FILE_TOOLS:
                    self.journal.reconcile(operation, evidence={})
            raise
        finally:
            self.events.reset(token)

    def reconcile(self, operation_id):
        if any(row['id'] == operation_id for row in self.transactions.snapshot()['operations']):
            return self.operations.reconcile(operation_id)
        return self.journal.reconcile(operation_id, evidence={})

    def evaluate(self, proposed_criteria=()):
        with self.lock:
            report = self.acceptance.evaluate(check_receipts=self.receipts(),
                proposed_criteria=proposed_criteria, command_results=self.command_results)
            self.last_acceptance = report
            atomic_write(self.directory / 'acceptance.json', json.dumps(report, ensure_ascii=False))
            return report

    def final_status(self):
        report = self.evaluate()
        actions = self.journal.snapshot()
        unresolved = actions['unresolved_mutations'] + self.transactions.snapshot()['unresolved_mutations']
        running = [row['id'] for row in actions['operations']
                   if row['status'] in {'running', 'prepared'}]
        latest_files = {}
        for row in actions['operations']:
            if row['tool'] in FILE_TOOLS and row.get('execution_status') == 'succeeded':
                try:
                    proof = json.loads((self.proofs / (row['id'] + '.json')).read_text())
                    latest_files[proof['path']] = row
                except (OSError, ValueError, KeyError):
                    continue  # A missing required proof remains unresolved above.
        failed_files = [row['id'] for row in latest_files.values()
                        if row.get('verification_outcome') != 'applied']
        # A rejected attempt is not itself a requested postcondition. If the
        # independent frozen contract passes, an attempt proven to have had no
        # effects must not poison completion (e.g. an obsolete path). Keep it in
        # the journal. Without a satisfied contract, the conservative block stays.
        current_ids = set(self.current_file_attempts.values())
        failed_files = list(dict.fromkeys(failed_files + [
            row['id'] for row in actions['operations']
            if row['id'] in current_ids and row.get('verification_outcome') != 'applied'
            and not (report['contract_satisfied'] is True
                     and row.get('verification_status') == 'verified'
                     and row.get('verification_outcome') == 'not_applied'
                     and row.get('may_have_effects') is False
                     and row.get('execution_status') != 'succeeded')]))
        failed_checks = [item['check'] for item in report['checks'] if item.get('status') == 'failed']
        self.incomplete = bool(unresolved or running or failed_files or failed_checks or report['criteria_status'] not in {'passed', 'not_configured'})
        result = {'incomplete': self.incomplete, 'unresolved': unresolved, 'running': running, 'failed_checks': failed_checks,
                  'historical_unresolved': [identifier for identifier in unresolved
                      if identifier in self.initial_action_ids | self.initial_transaction_ids],
                  'failed_files': failed_files, 'command_status': report['command_status'], 'test_status': report['test_status'],
                  'criteria_status': report['criteria_status'], 'contract_satisfied': report['contract_satisfied'],
                  'natural_language_goal_verified': False, 'satisfaction_scope': 'configured_contract_only'}
        self.publish({'type': 'execution_evidence', **result})
        return result

    def snapshot(self):
        return {'scope': str(self.workspace), 'goal_revision': self.goal_revision,
                'execution': self.journal.snapshot(), 'transactions': self.transactions.snapshot(),
                'acceptance': self.last_acceptance, 'criteria': self.acceptance.describe(),
                'operations': self.operations.describe(),
                'limits': ['Shell effects have no universal rollback.',
                           'Configured criteria do not prove arbitrary natural-language intent.']}

    def context(self):
        # This compact state survives chat compaction without copying tool
        # transcripts, commands or expected fixture values into every prompt.
        # Cached receipts are explicitly observations, never fresh certification.
        def clip(value, limit):
            value = str(value)
            return value if len(value) <= limit else value[:limit // 2] + '…' + value[-limit // 2:]
        actions, transactions = self.journal.snapshot(), self.transactions.snapshot()
        pending = [(source, row) for source, state in (('actions', actions), ('transactions', transactions))
                   for row in state['operations'] if row['id'] in state['unresolved_mutations']]
        contract = self.acceptance.describe()
        report = self.last_acceptance or self.previous_acceptance or {}
        current = self.last_acceptance is not None
        payload = {
            'scope': clip(self.workspace, 300), 'revision': self.goal_revision,
            'constraints': {'allow_external': self.allow_external, 'read_only': self.read_only,
                            'required_proofs': 'file_content_and_configured_postconditions'},
            'user_corrections': [clip(item, 450) for item in self.corrections[-3:]],
            'previous_conclusions_valid': False,
            'contract': {'configured': contract['configured'], 'criteria_count': len(contract['criteria']),
                'criteria': [{key: clip(row[key], 64) for key in ('id', 'type')} for row in contract['criteria'][:6]],
                'natural_language_goal_verified': False, 'satisfaction_scope': 'configured_contract_only'},
            'recovery': {'unknown_count': len(pending), 'operations': [
                {'journal': source, 'id': row['id'], 'tool': clip(row['tool'], 100), 'status': row['status']}
                for source, row in pending[:4]], 'automatic_replay': False},
            'evidence': {'freshness': 'not_rechecked_since_evaluation' if current else 'previous_process_unverified' if report else 'not_evaluated',
                'evaluated_at': report.get('evaluated_at'),
                'criteria_status': report.get('criteria_status') if current else 'unverified',
                'checks': [{'check': clip(row.get('check', ''), 64), 'status': row.get('status') if current else 'unverified'}
                           for row in report.get('checks', [])[-3:]],
                'fingerprints': [{'path': clip(path, 160), 'sha256': fingerprint}
                                 for path, fingerprint in list(report.get('fingerprints', {}).items())[:2]]}}
        render = lambda: json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        # Leave a hard bound even with Unicode-heavy corrections or long IDs;
        # callers retain complete state through workflow_status when needed.
        for collection in (payload['evidence']['fingerprints'], payload['contract']['criteria'], payload['user_corrections']):
            while len(render()) > 4800 and len(collection) > (1 if collection is payload['user_corrections'] else 0):
                collection.pop(0)
                payload['truncated'] = True
        return render()
