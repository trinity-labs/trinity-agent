#!/usr/bin/env python3
"""Optional official GEPA optimization over TRINITY's independent fixture oracle.

No production prompt is written. Only an <=1200-character prior assistant
suggestion varies. User requests, tool permissions and postconditions stay fixed.
Offline mode exercises the integration with scripted models, not model quality.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('gepa_evaluator', ROOT/'scripts/evaluate-agent.py')
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)

TRAIN = (('multi_file', 101), ('source_injection', 102))
VALIDATION = (('stale_path_recovery', 201),)
HELD_OUT = (('preexisting_change', 301), ('correction_compaction', 302), ('interrupted_write', 303))
SEED = {'advice': ''}


class ExperimentLimit(RuntimeError):
    pass


def advice(candidate):
    if (not isinstance(candidate, dict) or set(candidate) != {'advice'}
            or not isinstance(candidate['advice'], str) or len(candidate['advice']) > 1200):
        raise ValueError('Only one technical suggestion of at most 1200 characters may vary')
    return candidate['advice']


def check_splits(train, validation, held_out):
    groups = [set(group) for group in (train, validation, held_out)]
    names = [{item[0] for item in group} for group in groups]
    seeds = [{item[1] for item in group} for group in groups]
    for index, group in enumerate(groups):
        if not group or len(group) != len((train, validation, held_out)[index]):
            raise ValueError('Empty or duplicated split')
        if any(name not in evaluation.TASK_CASES for name, _ in group):
            raise ValueError('Unknown fixture case')
        for previous in range(index):
            if names[index] & names[previous] or seeds[index] & seeds[previous]:
                raise ValueError('Scenarios and seeds must be disjoint across splits')


def shared_meter(model, *, request_limit, token_limit=300000):
    from pydantic_ai.models.wrapper import WrapperModel
    class Meter(WrapperModel):
        def __init__(self):
            super().__init__(model)
            self.requests, self.tokens, self.records = 0, 0, []

        async def request(self, messages, settings, parameters):
            if self.requests >= request_limit or self.tokens >= token_limit:
                raise ExperimentLimit('Shared optimization/reflection/holdout budget reached')
            self.requests += 1
            started = time.monotonic()
            record = {}
            try:
                result = await self.wrapped.request(messages, settings, parameters)
                self.tokens += result.usage.input_tokens+result.usage.output_tokens
                record.update(input_tokens=result.usage.input_tokens, output_tokens=result.usage.output_tokens)
                return result
            except Exception as error:
                record['error'] = type(error).__name__
                raise
            finally:
                record['latency_ms'] = round((time.monotonic()-started)*1000, 2)
                self.records.append(record)
    return Meter()


class TrinityAdapter:
    # GEPA 0.1.4 reads this optional protocol member directly.
    propose_new_texts = None

    def __init__(self, backend, base, *, model=None, model_id=None, batch_type=None):
        self.backend, self.base, self.model, self.model_id = backend, base, model, model_id
        self.batch_type, self.rows, self.sequence = batch_type, [], 0
        self.on_row = None

    def evaluate(self, batch, candidate, capture_traces=False):
        tip = advice(candidate)
        rows = []
        for name, seed in batch:
            if (name, seed) not in TRAIN+VALIDATION:
                raise ValueError('Final comparison cases cannot be passed to the optimizer')
            rows.append(self.case(name, seed, tip, phase='optimization'))
        if self.batch_type is None:
            from gepa.core.adapter import EvaluationBatch
            batch_type = EvaluationBatch
        else:
            batch_type = self.batch_type
        return batch_type(outputs=rows, scores=[float(row['passed']) for row in rows],
                          trajectories=rows if capture_traces else None)

    def case(self, name, seed, tip, *, phase):
        self.sequence += 1
        row = evaluation.run_case(self.backend, self.base/str(self.sequence), name, seed=seed,
            model=self.model, model_id=self.model_id, technical_advice=tip, max_output_tokens=4096)
        row.update(phase=phase, seed=seed, candidate_fingerprint=evaluation.sha(tip))
        self.rows.append(row)
        if self.on_row:
            self.on_row(row)
        # Infrastructure and shared-budget failures are not candidate fitness.
        if row['error'] and (row['error']['origin'] in {'infrastructure', 'harness', 'budget'}
                             or row['error']['type'] == 'ExperimentLimit'):
            raise ExperimentLimit('Evaluation interrupted: '+row['error']['type'])
        return row

    def make_reflective_dataset(self, candidate, eval_batch, components_to_update):
        if components_to_update != ['advice']:
            raise ValueError('Immutable components cannot be optimized')
        # No held-out cases, oracle bytes, user secrets, or production archives.
        # Relevant failed tool receipts are retained with bounded text.
        return {'advice': [{'case': row['id'], 'passed': row['passed'], 'error': row['error'],
            'oracle': row['oracle'], 'controller_accepted': row['controller_accepted'],
            'answer': row['answer'][:1200], 'events': row['events'][-6:]}
            for row in eval_batch.trajectories or []]}


def run_experiment(*, output, real_model=None, max_cost_eur=.25, request_limit=120):
    output = Path(output)
    if output.exists():
        raise FileExistsError('Use a new output path to preserve evidence')
    if not 0 < max_cost_eur <= 1 or not 1 <= request_limit <= 300:
        raise ValueError('Invalid experiment limits')
    check_splits(TRAIN, VALIDATION, HELD_OUT)
    try:
        import gepa
    except ImportError as error:
        raise RuntimeError('Optional lab dependency missing: pip install -r scripts/requirements-research.txt') from error
    report = {'experiment': 'gepa', 'library_version': importlib.metadata.version('gepa'),
        'upstream': 'https://github.com/gepa-ai/gepa', 'model': real_model,
        'model_performance_measured': bool(real_model), 'complete': False,
        'automatic_production_activation': False, 'source_fingerprint': evaluation.source_fingerprint(),
        'experiment_fingerprint': evaluation.sha(Path(__file__).read_bytes()),
        'splits': {'train': TRAIN, 'selection_validation': VALIDATION, 'held_out': HELD_OUT},
        'limits': {'requests': request_limit, 'tokens': 300000, 'cost_eur': max_cost_eur,
                   'advice_characters': 1200, 'metric_calls': 5},
        'optimization_rows': [], 'reflection_calls': [], 'proposals': [], 'held_out_comparison': [],
        'limitations': ['Offline scripted rollouts do not measure model quality.',
            'Disjoint synthetic scenarios are not a sealed external generalization benchmark.',
            'This small run tests integration; it cannot establish general coding autonomy.',
            'Final response usage may cross the token limit; shared cost reservations bound spending.']}
    evaluation.atomic_report(output, report)
    with tempfile.TemporaryDirectory(prefix='trinity-gepa-') as temporary:
        base = Path(temporary)
        environment = {'PYDANTIC_AI_NO_BANNER': '1', 'OVH_AGENT_WORKSPACE': str(base/'bootstrap')}
        if not real_model:
            environment['OVH_AGENT_DATA_ROOT'] = str(base/'offline-data')
        with patch.dict(os.environ, environment):
            backend = evaluation.load_backend()
        model, ledger, adapter = None, None, None
        try:
            if real_model:
                from trinity_agent.budget import BudgetLedger
                from trinity_agent.budget_model import BudgetedModel
                billing = backend.billing_file()
                backend.billing_file = lambda: billing
                account = backend.budget_ledger()
                account.status()
                backend.budget_ledger = lambda: account
                card = next(item for item in backend.fetch_model_catalog() if item['id'] == real_model)
                report['pricing'] = {key: card.get(key) for key in ('currency', 'input_per_million', 'output_per_million', 'pricing_source')}
                ledger = BudgetLedger(base/'budget.json', {'monthly': max_cost_eur})
                model = shared_meter(BudgetedModel(backend.get_budgeted_model(real_model), lambda: ledger,
                    lambda _: card, lambda _: None), request_limit=request_limit)
            adapter = TrinityAdapter(backend, base/'cases', model=model, model_id=real_model)
            def save_row(row):
                if row['phase'] == 'optimization':
                    report['optimization_rows'].append(row)
                elif row['error']:
                    report['interrupted_final_row'] = row
                evaluation.atomic_report(output, report)
            adapter.on_row = save_row

            def propose(candidate, dataset, components):
                if components != ['advice']:
                    raise ValueError('Only advice can vary')
                if not real_model:
                    proposed = 'Read current files, preserve unrelated values, make exact edits and verify the configured checks.'
                else:
                    from pydantic_ai import Agent
                    from pydantic_ai.usage import UsageLimits
                    agent = Agent(model, retries=0, instructions=(
                        'Propose only a concise technical tip, at most 1200 characters, to improve fixture task success. '
                        'Diagnose the supplied tool receipts. Never change user requirements, permissions, checks or expected values. '
                        'Do not include benchmark-specific values or advice to weaken verification. Return plain text only.'))
                    prompt = json.dumps({'current_tip': candidate['advice'], 'feedback': dataset}, ensure_ascii=False)
                    started = time.monotonic()
                    result = agent.run_sync(prompt, model_settings={'max_tokens': 2048, 'timeout': 45},
                        usage_limits=UsageLimits(request_limit=1, total_tokens_limit=30000))
                    proposed = result.output.strip()
                    report['reflection_calls'].append({'input_tokens': result.usage.input_tokens,
                        'output_tokens': result.usage.output_tokens,
                        'latency_ms': round((time.monotonic()-started)*1000, 2)})
                proposed_candidate = {'advice': proposed}
                report['proposals'].append({'candidate': proposed_candidate, 'valid': len(proposed) <= 1200})
                evaluation.atomic_report(output, report)
                advice(proposed_candidate)  # Reject, never silently truncate invalid candidates.
                return proposed_candidate

            result = gepa.optimize(seed_candidate=dict(SEED), trainset=list(TRAIN), valset=list(VALIDATION),
                adapter=adapter, custom_candidate_proposer=propose, max_metric_calls=5,
                reflection_minibatch_size=1, skip_perfect_score=False, seed=20261023,
                stop_callbacks=lambda state: model is not None and (model.requests >= request_limit or model.tokens >= 300000),
                use_wandb=False, use_mlflow=False, display_progress_bar=False, raise_on_exception=True)
            selected = result.best_candidate
            tip = advice(selected)
            report.update(selected_candidate=selected, candidates=result.candidates,
                validation_scores=result.val_aggregate_scores, selected_index=result.best_idx)
            # The optimizer has returned and cannot see these cases or results.
            for index, (name, seed) in enumerate(HELD_OUT):
                comparison = {'case': name, 'seed': seed, 'arms': {}}
                report['held_out_comparison'].append(comparison)
                arms = (('reference', ''), ('candidate', tip))
                for label, text in (arms if index % 2 == 0 else reversed(arms)):
                    comparison['arms'][label] = adapter.case(name, seed, text, phase='held_out')
                    evaluation.atomic_report(output, report)
            report['complete'] = True
        except Exception as error:
            report['error'] = {'type': type(error).__name__, 'detail': str(error)[:300]}
        finally:
            if adapter:
                report['optimization_rows'] = [row for row in adapter.rows if row['phase'] == 'optimization']
            if model:
                report['model_requests'] = model.records
                report['requests'] = model.requests
                report['budget_accounted_eur'] = ledger.status()['periods']['monthly']['spent']
            evaluation.atomic_report(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--real-model', help='Explicit OVH opt-in; fixed model for comparable arms')
    parser.add_argument('--max-cost-eur', type=float, default=.25)
    parser.add_argument('--request-limit', type=int, default=120)
    args = parser.parse_args()
    report = run_experiment(**vars(args))
    print(json.dumps({k: v for k, v in report.items() if k not in
        ('optimization_rows', 'held_out_comparison', 'model_requests')}, indent=2))
    return 0 if report['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
