"""Offline, isolated startup/context benchmark. No provider calls or OS cache flush."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]


def worker(args):
    started=time.perf_counter()
    sys.path.insert(0,str(ROOT))
    # A child gets an isolated data root/workspace; never read production credentials.
    os.environ.update(OVH_AGENT_DATA_ROOT=args.data,OVH_AGENT_WORKSPACE=args.workspace,
                      OVH_AI_API_KEY='offline-benchmark',PYDANTIC_AI_NO_BANNER='1')
    import socket
    def offline(*args,**kwargs): raise RuntimeError('Benchmark forbids network access')
    socket.create_connection=offline
    socket.socket.connect=offline
    if args.env_file:
        for line in Path(args.env_file).read_text().splitlines():
            key,sep,value=line.partition('=')
            if sep and key.startswith('OVH_AGENT_') and (value.isdigit() or value=='auto'):
                os.environ[key]=value
    sys.argv=['benchmark','--billing-status']
    if args.adapter:
        sys.path.insert(0,str(Path(args.adapter).parent))
        spec=importlib.util.spec_from_file_location('benchmark_adapter',args.adapter)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        a=module.load_backend()
    else:
        spec=importlib.util.spec_from_file_location('benchmark_backend',ROOT/'ovh-agent.py')
        a=importlib.util.module_from_spec(spec);sys.modules[spec.name]=a;spec.loader.exec_module(a)
    if not Path(args.workspace).is_relative_to(a.ALLOWED_ROOT):
        # CI runners need not have /home/coder; keep every path in the fixture.
        import hashlib
        a.ALLOWED_ROOT=Path(args.workspace).parent
        a.WORKSPACE=Path(args.workspace)
        a.SESSION_ID=hashlib.sha256(str(a.WORKSPACE).encode()).hexdigest()[:20]
        a.SESSION_FILE=a.STATE_ROOT/(a.SESSION_ID+'.json')
        a.ARCHIVE_ROOT=a.DATA_ROOT/'archive'/a.SESSION_ID
        a.POINTER_FILE=a.ARCHIVE_ROOT/'current'
        a.MODEL_SELECTION_FILE=a.ARCHIVE_ROOT/'selected-model'
    imported=time.perf_counter()
    events=[]
    def emit(event):
        if event.get('type')=='ready':events.append((time.perf_counter()-started)*1000)
    a.emit=emit
    a.refresh_billing_async=lambda *args,**kwargs:None
    a.current_budget_status=lambda:{}
    with a.backend_session():
        a.initialise_archive()
        a.main_json_lines()  # stdin is EOF, so ready/archive lifecycle then clean shutdown.
    a.JSON_MODE=False
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.messages import ModelResponse,TextPart,ModelRequest,UserPromptPart,ToolCallPart,ToolReturnPart
    captured=[]
    def respond(messages,info):
        schema=[{'name':t.name,'description':t.description,'parameters':t.parameters_json_schema} for t in info.function_tools]
        captured.append({'instruction_tokens':a.text_tokens(info.instructions or ''),
                         'schema_tokens':a.text_tokens(json.dumps(schema,ensure_ascii=False,separators=(',',':'))),
                         'message_tokens':a.estimate_tokens(messages),'tools':len(schema)})
        return ModelResponse(parts=[TextPart('Offline fixture completed.')])
    fake=FunctionModel(respond,model_name='gpt-oss-120b')
    a.BudgetedModel=lambda *args,**kwargs:fake
    a.fetch_model_catalog=lambda *args,**kwargs:[{'id':'gpt-oss-120b','context_length':131072}]
    a.model_capabilities=lambda *args,**kwargs:{'tools':True,'reasoning':True,'reasoning_efforts':['low','medium']}
    a.AGENTS.clear()
    stamp=time.perf_counter();agent=a.get_agent('gpt-oss-120b');build=(time.perf_counter()-stamp)*1000
    stamps=[]
    for _ in range(4):
        stamp=time.perf_counter();agent.run_sync('Inspecte le projet sans le modifier.',model_settings={'max_tokens':8192});stamps.append((time.perf_counter()-stamp)*1000)
    # Small files use the real configured reader, cold then warm, without mutations.
    sample=Path(args.workspace)/'sample.py';sample.write_text('value = 42\n'*1000)
    reads=[]
    for _ in range(11):
        stamp=time.perf_counter();a.read_text_cached(sample);reads.append((time.perf_counter()-stamp)*1000)
    from ovh_context import compact_context,estimate_tokens
    from ovh_memory import ConversationMemory
    import asyncio
    history=[ModelRequest(parts=[UserPromptPart('Inspect all results; preserve evidence.')])]
    for i in range(160):
        history.extend([ModelResponse(parts=[ToolCallPart('read_file',{'path':f'f{i}'},tool_call_id=str(i))]),
                        ModelRequest(parts=[ToolReturnPart('read_file','line detail '*400,tool_call_id=str(i))])])
    stamp=time.perf_counter()
    compacted,stats=asyncio.run(compact_context(history,ConversationMemory(Path(args.data)/'synthetic'),10000))
    compact_ms=(time.perf_counter()-stamp)*1000
    print(json.dumps({'import_ms':(imported-started)*1000,'ready_ms':events[0], 'agent_build_ms':build,
        'first_request_offline_ms':stamps[0],'warm_request_offline_ms':statistics.median(stamps[1:]),
        'first_read_ms':reads[0],'warm_read_ms':statistics.median(reads[1:]),
        'context':captured[-1],'compaction_ms':compact_ms,'compacted_tokens':estimate_tokens(compacted),
        'compacted_units':stats['dropped_turns'],'context_cap':a.CONTEXT_TOKEN_CAP}))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--adapter');p.add_argument('--env-file');p.add_argument('--worker',action='store_true')
    p.add_argument('--data');p.add_argument('--workspace');p.add_argument('--runs',type=int,default=3);p.add_argument('--output',type=Path)
    args=p.parse_args()
    if args.worker:return worker(args)
    if not 1<=args.runs<=20:p.error('runs must be 1..20')
    rows=[]
    with tempfile.TemporaryDirectory(prefix='trinity-perf-',dir='/home/coder' if Path('/home/coder').is_dir() else None) as temporary:
        root=Path(temporary);workspace=root/'project';workspace.mkdir()
        for i in range(args.runs):
            data=root/f'data-{i}'
            for phase in ('cold_app_state','warm_restart'):
                command=[sys.executable,__file__,'--worker','--data',str(data),'--workspace',str(workspace)]
                for key in ('adapter','env_file'):
                    if getattr(args,key):command.extend(['--'+key.replace('_','-'),str(Path(getattr(args,key)).resolve())])
                result=subprocess.run(command,input='',text=True,capture_output=True,timeout=90)
                if result.returncode:raise RuntimeError(result.stderr[-3000:])
                row=json.loads(result.stdout.splitlines()[-1]);row.update(phase=phase,run=i);rows.append(row)
    output={'scope':'offline backend; fresh app data vs reused app data; OS caches not flushed; provider cold/warm not measured',
            'adapter':args.adapter,'rows':rows,'medians':{phase:{key:statistics.median(r[key] for r in rows if r['phase']==phase)
                for key in rows[0] if key.endswith('_ms')} for phase in ('cold_app_state','warm_restart')}}
    serialized=json.dumps(output,ensure_ascii=False,indent=2)
    if args.output:args.output.write_text(serialized)
    print(serialized)

if __name__=='__main__':main()
