#!/usr/bin/env python3
"""Controlled dead-chat fixture with real Ollama calls; NOT an official game."""
import asyncio,copy,json
from pathlib import Path
from app.engine import Engine
from app.models import GameConfig,Inference
from app.prompts import build_prompt,audit_prompt
from app.quality import adapt_request,validate_speech
from app.ollama import Ollama,ModelError
ROOT=Path(__file__).resolve().parents[1]
def ensure_idle():
 import httpx
 with httpx.Client(trust_env=False,timeout=5) as client:
  state=client.get('http://127.0.0.1:8000/api/state');state.raise_for_status();s=state.json()
  if s['busy'] or s['auto'] or s['benchmark_busy']:raise RuntimeError('앱의 게임·추론이 진행 중입니다. 기존 작업은 중단하지 않습니다.')

async def main():
 ensure_idle()
 cfg=GameConfig(random_roles=False);cfg.inference=Inference(num_ctx=4096,num_gpu=0,num_batch=64,num_predict=512,thinking=False,temperature=.6,timeout=100,retries=0)
 models=['deepseek-r1:1.5b','gemma3:1b','ministral-3:3b','exaone3.5:2.4b','qwen3:1.7b','llama3.2:3b']
 for p,m in zip(cfg.players,models):p.model=m
 base=Engine(cfg);base.kill('exaone','[CONTROLLED FIXTURE] 투표로 사망');base.kill('ministral','[CONTROLLED FIXTURE] 밤에 사망')
 base.event('speech','EXAONE을 처형한 게 맞았는지 아직 확신이 없어요.',actor='qwen')
 results=[];o=Ollama()
 for mode in ['original','character']:
  e=Engine.restore(copy.deepcopy(base.s));e.s['config']['mode']=mode;e.s['phase']='dead'
  for pid in ['exaone','ministral']:
   task={'actor':pid,'kind':'dead','eligible':[]};msg,style=build_prompt(e,task);msg,opt,schema,protocol=adapt_request(msg,cfg.inference.model_dump(),e.player(pid)['model'],task)
   scope=audit_prompt(e,task,msg);r={'fixture':'controlled dead chat, NOT actual completed match','model':e.player(pid)['model'],'mode':mode,'style':style,'scope':scope}
   ensure_idle();print('START',mode,pid,flush=True)
   try:
    text,metric=await o.generate(r['model'],msg,opt,schema);r.update(metric,raw_output=text)
    data=json.loads(text) if protocol=='json' else {'speech':text,'target':None,'memory':''}
    validate_speech(data['speech'],metric,e,task);r.update(outcome='success',speech=data['speech'])
    e.event('dead',data['speech'],actor=pid,channel='dead',**style)
    live={'actor':'qwen','kind':'speech','eligible':[]};living,_=build_prompt(e,live)
    r['living_prompt_excludes_response']=data['speech'] not in str(living)
    r['living_prompt_scope']=audit_prompt(e,live,living)
   except (ModelError,ValueError) as err:r.update(getattr(err,'metrics',{}),outcome='failed',error=str(err),error_code=getattr(err,'code','parse_error'))
   results.append(r);(ROOT/'reports/local-quality/dead-character.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
   print({k:r.get(k) for k in ['model','mode','outcome','speech','error','seconds','tps','unloaded','living_prompt_excludes_response']},flush=True)
   if r.get('error_code') in ('memory_emergency_stop','memory_critical','unload_failed'):return
if __name__=='__main__':asyncio.run(main())
