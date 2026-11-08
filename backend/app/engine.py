"""Pure deterministic rules. Model output never controls state transitions."""
from __future__ import annotations
import random
import copy
import uuid
from collections import Counter
from datetime import datetime,timezone
from .models import GameConfig,Role,team,ROLE_NAMES

def now(): return datetime.now(timezone.utc).isoformat()

def winner(players):
    alive=[p for p in players if p['alive']]
    if not any(p['role']==Role.MAFIA for p in alive): return 'town'
    m=sum(team(p['role'])=='mafia' for p in alive)
    return 'mafia' if m>=len(alive)-m else None

def tally(votes,alive):
    for actor,target in votes.items():
        if actor not in alive or target not in alive or actor==target: raise ValueError('잘못된 투표')
    c=Counter(votes.values())
    if not c: return None
    high=max(c.values()); top=[p for p,n in c.items() if n==high]
    return top[0] if len(top)==1 else None

def investigate(role): return 'mafia' if role==Role.MAFIA else 'town'

def resolve_night(players,actions):
    alive={p['id']:p for p in players if p['alive']}
    attack_votes={a:t for a,t in actions.items() if a in alive and alive[a]['role']==Role.MAFIA}
    # Two mafias agree by majority; a tie is explicitly no attack, never a random choice.
    attacked=tally(attack_votes,list(alive))
    healed={t for a,t in actions.items() if a in alive and alive[a]['role']==Role.DOCTOR}
    guards=[(a,t) for a,t in actions.items() if a in alive and alive[a]['role']==Role.BODYGUARD]
    if not attacked or attacked in healed: return []
    victim=attacked
    for guard,target in guards:
        if target==attacked:
            victim=guard; break
    return [] if victim in healed else [victim]

class Engine:
    def __init__(self,config:GameConfig,series_id=None,index=0,roles=None):
        cfg=config.model_dump(mode='json')
        assigned=roles or [p.role.value for p in config.players]
        if roles is None and config.random_roles: random.shuffle(assigned)
        if index and config.rotate_roles: assigned=assigned[-index%6:]+assigned[:-index%6] if index%6 else assigned
        self.s={'id':str(uuid.uuid4()),'series_id':series_id or str(uuid.uuid4()),'series_index':index,
          'config':cfg,'initial_config':copy.deepcopy(cfg),'initial_roles':roles or assigned.copy(),'created_at':now(),'updated_at':now(),
          'status':'paused','phase':'discussion','day':1,'round':1,'cursor':0,'order':[],
          'revision':0,'winner':None,'events':[],'votes':{},'night_actions':{},'memories':{},
          'dead_usage':{},'investigations':[],'calls':[],'mode_history':[config.mode],'mixed':False,'admin_events':[],
          'players':[dict(id=p.id,name=p.name,model=p.model,role=r,alive=True,death_day=None,death_order=None,digest=None) for p,r in zip(config.players,assigned)]}
        self.set_order(); self.event('system','게임 시작 · 첫날 낮이 밝았습니다.')

    @classmethod
    def restore(cls,s):
        obj=object.__new__(cls);obj.s=s;return obj

    def player(self,pid): return next(p for p in self.s['players'] if p['id']==pid)
    def alive(self): return [p for p in self.s['players'] if p['alive']]
    def event(self,kind,text,actor=None,channel='public',**extra):
        event={'id':len(self.s['events'])+1,'at':now(),'day':self.s['day'],'phase':self.s['phase'],
               'kind':kind,'text':text,'actor':actor,'channel':channel,**extra}
        self.s['events'].append(event);return event
    def touch(self): self.s['revision']+=1;self.s['updated_at']=now()
    def set_order(self,ids=None):
        ids=ids if ids is not None else [p['id'] for p in self.alive()]
        if self.s['config']['order']=='random': random.shuffle(ids)
        elif ids:
            shift=(self.s['day']+self.s['round']-2)%len(ids);ids=ids[shift:]+ids[:shift]
        self.s['order']=ids;self.s['cursor']=0
    def phase(self,name,ids=None):
        self.s['phase']=name;self.set_order(ids)
    def kill(self,pid,cause):
        p=self.player(pid);p['alive']=False;p['death_day']=self.s['day']
        p['death_order']=sum(not x['alive'] for x in self.s['players'])
        role=f" · {ROLE_NAMES[p['role']]}" if self.s['config']['reveal_roles'] else ''
        self.event('death',f"{p['name']} {cause}{role}",target=pid,cause=cause,role=p['role'] if role else None)
    def check_win(self):
        w=winner(self.s['players'])
        if w:
            self.s['winner']=w;self.s['status']='completed';self.s['ended_at']=now()
            self.event('result',('시민' if w=='town' else '마피아')+' 진영 승리')
            self.phase('interview', [p['id'] for p in self.s['players']] if self.s['config']['interviews'] else [])
            return True
        return False
    def eligible(self,pid,kind):
        alive=[p['id'] for p in self.alive()]
        if kind=='vote': return [x for x in alive if x!=pid]
        if kind=='night':
            r=self.player(pid)['role']
            return [x for x in alive if (x!=pid or r==Role.DOCTOR) and (r!=Role.MAFIA or team(self.player(x)['role'])!='mafia')]
        return []
    def dead_queue(self):
        cfg=self.s['config']
        if not cfg['dead_chat']:return []
        return [p['id'] for p in self.s['players'] if not p['alive'] for _ in range(max(0,cfg['dead_reactions']-self.s['dead_usage'].get(str(self.s['day'])+':'+p['id'],0)))]
    def next_task(self):
        s=self.s;phase=s['phase']
        if s['cursor']>=len(s['order']): return None
        pid=s['order'][s['cursor']]
        kind={'discussion':'speech','defense':'speech','vote':'vote','mafia_chat':'secret','night':'night','dead':'dead','interview':'interview'}.get(phase)
        return {'actor':pid,'kind':kind,'eligible':self.eligible(pid,kind)} if kind else None
    def apply(self,task,result,meta):
        if task!=self.next_task():raise ValueError('이미 처리됐거나 현재 순서와 다른 행동입니다.')
        s=self.s;pid=task['actor'];kind=task['kind'];target=result.get('target');p=self.player(pid)
        if kind in ('speech','secret','vote','night') and not p['alive']:raise ValueError('사망자는 생존자 행동에 참여할 수 없습니다.')
        if kind=='dead' and p['alive']:raise ValueError('생존자는 사망자 채팅에 참여할 수 없습니다.')
        if kind=='night' and p['role'] not in (Role.MAFIA,Role.POLICE,Role.DOCTOR,Role.BODYGUARD):raise ValueError('사용할 밤 능력이 없습니다.')
        if kind=='secret' and team(p['role'])!='mafia':raise ValueError('마피아 진영만 비밀 대화에 참여할 수 있습니다.')
        if kind in ('vote','night') and target not in task['eligible']: raise ValueError('선택 대상이 유효하지 않습니다.')
        speech=result.get('speech','').strip()
        if kind in ('speech','secret','dead','interview') and not speech: raise ValueError('발언 또는 명시적인 침묵이 필요합니다.')
        channel={'secret':'mafia','dead':'dead','night':'private'}.get(kind,'public')
        if kind=='vote':
            s['votes'][pid]=target
            # Votes are revealed simultaneously, after every voter has chosen.
        elif kind=='night': s['night_actions'][pid]=target
        else: self.event(kind,speech,pid,channel,**meta)
        if kind=='dead':
            key=str(s['day'])+':'+pid;s['dead_usage'][key]=s['dead_usage'].get(key,0)+1
        mem=s['memories'].setdefault(pid,{'opinions':[]})
        if kind in ('speech','secret','dead','interview'):
            mem['own_statements']=(mem.get('own_statements',[])+[{'day':s['day'],'channel':channel,'text':speech}])[-4:]
        if result.get('memory'):
            mem['opinions']=(mem['opinions']+[{'day':s['day'],'note':result['memory'][:220]}])[-8:]
        s['cursor']+=1
    def advance(self):
        s=self.s;cfg=s['config'];phase=s['phase']
        if self.next_task(): raise ValueError('아직 처리하지 않은 AI 행동이 있습니다.')
        if phase=='discussion':
            if s['round']<cfg['rounds']: s['round']+=1;self.set_order()
            else: self.phase('defense' if cfg['last_words'] else 'vote')
        elif phase=='defense': self.phase('vote')
        elif phase=='vote':
            for a,t in s['votes'].items():
                self.event('vote',f"{self.player(a)['name']} → {self.player(t)['name']}",a,target=t)
            victim=tally(s['votes'],[p['id'] for p in self.alive()])
            if victim:self.kill(victim,'투표로 처형되었습니다.')
            else:self.event('tie','최다 득표 동률 · 처형 없이 밤으로 넘어갑니다.')
            s['votes']={}
            if not self.check_win():
                dead=self.dead_queue()
                self.phase('dead',dead);s['after_dead']='mafia_chat'
        elif phase=='dead':
            if s['after_dead']=='mafia_chat':
                self.phase('mafia_chat',[p['id'] for p in self.alive() if team(p['role'])=='mafia'])
                self.event('system','밤이 되었습니다. 비밀 행동이 진행됩니다.')
            else:
                s['day']+=1;s['round']=1
                if s['day']>cfg['max_days']:
                    s['status']='invalid';s['phase']='finished';self.event('system','최대 일수 초과 · 무효 경기')
                else:self.phase('discussion');self.event('system',f"Day {s['day']} · 낮이 밝았습니다.")
        elif phase=='mafia_chat':
            self.phase('night',[p['id'] for p in self.alive() if p['role'] in (Role.MAFIA,Role.POLICE,Role.DOCTOR,Role.BODYGUARD)])
        elif phase=='night':
            for a,t in s['night_actions'].items():
                if self.player(a)['role']==Role.POLICE:
                    s['investigations'].append({'actor':a,'target':t,'day':s['day'],'result':investigate(self.player(t)['role'])})
            victims=resolve_night(s['players'],s['night_actions'])
            s.setdefault('night_history',[]).append({'day':s['day'],'actions':s['night_actions'].copy()})
            for v in victims:self.kill(v,'밤에 사망했습니다.')
            if not victims:self.event('system','밤사이 사망자가 없습니다.')
            s['night_actions']={}
            if not self.check_win():
                dead=self.dead_queue()
                self.phase('dead',dead);s['after_dead']='day'
        elif phase=='interview':self.phase('finished',[])
    def public(self):
        s=self.s;ended=s['winner'] is not None
        players=[{k:v for k,v in p.items() if k!='role' or ended or (not p['alive'] and s['config']['reveal_roles'])} for p in s['players']]
        return {k:s[k] for k in ('id','series_id','series_index','created_at','status','phase','day','round','revision','winner','mixed','mode_history')} | {
          'players':players,'events':[e for e in s['events'] if e['channel'] in ('public','dead')],
          'mode':s['config']['mode'],'intensity':s['config']['intensity'],'provider':s['config']['provider'],
          'rounds':s['config']['rounds'],'series_count':s['config']['series_count'],
          'inference':{k:s['config']['inference'].get(k) for k in ('num_ctx','num_predict','reasoning_budget','num_batch','num_gpu','thinking','model_profiles')},
          'interviews_pending':ended and s['phase']!='finished'}
