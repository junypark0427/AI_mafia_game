import asyncio,copy,json
import pytest
from app.engine import Engine
from app.models import GameConfig,Character
from app.prompts import build_prompt
from app.ollama import clean_final,ModelError,Ollama
from app.manager import Manager,Conflict
from app.storage import Store

@pytest.fixture
def engine():return Engine(GameConfig(random_roles=False,provider='mock'))

def test_personal_secret_allowlist(engine):
    engine.s['investigations']=[{'actor':'gemma','target':'deepseek','result':'mafia','day':1}]
    engine.s['memories']={'gemma':{'opinions':[{'note':'GEMMA_PRIVATE_MEMORY'}]}}
    engine.event('secret','SECRET_MAFIA_PLAN',actor='deepseek',channel='mafia')
    engine.event('dead','DEAD_CHANNEL',actor='llama',channel='dead')
    text=json.dumps(build_prompt(engine,{'actor':'qwen','kind':'speech','eligible':[]})[0],ensure_ascii=False)
    assert 'GEMMA_PRIVATE_MEMORY' not in text and 'SECRET_MAFIA_PLAN' not in text and 'DEAD_CHANNEL' not in text
    assert '"role": "마피아"' not in text and '"role": "경찰"' not in text
    own=json.dumps(build_prompt(engine,{'actor':'gemma','kind':'speech','eligible':[]})[0],ensure_ascii=False)
    assert 'GEMMA_PRIVATE_MEMORY' in own and 'investigations' in own and 'SECRET_MAFIA_PLAN' not in own

def test_original_prompt_no_character(engine):
    task=engine.next_task();messages,meta=build_prompt(engine,task)
    assert '혁명 연설가' not in str(messages) and '코미디' not in str(messages)
    assert not meta['character_applied'] and meta['character_id'] is None

def test_character_independent_and_optional(engine):
    engine.s['config']['mode']='character';task=engine.next_task();msg,meta=build_prompt(engine,task)
    assert '혁명 연설가' in str(msg) and '서부 카우보이' not in str(msg) and meta['character_applied']
    engine.s['config']['characters'][task['actor']]['enabled']=False
    msg,meta=build_prompt(engine,task);assert '혁명 연설가' not in str(msg) and not meta['character_applied']

@pytest.mark.parametrize('kind',['speech','secret','dead','interview'])
def test_style_all_speech_channels(engine,kind):
    engine.s['config']['mode']='character'
    _,meta=build_prompt(engine,{'actor':'deepseek','kind':kind,'eligible':[]})
    assert meta['character_applied'] and meta['character_version']==1

def test_dead_does_not_get_new_faction_chat(engine):
    engine.player('deepseek')['alive']=False;engine.event('secret','NEW_PLAN_AFTER_DEATH',actor='qwen',channel='mafia')
    assert 'NEW_PLAN_AFTER_DEATH' not in str(build_prompt(engine,{'actor':'deepseek','kind':'dead','eligible':[]})[0])

def test_reasoning_redaction():
    assert clean_final('<think>SECRET_REASONING</think>Final')=='Final'
    with pytest.raises(ModelError):clean_final('<think>unfinished')
    with pytest.raises(ModelError):clean_final('oops </think>')

@pytest.fixture
def manager(tmp_path):return Manager(Store(tmp_path/'test.sqlite'),Ollama())

async def test_mock_complete_game_and_independent_calls(manager):
    cfg=GameConfig(provider='mock',random_roles=False,rounds=1,interviews=True)
    await manager.start(cfg)
    for i in range(500):
        await manager.step(manager.game.s['revision'])
        if manager.game.s['phase']=='finished':break
    s=manager.game.s
    assert s['status']=='completed' and s['winner']
    assert {c['actor'] for c in s['calls']}=={p.id for p in cfg.players}
    assert len(s['memories'])==6
    assert len([e for e in s['events'] if e['kind']=='interview'])==6
    assert any(e['channel']=='dead' for e in s['events'])
    assert manager.store.stats()==[]
    assert manager.store.get(s['id'])['winner']==s['winner']

async def test_stale_step_rejected(manager):
    await manager.start(GameConfig(provider='mock'));rev=manager.game.s['revision'];await manager.step(rev)
    with pytest.raises(Conflict):await manager.step(rev)

async def test_concurrent_step_rejected(manager):
    await manager.start(GameConfig(provider='mock'));rev=manager.game.s['revision']
    a=asyncio.create_task(manager.step(rev));await asyncio.sleep(.001)
    with pytest.raises(Conflict):await manager.step(rev)
    await a

async def test_pause_safe_point_and_next_style(manager):
    await manager.start(GameConfig(provider='mock',random_roles=False))
    step=asyncio.create_task(manager.step(manager.game.s['revision']));await asyncio.sleep(.001)
    manager.pause();manager.change_style('character','strong',manager.game.s['config']['characters'])
    await step
    e=[e for e in manager.game.s['events'] if e['kind']=='speech'][-1]
    assert e['mode']=='original' and not e['character_applied']
    await manager.step(manager.game.s['revision'])
    e=[e for e in manager.game.s['events'] if e['kind']=='speech'][-1]
    assert e['mode']=='character' and e['character_applied']
    assert manager.game.s['mixed'] and manager.game.s['status']=='paused'
    assert manager.game.s['admin_events'][-1]['type']=='style_change'

async def test_forced_stop_excludes_stats(manager):
    await manager.start(GameConfig(provider='mock'));manager.pause(True)
    assert manager.game.s['status']=='aborted' and manager.game.s['phase']=='finished'
    assert manager.store.stats()==[]

async def test_error_pauses_without_fake_choice(manager,monkeypatch):
    await manager.start(GameConfig(provider='mock',random_roles=False));g=manager.game;g.phase('vote')
    async def invalid(*args):return {'speech':'','target':'INVALID','memory':''},{'seconds':.01}
    from app.ollama import MockAI
    monkeypatch.setattr(MockAI,'act',invalid)
    await manager.step(g.s['revision'])
    assert g.s['cursor']==0 and g.s['votes']=={} and manager.last_error
    assert g.s['status']=='paused'

async def test_series_sequential(manager):
    await manager.start(GameConfig(provider='mock',rounds=1,interviews=False,series_count=2,random_roles=False))
    manager.resume()
    await asyncio.wait_for(manager.auto_task,45)
    assert len(manager.store.all())==2
    assert all(s['status']=='completed' for s in manager.store.all())

async def test_memory_emergency_is_not_retried_and_metrics_survive(manager,monkeypatch):
    await manager.start(GameConfig(provider='mock',random_roles=False))
    manager.game.s['config']['provider']='ollama';attempts=[]
    async def unsafe(*args,**kwargs):
        attempts.append(1)
        raise ModelError('critical',code='memory_emergency_stop',attempted=True,metrics={'seconds':2.1,'unloaded':True,'tokens':0})
    monkeypatch.setattr(manager.ollama,'generate',unsafe)
    await manager.step(manager.game.s['revision'])
    assert len(attempts)==1 and manager.game.s['status']=='paused'
    record=manager.game.s['calls'][-1]
    assert record['error_code']=='memory_emergency_stop' and record['seconds']==2.1 and record['unloaded']

def test_actual_prompt_scope_audit_rejects_hidden_role(engine):
    from app.prompts import audit_prompt
    task={'actor':'qwen','kind':'speech','eligible':[]};messages,_=build_prompt(engine,task)
    scope=audit_prompt(engine,task,messages)
    assert scope['private_owner']=='qwen' and scope['teammate_ids']==[] and scope['verified']
    blocks=messages[1]['content'].split('\n\n')
    context=json.loads(blocks[1].partition(': ')[2]);context['players'][0]['role']='마피아'
    blocks[1]='확정 공개 사건과 주관적 기억을 구분한 자료: '+json.dumps(context,ensure_ascii=False)
    messages[1]['content']='\n\n'.join(blocks)
    with pytest.raises(ModelError,match='정보 격리'):audit_prompt(engine,task,messages)

def test_action_schema_limits_only_legal_model_choices():
    from app.prompts import schema_for
    vote=schema_for({'kind':'vote','eligible':['gemma','qwen']})
    assert vote['properties']['target']['enum']==['gemma','qwen']
    speech=schema_for({'kind':'speech','eligible':[]})
    assert speech['properties']['target']=={'type':'null'}

def test_defense_request_is_distinct_and_keeps_choice_autonomy(engine):
    engine.s['phase']='defense';messages,_=build_prompt(engine,engine.next_task())
    assert '최후 변론' in messages[1]['content'] and '유지하거나 바꾸는 선택은 자유' in messages[1]['content']

def test_action_payload_does_not_spend_budget_on_unspoken_dialogue():
    from app.prompts import schema_for
    schema=schema_for({'kind':'vote','eligible':['gemma','qwen']})
    assert schema['properties']['speech']['enum']==[''] and schema['properties']['memory']['enum']==['']
