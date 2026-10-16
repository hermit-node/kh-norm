"""Domain-scoped, persistent page scripts for the existing Qt WebEngine profile.

Approval is local human UI only; RPC callers cannot grant domains. No bridge is
exposed to arbitrary website scripts. Script execution checks both the page's
current origin and the persisted exact origin allowlist.
"""
from __future__ import annotations
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from PySide6.QtWebEngineCore import QWebEngineScript

NAME = re.compile(r'^[A-Za-z0-9_.-]{1,80}$')

def origin_from_url(value: str) -> str:
    parsed=urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Only HTTPS websites may receive approved scripts')
    if parsed.port not in (None,443):
        raise ValueError('Nonstandard HTTPS ports are not supported by domain scripting')
    return 'https://'+parsed.hostname.lower()

class ScriptRegistry:
    def __init__(self, profile, root: Path):
        self.profile=profile
        self.root=Path(root)/'cooperation'
        self.registry_file=self.root/'scripts.json'
        self.approvals_file=self.root/'approved_origins.json'
        self.root.mkdir(parents=True,exist_ok=True)
        self.approved=set(self._read(self.approvals_file,[]))
        self.definitions=self._read(self.registry_file,{})
        self._installed={}
        for name in tuple(self.definitions):
            try:self._install(name,self.definitions[name])
            except Exception:pass

    @staticmethod
    def _read(file,default):
        try:return json.loads(file.read_text(encoding='utf8'))
        except (OSError,ValueError):return default

    @staticmethod
    def _save(file,record):
        tmp=file.with_suffix('.tmp')
        with tmp.open('w',encoding='utf8') as f:
            json.dump(record,f,indent=2,ensure_ascii=False)
            f.flush();os.fsync(f.fileno())
        tmp.replace(file)

    def allowed(self,url):
        try:return origin_from_url(url) in self.approved
        except ValueError:return False

    def approve_human(self,url):
        origin=origin_from_url(url)
        self.approved.add(origin)
        self._save(self.approvals_file,sorted(self.approved))
        for name,definition in self.definitions.items():
            if definition['origin']==origin:self._install(name,definition)
        return origin

    def revoke_human(self,url):
        origin=origin_from_url(url)
        self.approved.discard(origin)
        self._save(self.approvals_file,sorted(self.approved))
        for name,definition in self.definitions.items():
            if definition['origin']==origin:self._uninstall(name)
        return origin

    def _uninstall(self,name):
        script=self._installed.pop(name,None)
        if script is not None:self.profile.scripts().remove(script)

    def _install(self,name,definition):
        self._uninstall(name)
        if definition['origin'] not in self.approved:return
        script=QWebEngineScript()
        script.setName('Norm:'+name)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld if definition['world']=='main' else
                          QWebEngineScript.ScriptWorldId.ApplicationWorld)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation if definition['timing']=='creation' else
                                 QWebEngineScript.InjectionPoint.DocumentReady)
        script.setRunsOnSubFrames(False)
        # Check the exact origin inside the injected script; Qt scripts() is profile-wide.
        script.setSourceCode('if (location.origin === '+json.dumps(definition['origin'])+') {\n'+definition['source']+'\n}')
        self.profile.scripts().insert(script)
        self._installed[name]=script

    def register(self,name,origin,source,world='isolated',timing='ready'):
        if not NAME.fullmatch(name):raise ValueError('Invalid script name')
        if origin not in self.approved:raise PermissionError('Approve domain in browser UI first')
        if not isinstance(source,str) or not source.strip() or len(source)>200000:
            raise ValueError('Script must be 1–200,000 characters')
        if world not in ('main','isolated') or timing not in ('creation','ready'):
            raise ValueError('Unsupported script execution world or timing')
        record={'origin':origin,'source':source,'world':world,'timing':timing}
        self._install(name,record)
        self.definitions[name]=record
        self._save(self.registry_file,self.definitions)
        return {'name':name,'origin':origin,'world':world,'timing':timing,
                'note':'Registered for subsequent page loads. Already-loaded page not reloaded.'}

    def remove(self,name):
        if name not in self.definitions:raise KeyError('Unknown registered script')
        self._uninstall(name)
        del self.definitions[name]
        self._save(self.registry_file,self.definitions)
        return {'removed':name,'note':'Already-executed hooks may persist until page reload.'}

    def list_scripts(self):
        return {'approved_origins':sorted(self.approved),
                'scripts':[{'name':n, 'origin':s['origin'],'world':s['world'],'timing':s['timing']}
                           for n,s in self.definitions.items()]}
