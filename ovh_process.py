"""Bounded command execution and an explicit, fail-closed workspace sandbox.

``approved`` preserves the caller's approval policy; it is not an OS sandbox.
``workspace`` requires bubblewrap namespaces and never falls back to local
execution. Process groups cover ordinary child processes, not hostile daemons
that deliberately detach in the approved profile. No credentials are logged.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import signal
import subprocess
import tempfile
import time

MAX_OUTPUT_BYTES = 16 * 1024 * 1024
READ_BYTES = 65536
RUNTIME_DIRECTORIES = ('/usr', '/bin', '/sbin', '/lib', '/lib64', '/opt/ovh-agent')
_SENSITIVE_NAME = re.compile(r'(?:TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|API_KEY|ACCESS_KEY|PRIVATE_KEY|AUTHORIZATION|COOKIE|(?:^|_)PAT(?:_|$))')
_PROVIDER_PREFIXES = ('OVH_', 'OVHCLOUD_', 'OPENAI_', 'ANTHROPIC_', 'GEMINI_', 'DEEPSEEK_',
                      'MISTRAL_', 'TOGETHER_', 'GITHUB_', 'GH_', 'VSCE_', 'GIT_CONFIG_')
_UNSAFE_ENV = {'SSH_AUTH_SOCK', 'SSH_AGENT_PID', 'GIT_ASKPASS', 'SSH_ASKPASS', 'GIT_SSH_COMMAND',
               'BASH_ENV', 'ENV', 'LD_PRELOAD', 'LD_LIBRARY_PATH', 'PYTHONSTARTUP', 'PYTHONPATH', 'PYTHONHOME',
               # Commands are new processes without code-server's parent IPC.
               'CODE_SERVER_PARENT_PID'}


def command_is_destructive(command, _depth=0):
    """Recognize obvious destructive commands, not arbitrary shell semantics.

    This is a typo guard, not a security boundary: expansion, aliases, scripts
    and indirect execution still require the executor's approval/sandbox policy.
    Quoted prose containing command names must not be treated as execution.
    """
    if _depth > 8:
        return True
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|()\n')
        lexer.whitespace = ' \t\r'
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return True
    words = []
    for token in tokens + [';']:
        if token and all(c in ';&|()\n' for c in token):
            if words:
                # Unwrap common literal launchers. This deliberately makes no
                # claim to resolve arbitrary sudo/env option arguments.
                while words and (words[0] in {'sudo', 'command', 'exec', 'nohup', 'then', 'do', 'env'}
                        or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*', words[0])):
                    words.pop(0)
                if words:
                    binary, args = Path(words[0]).name, words[1:]
                    if binary in {'shutdown', 'reboot', 'halt', 'poweroff', 'mkfs'} or binary.startswith('mkfs.'):
                        return True
                    if binary == 'dd' and any(arg.startswith('if=') for arg in args):
                        return True
                    if binary == 'rm':
                        options = args[:args.index('--')] if '--' in args else args
                        recursive = any(arg == '--recursive' or arg.startswith('-') and not arg.startswith('--')
                                        and ('r' in arg or 'R' in arg) for arg in options)
                        if recursive and any(arg.startswith('/') and os.path.normpath('/' + arg.lstrip('/')) == '/' for arg in args):
                            return True
                    if binary in {'sh', 'bash', 'dash', 'ash', 'zsh'}:
                        for index, arg in enumerate(args[:-1]):
                            if arg.startswith('-') and not arg.startswith('--') and 'c' in arg:
                                if command_is_destructive(args[index+1], _depth+1):
                                    return True
                    if binary == ':' and token == '()':
                        return True
            words = []
        else:
            words.append(token)
    return False


def sanitized_environment(environment=None):
    """Remove inherited provider/git credentials and automatic startup injection.

    This is an inheritance boundary, not a secret scanner. An approved command
    still has its OS user's file access and may explicitly load project config.
    """
    source = os.environ if environment is None else environment
    return {key: value for key, value in source.items()
            if not _SENSITIVE_NAME.search(key.upper())
            and not key.upper().startswith(_PROVIDER_PREFIXES)
            and key.upper() not in _UNSAFE_ENV}


def sandbox_argv(argv, cwd, workspace, binary):
    """Build only fixed read-only runtime mounts plus the authorized workspace."""
    if workspace is None:
        raise ValueError('workspace_required')
    root = Path(workspace).resolve(strict=True)
    location = Path(cwd).resolve(strict=True)
    if not root.is_dir() or not location.is_relative_to(root):
        raise ValueError('cwd_outside_workspace')
    runtimes = [Path(path) for path in RUNTIME_DIRECTORIES if Path(path).exists()]
    if root in {Path('/'), Path('/home'), Path('/root')} or any(
            root == path.resolve() or root.is_relative_to(path.resolve()) or path.resolve().is_relative_to(root)
            for path in runtimes):
        raise ValueError('workspace_overlaps_runtime')
    # --disable-userns requires an explicit --unshare-user even on bubblewrap
    # versions where --unshare-all otherwise implies a user namespace.
    command = [binary, '--die-with-parent', '--new-session', '--unshare-all', '--unshare-user',
               '--disable-userns', '--cap-drop', 'ALL']
    for path in runtimes:
        command += ['--ro-bind', str(path), str(path)]
    # No host /etc, home, /run, SSH agent, Docker socket or host network mount.
    command += ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--tmpfs', '/run',
                '--dir', '/tmp/ovh-home']
    for parent in reversed(root.parents):
        if str(parent) not in {'/', '/tmp', '/run'}:
            command += ['--dir', str(parent)]
    command += ['--bind', str(root), str(root), '--chdir', str(location), '--', *argv]
    return command


def _kill_group(process):
    # Kill the entire group even when its original shell has already exited:
    # otherwise background workers can keep changing files after a check passed.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_process(argv, cwd, timeout=120, *, workspace=None, profile='approved', output_limit=16000):
    """Return the real exit outcome with bounded combined stdout/stderr.

    Output is kept in memory as a bounded tail. A 16 MiB stream flood terminates
    the group; stdout truncation alone does not change a command's exit status.
    Timeout/flood/startup failure cannot be reported as a passed command.
    """
    started = time.monotonic()
    result = {'exit_code': None, 'passed': False, 'timed_out': False, 'output': '',
              'output_truncated': False, 'duration_ms': 0, 'profile': profile, 'executed': False}
    def finish():
        result['duration_ms'] = round((time.monotonic() - started) * 1000)
        return result
    if (not isinstance(argv, (list, tuple)) or not argv or any(not isinstance(item, str) or '\x00' in item for item in argv)
            or profile not in {'approved', 'workspace'} or isinstance(timeout, bool)
            or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 86400
            or isinstance(output_limit, bool) or not isinstance(output_limit, int) or not 1 <= output_limit <= 1024 * 1024):
        result['error'] = 'invalid_process_arguments'
        return finish()
    environment = sanitized_environment()
    environment['PYDANTIC_AI_NO_BANNER'] = '1'
    # Project-installed test tools must remain available without a login shell.
    environment['PATH'] = os.pathsep.join((str(Path(cwd) / '.agent-tools/venv/bin'),
        str(Path(cwd) / '.agent-tools/npm/node_modules/.bin'), environment.get('PATH', '')))

    command = list(argv)
    information = None
    try:
        if profile == 'workspace':
            binary = shutil.which('bwrap')
            if not binary:
                result['error'] = 'sandbox_unavailable'
                return finish()
            command = sandbox_argv(command, cwd, workspace, binary)
            environment.update(HOME='/tmp/ovh-home', TMPDIR='/tmp', XDG_CACHE_HOME='/tmp/ovh-home/.cache',
                               XDG_CONFIG_HOME='/tmp/ovh-home/.config', XDG_RUNTIME_DIR='/run')
            information = tempfile.TemporaryFile()
            command[1:1] = ['--info-fd', str(information.fileno())]
        process = subprocess.Popen(command, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True,
                                   pass_fds=(information.fileno(),) if information else ())
    except (OSError, ValueError, TypeError) as error:
        if information:
            information.close()
        result['error'] = 'sandbox_setup_failed' if profile == 'workspace' else 'process_start_failed'
        # Error class is sufficient for diagnosis; argv/environment may be secret.
        result['error_type'] = type(error).__name__
        return finish()
    result['executed'] = profile == 'approved'
    tail = bytearray()
    received = 0
    flood = False
    eof = False
    deadline = time.monotonic() + timeout
    exited_at = None
    try:
        os.set_blocking(process.stdout.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                now = time.monotonic()
                exited = process.poll() is not None
                if exited and exited_at is None:
                    exited_at = now
                    _kill_group(process)
                if not exited and now >= deadline:
                    result['timed_out'] = True
                    _kill_group(process)
                if exited and (eof or now - exited_at >= .25):
                    if not eof:
                        result['output_truncated'] = True
                        result['error'] = 'descendant_output_open'
                    break
                for key, _ in selector.select(.02):
                    chunk = os.read(key.fileobj.fileno(), min(READ_BYTES, max(1, MAX_OUTPUT_BYTES - received + 1)))
                    if not chunk:
                        eof = True
                        selector.unregister(key.fileobj)
                        continue
                    received += len(chunk)
                    tail.extend(chunk)
                    if len(tail) > output_limit:
                        del tail[:-output_limit]
                        result['output_truncated'] = True
                    if received > MAX_OUTPUT_BYTES:
                        flood = True
                        result['output_truncated'] = True
                        result['error'] = 'output_limit_exceeded'
                        _kill_group(process)
                        break
                if flood:
                    break
                # A group stuck in uninterruptible kernel I/O must not keep this
                # controller waiting forever after its timeout.
                if result['timed_out'] and time.monotonic() > deadline + 2:
                    result['error'] = 'process_termination_unconfirmed'
                    break
    finally:
        _kill_group(process)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            result['error'] = 'process_termination_unconfirmed'
        process.stdout.close()
        if information:
            information.seek(0)
            try:
                state = json.loads(information.read(4096))
                result['executed'] = isinstance(state, dict) and isinstance(state.get('child-pid'), int)
            except (ValueError, TypeError):
                result['executed'] = False
            information.close()
    result['exit_code'] = process.returncode
    result['output'] = tail.decode('utf-8', 'replace')
    result['output_bytes'] = received
    if profile == 'workspace' and not result['executed']:
        result['error'] = 'sandbox_setup_failed'
    result['passed'] = result['executed'] and process.returncode == 0 and not result['timed_out'] and not flood and not result.get('error')
    return finish()
