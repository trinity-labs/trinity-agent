"""Offline adversarial tests for the bounded, read-only acceptance controller."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ovh_acceptance import AcceptanceSuite, MAX_FILE_BYTES
from ovh_verification import ChangeGuard


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        self.policy = self.workspace / ".ovh-agent-checks.json"
        self.source = self.workspace / "result.json"
        self.source.write_text('{"ready":false,"nested":{"a/b":[true]},"~value":12}')
        self.check = self.workspace / "check.py"
        self.check.write_text("raise AssertionError('never executed by acceptance')\n")
        self.definitions = [{"name": "Behavior", "paths": ["result.json"],
                             "argv": ["{python}", "check.py"], "protect": ["check.py"]}]

    def write_policy(self, criteria=None, **extra):
        document = {"version": 1, "checks": self.definitions, **extra}
        if criteria is not None:
            document["acceptance"] = {"version": 1, "criteria": criteria}
        self.policy.write_text(json.dumps(document))
        return AcceptanceSuite(self.workspace)

    def ready(self, expected=True):
        return {"id": "ready", "type": "json_equals", "path": "result.json",
                "pointer": "/ready", "value": expected}

    def receipt(self, **changes):
        return {"check": "Behavior", "argv": [sys.executable, "check.py"],
                "cwd": str(self.workspace), "phase": "after", "exit_code": 0,
                "passed": True, "timestamp": datetime.now(timezone.utc).isoformat(),
                "fingerprints": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in (self.policy, self.source, self.check)}, **changes}

    def test_command_and_tests_pass_do_not_satisfy_wrong_expectation(self):
        suite = self.write_policy([self.ready(), {"id": "tests", "type": "configured_check", "check": "Behavior"}])
        report = suite.evaluate(command_results=[{"exit_code": 0}], check_receipts=[self.receipt()])
        self.assertEqual(report["command_status"], "passed")
        self.assertEqual(report["test_status"], "passed")
        self.assertFalse(report["task_fulfilled"])
        self.assertFalse(report["contract_satisfied"])
        self.assertFalse(report["natural_language_goal_verified"])

    def test_optional_policy_and_model_proposal_never_certify(self):
        suite = AcceptanceSuite(self.workspace)
        report = suite.evaluate(proposed_criteria=[self.ready(False)])
        self.assertEqual(report["criteria"][0]["status"], "passed")
        self.assertFalse(report["criteria"][0]["independent"])
        self.assertEqual(report["criteria"][0]["source"], "model_proposed")
        self.assertIsNone(report["task_fulfilled"])
        self.assertEqual(report["criteria_status"], "not_configured")

    def test_project_tests_pass_without_acceptance_is_not_fulfillment(self):
        suite = self.write_policy()
        report = suite.evaluate(check_receipts=[self.receipt()])
        self.assertEqual(report["test_status"], "passed")
        self.assertIsNone(report["task_fulfilled"])

    def test_all_declarative_criteria_are_read_only(self):
        data = self.source.read_bytes()
        suite = self.write_policy([
            {"id": "exists", "type": "file_exists", "path": "result.json"},
            {"id": "absent", "type": "file_absent", "path": "not-created/empty.json"},
            {"id": "hash", "type": "file_sha256", "path": "result.json", "sha256": hashlib.sha256(data).hexdigest()},
            {"id": "text", "type": "text_contains", "path": "result.json", "text": '"ready":false'},
            {"id": "notext", "type": "text_absent", "path": "result.json", "text": "missing"},
            {"id": "json", "type": "json_equals", "path": "result.json", "pointer": "/nested/a~1b/0", "value": True},
            {"id": "tilde", "type": "json_equals", "path": "result.json", "pointer": "/~0value", "value": 12},
        ])
        before = {path: path.read_bytes() for path in self.workspace.iterdir()}
        with patch("subprocess.Popen", side_effect=AssertionError("No subprocess")), patch("os.system", side_effect=AssertionError("No shell")):
            report = suite.evaluate()
        self.assertTrue(report["task_fulfilled"])
        self.assertEqual(report["satisfaction_scope"], "configured_contract_only")
        self.assertEqual(before, {path: path.read_bytes() for path in self.workspace.iterdir()})

    def test_expectation_equality_and_json_pointer_are_strict(self):
        tests = [("/ready", 0), ("/nested/a~1b/00", True), ("/nested/a~1b/-", True), ("/missing", None)]
        for pointer, value in tests:
            with self.subTest(pointer=pointer):
                criterion = self.ready() | {"pointer": pointer, "value": value}
                self.assertFalse(self.write_policy([criterion]).evaluate()["task_fulfilled"])

    def test_invalid_json_is_unknown_not_empty_or_passed(self):
        for data in ('{"ready":false,"ready":true}', '{"ready":NaN}', '[', ''):
            with self.subTest(data=data):
                self.source.write_text(data)
                report = self.write_policy([self.ready()]).evaluate()
                self.assertEqual(report["criteria"][0]["status"], "unknown")
                self.assertIsNone(report["task_fulfilled"])

    def test_malformed_policy_and_executable_criteria_fail_closed(self):
        for criterion in (self.ready() | {"argv": ["sh", "-c", "touch marker"]},
                          {"id": "code", "type": "python", "code": "exec('')"},
                          self.ready() | {"pointer": "/invalid~2"}, self.ready() | {"type": []}):
            with self.subTest(criterion=criterion):
                report = self.write_policy([criterion]).evaluate()
                self.assertIsNone(report["task_fulfilled"])
                self.assertEqual(report["criteria_status"], "invalid")
        self.assertFalse((self.workspace / "marker").exists())
        self.policy.write_text('{"version":1,"version":1}')
        self.assertEqual(AcceptanceSuite(self.workspace).evaluate()["criteria_status"], "invalid")

    def test_empty_duplicate_and_excessive_criteria_are_not_vacuously_true(self):
        for criteria in ([], [self.ready(), self.ready()], [self.ready() | {"id": f"check{i}"} for i in range(65)]):
            with self.subTest(count=len(criteria)):
                self.assertIsNone(self.write_policy(criteria).evaluate()["task_fulfilled"])

    def test_policy_cannot_be_added_or_rewritten_mid_task(self):
        absent = AcceptanceSuite(self.workspace)
        self.write_policy([self.ready(False)])
        self.assertEqual(absent.evaluate()["criteria_status"], "invalid")
        frozen = self.write_policy([self.ready(True)])
        self.write_policy([self.ready(False)])
        report = frozen.evaluate()
        self.assertFalse(report["policy_unchanged"])
        self.assertIsNone(report["task_fulfilled"])

    def test_outside_traversal_symlinks_and_secrets_are_not_read(self):
        outside = self.root / "outside.json"
        outside.write_text('{"ready":true}')
        (self.workspace / "link.json").symlink_to(outside)
        (self.workspace / "linkdir").symlink_to(self.root, target_is_directory=True)
        (self.workspace / "broken.json").symlink_to(self.root / "missing")
        for path in (str(outside), "../outside.json", "link.json", "linkdir/outside.json", ".env", ".ssh/id_rsa", "credentials.json", "server.key", "broken.json"):
            with self.subTest(path=path):
                suite = self.write_policy([self.ready() | {"path": path}])
                self.assertIsNone(suite.evaluate()["task_fulfilled"])
        report = self.write_policy([{"id": "absent", "type": "file_absent", "path": "broken.json"}]).evaluate()
        self.assertEqual(report["criteria"][0]["status"], "unknown")
        for pointer in ("/password", "/api_key", "/config/access_token"):
            self.assertIsNone(self.write_policy([self.ready() | {"pointer": pointer}]).evaluate()["task_fulfilled"])

    def test_special_files_rejected_without_blocking(self):
        fifo = self.workspace / "pipe"
        os.mkfifo(fifo)
        suite = self.write_policy([{"id": "exists", "type": "file_exists", "path": "pipe"}])
        self.assertEqual(suite.evaluate()["criteria"][0]["status"], "unknown")

    def test_large_file_existence_needs_no_content_but_hash_is_bounded(self):
        self.source.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
        exists = self.write_policy([{"id": "exists", "type": "file_exists", "path": "result.json"}]).evaluate()
        self.assertTrue(exists["task_fulfilled"])
        limited = self.write_policy([self.ready()]).evaluate()
        self.assertIsNone(limited["task_fulfilled"])
        self.assertEqual(limited["criteria"][0]["status"], "unknown")

    def test_final_sweep_detects_cross_criterion_mutation(self):
        suite = self.write_policy([self.ready(False)])
        original = suite._evaluate_one

        def mutate(*args):
            result = original(*args)
            self.source.write_text('{"ready":true}')
            return result

        with patch.object(suite, "_evaluate_one", side_effect=mutate):
            report = suite.evaluate()
        self.assertIsNone(report["task_fulfilled"])
        self.assertEqual(report["criteria_status"], "invalid")
        self.assertIn("concurrente", report["errors"][0])

    def test_file_appearing_after_absence_observation_invalidates(self):
        suite = self.write_policy([{"id": "absent", "type": "file_absent", "path": "new.json"}])
        original = suite._evaluate_one

        def create(*args):
            result = original(*args)
            (self.workspace / "new.json").write_text('{}')
            return result

        with patch.object(suite, "_evaluate_one", side_effect=create):
            report = suite.evaluate()
        self.assertIsNone(report["task_fulfilled"])

    def test_receipts_are_current_task_exact_command_and_stable_files(self):
        suite = self.write_policy([{"id": "tests", "type": "configured_check", "check": "Behavior"}])
        for changes, status in (
            ({"timestamp": (suite.started_at - timedelta(seconds=1)).isoformat()}, "stale"),
            ({"timestamp": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}, "stale"),
            ({"timestamp": "not a timestamp"}, "unverified"),
            ({"phase": "before"}, "stale"),
            ({"environment_changed": True}, "stale"),
            ({"changed_during_check": True}, "stale"),
            ({"argv": ["echo", "PASS"]}, "unverified"),
            ({"cwd": str(self.root)}, "unverified"),
            ({"fingerprints": {}}, "unverified"),
            ({"timed_out": True}, "unknown"),
            ({"exit_code": None}, "unknown"),
            ({"exit_code": 1, "passed": False}, "failed"),
        ):
            with self.subTest(changes=changes):
                report = suite.evaluate(check_receipts=[self.receipt(**changes)])
                self.assertEqual(report["criteria"][0]["status"], status)
                self.assertIsNot(report["task_fulfilled"], True)
        old = self.receipt()
        self.source.write_text('{"ready":true}')
        report = suite.evaluate(check_receipts=[old])
        self.assertEqual(report["criteria"][0]["status"], "stale")
        self.assertTrue(suite.evaluate(check_receipts=[self.receipt()])["task_fulfilled"])

    def test_receipt_requires_policy_evidence_and_rejects_external_paths(self):
        suite = self.write_policy([{"id": "tests", "type": "configured_check", "check": "Behavior"}])
        receipt = self.receipt()
        del receipt["fingerprints"][str(self.policy)]
        self.assertEqual(suite.evaluate(check_receipts=[receipt])["criteria"][0]["status"], "unverified")
        receipt = self.receipt()
        receipt["fingerprints"][str(self.root / "outside")] = None
        self.assertEqual(suite.evaluate(check_receipts=[receipt])["criteria"][0]["status"], "unknown")

    def test_missing_check_is_not_a_pass(self):
        suite = self.write_policy([{"id": "tests", "type": "configured_check", "check": "Behavior"}])
        report = suite.evaluate()
        self.assertEqual(report["test_status"], "not_run")
        self.assertEqual(report["criteria"][0]["status"], "not_run")
        self.assertIsNone(report["task_fulfilled"])

    def test_changed_test_cannot_become_its_own_independent_oracle(self):
        suite = self.write_policy([{"id": "tests", "type": "configured_check", "check": "Behavior"}])
        self.check.write_text("print('everything passes')\n")
        # Even a fresh current receipt is insufficient if the model rewrote the
        # reference test before ChangeGuard first observed it.
        report = suite.evaluate(check_receipts=[self.receipt()])
        self.assertEqual(report["criteria"][0]["status"], "stale")
        self.assertIsNone(report["task_fulfilled"])

    def test_protected_glob_membership_is_frozen_at_task_start(self):
        tests = self.workspace / "tests"
        tests.mkdir()
        (tests / "first.py").write_text("assert True\n")
        self.definitions[0]["protect"].append("tests/**/*.py")
        suite = self.write_policy([{"id": "tests", "type": "configured_check", "check": "Behavior"}])
        self.assertIsNone(suite.describe()["error"])
        (tests / "new.py").write_text("assert True\n")
        report = suite.evaluate(check_receipts=[self.receipt()])
        self.assertEqual(report["criteria"][0]["status"], "stale")

    def test_missing_protected_test_in_receipt_cannot_pass(self):
        suite = self.write_policy([{"id": "tests", "type": "configured_check", "check": "Behavior"}])
        receipt = self.receipt()
        del receipt["fingerprints"][str(self.check)]
        self.assertEqual(suite.evaluate(check_receipts=[receipt])["criteria"][0]["status"], "unverified")

    def test_new_glob_member_during_evaluation_invalidates_receipt(self):
        tests = self.workspace / "tests"
        tests.mkdir()
        original_test = tests / "first.py"
        original_test.write_text("assert True\n")
        self.definitions[0]["protect"].append("tests/*.py")
        suite = self.write_policy([{"id": "tests", "type": "configured_check", "check": "Behavior"}])
        receipt = self.receipt()
        receipt["fingerprints"][str(original_test)] = hashlib.sha256(original_test.read_bytes()).hexdigest()
        original = suite._evaluate_one

        def add_test(*args):
            result = original(*args)
            (tests / "new.py").write_text("assert True\n")
            return result

        with patch.object(suite, "_evaluate_one", side_effect=add_test):
            report = suite.evaluate(check_receipts=[receipt])
        self.assertIsNone(report["task_fulfilled"])
        self.assertEqual(report["criteria_status"], "invalid")

    def test_latest_failed_receipt_supersedes_old_success(self):
        suite = self.write_policy([{"id": "tests", "type": "configured_check", "check": "Behavior"}])
        report = suite.evaluate(check_receipts=[self.receipt(), self.receipt(exit_code=1, passed=False)])
        self.assertFalse(report["task_fulfilled"])
        self.assertEqual(report["test_status"], "not_passed")

    def test_metadata_and_reports_do_not_disclose_file_contents_or_expectations(self):
        private_value = "a-project-value-which-must-not-be-echoed"
        self.source.write_text(private_value)
        suite = self.write_policy([{"id": "text", "type": "text_contains", "path": "result.json", "text": private_value}])
        serialized = json.dumps({"describe": suite.describe(), "report": suite.evaluate()})
        self.assertNotIn(private_value, serialized)
        self.assertTrue(suite.evaluate()["task_fulfilled"])

    def test_mutating_returned_description_does_not_change_contract(self):
        suite = self.write_policy([self.ready()])
        description = suite.describe()
        description["criteria"].clear()
        self.assertFalse(suite.evaluate()["task_fulfilled"])

    def test_nested_policy_paths_stay_in_workspace(self):
        sub = self.workspace / "component"
        sub.mkdir()
        (sub / "value.txt").write_text("ok")
        policy = sub / ".ovh-agent-checks.json"
        policy.write_text(json.dumps({"version": 1, "checks": [], "acceptance": {"version": 1,
             "criteria": [{"id": "exists", "type": "file_exists", "path": "value.txt"}]}}))
        report = AcceptanceSuite(self.workspace, policy).evaluate()
        self.assertTrue(report["task_fulfilled"])
        self.assertIsNone(AcceptanceSuite(self.workspace, self.root / "outside.json").evaluate()["task_fulfilled"])

    def test_controller_receipt_integration_with_change_guard(self):
        # Fixture runner avoids subprocesses. This tests receipt wiring, not the
        # assertions in check.py: that distinction must remain explicit.
        suite = self.write_policy([self.ready(), {"id": "checks", "type": "configured_check", "check": "Behavior"}])
        guard = ChangeGuard(self.workspace, self.root / "journal", runner=lambda *_args:
                            {"exit_code": 0, "passed": True, "output": "", "duration_ms": 0})
        before = self.source.read_text()
        guard.write(self.source, before, '{"ready":true}')
        guard.verify()
        report = suite.evaluate(check_receipts=guard.state["receipts"], command_results=[{"exit_code": 0}])
        self.assertTrue(report["task_fulfilled"])
        self.assertEqual(report["test_status"], "passed")
        self.source.write_text('{"ready":false}')
        stale = suite.evaluate(check_receipts=guard.state["receipts"])
        self.assertIsNot(stale["task_fulfilled"], True)


if __name__ == "__main__":
    unittest.main()
