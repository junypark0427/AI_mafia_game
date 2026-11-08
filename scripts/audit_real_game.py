#!/usr/bin/env python3
"""Independent checks over a real saved game's events (no model calls)."""
import json
from collections import Counter,defaultdict
from pathlib import Path

MAFIA={'mafia','spy'}
def audit(s,public=None):
    issues=[];checks=[];players={p['id']:p for p in s['players']};alive=set(players);votes=defaultdict(list);voters_alive={};night_alive={};death_times={}
    def check(condition,message):
        checks.append(message)
        if not condition:issues.append(message)
    check(s['config']['provider']=='ollama','provider is actual Ollama')
    check(len(players)==6 and len({p['model'] for p in players.values()})==6,'six distinct actual model tags')
    for e in s['events']:
        actor=e.get('actor');phase=e['phase'];day=e['day'];kind=e['kind']
        if phase in ('mafia_chat','night'):night_alive.setdefault(day,set(alive))
        if kind=='vote':
            voters_alive.setdefault(day,set(alive));votes[day].append(e)
            check(actor in alive and e['target'] in alive and actor!=e['target'],f'valid live vote event {e["id"]}')
        if kind in ('speech','secret'):check(actor in alive,f'live speaker event {e["id"]}')
        if kind=='dead':check(actor not in alive,f'dead-only lounge event {e["id"]}')
        if kind=='death':
            check(e['target'] in alive,f'death occurs once {e["id"]}');alive.discard(e['target']);death_times[e['target']]=e['at']
    for day,events in votes.items():
        actors=[e['actor'] for e in events];check(len(actors)==len(set(actors)) and set(actors)==voters_alive[day],f'all and only live players vote once on day {day}')
        counts=Counter(e['target'] for e in events);highest=max(counts.values());top=[p for p,n in counts.items() if n==highest]
        victims=[e['target'] for e in s['events'] if e['kind']=='death' and e['day']==day and e['phase']=='vote']
        check(victims==(top if len(top)==1 else []),f'independent vote tally day {day}')
    for night in s.get('night_history',[]):
        day=night['day'];actions=night['actions'];live=night_alive[day]
        expected_actors={p for p in live if players[p]['role'] in ('mafia','police','doctor','bodyguard')}
        check(set(actions)==expected_actors,f'all and only live ability actors day {day}')
        for actor,target in actions.items():
            role=players[actor]['role'];check(target in live and (actor!=target or role=='doctor'),f'legal night target {day}/{actor}')
        attack=Counter(t for a,t in actions.items() if players[a]['role']=='mafia');top=[]
        if attack:
            high=max(attack.values());top=[t for t,n in attack.items() if n==high]
        victim=top[0] if len(top)==1 else None
        heals={t for a,t in actions.items() if players[a]['role']=='doctor'}
        if victim in heals:victim=None
        elif victim:
            for actor,target in actions.items():
                if players[actor]['role']=='bodyguard' and target==victim:victim=actor;break
            if victim in heals:victim=None
        actual=[e['target'] for e in s['events'] if e['kind']=='death' and e['phase']=='night' and e['day']==day]
        check(actual==([victim] if victim else []),f'independent attack/heal resolution day {day}')
        for actor,target in actions.items():
            if players[actor]['role']=='police':
                results=[i for i in s['investigations'] if i['actor']==actor and i['day']==day]
                expected={'actor':actor,'target':target,'day':day,'result':'mafia' if players[target]['role']=='mafia' else 'town'}
                check(results==[expected],f'private police investigation day {day}')
    for call in s['calls']:
        if call.get('error'):continue
        actor=call['actor'];kind=call['kind'];scope=call.get('prompt_scope',{})
        check(scope.get('verified') and scope.get('private_owner')==actor,f'actual request context audit {actor}/{call["at"]}')
        check(scope.get('model')==players[actor]['model'] and call['model']==players[actor]['model'],f'actual model identity {actor}/{call["at"]}')
        check(call.get('unloaded') is True,f'model unloaded {actor}/{call["at"]}')
        if kind in ('vote','night'):
            check(call.get('target') in scope.get('eligible_targets',[]),f'model chose legal target {actor}/{call["at"]}')
        if kind in ('speech','vote','night','secret') and actor in death_times:check(call['at']<=death_times[actor],f'no actions after death {actor}/{call["at"]}')
        if kind in ('speech','vote','night','secret'):check(scope.get('dead_chat_messages',0)==0,f'living agent receives no ghost messages {actor}/{call["at"]}')
    check(alive=={p['id'] for p in s['players'] if p['alive']},'final alive flags match event ledger')
    actual_mafia=sum(players[p]['role']=='mafia' for p in alive);mafia_team=sum(players[p]['role'] in MAFIA for p in alive)
    expected='town' if not actual_mafia else 'mafia' if mafia_team>=len(alive)-mafia_team else None
    if s['status']=='completed':check(expected==s['winner'] and expected is not None,'independent official victory condition')
    else:check(s['winner'] is None,'unfinished game has no official winner')
    if public is not None:
        check('config' not in public and 'calls' not in public and 'memories' not in public and 'night_actions' not in public,'public replay excludes private state')
        check(all(e['channel'] in ('public','dead') for e in public['events']),'public replay excludes secret channels')
        if s['status']=='completed':check(all('role' in p for p in public['players']),'roles reveal after official completion')
    return {'game_id':s['id'],'status':s['status'],'winner':s['winner'],'checks':len(checks),'issues':issues,
        'nights':len(s.get('night_history',[])),'votes':sum(map(len,votes.values())),'speeches':sum(e['kind']=='speech' for e in s['events']),
        'ghost_reactions':sum(e['kind']=='dead' for e in s['events']),'audited_successful_requests':sum(not c.get('error') for c in s['calls'])}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('game_id');args=p.parse_args();root=Path(__file__).resolve().parents[1]
    s=json.loads((root/'reports/local-real-game'/args.game_id/'snapshot.json').read_text());public=json.loads((root/'reports/real-games'/args.game_id/'replay.json').read_text())
    result=audit(s,public);(root/'reports/real-games'/args.game_id/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
