import unittest
from pathlib import Path
import sys

from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trinity_agent.precision import (PrecisionReviewError, PrecisionVerdict,
    enforce_verdict, evidence_packet, review_required)


class PrecisionTests(unittest.TestCase):
    def test_review_is_adaptive(self):
        simple = {'domain': 'ui', 'complexity': 'simple', 'scope': 'targeted'}
        self.assertFalse(review_required(simple, {'changes': ['a.css'], 'checks': [{'passed': True}]}))
        self.assertTrue(review_required({**simple, 'complexity': 'complex'},
                                        {'changes': ['a.py'], 'checks': [{'passed': True}]}))
        self.assertTrue(review_required({'domain': 'security'}, {'changes': [], 'checks': []}))

    def test_verdict_contract_rejects_unsubstantiated_revision(self):
        with self.assertRaises(ValidationError):
            PrecisionVerdict(verdict='revise', confidence=.8, findings=[])
        with self.assertRaises(ValidationError):
            PrecisionVerdict(verdict='pass', confidence=.8, findings=[{
                'severity': 'blocking', 'claim': 'x', 'evidence': 'y', 'evidence_ref': 'verification'}])

    def test_blocking_finding_stops_completion(self):
        verdict = PrecisionVerdict(verdict='revise', confidence=.9, findings=[{
            'severity': 'blocking', 'claim': 'Le fichier demandé manque',
            'evidence': 'La liste des changements ne le contient pas',
            'evidence_ref': 'verification', 'correction': 'Modifier ce fichier'}])
        with self.assertRaises(PrecisionReviewError):
            enforce_verdict(verdict, {'status': 'passed'})

    def test_model_cannot_override_failed_checks(self):
        verdict = PrecisionVerdict(verdict='pass', confidence=.9)
        with self.assertRaises(RuntimeError):
            enforce_verdict(verdict, {'status': 'failed'})

    def test_packet_is_bounded_and_uses_named_receipts(self):
        packet = evidence_packet('p' * 9000, 'o' * 13000, {}, {
            'status': 'passed', 'changes': ['x'], 'checks': [{'check': 'unit', 'passed': True}]})
        self.assertLess(len(packet), 23000)
        self.assertIn('check:0', packet)
        self.assertNotIn('p' * 8001, packet)


if __name__ == '__main__':
    unittest.main()
