"""Offline crash/concurrency regressions for durable mutation execution."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from trinity_agent.task_engine import (FailureTracker, JournalCorrupt, JournalError,
                             TaskJournal, UnresolvedMutation, classify_failure)


class DurableExecution(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_interrupted_process_persists_intent_before_effect_and_blocks_repeat(self):
        journal_dir = self.root / "journal"
        target = self.root / "file.txt"
        script = """
import os, sys
from pathlib import Path
from trinity_agent.task_engine import TaskJournal
journal = TaskJournal(sys.argv[1])
journal.start('write_file', {'path':sys.argv[2], 'content':'new'}, mutation=True)
Path(sys.argv[2]).write_text('new')
os._exit(23)
"""
        completed = subprocess.run([sys.executable, "-c", script, str(journal_dir), str(target)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 23, completed.stderr)
        journal = TaskJournal(journal_dir)
        recovered = journal.recover()
        self.assertEqual(target.read_text(), "new")
        self.assertEqual(len(recovered["recovered"]), 1)
        self.assertEqual(recovered["operations"][0]["status"], "unknown")
        self.assertFalse(recovered["retry_automatically"])
        with self.assertRaises(UnresolvedMutation):
            journal.start("write_file", {"path":str(target), "content":"new"}, mutation=True)
        # Unrelated work must remain possible while the original is inspected.
        journal.start("read_file", {"path":str(target)}, mutation=False)
        journal.start("write_file", {"path":"different", "content":"new"}, mutation=True)

    def test_independent_verifier_observes_actual_file_and_rejects_model_assertion(self):
        journal = TaskJournal(self.root / "journal")
        operation = journal.start("write_file", {"content":"new"}, mutation=True)
        journal.recover()
        with self.assertRaises(JournalError):
            journal.reconcile(operation, evidence={"verified":True, "outcome":"applied"})
        target = self.root / "target"
        def verifier(op, parameters):
            # Frozen controller target, never a path or boolean from model.
            observed = target.read_text() if target.exists() else None
            return {"verified":observed == "new", "outcome":"applied" if observed == "new" else "unknown",
                    "receipt":{"sha256":hashlib.sha256((observed or "").encode()).hexdigest()}}
        journal.verifier = verifier
        self.assertEqual(journal.reconcile(operation, evidence={"verified":True})["status"], "unknown")
        target.write_text("new")
        receipt = journal.reconcile(operation, evidence={"verified":False})
        self.assertEqual(receipt["status"], "applied")
        self.assertEqual(receipt["verification_status"], "verified")
        self.assertEqual(receipt["execution_status"], "unknown")
        self.assertEqual(receipt["task_satisfaction"], "unknown")
        self.assertEqual(journal.snapshot()["unresolved_mutations"], [])

    def test_failed_command_and_unverified_rollback_may_have_partial_effects(self):
        for status in ("failed", "partial", "cancelled", "rolled_back", "not_applied", "unknown"):
            with self.subTest(status=status):
                journal = TaskJournal(self.root / status)
                operation = journal.start("run_command", {"command":"apply"}, mutation=True)
                journal.finish(operation, status=status, evidence={"exit_code":1, "verified":True})
                entry = journal.snapshot()["operations"][0]
                self.assertTrue(entry["may_have_effects"])
                self.assertNotEqual(entry["verification_status"], "verified")
                with self.assertRaises(UnresolvedMutation):
                    journal.start("run_command", {"command":"apply"}, mutation=True)

    def test_successful_command_never_means_verified_or_satisfied_task(self):
        journal = TaskJournal(self.root)
        operation = journal.start("run_command", {"command":"echo ok"}, mutation=True)
        journal.finish(operation, status="success", evidence={"passed":True})
        entry = journal.snapshot()["operations"][0]
        self.assertEqual(entry["execution_status"], "succeeded")
        self.assertEqual(entry["verification_status"], "not_checked")
        self.assertEqual(entry["task_satisfaction"], "unknown")
        with self.assertRaises(JournalError):
            journal.finish(operation, status="succeeded")

    def test_concurrent_initialization_writes_and_exact_mutation_exclusion(self):
        def create(index):
            journal = TaskJournal(self.root / "shared")
            operation = journal.start("read_file", {"index":index}, mutation=False)
            journal.finish(operation, status="succeeded")
            return operation
        with ThreadPoolExecutor(max_workers=12) as pool:
            identifiers = list(pool.map(create, range(48)))
        self.assertEqual(len(set(identifiers)), 48)
        journal = TaskJournal(self.root / "shared")
        self.assertEqual(len(journal.snapshot()["operations"]), 48)
        def mutate(index):
            try:
                return journal.start("apply", {"same":True}, mutation=True)
            except UnresolvedMutation:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(mutate, range(16)))
        self.assertEqual(sum(item is not None for item in outcomes), 1)

    def test_corruption_is_not_silently_reset(self):
        journal = TaskJournal(self.root)
        operation = journal.start("apply", {"key":1}, mutation=True)
        with sqlite3.connect(journal.path) as db:
            db.execute("UPDATE state SET payload='{}'")
        original = journal.path.read_bytes()
        with self.assertRaises(JournalCorrupt):
            journal.snapshot()
        with self.assertRaises(JournalCorrupt):
            TaskJournal(self.root)
        self.assertEqual(journal.path.read_bytes(), original)
        self.assertTrue(operation)

    def test_missing_or_empty_database_does_not_clear_unknown_mutation(self):
        journal = TaskJournal(self.root)
        journal.start("apply", {}, mutation=True)
        journal.path.unlink()
        with self.assertRaises(JournalCorrupt):
            journal.start("apply", {}, mutation=True)
        with self.assertRaises(JournalCorrupt):
            TaskJournal(self.root)
        journal.path.write_bytes(b"")
        with self.assertRaises(JournalCorrupt):
            TaskJournal(self.root)

    def test_secrets_and_raw_output_are_not_retained(self):
        journal = TaskJournal(self.root)
        secret = "SECRET_EXAMPLE_TOKEN_MUST_NOT_PERSIST_9385093"
        operation = journal.start("run_command", {"password":secret}, mutation=True, scope=secret)
        journal.finish(operation, status="failed", detail=secret, evidence={"output":secret})
        for path in self.root.iterdir():
            if path.is_file():
                self.assertNotIn(secret.encode(), path.read_bytes())
        self.assertNotIn(secret, json.dumps(journal.snapshot()))

    def test_stale_verifier_cannot_overwrite_concurrent_reconciliation(self):
        journal = TaskJournal(self.root)
        operation = journal.start("apply", {}, mutation=True)
        journal.recover()
        def verifier(op, evidence):
            other = TaskJournal(self.root, verifier=lambda *_: {"verified":True,"outcome":"not_applied","receipt":{"check":"absent"}})
            other.reconcile(operation, evidence={})
            return {"verified":True,"outcome":"applied","receipt":{"check":"old"}}
        journal.verifier = verifier
        with self.assertRaises(JournalError):
            journal.reconcile(operation, evidence={})
        self.assertEqual(journal.snapshot()["operations"][0]["status"], "not_applied")

    def test_partial_rollback_does_not_unlock_retry_and_unknowns_are_not_evicted(self):
        journal = TaskJournal(self.root, max_completed=2, verifier=lambda *_: {"verified":True,"outcome":"partial","receipt":{"files_restored":1,"files_conflicted":1}})
        operation = journal.start("apply", {}, mutation=True)
        journal.recover()
        self.assertEqual(journal.reconcile(operation, evidence={})["status"], "partial")
        for index in range(10):
            read = journal.start("read", {"index":index}, mutation=False)
            journal.finish(read, status="succeeded")
        state = journal.snapshot()
        self.assertIn(operation, state["unresolved_mutations"])
        self.assertLessEqual(len(state["operations"]), 4)
        with self.assertRaises(UnresolvedMutation):
            journal.start("apply", {}, mutation=True)

    def test_another_mutation_started_during_verification_invalidates_receipt(self):
        journal = TaskJournal(self.root)
        operation = journal.start("apply", {"target":"a"}, mutation=True)
        journal.recover()
        def verifier(op, evidence):
            journal.start("apply", {"target":"b"}, mutation=True)
            return {"verified":True, "outcome":"applied", "receipt":{"observed":"before b"}}
        journal.verifier = verifier
        with self.assertRaises(JournalError):
            journal.reconcile(operation, evidence={})
        self.assertEqual(journal.snapshot()["operations"][0]["status"], "unknown")

    def test_success_awaiting_required_probe_is_never_evicted_or_replayed(self):
        journal = TaskJournal(self.root, max_completed=1)
        identifier = journal.start('write_file', {'path': 'target'}, mutation=True, requires_verification=True)
        journal.finish(identifier, status='succeeded')
        for index in range(4):
            read = journal.start('read', {'index': index}, mutation=False)
            journal.finish(read, status='succeeded')
        self.assertIn(identifier, journal.snapshot()['unresolved_mutations'])
        with self.assertRaises(UnresolvedMutation):
            journal.start('write_file', {'path': 'target'}, mutation=True)

    def test_unknown_same_arguments_different_scope_does_not_block(self):
        journal = TaskJournal(self.root)
        journal.start("apply", {}, mutation=True, scope="host-A")
        journal.recover()
        journal.start("apply", {}, mutation=True, scope="host-B")
        with self.assertRaises(UnresolvedMutation):
            journal.start("apply", {}, mutation=True, scope="host-A")


class FailureClassification(unittest.TestCase):
    def test_observations_classify_without_success_from_prose(self):
        cases = [("UsageLimitExceeded", "provider_quota"),
                 (TimeoutError("wait"), "timeout"),
                 ("ModuleNotFoundError: no module named x", "dependency_missing"),
                 ("missing 1 required positional argument", "invalid_arguments"),
                 ("Host key verification failed", "network"),
                 ({"exit_code":1, "output":"PASS\nAssertionError"}, "incorrect_result"),
                 ({"exit_code":0, "output":"warning"}, "none"),
                 ({"status":"partial_rollback"}, "partial_rollback"),
                 ({"rollback_failed":True}, "rollback_failed"),
                 ({"category":"permission_denied"}, "permission_denied"),
                 ({"category":"none","exit_code":1}, "incorrect_result"),
                 ({"category":"provider_quota","exit_code":0}, "none"),
                 ({"category":"verified"}, "unknown"),
                 ({'status': 'failed', 'exit_code': 0}, 'unknown'),
                 ({'success': False, 'exit_code': 0}, 'incorrect_result'),
                 (KeyboardInterrupt(), 'cancelled'),
                 ("Everything succeeded", "unknown")]
        for observation, expected in cases:
            with self.subTest(observation=observation):
                self.assertEqual(classify_failure(observation), expected)

    def test_equivalent_failures_and_evidence_revision_invalidation(self):
        tracker = FailureTracker(max_entries=5)
        for path in ("a", "b", "a", "c"):
            tracker.record("read_file", {"path":path}, FileNotFoundError("gone"), revision="tree-1")
        self.assertTrue(tracker.before_call("read_file", {"path":"new paraphrase"}, revision="tree-1")["blocked"])
        self.assertFalse(tracker.before_call("read_file", {"path":"a"}, revision="tree-2")["blocked"])
        tracker.record("read_file", {"path":"a"}, {}, revision="tree-1", succeeded=True)
        self.assertFalse(tracker.before_call("read_file", {"path":"a"}, revision="tree-1")["blocked"])
        for index in range(20):
            tracker.record("read_file", {"path":str(index)}, FileNotFoundError("gone"), revision=str(index))
        self.assertLessEqual(len(tracker.entries), 5)

    def test_unrelated_success_does_not_erase_a_repeated_failure(self):
        tracker = FailureTracker()
        tracker.record("read_file", {"path":"missing"}, FileNotFoundError("gone"), revision="tree-1")
        tracker.record("read_file", {"path":"exists"}, {"exit_code":0}, revision="tree-1", succeeded=True)
        tracker.record("read_file", {"path":"missing"}, FileNotFoundError("gone"), revision="tree-1")
        self.assertTrue(tracker.before_call("read_file", {"path":"missing"}, revision="tree-1")["blocked"])
        self.assertFalse(tracker.before_call("read_file", {"path":"exists"}, revision="tree-1")["blocked"])
        tracker.record("read_file", {"path":"missing"}, {"exit_code":0}, revision="tree-1", succeeded=True)
        self.assertFalse(tracker.before_call("read_file", {"path":"missing"}, revision="tree-1")["blocked"])


if __name__ == "__main__":
    unittest.main()
