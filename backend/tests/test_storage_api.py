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


def test_frontend_json_contract_and_ollama_start(client,monkeypatch):
    """Use the exact serialized frontend fixture; never load a real model here."""
    from pathlib import Path
    import app.ollama as runtime
    c,m=client
    payload=json.loads((Path(__file__).resolve().parents[2]/'frontend/tests/fixtures/game-config.json').read_text())
    # Distinguish a successful valid request from current hardware readiness.
    m.ollama.models={p['model']:{'size':4683075440,'digest':'test-version'} for p in payload['players']}
    async def available():return {'online':True,'version':'0.34.3'}
    monkeypatch.setattr(m.ollama,'inventory',available)
    monkeypatch.setattr(runtime,'memory_snapshot',lambda:{'total_gb':8,'available_gb':7,'used_gb':1,'swap_gb':0})
    assert c.post('/api/admin/validate',json=payload,headers=AUTH).status_code==200
    result=c.post('/api/admin/start',json=payload,headers=AUTH)
    assert result.status_code==200
    assert result.json()['game']['provider']=='ollama'
    assert result.json()['game']['status']=='paused'
    assert m.manager.game.s['initial_config']==GameConfig(**payload).model_dump(mode='json')
    assert m.manager.game.s['calls']==[]


def test_low_memory_is_runtime_503_not_schema_422(client,monkeypatch):
    import app.ollama as runtime
    c,m=client;payload=GameConfig().model_dump(mode='json')
    m.ollama.models={p['model']:{'size':4683075440,'digest':'test-version'} for p in payload['players']}
    async def online():return {'online':True,'version':'0.34.3'}
    monkeypatch.setattr(m.ollama,'inventory',online)
    monkeypatch.setattr(runtime,'memory_snapshot',lambda:{'total_gb':8,'available_gb':1.4,'used_gb':6.6,'swap_gb':3.57})
    assert c.post('/api/admin/validate',json=payload,headers=AUTH).status_code==200
    result=c.post('/api/admin/start',json=payload,headers=AUTH)
    assert result.status_code==503
    error=result.json();assert error['code']=='insufficient_memory'
    assert '가용 1.40GiB' in error['detail'] and '경량' in error['hint']
    assert isinstance(error['detail'],str)
    assert m.manager.game is None and m.store.all()==[]


def test_offline_ollama_is_runtime_503(client,monkeypatch):
    c,m=client
    async def offline():return {'online':False}
    monkeypatch.setattr(m.ollama,'inventory',offline)
    result=c.post('/api/admin/start',json=GameConfig().model_dump(mode='json'),headers=AUTH)
    assert result.status_code==503 and result.json()['code']=='ollama_offline'


@pytest.mark.parametrize('field,label',[('role','직업'),('model','모델'),('name','이름'),('id','ID')])
def test_missing_player_field_localized_without_echo(client,field,label):
    c,m=client;payload=GameConfig(provider='mock').model_dump(mode='json')
    del payload['players'][1][field]
    payload['characters']['deepseek']['instructions']='PRIVATE_CONFIGURATION_MUST_NOT_BE_ECHOED'
    response=c.post('/api/admin/start',json=payload,headers=AUTH)
    assert response.status_code==422
    error=response.json();assert error['code']=='invalid_request'
    assert error['issues'][0]['field']==f'players.1.{field}'
    assert label in error['issues'][0]['message'] and '필수' in error['issues'][0]['message']
    assert error['detail'][0]['type']=='missing'
    assert 'input' not in error['detail'][0] and 'ctx' not in error['detail'][0]
    assert 'PRIVATE_CONFIGURATION_MUST_NOT_BE_ECHOED' not in response.text
    assert m.manager.game is None


@pytest.mark.parametrize('case,field,expected',[
    ('no_mafia','players','마피아는 1~2명'),
    ('duplicate_police','players','경찰은 최대 1명'),
    ('disabled_role','enabled_roles','Gemma(경찰)'),
    ('short_context','inference.num_ctx','2048 이상'),
    ('invalid_role','players.0.role','지원하지 않는 값'),
])
def test_invalid_configuration_returns_actionable_422(client,case,field,expected):
    c,m=client;payload=GameConfig(provider='mock').model_dump(mode='json')
    if case=='no_mafia':payload['players'][0]['role']='citizen'
    elif case=='duplicate_police':payload['players'][3]['role']='police'
    elif case=='disabled_role':payload['enabled_roles'].remove('police')
    elif case=='short_context':payload['inference']['num_ctx']=1024
    elif case=='invalid_role':payload['players'][0]['role']='unknown'
    for endpoint in ('validate','start'):
        response=c.post('/api/admin/'+endpoint,json=payload,headers=AUTH)
        assert response.status_code==422
        issue=response.json()['issues'][0]
        assert issue['field']==field and expected in issue['message']
    assert m.manager.game is None


def test_valid_defaults_can_start_mock_without_required_top_level_fields(client):
    c,m=client
    result=c.post('/api/admin/start',json={'provider':'mock'},headers=AUTH)
    assert result.status_code==200 and result.json()['game']['provider']=='mock'
    assert len(m.manager.game.s['players'])==6
    assert m.store.stats()==[]


def test_lab_preflight_is_independent_per_model(client,monkeypatch):
    import app.ollama as adapter
    c,m=client;m.ollama.models={'large:7b':{'size':4683075440},'small:1b':{'size':1000000000}}
    async def inventory():return {'online':True}
    monkeypatch.setattr(m.ollama,'inventory',inventory)
    monkeypatch.setattr(adapter,'memory_snapshot',lambda:{'total_gb':8,'available_gb':3,'used_gb':4,'swap_gb':4,'pressure':'normal'})
    assert c.post('/api/admin/benchmark/preflight',json={'models':['small:1b']}).status_code==401
    result=c.post('/api/admin/benchmark/preflight',json={'models':['large:7b','small:1b']},headers=AUTH).json()
    assert [a['state'] for a in result['models']]==['warning','ready']


def test_lab_risk_scope_and_actual_single_attempt(client,monkeypatch):
    import time
    c,m=client;calls=[]
    async def generate(model,messages,options,**kwargs):
        calls.append((model,options,kwargs));return '실제 어댑터 대역 응답',{'attempted':True,'unloaded':True,'seconds':.1}
    monkeypatch.setattr(m.ollama,'generate',generate)
    for payload in ({'models':['a','b'],'risk_once':True},{'models':['a'],'risk_once':True,'quality':True}):
        assert c.post('/api/admin/benchmark',json=payload,headers=AUTH).status_code==422
    response=c.post('/api/admin/benchmark',json={'models':['a'],'risk_once':True},headers=AUTH)
    assert response.status_code==200
    for _ in range(30):
        if not c.get('/api/admin/benchmark/status',headers=AUTH).json()['busy']:break
        time.sleep(.01)
    assert len(calls)==1 and calls[0][1]['retries']==0 and calls[0][2]['risk_once'] is True
    record=c.get('/api/admin/benchmarks',headers=AUTH).json()[0]
    assert record['outcome']=='success' and record['risk_once']


def test_lab_cancel_releases_busy_and_records_cancellation(client,monkeypatch):
    import asyncio,threading,time
    c,m=client;started=threading.Event()
    async def generate(*args,**kwargs):
        started.set();await asyncio.sleep(60)
    monkeypatch.setattr(m.ollama,'generate',generate)
    c.post('/api/admin/benchmark',json={'models':['a']},headers=AUTH)
    assert started.wait(1)
    assert c.post('/api/admin/benchmark/cancel',json={}).status_code==401
    assert c.post('/api/admin/benchmark/cancel',json={},headers=AUTH).json()['requested']
    # Repeated clicks must not interrupt the unload cleanup a second time.
    c.post('/api/admin/benchmark/cancel',json={},headers=AUTH)
    for _ in range(30):
        result=c.get('/api/admin/benchmark/status',headers=AUTH).json()
        if not result['busy']:break
        time.sleep(.01)
    assert not result['busy'] and result['job']['status']=='cancelled'
    assert c.get('/api/admin/benchmarks',headers=AUTH).json()[0]['outcome']=='cancelled'


def test_private_inference_identity_is_not_public(client,monkeypatch):
    c,m=client;cfg=GameConfig(provider='mock',random_roles=False).model_dump(mode='json')
    c.post('/api/admin/start',json=cfg,headers=AUTH)
    m.manager.busy={'actor':'gemma','model':'gemma3:4b','kind':'night','started':1,'style':{'mode':'original','character_applied':False}}
    async def inventory():return {'online':True,'active':{'model':'gemma3:4b'},'loaded':[{'name':'gemma3:4b'}]}
    monkeypatch.setattr(m.ollama,'inventory',inventory)
    public=c.get('/api/state').json()
    assert public['busy']['actor'] is None and public['busy']['kind']=='hidden'
    assert c.get('/api/health').json()['active'] is None and c.get('/api/health').json()['loaded']==[]
    assert c.get('/api/admin/state',headers=AUTH).json()['busy']['actor']=='gemma'


def test_stats_exclude_completed_record_without_winner(tmp_path):
    store=Store(tmp_path/'test.db');e=Engine(GameConfig());e.s['status']='completed';e.s['winner']=None;store.save(e.s)
    assert store.stats()==[]


def test_fresh_browser_state_has_current_saved_game(client):
    c,m=client
    result=c.post('/api/admin/start',json=GameConfig(provider='mock').model_dump(mode='json'),headers=AUTH)
    fresh=c.get('/api/state').json()
    assert fresh['game']['id']==result.json()['game']['id'] and fresh['game']['revision']==result.json()['game']['revision']
