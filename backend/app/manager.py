from __future__ import annotations
import asyncio,copy,json,time
from .models import GameConfig
from .quality import adapt_request,validate_speech,fit_request,PROFILE_VERSION,SPEECH_KINDS
from .engine import Engine,now
from .ollama import ModelError,MockAI,clean_final
from .prompts import build_prompt,audit_prompt,schema_for,style_snapshot,SCHEMA

class Conflict(Exception):pass

class Manager:
    def __init__(self,store,ollama):
        self.store=store;self.ollama=ollama;self.game=None;self.lock=asyncio.Lock()
        self.auto_task=None;self.pause_requested=False;self.stop_requested=False;self.busy=None
        self.last_error=None;self.series_cancelled=False;self.benchmark_busy=False
    def save(self):
        self.game.touch();self.store.save(self.game.s)
    async def start(self,cfg):
        if self.lock.locked() or self.benchmark_busy:raise Conflict('현재 작업 완료를 기다리세요.')
        if self.game and self.game.s['phase']!='finished' and self.game.s['status'] not in ('aborted','invalid','interrupted'):
            raise Conflict('진행 중인 경기를 먼저 중단하세요.')
        async with self.lock:
            if cfg.provider=='ollama':
                inventory=await self.ollama.inventory()
                if not inventory['online']:raise ModelError('Ollama 서버를 시작하세요.',code='ollama_offline')
                for p in cfg.players:self.ollama.guard(p.model,cfg.inference.model_dump())
            self.game=Engine(cfg);self.last_error=None;self.series_cancelled=False
            for p in self.game.s['players']:p['digest']=self.ollama.models.get(p['model'],{}).get('digest') if cfg.provider=='ollama' else 'mock'
            self.pause_requested=False;self.stop_requested=False;self.save()
        if cfg.auto:self.resume()
        return self.snapshot()
    def snapshot(self):
        return {'game':self.game.public() if self.game else None,'busy':self.busy,'error':self.last_error,
                'pause_requested':self.pause_requested,'benchmark_busy':self.benchmark_busy,'auto':self.auto_task is not None and not self.auto_task.done()}
    def resume(self):
        if self.lock.locked() or self.benchmark_busy:raise Conflict('현재 요청 완료 후 자동 진행을 시작하세요.')
        if not self.game or self.game.s['phase']=='finished':raise Conflict('진행 가능한 게임이 없습니다.')
        if self.game.s['status'] in ('aborted','invalid','interrupted'):raise Conflict('종료된 경기입니다.')
        self.pause_requested=False;self.stop_requested=False
        if self.game.s['status']!='completed':self.game.s['status']='running'
        self.last_error=None;self.save()
        if not self.auto_task or self.auto_task.done():self.auto_task=asyncio.create_task(self.loop())
    def pause(self,stop=False):
        self.pause_requested=True
        if stop:self.stop_requested=True;self.series_cancelled=True
        if not self.busy:self.apply_control()
    def apply_control(self):
        if self.stop_requested:
            if self.game.s['status']!='completed':self.game.s['status']='aborted'
            self.game.s['phase']='finished';self.game.event('system','관리자가 진행을 중단했습니다.')
            self.stop_requested=False
        elif self.pause_requested and self.game.s['status']=='running':self.game.s['status']='paused'
        self.save()
    async def step(self,expected=None,automatic=False):
        if self.lock.locked() or self.benchmark_busy:raise Conflict('이미 한 단계가 실행 중입니다.')
        async with self.lock:
            if not self.game or self.game.s['phase']=='finished':raise Conflict('진행 가능한 단계가 없습니다.')
            if self.game.s['status'] in ('aborted','invalid','interrupted'):raise Conflict('종료된 경기입니다.')
            if expected is not None and expected!=self.game.s['revision']:raise Conflict('이미 처리된 화면입니다. 새로고침 후 다시 시도하세요.')
            if not automatic and self.auto_task and not self.auto_task.done():raise Conflict('자동 진행을 먼저 일시정지하세요.')
            if automatic and self.pause_requested:return
            g=self.game;task=g.next_task();self.last_error=None
            if task and len(g.s['calls'])>=g.s['config'].get('max_calls',300):
                self.finish_limit('모델 호출 횟수 한도');return self.snapshot()
            try:
                if task:
                    # Snapshot BEFORE awaiting generation. Mode edits affect only the next request.
                    frozen=Engine.restore(copy.deepcopy(g.s));messages,style=build_prompt(frozen,task)
                    messages,options,schema,protocol=adapt_request(messages,g.s['config']['inference'],g.player(task['actor'])['model'],task)
                    prompt_scope=audit_prompt(frozen,task,messages)
                    self.busy={'actor':task['actor'],'model':g.player(task['actor'])['model'],'kind':task['kind'],'style':style,'started':time.time()}
                    tries=g.s['config']['inference']['retries']+1
                    for attempt in range(tries):
                        if len(g.s['calls'])>=g.s['config'].get('max_calls',300):
                            self.finish_limit('모델 호출 횟수 한도');break
                        metric=None;text=None
                        try:
                            if g.s['config']['provider']=='mock':result,metric=await MockAI().act(frozen,task)
                            else:
                                text,metric=await self.ollama.generate(g.player(task['actor'])['model'],messages,options,schema)
                                if metric.get('done_reason')=='length':validate_speech('',metric,frozen,task)
                                result=json.loads(clean_final(text)) if protocol=='json' else {'speech':clean_final(text),'memory':'','target':None}
                                if not isinstance(result,dict) or set(result)!={'speech','target','memory'}:raise ValueError('JSON 필드가 유효하지 않습니다.')
                                if not isinstance(result['speech'],str) or not isinstance(result['memory'],str):raise ValueError('발언 및 메모 타입 오류')
                                result['speech']=clean_final(result['speech']);result['memory']=clean_final(result['memory'])
                            if g.s['config']['provider']=='ollama' and metric.get('digest')!=g.player(task['actor'])['digest']:
                                raise ModelError('경기 중 모델 버전이 변경되었습니다. 새 경기를 시작하세요.')
                            if len(result.get('speech',''))>1800 or len(result.get('memory',''))>220:raise ValueError('출력 길이 제한 초과')
                            if g.s['config']['provider']=='ollama' and task['kind'] in SPEECH_KINDS:validate_speech(result['speech'],metric,frozen,task)
                            if metric is not None:metric.update(profile_version=PROFILE_VERSION if options.get('model_profiles',True) else None,response_protocol=protocol)
                            g.apply(task,result,style)
                            g.s['calls'].append(dict(metric,prompt_scope=prompt_scope,actor=task['actor'],kind=task['kind'],day=g.s['day'],target=result.get('target'),style=style,at=now()))
                            break
                        except (ModelError,ValueError,TypeError) as e:
                            failed_metric=metric or (e.metrics if isinstance(e,ModelError) else {})
                            g.s['calls'].append(dict(failed_metric,prompt_scope=prompt_scope,actor=task['actor'],model=g.player(task['actor'])['model'],kind=task['kind'],day=g.s['day'],seconds=failed_metric.get('seconds',0),raw_output=text,error=str(e),error_code=e.code if isinstance(e,ModelError) else 'invalid_action',style=style,at=now()))
                            retryable=not isinstance(e,ModelError) or e.code in ('ollama_transport_error','ollama_http_error','timeout','invalid_output','empty_final_output','output_truncated','dialogue_quality','dialogue_repetition')
                            if attempt+1==tries or not retryable:
                                if isinstance(e,ModelError):raise
                                raise ModelError(str(e),code='invalid_action') from e
                            messages=messages+[{'role':'user','content':('직전 응답을 게시하지 않았다. '+str(e)+' 본인의 새 발언을 자연스러운 한국어 두 문장으로 마무리하라. 내부 추론은 쓰지 마라.' if protocol=='text' else '유효하지 않은 응답이었다. '+str(e)+' 정해진 JSON 형식과 선택 가능 대상만 사용하라. speech는 한국어 두 문장, memory는 짧게 마무리하고 내부 추론은 쓰지 마라.')}]
                            messages=fit_request(messages,options,g.player(task['actor'])['model'])
                            prompt_scope=audit_prompt(frozen,task,messages)
                else:g.advance()
            except ModelError as e:
                self.last_error=str(e);self.pause_requested=True
                if g.s['status']!='completed':g.s['status']='paused'
                g.s['admin_events'].append({'at':now(),'type':'model_error','message':str(e)})
            finally:
                self.busy=None
                if self.pause_requested or self.stop_requested:self.apply_control()
                else:self.save()
            return self.snapshot()
    def finish_limit(self,reason):
        s=self.game.s
        if s['status']=='completed':s['interviews_interrupted']=True
        else:s['status']='invalid';s['winner']=None
        s['phase']='finished';self.pause_requested=True
        self.game.event('system',reason+' 도달 · '+('인터뷰 중단' if s['status']=='completed' else '무효 경기'))
        self.last_error=reason+'에 도달해 종료했습니다. 한도를 무제한으로 늘리지 말고 모델 응답 기록을 확인하세요.'
        self.save()

    async def loop(self):
        deadline=time.monotonic()+self.game.s['config'].get('max_runtime_minutes',45)*60
        try:
            while not self.pause_requested and not self.series_cancelled:
                if time.monotonic()>deadline:
                    self.finish_limit('자동 진행 실행 시간 한도');break
                await self.step(automatic=True)
                if self.game.s['phase']=='finished':
                    s=self.game.s;cfg=GameConfig(**s['config'])
                    if s['status']=='completed' and s['series_index']+1<cfg.series_count:
                        self.game=Engine(cfg,s['series_id'],s['series_index']+1,s['initial_roles'])
                        for p in self.game.s['players']:p['digest']=self.ollama.models.get(p['model'],{}).get('digest') if cfg.provider=='ollama' else 'mock'
                        self.game.s['status']='running';self.save()
                    else:break
                await asyncio.sleep(.25)
        except Exception as e:
            self.last_error=f'진행 중단: {type(e).__name__}: {e}';self.pause_requested=True
            if self.game and self.game.s['status']!='completed':self.game.s['status']='paused'
            if self.game:self.save()
    def change_style(self,mode,intensity,characters):
        if not self.game or self.game.s['phase']=='finished':raise Conflict('진행 중인 게임이 없습니다.')
        s=self.game.s;old=s['config']['mode'];changed=mode!=old or intensity!=s['config']['intensity'] or characters!=s['config']['characters']
        if not changed:return
        s['config'].update(mode=mode,intensity=intensity,characters=characters)
        s['mode_history'].append(mode);s['mixed']=True
        s['admin_events'].append({'at':now(),'type':'style_change','from':old,'to':mode,'intensity':intensity,'revision':s['revision'],'effective':'next_request','characters':copy.deepcopy(characters)})
        self.save()
