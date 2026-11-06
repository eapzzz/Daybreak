"""Private configuration, atomic writes and durable execution ledger."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from datetime import datetime, timezone
import uuid
from .model import Settings, validate


def atomic_json(path:Path, value:dict) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.write-',dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(value,stream,indent=2,ensure_ascii=False); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp,path)
        dfd=os.open(path.parent,os.O_DIRECTORY)
        try: os.fsync(dfd)
        finally: os.close(dfd)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


class SettingsFiles:
    def __init__(self,directory:Path):
        self.directory=Path(directory); self.directory.mkdir(parents=True,exist_ok=True)
        self.path=self.directory/'settings.json'
    def load(self)->Settings:
        if not self.path.exists(): return Settings()
        return Settings.from_dict(json.loads(self.path.read_text()))
    def token(self)->str:
        p=self.directory/'credentials'
        return json.loads(p.read_text()).get('token','') if p.exists() else ''
    def save(self,s:Settings,token:str|None=None)->None:
        validate(s)
        if token is not None and (not isinstance(token,str) or '\n' in token or len(token)>8192):
            raise ValueError('Invalid token.')
        # Persist credentials before settings: a crash cannot expose a half-written file.
        if token: atomic_json(self.directory/'credentials',{'token':token.strip()})
        atomic_json(self.path,s.to_dict())
    def backup(self)->str:
        name=datetime.now(timezone.utc).strftime('settings-%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:6]+'.json'
        atomic_json(self.directory/'backups'/name,self.load().to_dict())
        return name
    def backups(self)->list[str]:
        return sorted((x.name for x in (self.directory/'backups').glob('settings-*.json')),reverse=True)
    def read_backup(self,name:str)->Settings:
        if name!=Path(name).name or name not in self.backups(): raise ValueError('Unknown configuration backup.')
        return Settings.from_dict(json.loads((self.directory/'backups'/name).read_text()))
    def restore(self,name:str)->None: self.save(self.read_backup(name))


class Store:
    def __init__(self,path:Path):
        self.db=sqlite3.connect(path); self.db.row_factory=sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(
                id TEXT PRIMARY KEY,kind TEXT NOT NULL,plan TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,reason TEXT NOT NULL DEFAULT '',
                needs_normal INTEGER NOT NULL DEFAULT 0,normal_service TEXT NOT NULL DEFAULT '',
                normal_alarm INTEGER NOT NULL DEFAULT 4,normal_ring INTEGER NOT NULL DEFAULT 3,
                created TEXT NOT NULL,completed_at TEXT);
            CREATE TABLE IF NOT EXISTS alarms(
                label TEXT PRIMARY KEY,session TEXT NOT NULL,due TEXT NOT NULL,
                notify_service TEXT NOT NULL,package TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'recorded',prepared INTEGER NOT NULL DEFAULT 0,
                last_attempt TEXT,seed INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,at TEXT NOT NULL,
                level TEXT NOT NULL,message TEXT NOT NULL);
        '''); self.db.commit()
    def close(self): self.db.close()
    def meta(self,key:str,default=None):
        r=self.db.execute('SELECT value FROM metadata WHERE key=?',(key,)).fetchone()
        return json.loads(r[0]) if r else default
    def set_meta(self,key:str,value):
        with self.db: self.db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)',(key,json.dumps(value)))
    def ensure_session(self,sid:str,plan:dict,kind:str='real'):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO sessions(id,kind,plan,created) VALUES (?,?,?,?)',
                            (sid,kind,json.dumps(plan),datetime.now(timezone.utc).isoformat()))
    def session(self,sid:str)->dict|None:
        r=self.db.execute('SELECT * FROM sessions WHERE id=?',(sid,)).fetchone()
        if not r: return None
        d=dict(r); d['plan']=json.loads(d['plan']); return d
    def sessions(self,limit:int=60)->list[dict]:
        return [self.session(r[0]) for r in self.db.execute('SELECT id FROM sessions ORDER BY created DESC LIMIT ?',(limit,))]
    def work_sessions(self)->list[dict]:
        # Pending work must never be truncated by the history display limit.
        return [self.session(r[0]) for r in self.db.execute(
            'SELECT id FROM sessions WHERE completed=0 OR needs_normal=1 ORDER BY created')]
    def replace_plan(self,sid:str,plan:dict):
        with self.db: self.db.execute('UPDATE sessions SET plan=? WHERE id=?',(json.dumps(plan),sid))
    def complete(self,sid:str,reason:str):
        with self.db:
            self.db.execute('UPDATE sessions SET completed=1,reason=?,completed_at=? WHERE id=?',
                            (reason,datetime.now(timezone.utc).isoformat(),sid))
            self.db.execute("UPDATE alarms SET status='cancel_pending' WHERE session=? AND status!='cancelled'",(sid,))
    def loud(self,sid:str,s:Settings):
        with self.db:
            self.db.execute('UPDATE sessions SET needs_normal=1,normal_service=?,normal_alarm=?,normal_ring=? WHERE id=?',
                            (s.notify_service,s.normal_alarm,s.normal_ring,sid))
    def normal_done(self,sid:str):
        with self.db: self.db.execute('UPDATE sessions SET needs_normal=0 WHERE id=?',(sid,))
    def record_alarm(self,label:str,sid:str,due:datetime,s:Settings,seed:bool=False):
        with self.db:
            self.db.execute('''INSERT INTO alarms(label,session,due,notify_service,package,seed,last_attempt)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(label) DO UPDATE SET
                status='recorded',last_attempt=excluded.last_attempt,seed=excluded.seed''',
                (label,sid,due.isoformat(),s.notify_service,s.alarm_package,int(seed),datetime.now(timezone.utc).isoformat()))
    def alarm(self,label:str)->dict|None:
        r=self.db.execute('SELECT * FROM alarms WHERE label=?',(label,)).fetchone(); return dict(r) if r else None
    def alarms(self,sid:str|None=None)->list[dict]:
        sql='SELECT * FROM alarms'; args=()
        if sid: sql+=' WHERE session=?'; args=(sid,)
        return [dict(r) for r in self.db.execute(sql+' ORDER BY due DESC',args)]
    def sent(self,label:str,prepared:bool=False):
        with self.db:
            # A concurrent tag/network event takes priority over late API success.
            self.db.execute("UPDATE alarms SET status=CASE WHEN status='cancel_pending' THEN status ELSE 'sent' END,prepared=MAX(prepared,?) WHERE label=?",(int(prepared),label))
    def cancel_all(self,sid:str|None=None):
        with self.db:
            sql="UPDATE alarms SET status='cancel_pending' WHERE status IN ('recorded','sent')"
            args=()
            if sid: sql+=' AND session=?'; args=(sid,)
            self.db.execute(sql,args)
    def cancelled(self,label:str):
        with self.db: self.db.execute("UPDATE alarms SET status='cancelled',prepared=0 WHERE label=?",(label,))
    def event(self,level:str,message:str):
        with self.db:
            self.db.execute('INSERT INTO events(at,level,message) VALUES (?,?,?)',
                            (datetime.now(timezone.utc).isoformat(),level,message[:2000]))
            self.db.execute('DELETE FROM events WHERE id <= (SELECT COALESCE(MAX(id),0)-5000 FROM events)')
    def events(self,limit:int=100)->list[dict]:
        return [dict(r) for r in self.db.execute('SELECT * FROM events ORDER BY id DESC LIMIT ?',(max(1,min(limit,5000)),))]
    def count(self,sid:str)->int:
        return self.db.execute('SELECT COUNT(*) FROM alarms WHERE session=?',(sid,)).fetchone()[0]
