#!/usr/bin/env python3
"""Opt-in real same-scenario Korean comparisons; never mutates an ongoing game."""
import asyncio,copy,hashlib,json
from pathlib import Path
from app.engine import Engine
from app.models import GameConfig,Inference
from app.ollama import Ollama,ModelError
from app.prompts import build_prompt,schema_for,audit_prompt
from app.quality import validate_speech,adapt_request
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/local-quality'
MODELS=['deepseek-r1:1.5b','gemma3:1b','ministral-3:3b','exaone3.5:2.4b','qwen3:1.7b','llama3.2:3b']
def ensure_idle():
 import httpx
 with httpx.Client(trust_env=False,timeout=5) as client:
  state=client.get('http://127.0.0.1:8000/api/state');state.raise_for_status();s=state.json()
  if s['busy'] or s['auto'] or s['benchmark_busy']:raise RuntimeError('앱의 게임·추론이 진행 중입니다. 기존 작업은 중단하지 않습니다.')

async def main():
 ensure_idle()
 OUT.mkdir(exist_ok=True);cfg=GameConfig(random_roles=False)
 for p,m in zip(cfg.players,MODELS):p.model=m
 cfg.inference=Inference(num_ctx=4096,num_gpu=0,num_batch=64,num_predict=512,reasoning_budget=1536,temperature=.6,thinking=False,timeout=120,retries=0)
 # A transparent synthetic fixture: all models receive the identical public contradiction.
 engine=Engine(cfg);engine.s.update(phase='defense',round=3)
 engine.event('speech','Gemma는 믿을 만해요. 아직 의심할 근거를 찾지 못했어요.',actor='qwen')
 engine.event('speech','처음부터 Gemma가 가장 수상했어요.',actor='qwen')
 engine.event('speech','Qwen, 왜 판단을 바꿨는지 이유를 듣고 싶어요.',actor='llama')
 o=Ollama();results=[]
 for player in engine.s['players']:
  task={'actor':player['id'],'kind':'speech','eligible':[]};base,_=build_prompt(engine,task)
  for profile in (False,True):
   messages,options,schema,mode=adapt_request(base,cfg.inference.model_dump(),player['model'],task,enabled=profile)
   scope=audit_prompt(engine,task,messages)
   r={'model':player['model'],'profile':profile,'fixture':'synthetic contradiction; not actual game speech','prompt_scope':scope,'mode':mode}
   ensure_idle();print('START',r['model'],profile,flush=True)
   try:
    text,metric=await o.generate(player['model'],messages,options,schema);r.update(metric,raw_output=text,outcome='success')
    data=json.loads(text) if mode=='json' else {'speech':text,'memory':'','target':None};r['speech']=data['speech']
    try:validate_speech(data['speech'],metric,engine,task);r['screen']='passed_heuristics_not_semantic_certification'
    except ModelError as e:r.update(screen='rejected',quality_error=str(e),quality_code=e.code)
   except (ModelError,ValueError) as e:r.update(getattr(e,'metrics',{}),outcome='failed',error=str(e),error_code=getattr(e,'code','parse_error'))
   results.append(r);(OUT/'dialogue-comparison.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
   print({k:r.get(k) for k in ['model','profile','outcome','screen','speech','error','quality_error','seconds','tokens','tps','done_reason','unloaded']},flush=True)
   if r.get('error_code') in ('memory_emergency_stop','memory_critical','unload_failed'):return
if __name__=='__main__':asyncio.run(main())
