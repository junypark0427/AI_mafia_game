import copy,json,importlib,os
import pytest
from fastapi.testclient import TestClient
from app.engine import Engine
from app.models import GameConfig
from app.storage import Store
from app.ollama import Ollama,ModelError

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('AI_MAFIA_DATA',str(tmp_path));monkeypatch.setenv('ADMIN_TOKEN','test-only-admin-key-at-least-32-characters')
    import app.main as m
    importlib.reload(m)
    with TestClient(m.app) as c:yield c,m

AUTH={'Authorization':'Bearer test-only-admin-key-at-least-32-characters'}

def test_auth_and_no_role_leaks(client):
    c,m=client
    cfg=GameConfig(provider='mock',random_roles=False).model_dump(mode='json')
    assert c.post('/api/admin/start',json=cfg).status_code==401
    assert c.get('/api/admin/settings').status_code==401
    assert c.put('/api/admin/settings',json=cfg).status_code==401
    r=c.post('/api/admin/start',json=cfg,headers=AUTH);assert r.status_code==200
    public=c.get('/api/state').json()['game'];assert all('role' not in p for p in public['players'])
    m.manager.game.event('secret','PRIVATE_FACTION_123',channel='mafia')
    m.manager.game.s['memories']['gemma']={'opinions':[{'note':'PRIVATE_NOTE_123'}]}
    m.manager.save()
    for path in ('/api/state','/api/games/'+public['id']):
        text=c.get(path).text;assert 'PRIVATE_FACTION_123' not in text and 'PRIVATE_NOTE_123' not in text
        assert 'night_actions' not in text and 'investigations' not in text
    assert c.get('/api/admin/games/'+public['id']).status_code==401
    assert 'PRIVATE_FACTION_123' in c.get('/api/admin/games/'+public['id'],headers=AUTH).text
    assert c.get('/api/admin/games/not-found',headers=AUTH).status_code==404
    assert c.get('/api/state').headers['x-content-type-options']=='nosniff'

def test_api_validation_controls_and_style(client):
    c,m=client;cfg=GameConfig(provider='mock',random_roles=False).model_dump(mode='json')
    c.post('/api/admin/start',json=cfg,headers=AUTH)
    assert c.post('/api/admin/control',json={'action':'step'},headers=AUTH).status_code==422
    rev=m.manager.game.s['revision']
    assert c.post('/api/admin/control',json={'action':'step','revision':rev},headers=AUTH).status_code==200
    assert c.post('/api/admin/control',json={'action':'step','revision':rev},headers=AUTH).status_code==409
    style={'mode':'character','intensity':'strong','characters':cfg['characters']}
    assert c.put('/api/admin/style',json=style).status_code==401
    assert c.put('/api/admin/style',json=style,headers=AUTH).json()['game']['mixed']
    assert c.put('/api/admin/settings',json=cfg,headers=AUTH).status_code==200
    assert c.get('/api/admin/settings',headers=AUTH).json()['config']==cfg
    invalid=copy.deepcopy(cfg);invalid['players'][0]['role']='citizen'
    assert c.post('/api/admin/validate',json=invalid,headers=AUTH).status_code==422

def test_recover_persists_and_marks_interrupted(tmp_path):
    store=Store(tmp_path/'mafia.db');e=Engine(GameConfig(provider='mock'));store.save(e.s)
    store2=Store(tmp_path/'mafia.db');store2.recover()
    assert store2.get(e.s['id'])['status']=='interrupted'
    assert store2.stats()==[]

def test_stats_modes_versions_and_exclusions(tmp_path):
    store=Store(tmp_path/'mafia.db')
    for mode in ['original','character','mixed','aborted','mock']:
        e=Engine(GameConfig(provider='mock',random_roles=False));s=e.s
        s['config']['provider']='mock' if mode=='mock' else 'ollama';s['status']='aborted' if mode=='aborted' else 'completed';s['winner']='town'
        s['mode_history']=['character'] if mode=='character' else ['original'];s['mixed']=mode=='mixed'
        e.player('gemma')['digest']='digest-v1'
        e.event('vote','vote',actor='gemma',target='deepseek')
        s['investigations']=[{'actor':'gemma','target':'deepseek','result':'mafia','day':1}]
        store.save(s)
    for mode in ('original','character','mixed'):
        rows=store.stats(mode);assert len(rows)==6
        gemma=next(r for r in rows if r['model']=='gemma3:4b')
        assert gemma['games']==1 and gemma['wins']==1 and gemma['win_rate']==100
        assert gemma['vote_accuracy']==100 and gemma['police_success']==1 and gemma['roles']['police']['games']==1
    e=Engine(GameConfig(random_roles=False));e.s['status']='completed';e.s['winner']='mafia';e.player('gemma')['digest']='digest-v2';store.save(e.s)
    assert len([s for s in store.stats() if s['model']=='gemma3:4b'])==2

def test_memory_guard_no_silent_replacement(monkeypatch):
    import app.ollama as mod
    o=Ollama();o.models={'deepseek-r1:7b':{'size':4683075440}}
    monkeypatch.setattr(mod,'memory_snapshot',lambda:{'total_gb':8,'available_gb':5,'swap_gb':3,'used_gb':3})
    with pytest.raises(ModelError,match='메모리 보호'):o.guard('deepseek-r1:7b')
    with pytest.raises(ModelError,match='미설치'):o.guard('deepseek-r1:1.5b')
    assert list(o.models)==['deepseek-r1:7b']
