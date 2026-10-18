"""Local, expiring empirical routing evidence. Never treats catalogue claims as tests."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import unicodedata

from ovh_cache import read_text_cached

DOMAINS = {'code','web','system_security','network_security','linux','windows','macos','android','ios','vision','general'}


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
        if data.get('schema') != 1 or data.get('source') != source or not 0 <= age <= 14*86400:
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
        return data
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return {}


def prefer_evaluated(candidates, evidence, domain):
    """Prefer full-quality measured cases; unknown/expired evidence keeps old routing."""
    measured = []
    for card in candidates:
        model = evidence.get('models',{}).get(card['id'],{})
        result = model.get('domains',{}).get(domain, {})
        if result.get('samples',0) >= 2:
            measured.append((card,result))
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
    return evidence.get('models',{}).get(card['id'],{}).get('domains',{}).get(domain,{}).get('median_seconds',float('inf'))


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
    if (domain not in settings.get('reasoning_domains',[]) or result.get('samples',0) < 2
            or result.get('passed') != result.get('samples') or result.get('errors',1)):
        return {}
    # Only optional supported reasoning modes, never arbitrary endpoint settings.
    return {key:value for key,value in settings.items()
            if key == 'reasoning_effort' and value in {'none','low','medium','high'}}
