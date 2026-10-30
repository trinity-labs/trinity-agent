"""Explicitly enabled local daily campaign; exits at credit expiry or budget exhaustion."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from trinity_agent.budget import BudgetLedger
from trinity_agent.memory import atomic_write


def plan(config, now):
    start=datetime.fromisoformat(config['starts_at'])
    end=datetime.fromisoformat(config['expires_at'])
    if not config.get('enabled') or not start <= now < end:
        return None
    day=(now.date()-start.date()).days
    # Basic -> stale context/subnet decomposition -> injected instructions/long context.
    return {'level':min(3,day+2),'seed':int(now.strftime('%Y%m%d')),
            'max_tokens':8192 if day<2 else 16384,
            'reasoning':'medium' if day % 2 else 'low'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--dry-run',action='store_true')
    args=parser.parse_args()
    config=json.loads(args.config.read_text())
    now=datetime.now(timezone.utc);selected=plan(config,now)
    if selected is None:
        print('Campagne inactive ou échéance atteinte.');return
    output=ROOT/'local-recovery/model-evaluation'
    output.mkdir(parents=True,exist_ok=True)
    with (output/'campaign.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print('Campagne déjà active.');return
        day=now.date().isoformat();state_file=output/'campaign-state.json'
        state=json.loads(state_file.read_text()) if state_file.exists() else {}
        if state.get('date')==day and not args.dry_run:
            print('Campagne du jour déjà lancée.');return
        data=Path(config['data_root']);policy_file=data/'budgets/policy.json'
        policy=json.loads(policy_file.read_text())
        ledger=BudgetLedger.from_policy(data/'budgets/usage-eur.json',{},policy_file)
        status=ledger.status()
        if status['blocked']:
            print('Budget indisponible.');return
        if args.dry_run:
            print(json.dumps({'plan':selected,'budget':status,'date':day}));return
        # The provider balance can decrease independently of our local ledger.
        result=subprocess.run([str(ROOT/'trinity-agent.local.sh'),'--billing-status'],
                              capture_output=True,text=True,timeout=90)
        if result.returncode:
            raise RuntimeError('Impossible de vérifier le crédit OVH ; aucun test lancé.')
        billing=json.loads(result.stdout)
        remaining=billing.get('credit_remaining')
        if not billing.get('available') or not isinstance(remaining,(int,float)) or not math.isfinite(remaining):
            raise RuntimeError('Solde OVH non vérifié ; aucun test lancé.')
        if remaining <= 2:
            print('Crédit OVH épuisé (marge de facturation 2 EUR).');return
        spent=status['periods']['credit']['spent']
        policy['credit']['limit_eur']=min(policy['credit']['limit_eur'],round(spent+remaining-2,8))
        atomic_write(policy_file,json.dumps(policy,indent=2))
        state={'date':day,'started_at':now.isoformat(),'plan':selected,'status':'running'}
        atomic_write(state_file,json.dumps(state,indent=2))
        report=output/f'campaign-{day}.json'
        argv=[sys.executable,str(ROOT/'scripts/evaluate-catalog.py'),'--live','--output',str(report),
              '--level',str(selected['level']),'--seed',str(selected['seed']),
              '--max-tokens',str(selected['max_tokens']),'--reasoning',selected['reasoning'],
              '--workers','3','--retries','2','--feedback']
        env={**os.environ,'PYDANTIC_AI_NO_BANNER':'1'}
        try:
            with report.with_suffix('.log').open('w') as log:
                completed=subprocess.run(argv,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,env=env,timeout=7200)
            state['status']='completed' if completed.returncode==0 else 'failed'
            if completed.returncode==0:
                # Only current, complete reports with the repaired methodology affect routing.
                reports=[]
                for path in [output/'catalog-v2.json',*sorted(output.glob('campaign-????-??-??.json'))]:
                    if not path.exists(): continue
                    saved=json.loads(path.read_text())
                    if saved.get('completed_at') and saved.get('methodology_version')==2 and (now-datetime.fromisoformat(saved['date'])).days<14:
                        reports.append(str(path))
                subprocess.run([sys.executable,str(ROOT/'scripts/summarize-evaluation.py'),*reports,'--apply'],
                               cwd=ROOT,env=env,check=True,timeout=90)
        except Exception as error:
            state.update(status='failed',error=type(error).__name__+': '+str(error)[:500])
            raise
        finally:
            state['finished_at']=datetime.now(timezone.utc).isoformat()
            atomic_write(state_file,json.dumps(state,indent=2))


if __name__=='__main__': main()
