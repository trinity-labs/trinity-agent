"""Read-only acceptance contracts, independent of a model's success claims.

An optional ``acceptance`` section in .ovh-agent-checks.json is frozen when the
task starts. It can contain version=1 and a bounded list of declarative criteria:
file_exists, file_absent, file_sha256, text_contains, text_absent, json_equals,
or configured_check. No criterion executes code, imports a module, or runs a shell.

Passing a configured contract is deliberately narrower than proving that it
captures a user's natural-language goal. Model-proposed criteria are useful
observations but cannot certify task fulfillment. Receipts must be provided by
the controller, never accepted from model tool arguments or prose.
"""
from __future__ import annotations

from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import threading
from typing import Any


MAX_FILE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 24_000_000
MAX_CRITERIA = 64
MAX_RECEIPTS = 256
MAX_FINGERPRINTS = 512
_TYPES = {"file_exists", "file_absent", "file_sha256", "text_contains",
          "text_absent", "json_equals", "configured_check"}
_SENSITIVE = {".ssh", ".gnupg", ".aws", ".azure", ".kube", ".env", ".netrc",
              ".npmrc", ".pypirc", "credentials", "credentials.json", "token",
              "token.json", "secrets", "secrets.json", "id_rsa", "id_ed25519",
              "id_ecdsa", "id_dsa"}
_SECRET_FIELDS = {"password", "passwd", "secret", "secrets", "token", "api_key",
                  "apikey", "private_key", "credentials", "access_token", "refresh_token"}


class AcceptanceError(ValueError):
    """A contract or observation cannot safely be evaluated."""


def _strict_json(raw: str | bytes) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise AcceptanceError("Clé JSON dupliquée.")
            result[key] = value
        return result

    def constant(_value):
        raise AcceptanceError("Constante JSON non standard.")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        # Parse errors may include content; never echo project data into a report.
        raise AcceptanceError("Document JSON invalide ou ambigu.") from error


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _pointer_parts(pointer):
    if not isinstance(pointer, str) or len(pointer) > 1024:
        raise AcceptanceError("Pointeur JSON invalide.")
    if pointer == "":
        return []
    if not pointer.startswith("/") or re.search(r"~(?:[^01]|$)", pointer):
        raise AcceptanceError("Pointeur JSON invalide.")
    parts = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
    if len(parts) > 64 or any(part.lower() in _SECRET_FIELDS for part in parts):
        raise AcceptanceError("Pointeur sensible ou trop profond.")
    return parts


class AcceptanceSuite:
    """Frozen task-start policy; evidence is re-read on every evaluate call.

    Paths are relative to the policy's directory, constrained to ``workspace``.
    Symlinks, special files, credential paths and oversized reads fail closed.
    A report is a point-in-time observation, not a persistent certification.
    """

    def __init__(self, workspace: str | Path, policy_path: str | Path | None = None):
        self.workspace = Path(workspace).resolve(strict=True)
        self.started_at = datetime.now(timezone.utc)
        self._lock = threading.RLock()
        self._criteria = []
        self._checks = {}
        self._error = None
        self._policy_snapshot = None
        self._policy_name = None
        self._base = PurePosixPath(".")
        self._budget = MAX_TOTAL_BYTES
        self._observed = {}
        try:
            supplied = Path(policy_path) if policy_path is not None else Path(".ovh-agent-checks.json")
            self._policy_name = self._relative(supplied)
            self._base = PurePosixPath(self._policy_name).parent
            policy = self._snapshot(self._policy_name)
            self._policy_snapshot = self._identity(policy)
            if policy["kind"] == "absent":
                return
            document = _strict_json(policy["data"])
            if not isinstance(document, dict) or type(document.get("version")) is not int or document["version"] != 1:
                raise AcceptanceError("Version de politique invalide.")
            self._checks = self._check_definitions(document.get("checks", []))
            if "acceptance" not in document:
                return
            section = document["acceptance"]
            if (not isinstance(section, dict) or type(section.get("version")) is not int or section["version"] != 1
                    or set(section) - {"version", "criteria"}):
                raise AcceptanceError("Section acceptance invalide.")
            self._criteria = self._validate_criteria(section.get("criteria"))
            for name in {spec["check"] for spec in self._criteria if spec["type"] == "configured_check"}:
                self._checks[name]["frozen_references"] = self._references(self._checks[name])
        except (AcceptanceError, OSError, TypeError) as error:
            self._error = str(error) if isinstance(error, AcceptanceError) else "Politique inaccessible."

    def _relative(self, value) -> str:
        text = str(value)
        if not text or len(text) > 4096 or "\\" in text or "\x00" in text:
            raise AcceptanceError("Chemin invalide.")
        path = Path(text)
        if path.is_absolute():
            try:
                path = path.relative_to(self.workspace)
            except ValueError as error:
                raise AcceptanceError("Chemin hors projet refusé.") from error
        if not path.parts or any(part in {"..", ""} for part in path.parts):
            raise AcceptanceError("Chemin hors projet ou vide.")
        for part in path.parts:
            lower = part.lower()
            if (lower in _SENSITIVE or lower.startswith(".env.")
                    or lower.endswith((".pem", ".key", ".p12", ".pfx"))):
                raise AcceptanceError("Chemin sensible refusé.")
        return path.as_posix()

    def _criterion_path(self, value):
        if not isinstance(value, str) or Path(value).is_absolute():
            raise AcceptanceError("Le critère exige un chemin relatif.")
        return self._relative(self._base / value)

    def _snapshot(self, name: str, *, content=True) -> dict:
        """Open each component without following links, then check inode stability.

        A second pass at report creation detects mutations between criteria. No
        user-controlled executable is loaded; O_NONBLOCK also prevents a FIFO
        replacing a regular file from hanging the controller.
        """
        name = self._relative(name)
        if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
            raise AcceptanceError("Lecture sécurisée indisponible sur cette plateforme.")
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        descriptors = []
        try:
            current = os.open(self.workspace, flags | os.O_DIRECTORY)
            descriptors.append(current)
            parts = PurePosixPath(name).parts
            for component in parts[:-1]:
                current = os.open(component, flags | os.O_DIRECTORY, dir_fd=current)
                descriptors.append(current)
            descriptor = os.open(parts[-1], flags, dir_fd=current)
            descriptors.append(descriptor)
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise AcceptanceError("Fichier régulier requis.")
            if content and (before.st_size > MAX_FILE_BYTES or before.st_size > self._budget):
                raise AcceptanceError("Limite de lecture atteinte.")
            chunks, size = [], 0
            while content:
                chunk = os.read(descriptor, min(65536, MAX_FILE_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_FILE_BYTES or size > self._budget:
                    raise AcceptanceError("Limite de lecture atteinte.")
            after = os.fstat(descriptor)
            actual = os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
            if _stamp(before) != _stamp(after) or _stamp(actual) != _stamp(after):
                raise AcceptanceError("Modification concurrente pendant la lecture.")
            self._budget -= size
            data = b"".join(chunks) if content else None
            return {"kind": "file", "sha256": _hash(data) if content else None,
                    "stamp": _stamp(after), "data": data, "content": content}
        except FileNotFoundError:
            return {"kind": "absent", "sha256": None, "stamp": None, "data": None, "content": content}
        except OSError as error:
            raise AcceptanceError("Lecture refusée : lien, permissions ou état inaccessible.") from error
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    @staticmethod
    def _identity(snapshot):
        return {key: snapshot[key] for key in ("kind", "sha256", "stamp")}

    def _observe(self, path, *, content=True):
        if path not in self._observed:
            self._observed[path] = self._snapshot(path, content=content)
        elif content and not self._observed[path]["content"]:
            previous = self._observed[path]
            current = self._snapshot(path)
            if current["stamp"] != previous["stamp"] or current["kind"] != previous["kind"]:
                raise AcceptanceError("Modification concurrente entre observations.")
            self._observed[path] = current
        return self._observed[path]

    def _check_definitions(self, definitions):
        if not isinstance(definitions, list) or len(definitions) > MAX_RECEIPTS:
            raise AcceptanceError("Liste de contrôles invalide.")
        checks = {}
        for index, definition in enumerate(definitions):
            if not isinstance(definition, dict):
                raise AcceptanceError("Contrôle invalide.")
            name = definition.get("name", f"{self.workspace / str(self._base)}:{index}")
            argv = definition.get("argv")
            if (not isinstance(name, str) or not name or name in checks or not isinstance(argv, list)
                    or not argv or len(argv) > 128 or not all(isinstance(arg, str) and arg for arg in argv)):
                raise AcceptanceError("Contrôle ambigu ou argv invalide.")
            cwd = definition.get("cwd", ".")
            if not isinstance(cwd, str) or Path(cwd).is_absolute() or ".." in Path(cwd).parts:
                raise AcceptanceError("Répertoire du contrôle invalide.")
            protections = definition.get("protect", [])
            if (not isinstance(protections, list) or len(protections) > 64
                    or not all(isinstance(item, str) and item and len(item) <= 1024
                               and not Path(item).is_absolute() and ".." not in Path(item).parts
                               for item in protections)):
                raise AcceptanceError("Références protégées invalides.")
            checks[name] = {"argv": [sys.executable if arg == "{python}" else arg for arg in argv],
                            "cwd": str(self.workspace / self._base / cwd), "protect": protections}
        return checks

    def _protected_matches(self, pattern):
        """Expand only explicit protected patterns, with bounded directory visits.

        Glob expansion must not silently traverse a symlink or enumerate a whole
        monorepo without a limit. Relative dir descriptors preserve that boundary.
        """
        parts = PurePosixPath(self._criterion_path(pattern)).parts
        matches, visits = set(), [0]
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

        def walk(descriptor, prefix, remaining):
            visits[0] += 1
            if visits[0] > 2048 or len(matches) > MAX_FINGERPRINTS:
                raise AcceptanceError("Trop de références protégées à inventorier.")
            component, rest = remaining[0], remaining[1:]
            if component == "**" and rest:
                walk(descriptor, prefix, rest)
            with os.scandir(descriptor) as entries:
                for entry in entries:
                    visits[0] += 1
                    if visits[0] > 2048:
                        raise AcceptanceError("Inventaire protégé trop large.")
                    if not fnmatch.fnmatchcase(entry.name, component):
                        continue
                    relative = str(prefix / entry.name)
                    if entry.is_symlink():
                        raise AcceptanceError("Référence protégée symbolique refusée.")
                    if entry.is_file(follow_symlinks=False) and not rest:
                        matches.add(relative)
                    if entry.is_dir(follow_symlinks=False) and (rest or component == "**"):
                        child = os.open(entry.name, flags, dir_fd=descriptor)
                        try:
                            walk(child, PurePosixPath(relative), remaining if component == "**" else rest)
                        finally:
                            os.close(child)

        descriptor = os.open(self.workspace, flags)
        try:
            walk(descriptor, PurePosixPath("."), parts)
        except OSError as error:
            raise AcceptanceError("Références protégées inaccessibles.") from error
        finally:
            os.close(descriptor)
        # Missing literal references are also frozen: adding a test later cannot
        # silently turn a model-created script into an independent oracle.
        if not any(char in pattern for char in "*?["):
            matches.add(self._criterion_path(pattern))
        return matches

    def _references(self, definition):
        names = {self._policy_name}
        for pattern in definition["protect"]:
            names.update(self._protected_matches(pattern))
        for arg in definition["argv"]:
            candidate = Path(arg)
            if candidate.is_absolute() or arg.startswith("-"):
                continue
            try:
                path = self._relative(Path(definition["cwd"]) / candidate)
            except AcceptanceError:
                continue  # An argv entry may be code or a value, not a file path.
            if (self.workspace / path).is_dir():
                continue  # Directories require explicit, bounded protect patterns.
            snapshot = self._snapshot(path)
            if snapshot["kind"] != "absent":
                names.add(path)
        if len(names) > MAX_FINGERPRINTS:
            raise AcceptanceError("Trop de références protégées.")
        return {name: self._snapshot(name)["sha256"] for name in sorted(names)}

    def _validate_criteria(self, criteria):
        if not isinstance(criteria, list) or not 1 <= len(criteria) <= MAX_CRITERIA:
            raise AcceptanceError("Entre 1 et 64 critères explicites sont requis.")
        result, identifiers = [], set()
        for spec in criteria:
            if not isinstance(spec, dict):
                raise AcceptanceError("Critère invalide.")
            identifier, kind = spec.get("id"), spec.get("type")
            if (not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", identifier)
                    or identifier in identifiers or not isinstance(kind, str) or kind not in _TYPES):
                raise AcceptanceError("Identifiant dupliqué ou type de critère invalide.")
            identifiers.add(identifier)
            allowed = {"id", "type", "check"} if kind == "configured_check" else {"id", "type", "path"}
            if kind == "file_sha256":
                allowed.add("sha256")
                if not isinstance(spec.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", spec["sha256"]):
                    raise AcceptanceError("Empreinte SHA256 invalide.")
            elif kind in {"text_contains", "text_absent"}:
                allowed.add("text")
                if not isinstance(spec.get("text"), str) or not 1 <= len(spec["text"]) <= 32768:
                    raise AcceptanceError("Texte attendu invalide.")
            elif kind == "json_equals":
                allowed.update({"pointer", "value"})
                _pointer_parts(spec.get("pointer"))
                if "value" not in spec:
                    raise AcceptanceError("Valeur JSON attendue manquante.")
                try:
                    encoded = json.dumps(spec["value"], allow_nan=False)
                    if len(encoded) > 32768:
                        raise ValueError()
                except (ValueError, TypeError, RecursionError) as error:
                    raise AcceptanceError("Valeur JSON attendue invalide.") from error
            if set(spec) - allowed:
                raise AcceptanceError("Champ de critère inconnu : aucune commande n'est acceptée.")
            if kind == "configured_check":
                if not isinstance(spec.get("check"), str) or spec["check"] not in self._checks:
                    raise AcceptanceError("Contrôle non déclaré dans la politique figée.")
            else:
                self._criterion_path(spec.get("path"))
            # Defensive copy: model/caller objects must not mutate a frozen contract.
            result.append(json.loads(json.dumps(spec)))
        return result

    def describe(self) -> dict:
        """Only safe metadata; observed values and expected file contents stay private."""
        return {"configured": bool(self._criteria) and not self._error,
                "policy_path": self._policy_name, "policy_fingerprint":
                self._policy_snapshot.get("sha256") if self._policy_snapshot else None,
                "started_at": self.started_at.isoformat(), "error": self._error,
                "criteria": [{key: spec[key] for key in ("id", "type", "path", "check") if key in spec}
                             | {"source": "project_policy", "independent": True} for spec in self._criteria],
                "satisfaction_scope": "configured_contract_only",
                "natural_language_goal_verified": False}

    def _receipt_status(self, receipt):
        if (not isinstance(receipt, dict) or not isinstance(receipt.get("check"), str)
                or receipt["check"] not in self._checks):
            return "unverified", "Contrôle absent de la politique figée."
        definition = self._checks[receipt["check"]]
        if receipt.get("argv") != definition["argv"] or receipt.get("cwd") != definition["cwd"]:
            return "unverified", "Le reçu ne correspond pas au contrôle déclaré."
        if "frozen_references" in definition and self._references(definition) != definition["frozen_references"]:
            return "stale", "Références de contrôle modifiées depuis le début de la tâche."
        try:
            timestamp = datetime.fromisoformat(receipt.get("timestamp", ""))
            if (timestamp.tzinfo is None or timestamp < self.started_at
                    or timestamp > datetime.now(timezone.utc)):
                return "stale", "Reçu antérieur à la tâche ou horodatage invalide."
        except (ValueError, TypeError):
            return "unverified", "Horodatage du reçu absent ou invalide."
        if (receipt.get("phase") not in {"after", "manual"} or receipt.get("environment_changed")
                or receipt.get("changed_during_check")):
            return "stale", "Contrôle de référence, environnement modifié ou écriture concurrente."
        fingerprints = receipt.get("fingerprints")
        if not isinstance(fingerprints, dict) or not 1 <= len(fingerprints) <= MAX_FINGERPRINTS:
            return "unverified", "Empreintes du reçu absentes ou invalides."
        observed = {}
        for raw_path, expected in fingerprints.items():
            if not isinstance(raw_path, str):
                return "unverified", "Chemin de preuve invalide."
            path = self._relative(raw_path)
            if path in observed or (expected is not None and (not isinstance(expected, str) or
                    not re.fullmatch(r"[0-9a-f]{64}", expected))):
                return "unverified", "Empreintes du reçu ambiguës."
            observed[path] = expected
            if self._observe(path)["sha256"] != expected:
                return "stale", "Fichier modifié après le contrôle."
        if self._policy_name not in observed or observed[self._policy_name] != self._policy_snapshot["sha256"]:
            return "unverified", "Le reçu ne couvre pas la politique figée."
        if any(path not in observed or observed[path] != expected
               for path, expected in definition.get("frozen_references", {}).items()):
            return "unverified", "Le reçu ne couvre pas toutes les références protégées."
        if receipt.get("timed_out") or receipt.get("exit_code") is None:
            return "unknown", "Contrôle interrompu ou résultat inconnu."
        if receipt.get("passed") is True and type(receipt.get("exit_code")) is int and receipt["exit_code"] == 0:
            return "passed", "Reçu du contrôleur actuel et vérifié."
        return "failed", "Le contrôle a échoué."

    def _evaluate_one(self, spec, receipts):
        if spec["type"] == "configured_check":
            matches = [item for item in receipts if isinstance(item, dict) and item.get("check") == spec["check"]]
            return self._receipt_status(matches[-1]) if matches else ("not_run", "Contrôle non exécuté.")
        kind = spec["type"]
        data = self._observe(self._criterion_path(spec["path"]), content=kind not in {"file_exists", "file_absent"})
        exists = data["kind"] == "file"
        if kind in {"file_exists", "file_absent"}:
            passed = exists == (kind == "file_exists")
        elif not exists:
            return "failed", "Fichier attendu absent."
        elif kind == "file_sha256":
            passed = data["sha256"] == spec["sha256"]
        elif kind in {"text_contains", "text_absent"}:
            try:
                text = data["data"].decode("utf-8")
            except UnicodeError as error:
                raise AcceptanceError("Texte UTF-8 requis.") from error
            passed = (spec["text"] in text) == (kind == "text_contains")
        else:
            actual = _strict_json(data["data"])
            for component in _pointer_parts(spec["pointer"]):
                if isinstance(actual, dict) and component in actual:
                    actual = actual[component]
                elif isinstance(actual, list) and re.fullmatch(r"0|[1-9][0-9]*", component) and len(component) <= 9 and int(component) < len(actual):
                    actual = actual[int(component)]
                else:
                    return "failed", "Emplacement JSON attendu absent."
            # JSON boolean true must not compare equal to integer 1 in Python.
            passed = json.dumps(actual, sort_keys=True, ensure_ascii=True) == json.dumps(spec["value"], sort_keys=True, ensure_ascii=True)
        return ("passed", "Critère satisfait.") if passed else ("failed", "Critère non satisfait.")

    @staticmethod
    def _command_status(results):
        if not results:
            return "not_run"
        if any(not isinstance(item, dict) or item.get("exit_code") is None or item.get("timed_out")
               or item.get("status") in {"unknown", "interrupted"} for item in results):
            return "unknown"
        return "passed" if all(type(item.get("exit_code")) is int and item["exit_code"] == 0 for item in results) else "failed"

    def evaluate(self, *, check_receipts=(), proposed_criteria=(), command_results=()) -> dict:
        """Produce JSON-safe evidence without running commands or writing files."""
        with self._lock:
            return self._evaluate(check_receipts, proposed_criteria, command_results)

    def _evaluate(self, check_receipts, proposed_criteria, command_results):
        self._budget = MAX_TOTAL_BYTES
        self._observed = {}
        report = self.describe() | {"evaluated_at": datetime.now(timezone.utc).isoformat(),
                                   "criteria": [], "policy_unchanged": False, "errors": [],
                                   "checks": [],
                                   "test_status": "not_run", "task_fulfilled": None,
                                   "contract_satisfied": None, "fingerprints": {}}
        if not isinstance(command_results, (list, tuple)) or len(command_results) > MAX_RECEIPTS:
            report["command_status"] = "unknown"
        else:
            report["command_status"] = self._command_status(command_results)
        if not isinstance(check_receipts, (list, tuple)) or len(check_receipts) > MAX_RECEIPTS:
            report["errors"].append("Trop de reçus ou format invalide.")
            check_receipts = ()
        try:
            if self._error:
                raise AcceptanceError(self._error)
            policy = self._observe(self._policy_name)
            report["policy_unchanged"] = self._identity(policy) == self._policy_snapshot
            if not report["policy_unchanged"]:
                raise AcceptanceError("Politique modifiée depuis le début de la tâche ; aucun nouveau critère n'est accepté.")
            latest = {}
            for receipt in check_receipts:
                if isinstance(receipt, dict) and isinstance(receipt.get("check"), str):
                    latest[receipt["check"]] = receipt
                else:
                    report["checks"].append({"status": "unverified", "detail": "Reçu invalide."})
            for name, receipt in latest.items():
                try:
                    status, detail = self._receipt_status(receipt)
                except AcceptanceError as error:
                    status, detail = "unknown", str(error)
                report["checks"].append({"check": name if name in self._checks else "undeclared",
                                         "status": status, "detail": detail})
            if report["checks"]:
                report["test_status"] = "passed" if all(item["status"] == "passed" for item in report["checks"]) else "not_passed"
            groups = [(self._criteria, "project_policy", True)]
            if proposed_criteria:
                try:
                    groups.append((self._validate_criteria(proposed_criteria), "model_proposed", False))
                except AcceptanceError as error:
                    report["errors"].append(f"Proposition non vérifiée : {error}")
            for criteria, source, independent in groups:
                for spec in criteria:
                    try:
                        status, detail = self._evaluate_one(spec, check_receipts)
                    except AcceptanceError as error:
                        status, detail = "unknown", str(error)
                    report["criteria"].append({"id": spec["id"], "type": spec["type"], "source": source,
                                               "independent": independent, "status": status, "detail": detail})
            # Compare every observation after all criteria: individually valid
            # reads must not certify mutually inconsistent versions of a project.
            for name, snapshot in list(self._observed.items()):
                current = self._snapshot(name, content=snapshot["content"])
                if self._identity(current) != self._identity(snapshot):
                    raise AcceptanceError("Modification concurrente pendant l'évaluation ; preuves périmées.")
            for definition in self._checks.values():
                if ("frozen_references" in definition
                        and self._references(definition) != definition["frozen_references"]):
                    raise AcceptanceError("Références protégées modifiées pendant l'évaluation.")
            configured = [item for item in report["criteria"] if item["independent"]]
            if configured and not report["errors"]:
                status = (True if all(item["status"] == "passed" for item in configured)
                          else False if any(item["status"] == "failed" for item in configured) else None)
                report["contract_satisfied"] = status
                report["task_fulfilled"] = status
        except (AcceptanceError, OSError) as error:
            report["errors"].append(str(error) if isinstance(error, AcceptanceError) else "Lecture inaccessible.")
        report["fingerprints"] = {name: item["sha256"] for name, item in self._observed.items() if item["content"]}
        report["criteria_status"] = ("invalid" if report["errors"] else "not_configured" if not self._criteria
                                     else "passed" if report["contract_satisfied"] is True
                                     else "failed" if report["contract_satisfied"] is False else "unverified")
        return report
