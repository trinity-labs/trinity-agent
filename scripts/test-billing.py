"""Offline tests: OVH billing snapshots, deductions without intermediate rounding."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
sys.argv.append('--billing-status')
spec = importlib.util.spec_from_file_location('agent_billing', root / 'ovh-agent.py')
a = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = a
spec.loader.exec_module(a)

base = {'configured': True, 'available': True, 'currency': 'EUR',
        'credit_currency': 'EUR', 'credit_available': 100, 'current_usage': 10.04}
with patch.dict(os.environ, {'OVH_AGENT_BALANCE_STEP': '0.10'}):
    status = a.billing_balance(base)
    assert status['credit_available'] == 100 and status['credit_remaining'] == 89.96
    assert status['credit_remaining_display'] == 89.96 and status['balance_estimated']
    # Reapplying an enriched status must not accumulate a second debit.
    assert a.billing_balance(status) == status
    for credit, usage, expected in [(1, .95, .05), (1, 1.05, -.05), (1, 1.01, -.01)]:
        assert a.billing_balance({**base, 'credit_available': credit, 'current_usage': usage})['credit_remaining_display'] == expected
    mismatch = a.billing_balance({**base, 'credit_currency': 'USD'})
    assert not mismatch['balance_estimated'] and 'credit_remaining' not in mismatch
    for invalid in (None, 'oops', float('nan'), float('inf')):
        bad = a.billing_balance({**base, 'current_usage': invalid})
        assert not bad['balance_estimated'] and bad['balance_error']
for invalid in ('oops', 'NaN', 'Infinity', '0', '-1', '100'):
    with patch.dict(os.environ, {'OVH_AGENT_BALANCE_STEP': invalid}):
        assert a.billing_balance(base)['credit_remaining_display'] == 89.96
with patch.dict(os.environ, {'OVH_AGENT_BALANCE_STEP': '.01'}):
    assert a.billing_balance(base)['credit_remaining_display'] == 89.96

def price(value, currency='EUR'):
    return {'value': value, 'currencyCode': currency}

snapshot = {'credit': 100, 'usage': 10.04, 'currency': 'EUR',
            'credit_currency': 'EUR', 'period': {'from': '2026-10-01', 'to': '2026-11-01'}}
def api(path):
    if path.endswith('/usage/current'):
        return {'totalPrice': price(snapshot['usage'], snapshot['currency']),
                'period': snapshot['period'], 'lastUpdate': '2026-10-16T10:00:00Z'}
    if path.endswith('/usage/forecast'):
        return {'totalPrice': price(30), 'usableCredits': {'totalCredit': 30}}
    if path.endswith('/credit'):
        return ['one']
    if path.endswith('/credit/one'):
        return {'available_credit': price(snapshot['credit'], snapshot['credit_currency']),
                'used_credit': price(0), 'total_credit': price(100)}
    raise AssertionError(path)

with tempfile.TemporaryDirectory() as tmp, patch.object(a, 'ARCHIVE_ROOT', Path(tmp)), \
        patch.object(a, 'billing_configured', return_value=True), \
        patch.dict(os.environ, {'OVH_CLOUD_PROJECT': 'test-project', 'OVH_AGENT_BALANCE_STEP': '.10'}), \
        patch.object(a, 'ovh_api_get', side_effect=api) as remote:
    first = a.fetch_billing_status(force=True)
    assert first['credit_remaining_display'] == 89.96
    raw = json.loads(a.billing_file().read_text())
    assert raw['credit_available'] == 100 and 'credit_remaining' not in raw
    assert raw['last_update'] == '2026-10-16T10:00:00Z'
    second = a.fetch_billing_status(force=True)
    assert second['credit_remaining'] == first['credit_remaining']
    remote.reset_mock()
    cached = a.fetch_billing_status()
    assert cached['credit_remaining'] == first['credit_remaining']
    remote.assert_not_called()
    snapshot['usage'] = 10.25
    updated = a.fetch_billing_status(force=True)
    assert updated['credit_remaining_display'] == 89.75
    # Invoice consumes last month's credit; a new period starts from new raw values.
    snapshot.update(credit=90, usage=.25, period={'from': '2026-11-01', 'to': '2026-12-01'})
    next_month = a.fetch_billing_status(force=True)
    assert next_month['credit_remaining'] == 89.75
    assert next_month['usage_period'] == snapshot['period']
    remote.side_effect = OSError('offline')
    stale = a.fetch_billing_status(force=True)
    assert stale['stale'] and stale['credit_remaining'] == next_month['credit_remaining']
    remote.side_effect = api
    # Corrupt provider amounts never silently become zero usage or credit.
    for field in ('credit', 'usage'):
        valid = snapshot[field]
        for invalid in ('oops', None, float('inf')):
            snapshot[field] = invalid
            bad = a.fetch_billing_status(force=True)
            assert bad['stale'] and bad['credit_remaining'] == next_month['credit_remaining']
        snapshot[field] = valid
    snapshot['credit_currency'] = 'USD'
    different_currency = a.fetch_billing_status(force=True)
    assert different_currency['available'] and not different_currency['balance_estimated']
    assert different_currency['credit_currency'] == 'USD'

print('PASS: credit minus current OVH usage, full precision, no repeated debit, period rollover, cache and invalid/cross-currency data')
