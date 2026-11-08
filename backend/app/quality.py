"""Technical model profiles and conservative output checks, never personality presets.
Heuristics detect obvious failures; they cannot certify reasoning or Korean fluency.
"""
import copy,re
from .ollama import ModelError
from .prompts import schema_for

PROFILE_VERSION='korean-dialogue-v1'
SPEECH_KINDS={'speech','secret','dead','interview'}

def fit_request(messages,options,model):
    """Reserve the output budget after technical hints; never trim confirmed facts."""
    import json
    reserve=options['reasoning_budget'] if model.startswith('deepseek-r1') and options.get('thinking') is not False else options['num_predict']
    def size():
        text=''.join(m['content'] for m in messages);cjk=sum(ord(c)>127 for c in text)
        return int(cjk*1.1+(len(text)-cjk)/3.5)+64
    owner=next((m for m in messages if '확정 공개 사건과 주관적 기억을 구분한 자료: ' in m['content']),None)
    if owner:
        prefix='확정 공개 사건과 주관적 기억을 구분한 자료: '
        raw=owner['content'].partition(prefix)[2].split('\n\n')[0]
        context=json.loads(raw)
        for key in ('recent_public_speech','your_previous_statements','your_subjective_notes','faction_chat','dead_chat'):
            while size()+reserve>options['num_ctx'] and context.get(key):
                context[key].pop(0)
                if key=='recent_public_speech':context['omitted_old_speech']+=1
                new=json.dumps(context,ensure_ascii=False);owner['content']=owner['content'].replace(prefix+raw,prefix+new,1);raw=new
    if size()+reserve>options['num_ctx']:
        raise ModelError('컨텍스트 보호: 확정 사건과 비밀 정보를 보존할 공간이 부족합니다. 다음 경기의 컨텍스트 크기를 늘리세요.',code='context_full')
    return messages

def adapt_request(messages,options,model,task,*,enabled=None):
    messages=copy.deepcopy(messages);options=dict(options)
    enabled=options.get('model_profiles',True) if enabled is None else enabled
    protocol='json';schema=schema_for(task)
    if not enabled:return messages,options,schema,protocol
    # Small general models spend less of their output on envelopes/duplicate notes.
    # Their verbatim own statements remain in personal memory; no summary is invented.
    if task['kind'] in SPEECH_KINDS and model.startswith(('deepseek-r1:','gemma3:1b','llama3.2:')):
        protocol='text';schema=None
        marker=' 선택 가능 ID: '
        before,sep,after=messages[-1]['content'].partition(marker)
        style=after.partition('\n\n')[2] if sep else ''
        messages[-1]['content']=before
        messages[-1]['content']+='\n이번 응답은 본인 발언 두세 문장만 자연스러운 한국어로 말하라. JSON, 메모, 진행자 설명, 다른 사람의 대사는 쓰지 마라. 참가자 이름 외 불필요한 외국어를 섞지 마라. 인용된 글의 깨진 문체는 따라 쓰지 마라.'
        if style:messages[-1]['content']+='\n\n'+style
    elif task['kind'] in SPEECH_KINDS:
        messages[-1]['content']+='\n발언은 두세 문장으로 마무리하고 memory는 짧은 주관적 메모 한 문장만 써라. 자기 직업의 실제 능력은 비밀 정보 기준이다. 공개 사칭은 전략으로 선택할 수 있다.'
    if model.startswith('deepseek-r1:'):
        # DeepSeek's published recommendation: user instructions, temperature 0.5–0.7.
        options['temperature']=min(.7,max(.5,options['temperature']))
        messages=[{'role':'user','content':'\n\n'.join(m['content'] for m in messages)}]
    return fit_request(messages,options,model),options,schema,protocol

def validate_speech(speech,metric,engine=None,task=None):
    def reject(message,code='dialogue_quality'):
        raise ModelError(message,code=code,hint='실제 원문은 관리자 기록에 보존됩니다. 같은 모델로 제한 재시도 후에도 반복되면 모델 태그 또는 출력 예산을 직접 바꾸세요.',attempted=True,phase='generating',metrics=metric)
    if metric.get('done_reason')=='length':reject('출력 토큰 한도에 도달한 응답을 게시하지 않았습니다. 출력 예산을 늘리거나 더 짧은 발언 설정으로 다시 시도하세요.','output_truncated')
    if speech.strip()=='[침묵]':return
    text=speech
    # Names and explicitly enabled theatrical vocabulary are not language failures.
    names=['DeepSeek','Gemma','Ministral','EXAONE','Qwen','Llama','AI','Day','R1']
    if engine and task:
        names += [p['name'] for p in engine.s['players']]
        c=engine.s['config']['characters'][task['actor']]
        if engine.s['config']['mode']=='character' and c['enabled']:
            names += re.findall(r'[A-Za-z]+',c['vocabulary'])
    for name in sorted(set(names),key=len,reverse=True):text=re.sub(re.escape(name),'',text,flags=re.I)
    hangul=sum('가'<=c<='힣' for c in text)
    latin=sum(c.isascii() and c.isalpha() for c in text)
    other=sum(c.isalpha() and not c.isascii() and not ('가'<=c<='힣') for c in text)
    if hangul<3 or latin+other>max(12,hangul*.3):reject('한국어 발언 형식 검사에서 외국어 혼합 또는 한국어 부족을 발견했습니다. 실패 원문은 공개 발언으로 사용하지 않습니다.')
    compact=re.sub(r'\s+','',speech)
    if any(compact.count(compact[i:i+24])>=4 for i in range(max(0,len(compact)-23))):reject('같은 구절이 과도하게 반복된 응답을 게시하지 않았습니다.','dialogue_repetition')
    if engine and task:
        own=[e['text'] for e in engine.s['events'] if e.get('actor')==task['actor'] and e['kind'] in SPEECH_KINDS]
        if own and len(compact)>35 and re.sub(r'\s+','',own[-1])==compact:reject('직전 발언을 그대로 복제한 응답을 게시하지 않았습니다.','dialogue_repetition')
