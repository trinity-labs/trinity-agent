"""Regrade saved answers, archive an audit summary and optionally apply local routing evidence."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from trinity_agent.memory import atomic_write
from trinity_agent.evaluation import comparison_key
spec=importlib.util.spec_from_file_location('catalog_benchmark',ROOT/'scripts/evaluate-catalog.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)


def metrics(rows):
    usable=[r for r in rows if r['status'] not in {'skipped','blocked'}]
    successful=[r for r in usable if r.get('passed')]
    times=[r['seconds'] for r in usable if 'seconds' in r]
    return {'samples':len(usable),'passed':sum(bool(r.get('first_passed',r.get('passed'))) for r in usable),
            'final_passed':sum(bool(r.get('passed')) for r in usable),
            'errors':sum(r['status']=='error' for r in usable),
            'median_seconds':round(statistics.median(times),3) if times else 0,
            'median_tokens':statistics.median([r.get('input_tokens',0)+r.get('output_tokens',0) for r in successful]) if successful else 0,
            'median_cost_eur':statistics.median([r.get('cost_eur',0) for r in successful]) if successful else 0}


def comparison_for(report, rows):
    """Bind every measured sample to its original exact case and settings."""
    if (report.get('methodology_version') != 3 or not report.get('completed_at')
            or not isinstance(report.get('routing_protocol'), dict) or not rows
            or any(row.get('status') in {'blocked','skipped'} for row in rows)):
        return None
    domain = rows[0]['domain']
    cases = {case['id']:(case['id'],case['domain'],case['prompt'],case['expected'])
             for case in report['cases'] if case['domain'] == domain}
    if len(rows) != len(cases) or {row['case'] for row in rows} != set(cases):
        return None
    fingerprints = []
    for row in sorted(rows, key=lambda row:row['case']):
        fingerprint = b.case_digest(cases[row['case']])
        if row.get('case_sha256') != fingerprint or not isinstance(row.get('settings'), dict):
            return None
        fingerprints.append({'id':row['case'],'sha256':fingerprint,'settings':row['settings']})
    comparison = {'version':1,'campaign':report['run_id']+'@'+report['date'],
                  'protocol':report['routing_protocol'],'cases':fingerprints}
    return comparison if comparison_key({'samples':len(rows),'comparison':comparison}) else None


def comparable_scores(reports, grouped):
    """Select a shared cohort by coverage/recency, never per-model performance."""
    report_by_run = {report['run_id']:report for report in reports}
    cohorts = defaultdict(lambda:defaultdict(dict))
    for (mid,domain,run),rows in grouped.items():
        report = report_by_run[run]
        comparison = comparison_for(report,rows)
        score = metrics(rows)
        if not comparison or score['samples'] < 2:
            continue
        score['comparison'] = comparison
        cohorts[domain][comparison_key(score)][mid] = (score,rows,report['date'])
    selected = {}
    for domain, options in cohorts.items():
        # One tested model cannot establish a comparative preference. Keep its
        # report archived until a common campaign actually measures its peers.
        comparable = [(key,models) for key,models in options.items() if len(models) >= 2]
        if not comparable:
            continue
        _, models = max(comparable, key=lambda item:(len(item[1]),
                         max(value[2] for value in item[1].values()), item[0]))
        for mid,(score,rows,_) in models.items():
            selected[(mid,domain)] = (score,rows)
    return selected


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports',nargs='+',type=Path)
    parser.add_argument('--data-root',type=Path,default=Path.home()/'.trinity-agent')
    parser.add_argument('--workspace',type=Path,default=Path('/home/coder'))
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    reports=[json.loads(p.read_text()) for p in args.reports]
    sources={r['source'] for r in reports}
    if len(sources)!=1: raise SystemExit('Cannot combine different endpoints')
    if args.apply and any(not r.get('completed_at') for r in reports): raise SystemExit('Wait for completed reports before applying')
    grouped=defaultdict(list);all_rows=[]
    archive_root=args.data_root/'archive'/hashlib.sha256(str(args.workspace.resolve()).encode()).hexdigest()[:20]
    for report in reports:
        cases={c['id']:(c['id'],c['domain'],c['prompt'],c['expected']) for c in report['cases']}
        for original in report['results']:
            row=dict(original)
            if row.get('error_type') == 'BudgetExceeded':
                row.update(status='blocked',passed=None)
            if row.get('attempts') and not row['attempts'][-1]['passed'] and 'output retries' in row.get('reason',''):
                row.update(status='failed',answer=row['attempts'][-1]['answer'],error_category='validation')
            if 'answer' in row:
                passed,reason=b.grade(cases[row['case']],row['answer'],row.get('calls',[]))
                row.update(passed=bool(passed),status='passed' if passed else 'failed',grading_note=reason,original_status=original['status'])
            row['profile']={'reasoning':report.get('reasoning','default'),'max_tokens':report['max_tokens'],'retries':report.get('retries',0),'run_id':report['run_id'],'sampling':report.get('sampling','default')}
            row['methodology_version']=report.get('methodology_version',1)
            row['streaming']=report.get('streaming',True)
            row['api']=report.get('api','chat')
            if row.get('attempts') and 'calls' in row['attempts'][0]:
                first=row['attempts'][0]
                row['first_passed']=bool(b.grade(cases[row['case']],first['answer'],first['calls'])[0])
            grouped[(row['model'],row['domain'],report['run_id'])].append(row)
            all_rows.append(row)
        # Append a clearly identified regrade to existing conversations; preserve raw answers.
        for mid in {r['model'] for r in report['results']}:
            identifier='eval-'+report['run_id']+'-'+b.re.sub('[^a-zA-Z0-9_-]','-',mid)
            folder=archive_root/identifier
            if not (folder/'events.jsonl').exists(): continue
            rows=[r for r in all_rows if r['model']==mid and r['profile']['run_id']==report['run_id']]
            checks='\n'.join(f"- {r['case']}: {r['status']}" for r in rows)
            event={'type':'response','timestamp':datetime.now(timezone.utc).isoformat(),'evaluation_regrade':True,'text':'Révision du score technique (commandes équivalentes et JSON entouré de prose acceptés). Les réponses et scores initiaux restent ci-dessus.\n\n'+checks}
            events=[json.loads(line) for line in (folder/'events.jsonl').read_text().splitlines() if line]
            events=[e for e in events if not e.get('evaluation_regrade')]+[event]
            atomic_write(folder/'events.jsonl',''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in events))
    evidence={'schema':2,'source':next(iter(sources)),'created_at':min(r['date'] for r in reports),'scope':'Small synthetic per-domain evaluations from comparable campaigns; not a general quality certification or a Codex comparison.','reports':[str(p.resolve()) for p in args.reports],'models':{}}
    for (mid,domain),(score,rows) in comparable_scores(reports,grouped).items():
        model=evidence['models'].setdefault(mid,{'domains':{},'settings':{'reasoning_domains':[]}})
        model['domains'][domain]=score
        if all(row['settings'].get('openai_reasoning_effort')=='none' for row in rows) and score['passed']==score['samples'] and not score['errors']:
            model['settings']['reasoning_effort']='none';model['settings']['reasoning_domains'].append(domain)
    if args.apply:
        from trinity_agent.evaluation import load_evidence
        target=args.data_root/'model-evaluations.json'
        if target.exists(): atomic_write(target.with_suffix('.previous.json'),target.read_text())
        atomic_write(target,json.dumps(evidence,ensure_ascii=False,indent=2))
        if not load_evidence(args.data_root,evidence['source']): raise SystemExit('Evidence validation failed')
    text=['Évaluations du catalogue OVH — synthèse','',f"{len(all_rows)} résultats enregistrés, dont {sum(r['status']=='skipped' for r in all_rows)} non applicables et {sum(r['status']=='blocked' for r in all_rows)} bloqués avant appel par le budget.",
          'Les essais système/réseau/OS sont des cas synthétiques : aucune commande sur vos machines. Le code Python produit est testé par 6 assertions. Les tests web exigent des appels réels de recherche et lecture. Les images sont des captures synthétiques archivées.',
          'Les scores techniques acceptent des commandes équivalentes ; les réponses brutes restent consultables. Un petit nombre de cas ne certifie pas une spécialisation. Aucun chronométrage Codex comparable n’a été effectué.','',
          'Les essais sans contrat de comparaison restent archivés mais ne règlent pas le routeur. Seuls les modèles mesurés dans une campagne commune, avec les mêmes cas et réglages effectifs, sont comparés. Le score au premier essai gouverne le routage ; le score après feedback est séparé.', '',
          '| Modèle | Domaine | Premier essai | Après feedback | Erreurs | Médiane s | Tokens médians (réussites) |', '|---|---|---:|---:|---:|---:|---:|']
    for mid,model in sorted(evidence['models'].items()):
        for domain,value in sorted(model['domains'].items()):
            text.append(f"| {mid} | {domain} | {value['passed']}/{value['samples']} | {value['final_passed']}/{value['samples']} | {value['errors']} | {value['median_seconds']} | {value['median_tokens']} |")
    text += ['','Routage local : '+('appliqué, preuves valables 14 jours sur cet endpoint.' if args.apply else 'non appliqué par cette synthèse.'),'',
             'Les erreurs réseau, timeouts et dépassements de tokens restent des erreurs, pas des refus de sécurité. Les modes sans raisonnement ne sont retenus que dans les domaines ayant réussi tous leurs cas.',
             '', 'Modèles hors périmètre chat ou indisponibles :']
    for card in reports[0]['catalog']:
        if not card.get('selectable'): text.append('- '+card['id']+' : '+card['kind']+', non sélectionnable dans le catalogue courant.')
    identifier='eval-'+reports[0]['run_id']+'-summary';folder=archive_root/identifier;folder.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(timezone.utc).isoformat()
    events=[{'type':'user','timestamp':reports[0]['date'],'text':'Comparer les modèles du catalogue : précision, code, web, sécurité, OS, images, tokens et latence.'},{'type':'response','timestamp':stamp,'text':'\n'.join(text)}]
    atomic_write(folder/'events.jsonl',''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in events))
    atomic_write(folder/'meta.json',json.dumps({'id':identifier,'title':'Tests catalogue — SYNTHÈSE et réglages','workspace':str(args.workspace.resolve()),'created_at':reports[0]['date'],'updated_at':stamp,'evaluation':True},ensure_ascii=False))
    atomic_write(args.reports[0].parent/'evaluated-results.json',json.dumps({'evidence':evidence,'results':all_rows},ensure_ascii=False,indent=2))
    print(json.dumps({'results':len(all_rows),'archive':str(folder),'applied':args.apply},ensure_ascii=False))


if __name__=='__main__': main()
