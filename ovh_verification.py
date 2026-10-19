"""Local edit transactions and executable checks, independent of model assertions.

This is a correctness guard for file tools, not an OS sandbox for shell/SSH.
Original dirty working files are preserved; rollback never resets a repository.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import fnmatch
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

from ovh_memory import atomic_write


class VerificationError(RuntimeError):
    pass


def digest(content: bytes | None) -> str | None:
    return hashlib.sha256(content).hexdigest() if content is not None else None


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
    from ovh_process import run_process
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
            shell = "bash" if "bash" in content.split("\n", 1)[0] else "sh"
            result = subprocess.run([shell, "-n"], input=content, text=True,
                                    capture_output=True, timeout=15)
            if result.returncode:
                raise VerificationError(result.stderr[-2000:])
            return shell
    except (SyntaxError, ValueError, OSError, subprocess.TimeoutExpired) as error:
        raise VerificationError(f"Syntaxe refusée avant écriture ({path.name}) : {error}") from error
    return "non disponible pour ce format"


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
        self.observed: set[Path] = set()
        self.require_checks = require_checks
        self.task_intent = "question"
        self.failures: dict[str, int] = {}
        self.state = {"version": 1, "workspace": str(self.workspace), "status": "pending",
                      "changes": {}, "baseline": {}, "receipts": [], "report": None}

    def _save(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
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
            folder = path if path.is_dir() else path.parent
            # External projects retain their own checks; never widen the default workspace.
            while self.allow_external or folder.is_relative_to(self.workspace):
                if any((folder / name).exists() for name in (".ovh-agent-checks.json", "package.json", ".git", "pyproject.toml")):
                    if folder in self.projects:
                        return
                    policy = folder / ".ovh-agent-checks.json"
                    definitions = []
                    if policy.is_file():
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
                                                 protected, timeout)
                    self.checks.update(discovered)
                    self.projects.add(folder)
                    return
                if folder == self.workspace or folder == folder.parent:
                    return
                folder = folder.parent

    def plan(self) -> dict:
        with self.lock:
            return {"projects": sorted(str(path) for path in self.projects),
                    "checks": [{"name": check.name, "argv": check.argv, "cwd": str(check.root),
                                "paths": check.patterns, "protected": sorted(check.protected)}
                               for check in self.checks.values()],
                    "instruction": "Utilise les contrôles existants avant de créer des scripts. Les fichiers non couverts sont refusés avant écriture ; n'altère pas les contrôles de référence."}

    def _applicable(self, paths):
        return {key: check for key, check in self.checks.items()
                if any(fnmatch.fnmatch(str(path), pattern) for path in paths for pattern in check.patterns)}

    def _evidence_files(self, check: Check) -> dict:
        paths = set(self.observed) | {Path(p) for p in self.state["changes"]} | {Path(p) for p in check.protected}
        # Literal declared subjects matter even when a compliant task needs no edit.
        for pattern in check.patterns:
            if not any(marker in pattern for marker in ('*', '?', '[')):
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

    def validate_summary(self, output: str) -> str:
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
        if self.task_intent == "edit" and not self.state["changes"]:
            # A truthful blocker/no-op is allowed; a plan presented as completion is not.
            honest = re.search(r"aucun(?:e)? (?:fichier[^.\n]{0,40}modifi|modification (?:effectuée|réalisée|appliquée))|"
                               r"(?:je n[’']ai|n[’']a été)[^.\n]{0,30}modifi|"
                               r"(?:no files (?:were )?changed|no changes made)", prose, re.I)
            if not honest:
                raise VerificationError("La demande est une modification, mais aucun fichier n’a été modifié par les outils suivis. Lis le code actuel puis réalise la demande autorisée. Si un blocage ou un état déjà conforme empêche une modification, écris explicitement « Aucune modification effectuée » et explique les faits vérifiés. Un plan ou une nouvelle demande de permission ne suffit pas.")
        if not summary["checks"]:
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
        receipt = {**result, **test_count(result), "id": uuid.uuid4().hex, "check": check.name, "key": key,
                   "phase": phase, "argv": check.argv, "cwd": str(check.root),
                   "fingerprints": before, "changed_during_check": before != self._evidence_files(check),
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
                if any(str(path) in check.protected for check in self.checks.values()):
                    raise VerificationError("Contrôle de référence protégé : modification refusée avant écriture.")
                if not self._applicable([path]):
                    raise VerificationError(f"Aucun contrôle fonctionnel configuré pour {path}. Écriture refusée avant modification. Utilise verification_plan sur le dossier du projet pour consulter les contrôles existants ; ne contourne pas ce refus avec le shell.")
                for check_key, check in self._applicable([path]).items():
                    if check_key not in self.state["baseline"]:
                        self.state["baseline"][check_key] = self._execute(check_key, check, "before")
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
                change.update({"previous": digest(original), "written": digest(candidate), "syntax": syntax})
                self.state["status"] = "pending"
                self.state["report"] = None
                self._save()  # Recovery information precedes the filesystem mutation.
                replace_checked(path, candidate, digest(original), mode)
                self.publish({"type": "verification_status", "status": "pending", "files": list(self.state["changes"])})
                return f"Syntaxe {syntax}. Modification sauvegardée ; validation fonctionnelle encore requise."
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
            if old and old.get("fingerprints") == actual:
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
            uncovered = [path for path in changes if not self._applicable([path])]
            problems += [f"Aucun contrôle fonctionnel configuré pour {path}" for path in uncovered]
            if not applicable:
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
            passed = bool(receipts) and all(item["passed"] for item in receipts) and not problems
            # An audit can legitimately conclude with reproduced failures. A code
            # modification, however, cannot be accepted with failing controls.
            observed_failure = (not changes and receipts and not problems
                                and all(item.get("exit_code") is not None and not item.get("timed_out") for item in receipts))
            status = "passed" if passed else "observed_failure" if observed_failure else "failed"
            report = {"status": status, "files": list(changes),
                      "checks": receipts, "problems": problems, "fingerprints": actual,
                      "scope": "configured_local_checks", "universal_correctness": False}
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
            raise VerificationError("Tâche non validée. Corrige les erreurs puis utilise verify_changes.\n" + "\n".join(reasons))
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
        return guard
