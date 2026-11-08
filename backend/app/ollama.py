from __future__ import annotations
import asyncio,hashlib,json,re,time
import httpx
from .models import CATALOG
from .memory import memory_snapshot,assess_memory,emergency_reason

class ModelError(Exception):
    def __init__(self,message,*,code='model_unavailable',hint=None,attempted=False,phase='preflight',metrics=None):
        super().__init__(message);self.code=code;self.attempted=attempted;self.phase=phase;self.metrics=metrics or {}
        self.hint=hint or '모델 실험실에서 설치 상태와 메모리를 확인한 뒤 다시 시도하세요.'

def clean_final(text):
    text=re.sub(r'<think>.*?</think>','',text,flags=re.S|re.I)
    if re.search(r'<think>|</think>|<analysis>|</analysis>',text,re.I):raise ModelError('불완전한 내부 추론 블록: 공개하지 않았습니다.',code='invalid_output')
    return text.strip()

class Ollama:
    def __init__(self,url='http://127.0.0.1:11434'):
        self.url=url.rstrip('/');self.lock=asyncio.Lock();self.models={};self.active=None;self.unload_failed=False;self.descriptions={}
    async def describe(self,model):
        key=(model,self.models[model].get("digest"))
        if key not in self.descriptions:
            async with httpx.AsyncClient(timeout=8,trust_env=False) as c:
                r=await c.post(self.url+"/api/show",json={"model":model})
                if r.status_code==200:self.descriptions[key]=r.json()
                else:self.descriptions[key]={}
        description=self.descriptions[key]
        if "capabilities" in description:self.models[model]["capabilities"]=description["capabilities"]
        return description
    async def inventory(self):
        try:
            async with httpx.AsyncClient(timeout=5,trust_env=False) as c:
                v=await c.get(self.url+'/api/version');v.raise_for_status()
                r=await c.get(self.url+'/api/tags');r.raise_for_status()
                self.models={m['name']:m for m in r.json().get('models',[])}
                ps=await c.get(self.url+'/api/ps');ps.raise_for_status()
            return {'online':True,'version':v.json()['version'],'installed':list(self.models.values()),
                    'loaded':ps.json().get('models',[]),'memory':memory_snapshot(),'catalog':CATALOG,'active':self.active}
        except (httpx.HTTPError,ValueError) as e:
            return {'online':False,'error':str(e),'installed':[],'catalog':CATALOG,'memory':memory_snapshot(),'active':self.active}
    def assessment(self,model,options=None):
        m=memory_snapshot()
        if model not in self.models:
            return {'model':model,'state':'blocked','code':'model_not_installed','can_override':False,'reason':f'미설치 모델: {model}. ollama pull {model}','warnings':[],'notes':[],'memory':m}
        desc=self.models[model]
        if desc.get('remote_model') or desc.get('remote_host') or model.endswith('-cloud'):
            return {'model':model,'state':'blocked','code':'unsupported_model','can_override':False,'reason':'로컬 모델만 사용할 수 있습니다.','warnings':[],'notes':[],'memory':m}
        return assess_memory(model,desc['size'],m,options)
    def guard(self,model,options=None,*,risk_once=False):
        a=self.assessment(model,options)
        if a['state']=='blocked':raise ModelError(a['reason'],code=a['code'],metrics={'assessment':a,'memory_before':a['memory']})
        if a['state']=='warning' and not risk_once:
            raise ModelError('메모리 보호로 실행 보류: '+' '.join(a['warnings']),code='insufficient_memory',
                hint='경량 모델을 직접 선택하거나, 실험실에서 해당 모델의 경고를 확인하고 위험을 감수한 1회 실행을 선택할 수 있습니다. 게임에는 자동 적용하지 않습니다.',metrics={'assessment':a,'memory_before':a['memory']})
        return a['memory']
    async def unload(self,model):
        try:
            async with httpx.AsyncClient(timeout=8,trust_env=False) as c:
                r=await c.post(self.url+'/api/generate',json={'model':model,'keep_alive':0});r.raise_for_status()
                for _ in range(30):
                    loaded=await c.get(self.url+'/api/ps');loaded.raise_for_status()
                    if not any(m.get('name',m.get('model'))==model for m in loaded.json().get('models',[])):
                        self.unload_failed=False;return
                    await asyncio.sleep(.5)
                raise ModelError('모델 언로드를 확인하지 못했습니다. 다음 추론을 중지합니다.',code='unload_failed',phase='unloading')
        except (httpx.HTTPError,ValueError) as e:
            self.unload_failed=True
            raise ModelError('Ollama 모델 언로드 확인 실패: '+str(e),code='unload_failed',phase='unloading') from e
    async def generate(self,model,messages,options,schema=None,*,risk_once=False):
        async with self.lock:
            inventory=await self.inventory()
            if not inventory['online']:raise ModelError('Ollama 서버에 연결할 수 없습니다.',code='ollama_offline')
            loaded=inventory.get('loaded',[])
            if any(m.get('name',m.get('model'))!=model for m in loaded):
                raise ModelError('다른 모델이 로드되어 있습니다. 모델 실험실의 로드 목록을 확인하고 해당 작업이 끝난 뒤 실행하세요.',code='other_model_loaded')
            if self.unload_failed and loaded:raise ModelError('이전 모델의 해제를 확인하지 못했습니다. Ollama 상태를 확인하세요.',code='unload_failed')
            if not loaded:self.unload_failed=False
            version=tuple(int(n) for n in inventory['version'].split('.')[:3] if n.isdigit())
            if model.startswith('ministral-3:') and version<(0,13,1):raise ModelError('Ministral 3는 Ollama 0.13.1 이상이 필요합니다.',code='unsupported_version')
            description=await self.describe(model) if model in self.models else {}
            before=self.guard(model,options,risk_once=risk_once)
            assessment=self.assessment(model,options)
            self.active={'model':model,'state':'loading','started':time.time(),'risk_once':risk_once}
            stop=asyncio.Event();pressure_reason=[];request_task=asyncio.current_task();start=time.monotonic()
            phase='loading';attempted=False;first=None;visible=None;chunks=[];final={};samples=[before];peak=before['used_gb'];peak_swap=before.get('swap_gb')
            def metrics():
                after=memory_snapshot();swap_before=before.get('swap_gb');swap_after=after.get('swap_gb')
                duration=final.get('eval_duration',0)/1e9;tokens=final.get('eval_count',0)
                return {'model':model,'digest':self.models[model].get('digest'),'seconds':round(time.monotonic()-start,3),
                    'first_token_seconds':round(first,3) if first is not None else None,'first_content_seconds':round(visible,3) if visible is not None else None,
                    'load_seconds':round(final.get('load_duration',0)/1e9,3),'tokens':tokens,'tps':round(tokens/duration,2) if duration else None,
                    'prompt_tokens':final.get('prompt_eval_count'),'template_sha256':hashlib.sha256(description.get('template','').encode()).hexdigest(),'template_source':description.get('template_source'),'renderer':description.get('renderer'),'parser':description.get('parser'),'capabilities':self.models[model].get('capabilities',[]),'thinking_configured':options.get('thinking'),'done_reason':final.get('done_reason'),'memory_before':before,'memory_after':after,
                    'peak_used_gb':peak,'peak_swap_gb':peak_swap,'swap_delta_gb':round(swap_after-swap_before,3) if swap_before is not None and swap_after is not None else None,
                    'memory_samples':samples[-180:],'assessment':assessment,'risk_once':risk_once,'attempted':attempted,'phase':phase,'runtime_options':dict(payload['options']),'thinking_requested':payload.get('think')}
            async def sample():
                nonlocal peak,peak_swap
                while not stop.is_set():
                    m=memory_snapshot();samples.append(m);peak=max(peak,m['used_gb'])
                    if m.get('swap_gb') is not None:peak_swap=max(peak_swap or 0,m['swap_gb'])
                    reason=emergency_reason(m)
                    delta=(m.get('swap_gb') or 0)-(before.get('swap_gb') or 0)
                    if not reason and delta>1 and m['available_gb']<.5:reason='실행 중 스왑이 1GiB 이상 증가했고 가용 메모리가 0.50GiB 미만입니다.'
                    if reason:
                        pressure_reason.append(reason);request_task.cancel();return
                    try:await asyncio.wait_for(stop.wait(),.5)
                    except TimeoutError:pass
            sampler=asyncio.create_task(sample())
            budget=options['reasoning_budget'] if model.startswith('deepseek-r1') and options.get('thinking') is not False else options['num_predict']
            payload={'model':model,'messages':messages,'stream':True,'keep_alive':0,
                'options':{k:options[k] for k in ('num_ctx','temperature','top_p')}|{'num_predict':budget}}
            if options.get('num_batch') is not None:payload['options']['num_batch']=options['num_batch']
            payload['options']['use_mmap']=True
            if options.get('num_gpu') is not None:payload['options']['num_gpu']=options['num_gpu']
            if options.get('thinking') is not None and 'thinking' in self.models[model].get('capabilities',[]):payload['think']=options['thinking']
            elif model.startswith('qwen3:'):payload['think']=False
            if schema:payload['format']=schema
            result=None;failure=None;cancelled=None
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(options['timeout'],connect=5),trust_env=False) as client:
                    async with asyncio.timeout(options['timeout']):
                        attempted=True
                        async with client.stream('POST',self.url+'/api/chat',json=payload) as r:
                            if r.status_code!=200:
                                await r.aread();detail=r.text[:400]
                                code='ollama_oom' if any(t in detail.lower() for t in ('out of memory','not enough','requires more','unable to allocate')) else 'ollama_http_error'
                                raise ModelError(f'Ollama HTTP {r.status_code}: {detail}',code=code,attempted=True,phase=phase)
                            async for line in r.aiter_lines():
                                if not line:continue
                                data=json.loads(line)
                                if data.get('error'):raise ModelError(str(data['error']),code='ollama_generation_failed',attempted=True,phase=phase)
                                msg=data.get('message',{})
                                if (msg.get('content') or msg.get('thinking')) and first is None:first=time.monotonic()-start
                                if msg.get('content') or msg.get('thinking'):
                                    phase='generating';self.active['state']=phase
                                # Thinking is never stored or forwarded.
                                if msg.get('content'):
                                    if visible is None:visible=time.monotonic()-start
                                    chunks.append(msg['content'])
                                if data.get('done'):final=data
                if not final:raise ModelError('완료 신호 없는 응답',code='incomplete_response')
                if final.get('model',model)!=model:raise ModelError('모델 태그 불일치',code='model_mismatch')
                content=clean_final(''.join(chunks))
                if not content:raise ModelError(f'{model}: 최종 발언 없음 (생성 {final.get("eval_count",0)}토큰, 종료 {final.get("done_reason")}). 내부 추론이 예산을 소진했을 수 있습니다. 모델 실험실에서 추론 예산·모드를 비교하세요. 충분한 예산에서도 반복되면 현재 모델의 실행 한계일 수 있습니다.',code='empty_final_output')
                result=(content,metrics())
            except asyncio.CancelledError as e:
                if pressure_reason:failure=ModelError('메모리 압박으로 실행 중지: '+pressure_reason[0],code='memory_emergency_stop',attempted=attempted,phase=phase,metrics=metrics())
                else:cancelled=e;cancelled.metrics=metrics()
            except ModelError as e:
                e.attempted=attempted;e.phase=phase;e.metrics=metrics();failure=e
            except (httpx.HTTPError,TimeoutError,ValueError) as e:
                failure=ModelError(f'실제 Ollama 실행 실패: {type(e).__name__}: {e}',code='timeout' if isinstance(e,TimeoutError) else 'ollama_transport_error',attempted=attempted,phase=phase,metrics=metrics())
            finally:
                stop.set();await sampler;self.active['state']='unloading'
                try:await self.unload(model)
                except ModelError as e:
                    self.unload_failed=True;e.attempted=attempted;e.metrics=metrics();failure=e
                self.active=None
            after_unload=memory_snapshot()
            after_delta=round(after_unload['swap_gb']-before['swap_gb'],3) if after_unload.get('swap_gb') is not None and before.get('swap_gb') is not None else None
            if failure:
                failure.metrics.update(memory_after_unload=after_unload,swap_delta_after_unload_gb=after_delta,unloaded=not self.unload_failed);raise failure
            if cancelled:
                cancelled.metrics.update(memory_after_unload=after_unload,swap_delta_after_unload_gb=after_delta,unloaded=not self.unload_failed);raise cancelled
            result[1].update(unloaded=True,memory_after_unload=after_unload,swap_delta_after_unload_gb=after_delta)
            return result

class MockAI:
    """Test-only fixtures; UI always labels these games MOCK, never official."""
    async def act(self,engine,task):
        await asyncio.sleep(.015)
        actor=task['actor'];valid=task['eligible'];kind=task['kind']
        # Deterministic fixture creates progress without pretending to be a real model.
        target=(valid[-1] if kind=='vote' else valid[0]) if valid else None
        return {'speech':f'[MOCK 기능 테스트] {actor} / {kind} / Day {engine.s["day"]}',
                'target':target,'memory':'Mock 검증 메모'}, {'seconds':.015,'tps':None,'tokens':0,'model':engine.player(actor)['model'],'mock':True}
