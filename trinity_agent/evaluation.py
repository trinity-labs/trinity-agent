"""Local, expiring empirical routing evidence. Never treats catalogue claims as tests."""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import unicodedata

from trinity_agent.cache import read_text_cached

DOMAINS = {'code','web','system_security','network_security','linux','windows','macos','android','ios','vision','general'}


def comparison_key(result):
    """Require a versioned campaign with the exact cases and model settings.

    Legacy aggregates remain readable, but a matching domain name alone never
    establishes equal prompts or inference conditions.
    """
    comparison = result.get('comparison', {})
    if not isinstance(comparison, dict) or comparison.get('version') != 1:
        return None
    protocol = comparison.get('protocol', {})
    cases = comparison.get('cases', [])
    if (not isinstance(protocol, dict) or protocol.get('version') != 1
            or not isinstance(comparison.get('campaign'), str) or not comparison['campaign']
            or protocol.get('methodology_version') != 3
            or not re.fullmatch(r'[a-f0-9]{64}', str(protocol.get('runner_sha256', '')))
            or not isinstance(protocol.get('sdk_version'), str) or not protocol['sdk_version']
            or protocol.get('api') not in {'chat', 'responses'}
            or type(protocol.get('streaming')) is not bool or type(protocol.get('feedback')) is not bool
            or type(protocol.get('retries')) is not int or protocol['retries'] < 0
            or type(protocol.get('workers')) is not int or not 1 <= protocol['workers'] <= 4
            or protocol.get('request_limit') != 7
            or not isinstance(cases, list) or len(cases) != result.get('samples') or not cases):
        return None
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get('id'), str) or not case['id']
                or not re.fullmatch(r'[a-f0-9]{64}', str(case.get('sha256', '')))
                or not isinstance(case.get('settings'), dict)):
            return None
        for key in ('max_tokens', 'timeout'):
            value = case['settings'].get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                return None
    try:
        return hashlib.sha256(json.dumps(comparison, sort_keys=True, separators=(',', ':'),
                                         allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError):
        return None


def routing_domain(prompt, analysis=None, image_count=0):
    if image_count:
        return 'vision'
    text = ''.join(c for c in unicodedata.normalize('NFKD', prompt.lower()) if not unicodedata.combining(c))
    for domain, pattern in [('android',r'\bandroid\b|\badb\b'),('ios',r'\bios\b|\biphone\b|\bipad\b'),
                            ('macos',r'\bmacos\b|\bmac os\b|\blaunchctl\b'),('windows',r'\bwindows\b|\bpowershell\b'),
                            ('network_security',r'\breseau\b|\bnetwork\b|\bfirewall\b|\btls\b|\bsubnet\b|\bnftables\b'),
                            ('linux',r'\blinux\b|\balpine\b|\bdebian\b|\bubuntu\b|\bfedora\b|\barch\b|\bopenrc\b')]:
        if re.search(pattern, text):
            return domain
    if (analysis or {}).get('needs_web'):
        return 'web'
    domain = (analysis or {}).get('domain')
    if domain == 'security' or re.search(r'\bsecurite\b|\bsecurity\b', text):
        return 'system_security'
    if domain in {'code','ui'} or re.search(r'\bcode\b|\bpython\b|\bjavascript\b|\bfonction\b|\btests?\b',text):
        return 'code'
    return 'general'


def load_evidence(data_root, source, now=None):
    try:
        data = json.loads(read_text_cached(Path(data_root) / 'model-evaluations.json', limit=250001))
        age = ((now or datetime.now(timezone.utc)) - datetime.fromisoformat(data['created_at'])).total_seconds()
        if data.get('schema') not in {1, 2} or data.get('source') != source or not 0 <= age <= 14*86400:
            return {}
        if not isinstance(data.get('models'), dict):
            return {}
        for model in data['models'].values():
            if not isinstance(model, dict) or not isinstance(model.get('domains'), dict):
                return {}
            for domain, result in model['domains'].items():
                if domain not in DOMAINS or not isinstance(result,dict): return {}
                for key in ('samples','passed','errors','median_seconds','median_tokens','median_cost_eur'):
                    value = result[key]
                    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value < 0:
                        return {}
                if result['passed'] + result['errors'] > result['samples']: return {}
                if data['schema'] == 2 and not comparison_key(result): return {}
        return data
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return {}


def prefer_evaluated(candidates, evidence, domain):
    """Compare quality/latency only within the same campaign and conditions."""
    measured = []
    for card in candidates:
        model = evidence.get('models',{}).get(card['id'],{})
        result = model.get('domains',{}).get(domain, {})
        if result.get('samples',0) >= 2 and comparison_key(result):
            measured.append((card,result))
    # A single tested model cannot establish a comparative preference over
    # unknown peers. One cohort's easy cases cannot beat another's hard cases.
    if len(measured) < 2 or len({comparison_key(result) for _,result in measured}) != 1:
        return candidates, False
    reliable = [(card,result) for card,result in measured
                if result['passed']/result['samples'] >= .8 and not result['errors']]
    if not reliable:
        partial = [(card,result) for card,result in measured if result['passed']/result['samples'] >= .5]
        if partial:
            best = max(result['passed']/result['samples'] for _,result in partial)
            return [card for card,result in partial if result['passed']/result['samples'] == best], False
        # Repeated transport/output failures should not beat healthy/unknown models.
        unhealthy = {card['id'] for card,result in measured
                     if not result['passed'] and result['errors']/result['samples'] >= .5}
        healthy = [c for c in candidates if c['id'] not in unhealthy]
        return healthy or candidates, False
    best = max(result['passed']/result['samples'] for _,result in reliable)
    return [card for card,result in reliable if result['passed']/result['samples'] == best], True


def measured_latency(card, evidence, domain):
    result = evidence.get('models',{}).get(card['id'],{}).get('domains',{}).get(domain,{})
    return result.get('median_seconds', float('inf')) if comparison_key(result) else float('inf')


def routing_price_factor(default, override):
    try:
        value=float(override)
        return value if math.isfinite(value) and 1 <= value <= 10 else default
    except (ValueError,TypeError):
        return default


def measured_settings(model_id, evidence, domain='general'):
    model = evidence.get('models',{}).get(model_id,{})
    settings = model.get('settings',{})
    result = model.get('domains',{}).get(domain,{})
    if (not comparison_key(result) or domain not in settings.get('reasoning_domains',[]) or result.get('samples',0) < 2
            or result.get('passed') != result.get('samples') or result.get('errors',1)):
        return {}
    if any(case['settings'].get('openai_reasoning_effort') != settings.get('reasoning_effort')
           for case in result['comparison']['cases']):
        return {}
    # Only optional supported reasoning modes, never arbitrary endpoint settings.
    return {key:value for key,value in settings.items()
            if key == 'reasoning_effort' and value in {'none','low','medium','high'}}
