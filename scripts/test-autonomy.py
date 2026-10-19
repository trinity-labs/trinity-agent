"""Regression checks for evaluator independence, not mirrored engine internals."""
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location("autonomy_evaluator",Path(__file__).with_name("evaluate-autonomy.py"))
evaluation=importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class AutonomyEvaluation(unittest.TestCase):
    def test_seeded_reference_suite_and_offline_measurement_scope(self):
        report=evaluation.run_evaluation(seed=271828)
        self.assertTrue(report["passed"],[(row["id"],row) for row in report["scenarios"] if not row["passed"]])
        self.assertEqual(report["metrics"]["model_calls"],0)
        self.assertFalse(report["model_performance_measured"])
        self.assertEqual(report["metrics"]["prevented_duplicate_mutations"],1)
        self.assertEqual(report["metrics"]["task_success_without_human_intervention"]["passed"],4)

    def test_runner_success_prose_cannot_change_independent_result(self):
        report=evaluation.run_evaluation(seed=314159,runner=lambda request,tools:{"claimed_success":True,"tests":"all pass"})
        tasks=[row for row in report["scenarios"] if row["kind"] == "task"]
        self.assertFalse(report["passed"])
        self.assertTrue(all(not row["oracle_satisfied"] and not row["controller_accepted"] for row in tasks))
        self.assertEqual(report["metrics"]["false_success_claims"],4)
        self.assertIsNone(report["metrics"]["model_cost_eur"])

    def test_partial_multifile_edit_cannot_pass_as_complete(self):
        def partial(request,tools):
            if len(request.get("paths",[]))>1:
                request=dict(request,paths=request["paths"][:1])
            return evaluation.reference_runner(request,tools)
        report=evaluation.run_evaluation(seed=161803,runner=partial)
        result=next(row for row in report["scenarios"] if row["id"] == "multi_file")
        self.assertFalse(result["oracle_satisfied"])
        self.assertTrue(result["false_success_claim"])


if __name__ == "__main__":
    unittest.main()
