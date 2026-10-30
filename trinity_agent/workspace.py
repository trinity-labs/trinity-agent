"""Project context, sourced cross-conversation memory and bounded host discovery."""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import sys
import time
import threading
import queue
from pathlib import Path
from trinity_agent.memory import ConversationMemory, atomic_write, terms
from trinity_agent.cache import read_text_cached
from trinity_agent.project_memory import ProjectMemory, notes as markdown_notes, markdown_note, safe_path, conversation_eligible

TOOLS = ('sh','bash','git','rg','python3','node','npm','bun','go','cargo','rustc','docker','ssh','apk','apt-get','dnf','pacman','make','gcc','curl','xl','lbu')
_reader = None
_reader_lock = threading.Lock()

def project_id(workspace):
    return hashlib.sha256(str(Path(workspace).resolve()).encode()).hexdigest()[:20]

def read_bounded(path, limit=12000):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        return ''
    return read_text_cached(path, limit=limit)

class WorkspaceContext:
    def __init__(self, data_root, workspace):
        self.data = Path(data_root)
        self.workspace = Path(workspace).resolve()
        self.id = project_id(self.workspace)
        self.archive = self.data / 'archive' / self.id

    def profile(self):
        path = self.data / 'MASTER.md'
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                with path.open('x', encoding='utf-8') as stream:
                    stream.write('# Contexte utilisateur\n\nPréférences communes à tous les projets.\n'
                                 'Renseignez ici votre langue, vos conventions et vos contraintes durables.\n'
                                 'Les informations propres à un projet restent dans son AGENTS.md.\n')
                path.chmod(0o600)
            except FileExistsError:
                pass
        return read_bounded(path)

    def project_context(self):
        facts = {'root': str(self.workspace), 'name': self.workspace.name,
                 'manifests': [p for p in ('package.json','pyproject.toml','requirements.txt','Cargo.toml','go.mod','docker-compose.yml','config/trinity-agent-checks.json','.trinity-agent-checks.json') if (self.workspace/p).is_file()]}
        memory=ProjectMemory(self.data,self.id,self.workspace)
        memory.initialise()
        # Keep permanent context bounded. The index and memory tools expose the
        # complete Markdown set; retrieval injects only excerpts relevant now.
        documents = sum(1 for _ in memory.root_documents())
        facts['memory']={'notes':str(memory.root/'NOTES.md'),'overview':str(memory.root/'MEMORY.md'),'sources':str(memory.root/'INDEX.md'),'verification_history':str(memory.root/'REGRESSIONS.md'),'document_count':documents, 'read_with':{'tool':'read_project_memory','conversation':'project','references':['notes','overview','index','regressions']}}
        return json.dumps(facts, ensure_ascii=False)

    def folders(self, all_projects=False, limit=100):
        roots = list((self.data/'archive').glob('*'))[:100] if all_projects else [self.archive]
        folders = []
        for root in roots:
            if root.is_symlink() or not root.is_dir():
                continue
            for folder in root.iterdir():
                if folder.is_dir() and not folder.is_symlink() and re.fullmatch(r'[A-Za-z0-9_-]+',folder.name) and conversation_eligible(folder):
                    folders.append(folder)
        return sorted(folders, key=lambda p:p.stat().st_mtime, reverse=True)[:limit]

    def search_page(self, query, offset=0):
        """Explicit paging reaches old project conversations beyond the automatic window."""
        if offset < 0: raise ValueError('Offset positif requis.')
        global _reader
        result = queue.Queue(maxsize=1)
        def run():
            try:
                folders=self.folders(limit=None)
                rows=[]
                documents=ProjectMemory(self.data,self.id,self.workspace).search(query,3000) if offset==0 else ''
                for folder in folders[offset:offset+20]:
                    if any((folder/part).is_symlink() for part in ['memory','memory/index','memory/documents']): continue
                    if not (folder/'memory/index').is_dir(): continue
                    excerpt=ConversationMemory(folder).search(query,800,limit=1)
                    if excerpt: rows.append({'conversation':folder.name,'excerpt':excerpt})
                result.put({'matches':rows,'project_markdown':documents,'next_offset':offset+20 if offset+20<len(folders) else None,
                            'total_conversations':len(folders),'partial':False})
            except (OSError,ValueError,TypeError): result.put({'matches':[],'partial':True,'retry_offset':offset})
        with _reader_lock:
            if _reader is not None and _reader.is_alive(): return {'matches':[],'partial':True,'retry_offset':offset}
            _reader=threading.Thread(target=run,daemon=True,name='trinity-shared-memory-page');_reader.start();worker=_reader
        worker.join(1.1)
        try: return result.get_nowait()
        except queue.Empty: return {'matches':[],'partial':True,'retry_offset':offset}

    def remember(self, conversation, text, scope, status, source):
        if scope not in {'project','user'} or status not in {'declared','hypothesis'}:
            raise ValueError('scope: project/user ; status: declared/hypothesis. Le modèle ne peut pas certifier un souvenir.')
        if not text.strip() or len(text)>2000 or len(source)>500:
            raise ValueError('Souvenir vide ou trop long.')
        folder = self.archive / conversation
        if not re.fullmatch(r'[A-Za-z0-9_-]+',conversation) or not folder.is_dir() or folder.is_symlink():
            raise ValueError('Conversation invalide.')
        file = folder/'memory/notes'
        if not safe_path(file,self.data) or (folder/'.notes.lock').is_symlink():
            raise ValueError('Chemin mémoire invalide.')
        markdown_notes(folder)  # Migrate before taking the write lock.
        with (folder/'.notes.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            note = {'id':hashlib.sha256((scope+text).encode()).hexdigest()[:20], 'text':text,
                    'scope':scope,'status':status,'source':source,'conversation':conversation,
                    'project':self.id,'timestamp':time.time()}
            file.mkdir(parents=True,exist_ok=True,mode=0o700)
            target=file/(note['id']+'.md')
            if target.is_symlink(): raise ValueError('Note symbolique refusée.')
            atomic_write(target,markdown_note(note))
            atomic_write(file/'.migrated','Markdown is authoritative.\n')
            ProjectMemory(self.data,self.id,self.workspace).changed()
        return note

    def answer(self, query):
        """Bound local FAQ lookup on slow mounts; never start a second reader."""
        global _reader
        result=queue.Queue(maxsize=1)
        def run():
            try: result.put(ProjectMemory(self.data,self.id,self.workspace).answer(query))
            except (OSError,ValueError,TypeError): result.put(None)
        with _reader_lock:
            if _reader is not None and _reader.is_alive(): return None
            _reader=threading.Thread(target=run,daemon=True,name='trinity-memory-answer')
            _reader.start();worker=_reader
        worker.join(1.0)
        try: return result.get_nowait()
        except queue.Empty: return None

    def search(self, query, current='', budget=8000, *, automatic=False, exclude_paths=()):
        global _reader
        if budget <= 0 or not terms(query): return ''
        partial = "Recherche mémoire du projet incomplète (stockage lent ou indisponible) ; ne pas conclure à une absence de solution. Utiliser search_project_memory si nécessaire."
        result = queue.Queue(maxsize=1)
        def run():
            try: result.put(self._search(query, current, budget, automatic, exclude_paths))
            except (OSError, ValueError, TypeError): result.put(partial)
        with _reader_lock:
            if _reader is not None and _reader.is_alive(): return partial
            _reader = threading.Thread(target=run, daemon=True, name='trinity-shared-memory')
            _reader.start()
            worker = _reader
        worker.join(1.1)
        try: return result.get_nowait()
        except queue.Empty: return partial

    def _search(self, query, current='', budget=8000, automatic=False, exclude_paths=()):
        started = time.monotonic()
        deadline = started + 1.0
        words = terms(query)
        results = []
        # Project solutions and documents precede global preferences.
        project = ProjectMemory(self.data,self.id,self.workspace).search(query,max(0,budget- min(1000,budget//4)),current,automatic=automatic,exclude_paths=exclude_paths)
        # Explicit user notes alone may cross project boundaries. Source conversation
        # owns each note: deleting that conversation also deletes its shared memory.
        for folder in self.folders(all_projects=True):
            if time.monotonic()>deadline: break
            try:
                for note in markdown_notes(folder):
                    if note['scope']!='user': continue
                    if automatic and (note['status']!='declared' or not note['source'].strip()): continue
                    # User preferences apply even when the next request uses unrelated words.
                    # Equal priority puts the most recent declarations first.
                    score = 100 if note['scope']=='user' else len(words & terms(note['text']))
                    if score:
                        results.append((score, note['timestamp'], json.dumps(note,ensure_ascii=False)))
            except (OSError,ValueError,KeyError,TypeError): continue
        remaining=max(0,budget-len(project)-2)
        preferences='\n\n'.join(item[2] for item in sorted(results,reverse=True))[:remaining]
        return '\n\n'.join(p for p in [project,preferences] if p)[:budget]

    def read(self, conversation, reference, offset=0, length=3000):
        if reference.startswith(('note-','root-')) or (conversation=='project' and reference in {'notes','overview','index','regressions'}):
            return ProjectMemory(self.data,self.id,self.workspace).read(conversation,reference,offset,length)
        if not re.fullmatch(r'[A-Za-z0-9_-]+',conversation): raise ValueError('Conversation invalide.')
        folder = self.archive/conversation
        if folder.is_symlink() or not folder.is_dir(): raise ValueError('Conversation introuvable dans ce projet.')
        if any((folder/part).is_symlink() for part in ['memory','memory/documents']): raise ValueError('Mémoire symbolique refusée.')
        return ConversationMemory(folder).read(reference,offset,length)


def inventory(workspace, data_root, refresh=False):
    """No package execution, no network, no environment or credentials dump."""
    workspace = Path(workspace).resolve()
    cache = Path(data_root)/'environment'/f'{project_id(workspace)}.json'
    try: ttl = max(0,int(os.environ.get('TRINITY_AGENT_INVENTORY_TTL_SECONDS','300')))
    except ValueError: ttl = 300
    signature = (os.environ.get('PATH',''), str(workspace),platform.node())
    key = hashlib.sha256(repr(signature).encode()).hexdigest()
    try:
        saved = json.loads(read_bounded(cache,24000))
        if not refresh and saved['key']==key and time.time()-saved['observed_at']<ttl:
            return saved
    except (OSError,ValueError,KeyError): pass
    os_release = {}
    # /etc/os-release is commonly a distribution-owned symlink to /usr/lib.
    for line in read_bounded(Path('/etc/os-release').resolve(),4000).splitlines():
        if '=' in line:
            k,v=line.split('=',1)
            if k in {'ID','VERSION_ID','PRETTY_NAME'}: os_release[k]=v.strip('"')
    libc = platform.libc_ver()
    if not libc[0] and list(Path('/lib').glob('ld-musl-*.so.1')): libc=('musl','unknown')
    local_bins=[str(workspace/'.agent-tools/venv/bin'),str(workspace/'.agent-tools/npm/node_modules/.bin')]
    tool_path = os.pathsep.join(local_bins + [os.environ.get('PATH','')])
    paths = {name:shutil.which(name, path=tool_path) for name in TOOLS}
    local_tools = {}
    for folder in local_bins:
        directory=Path(folder)
        if directory.is_dir() and not directory.is_symlink():
            for item in list(directory.iterdir())[:100]:
                if item.is_file() and os.access(item,os.X_OK): local_tools[item.name]=str(item)
    facts = {'key':key,'observed_at':time.time(),'status':'observed','target':'IDE process (not mounted hosts)',
             'os':os_release,'kernel':platform.system()+' '+platform.release(),'architecture':platform.machine(),
             'libc':list(libc),'container_hint':Path('/.dockerenv').exists() or Path('/run/.containerenv').exists(),
             'uid':os.geteuid(),'workspace_writable':os.access(workspace,os.W_OK),'tools':paths,
             'project_tool_bins':local_bins,'project_tools':local_tools,'python':sys.version.split()[0],
             'installation_policy':os.environ.get('TRINITY_AGENT_AUTO_INSTALL','project'),
             'limits':'Executable paths prove presence only; versions and operational readiness must be checked when needed. Mounts are not execution targets.'}
    cache.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    atomic_write(cache,json.dumps(facts,ensure_ascii=False));cache.chmod(0o600)
    return facts


def install_plan(workspace, ecosystem, packages, scope='project'):
    if not packages or len(packages)>12 or any(not re.fullmatch(r'(?:@[a-z0-9_.-]+/)?[A-Za-z0-9][A-Za-z0-9_.-]*(?:(?:==|@)[A-Za-z0-9_.+-]+)?',p) for p in packages):
        raise ValueError('Noms de paquets uniquement, versions exactes facultatives ; aucun chemin, URL ou argument shell.')
    root=Path(workspace).resolve()/'.agent-tools'
    if root.is_symlink(): raise ValueError('Dossier outils symbolique refusé.')
    if scope=='project' and ecosystem=='python':
        venv=root/'venv'
        if venv.is_symlink() or (venv/'bin').is_symlink(): raise ValueError('Environnement symbolique refusé.')
        return [[sys.executable,'-m','venv',str(venv)], [str(venv/'bin/python'),'-m','pip','install','--disable-pip-version-check','--',*packages]] if not (venv/'bin/python').exists() else [[str(venv/'bin/python'),'-m','pip','install','--disable-pip-version-check','--',*packages]]
    if scope=='project' and ecosystem=='npm':
        if (root/'npm').is_symlink(): raise ValueError('Environnement symbolique refusé.')
        executable=shutil.which('npm')
        if not executable: raise ValueError('npm absent ; installer le runtime via la politique système.')
        return [[executable,'install','--prefix',str(root/'npm'),'--ignore-scripts','--no-audit','--no-fund','--',*packages]]
    if scope=='system' and ecosystem=='system':
        for name,args in [('apk',['add','--']),('apt-get',['install','-y','--']),('dnf',['install','-y','--'])]:
            executable=shutil.which(name)
            if executable: return [[executable,*args,*packages]]
        raise ValueError('Aucun gestionnaire système pris en charge disponible.')
    raise ValueError('Installation prise en charge : project/python, project/npm, system/system.')
