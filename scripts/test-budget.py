#!/usr/bin/env python3
"""Offline checks for exact EUR budget accounting and cross-process limits."""

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
import multiprocessing
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ovh_budget import BudgetExceeded, BudgetLedger, BudgetLedgerError


UTC = timezone.utc
BASE = datetime(2026, 10, 16, 12, 30, tzinfo=UTC)


class Clock:
    def __init__(self, value=BASE):
        self.value = value

    def __call__(self):
        return self.value


def competing_reservation(path):
    ledger = BudgetLedger(Path(path), {"daily": "1"}, now=lambda: BASE)
    try:
        return ledger.reserve("0.2")
    except BudgetExceeded:
        return None


def competing_settlement(args):
    path, token = args
    ledger = BudgetLedger(Path(path), {"daily": "1"}, now=lambda: BASE)
    return ledger.settle(token, "0.2")["periods"]["daily"]["spent"]


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ovh-budget-test-")
        self.path = Path(self.temporary.name) / "budget.json"
        self.clock = Clock()

    def tearDown(self):
        self.temporary.cleanup()

    def ledger(self, limits=None):
        return BudgetLedger(self.path, limits or {"daily": 2, "weekly": 15, "monthly": 60}, now=self.clock)

    def test_new_ledger_periods_and_permissions(self):
        state = self.ledger().status()
        self.assertEqual(state["currency"], "EUR")
        self.assertFalse(state["blocked"])
        self.assertEqual(state["periods"]["daily"]["remaining"], 2)
        self.assertEqual(state["periods"]["daily"]["reset_at"], "2026-10-17T00:00:00Z")
        self.assertEqual(state["periods"]["weekly"]["reset_at"], "2026-10-19T00:00:00Z")
        self.assertEqual(state["periods"]["monthly"]["reset_at"], "2026-11-01T00:00:00Z")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.with_name("budget.json.lock").stat().st_mode), 0o600)

    def test_credit_survives_month_rollover_and_expiry_blocks_paid_calls(self):
        credit = {'starts_at':'2026-10-16T00:00:00Z', 'expires_at':'2026-11-15T20:28:46Z', 'limit_eur':'1'}
        ledger = BudgetLedger(self.path, {}, now=self.clock, credit=credit)
        token = ledger.reserve('.6'); ledger.settle(token, '.6')
        self.clock.value = datetime(2026,11,1,tzinfo=UTC)
        with self.assertRaises(BudgetExceeded): ledger.reserve('.41')
        token = ledger.reserve('.4')
        self.assertEqual(ledger.status()['periods']['credit']['reserved'], .4)
        ledger.settle(token, '.1')
        self.clock.value = datetime(2026,11,15,20,28,46,tzinfo=UTC)
        self.assertTrue(ledger.status()['blocked'])
        with self.assertRaises(BudgetExceeded): ledger.reserve('.0001')
        ledger.settle(ledger.reserve(0), 0)

    def test_local_policy_is_reloaded_and_malformed_policy_fails_closed(self):
        policy = self.path.with_name('policy.json')
        policy.write_text(json.dumps({'schema':1,'limits':{'daily':5}}))
        self.assertEqual(BudgetLedger.from_policy(self.path, {'daily':2}, policy, now=self.clock).status()['periods']['daily']['limit'], 5)
        policy.write_text('{broken')
        with self.assertRaises(BudgetLedgerError): BudgetLedger.from_policy(self.path, {}, policy)
        for credit in ({'starts_at':'2026-10-16T12:00:00Z','expires_at':'2026-11-15T00:00:00Z','limit_eur':1},
                       {'starts_at':'2026-10-16T00:00:00Z','expires_at':'2027-11-15T00:00:00Z','limit_eur':1}):
            with self.assertRaises(BudgetLedgerError): BudgetLedger(self.path, {}, credit=credit)

    def test_exact_decimals_and_idempotent_settlement(self):
        first = self.ledger({"daily": Decimal("0.3")})
        second = self.ledger({"daily": "0.3"})
        one = first.reserve(0.1, {"purpose": "request"})
        two = second.reserve(Decimal("0.2"))
        self.assertEqual(uuid.UUID(one).version, 8)
        self.assertTrue(first.status()["blocked"])
        with self.assertRaises(BudgetExceeded):
            first.reserve("0.000000001")
        first.settle(one, "0.1")
        second.settle(two, "0.2")
        second.settle(one, "999")
        first.settle(two, None)
        state = second.status()
        self.assertEqual(state["periods"]["daily"]["spent"], 0.3)
        self.assertEqual(state["periods"]["daily"]["reserved"], 0)
        self.assertEqual(state["pending_requests"], 0)

    def test_every_period_can_block_a_request(self):
        for period in ("daily", "weekly", "monthly"):
            with self.subTest(period=period):
                path = self.path.with_name(f"{period}.json")
                ledger = BudgetLedger(path, {period: "0.3"}, now=self.clock)
                token = ledger.reserve("0.2")
                ledger.settle(token, "0.2")
                with self.assertRaises(BudgetExceeded) as caught:
                    ledger.reserve("0.100000000000000001")
                self.assertEqual(caught.exception.status["exceeded_periods"], [period])
                self.assertTrue(caught.exception.status["blocked"])
                self.assertIn("€", str(caught.exception))

    def test_failed_request_charges_full_reservation(self):
        ledger = self.ledger({"daily": "1"})
        failed = ledger.reserve("0.9")
        state = ledger.settle(failed, None)
        self.assertEqual(state["periods"]["daily"]["spent"], 0.9)
        self.assertEqual(state["periods"]["daily"]["remaining"], 0.1)
        with self.assertRaises(BudgetExceeded):
            ledger.reserve("0.2")

    def test_actual_can_exceed_estimate_and_blocks_future_calls(self):
        ledger = self.ledger({"daily": "1"})
        token = ledger.reserve("0.1")
        state = ledger.settle(token, "1.01")
        self.assertEqual(state["periods"]["daily"]["spent"], 1.01)
        self.assertEqual(state["periods"]["daily"]["remaining"], 0)
        self.assertTrue(state["blocked"])
        with self.assertRaises(BudgetExceeded):
            ledger.reserve("0.01")
        free = ledger.reserve(0)
        ledger.settle(free, 0)

    def test_zero_means_unlimited_and_free_requests_do_not_consume(self):
        unlimited = self.ledger({"daily": 0, "weekly": 0, "monthly": 0})
        token = unlimited.reserve("1000000.000000001")
        state = unlimited.settle(token, "1000000.000000001")
        self.assertFalse(state["blocked"])
        for period in state["periods"].values():
            self.assertIsNone(period["remaining"])
        free = unlimited.reserve(0)
        unlimited.settle(free, 0)
        self.assertEqual(unlimited.status()["pending_requests"], 0)

    def test_rollover_keeps_open_reservations_and_charges_settlement_date(self):
        self.clock.value = datetime(2026, 5, 31, 23, 59, tzinfo=UTC)
        ledger = self.ledger({"daily": "1", "weekly": "1", "monthly": "1"})
        token = ledger.reserve("1")
        self.clock.value += timedelta(minutes=2)
        state = ledger.status()
        for period in state["periods"].values():
            self.assertEqual(period["spent"], 0)
            self.assertEqual(period["reserved"], 1)
        with self.assertRaises(BudgetExceeded) as caught:
            ledger.reserve("0.01")
        self.assertEqual(caught.exception.status["exceeded_periods"], ["daily", "weekly", "monthly"])
        ledger.settle(token, "0.8")
        data = json.loads(self.path.read_text())
        for counters in data["spent"].values():
            self.assertEqual(counters, {"2026-06-01": "0.8"})
        self.clock.value += timedelta(days=1)
        self.assertEqual(ledger.status()["periods"]["daily"]["spent"], 0)
        self.assertEqual(ledger.status()["periods"]["weekly"]["spent"], 0.8)
        self.clock.value += timedelta(days=6)
        self.assertEqual(ledger.status()["periods"]["weekly"]["spent"], 0)
        self.assertEqual(ledger.status()["periods"]["monthly"]["spent"], 0.8)
        self.clock.value = datetime(2026, 7, 1, tzinfo=UTC)
        self.assertEqual(ledger.status()["periods"]["monthly"]["spent"], 0)

    def test_year_boundary_leap_day_and_timezone(self):
        ledger = self.ledger()
        self.clock.value = datetime(2027, 12, 31, 23, tzinfo=UTC)
        self.assertEqual(ledger.status()["periods"]["monthly"]["reset_at"], "2028-01-01T00:00:00Z")
        self.clock.value = datetime(2028, 2, 29, 23, tzinfo=UTC)
        self.assertEqual(ledger.status()["periods"]["daily"]["reset_at"], "2028-03-01T00:00:00Z")
        self.clock.value = datetime(2026, 10, 17, 1, tzinfo=timezone(timedelta(hours=2)))
        self.assertEqual(ledger.status()["periods"]["daily"]["reset_at"], "2026-10-17T00:00:00Z")

    def test_orphans_do_not_expire_and_old_counters_are_pruned(self):
        ledger = self.ledger({"daily": "1", "weekly": "2", "monthly": "3"})
        charged = ledger.reserve("0.1")
        ledger.settle(charged, "0.1")
        orphan = ledger.reserve("0.8")
        self.clock.value += timedelta(days=400)
        state = ledger.status()
        self.assertEqual(state["periods"]["daily"]["reserved"], 0.8)
        self.assertEqual(state["periods"]["daily"]["spent"], 0)
        data = json.loads(self.path.read_text())
        self.assertTrue(all(not counters for counters in data["spent"].values()))
        self.assertIn(orphan, data["reservations"])
        ledger.settle(charged, "0.9")
        self.assertEqual(ledger.status()["periods"]["daily"]["spent"], 0)

    def test_corrupt_ledger_never_resets_to_zero(self):
        for damaged in ("", "{", "null", '{"version":1}', '{"version":1,"currency":"USD"}',
                        '{"version":1,"version":1}', '{"number":NaN}'):
            with self.subTest(damaged=damaged):
                self.path.write_text(damaged)
                with self.assertRaises(BudgetLedgerError):
                    self.ledger().status()
                with self.assertRaises(BudgetLedgerError):
                    self.ledger().reserve("0.1")
                self.assertEqual(self.path.read_text(), damaged)

    def test_invalid_persisted_amount_and_reservation_fail_closed(self):
        ledger = self.ledger()
        token = ledger.reserve("0.5")
        original = json.loads(self.path.read_text())
        for invalid in ("NaN", "-1", "Infinity", True):
            damaged = json.loads(json.dumps(original))
            damaged["reservations"][token]["amount"] = invalid
            self.path.write_text(json.dumps(damaged))
            with self.assertRaises(BudgetLedgerError):
                ledger.status()
        self.path.write_text(json.dumps(original))
        with self.assertRaises(BudgetLedgerError):
            ledger.settle(str(uuid.uuid4()), 0)
        self.assertEqual(ledger.status()["periods"]["daily"]["reserved"], 0.5)

    def test_invalid_input_does_not_mutate_ledger(self):
        ledger = self.ledger()
        token = ledger.reserve("0.1")
        before = self.path.read_bytes()
        for invalid in ("NaN", "Infinity", "-Infinity", "-0.001", True, None, "garbage"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    ledger.reserve(invalid)
                if invalid is not None:
                    with self.assertRaises(ValueError):
                        ledger.settle(token, invalid)
                with self.assertRaises(ValueError):
                    self.ledger({"daily": invalid})
        self.assertEqual(self.path.read_bytes(), before)

    def test_threaded_reservations_respect_exact_three_quarters_limit(self):
        for shared in (True, False):
            with self.subTest(shared_instance=shared):
                path = self.path.with_name(f"threads-{shared}.json")
                ledger = BudgetLedger(path, {"daily": "0.75"}, now=self.clock)
                ready = threading.Barrier(10)

                def reserve(_index):
                    instance = ledger if shared else BudgetLedger(path, {"daily": "0.75"}, now=self.clock)
                    ready.wait(timeout=10)
                    try:
                        return instance.reserve("0.25")
                    except BudgetExceeded:
                        return None

                with ThreadPoolExecutor(max_workers=10) as pool:
                    accepted = [token for token in pool.map(reserve, range(10)) if token]
                self.assertEqual(len(accepted), 3)
                self.assertEqual(len(set(accepted)), 3)
                state = ledger.status()
                self.assertEqual(state["pending_requests"], 3)
                self.assertEqual(state["periods"]["daily"]["reserved"], 0.75)
                self.assertTrue(state["blocked"])
                stored = json.loads(path.read_text())
                self.assertEqual(sum(Decimal(item["amount"]) for item in stored["reservations"].values()), Decimal("0.75"))

    def test_one_hundred_float_cent_inputs_are_exact_on_disk(self):
        ledger = self.ledger()
        for _ in range(100):
            token = ledger.reserve(0.01)
            ledger.settle(token, 0.01)
        stored = json.loads(self.path.read_text())
        for counters in stored["spent"].values():
            self.assertEqual(len(counters), 1)
            self.assertEqual(Decimal(next(iter(counters.values()))), Decimal("1.00"))
        self.assertEqual(stored["reservations"], {})

    def test_unknown_settlement_cannot_discard_valid_reservations(self):
        ledger = self.ledger()
        tokens = [ledger.reserve("0.25") for _ in range(2)]
        before = self.path.read_bytes()
        with self.assertRaises(BudgetLedgerError):
            ledger.settle("invalid", None)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(ledger.status()["pending_requests"], 2)
        for token in tokens:
            ledger.settle(token, "0.25")
        self.assertEqual(ledger.status()["pending_requests"], 0)
        self.assertEqual(ledger.status()["periods"]["daily"]["spent"], 0.5)

    def test_concurrent_processes_cannot_overbook(self):
        with multiprocessing.get_context("fork").Pool(8) as pool:
            tokens = pool.map(competing_reservation, [str(self.path)] * 24)
        accepted = [token for token in tokens if token]
        self.assertEqual(len(accepted), 5)
        self.assertEqual(len(set(accepted)), 5)
        ledger = self.ledger({"daily": 1})
        self.assertEqual(ledger.status()["periods"]["daily"]["reserved"], 1)
        for token in accepted:
            ledger.settle(token, "0.2")
        self.assertEqual(ledger.status()["periods"]["daily"]["spent"], 1)

    def test_concurrent_settlement_is_idempotent(self):
        ledger = self.ledger({"daily": 1})
        token = ledger.reserve("0.2")
        with multiprocessing.get_context("fork").Pool(4) as pool:
            charges = pool.map(competing_settlement, [(str(self.path), token)] * 16)
        self.assertEqual(set(charges), {0.2})
        self.assertEqual(ledger.status()["pending_requests"], 0)

    def test_failed_atomic_write_keeps_previous_accounting(self):
        ledger = self.ledger({"daily": 1})
        ledger.reserve("0.2")
        before = self.path.read_bytes()
        with patch("ovh_budget.os.replace", side_effect=OSError("unavailable storage")):
            with self.assertRaises(BudgetLedgerError):
                ledger.reserve("0.3")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(ledger.status()["periods"]["daily"]["reserved"], 0.2)
        self.assertEqual(len(list(self.path.parent.glob(".budget.json.*"))), 0)

    def test_settlement_receipts_do_not_grow_with_request_count(self):
        ledger = self.ledger({"daily": 0})
        first = None
        for _ in range(120):
            token = ledger.reserve("0.001")
            first = first or token
            ledger.settle(token, "0.001")
        ledger.settle(first, "100")
        self.assertEqual(ledger.status()["periods"]["daily"]["spent"], 0.12)
        self.assertLess(self.path.stat().st_size, 1024)
        data = json.loads(self.path.read_text())
        self.assertEqual(data["sequence"], 120)
        self.assertEqual(data["reservations"], {})


class OvhBalanceTests(unittest.TestCase):
    def setUp(self):
        from ovh_budget import OvhBalanceLedger
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = datetime(2026, 10, 18, 15, tzinfo=timezone.utc)
        self.snapshot = dict(available=True, currency='EUR', credit_currency='EUR',
            project='test-project', current_usage=2.1, credit_available=200,
            last_update=self.now.isoformat(), cached_at=self.now.timestamp(),
            usage_period={'from':'2026-10-01T00:00:00Z'})
        self.factory = lambda: OvhBalanceLedger.from_provider(self.root/'ovh.json',
            {'daily':8,'weekly':56,'monthly':197}, self.root/'policy.json',
            lambda:dict(self.snapshot), now=lambda:self.now)

    def test_provider_balance_is_authoritative_not_settled_estimates(self):
        ledger=self.factory()
        self.assertAlmostEqual(ledger.status()['provider_balance'],197.9)
        for cost in (None, '100', '0.02'):
            token=ledger.reserve('.03');ledger.settle(token,cost)
        state=ledger.status()
        self.assertEqual(state['periods']['daily']['spent'],0)
        self.assertEqual(state['periods']['monthly']['spent'],2.1)
        self.assertEqual(state['pending_requests'],0)
        self.assertTrue(state['periods']['daily']['partial'])
        self.snapshot['current_usage']=2.35
        self.snapshot['last_update']=(self.now+timedelta(seconds=1)).isoformat()
        self.now+=timedelta(seconds=1)
        state=ledger.status()
        self.assertEqual(state['periods']['daily']['spent'],.25)
        self.assertEqual(state['provider_balance'],197.65)
        self.assertEqual(self.factory().status()['periods']['daily']['spent'],.25)

    def test_configured_credit_cap_does_not_relabel_provider_balance(self):
        policy={'schema':1,'limits':{},'credit':{'starts_at':'2026-10-18T00:00:00Z',
                'expires_at':'2026-11-15T20:00:00Z','limit_eur':197}}
        (self.root/'policy.json').write_text(json.dumps(policy))
        state=self.factory().status()
        self.assertEqual(state['provider_balance'],197.9)
        self.assertEqual(state['authorized_balance'],197)

    def test_corrupt_provider_baseline_never_resets(self):
        self.factory().status()
        path=self.root/'ovh.json';data=json.loads(path.read_text());del data['ovh']['baselines']['daily']
        path.write_text(json.dumps(data));before=path.read_bytes()
        with self.assertRaises(BudgetLedgerError):self.factory().status()
        self.assertEqual(path.read_bytes(),before)

    def test_insufficient_ovh_balance_and_concurrent_holds(self):
        from concurrent.futures import ThreadPoolExecutor
        self.snapshot['credit_available']=3
        def attempt(_):
            try:return self.factory().reserve('.6')
            except BudgetExceeded:return None
        with ThreadPoolExecutor(max_workers=2) as pool:tokens=list(pool.map(attempt,range(2)))
        self.assertEqual(sum(token is not None for token in tokens),1)
        for token in tokens:
            if token:self.factory().settle(token,None)
        self.assertAlmostEqual(self.factory().status()['provider_balance'],.9)

    def test_missing_stale_mismatched_data_blocks_and_does_not_spend(self):
        for patch in [{'available':False},{'stale':True},{'credit_currency':'USD'},
                      {'cached_at':self.now.timestamp()-301},{'last_update':'bad'},
                      {'usage_period':{'from':'2026-09-01T00:00:00Z'}}]:
            original=dict(self.snapshot);self.snapshot.update(patch)
            with self.assertRaises(BudgetLedgerError):self.factory().reserve('.1')
            self.snapshot=original
        self.assertFalse((self.root/'ovh.json').exists())

    def test_failure_releases_reservation_even_if_billing_is_down(self):
        ledger=self.factory();token=ledger.reserve('.2')
        self.snapshot['available']=False
        self.assertEqual(ledger.settle(token,None)['pending_requests'],0)
        self.assertEqual(ledger.settle(token,None)['periods']['monthly']['spent'],2.1)

    def test_month_change_and_top_up_are_not_counted_as_extra_spending(self):
        ledger=self.factory();ledger.status()
        self.snapshot['credit_available']=250
        self.assertEqual(ledger.status()['periods']['daily']['spent'],0)
        self.now=datetime(2026,11,1,1,tzinfo=timezone.utc)
        self.snapshot.update(current_usage=.2,usage_period={'from':'2026-11-01T00:00:00Z'},
                             last_update=self.now.isoformat(),cached_at=self.now.timestamp())
        state=ledger.status()
        self.assertEqual(state['periods']['monthly']['spent'],.2)
        self.assertEqual(state['periods']['daily']['spent'],.2)
        self.assertEqual(state['provider_balance'],249.8)

    def test_older_backend_snapshot_cannot_restore_spent_credit(self):
        ledger=self.factory();ledger.status();old=dict(self.snapshot)
        self.now+=timedelta(seconds=60)
        self.snapshot.update(current_usage=3,last_update=self.now.isoformat(),cached_at=self.now.timestamp())
        ledger.status();self.snapshot=old
        self.assertEqual(ledger.status()['provider_balance'],197)


if __name__ == "__main__":
    unittest.main(verbosity=2)
