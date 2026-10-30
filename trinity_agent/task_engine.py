"""Durable action receipts, interruption recovery and bounded failure detection.

This journal records execution, not task satisfaction. SQLite transactions make
each state transition durable before an executor starts work. It is neither a
filesystem sandbox nor a rollback mechanism for arbitrary shell side effects.
Only a controller-owned verifier may reconcile uncertain mutations.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Callable
import uuid


class JournalError(RuntimeError):
    pass


class JournalCorrupt(JournalError):
    """Fail closed: a missing/corrupt receipt must never authorize a retry."""


class UnresolvedMutation(JournalError):
    def __init__(self, operation: dict):
        self.operation = deepcopy(operation)
        super().__init__(f"Mutation non réconciliée : {operation['id']} ({operation['status']}). "
                         "Vérifier les effets avant de répéter cette opération.")


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _hash(value) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


_FAILURE_CATEGORIES = frozenset({"rollback_failed", "partial_rollback", "provider_quota",
    "provider_auth", "provider_unavailable", "timeout", "dependency_missing",
    "permission_denied", "invalid_arguments", "conflict", "not_found", "network",
    "cancelled", "interrupted", "incorrect_result", "unknown"})


def classify_failure(result: str | dict | BaseException) -> str:
    """Classify executor observations, never treat model prose as verification.

    Structured status/exit codes take precedence over success-looking output.
    Unknown output stays unknown; callers decide whether execution succeeded.
    """
    if isinstance(result, dict):
        status = str(result.get("status", "")).lower()
        if result.get("rollback_failed") or status == "rollback_failed":
            return "rollback_failed"
        if result.get("partial_rollback") or status in {"partial", "partial_rollback"}:
            return "partial_rollback"
        if result.get("timed_out") or status == "timeout":
            return "timeout"
        if result.get("cancelled") or status in {"cancelled", "canceled"}:
            return "cancelled"
        if (result.get("passed") is False or result.get("verified") is False or
                result.get("success") is False or status in {"failed", "error", "blocked", "unknown"} or
                isinstance(result.get("exit_code"), bool)):
            structured_failure = True
        else:
            structured_failure = result.get("exit_code") not in (None, 0)
        if not structured_failure and (result.get("exit_code") == 0 or
                                       result.get("passed") is True or
                                       status in {"success", "succeeded", "ok"}):
            return "none"
        # Executor-defined failure categories avoid interpreting localized prose.
        # This path never accepts a supplied "success"/"verified" category and
        # never turns a failed exit into successful execution or satisfaction.
        supplied = result.get("category")
        if isinstance(supplied, str) and supplied in _FAILURE_CATEGORIES:
            return supplied
        text = " ".join(str(result.get(key, "")) for key in ("error", "detail", "output", "message", "status"))
        if structured_failure and not text.strip():
            return "incorrect_result"
    else:
        if isinstance(result, (KeyboardInterrupt, SystemExit)) or type(result).__name__ == 'CancelledError':
            return "cancelled"
        if isinstance(result, TimeoutError):
            return "timeout"
        if isinstance(result, FileNotFoundError):
            return "not_found"
        if isinstance(result, PermissionError):
            return "permission_denied"
        if isinstance(result, ConnectionError):
            return "network"
        text = f"{type(result).__name__}: {result}" if isinstance(result, BaseException) else str(result)
    text = text.lower()
    rules = (
        ("rollback_failed", r"rollback.{0,40}(?:failed|failure|échou)|(?:failed|échec).{0,40}rollback"),
        ("partial_rollback", r"partial[ _-]?rollback|rollback partiel"),
        ("provider_quota", r"usagelimitexceeded|quota|insufficient.{0,16}(?:credit|balance)|solde.{0,25}(?:insuffisant|indisponible)|rate.limit|\b429\b"),
        ("provider_auth", r"invalid.api.key|unauthorized|authentication.{0,12}failed|\b401\b"),
        ("timeout", r"timed? ?out|timeout|délai.{0,16}dépass"),
        ("dependency_missing", r"modulenotfounderror|no module named|command not found|executable.{0,20}not found|cannot find (?:module|package)"),
        ("permission_denied", r"permission denied|access denied|operation not permitted|accès refusé|\b403\b"),
        ("invalid_arguments", r"validationerror|invalid.{0,12}(?:argument|parameter)|missing.{0,30}(?:argument|parameter)|unexpected keyword|invalid json|jsondecodeerror"),
        ("conflict", r"concurrent|conflict|stale|changed since|modifié depuis|hash mismatch"),
        ("not_found", r"no such file|file not found|does not exist|introuvable|n.existe pas|\b404\b"),
        ("provider_unavailable", r"service unavailable|bad gateway|\b50[234]\b|provider.{0,20}unavailable"),
        ("network", r"connection.{0,12}(?:reset|refused|aborted)|dns|network unreachable|name resolution|host key verification"),
        ("cancelled", r"cancelled|canceled|interrupted|annulé"),
        ("incorrect_result", r"assertionerror|test.{0,20}fail|verification.{0,20}fail|résultat incorrect|checks?.{0,20}fail"),
    )
    for category, pattern in rules:
        if re.search(pattern, text):
            return category
    return "unknown"


class TaskJournal:
    """One journal per task/conversation, shared safely by concurrent executors.

    ``verifier(operation, evidence)`` must be installed by trusted application
    code. It must inspect current state using a frozen policy and return
    ``{verified: True, outcome: applied|not_applied|rolled_back, receipt: dict}``.
    A tool/model supplied boolean or receipt cannot bypass this callback.
    The callback runs outside the database lock; revision comparison prevents
    its stale observation from overwriting a concurrent state transition.
    """
    _FINISH = {"succeeded", "failed", "unknown", "partial", "cancelled", "rolled_back", "not_applied"}
    _STATUSES = _FINISH | {"prepared", "running", "applied"}
    _UNRESOLVED = {"prepared", "running", "unknown", "partial", "failed", "cancelled", "rolled_back", "not_applied"}

    def __init__(self, directory: str | Path, *, verifier: Callable | None = None, max_completed: int = 256):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.directory.is_symlink():
            raise JournalError("Le journal ne peut pas être un lien symbolique.")
        self.path = self.directory / "operations.sqlite3"
        self.marker = self.directory / ".journal-initialized"
        if self.path.is_symlink():
            raise JournalError("La base du journal ne peut pas être un lien symbolique.")
        if self.marker.is_symlink():
            raise JournalError("Le témoin du journal ne peut pas être un lien symbolique.")
        self.verifier = verifier
        self.max_completed = max(1, min(int(max_completed), 10000))
        # Publish a fully committed initial database atomically. Creating an
        # empty destination first races with another process opening it and
        # makes a crashed initialization indistinguishable from lost receipts.
        if not self.path.exists():
            if self.marker.exists():
                raise JournalCorrupt("Base d’un journal existant absente ; reprise automatique refusée.")
            temporary = self.directory / f".journal-{uuid.uuid4().hex}.tmp"
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            try:
                db = sqlite3.connect(str(temporary), isolation_level=None)
                try:
                    db.execute("PRAGMA synchronous=FULL")
                    db.execute("BEGIN IMMEDIATE")
                    db.execute("CREATE TABLE state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL, checksum TEXT NOT NULL)")
                    self._save(db, {"version": 1, "revision": 0, "operations": []}, insert=True)
                    db.commit()
                finally:
                    db.close()
                try:
                    os.link(temporary, self.path)
                except FileExistsError:
                    pass  # A concurrent initializer published a complete DB.
                if os.name != "nt":
                    directory_fd = os.open(self.directory, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
            finally:
                temporary.unlink(missing_ok=True)
        try:
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                self._read(db)
                db.commit()
            # A later deletion must not make a new worker believe this is a
            # fresh conversation. No action is returned before this is durable.
            try:
                marker_fd = os.open(self.marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                try:
                    os.write(marker_fd, b"task-journal-v1\n")
                    os.fsync(marker_fd)
                finally:
                    os.close(marker_fd)
                if os.name != "nt":
                    directory_fd = os.open(self.directory, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
        except sqlite3.Error as error:
            raise JournalCorrupt("Journal illisible ; aucune opération autorisée.") from error

    @contextmanager
    def _connection(self):
        if self.path.is_symlink() or not self.path.is_file():
            raise JournalCorrupt("Journal absent ou remplacé ; aucune reprise automatique.")
        db = None
        try:
            # DELETE journaling avoids long-lived WAL copies; FULL sync persists
            # the prepared intent before the caller can execute a mutation.
            db = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA busy_timeout=15000")
            yield db
        except sqlite3.Error as error:
            if db is not None and db.in_transaction:
                db.rollback()
            raise JournalCorrupt("Échec de persistance du journal ; opération bloquée.") from error
        finally:
            if db is not None:
                db.close()

    def _read(self, db) -> dict:
        try:
            rows = db.execute("SELECT payload, checksum FROM state WHERE id=1").fetchall()
            if len(rows) != 1 or hashlib.sha256(rows[0][0].encode()).hexdigest() != rows[0][1]:
                raise ValueError("checksum")
            state = json.loads(rows[0][0])
            if state.get("version") != 1 or not isinstance(state.get("revision"), int) or not isinstance(state.get("operations"), list):
                raise ValueError("schema")
            ids = set()
            for op in state["operations"]:
                if (not isinstance(op, dict) or op.get("id") in ids or
                        not re.fullmatch(r"[0-9a-f]{32}", op.get("id", "")) or
                        op.get("status") not in self._STATUSES or
                        not isinstance(op.get("mutation"), bool) or
                        not isinstance(op.get("revision"), int) or
                        op.get("verification_status") not in {"not_checked", "verified", "unverified"} or
                        not re.fullmatch(r"[0-9a-f]{64}", op.get("fingerprint", ""))):
                    raise ValueError("operation")
                ids.add(op["id"])
            return state
        except (ValueError, TypeError, KeyError, sqlite3.Error) as error:
            raise JournalCorrupt("Journal corrompu ; conserver les preuves et vérifier les effets.") from error

    @staticmethod
    def _save(db, state, *, insert=False):
        payload = _json(state)
        checksum = hashlib.sha256(payload.encode()).hexdigest()
        if insert:
            db.execute("INSERT INTO state(id,payload,checksum) VALUES(1,?,?)", (payload, checksum))
        else:
            db.execute("UPDATE state SET payload=?,checksum=? WHERE id=1", (payload, checksum))

    @contextmanager
    def _transaction(self):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            state = self._read(db)
            try:
                yield state
                state["revision"] += 1
                self._save(db, state)
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @classmethod
    def _uncertain(cls, op):
        # An executor's exit 0 is not the receipt of a required postcondition.
        # Keep that crash window non-evictable, including older transaction rows.
        required = op.get("requires_verification", False) or op.get("tool", "").startswith(("operation:", "rollback:"))
        return op["mutation"] and (op["status"] in cls._UNRESOLVED or required) and op.get("verification_status") != "verified"

    @staticmethod
    def _find(state, identifier):
        for operation in state["operations"]:
            if operation["id"] == identifier:
                return operation
        raise JournalError("Opération absente du journal.")

    def start(self, tool: str, arguments: dict, *, mutation: bool, scope: str = "local", requires_verification: bool = False) -> str:
        if not isinstance(tool, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", tool):
            raise ValueError("Nom d’outil invalide.")
        if (not isinstance(arguments, dict) or not isinstance(scope, str) or not isinstance(mutation, bool)
                or not isinstance(requires_verification, bool)):
            raise ValueError("Arguments, portée ou type de mutation invalides.")
        fingerprint = _hash({"tool": tool, "arguments": arguments, "scope": scope})
        with self._transaction() as state:
            for operation in state["operations"]:
                if mutation and operation["fingerprint"] == fingerprint and self._uncertain(operation):
                    raise UnresolvedMutation(operation)
            completed = [op for op in state["operations"] if not self._uncertain(op) and op["status"] not in {"prepared", "running"}]
            remove = {op["id"] for op in completed[:-self.max_completed]}
            state["operations"] = [op for op in state["operations"] if op["id"] not in remove]
            if len(state["operations"]) >= 10000:
                raise JournalError("Journal saturé : réconcilier les opérations inconnues avant de poursuivre.")
            now = time.time()
            identifier = uuid.uuid4().hex
            state["operations"].append({
                "id": identifier, "tool": tool, "arguments_hash": _hash(arguments),
                "scope_hash": _hash(scope), "fingerprint": fingerprint, "mutation": mutation,
                "requires_verification": requires_verification,
                "status": "running", "execution_status": "running", "verification_status": "not_checked",
                "task_satisfaction": "unknown", "may_have_effects": mutation,
                "started_at": now, "updated_at": now, "revision": 0,
            })
        return identifier

    def finish(self, identifier: str, *, status: str, detail: str = "", evidence: dict | None = None):
        status = {"success": "succeeded", "ok": "succeeded", "error": "failed"}.get(status, status)
        if status not in self._FINISH:
            raise ValueError("Statut d’exécution inconnu.")
        if evidence is not None and not isinstance(evidence, dict):
            raise ValueError("La preuve doit être un objet.")
        with self._transaction() as state:
            operation = self._find(state, identifier)
            if operation["status"] not in {"running", "prepared"}:
                raise JournalError("Une opération terminée/inconnue exige une réconciliation indépendante.")
            operation.update(status=status, execution_status=status, updated_at=time.time(),
                             revision=operation["revision"] + 1,
                             detail_hash=_hash(detail),
                             evidence_hash=_hash(evidence) if evidence is not None else None,
                             failure_category=classify_failure(evidence or detail) if status != "succeeded" else "none")
            # A nonzero exit, cancellation or claimed rollback can leave effects.
            # Hashing evidence avoids retaining credentials from command output;
            # the verifier must inspect state independently before clearing risk.
            operation["may_have_effects"] = bool(operation["mutation"])

    def recover(self) -> dict:
        recovered = []
        with self._transaction() as state:
            for operation in state["operations"]:
                if operation["status"] in {"running", "prepared"}:
                    operation.update(status="unknown", execution_status="unknown", verification_status="not_checked",
                                     failure_category="interrupted", updated_at=time.time(), revision=operation["revision"] + 1)
                    recovered.append(operation["id"])
        return {"recovered": recovered, "retry_automatically": False, **self.snapshot()}

    def snapshot(self) -> dict:
        with self._connection() as db:
            state = self._read(db)
        state["unresolved_mutations"] = [op["id"] for op in state["operations"] if self._uncertain(op)]
        state["task_satisfaction"] = "unknown"
        return state

    def reconcile(self, identifier: str, *, evidence: dict) -> dict:
        if self.verifier is None:
            raise JournalError("Vérificateur indépendant non configuré ; une affirmation du modèle ne suffit pas.")
        if not isinstance(evidence, dict):
            raise ValueError("Paramètres de vérification invalides.")
        observed_state = self.snapshot()
        previous = deepcopy(self._find(observed_state, identifier))
        if previous["status"] in {"prepared", "running"}:
            raise JournalError("Récupérer explicitement une exécution interrompue avant de la réconcilier.")
        observed = self.verifier(deepcopy(previous), deepcopy(evidence))
        if not isinstance(observed, dict):
            raise JournalError("Résultat du vérificateur invalide.")
        outcome = observed.get("outcome", "unknown")
        resolved = observed.get("verified") is True and outcome in {"applied", "not_applied", "rolled_back"} and isinstance(observed.get("receipt"), dict) and bool(observed["receipt"])
        with self._transaction() as state:
            operation = self._find(state, identifier)
            if (operation["revision"] != previous["revision"] or
                    state["revision"] != observed_state["revision"]):
                raise JournalError("État modifié pendant la vérification ; résultat périmé.")
            operation.update(status=outcome if resolved else "partial" if outcome == "partial" else "unknown",
                             verification_status="verified" if resolved else "unverified",
                             verification_outcome=outcome if resolved else "unknown",
                             receipt_hash=_hash(observed.get("receipt")), updated_at=time.time(),
                             revision=operation["revision"] + 1,
                             may_have_effects=operation["mutation"] and not (resolved and outcome in {"not_applied", "rolled_back"}))
            return deepcopy(operation)


class FailureTracker:
    """Bound identical/equivalent failed actions without retaining arguments.

    The controller supplies a state revision derived from fresh observations.
    Success clears that exact action only. Changing the revision invalidates old
    failures; an unrelated successful read or paraphrased arguments do not erase
    a no-progress streak for that tool and failure category.
    """
    def __init__(self, *, exact_limit: int | None = 2, equivalent_limit: int | None = 4, max_entries: int = 128):
        self.exact_limit = None if exact_limit is None else max(1, exact_limit)
        self.equivalent_limit = None if equivalent_limit is None else max(self.exact_limit or 1, equivalent_limit)
        self.max_entries = max(1, max_entries)
        self.entries = OrderedDict()
        self.lock = threading.Lock()

    def before_call(self, tool: str, arguments: dict, *, revision: str = "") -> dict:
        exact, group = _hash((tool, arguments, revision)), _hash((tool, revision))
        with self.lock:
            same = [entry for entry in self.entries.values() if entry["group"] == group]
            count = sum(entry["count"] for entry in same if entry["key"] == exact)
            categories = {entry["category"] for entry in same}
            equivalent = max((sum(entry["count"] for entry in same if entry["category"] == category) for category in categories), default=0)
        return {"blocked": ((self.exact_limit is not None and count >= self.exact_limit)
                            or (self.equivalent_limit is not None and equivalent >= self.equivalent_limit)),
                "identical_failures": count, "equivalent_failures": equivalent,
                "retry_automatically": False}

    def record(self, tool: str, arguments: dict, result: str | dict | Exception, *, revision: str = "", succeeded: bool = False) -> dict:
        category = "none" if succeeded else classify_failure(result)
        exact, group = _hash((tool, arguments, revision)), _hash((tool, revision))
        with self.lock:
            if category == "none":
                for key in [key for key, entry in self.entries.items() if entry["key"] == exact]:
                    del self.entries[key]
            else:
                key = (exact, category)
                count = self.entries.get(key, {}).get("count", 0) + 1
                self.entries[key] = {"key": exact, "group": group, "category": category, "count": count}
                self.entries.move_to_end(key)
                while len(self.entries) > self.max_entries:
                    self.entries.popitem(last=False)
        return {"category": category, **self.before_call(tool, arguments, revision=revision)}
