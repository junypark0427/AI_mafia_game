#!/usr/bin/env python3
"""Opt-in real HTTP integration run. No mocks, fixed speeches, forced votes or rule bypasses."""
import argparse,hashlib,json,time
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]
PRIVATE=ROOT/'reports/local-real-game'
PUBLIC=ROOT/'reports/real-games'
MODELS=['deepseek-r1:1.5b','gemma3:1b','ministral-3:3b','exaone3.5:2.4b','qwen3:1.7b','llama3.2:3b']

def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')

def emit(value):print(json.dumps(value,ensure_ascii=False),flush=True)

def call(c,path,method='GET',body=None):
    r=c.request(method,path,json=body) if body is not None else c.request(method,path)
    if not r.is_success:raise RuntimeError(f'{method} {path}: {r.status_code} {r.text[:1000]}')
    return r.json()

def export(c,gid,label):
    raw=call(c,'/api/admin/games/'+gid);public=call(c,'/api/games/'+gid)
    save(PRIVATE/gid/'snapshot.json',raw);save(PUBLIC/gid/'replay.json',public)
    for filename,events in [('public_chat.jsonl',[e for e in public['events'] if e['channel']=='public' and e['kind'] in ('speech','interview')]),('events.jsonl',public['events'])]:
        (PUBLIC/gid).mkdir(parents=True,exist_ok=True)
        (PUBLIC/gid/filename).write_text(''.join(json.dumps(e,ensure_ascii=False)+'\n' for e in events))
    save(PRIVATE/(label+'-result.json'),{'id':gid,'status':raw['status'],'phase':raw['phase'],'winner':raw['winner'],'day':raw['day'],'calls':len(raw['calls']),'events':len(raw['events'])})
    return raw

def run_individual(c):
    inventory=call(c,'/api/health');installed={m['name'] for m in inventory['installed']}
    missing=set(MODELS)-installed
    if missing:raise RuntimeError('미설치: '+', '.join(sorted(missing)))
    state=call(c,'/api/state')
    if state['busy'] or state['benchmark_busy'] or state['auto']:raise RuntimeError('기존 작업을 중단하지 않습니다.')
    options={'num_ctx':2048,'num_predict':384,'reasoning_budget':1024,'temperature':.7,'top_p':.9,'timeout':150,'retries':0}
    started=time.time();call(c,'/api/admin/benchmark','POST',{'models':MODELS,'inference':options,'quality':False})
    deadline=time.monotonic()+1200
    while time.monotonic()<deadline:
        status=call(c,'/api/admin/benchmark/status');emit({'stage':'individual','active':status['active'],'memory':status['memory']})
        if not status['busy']:break
        time.sleep(10)
    else:call(c,'/api/admin/benchmark/cancel','POST',{});raise RuntimeError('개별 테스트 20분 한도')
    records=[r for r in call(c,'/api/admin/benchmarks') if r['model'] in MODELS and r['at'][:19]>=time.strftime('%Y-%m-%dT%H:%M:%S',time.gmtime(started-1))]
    # Timestamps are UTC ISO strings. Keep every current attempt, including refusals/failures.
    save(PRIVATE/'individual.json',records)
    for r in reversed(records):emit({k:r.get(k) for k in ('model','outcome','error','seconds','tokens','tps','done_reason','unloaded','output')})

def run_game(c,stage):
    config=call(c,'/api/admin/settings')['config']
    for p,model in zip(config['players'],MODELS):p['model']=model
    config.update(provider='ollama',mode='original' if stage!='character' else 'character',rounds=3 if stage=='full' else 1,random_roles=True,
        auto=True,last_words=True,dead_chat=True,dead_reactions=1,interviews=False,series_count=1,max_days=8 if stage=='full' else 4)
    config['inference'].update(num_ctx=4096,num_gpu=0,num_batch=64,thinking=False,num_predict=512,reasoning_budget=1024,temperature=.7,timeout=150,retries=1)
    if stage=='character':config['inference']['num_ctx']=4096
    call(c,'/api/admin/validate','POST',config)
    started=call(c,'/api/admin/start','POST',config);gid=started['game']['id'];save(PRIVATE/(stage+'-config.json'),config)
    save(PRIVATE/(stage+'-game.json'),{'id':gid});emit({'stage':stage,'id':gid,'replay':'/api/games/'+gid})
    deadline=time.monotonic()+(2700 if stage=='full' else 1200);last=None;last_export=0
    while time.monotonic()<deadline:
        s=call(c,'/api/admin/state');g=s['game']
        if not g or g['id']!=gid:raise RuntimeError('실행 중 게임이 외부에서 변경되었습니다.')
        progress=(g['revision'],g['day'],g['phase'],g['round'],s['busy']['actor'] if s['busy'] else None,g['status'])
        if progress!=last:
            emit({'stage':stage,'game':gid,'day':g['day'],'round':g['round'],'phase':g['phase'],'alive':sum(p['alive'] for p in g['players']),'busy':s['busy'],'status':g['status'],'error':s['error']});last=progress
        if time.monotonic()-last_export>15:export(c,gid,stage);last_export=time.monotonic()
        if s['error']:
            export(c,gid,stage);emit({'stage':stage,'result':'paused_error','error':s['error']});return
        if g['phase']=='finished':break
        if stage!='full' and g['day']>=2 and g['phase']=='discussion':
            call(c,'/api/admin/control','POST',{'action':'stop'});break
        time.sleep(2)
    else:
        call(c,'/api/admin/control','POST',{'action':'stop'});emit({'stage':stage,'result':'wall_clock_limit'})
    for _ in range(90):
        s=call(c,'/api/admin/state')
        if not s['busy']:break
        time.sleep(2)
    final=export(c,gid,stage)
    emit({'stage':stage,'id':gid,'result':final['status'],'winner':final['winner'],'day':final['day'],'calls':len(final['calls'])})

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['individual','short','full','character']);args=parser.parse_args()
    PRIVATE.mkdir(parents=True,exist_ok=True)
    with httpx.Client(base_url='http://127.0.0.1:8000',timeout=180,trust_env=False,headers={'Authorization':'Bearer '+(ROOT/'data/admin-token.txt').read_text().strip()}) as client:
        if args.stage=='individual':run_individual(client)
        else:run_game(client,args.stage)
