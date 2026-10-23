"""Task-local tool discovery: omit unused schemas, never cache authorization.

The complete catalogue remains discoverable. Discovery changes visibility only;
execution approvals, path checks and read-only policies still run independently.
"""
from __future__ import annotations
import re
import json
import threading
import unicodedata

CORE = frozenset({'read_file','list_files','search_text','edit_file','write_file','discover_tools',
                  'search_memory','read_memory'})
GROUPS = {
    'web': {'search_web','read_webpage'},
    'memory': {'search_memory','read_memory','search_project_memory','read_project_memory','remember_context','inspect_json','inspect_conversation_archives'},
    'repository': {'inspect_repository','update_plan'},
    'system': {'inspect_environment','install_tools','run_host_command','read_local_skill'},
    'recovery': {'workflow_status','inspect_task_execution','verify_task_acceptance','execute_operation','reconcile_operation'},
    'styles': {'inspect_styles','edit_file_batch'},
    'delivery': {'verify_extension_delivery','report_local_issue'},
}
ALIASES = {'mémoire':'memory','memoire':'memory','archives':'memory','historique':'memory',
           'dépôt':'repository','depot':'repository','repo':'repository','plan':'repository',
           'système':'system','systeme':'system','ssh':'system','linux':'system',
           'reprise':'recovery','contrat':'recovery','transaction':'recovery','rollback':'recovery',
           'css':'styles','livraison':'delivery','release':'delivery','publication':'delivery'}


def words(value):
    text=''.join(c for c in unicodedata.normalize('NFKD',value.casefold()) if not unicodedata.combining(c))
    return set(re.findall(r'[a-z0-9_]+',text))


class ToolSelection:
    def __init__(self, prompt=''):
        self.lock=threading.RLock()
        self.catalog={}
        self.schema_sizes={}
        self.enabled=set(CORE)
        self.hinted_names=set()
        self.hint(prompt)

    def hint(self,prompt):
        # Explicit capabilities are cheap to infer; ambiguous requests retain a
        # generic core and can discover anything without an extra planning call.
        tokens=words(prompt)
        with self.lock:
            # A tool explicitly requested by the user or by task-local guidance
            # must be callable immediately, including tools registered later.
            self.hinted_names.update(tokens)
            known=set(CORE).union(*GROUPS.values(),self.catalog)
            self.enabled.update(tokens & known)
            for group,names in GROUPS.items():
                if group in tokens or any(words(alias)<=tokens for alias,value in ALIASES.items() if value==group):
                    self.enabled.update(names)
            if tokens & {'logo','logos','header','opacity','opacite','couleur','background','spinner','loading','chargement','style','styles'}:
                self.enabled.update(GROUPS['styles'])
            if re.search(r'\b(?:ajout\w*|modifi\w*|corrig\w*|supprim\w*|appliqu\w*|je veux|je souhaite|fix|add|remove|build)\b', prompt, re.I):
                self.enabled.update({'run_command','verification_plan','verify_changes'})
            if re.search(r'\b(?:reprends?|continue|mémoire|memoire|historique|previous|resume)\b', prompt, re.I):
                self.enabled.update({'search_memory','read_memory'})
            if re.search(r'https?://|\b(?:documentation|sources|recherche web|internet)\b',prompt,re.I):
                self.enabled.update(GROUPS['web'])
            if re.search(r'\b(?:refactor|audit|architecture|plusieurs|multi.file|autonomie)\w*',prompt,re.I):
                self.enabled.update(GROUPS['repository']|GROUPS['recovery'])

    def register(self, name, description, schema=None):
        with self.lock:
            self.catalog[name]=str(description or '').split('\n')[0][:260]
            if name in self.hinted_names:
                self.enabled.add(name)
            if schema is not None:
                self.schema_sizes[name] = len(json.dumps(schema, ensure_ascii=False).encode()) + len(str(description or '').encode()) + len(name) + 128

    def token_overhead(self):
        # Conservative allowance grows when discovery expands the active schemas.
        # Before SDK preparation the fallback covers the unmeasured core catalogue.
        with self.lock:
            return max(2500, sum(size for name,size in self.schema_sizes.items() if name in self.enabled) // 3 + 512)


    def visible(self,name):
        with self.lock:
            return name in self.enabled

    def discover(self,query=''):
        with self.lock:
            tokens=words(query)
            group_names=set()
            for token in tokens:
                group=ALIASES.get(token,token)
                group_names.update(GROUPS.get(group,set()))
            ranked=[]
            for name,description in self.catalog.items():
                score=(100 if name in tokens else 0)+(30 if name in group_names else 0)
                score+=len(tokens & (words(name.replace('_',' '))|words(description)))
                if score or not tokens:
                    ranked.append((-score,name,description))
            rows=sorted(ranked, key=lambda row: (row[1] in self.enabled, row[1]))[:12] if not tokens else sorted(ranked)[:12]
            self.enabled.update(name for _,name,_ in rows)
            return {'activated':[{'name':name,'description':description} for _,name,description in rows],
                    'groups':list(GROUPS),'catalogue_size':len(self.catalog),
                    'more_available':len(ranked)>len(rows),
                    'scope':'visibility_only; execution permissions unchanged'}
