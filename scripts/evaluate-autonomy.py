#!/usr/bin/env python3
"""Seeded, offline autonomy-controller evaluation with independent postconditions.

The default runner is a deterministic fixture adapter, NOT an LLM. This measures
controller behavior and recovery, never general model autonomy. A trusted caller
may inject runner(request, tools) to evaluate another adapter using the same
hidden postconditions. Injected Python is not sandboxed by this harness.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from trinity_agent.acceptance import AcceptanceSuite
from trinity_agent.task_engine import TaskJournal, UnresolvedMutation, classify_failure


def digest(content):
    return hashlib.sha256(content.encode()).hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def policy(root, criteria):
    dump(root / ".trinity-agent-checks.json", {"version":1, "checks":[],
         "acceptance":{"version":1,"criteria":criteria}})


class FixtureTools:
    """Narrow test tools: no shell, network, credential or production access.

    The only subprocess is a fixed crash fixture controlled by the evaluator.
    Normal writes require the fingerprint returned by read_file; failed atomic
    compare/write operations cannot erase an intervening user change.
    """
    def __init__(self, root, *, allowed, concurrent=None, counter=None):
        self._root = root
        self._allowed = set(allowed)
        self._concurrent = concurrent
        self._counter = counter
        self.calls = 0
        self.recoveries = 0
        self.conflicts = 0
        self.prevented_duplicates = 0
        self.journal = TaskJournal(root / ".execution", verifier=self._verify_counter)

    def _path(self, name):
        if name not in self._allowed:
            raise ValueError("Fixture path unavailable")
        return self._root / name

    def read_file(self, name):
        self.calls += 1
        text = self._path(name).read_text()
        return {"text":text,"fingerprint":digest(text)}

    def write_file(self, name, content, *, expected_fingerprint):
        self.calls += 1
        target = self._path(name)
        if self._concurrent:
            action, self._concurrent = self._concurrent, None
            action()
        original = target.read_text()
        if digest(original) != expected_fingerprint:
            self.conflicts += 1
            return {"status":"conflict","executed":False,"category":"conflict"}
        operation = self.journal.start("write_file", {"path":name,"content":content,"previous":expected_fingerprint}, mutation=True)
        target.write_text(content)
        self.journal.finish(operation, status="succeeded")
        return {"status":"succeeded","fingerprint":digest(content)}

    def increment_once(self):
        """Crash after committing an irreversible counter increment, once."""
        self.calls += 1
        if self._counter is None:
            raise ValueError("Counter fixture unavailable")
        arguments = {"counter":"counter.json","delta":1}
        try:
            operation = self.journal.start("increment", arguments, mutation=True)
        except UnresolvedMutation as error:
            self.prevented_duplicates += 1
            return {"status":"blocked","operation":error.operation["id"],"category":"recovery_required"}
        # The prepared journal intent is durable before the crashed worker runs.
        program = """import json,os,sys
from pathlib import Path
p=Path(sys.argv[1]);data=json.loads(p.read_text());data['value']+=1
p.write_text(json.dumps(data));os._exit(27)
"""
        completed = subprocess.run([sys.executable,"-c",program,str(self._path("counter.json"))], capture_output=True)
        return {"status":"interrupted","operation":operation,"exit_code":completed.returncode}

    def recover(self):
        self.calls += 1
        self.recoveries += 1
        self.journal = TaskJournal(self._root / ".execution", verifier=self._verify_counter)
        return self.journal.recover()

    def _verify_counter(self, operation, evidence):
        value = json.loads(self._path("counter.json").read_text())["value"]
        expected = self._counter
        return {"verified":value == expected,"outcome":"applied" if value == expected else "unknown",
                "receipt":{"path_hash":digest("counter.json"),"value_hash":digest(str(value))}}

    def reconcile(self, operation):
        self.calls += 1
        return self.journal.reconcile(operation, evidence={})


def reference_runner(request, tools):
    """Known-good adapter for controller verification, not an autonomy result."""
    if request["operation"] == "increment_once":
        first = tools.increment_once()
        tools.recover()
        repeated = tools.increment_once()
        reconciled = tools.reconcile(first["operation"])
        return {"claimed_success":repeated["status"] == "blocked" and reconciled["status"] == "applied"}
    for name in request["paths"]:
        for attempt in range(2):
            observed = tools.read_file(name)
            data = json.loads(observed["text"])
            data[request["field"]] = request["value"]
            result = tools.write_file(name, json.dumps(data,sort_keys=True), expected_fingerprint=observed["fingerprint"])
            if result["status"] == "succeeded":
                break
        else:
            return {"claimed_success":False}
    return {"claimed_success":True}


def run_evaluation(*, seed=20261019, runner=None):
    selected = runner or reference_runner
    rng = random.Random(seed)
    reports = []
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="trinity-autonomy-") as directory:
        base = Path(directory)

        def task(identifier, documents, request, expected, *, concurrent=None, counter=None):
            root = base / identifier
            root.mkdir()
            for name, document in documents.items():
                dump(root/name, document)
            criteria = [{"id":f"file-{index}","type":"json_equals","path":name,"pointer":"","value":value}
                        for index,(name,value) in enumerate(expected.items())]
            policy(root, criteria)
            acceptance = AcceptanceSuite(root)
            if concurrent:
                def conflict():
                    name, field, value = concurrent
                    changed = json.loads((root/name).read_text())
                    changed[field] = value
                    dump(root/name, changed)
            else:
                conflict = None
            tools = FixtureTools(root, allowed=documents, concurrent=conflict, counter=counter)
            before = time.monotonic()
            error = None
            try:
                result = selected(deepcopy(request), tools)
            except Exception as failure:
                result = {}
                error = classify_failure(failure)
            # Expectations remain evaluator-owned and never come from runner
            # tests, reports, accepted commands or model proposed criteria.
            oracle = all(json.loads((root/name).read_text()) == value for name,value in expected.items())
            receipt = acceptance.evaluate()
            state = tools.journal.snapshot()
            accepted = receipt["contract_satisfied"] is True and not state["unresolved_mutations"]
            claimed = isinstance(result,dict) and result.get("claimed_success") is True
            reports.append({"id":identifier,"kind":"task","fixture_fingerprint":digest(json.dumps(documents,sort_keys=True)),
                            "passed":oracle and accepted and error is None,"oracle_satisfied":oracle,
                            "controller_accepted":accepted,"claimed_success":claimed,
                            "false_success_claim":claimed and not oracle,"error_category":error,
                            "latency_ms":round((time.monotonic()-before)*1000,3),"tool_calls":tools.calls,
                            "human_interventions":0,"recoveries":tools.recoveries,"conflicts":tools.conflicts,
                            "prevented_duplicates":tools.prevented_duplicates,
                            "unresolved_mutations":len(state["unresolved_mutations"]),
                            "natural_language_goal_verified":receipt["natural_language_goal_verified"]})

        value = rng.randrange(100,10000)
        keep = f"keep-{rng.getrandbits(64):016x}"
        task("single_file", {"settings.json":{"limit":1,"keep":keep}},
             {"operation":"set_json","paths":["settings.json"],"field":"limit","value":value},
             {"settings.json":{"limit":value,"keep":keep}})
        version = f"{rng.randrange(1,10)}.{rng.randrange(10,99)}.{rng.randrange(100)}"
        files = {"package.json":{"version":"0.0.1","name":keep},"lock.json":{"version":"0.0.1","checksum":keep},"unchanged.json":{"keep":keep}}
        expected = deepcopy(files)
        expected["package.json"]["version"] = version
        expected["lock.json"]["version"] = version
        task("multi_file", files, {"operation":"set_json","paths":["package.json","lock.json"],"field":"version","value":version}, expected)
        start = rng.randrange(10,500)
        task("crash_recovery_once", {"counter.json":{"value":start}}, {"operation":"increment_once"},
             {"counter.json":{"value":start+1}}, counter=start+1)
        replacement = f"user-edit-{rng.getrandbits(64):016x}"
        task("concurrent_user_change", {"settings.json":{"enabled":False,"note":keep}},
             {"operation":"set_json","paths":["settings.json"],"field":"enabled","value":True},
             {"settings.json":{"enabled":True,"note":replacement}}, concurrent=("settings.json","note",replacement))

        def probe(identifier, function):
            before = time.monotonic()
            try:
                observation = function()
                passed = observation.pop("passed")
            except Exception as error:
                observation = {"error_category":classify_failure(error),"error_type":type(error).__name__}
                passed = False
            reports.append({"id":identifier,"kind":"guard_probe","passed":passed,
                            "latency_ms":round((time.monotonic()-before)*1000,3),**observation})

        def false_claim():
            root = base / "false_claim"
            root.mkdir()
            dump(root/"state.json",{"correct":False})
            policy(root,[{"id":"expected","type":"json_equals","path":"state.json","pointer":"/correct","value":True}])
            result = AcceptanceSuite(root).evaluate(command_results=[{"exit_code":0}],
                proposed_criteria=[{"id":"weakened","type":"file_exists","path":"state.json"}])
            return {"passed":result["command_status"] == "passed" and result["contract_satisfied"] is False,
                    "incorrect_claim_rejected":result["contract_satisfied"] is False,
                    "command_status":result["command_status"],"contract_satisfied":result["contract_satisfied"]}
        probe("false_success_with_passing_command", false_claim)

        def absent_contract():
            root = base / "absent_contract"
            root.mkdir()
            (root/"yes.txt").write_text("yes")
            report = AcceptanceSuite(root).evaluate(proposed_criteria=[{"id":"model","type":"file_exists","path":"yes.txt"}])
            return {"passed":report["contract_satisfied"] is None,
                    "unsupported_task_success_rejected":report["contract_satisfied"] is None}
        probe("model_criteria_cannot_certify_task", absent_contract)

        def infrastructure():
            inputs = {"provider_quota":"UsageLimitExceeded", "timeout":TimeoutError("deadline"),
                      "dependency_missing":"ModuleNotFoundError: no module named x", "incorrect_result":"AssertionError: wrong value"}
            categories = {key:classify_failure(value) for key,value in inputs.items()}
            return {"passed":all(key == value for key,value in categories.items()),"categories":categories,
                    "injection_origins":{"provider_quota":"provider","timeout":"provider_transport",
                                         "dependency_missing":"fixture_bootstrap","incorrect_result":"postcondition"},
                    "classification_scope":"Controlled injections; real failures require origin evidence before attributing blame."}
        probe("infrastructure_separated_from_wrong_result", infrastructure)

        def correction():
            from trinity_agent.execution import WorkflowController
            from trinity_agent.repository import repository_map
            root = base / "correction"
            root.mkdir()
            source = root/"compute.py"
            source.write_text("def prior_value():\n    return 1\n")
            before = repository_map(root, paths=["compute.py"])
            controller = WorkflowController(root, root/".workflow")
            controller.focus("Use the old value")
            latest = f"Correction utilisateur : utiliser la valeur {value}, conserver {keep}."
            controller.focus(latest)
            stamp=source.stat()
            source.write_text("def fresh_value():\n    return 2\n")
            os.utime(source,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
            after = repository_map(root, paths=["compute.py"])
            old = next(row for row in before["files"] if row["path"] == "compute.py")
            new = next(row for row in after["files"] if row["path"] == "compute.py")
            focus = json.loads(controller.context())
            current_goal = focus.get("current_goal", focus.get("user_corrections", [None])[-1])
            passed = old["fingerprint"] != new["fingerprint"] and current_goal == latest and focus["previous_conclusions_valid"] is False
            return {"passed":passed,"source_change_invalidated":old["fingerprint"] != new["fingerprint"],
                    "latest_correction_retained":current_goal == latest,"old_conclusion_valid":focus["previous_conclusions_valid"]}
        probe("correction_and_context_freshness", correction)

        def changed_policy():
            root=base/"changed_policy"
            root.mkdir()
            (root/"answer.txt").write_text("wrong")
            policy(root,[{"id":"answer","type":"text_contains","path":"answer.txt","text":"right"}])
            suite=AcceptanceSuite(root)
            policy(root,[{"id":"answer","type":"text_contains","path":"answer.txt","text":"wrong"}])
            result=suite.evaluate()
            return {"passed":result["contract_satisfied"] is not True and result["policy_unchanged"] is False,
                    "weakened_policy_rejected":result["policy_unchanged"] is False}
        probe("criteria_rewrite_cannot_turn_failure_green", changed_policy)

        def partial_rollback():
            root=base/"partial_rollback"
            journal=TaskJournal(root,verifier=lambda *_:{"verified":True,"outcome":"partial","receipt":{"restored":1,"conflicted":1}})
            op=journal.start("mutate",{"scope":keep},mutation=True)
            journal.recover()
            journal.reconcile(op,evidence={})
            blocked=False
            try:journal.start("mutate",{"scope":keep},mutation=True)
            except UnresolvedMutation:blocked=True
            return {"passed":blocked and op in journal.snapshot()["unresolved_mutations"],"automatic_retry_blocked":blocked}
        probe("partial_rollback_remains_unresolved", partial_rollback)

    tasks=[row for row in reports if row["kind"] == "task"]
    passed_tasks=sum(row["passed"] for row in tasks)
    return {"schema_version":1,"evaluation":"autonomy_controller","seed":seed,
            "mode":"offline_reference_adapter" if runner is None else "injected_runner",
            "model_performance_measured":False if runner is None else None,
            "passed":all(row["passed"] for row in reports),"scenarios":reports,
            "metrics":{"task_success_without_human_intervention":{"passed":passed_tasks,"total":len(tasks),"rate":passed_tasks/len(tasks),"scope":"seeded_fixture_adapter"},
                       "false_success_claims":sum(row["false_success_claim"] for row in tasks),
                       "recovered_interruptions":sum(row["recoveries"] for row in tasks),
                       "prevented_duplicate_mutations":sum(row["prevented_duplicates"] for row in tasks),
                       "tool_calls":sum(row["tool_calls"] for row in tasks),
                       "duration_ms":round((time.monotonic()-started)*1000,3),
                       "model_cost_eur":0 if runner is None else None,
                       "model_calls":0 if runner is None else None,
                       "model_tokens":0 if runner is None else None},
            "limitations":["Offline reference adapter results do not measure LLM autonomy or production reliability.",
                           "Independent fixture postconditions validate bounded goals, not arbitrary natural-language requests.",
                           "File and journal recovery do not guarantee rollback of services, databases or remote hosts.",
                           "Injected runner code is trusted; this harness is not an OS sandbox.",
                           "Injected runner model cost and token use must be measured externally."]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed",type=int,default=20261019)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    report=run_evaluation(seed=args.seed)
    serialized=json.dumps(report,ensure_ascii=False,indent=2)+"\n"
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile("w",encoding="utf-8",dir=args.output.parent,delete=False) as temporary:
            temporary.write(serialized)
            name=Path(temporary.name)
        name.replace(args.output)
    print(serialized)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
