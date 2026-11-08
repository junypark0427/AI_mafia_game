import copy,json
import pytest
from app.engine import Engine
from app.models import GameConfig,Inference
from app.ollama import ModelError,Ollama
from app.manager import Manager
from app.storage import Store
from app.prompts import build_prompt,audit_prompt
from app.quality import validate_speech,adapt_request

@pytest.mark.parametrize('text,metric,code',[
 ('이미 feedback이 주어지며, 더 information을 obtain doing 것이 불 Golf-be.',{},'dialogue_quality'),
 ('I am a citizen. I will investigate the team.',{},'dialogue_quality'),
 ('자연스러운 한국어로 보이지만 아직 끝나지 않은',{'done_reason':'length'},'output_truncated'),
 (('이런 말이 계속 반복되는 것은 자연스럽지 않습니다.'*8),{},'dialogue_repetition')])
def test_rejects_obvious_failures(text,metric,code):
 with pytest.raises(ModelError) as error:validate_speech(text,metric)
 assert error.value.code==code

def test_names_and_intentional_silence_are_not_language_errors():
 validate_speech('DeepSeek와 Qwen의 설명이 달라요. Gemma는 어떻게 생각해요?',{})
 validate_speech('[침묵]',{})

def test_profiles_keep_private_scope_and_optional_character():
 e=Engine(GameConfig(provider='mock',random_roles=False,mode='character'));task=e.next_task();msg,_=build_prompt(e,task)
 profile,opt,schema,protocol=adapt_request(msg,Inference().model_dump(),'deepseek-r1:1.5b',task)
 assert len(profile)==1 and profile[0]['role']=='user' and protocol=='text' and schema is None
 assert '혁명 연설가' in str(profile) and '서부 카우보이' not in str(profile)
 assert audit_prompt(e,task,profile)['private_owner']=='deepseek'
 original,_,_,protocol=adapt_request(msg,Inference(model_profiles=False).model_dump(),'deepseek-r1:1.5b',task)
 assert original==msg and protocol=='json'
 e.s['config']['mode']='original';msg,_=build_prompt(e,task)
 profile,*_=adapt_request(msg,Inference().model_dump(),'deepseek-r1:1.5b',task)
 assert '혁명 연설가' not in str(profile)

async def test_bad_response_logged_then_retry_uses_same_model(tmp_path,monkeypatch):
 m=Manager(Store(tmp_path/'game.db'),Ollama());await m.start(GameConfig(provider='mock',random_roles=False))
 g=m.game;g.s['config']['provider']='ollama';g.player('deepseek')['digest']='test';calls=[]
 async def generate(model,messages,options,schema):
  calls.append(model)
  text='I am speaking English instead of Korean.' if len(calls)==1 else 'Gemma의 주장에는 아직 근거가 없어요. 이유를 더 들어보고 판단할게요.'
  return text,{'model':model,'digest':'test','seconds':1,'done_reason':'stop','unloaded':True}
 monkeypatch.setattr(m.ollama,'generate',generate)
 await m.step(g.s['revision'])
 assert len(calls)==2 and len(set(calls))==1
 assert g.s['calls'][0]['raw_output'].startswith('I am') and g.s['calls'][0]['error_code']=='dialogue_quality'
 assert 'I am' not in str(g.public()) and len([e for e in g.s['events'] if e['kind']=='speech'])==1
 assert g.s['memories']['deepseek']['own_statements'][-1]['text'].startswith('Gemma')
 assert not g.s['memories']['deepseek']['opinions']

async def test_repeated_failure_pauses_without_publishing(tmp_path,monkeypatch):
 m=Manager(Store(tmp_path/'game.db'),Ollama());await m.start(GameConfig(provider='mock',random_roles=False))
 g=m.game;g.s['config']['provider']='ollama';g.player('deepseek')['digest']='test'
 async def generate(*args):return 'English only response.',{'digest':'test','seconds':1}
 monkeypatch.setattr(m.ollama,'generate',generate)
 await m.step(g.s['revision'])
 assert g.s['status']=='paused' and g.s['cursor']==0 and len(g.s['calls'])==2
 assert not [e for e in g.s['events'] if e['kind']=='speech'] and m.store.stats()==[]

async def test_call_limit_is_invalid_not_official_win(tmp_path):
 m=Manager(Store(tmp_path/'game.db'),Ollama());await m.start(GameConfig(provider='mock',max_calls=12))
 m.game.s['calls']=[{'error':'previous attempt'}]*12
 await m.step(m.game.s['revision'])
 assert m.game.s['status']=='invalid' and m.game.s['phase']=='finished' and m.game.s['winner'] is None
 assert m.store.stats()==[]

def test_2048_context_retains_exact_votes_and_personal_facts():
 from app.models import CATALOG
 cfg=GameConfig(provider='mock',random_roles=False);cfg.players[0].model='llama3.2:1b'
 e=Engine(cfg)
 for day in range(1,4):
  for p in CATALOG:e.event('vote',p['name']+' 투표',actor=p['id'],target='qwen')
  for _ in range(10):e.event('speech','최근의 중요한 주장과 근거를 정확히 확인해 보겠습니다.'*2,actor='gemma')
 e.s['memories']['deepseek']={'opinions':[{'day':1,'note':'내가 의심하는 대상은 Gemma다.'}]}
 msg,_=build_prompt(e,{'actor':'deepseek','kind':'speech','eligible':[]})
 ctx=json.loads(msg[1]['content'].split('\n\n')[1].partition(': ')[2])
 assert len(ctx['public_facts'])==18 and ctx['omitted_old_speech']>0
 assert audit_prompt(e,{'actor':'deepseek','kind':'speech','eligible':[]},msg)['verified']


def test_action_prompt_does_not_request_subjective_prose():
 e=Engine(GameConfig(provider='mock'));e.phase('vote');msg,_=build_prompt(e,e.next_task())
 request=msg[1]['content'].split('\n\n')[-1]
 assert '빈 문자열' in request and '주관적 인상' not in request


async def test_completed_interview_limit_preserves_official_result(tmp_path):
 m=Manager(Store(tmp_path/'game.db'),Ollama());await m.start(GameConfig(provider='mock'))
 m.game.s.update(status='completed',winner='town',phase='interview');m.finish_limit('호출 한도')
 assert m.game.s['status']=='completed' and m.game.s['winner']=='town' and m.game.s['phase']=='finished'
 assert m.game.s['interviews_interrupted']
