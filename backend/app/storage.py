from __future__ import annotations
import json,sqlite3,os
from pathlib import Path
from collections import defaultdict
from .models import team

class Store:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS games(id TEXT PRIMARY KEY, created_at TEXT, status TEXT, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS benchmarks(id INTEGER PRIMARY KEY, data TEXT NOT NULL);
            ''')
        os.chmod(self.path,0o600)
    def db(self):return sqlite3.connect(self.path)
    def save(self,s):
        with self.db() as db:db.execute('INSERT OR REPLACE INTO games VALUES(?,?,?,?)',(s['id'],s['created_at'],s['status'],json.dumps(s,ensure_ascii=False)))
    def get(self,gid):
        with self.db() as db:r=db.execute('SELECT data FROM games WHERE id=?',(gid,)).fetchone()
        return json.loads(r[0]) if r else None
    def all(self):
        with self.db() as db:rows=db.execute('SELECT data FROM games ORDER BY created_at DESC').fetchall()
        return [json.loads(r[0]) for r in rows]
    def recover(self):
        for s in self.all():
            if s['status'] in ('running','paused'):
                s['status']='interrupted';s['phase']='finished';self.save(s)
            elif s['status']=='completed' and s['phase']!='finished':
                s['phase']='finished';s['interviews_interrupted']=True;self.save(s)
    def setting(self,key,default=None):
        with self.db() as db:r=db.execute('SELECT data FROM settings WHERE key=?',(key,)).fetchone()
        return json.loads(r[0]) if r else default
    def set_setting(self,key,value):
        with self.db() as db:db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,json.dumps(value,ensure_ascii=False)))
    def benchmark(self,r):
        with self.db() as db:db.execute('INSERT INTO benchmarks(data) VALUES(?)',(json.dumps(r,ensure_ascii=False),))
    def benchmarks(self):
        with self.db() as db:rows=db.execute('SELECT data FROM benchmarks ORDER BY id DESC LIMIT 100').fetchall()
        return [json.loads(r[0]) for r in rows]
    def stats(self,mode='original'):
        records=defaultdict(list)
        for s in self.all():
            category='mixed' if s['mixed'] else s['mode_history'][0]
            if s['status']!='completed' or s.get('winner') not in ('town','mafia') or s['config']['provider']!='ollama' or category!=mode:continue
            for p in s['players']: records[(p['model'],p.get('digest'))].append((s,p))
        out=[]
        for (model,digest),items in records.items():
            wins=0;factions={};roles={};days=[];survived=0;correct=0;votes=0;police=0;calls=[];lengths=[]
            for s,p in items:
                won=s['winner']==team(p['role']);wins+=won;survived+=p['alive'];days.append(p['death_day'] or s['day'])
                for mapping,key in ((factions,team(p['role'])),(roles,p['role'])):
                    row=mapping.setdefault(key,{'games':0,'wins':0});row['games']+=1;row['wins']+=won
                for e in s['events']:
                    if e['actor']==p['id'] and e['kind']=='vote':
                        votes+=1;correct+=next(x for x in s['players'] if x['id']==e['target'])['role']=='mafia'
                    if e['actor']==p['id'] and e['kind']=='speech': lengths.append(len(e['text']))
                police+=sum(i['actor']==p['id'] and i['result']=='mafia' for i in s['investigations'])
                calls.extend(c for c in s['calls'] if c['actor']==p['id'] and not c.get('error'))
            mean=lambda x:round(sum(x)/len(x),2) if x else None
            out.append(dict(model=model,digest=digest,games=len(items),wins=wins,losses=len(items)-wins,win_rate=round(wins/len(items)*100,1),factions=factions,roles=roles,
                avg_survival=mean(days),last_alive_rate=round(survived/len(items)*100,1),vote_accuracy=round(correct/votes*100,1) if votes else None,vote_samples=votes,police_success=police,
                avg_seconds=mean([c['seconds'] for c in calls]),avg_tps=mean([c['tps'] for c in calls if c.get('tps') is not None]),avg_speech_length=mean(lengths)))
        return out
