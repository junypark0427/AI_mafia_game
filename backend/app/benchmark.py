from .engine import now
from .prompts import RULES
from .ollama import ModelError
from .quality import validate_speech
SCENARIOS=[
 ('first_day','너는 시민이다. 첫날 아무 정보가 없다. 참가자는 DeepSeek, Gemma, Ministral, EXAONE, Qwen, Llama다. 지금 토론에서 할 말을 2~4문장으로 말하라.'),
 ('accused','너는 시민이다. Gemma가 "당신은 근거 없이 Qwen을 몰아갔으니 마피아야"라고 의심한다. 자연스럽게 대응하라.'),
 ('contradiction','너는 시민이다. Qwen은 어제 "Gemma를 믿는다"고 했고 오늘 "처음부터 Gemma가 수상했다"고 말했다. 이 모순에 반응하라.'),
 ('bluff','너는 실제 마피아다. 경찰을 사칭할지 숨길지는 자유다. Llama가 네 투표 기록을 의심한다. 이기기 위한 말을 하라.'),
 ('defense','너는 시민이다. 동료들이 너를 처형하려 한다. 마지막으로 설득하거나 원하는 반응을 하라.'),
 ('dead','너는 억울하게 처형된 시민이다. 생존한 Qwen이 "잘 처형한 것 같다"고 말했다. 사망자 전용 채팅에서 관전 소감을 말하라.'),
]
async def run_benchmark(ollama,store,models,options,quality=False,*,risk_once=False,progress=None):
    import asyncio
    options=dict(options)
    if risk_once:options['retries']=0
    results=[]
    progress=progress if progress is not None else {}
    # A risk acknowledgement covers one model, one prompt, one actual attempt.
    if risk_once and (len(models)!=1 or quality):raise ValueError('위험 실행은 단일 모델·단일 응답만 허용합니다.')
    for model in models:
        for scenario,prompt in SCENARIOS if quality else SCENARIOS[:1]:
            progress.update(model=model,scenario=scenario,phase='preflight')
            limit=1 if risk_once else min(options.get('retries',1)+1,2)
            for attempt in range(limit):
                r={'at':now(),'model':model,'scenario':scenario,'quality_status':'사람의 검토 필요','options':dict(options),
                   'risk_once':risk_once,'attempt':attempt+1,'attempted':False,'outcome':'deferred'}
                try:
                    kwargs={'risk_once':True} if risk_once else {}
                    content,metrics=await ollama.generate(model,[{'role':'system','content':RULES},{'role':'user','content':prompt}],options,**kwargs)
                    r.update(metrics,output=content,error=None,outcome='success',hangul_characters=sum('가'<=c<='힣' for c in content))
                    validate_speech(content,metrics)
                    r['quality_status']='형식 검사 통과 · 의미/전략은 사람의 검토 필요'
                except asyncio.CancelledError as e:
                    r.update(getattr(e,'metrics',{}),error='사용자가 실험을 중단했습니다.',error_code='cancelled',output=None,outcome='cancelled')
                    store.benchmark(r);results.append(r);progress.update(phase='cancelled');return results
                except ModelError as e:
                    outcome='emergency_stopped' if e.code=='memory_emergency_stop' else 'failed' if e.attempted else 'deferred'
                    r.update(e.metrics,error=str(e),error_code=e.code,output=r.get('output'),outcome=outcome,attempted=e.attempted,phase=e.phase)
                store.benchmark(r);results.append(r);progress.update(completed=len(results),last_outcome=r['outcome'])
                retry=r.get('error_code') in ('ollama_transport_error','ollama_http_error','timeout') and attempt+1<limit
                if not retry:break
                progress['phase']='retrying';await asyncio.sleep(.5)
            # This model's refusal/failure never prevents the next selected model from running.
            if r.get('error'):break
    return results
