"""Read-only live benchmark of the actual preparation, router, agent and streaming path."""
import argparse
import asyncio
from datetime import datetime,timezone
import functools
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--adapter',type=Path,help='Optional private deployment adapter, excluded from public packages')
    parser.add_argument('--env-file',type=Path,help='Optional installation limits; omitted for public defaults')
    parser.add_argument('--model',help='Explicit comparison model; default is automatic routing')
    args=parser.parse_args()
    if not args.live: parser.error('--live required')
    local_keys={'TRINITY_AGENT_ROUTING_PRICE_FACTOR','TRINITY_AGENT_CONTEXT_TOKENS','TRINITY_AGENT_OUTPUT_TOKENS','TRINITY_AGENT_COMPLEX_OUTPUT_TOKENS','TRINITY_AGENT_TOOL_OUTPUT_CHARS','TRINITY_AGENT_MEMORY_CHARS','TRINITY_AGENT_SHARED_MEMORY_CHARS','TRINITY_AGENT_PREFLIGHT_CONTEXT_CHARS'}
    if args.env_file:
        for line in args.env_file.read_text().splitlines():
            key,sep,value=line.partition('=')
            if sep and key in local_keys and value.isdecimal(): os.environ[key]=value
    sys.path.insert(0,str(ROOT));sys.argv.append('--billing-status')
    if args.adapter:
        sys.path.insert(0,str(args.adapter.resolve().parent))
        spec=importlib.util.spec_from_file_location('pipeline_adapter',args.adapter.resolve())
        adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
        a=adapter.load_backend()
    else:
        spec=importlib.util.spec_from_file_location('pipeline_agent',ROOT/'trinity_agent/agent.py')
        a=importlib.util.module_from_spec(spec);sys.modules[spec.name]=a;spec.loader.exec_module(a)
    for path in [Path('/run/secrets/TRINITY_OVHCLOUD_API_KEY'),Path('/home/coder/TRINITY-DOCKER/etc/trinity/docker-trinity/TRINITY_OVHCLOUD_API_KEY')]:
        if not os.environ.get('TRINITY_OVHCLOUD_API_KEY') and path.is_file():os.environ['TRINITY_OVHCLOUD_API_KEY']=path.read_text().strip()
    from pydantic_ai.usage import UsageLimits,RunUsage
    from trinity_agent.memory import atomic_write
    # Preserve every tool schema; mutations are rejected inside this benchmark only.
    for name in ['edit_file','write_file','run_command','run_host_command','remember_context','install_tools']:
        original=getattr(a,name)
        @functools.wraps(original)
        def blocked(*args,**kwargs):return 'Benchmark en lecture seule : cette action est désactivée.'
        setattr(a,name,blocked)
    # A private adapter may add other tools; expose only the read-only fixture tools.
    original_tools=a.agent_tools
    allowed={'read_file','list_files','search_text','read_memory','search_memory','search_project_memory','read_project_memory','verify_changes','verification_plan'}
    a.agent_tools=lambda:[tool for tool in original_tools() if tool.name in allowed]
    a.request_approval=lambda *args,**kwargs:False
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    a.CONVERSATION_ID='eval-'+stamp+'-pipeline';a.JSON_MODE=True
    a.emit=a.archive_event
    folder=a.conversation_dir();folder.mkdir(parents=True,exist_ok=True)
    atomic_write(folder/'meta.json',json.dumps({'id':a.CONVERSATION_ID,'title':'Tests fluidité — pipeline réel et choix automatique','workspace':str(a.WORKSPACE),'created_at':a.now_iso(),'updated_at':a.now_iso(),'evaluation':True}))
    fixtures=Path('/home/coder/.trinity-agent-model-test')/stamp;fixtures.mkdir(parents=True)
    fixture=fixtures/'settings.json';fixture.write_text('{"port":8457,"service":"nebula-lab"}\n')
    prompt=f'Lis uniquement {fixture} et réponds en JSON avec port et service. Aucune modification ni recherche Internet.'
    report={'adapter':str(args.adapter) if args.adapter else None,'local_settings':{key:os.environ.get(key) for key in local_keys},'scope':'Actual agent instructions and preparation, read-only fixture tools; first request vs reused client/agent, provider cold state unknown; three repetitions, no Codex baseline.','results':[]}
    with asyncio.Runner() as runner:
      for index in range(3):
          started=time.monotonic();a.run_dependencies().verification=None
          a.emit({'type':'user','text':f'Test de fluidité {index+1}/3 — '+prompt})
          history,preparation=a.prepare_task_context([],prompt)
          model,routing=a.choose_cost_effective_model(prompt,a.estimate_tokens(history)*3,0,analysis=preparation['analysis'])
          if args.model: model=args.model
          settings=a.generation_settings(model,prompt,preparation['analysis']);settings['timeout']=45
          agent=a.get_agent(model);stream=a.ResponseStream();usage=RunUsage()
          a.begin_verification(prompt)
          row={'run':index+1,'model':model,'preparation':preparation,'routing':{k:v for k,v in routing.items() if k!='billing'}}
          try:
              async def run():return await asyncio.wait_for(agent.run(prompt,deps=a.run_dependencies(),message_history=history,model_settings=settings,usage=usage,usage_limits=UsageLimits(request_limit=4),event_stream_handler=stream.handle),timeout=60)
              result=runner.run(run());text=str(result.output)
              import re
              payload=re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())
              try: actual=json.loads(payload)
              except ValueError: actual=None
              row.update(answer=text,passed=actual=={'port':8457,'service':'nebula-lab'})
          except Exception as error:row.update(passed=False,error=type(error).__name__+': '+str(error)[:500])
          row.update(seconds=round(time.monotonic()-started,3),first_text_ms=stream.first_text_ms,input_tokens=usage.input_tokens,output_tokens=usage.output_tokens,requests=usage.requests)
          report['results'].append(row)
          a.emit({'type':'response','text':'```json\n'+json.dumps(row,ensure_ascii=False,indent=2)+'\n```'})
          atomic_write(args.output,json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(row,ensure_ascii=False),flush=True)
    report['methodology_version']=2
    report['archive']=str(folder);atomic_write(args.output,json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
