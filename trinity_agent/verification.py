"""Local edit transactions and executable checks, independent of model assertions.

This is a correctness guard for file tools, not an OS sandbox for shell/SSH.
Original dirty working files are preserved; rollback never resets a repository.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import ast
from datetime import datetime, timezone
import fnmatch
import glob
import hashlib
import json
import os
import re
from pathlib import Path
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import uuid

from trinity_agent.memory import atomic_write


class VerificationError(RuntimeError):
    pass


SYNTAX_UNAVAILABLE = "non disponible pour ce format"


def digest(content: bytes | None) -> str | None:
    return hashlib.sha256(content).hexdigest() if content is not None else None


def same_check_definition(path: Path, before: str | None, after: str) -> bool:
    """Recognize metadata/comment-only changes without trusting a rewritten test."""
    if before is None:
        return False
    try:
        if path.name == 'package.json':
            old, new = json.loads(before), json.loads(after)
            return isinstance(old, dict) and isinstance(new, dict) and old.get('scripts') == new.get('scripts')
        if path.suffix == '.py':
            return ast.dump(ast.parse(before)) == ast.dump(ast.parse(after))
        if path.suffix == '.json':
            return json.loads(before) == json.loads(after)
        if path.suffix in {'.js', '.cjs', '.mjs'}:
            # Recognize appended whole-line comments without attempting to strip
            # JavaScript comments from strings, templates or regular expressions.
            for original, amended in ((before, after), (after, before)):
                if amended.startswith(original):
                    suffix = amended[len(original):]
                    if suffix.startswith(('\n', '\r')) and all(
                            not line.strip() or line.lstrip().startswith('//') for line in suffix.splitlines()):
                        return True
    except (SyntaxError, ValueError, TypeError):
        pass
    return before == after


def contents(path: Path) -> bytes | None:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise VerificationError(f"Fichier régulier requis : {path}")
        return path.read_bytes()
    except FileNotFoundError:
        return None


def run_check(argv: list[str], cwd: Path, timeout: int = 120) -> dict:
    """Use the shared bounded executor; configured checks retain argv semantics."""
    from trinity_agent.process import run_process
    return run_process(argv, cwd, timeout)


def test_count(result: dict) -> dict:
    """Recognize complete runner summaries; PASS banners are never test counts.

    Counts are reported executions, not unique tests or a guarantee of coverage.
    Ambiguous, truncated and interrupted output deliberately remains uncounted.
    """
    unknown = {"reported_tests": None, "count_source": None}
    if result.get("timed_out") or result.get("output_truncated") or result.get("exit_code") is None:
        return unknown
    text = re.sub(r"\x1b\[[0-9;]*m", "", str(result.get("output", "")))
    unit = re.findall(r"^Ran (\d+) tests? in [^\n]+\n\s*\n(?:OK\b|FAILED\b)", text, re.M)
    node = re.findall(r"^(?:#|ℹ) tests (\d+)\s*$", text, re.M)
    pytest = re.findall(r"^(?:=+\s*)?((?:\d+ (?:passed|failed|error|errors|skipped|xfailed|xpassed|deselected)(?:, )?)+) in [\d.]+s(?:\s*\([^\n]*\))?(?:\s*=+)?$", text, re.M)
    candidates = [(int(n), "unittest") for n in unit] + [(int(n), "node:test") for n in node]
    for summary in pytest:
        count = sum(int(n) for n, status in re.findall(r"(\d+) (\w+)", summary) if status != "deselected")
        candidates.append((count, "pytest"))
    if len(candidates) != 1:
        return unknown
    count, source = candidates[0]
    return {"reported_tests": count, "count_source": source}


def check_syntax(path: Path, content: str) -> str:
    """Reject invalid supported syntax before touching the original inode."""
    try:
        if path.suffix == ".py":
            compile(content, str(path), "exec", dont_inherit=True)
            return "Python"
        if path.suffix == ".json":
            def invalid_constant(value):
                raise ValueError(f"Constante JSON invalide : {value}")
            json.loads(content, parse_constant=invalid_constant)
            return "JSON"
        if path.suffix == ".toml":
            tomllib.loads(content)
            return "TOML"
        if path.suffix in {".js", ".cjs", ".mjs"}:
            executable = shutil.which("node")
            if not executable:
                raise VerificationError("Node.js absent : syntaxe JavaScript non vérifiable.")
            modes = ["module"] if path.suffix == ".mjs" else ["commonjs"] if path.suffix == ".cjs" else ["commonjs", "module"]
            for mode in modes:
                result = subprocess.run([executable, "--check", f"--input-type={mode}"],
                                        input=content, text=True, capture_output=True, timeout=15)
                if result.returncode == 0:
                    return "JavaScript"
            raise VerificationError(result.stderr[-2000:])
        if path.suffix == ".sh":
            first = content.split("\n", 1)[0]
            shell = "bash" if re.match(r"#!\s*(?:/\S+/)?(?:env\s+(?:-S\s+)?)?bash\b", first) else "sh"
            result = subprocess.run([shell, "-n"], input=content, text=True,
                                    capture_output=True, timeout=15)
            if result.returncode:
                raise VerificationError(result.stderr[-2000:])
            return shell
    except (SyntaxError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        raise VerificationError(f"Syntaxe refusée avant écriture ({path.name}) : {error}") from error
    return SYNTAX_UNAVAILABLE


def syntax_claims(output: str) -> list[str]:
    """Find explicit success assertions; receipts, not prose, decide their truth.

    Keep inline filenames for scope matching, but ignore quoted examples. This
    intentionally does not pretend to classify arbitrary natural language.
    """
    prose = re.sub(r"```[\s\S]*?```|(?m:^>.*$)", "", output)
    prose = re.sub(r"`([^`]*)`", lambda match: match[0] if re.fullmatch(
        r"[^`\n]+\.[a-zA-Z][\w+-]{0,10}", match[1]) else "", prose)
    prose = prose.replace("*", "")
    prose = re.sub(r":\s*\n\s*", ": ", prose)
    clauses = re.split(r"(?<=[.!?;])\s+|\n|\b(?:mais|cependant|pourtant|but|however)\b", prose, flags=re.I)
    # Recognize completed actions and compact outcome labels, not arbitrary
    # co-occurrence ("je n’ai pas vérifié la syntaxe", future plans or lessons).
    target = r"(?:CSS|Python|JavaScript|JSON|TOML|shell|`[^`\n]+\.[a-zA-Z][\w+-]{0,10}`|[\w./-]+\.[a-zA-Z][\w+-]{0,10})"
    scope = r"(?:\s+(?:(?:de|du|des|pour|of|for)\s+(?:(?:fichier|file)\s+)?)?" + target + r"){0,2}"
    outcome = r"(?:pass[ée](?:e?s?)?|passed|r[ée]ussi(?:e?s?)?|valid(?:ée?s?|ees?)|v[ée]rifi[ée](?:e?s?)?|checked|verified|successful(?:ly)?|OK)\b"
    label = re.compile(r"\bsyntaxe?\b" + scope
                       + r"\s*(?:(?:(?:a|ont)\s+[ée]t[ée]|est|sont|check(?:s)?)\s+)?"
                       + r"[:|–—-]?\s*(?:✅\s*)?(?:" + outcome + r"|✅)", re.I)
    performed = re.compile(r"\b(?:j[’']ai|nous avons|I have|we have|I|we)\s+"
                           + r"(?:v[ée]rifi[ée]|contr[oô]l[ée]|validated|verified|checked)\s+"
                           + r"(?:(?:la|the)\s+)?syntaxe?\b", re.I)
    claims = []
    for clause in clauses:
        action = performed.search(clause)
        status = label.search(clause)
        if status and re.search(r"\b(?:aucun(?:e)?|no|sans|without)\s+(?:(?:contr[oô]les?|checks?|validation)(?:\s+de|\s+of)?\s+)?$", clause[:status.start()], re.I):
            status = None
        if action or status:
            claims.append(clause)
    return claims


def replace_checked(path: Path, data: bytes | None, expected: str | None, mode: int = 0o644) -> None:
    """Compare with the exact last read version, then replace atomically."""
    if digest(contents(path)) != expected:
        raise VerificationError(f"Modification extérieure détectée, fichier préservé : {path}")
    if data is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.verified-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if digest(contents(path)) != expected:
            raise VerificationError(f"Fichier modifié pendant la préparation : {path}")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@dataclass
class Check:
    name: str
    root: Path
    argv: list[str]
    patterns: list[str]
    protected: dict[str, str | None]
    timeout: int = 120
    kind: str = "functional"
    project: Path | None = None


class ChangeGuard:
    def __init__(self, workspace: Path, directory: Path, *, runner=run_check, publish=None, require_checks=False, allow_external=False):
        self.workspace = workspace.resolve()
        self.allow_external = allow_external
        self.directory = directory
        self.runner = runner
        self.publish = publish or (lambda _event: None)
        self.lock = threading.RLock()
        self.checks: dict[str, Check] = {}
        self.projects: set[Path] = set()
        self.explicit_policies: set[Path] = set()
        self.observed: set[Path] = set()
        self.inspected_directories: set[Path] = set()
        self.require_checks = require_checks
        self.task_intent = "question"
        self.failures: dict[str, int] = {}
        self.state = {"version": 1, "workspace": str(self.workspace), "status": "pending",
                      "changes": {}, "baseline": {}, "receipts": [], "report": None}

    def _save(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state['verification_context'] = {
            'checks': {key: {**asdict(check), 'root': str(check.root),
                            'project': str(check.project) if check.project else None}
                       for key, check in self.checks.items()},
            **{name: sorted(map(str, getattr(self, name))) for name in
               ('projects', 'explicit_policies', 'observed', 'inspected_directories')},
            'require_checks': self.require_checks, 'task_intent': self.task_intent}
        atomic_write(self.directory / "journal.json", json.dumps(self.state, ensure_ascii=False, indent=2))

    def _path(self, path: Path) -> Path:
        path = path.resolve()
        if (not self.allow_external and not path.is_relative_to(self.workspace)) or path.is_relative_to(self.directory.resolve()):
            raise VerificationError("Chemin hors périmètre des modifications vérifiables.")
        return path

    def observe(self, path: Path):
        """Freeze configured commands before the model changes a project."""
        with self.lock:
            path = self._path(path)
            if path.is_file():
                self.observed.add(path)
            elif path.is_dir():
                self.inspected_directories.add(path)
            folder = path if path.is_dir() else path.parent
            # External projects retain their own checks; never widen the default workspace.
            while self.allow_external or folder.is_relative_to(self.workspace):
                if any((folder / name).exists() for name in (".ovh-agent-checks.json", "package.json", ".git", "pyproject.toml")):
                    if folder in self.projects:
                        return
                    policy = folder / ".ovh-agent-checks.json"
                    definitions = []
                    if policy.is_file():
                        self.explicit_policies.add(folder)
                        try:
                            document = json.loads(policy.read_text())
                            if not isinstance(document, dict) or document.get("version") != 1 or not isinstance(document.get("checks"), list):
                                raise ValueError("Format de contrôles invalide")
                            definitions = document["checks"]
                        except (ValueError, OSError) as error:
                            raise VerificationError(f"Configuration des contrôles invalide : {error}") from error
                    elif (folder / "package.json").is_file():
                        try:
                            package = json.loads((folder / "package.json").read_text())
                            command = package.get("scripts", {}).get("test", "")
                            if not isinstance(command, str):
                                raise ValueError("script test invalide")
                        except (AttributeError, ValueError, OSError) as error:
                            raise VerificationError(f"package.json invalide : {error}") from error
                        if command and "no test specified" not in command.lower():
                            manager = "bun" if any((folder / name).exists() for name in ("bun.lock", "bun.lockb")) else "npm"
                            definitions = [{"name": "Tests du projet", "argv": [manager, "run", "test"],
                                            "protect": ["package.json", "test/**/*.test.*", "tests/**"]}]
                    discovered = {}
                    for index, definition in enumerate(definitions):
                        if not isinstance(definition, dict):
                            raise VerificationError("Définition de contrôle invalide.")
                        argv = definition.get("argv")
                        if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) and arg for arg in argv):
                            raise VerificationError("Chaque contrôle doit définir une liste argv non vide.")
                        argv = [sys.executable if arg == "{python}" else arg for arg in argv]
                        relative_cwd = definition.get("cwd", ".")
                        if not isinstance(relative_cwd, str):
                            raise VerificationError("cwd doit être un chemin relatif.")
                        cwd = (folder / relative_cwd).resolve()
                        if not cwd.is_relative_to(folder):
                            raise VerificationError("Le contrôle doit rester dans son projet.")
                        patterns = definition.get("paths", ["*"])
                        protections = definition.get("protect", [])
                        if (not isinstance(patterns, list) or not patterns or not isinstance(protections, list)
                                or not all(isinstance(item, str) and item for item in patterns + protections)):
                            raise VerificationError("paths et protect doivent être des listes de chemins.")
                        if any(Path(item).is_absolute() or ".." in Path(item).parts for item in patterns + protections):
                            raise VerificationError("Les chemins des contrôles doivent rester dans leur projet.")
                        timeout = definition.get("timeout", 120)
                        if type(timeout) is not int or not 1 <= timeout <= 300:
                            raise VerificationError("timeout doit être un entier entre 1 et 300 secondes.")
                        kind = definition.get("kind", "functional")
                        if not isinstance(kind, str) or kind not in {"functional", "syntax"}:
                            raise VerificationError("kind doit être functional ou syntax.")
                        protected = {}
                        for pattern in [".ovh-agent-checks.json", *protections]:
                            if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
                                raise VerificationError("Chemin de contrôle protégé invalide.")
                            for item in folder.glob(pattern):
                                if item.is_file():
                                    if item.is_symlink() or not item.resolve().is_relative_to(folder):
                                        raise VerificationError("Contrôle hors projet ou symbolique.")
                                    protected[str(item)] = digest(item.read_bytes())
                        key = f"{folder}:{index}"
                        discovered[key] = Check(str(definition.get("name", key)), cwd, argv,
                                                 [str(folder / pattern) for pattern in patterns],
                                                 protected, timeout, kind, folder)
                    self.checks.update(discovered)
                    self.projects.add(folder)
                    return
                if folder == self.workspace or folder == folder.parent:
                    return
                folder = folder.parent

    def plan(self) -> dict:
        with self.lock:
            return {"projects": sorted(str(path) for path in self.projects),
                    "checks": [{"name": check.name, "argv": check.argv, "cwd": str(check.root), "kind": check.kind,
                                "paths": check.patterns, "protected": sorted(check.protected)}
                               for check in self.checks.values()],
                    "baseline_failures": self.baseline_failures(),
                    "instruction": "Utilise les contrôles existants et préserve leurs références. Une politique explicite reste obligatoire. Sans suite configurée, les écritures conservent une sauvegarde et sont vérifiées par syntaxe disponible et contenu ; cela ne prouve pas leur comportement."}

    def baseline_failures(self, keys=None) -> list[dict]:
        """Expose initial failures to the model at the first edit, not only the UI."""
        return [{key: receipt.get(key) for key in
                 ('check', 'argv', 'cwd', 'exit_code', 'termination_signal', 'error', 'execution_context')}
                | {'output': receipt.get('output', '')[-1500:]}
                for key, receipt in self.state['baseline'].items()
                if (keys is None or key in keys) and not receipt.get('passed')]

    def _requires_functional_coverage(self, path):
        if any(path.is_relative_to(root) for root in self.explicit_policies):
            return True
        # A nested package marker must not hide an enclosing explicit policy and
        # turn an uncovered file into an unrestricted bootstrap.
        folder = path.parent
        while self.allow_external or folder.is_relative_to(self.workspace):
            if (folder / ".ovh-agent-checks.json").exists():
                return True
            if folder == self.workspace or folder == folder.parent:
                break
            folder = folder.parent
        return False

    def _applicable(self, paths):
        return {key: check for key, check in self.checks.items()
                if any(fnmatch.fnmatch(str(path), pattern) for path in paths for pattern in check.patterns)}

    def _evidence_files(self, check: Check) -> dict:
        paths = set(self.observed) | {Path(p) for p in self.state["changes"]} | {Path(p) for p in check.protected}
        # Freeze all declared subjects, including files the model did not read.
        # Re-enumeration detects additions/removals as well as content changes.
        for pattern in check.patterns:
            if any(marker in pattern for marker in ('*', '?', '[')):
                for name in glob.iglob(pattern, recursive=True):
                    path = Path(name)
                    if not path.is_file():
                        continue
                    if not path.resolve().is_relative_to(check.project or check.root):
                        raise VerificationError('Entrée de contrôle hors projet : ' + name)
                    paths.add(path)
            else:
                path = (check.root / pattern).resolve()
                if self.allow_external or path.is_relative_to(self.workspace):
                    paths.add(path)
        for argument in check.argv:
            path = check.root / argument
            if path.is_file() and (self.allow_external or path.resolve().is_relative_to(self.workspace)):
                paths.add(path.resolve())
        return {str(p): digest(contents(p)) for p in paths}

    def _fresh(self, receipt: dict) -> bool:
        if receipt.get("environment_changed"):
            return False
        check = self.checks.get(receipt.get("key"))
        if check is None:
            return False  # A receipt without its frozen check cannot be refreshed.
        # Newly observed inputs expand the evidence scope and invalidate old receipts.
        if check and self._evidence_files(check) != receipt.get("fingerprints"):
            return False
        return ("fingerprints" in receipt and not receipt.get("changed_during_check")
                and receipt.get("change_versions") == {p: c["written"] for p, c in self.state["changes"].items()}
                and all(digest(contents(Path(p))) == value for p, value in receipt["fingerprints"].items()))

    def invalidate_environment(self) -> None:
        """A package installation can change test behavior without changing source files."""
        with self.lock:
            for receipt in self.state["receipts"]:
                receipt["environment_changed"] = True
            self.state["report"] = None
            self._save()

    def execute_configured(self, command: str, cwd: Path) -> dict | None:
        """Run an exact configured argv directly, never interpret shell substitutions.

        The normal check runner owns approval and execution; no double approval.
        Unrecognized shell commands remain ordinary commands, not test evidence.
        """
        with self.lock:
            try:
                argv = shlex.split(command)
            except ValueError:
                return None
            for key, check in self.checks.items():
                if argv == check.argv and cwd.resolve() == check.root.resolve():
                    return self._execute(key, check, "manual")
            return None

    def ensure_checks(self, names) -> None:
        """Execute contract-required checks even for an already compliant task.

        Reuse fresh successful receipts; checking a no-op must not require making
        a pointless edit, or rerunning every unrelated project test.
        """
        with self.lock:
            names = set(names)
            if not names:
                return
            self.observe(self.workspace)
            selected = {key: check for key, check in self.checks.items() if check.name in names}
            missing = names - {check.name for check in selected.values()}
            if missing:
                raise VerificationError('Contrôles attendus absents : ' + ', '.join(sorted(missing)))
            self.require_checks = True
            for key, check in selected.items():
                previous = next((r for r in reversed(self.state['receipts'])
                                 if r['key'] == key and r['phase'] in {'manual', 'after'}), None)
                if previous and previous.get('passed') and self._fresh(previous):
                    continue
                self._execute(key, check, 'manual')

    def test_summary(self) -> dict:
        with self.lock:
            latest = {}
            for receipt in self.state["receipts"]:
                if receipt["phase"] in {"after", "manual"}:
                    latest[receipt["key"]] = receipt
            checks = []
            for receipt in latest.values():
                fresh = self._fresh(receipt)
                status = ("stale" if not fresh else "interrupted" if receipt.get("timed_out")
                          else "unavailable" if receipt.get("exit_code") is None
                          else "passed" if receipt.get("passed") else "failed")
                checks.append({k: receipt.get(k) for k in ("id", "check", "argv", "cwd", "exit_code", "duration_ms", "count_source")}
                              | {"status": status, "reported_tests": receipt.get("reported_tests") if fresh else None})
            return {"checks": checks, "checks_count": len(checks),
                    "passed_checks": sum(c["status"] == "passed" for c in checks),
                    "reported_test_executions": sum(c["reported_tests"] or 0 for c in checks),
                    "uncounted_checks": sum(c["reported_tests"] is None for c in checks),
                    "scope": "executed_configured_checks", "unique_tests_known": False,
                    "universal_correctness": False}

    def summary_markdown(self, summary: dict) -> str:
        def cell(value):
            return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ").replace("\r", " ").replace("<", "&lt;").replace(">", "&gt;")
        labels = {"passed": "Réussi", "failed": "Échec", "stale": "Périmé", "interrupted": "Interrompu", "unavailable": "Indisponible"}
        lines = ["### Bilan des contrôles calculé par le backend", "",
                 "| Contrôle | Commande | Sortie | Durée | Tests décomptés | Résultat |",
                 "| --- | --- | --- | --- | --- | --- |"]
        for check in summary["checks"]:
            values = [check["check"], shlex.join(check["argv"]), check["exit_code"] if check["exit_code"] is not None else "indisponible",
                      f"{check['duration_ms']} ms", check["reported_tests"] if check["reported_tests"] is not None else "indisponible", labels[check["status"]]]
            lines.append("| " + " | ".join(cell(v) for v in values) + " |")
        lines += ["", f"{summary['passed_checks']} contrôles réussis sur {summary['checks_count']}. "
                  f"{summary['reported_test_executions']} exécutions de tests décomptées ; "
                  f"{summary['uncounted_checks']} contrôles sans décompte exploitable.",
                  "Les suites peuvent se recouper : ce total ne représente pas nécessairement des tests uniques. "
                  "Ces résultats concernent les commandes et fichiers suivis lors de leur exécution ; "
                  "ils ne démontrent pas l’absence de défauts dans le reste du projet."]
        return "\n".join(lines)

    def syntax_summary(self) -> list[dict]:
        """Return syntax evidence for the current bytes, never a pre-write guess."""
        with self.lock:
            subjects = set(self.state["changes"]) or {str(path) for path in self.observed}
            final = {item["path"]: item for item in (self.state.get("report") or {}).get("syntax_checks", [])}
            summary = []
            for name in sorted(subjects):
                current = digest(contents(Path(name)))
                item = final.get(name, {})
                status, validator = "not_checked", None
                if name in self.state["changes"] and current != self.state["changes"][name]["written"]:
                    summary.append({"path": name, "status": "stale", "validator": None, "fingerprint": current})
                    continue
                if (item.get("phase") == "final" and item.get("fingerprint") == current
                        and current is not None and not item.get("receipt_id")
                        and item.get("status") in {"passed", "unavailable"}):
                    status, validator = item["status"], item.get("validator")
                for receipt in reversed(self.state["receipts"]):
                    check = self.checks.get(receipt.get("key"))
                    if (check and check.kind == "syntax" and receipt.get("phase") in {"after", "manual"}
                            and receipt.get("passed") and self._fresh(receipt)
                            and current is not None and receipt.get("fingerprints", {}).get(name) == current
                            and any(fnmatch.fnmatch(name, pattern) for pattern in check.patterns)):
                        status, validator = "passed", check.name
                        break
                summary.append({"path": name, "status": status, "validator": validator,
                                "fingerprint": current})
            return summary

    def _validate_syntax_claims(self, output: str) -> str:
        evidence = self.syntax_summary()
        for claim in syntax_claims(output):
            references = set(re.findall(r"`([^`]+)`", claim))
            unquoted = re.sub(r"`[^`]+`", "", claim)
            references.update(re.findall(r"(?<![\w.-])(?:/?[\w.-]+/)*[\w.-]+\.[a-zA-Z][\w+-]{0,10}\b", unquoted))
            mentioned = []
            for reference in references:
                matching = [item for item in evidence if
                            (str((self.workspace / reference).resolve()) == item["path"] if '/' in reference
                             else Path(item["path"]).name == reference)]
                if not matching:
                    raise VerificationError("Contrôle de syntaxe annoncé sans preuve pour le fichier cité : " + reference)
                mentioned.extend(matching)
            # A language-specific assertion may cover only that language in a
            # mixed edit; a generic assertion must cover every changed file.
            languages = {"python": {".py"}, "javascript": {".js", ".cjs", ".mjs"},
                         "json": {".json"}, "toml": {".toml"}, "shell": {".sh"}, "css": {".css"}}
            formats = {suffix for language, suffixes in languages.items()
                       if re.search(r"\b" + language + r"\b", claim, re.I) for suffix in suffixes}
            subjects = mentioned or ([item for item in evidence if Path(item["path"]).suffix in formats] if formats else evidence)
            if not subjects or any(item["status"] != "passed" for item in subjects):
                raise VerificationError("Contrôle de syntaxe annoncé sans preuve sur le contenu final. "
                                        "Indique les formats non vérifiés ; un contenu sauvegardé ne prouve pas sa syntaxe. "
                                        + json.dumps(evidence, ensure_ascii=False))
        if not self.state["changes"]:
            return output
        passed = [Path(item["path"]).name for item in evidence if item["status"] == "passed"]
        missing = [Path(item["path"]).name for item in evidence if item["status"] != "passed"]
        lines = []
        if passed:
            lines.append("Syntaxe vérifiée sur le contenu final : " + ", ".join(f"`{name}`" for name in passed) + ".")
        if missing:
            lines.append("Syntaxe non vérifiée pour ces fichiers : " + ", ".join(f"`{name}`" for name in missing) + ".")
        notice = "\n".join(lines)
        return output if not notice or notice in output else output.rstrip() + "\n\n" + notice

    def validate_summary(self, output: str) -> str:
        output = self._validate_syntax_claims(output)
        summary = self.test_summary()
        # Missing receipts mean "not executed", never "all passed". Check before
        # the old early return, including the exact false claim seen in production.
        prose = re.sub(r"```[\s\S]*?```|`[^`]*`|(?m:^>.*$)", "", output)
        if not summary["checks"]:
            claim = re.search(
                r"aucun test[^.\n]{0,60}(?:échoué|echoue)|"
                r"\b(?:tests?|contrôles|checks)\s+(?:automatisés?\s+)?(?:réussis?|validés?|passés?|passed)|"
                r"(?:j[’']ai|nous avons)\s+(?:exécuté|lancé|passé)[^.\n]{0,50}\btests?\b|"
                r"\ball tests pass(?:ed)?\b|\bno tests failed\b", prose, re.I)
            if claim:
                raise VerificationError("Aucune preuve de test exécuté dans cette tâche. Retire l’annonce de tests réussis ; exécute les contrôles ou indique explicitement qu’ils n’ont pas été exécutés.")
        modification_claim = (
                r"\b(?:modification|changement)\s+(?:a [ée]t[ée] |est |ont [ée]t[ée] )?(?:effectu[ée]|r[ée]alis[ée]|appliqu[ée])|"
                r"\b(?:modification|changement)\s+(?:effectu[ée]|r[ée]alis[ée]|appliqu[ée])|"
                r"\b(?:fichier|code|css)\s+(?:a [ée]t[ée] |est )?modifi[ée]|"
                r"\b(?:added|changed|modified|updated|applied)\b[^.\n]{0,50}\b(?:file|code|css|rule)\b")
        # Negation belongs to the clause, not the entire answer. A truthful
        # blocker must pass; adding one before a contradictory success must not.
        clauses = re.split(r'[.!?\n;:]|\b(?:mais|cependant|pourtant|but|however)\b', prose, flags=re.I)
        affirmative_change = any(
            re.search(modification_claim, clause, re.I)
            and not re.search(r"\baucun(?:e)?\b|\b(?:ne|n[’'])[^,]{0,70}\bpas\b|\b(?:not|no)\b", clause, re.I)
            for clause in clauses)
        if not self.state["changes"] and affirmative_change:
            if self.task_intent == 'execute':
                raise VerificationError("Aucune nouvelle modification de source n’a été enregistrée pendant cette demande d’exécution. Distingue les changements déjà présents du résultat de compilation, installation ou publication. Utilise run_command pour réaliser les étapes demandées et rapporte leurs résultats ; ne réécris pas les fichiers uniquement pour satisfaire ce contrôle.")
            raise VerificationError("La réponse annonce une modification, mais aucun changement n’a été enregistré par les outils suivis. Effectue la modification avec edit_file/write_file ou indique explicitement qu’aucune modification n’a été effectuée.")
        if self.task_intent == "edit" and not self.state["changes"]:
            # A truthful blocker/no-op is allowed; a plan presented as completion is not.
            honest = re.search(r"aucun(?:e)? (?:fichier[^.\n]{0,40}modifi|modification\b|changement\b)|"
                               r"(?:je n[’']ai|n[’']a été)[^.\n]{0,30}modifi|"
                               r"\bfichier[^.\n]{0,30}n[’']a pas[^.\n]{0,30}modifi|"
                               r"(?:no files (?:were )?changed|no changes made)", prose, re.I)
            if not honest:
                raise VerificationError("La demande est une modification, mais aucun fichier n’a été modifié par les outils suivis. Lis le code actuel puis réalise la demande autorisée. Si un blocage ou un état déjà conforme empêche une modification, écris explicitement « Aucune modification effectuée » et explique les faits vérifiés. Un plan ou une nouvelle demande de permission ne suffit pas.")
        if not summary["checks"]:
            basic = [path for path, change in self.state["changes"].items()
                     if change.get("validation") == "syntax_and_content"]
            if basic:
                notice = ("Validation limitée à la syntaxe disponible et au contenu sauvegardé. "
                          "Aucun test fonctionnel configuré n’a été exécuté pour ces fichiers.")
                return output if notice in output else output.rstrip() + "\n\n" + notice
            if (self.state.get("report") or {}).get("status") == "not_configured":
                notice = ("Audit limité aux observations locales : aucun contrôle exécutable configuré n’a été identifié. "
                          "Aucun test fonctionnel n’a été exécuté ; le comportement du projet reste non vérifié.")
                return output if notice in output else output.rstrip() + "\n\n" + notice
            return output
        footer = self.summary_markdown(summary)
        if output.endswith("\n\n" + footer):
            output = output[:-len(footer)-2]
        # Check explicit test-count claims in prose, leaving quoted/source code exact.
        prose = re.sub(r"```[\s\S]*?```|`[^`]*`|(?m:^>.*$)", "", output)
        allowed = {summary["reported_test_executions"]} | {c["reported_tests"] for c in summary["checks"] if c["reported_tests"] is not None}
        numbers = re.findall(r"\b(\d+)\s+tests?\b|\btotal\s*(?:de\s*)?[:=]?\s*(\d+)\b", prose, re.I)
        unsupported = [int(a or b) for a, b in numbers if int(a or b) not in allowed]
        false_success = summary["passed_checks"] != summary["checks_count"] and re.search(
            r"(?:tous|toutes) (?:les|ces) (?:tests|contrôles|vérifications)[^.\n]{0,50}(?:réussi|valides|passé)|all (?:tests|checks)[^.\n]{0,30}pass|\d+ tests?\s+(?:réussis?|passés?|passed)", prose, re.I)
        # A model-written table must agree row by row, not just use any valid count.
        columns = None
        for line in prose.splitlines():
            if not line.strip().startswith("|"):
                columns = None
                continue
            cells = [v.strip().strip("*") for v in line.strip().strip("|").split("|")]
            if columns is None:
                columns = next((i for i, v in enumerate(cells) if v.lower() in {"tests", "tests décomptés", "test count"}), None)
                continue
            if columns >= len(cells) or not re.fullmatch(r"\d+", cells[columns]):
                continue
            evidence = [c for c in summary["checks"] if c["check"] in line or any(
                Path(arg).name in line for arg in c["argv"][1:] if len(Path(arg).name) > 3)]
            if len(evidence) != 1 or int(cells[columns]) != evidence[0]["reported_tests"]:
                unsupported.append(int(cells[columns]))
        absolute = re.search(r"aucune modification (?:n[’']est )?(?:requise|nécessaire)|(?:projet|project)[^.\n]{0,100}(?:sans (?:défaut|bug)|aucun (?:défaut|bug)|bug[- ]free)|(?:aucun défaut|no defects?)\s*[.!]", prose, re.I)
        if unsupported or absolute or false_success:
            raise VerificationError("Bilan non justifié : n'invente pas de total et ne conclus pas à l'absence de défauts dans le projet. "
                                    "Décris seulement les contrôles exécutés et leurs limites. Le backend ajoute le tableau exact.\n"
                                    + json.dumps(summary, ensure_ascii=False))
        if any(change.get("validation") == "syntax_and_content" for change in self.state["changes"].values()):
            notice = ("Certains fichiers ont uniquement été contrôlés par syntaxe disponible et contenu sauvegardé ; "
                      "les tests ci-dessous ne prouvent pas leur comportement.")
            if notice not in output:
                output = output.rstrip() + "\n\n" + notice
        return output.rstrip() + "\n\n" + footer

    def _execute(self, key: str, check: Check, phase: str) -> dict:
        before = self._evidence_files(check)
        altered = [path for path, expected in check.protected.items() if digest(contents(Path(path))) != expected]
        self.publish({"type": "verification_status", "status": "running", "phase": phase, "check": check.name})
        if altered:
            result = {"passed": False, "exit_code": None,
                      "output": "Contrôles de référence modifiés : " + ", ".join(altered), "duration_ms": 0}
        else:
            result = self.runner(check.argv, check.root, check.timeout)
        after = self._evidence_files(check)
        changed_paths = sorted(path for path in before.keys() | after.keys()
                               if before.get(path) != after.get(path))
        changed = bool(changed_paths)
        if changed:
            result = {**result, 'passed': False, 'output': result.get('output', '')
                + '\nEntrées modifiées pendant le contrôle ; résultat non validé.'}
        receipt = {**result, **test_count(result), "id": uuid.uuid4().hex, "check": check.name, "key": key,
                   "phase": phase, "argv": check.argv, "cwd": str(check.root), "kind": check.kind,
                   "fingerprints": before, "changed_during_check": changed,
                   "changed_paths": changed_paths,
                   "change_versions": {p: c["written"] for p, c in self.state["changes"].items()},
                   "timestamp": datetime.now(timezone.utc).isoformat()}
        self.state["receipts"].append(receipt)
        self.state["report"] = None
        self._save()
        self.publish({"type": "verification_check", **receipt})
        return receipt

    def write(self, path: Path, before: str | None, after: str):
        with self.lock:
            path = self._path(path)
            key = str(path)
            original = contents(path)
            expected = before.encode() if before is not None else None
            if original != expected:
                raise VerificationError("Le fichier a changé depuis sa lecture. Relis-le avant de le modifier.")
            candidate = after.encode()
            if candidate == original:
                return "Aucun changement."
            failure_key = key + ":" + str(digest(candidate))
            if self.failures.get(failure_key, 0) >= 2:
                raise VerificationError("Deux tentatives identiques ont échoué. Réexamine le diagnostic avant de modifier ce fichier.")
            try:
                syntax = check_syntax(path, after)
                self.observe(path)
                applicable = self._applicable([path])
                if not applicable and self._requires_functional_coverage(path) and before is None:
                    raise VerificationError(f"Aucun contrôle fonctionnel configuré pour {path}. Un nouveau fichier exige une suite déclarée ; utilise verification_plan pour consulter les contrôles existants.")
                if not applicable and before is not None:
                    # Documented fallback: the previous content is kept as a
                    # backup and the edit is validated by syntax and content.
                    self.publish({"type": "verification_status", "status": "uncovered", "path": str(path),
                                  "text": "Aucun test fonctionnel ne couvre ce fichier ; validation par syntaxe et contenu."})
                previous_baselines = set(self.state['baseline'])
                for check_key, check in applicable.items():
                    if check_key not in self.state["baseline"]:
                        self.state["baseline"][check_key] = self._execute(check_key, check, "before")
                if any(digest(contents(Path(name))) != expected
                       and not (name == key and digest(candidate) == expected)
                       for check in applicable.values() for name, expected in check.protected.items()):
                    raise VerificationError('Contrôles de référence modifiés pendant la vérification ; écriture refusée.')
                # Tests may run arbitrary project code; check freshness again afterwards.
                if contents(path) != original:
                    raise VerificationError("Le fichier a changé pendant les contrôles préalables.")
                mode = stat.S_IMODE(path.stat().st_mode) if original is not None else 0o644
                change = self.state["changes"].get(key)
                if not change:
                    backup = hashlib.sha256(key.encode()).hexdigest() + ".before"
                    self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                    if original is not None:
                        atomic_write(self.directory / backup, original.decode())
                    change = {"backup": backup, "original": digest(original), "mode": mode}
                    self.state["changes"][key] = change
                change.update({"previous": digest(original), "written": digest(candidate), "syntax": syntax,
                               "syntax_available": syntax != SYNTAX_UNAVAILABLE,
                               "validation": "configured_checks" if applicable else "syntax_and_content"})
                self.state["status"] = "pending"
                self.state["report"] = None
                self._save()  # Recovery information precedes the filesystem mutation.
                replace_checked(path, candidate, digest(original), mode)
                references = [check for check in self.checks.values() if str(path) in check.protected]
                preserved = bool(references) and same_check_definition(path, before, after) and all(
                    check.protected[str(path)] == digest(original) for check in references)
                if preserved:
                    # Version bumps, JSON formatting and Python comments do not
                    # replace the test contract. Executable changes do.
                    for check in self.checks.values():
                        if str(path) in check.protected:
                            check.protected[str(path)] = digest(candidate)
                    self._save()  # Preserve the unchanged definition across task resumption.
                if references:
                    self.publish({"type": "verification_status", "status": "reference_edited", "path": str(path),
                                  "definition_preserved": preserved})
                self.publish({"type": "verification_status", "status": "pending", "files": list(self.state["changes"])})
                result = (f"Syntaxe {syntax}. Modification sauvegardée ; validation fonctionnelle encore requise."
                          if applicable else f"Syntaxe {syntax}. Modification sauvegardée ; vérifier le contenu avec verify_changes. Aucun test fonctionnel configuré.")
                failures = self.baseline_failures(self.state['baseline'].keys() - previous_baselines)
                if failures:
                    result += '\nContrôles déjà en échec AVANT cette modification (ce ne sont pas des régressions démontrées) : ' + json.dumps(failures, ensure_ascii=False)
                if references and not preserved:
                    result += '\nDéfinition de contrôle modifiée : elle ne remplace pas la référence de cette tâche. Restaurer cette référence pour vérifier les modifications applicatives ; un test réécrit ne constitue pas une preuve indépendante.'
                return result
            except VerificationError:
                self.failures[failure_key] = self.failures.get(failure_key, 0) + 1
                raise

    def verify(self) -> dict:
        with self.lock:
            changes = self.state["changes"]
            if not changes and not self.require_checks:
                return {"status": "not_required", "checks": [], "files": []}
            subjects = sorted(set(changes) | {str(path) for path in self.observed})
            actual = {path: digest(contents(Path(path))) for path in subjects}
            old = self.state.get("report")
            if (old and "syntax_checks" in old and old.get("fingerprints") == actual
                    and old.get("inspected_directories", []) == sorted(str(path) for path in self.inspected_directories)
                    and old.get("checks_available", []) == sorted(self.checks)):
                # Changing a test cannot make a cached result valid.
                if all(self._fresh(r) for r in old.get("checks", [])) and all(digest(contents(Path(path))) == expected for check in self._applicable(subjects).values()
                       for path, expected in check.protected.items()):
                    return old
            problems = [f"Modification extérieure : {path}" for path, item in changes.items()
                        if actual[path] != item["written"]]
            applicable = self._applicable(subjects)
            # Explicitly executed configured checks are valid audit subjects even
            # when the agent only read the README, not their implementation files.
            for receipt in self.state["receipts"]:
                if receipt["phase"] == "manual" and receipt["key"] in self.checks:
                    applicable[receipt["key"]] = self.checks[receipt["key"]]
            uncovered = [path for path in changes if not self._applicable([path])
                         and changes[path].get("validation") != "syntax_and_content"]
            problems += [f"Aucun contrôle fonctionnel configuré pour {path}" for path in uncovered]
            basic = [path for path, change in changes.items() if change.get("validation") == "syntax_and_content"]
            syntax_checks = []
            for name in changes:
                try:
                    data = contents(Path(name))
                    validator = check_syntax(Path(name), data.decode())
                    available = validator != SYNTAX_UNAVAILABLE
                    syntax_checks.append({"path": name, "available": available,
                                          "status": "passed" if available else "unavailable",
                                          "validator": validator if available else None,
                                          "fingerprint": digest(data), "phase": "final"})
                except (VerificationError, UnicodeError, AttributeError) as error:
                    problems.append(f"Validation actuelle impossible pour {name} : {error}")
            inspected = list(self.observed) + [path / '_audit_scope_' for path in self.inspected_directories]
            limited_audit = (not changes and not applicable and not self.checks and bool(inspected)
                             and not any(self._requires_functional_coverage(path) for path in inspected))
            if not applicable and not basic and not limited_audit:
                problems.append("Aucun contrôle exécutable identifié ; audit non vérifié.")
            receipts = []
            if not problems:
                for key, check in applicable.items():
                    previous = next((r for r in reversed(self.state["receipts"])
                                     if r["key"] == key and r["phase"] in {"manual", "after"}), None)
                    receipts.append(previous if previous and self._fresh(previous) else self._execute(key, check, "after"))
            after = {path: digest(contents(Path(path))) for path in subjects}
            if actual != after:
                problems.append("Des fichiers ont changé pendant les contrôles ; résultats périmés.")
            for receipt in receipts:
                if not self._fresh(receipt):
                    problems.append("Entrées modifiées pendant le contrôle : " + receipt['check'])
            for item in syntax_checks:
                for receipt in receipts:
                    check = self.checks[receipt["key"]]
                    if (check.kind == "syntax" and receipt.get("passed") and self._fresh(receipt)
                            and any(fnmatch.fnmatch(item["path"], pattern) for pattern in check.patterns)):
                        item.update(available=True, status="passed", validator=check.name,
                                    receipt_id=receipt["id"])
                        break
            passed = bool(receipts or basic) and all(item["passed"] for item in receipts) and not problems
            # An audit can legitimately conclude with reproduced failures. A code
            # modification, however, cannot be accepted with failing controls.
            observed_failure = (not changes and receipts and not problems
                                and all(item.get("exit_code") is not None and not item.get("timed_out") for item in receipts))
            status = ("passed" if passed else "observed_failure" if observed_failure else
                      "not_configured" if limited_audit and not problems else "failed")
            report = {"status": status, "files": list(changes),
                      "checks": receipts, "problems": problems, "fingerprints": actual,
                      "baseline_failures": self.baseline_failures(),
                      "syntax_checks": syntax_checks,
                      "syntax_unavailable": [item["path"] for item in syntax_checks if not item["available"]],
                      "inspected_directories": sorted(str(path) for path in self.inspected_directories),
                      "checks_available": sorted(self.checks),
                      "scope": "configured_local_checks" if receipts else "local_observations_only" if limited_audit else "syntax_and_content",
                      "functional_coverage_missing": [path for path in changes if not any(
                          check.kind == "functional" for check in self._applicable([path]).values())],
                      "universal_correctness": False}
            self.state["report"] = report
            self.state["status"] = report["status"]
            self._save()
            self.publish({"type": "verification_status", **report})
            return report

    def require_verified(self):
        report = self.verify()
        if report["status"] == "failed":
            reasons = report["problems"] + [f"{item['check']} : {item['output'][-2000:]}"
                                            for item in report["checks"] if not item["passed"]]
            baseline = report.get('baseline_failures', [])
            initial = ('\nÉtat initial déjà défaillant : ' + json.dumps(baseline, ensure_ascii=False)
                       + '\nUn arrêt du processus de test ne prouve pas un défaut applicatif. Vérifie son hôte, son exécutable et son environnement avant de modifier le code ou les tests.'
                       if baseline else '')
            raise VerificationError("Tâche non validée. Diagnostique les contrôles ci-dessous puis utilise verify_changes.\n" + "\n".join(reasons) + initial)
        return report

    def rollback(self) -> dict:
        with self.lock:
            restored, conflicts = [], []
            for name, change in reversed(list(self.state["changes"].items())):
                path = self._path(Path(name))
                current = digest(contents(path))
                if current == change["original"]:
                    continue
                mode = stat.S_IMODE(path.stat().st_mode) if current is not None else None
                if current != change["written"] or mode != change["mode"]:
                    conflicts.append(name)
                    continue
                original = (self.directory / change["backup"]).read_bytes() if change["original"] is not None else None
                if digest(original) != change["original"]:
                    raise VerificationError("Sauvegarde incohérente : restauration refusée.")
                replace_checked(path, original, current, change["mode"])
                restored.append(name)
            self.state["status"] = "conflict" if conflicts else "rolled_back"
            self.state["report"] = None
            self._save()
            report = {"status": self.state["status"], "restored": restored, "conflicts": conflicts}
            self.publish({"type": "verification_status", **report})
            return report

    @classmethod
    def recover(cls, workspace: Path, directory: Path, **kwargs):
        guard = cls(workspace, directory, **kwargs)
        data = json.loads((directory / "journal.json").read_text())
        if data.get("version") != 1 or data.get("workspace") != str(guard.workspace):
            raise VerificationError("Journal de récupération invalide.")
        for name, item in data.get("changes", {}).items():
            guard._path(Path(name))
            if Path(item["backup"]).name != item["backup"]:
                raise VerificationError("Chemin de sauvegarde invalide.")
        guard.state = data
        frozen = data.get('verification_context')
        if frozen is None:
            # Old journals retain rollback data but cannot re-freeze modified
            # test definitions as if they were the pre-interruption references.
            guard.require_checks = bool(data.get('receipts'))
            guard.state['report'] = None
        else:
            try:
                for key, item in frozen['checks'].items():
                    check = Check(**{**item, 'root': guard._path(Path(item['root'])),
                        'project': guard._path(Path(item['project'])) if item.get('project') else None})
                    for name in check.protected:
                        guard._path(Path(name))
                    guard.checks[key] = check
                for name in ('projects', 'explicit_policies', 'observed', 'inspected_directories'):
                    setattr(guard, name, {guard._path(Path(path)) for path in frozen[name]})
                guard.require_checks = bool(frozen['require_checks'])
                guard.task_intent = frozen['task_intent']
            except (KeyError, TypeError, ValueError) as error:
                raise VerificationError('Contexte de vérification sauvegardé invalide.') from error
        return guard
