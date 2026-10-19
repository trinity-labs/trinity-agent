"""Readable Markdown sources with disposable local compressed/RAM search indexes."""
from __future__ import annotations
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import tempfile
from urllib.parse import quote
import uuid
from datetime import datetime, timezone
from ovh_cache import CACHE, file_signature, read_text_cached, positive_env
from ovh_memory import atomic_write, terms

DISK_INDEX_BYTES = positive_env('OVH_AGENT_INDEX_CACHE_MB', 32) * 1024 * 1024
MAX_INDEX = DISK_INDEX_BYTES or 32 * 1024 * 1024
MAX_DOCUMENT_CHARS = max(1000, positive_env('OVH_AGENT_MEMORY_DOCUMENT_CHARS', 262144))
SCAN_SECONDS = positive_env('OVH_AGENT_MEMORY_SCAN_SECONDS', 5)
_export_lock = threading.Lock()


def safe_path(file, root):
    file, root = Path(file), Path(root)
    try:
        file.relative_to(root)
        return not any(p.is_symlink() for p in [file, *file.parents] if p != root and p.is_relative_to(root))
    except ValueError:
        return False


def markdown_note(note):
    fields = {key: note[key] for key in ('id','scope','status','source','conversation','project','timestamp')}
    return '---\n' + '\n'.join(key+': '+json.dumps(value,ensure_ascii=False) for key,value in fields.items()) + '\n---\n\n' + note['text'] + '\n'


def parse_note(text):
    if not text.startswith('---\n'): raise ValueError('En-tête Markdown absent.')
    header, body = text.removeprefix('---\n').split('\n---\n',1)
    values = {key:json.loads(value.strip()) for line in header.splitlines() for key,value in [line.split(':',1)]}
    if values['scope'] not in {'project','user'} or values['status'] not in {'declared','hypothesis'}:
        raise ValueError('Statut ou portée de note invalide.')
    if not re.fullmatch(r'[a-f0-9]{20}',values['id']) or not isinstance(values['timestamp'],(int,float)):
        raise ValueError('Identifiant ou date invalide.')
    if not all(isinstance(values[k],str) for k in ('source','conversation','project')): raise ValueError('Métadonnées invalides.')
    values['text']=body.strip()
    return values


def notes(folder, migrate=True):
    """One-time legacy import; after migration Markdown is the source of truth."""
    folder=Path(folder); target=folder/'memory/notes'
    if folder.is_symlink() or (folder/'memory').is_symlink() or target.is_symlink(): return []
    legacy=folder/'shared-notes.json'; marker=target/'.migrated'
    if migrate and legacy.is_file() and not legacy.is_symlink() and not marker.exists():
        if (folder/'.notes.lock').is_symlink(): return []
        with (folder/'.notes.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            if not marker.exists():
                try: previous=json.loads(read_text_cached(legacy,limit=200000))
                except (OSError,ValueError): previous=[]
                if not isinstance(previous,list): previous=[]
                target.mkdir(parents=True,exist_ok=True,mode=0o700)
                for note in previous:
                    if not isinstance(note,dict): continue
                    if not re.fullmatch(r'[a-f0-9]{20}',str(note.get('id',''))): continue
                    file=target/(note['id']+'.md')
                    if not file.exists() and not file.is_symlink():
                        try: atomic_write(file,markdown_note(parse_note(markdown_note(note))))
                        except (ValueError,KeyError,TypeError): continue
                atomic_write(marker,'Markdown is authoritative. Legacy JSON retained for recovery.\n')
    result=[]
    for file in target.glob('*.md'):
        if file.is_symlink(): continue
        try:
            note=parse_note(read_text_cached(file,limit=16000))
            if note['id']==file.stem:
                if note['project']!=folder.parent.name: note={**note,'original_project':note['project'],'project':folder.parent.name}
                result.append(note)
        except (OSError,ValueError,KeyError,TypeError): continue
    return result


class ProjectMemory:
    def __init__(self,data_root,project,workspace=''):
        if not re.fullmatch(r'[a-f0-9]{20}',project): raise ValueError('Identifiant projet invalide.')
        self.data=Path(data_root).resolve();self.project=project;self.workspace=str(workspace)
        self.archive=self.data/'archive'/project
        self.root=self.data/'memory/projects'/project
        namespace=hashlib.sha256(str(self.data).encode()).hexdigest()[:20]
        local=Path((os.environ.get('OVH_AGENT_MEMORY_INDEX_DIR') or str(Path(os.environ.get('XDG_CACHE_HOME',str(Path.home()/'.cache')))/'ovh-agent/project-memory')))
        self.cache=local/namespace/project
        self.index_file=self.cache/'index.json.gz'
        self.revision=self.root/'.revision'
        if not safe_path(self.root,self.data) or not safe_path(self.archive,self.data): raise ValueError('Dossier mémoire symbolique refusé.')
        if not self.workspace:
            try: self.workspace=json.loads(read_text_cached(self.root/'project.json'))['workspace']
            except (OSError,ValueError,KeyError,TypeError): pass
        if self.workspace:
            self.workspace=str(Path(self.workspace).resolve())
            if hashlib.sha256(self.workspace.encode()).hexdigest()[:20]!=self.project:
                raise ValueError('Le dossier ne correspond pas au projet mémoire.')


    @classmethod
    def for_conversation(cls,folder):
        folder=Path(folder).resolve()
        if folder.parent.parent.name!='archive': return None
        if not re.fullmatch(r'[a-f0-9]{20}',folder.parent.name): return None
        return cls(folder.parent.parent.parent,folder.parent.name)

    def changed(self):
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        atomic_write(self.revision,uuid.uuid4().hex)

    def invalidate(self):
        self.changed()
        for file in [self.root/'MEMORY.md',self.root/'INDEX.md',self.root/'REGRESSIONS.md',self.data/'memory/AGGREGATE.md',self.index_file]:
            file.unlink(missing_ok=True)

    def generation(self):
        try: return read_text_cached(self.revision,limit=64)
        except FileNotFoundError: return ''

    def initialise(self):
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        file=self.root/'NOTES.md'
        if not file.exists():
            try:
                with file.open('x',encoding='utf-8') as stream:
                    stream.write('# Notes durables du projet\n\n<!-- Ajouter ici des décisions et faits sourcés. Ce fichier est conservé, jamais régénéré. -->\n')
                file.chmod(0o600)
            except FileExistsError: pass
        metadata=self.root/'project.json'
        if self.workspace and not metadata.is_symlink():
            value=json.dumps({'id':self.project,'workspace':self.workspace},ensure_ascii=False)
            if not metadata.exists() or read_text_cached(metadata)!=value: atomic_write(metadata,value)

    def root_documents(self):
        if not self.workspace: return []
        root=Path(self.workspace)
        try: return sorted(p for p in root.iterdir() if p.suffix.lower()=='.md' and p.is_file() and not p.is_symlink())
        except OSError: return []

    def source_file(self,row):
        return Path(row['path']) if row['kind']=='project-document' else self.data/row['path']

    def source_link(self,row):
        file=self.source_file(row)
        return file.as_uri() if row['kind']=='project-document' else quote(os.path.relpath(file,self.root),safe='/.-_')

    def sources(self):
        self.initialise()
        if not (self.root/'NOTES.md').is_symlink():
            yield self.root/'NOTES.md','project','notes','manual',None
        for file in self.root_documents():
            yield file,'project','root-'+hashlib.sha256(file.name.encode()).hexdigest()[:20],'project-document',None
        if not self.archive.is_dir() or self.archive.is_symlink(): return
        folders=[]
        for folder in sorted(self.archive.iterdir(),key=lambda p:p.stat().st_mtime,reverse=True):
            if not folder.is_dir() or folder.is_symlink() or not re.fullmatch(r'[A-Za-z0-9_-]+',folder.name): continue
            # Evaluation archives intentionally contain wrong model answers.
            # Keep them browsable without promoting them into project knowledge.
            try:
                if json.loads(read_text_cached(folder/'meta.json',limit=16000)).get('evaluation') is True:
                    continue
            except (OSError, ValueError, TypeError, AttributeError):
                pass
            if any((folder/part).is_symlink() for part in ['memory','memory/documents','memory/notes']): continue
            folders.append(folder)
            for note in notes(folder):
                yield folder/'memory/notes'/f"{note['id']}.md",folder.name,'note-'+note['id'],'note',note
        # Curated notes take priority over bulk transcripts if the index reaches its cap.
        for folder in folders:
            for file in sorted((folder/'memory/documents').glob('*.md')):
                if not file.is_symlink() and re.fullmatch(r'[a-f0-9]{64}',file.stem):
                    yield file,folder.name,file.stem,'archive',None

    def index(self,refresh=False):
        generation=self.generation();key=('project-markdown-v4',str(self.data),self.project)
        signature=(generation, MAX_DOCUMENT_CHARS, int(time.time()/max(1,SCAN_SECONDS)))
        cached=None if refresh else CACHE.get(key,signature)
        if cached is not None: return cached
        self.cache.mkdir(parents=True,exist_ok=True,mode=0o700)
        with (self.cache/'index.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            previous={}
            try:
                if self.index_file.stat().st_size<=MAX_INDEX:
                    with gzip.open(self.index_file,'rt',encoding='utf-8') as stream: payload=json.loads(stream.read(MAX_INDEX+1))
                    if isinstance(payload,dict) and payload.get('version')==4 and payload.get('document_chars')==MAX_DOCUMENT_CHARS:
                        previous={r['path']:r for r in payload['rows']}
            except (OSError,ValueError,KeyError,TypeError,EOFError): pass
            rows=[];used=0;partial=False
            for file,conversation,reference,kind,note in self.sources():
                try:
                    stamp=list(file_signature(file));relative=str(file) if kind=='project-document' else str(file.relative_to(self.data))
                    old=previous.get(relative)
                    valid = old and all(k in old for k in ('path','conversation','reference','kind','body','tokens','scope','status','timestamp','partial'))
                    valid = valid and isinstance(old['body'],str) and isinstance(old['tokens'],list) and all(isinstance(t,str) for t in old['tokens']) and isinstance(old['timestamp'],(int,float))
                    if valid and old.get('signature')==stamp: row=old
                    else:
                        raw=read_text_cached(file,limit=MAX_DOCUMENT_CHARS+1)
                        body=note['text'] if note else raw[:MAX_DOCUMENT_CHARS]
                        tokens=terms(body)
                        if kind=='archive':
                            try:
                                event=json.loads(body)
                                if isinstance(event,dict) and event.get('type')=='verification_check': tokens |= terms('tests régression régressions regression regressions vérification vérifications')
                            except ValueError: pass
                        row={'path':relative,'conversation':conversation,'reference':reference,'kind':kind,
                             'body':body,'tokens':sorted(tokens),'signature':stamp,'partial':len(raw)>MAX_DOCUMENT_CHARS,
                             'scope':note['scope'] if note else 'project','status':note['status'] if note else 'historical',
                             'timestamp':note['timestamp'] if note else file.stat().st_mtime}
                    if row['partial']: partial=True
                    size=len(json.dumps(row,ensure_ascii=False).encode())
                    if used+size>MAX_INDEX-4096: partial=True;break
                    used+=size;rows.append(row)
                except (OSError,ValueError): continue
            payload={'version':4,'document_chars':MAX_DOCUMENT_CHARS,'generation':generation,'rows':rows,'partial':partial}
            encoded=json.dumps(payload,ensure_ascii=False).encode()
            if self.generation()!=generation: return self._compile(rows,True)
            if DISK_INDEX_BYTES:
                descriptor,name=tempfile.mkstemp(prefix='.index-',dir=self.cache)
                temporary=Path(name)
                try:
                    with os.fdopen(descriptor,'wb') as stream: stream.write(gzip.compress(encoded,compresslevel=1))
                    os.replace(temporary,self.index_file)
                    if self.generation()!=generation:
                        self.index_file.unlink(missing_ok=True)
                        return self._compile(rows,True)
                finally: temporary.unlink(missing_ok=True)
            else: self.index_file.unlink(missing_ok=True)
            result=self._compile(rows,partial)
            CACHE.put(key,signature,result,max(1024,len(encoded)*8))
            return result

    @staticmethod
    def _compile(rows,partial):
        inverted={}
        for i,row in enumerate(rows):
            for token in row['tokens']: inverted.setdefault(token,set()).add(i)
        return {'rows':rows,'inverted':inverted,'partial':partial}

    def current(self,row):
        file=self.source_file(row)
        try:
            if row['kind']=='project-document':
                return bool(self.workspace) and file.parent==Path(self.workspace) and file.suffix.lower()=='.md' and not file.is_symlink() and list(file_signature(file))==row['signature']
            if not safe_path(file,self.data): return False
            if not (file.is_relative_to(self.archive) or file == self.root/'NOTES.md'): return False
            return list(file_signature(file))==row['signature']
        except OSError: return False

    def answer(self, query, deadline=None):
        """Extract an exact documented question, never infer an answer from similarity.

        Only authoritative Markdown files are eligible, not generated summaries or
        assistant transcripts. The result is labelled as a document excerpt, not
        as a new verification of running code. A partial scan fails closed.
        """
        from ovh_request import analyze_request, normalized
        question = ' '.join(query.split()).strip()
        analysis = analyze_request(question)
        if ('\n' in query or len(question) > 500 or question.count('?') != 1 or not question.endswith('?')
                or analysis.fact_check or analysis.needs_web or analysis.intent != 'question'
                or not re.match(r"^(?:comment|quel(?:le)?s?|où|pourquoi|what|which|where|how)\b", question, re.I)
                or re.search(r"\b(?:actuel\w*|aujourd|maintenant|verifi\w*|confirm\w*|recent\w*|latest|current|today|check|verify|internet|web|execute|lance|puis)\b", normalized(question))):
            return None
        deadline = deadline or time.monotonic() + 0.8
        files = self.root_documents()
        manual = self.root/'NOTES.md'
        if manual.is_file() and safe_path(manual,self.data): files.append(manual)
        if len(files) > 64: return None
        key = question.casefold()
        matches = []
        def allowed(file):
            return safe_path(file,Path(self.workspace)) if file != manual else safe_path(file,self.data)
        for file in files:
            if time.monotonic() >= deadline: return None
            try:
                if not allowed(file): return None
                stamp = file_signature(file)
                if file.stat().st_size > MAX_DOCUMENT_CHARS: return None
                text = read_text_cached(file,limit=MAX_DOCUMENT_CHARS)
                if not allowed(file) or file_signature(file) != stamp: return None
            except (OSError, ValueError): return None
            heading = ''; body = []; fence = None
            sections = []
            for line in text.splitlines():
                marker = re.match(r'^ {0,3}(`{3,}|~{3,})',line)
                if marker:
                    token=marker[1]
                    if fence is None: fence=token
                    elif token[0]==fence[0] and len(token)>=len(fence): fence=None
                title = re.match(r'^#{1,6}\s+(.+?)\s*#*$',line) if fence is None and not marker else None
                if title:
                    sections.append((heading,'\n'.join(body).strip()))
                    heading = re.sub(r'^(?:Q(?:uestion)?\s*:\s*)','',title[1],flags=re.I)
                    heading = ' '.join(heading.split()).casefold(); body=[]
                else: body.append(line)
            sections.append((heading,'\n'.join(body).strip()))
            for title, answer in sections:
                if title == key and answer:
                    if len(answer)>4000: return None
                    matches.append({'answer':answer,'source':str(file),'signature':list(stamp),
                                    'modified':datetime.fromtimestamp(file.stat().st_mtime,timezone.utc).isoformat()})
        if time.monotonic() >= deadline or not matches or len({m['answer'] for m in matches}) != 1:
            return None
        # Recheck every matching source after scanning, including conflicting aliases.
        if any(not allowed(Path(m['source'])) or list(file_signature(m['source']))!=m['signature'] for m in matches): return None
        match=matches[0]
        match['text'] = ('Réponse extraite de la mémoire du projet, sans appel au modèle.\n\n'
                         + '\n'.join('> '+line for line in match['answer'].splitlines())
                         + f"\n\nSource : [{Path(match['source']).name}]({Path(match['source']).as_uri()})"
                         + f" · modifiée le {match['modified']}.\nExtrait documentaire ; aucune nouvelle vérification exécutée.")
        return match

    def search(self,query,budget=4000,current=''):
        if budget<=0:return ''
        index=self.index();words=terms(query);inverted=index['inverted']
        candidates=set().union(*(inverted.get(word,set()) for word in words))
        ranked=sorted(candidates,key=lambda i:(index['rows'][i]['kind'] in {'manual','project-document','note'},len(words.intersection(index['rows'][i]['tokens'])),index['rows'][i]['timestamp']),reverse=True)
        parts=[];used=0
        for i in ranked:
            row=index['rows'][i]
            if row['conversation']==current and row['kind']=='archive':continue
            if not self.current(row):continue
            text=row['body']
            first=next((m.start() for m in re.finditer(r'[\w-]{3,}',text) if m.group().casefold() in words),0)
            start=max(0,first-180)
            excerpt=text[start:start+1000]
            header=f"[{'Recherche partielle ; ' if index['partial'] else ''}Projet {self.project}; conversation {row['conversation']}; référence {row['reference']}; offset={start}; statut={row['status']}; source={row['path']}]\n"
            remaining=budget-used
            if remaining<=len(header)+40:break
            part=(header+excerpt)[:remaining];parts.append(part);used+=len(part)+2
            if len(parts)>=5:break
        return '\n\n'.join(parts)[:budget]

    def read(self,conversation,reference,offset=0,length=3000):
        if offset<0 or length<1:raise ValueError('Plage invalide.')
        if conversation=='project' and reference=='notes':file=self.root/'NOTES.md';kind='manual'
        elif conversation=='project' and reference in {'overview','index','regressions'}:
            file=self.root/{'overview':'MEMORY.md','index':'INDEX.md','regressions':'REGRESSIONS.md'}[reference];kind='generated'
        elif conversation=='project' and re.fullmatch(r'root-[a-f0-9]{20}',reference):
            file=next((p for p in self.root_documents() if 'root-'+hashlib.sha256(p.name.encode()).hexdigest()[:20]==reference),None);kind='project-document'
            if file is None: raise ValueError('Document projet introuvable.')
        elif re.fullmatch(r'[A-Za-z0-9_-]+',conversation) and re.fullmatch(r'note-[a-f0-9]{20}',reference):
            file=self.archive/conversation/'memory/notes'/(reference[5:]+'.md');kind='note'
        else: raise ValueError('Référence Markdown invalide.')
        if kind!='project-document' and not safe_path(file,self.data):raise ValueError('Source invalide.')
        if kind=='generated':
            try: self.materialize()
            except RuntimeError as error: raise ValueError(str(error)) from error
        raw=read_text_cached(file);text=parse_note(raw)['text'] if kind=='note' else raw
        end=min(len(text),offset+min(length,4000))
        return f'[{file}; caractères {offset}:{end}/{len(text)}]\n'+text[offset:end]

    def materialize(self):
        self.initialise();generation=self.generation();index=self.index(refresh=True)
        rows=[r for r in index['rows'] if self.current(r)]
        title=self.workspace or self.project
        if not self.workspace:
            try: title=json.loads(read_text_cached(self.root/'project.json'))['workspace']
            except (OSError,ValueError,KeyError): pass
        memory=[f'# Mémoire du projet - {title}', '', '<!-- Généré depuis les sources Markdown. Modifier NOTES.md ou les notes sources. -->',
                '', 'Les archives sont historiques. Une vérification passée ne certifie pas les fichiers actuels.', '']
        links=['# Index des sources Markdown', '', f'Projet : {title}', '']
        for row in rows:
            link=self.source_link(row)
            links.append(f"- [{row['conversation']} / {row['reference']}]({link}) - {row['kind']}")
            if row['kind']=='project-document':
                memory += [f"## Document du projet : {Path(row['path']).name}",f"Chemin : {row['path']}",f'[Source]({link})','',row['body'][:4000],'']
            if row['kind'] in {'manual','note'}:
                memory += [f"## {row['conversation']} / {row['reference']}",f"Portée : {row['scope']} · Statut : {row['status']} · Date Unix : {row['timestamp']}",f'[Source]({link})','',row['body'],'']
        recent=sorted((r for r in rows if r['kind']=='archive' and r['body'].startswith(('response:', 'user:'))),key=lambda r:r['timestamp'],reverse=True)[:6]
        if recent: memory += ['## Repères récents - extraits exacts', '', 'Extraits historiques limités à 1 200 caractères ; consulter les sources complètes.', '']
        for row in recent:
            link=self.source_link(row)
            memory += [f"### Conversation {row['conversation']}", f"[Source complète]({link}) · Date Unix : {row['timestamp']}",'',row['body'][:1200],'']
        regressions=['# Vérifications historiques du projet', '', 'Les 40 contrôles les plus récents des sources indexées. Résultats observés à leur date d’exécution, jamais une certification des fichiers actuels.', '']
        receipts=[]
        for row in rows:
            if row['kind']!='archive': continue
            try: event=json.loads(row['body'])
            except (ValueError,TypeError): continue
            if isinstance(event,dict) and event.get('type')=='verification_check': receipts.append((row,event))
        for row,event in sorted(receipts,key=lambda pair:pair[0]['timestamp'],reverse=True)[:40]:
            passed=event.get('passed');verdict='réussi' if passed is True else 'échoué' if passed is False else 'non déterminé'
            regressions += [f"## {event.get('check',event.get('name','Contrôle'))} - {verdict}", f"Date : {event.get('timestamp',row['timestamp'])} · Conversation : {row['conversation']} · Code de sortie : {event.get('exit_code','inconnu')}",f"[Source]({self.source_link(row)})",'']
        if not receipts: regressions += ['Aucun résultat de vérification archivé dans les sources indexées.']
        memory += ['## Historique des contrôles', '[Source](REGRESSIONS.md)', '', 'Relancer les contrôles pertinents sur les fichiers actuels avant de conclure.', '']
        if index['partial']:memory+=['Index partiel : limite de cache atteinte. Les sources originales restent disponibles.']
        for name,lines in [('MEMORY.md',memory),('INDEX.md',links),('REGRESSIONS.md',regressions)]:
            content='\n'.join(lines)+'\n';file=self.root/name
            if self.generation()!=generation: raise RuntimeError('Sources modifiées pendant l’agrégation ; réessayer.')
            atomic_write(file,content)
            if self.generation()!=generation:
                if read_text_cached(file)==content:file.unlink(missing_ok=True)
                raise RuntimeError('Sources modifiées pendant l’agrégation ; réessayer.')
        return {'project':self.project,'memory':str(self.root/'MEMORY.md'),'index':str(self.root/'INDEX.md'),'notes':str(self.root/'NOTES.md'),'regressions':str(self.root/'REGRESSIONS.md'),'documents':len(rows),'partial':index['partial']}


def schedule_export(data_root,project,workspace):
    if not _export_lock.acquire(blocking=False):return
    def run():
        try:ProjectMemory(data_root,project,workspace).materialize()
        except (OSError,ValueError,RuntimeError):pass
        finally:_export_lock.release()
    threading.Thread(target=run,daemon=True,name='ovh-markdown-export').start()


def aggregate(data_root,projects):
    if not projects:raise ValueError('Choisir au moins un projet.')
    data=Path(data_root).resolve();parts=['# Mémoires des projets sélectionnés','', 'Export explicite ; les périmètres et sources restent séparés.','']
    # A registered folder may have no conversation yet. Absence of an archive
    # is not proof that the project identifier is unknown.
    try:
        registry=json.loads((data/'projects.json').read_text())
        registered={row['id'] for row in registry if isinstance(row,dict) and isinstance(row.get('id'),str)} if isinstance(registry,list) else set()
    except (OSError,ValueError):
        registered=set()
    reports=[];generations=[]
    for project in dict.fromkeys(projects):
        memory=ProjectMemory(data,project)
        if not memory.archive.is_dir() and not memory.root.is_dir():
            if project not in registered: raise ValueError('Projet inconnu : '+project)
            parts += [f'## Projet {project}', '', 'Aucune mémoire ni conversation enregistrée pour ce projet.', '']
            reports.append({'project':project,'documents':0,'empty':True})
            continue
        generation=memory.generation()
        report=memory.materialize();reports.append(report);generations.append((memory,generation))
        text=read_text_cached(Path(report['memory']))
        # Keep links valid after moving the composed document up two directories.
        text=re.sub(r'^(\[Source(?: complète)?\])\(([^)]+)\)',lambda m:m.group(0) if re.match(r'(?:[A-Za-z][A-Za-z0-9+.-]*:|/|#)',m.group(2)) else m.group(1)+'(projects/'+project+'/'+m.group(2)+')',text,flags=re.M)
        parts += [f'## Projet {project}','',text,'']
    target=data/'memory/AGGREGATE.md';content='\n'.join(parts)
    if any(m.generation()!=g for m,g in generations): raise RuntimeError('Sources modifiées ; réessayer.')
    atomic_write(target,content)
    if any(m.generation()!=g for m,g in generations):
        if read_text_cached(target)==content: target.unlink(missing_ok=True)
        raise RuntimeError('Sources modifiées ; réessayer.')
    return {'file':str(target),'projects':reports}


if __name__ == '__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Agréger les mémoires Markdown sans appel au modèle.')
    parser.add_argument('--data-root',default=os.environ.get('OVH_AGENT_DATA_ROOT',str(Path.home()/'.ovh-agent')))
    parser.add_argument('--workspace',action='append',required=True,help='Dossier projet ; répétable pour agréger plusieurs projets.')
    args=parser.parse_args()
    projects=[hashlib.sha256(str(Path(w).resolve()).encode()).hexdigest()[:20] for w in args.workspace]
    for w,p in zip(args.workspace,projects): ProjectMemory(args.data_root,p,w).initialise()
    print(json.dumps(aggregate(args.data_root,projects),ensure_ascii=False,indent=2))
