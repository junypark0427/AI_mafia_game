import copy
import pytest
from pydantic import ValidationError
from app.models import GameConfig,Role,team
from app.engine import Engine,winner,tally,investigate,resolve_night

def players(roles):
    return [dict(id=str(i),role=r,alive=True) for i,r in enumerate(roles)]

def test_default_valid():
    c=GameConfig();assert len(c.players)==6;assert not c.mode=='character';assert not c.enabled_roles==list(Role)
@pytest.mark.parametrize('roles',[
 ['citizen']*6,['mafia']*3+['citizen']*3,
 ['mafia','spy','spy','citizen','citizen','citizen'],
 ['mafia','mafia','spy','citizen','citizen','citizen'],
 ['mafia','police','police','citizen','citizen','citizen'],
 ['mafia','doctor','doctor','citizen','citizen','citizen'],
 ['mafia','bodyguard','bodyguard','citizen','citizen','citizen']])
def test_invalid_roles(roles):
    c=GameConfig().model_dump();c['enabled_roles']=list(Role)
    for p,r in zip(c['players'],roles):p['role']=r
    with pytest.raises(ValidationError):GameConfig(**c)
def test_disabled_assignment_rejected():
    c=GameConfig().model_dump();c['enabled_roles']=['mafia','citizen']
    with pytest.raises(ValidationError):GameConfig(**c)
def test_enabling_does_not_assign():
    c=GameConfig(enabled_roles=list(Role));assert all(p.role!='spy' for p in c.players)
def test_distinct_models():
    c=GameConfig().model_dump();c['players'][1]['model']=c['players'][0]['model']
    with pytest.raises(ValidationError):GameConfig(**c)
    c['provider']='mock';GameConfig(**c)
def test_assignment_counts_and_first_day():
    c=GameConfig();e=Engine(c)
    assert sorted(p['role'] for p in e.s['players'])==sorted(p.role for p in c.players)
    assert e.s['phase']=='discussion' and e.s['day']==1

def test_town_win_even_spy_alive():
    p=players(['mafia','spy','citizen']);p[0]['alive']=False;assert winner(p)=='town'
def test_mafia_parity_spy_counted():assert winner(players(['mafia','spy','citizen','citizen']))=='mafia'
def test_not_parity():assert winner(players(['mafia','spy','citizen','citizen','citizen'])) is None
def test_mafia_alone_wins():assert winner(players(['mafia']))=='mafia'
def test_spy_investigation():assert investigate('spy')=='town' and investigate('mafia')=='mafia'
def test_vote_tie():assert tally({'a':'b','b':'a'},['a','b']) is None
@pytest.mark.parametrize('votes',[{'a':'a'},{'a':'x'},{'x':'a'}])
def test_invalid_votes(votes):
    with pytest.raises(ValueError):tally(votes,['a','b'])
@pytest.mark.parametrize('actions,expected',[
 ({'0':'4','2':'5','3':'4'},['3']),
 ({'0':'4','2':'4','3':'4'},[]),
 ({'0':'4','2':'3','3':'4'},[]),
 ({'0':'4','2':'5','3':'5'},['4']),
 ({'0':'3','2':'5','3':'4'},['3']),
])
def test_doctor_bodyguard_priority(actions,expected):
    p=players(['mafia','police','doctor','bodyguard','citizen','citizen']);assert resolve_night(p,actions)==expected
def test_two_mafia_tied_attack_none():
    p=players(['mafia','mafia','doctor','citizen','citizen','citizen']);assert resolve_night(p,{'0':'4','1':'5','2':'2'})==[]
def test_dead_abilities_ignored():
    p=players(['mafia','doctor','bodyguard','citizen']);p[1]['alive']=False
    assert resolve_night(p,{'0':'3','1':'3'})==['3']
def test_cannot_apply_invalid_target():
    e=Engine(GameConfig(random_roles=False));e.phase('vote');task=e.next_task()
    with pytest.raises(ValueError):e.apply(task,{'target':task['actor'],'speech':''},{})
    assert e.s['cursor']==0

def test_rotation_balanced_six_games():
    cfg=GameConfig(random_roles=False,rotate_roles=True);roles=[p.role.value for p in cfg.players]
    mafia=[]
    for i in range(6):mafia.append(next(p['id'] for p in Engine(cfg,index=i,roles=roles).s['players'] if p['role']=='mafia'))
    assert len(set(mafia))==6

def test_public_never_exposes_living_roles():
    e=Engine(GameConfig(random_roles=False));view=e.public()
    assert all('role' not in p for p in view['players'])
    for channel in ('mafia','private'):e.event('secret','HIDDEN',channel=channel)
    assert 'HIDDEN' not in str(e.public())
    assert 'config' not in view and 'investigations' not in view and 'memories' not in view
    e.s['winner']='town';assert all('role' in p for p in e.public()['players'])

def test_dead_reactions_daily_cap():
    e=Engine(GameConfig(provider='mock',random_roles=False,dead_reactions=1))
    e.player('llama')['alive']=False
    assert e.dead_queue()==['llama']
    e.phase('dead',['llama']);e.apply(e.next_task(),{'speech':'reaction','memory':''},{})
    assert e.dead_queue()==[]
    e.s['day']+=1;assert e.dead_queue()==['llama']

def test_max_day_limit_is_invalid_without_winner():
    e=Engine(GameConfig(provider='mock',random_roles=False,max_days=2))
    e.s['day']=2;e.phase('dead',[]);e.s['after_dead']='day'
    e.advance()
    assert e.s['status']=='invalid' and e.s['phase']=='finished' and e.s['winner'] is None


def test_duplicate_task_cannot_advance_or_overwrite_choice():
    e=Engine(GameConfig(provider='mock'));e.phase('vote');task=e.next_task()
    result={'target':task['eligible'][0],'speech':'','memory':''};e.apply(task,result,{})
    before=copy.deepcopy(e.s)
    with pytest.raises(ValueError,match='현재 순서'):e.apply(task,result,{})
    assert e.s==before


@pytest.mark.parametrize('phase,actor',[('vote','llama'),('night','llama'),('discussion','llama')])
def test_dead_actor_cannot_use_living_phase_even_with_bad_queue(phase,actor):
    e=Engine(GameConfig(provider='mock'));e.player(actor)['alive']=False;e.phase(phase,[actor])
    task=e.next_task()
    with pytest.raises(ValueError,match='사망자는'):e.apply(task,{'speech':'말','target':'qwen'},{})
    assert e.s['cursor']==0 and not e.s['votes'] and not e.s['night_actions']
