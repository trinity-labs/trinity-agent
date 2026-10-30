import tempfile
from decimal import Decimal
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trinity_agent.budget import AvailableBalanceLedger, BudgetExceeded, BudgetLedgerError


with tempfile.TemporaryDirectory() as folder:
    state = {'available': True, 'currency': 'USD', 'credit_remaining': 10, 'last_update': 'now'}
    ledger = AvailableBalanceLedger(Path(folder) / 'balance.json', 'fixture', 'USD', lambda: dict(state))
    first = ledger.reserve(Decimal('6'))
    try:
        ledger.reserve(Decimal('4.01'))
        raise AssertionError('concurrent reservations overbooked the available balance')
    except BudgetExceeded:
        pass
    ledger.settle(first, Decimal('2'))
    assert ledger.status()['available_to_reserve'] == 8
    state['credit_remaining'] = 8
    assert ledger.status()['pending_spent'] == 0
    exact = ledger.reserve(Decimal('8'))
    ledger.settle(exact, Decimal('8'))
    assert ledger.status()['blocked']

with tempfile.TemporaryDirectory() as folder:
    stale = AvailableBalanceLedger(Path(folder) / 'stale.json', 'fixture', 'USD',
                                   lambda: {'available': True, 'stale': True,
                                            'currency': 'USD', 'credit_remaining': 10})
    try:
        stale.reserve(1)
        raise AssertionError('a stale balance did not fail closed')
    except BudgetLedgerError:
        pass

print('Available balance guard: OK')
