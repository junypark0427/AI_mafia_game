from __future__ import annotations
import asyncio,json,re,time
import httpx,psutil
from .models import CATALOG

class ModelError(Exception):pass

def memory_snapshot():
    v=psutil.virtual_memory()
    try:swap_used=psutil.swap_memory().used
    except OSError:swap_used=None
    return {'total_gb':round(v.total/2**30,2),'available_gb':round(v.available/2**30,2),
            'used_gb':round((v.total-v.available)/2**30,2),'swap_gb':round(swap_used/2**30,2) if swap_used is not None else None}

def clean_final(text):
    # Fail closed on an unfinished reasoning block; never forward it to any agent.
    text=re.sub(r'<think>.*?</think>','',text,flags=re.S|re.I)
    if re.search(r'<think>|</think>|<analysis>|</analysis>',text,re.I):raise ModelError('불완전한 내부 추론 블록: 공개하지 않았습니다.')
    return text.strip()

class Ollama:
    def __init__(self,url='http://127.0.0.1:11434'):
        self.url=url.rstrip('/');self.lock=asyncio.Lock();self.models={};self.active=None
    async def inventory(self):
        try:
            async with httpx.AsyncClient(timeout=5,trust_env=False) as c:
                v=await c.get(self.url+'/api/version');v.raise_for_status()
                r=await c.get(self.url+'/api/tags');r.raise_for_status()
                self.models={m['name']:m for m in r.json().get('models',[])}
                ps=await c.get(self.url+'/api/ps')
            return {'online':True,'version':v.json()['version'],'installed':list(self.models.values()),
                    'loaded':ps.json().get('models',[]) if ps.is_success else [],'memory':memory_snapshot(),'catalog':CATALOG,'active':self.active}
        except (httpx.HTTPError,ValueError) as e:
            return {'online':False,'error':str(e),'installed':[],'catalog':CATALOG,'memory':memory_snapshot(),'active':self.active}
    def guard(self,model):
        if model not in self.models:raise ModelError(f'미설치 모델: {model}. ollama pull {model}')
        m=memory_snapshot();size=self.models[model]['size']/2**30
        if self.models[model].get('remote_model') or self.models[model].get('remote_host') or model.endswith('-cloud'):
            raise ModelError('클라우드 모델은 사용할 수 없습니다. 로컬 Ollama 태그를 선택하세요.')
        if m['swap_gb'] is None:
            raise ModelError('메모리 보호: 운영체제의 스왑 정보를 읽을 수 없어 추론을 보류했습니다.')
        if size>4 and m['swap_gb']>2:
            raise ModelError(f"메모리 보호: {model} 실행 보류. 현재 스왑 {m['swap_gb']}GB. 앱을 정리하거나 경량 태그를 직접 선택하세요.")
        required=size*1.12+.4
        if m['available_gb']<required:
            raise ModelError(f"메모리 보호: 가용 {m['available_gb']}GB, 보수적 필요량 약 {required:.1f}GB. 경량 모델을 직접 선택하세요.")
        return m
    async def generate(self,model,messages,options,schema=None):
        async with self.lock:
            inventory=await self.inventory()
            if not inventory['online']:raise ModelError('Ollama 서버에 연결할 수 없습니다.')
            if inventory.get('loaded'):raise ModelError('다른 모델이 메모리에 남아 있습니다. Ollama에서 해당 모델을 해제한 뒤 다시 시도하세요.')
            version=tuple(int(n) for n in inventory['version'].split('.')[:3] if n.isdigit())
            if model.startswith('ministral-3:') and version<(0,13,1):raise ModelError('Ministral 3는 Ollama 0.13.1 이상이 필요합니다.')
            before=self.guard(model)
            self.active={'model':model,'state':'loading','started':time.time()}
            peak=before['used_gb'];peak_swap=before['swap_gb'] or 0;stop=asyncio.Event();pressure_reason=[];request_task=asyncio.current_task()
            async def sample():
                nonlocal peak,peak_swap
                while not stop.is_set():
                    m=memory_snapshot();peak=max(peak,m['used_gb']);peak_swap=max(peak_swap,m['swap_gb'] or 0)
                    if m['available_gb']<.3 or (m['swap_gb'] is not None and m['swap_gb']-before['swap_gb']>1):
                        pressure_reason.append('추론 중 메모리 압박 증가: 요청을 취소하고 모델을 해제했습니다.')
                        request_task.cancel();return
                    try:await asyncio.wait_for(stop.wait(),.5)
                    except TimeoutError:pass
            sampler=asyncio.create_task(sample());start=time.monotonic();first=None;visible=None;chunks=[];final={}
            budget=options['reasoning_budget'] if model.startswith('deepseek-r1') else options['num_predict']
            payload={'model':model,'messages':messages,'stream':True,'keep_alive':0,
              'options':{k:options[k] for k in ('num_ctx','temperature','top_p')}|{'num_predict':budget}}
            if model.startswith('qwen3:'):payload['think']=False
            if schema:payload['format']=schema
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(options['timeout'],connect=5),trust_env=False) as client:
                    # Wall-clock timeout also bounds a stream that keeps sending tiny chunks.
                    async with asyncio.timeout(options['timeout']):
                        async with client.stream('POST',self.url+'/api/chat',json=payload) as r:
                            if r.status_code!=200:
                                await r.aread();raise ModelError(f'Ollama HTTP {r.status_code}: {r.text[:300]}')
                            async for line in r.aiter_lines():
                                if not line:continue
                                data=json.loads(line)
                                if data.get('error'):raise ModelError(str(data['error']))
                                msg=data.get('message',{})
                                if (msg.get('content') or msg.get('thinking')) and first is None:first=time.monotonic()-start
                                # The thinking field is deliberately never stored, logged, or forwarded.
                                if msg.get('content'):
                                    if visible is None:visible=time.monotonic()-start
                                    chunks.append(msg['content']);self.active['state']='generating'
                                if data.get('done'):final=data
                if not final:raise ModelError('완료 신호 없는 응답')
                actual=final.get('model',model)
                if actual!=model:raise ModelError(f'모델 태그 불일치: 요청 {model}, 응답 {actual}')
                content=clean_final(''.join(chunks))
                if not content:raise ModelError('최종 발언 없음: 내부 추론 예산 소진 가능. 추론 예산을 조절하세요.')
                seconds=time.monotonic()-start;duration=final.get('eval_duration',0)/1e9;tokens=final.get('eval_count',0)
                return content,{'model':model,'digest':self.models[model].get('digest'),'seconds':round(seconds,3),
                  'first_token_seconds':round(first,3) if first is not None else None,'first_content_seconds':round(visible,3) if visible is not None else None,
                  'load_seconds':round(final.get('load_duration',0)/1e9,3),'tokens':tokens,
                  'tps':round(tokens/duration,2) if duration else None,'done_reason':final.get('done_reason'),
                  'memory_before':before,'peak_used_gb':peak,'peak_swap_gb':peak_swap,'memory_after':memory_snapshot()}
            except asyncio.CancelledError:
                if pressure_reason:raise ModelError(pressure_reason[0])
                raise
            except (httpx.HTTPError,TimeoutError,ValueError) as e:raise ModelError(f'추론 실패: {type(e).__name__}: {e}') from e
            finally:
                stop.set();await sampler
                # Explicit unload even after timeout/parse errors; the next call still waits for this lock.
                try:
                    async with httpx.AsyncClient(timeout=15,trust_env=False) as c:
                        await c.post(self.url+'/api/generate',json={'model':model,'keep_alive':0})
                        for _ in range(20):
                            loaded=await c.get(self.url+'/api/ps')
                            if not any(m.get('name')==model for m in loaded.json().get('models',[])):break
                            await asyncio.sleep(.5)
                        else:raise ModelError('모델 해제를 확인하지 못해 다음 추론을 중지했습니다.')
                except httpx.HTTPError as e:
                    self.active={'model':model,'state':'unload_error'}
                    raise ModelError('Ollama 모델 해제 확인 실패. 서버 상태를 확인하세요.') from e
                self.active=None

class MockAI:
    """Test-only fixtures; UI always labels these games MOCK, never official."""
    async def act(self,engine,task):
        await asyncio.sleep(.015)
        actor=task['actor'];valid=task['eligible'];kind=task['kind']
        # Deterministic fixture creates progress without pretending to be a real model.
        target=(valid[-1] if kind=='vote' else valid[0]) if valid else None
        return {'speech':f'[MOCK 기능 테스트] {actor} / {kind} / Day {engine.s["day"]}',
                'target':target,'memory':'Mock 검증 메모'}, {'seconds':.015,'tps':None,'tokens':0,'model':engine.player(actor)['model'],'mock':True}
