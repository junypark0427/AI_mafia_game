"""Public request errors never echo request bodies or Pydantic input/ctx."""
from .models import CATALOG

FIELD_NAMES={
    'players':'참가자 설정','enabled_roles':'직업 활성화','role':'직업','model':'Ollama 모델',
    'id':'ID','name':'이름','characters':'캐릭터 설정','inference':'추론 설정',
    'num_ctx':'컨텍스트 크기','num_predict':'출력 토큰 예산','reasoning_budget':'추론 토큰 예산',
    'temperature':'Temperature','top_p':'Top P','timeout':'요청 제한 시간','retries':'재요청 횟수',
    'rounds':'낮 토론 라운드','series_count':'연속 대전 수','dead_reactions':'사망자 반응 횟수',
    'max_days':'최대 게임 일수','provider':'실행 공급자','mode':'대화 모드','intensity':'말투 강도',
    'instructions':'말투 지침','description':'설명','vocabulary':'주요 어휘','avoid':'사용하지 않을 표현',
    'max_calls':'모델 호출 한도','max_runtime_minutes':'자동 진행 시간 한도','num_batch':'처리 배치','model_profiles':'모델별 출력 보정','thinking':'추론 모드','num_gpu':'CPU 실행','version':'캐릭터 버전','order':'발언 순서',
}
MODEL_FIELDS={'participants_count':'players','player_ids':'players','models_distinct':'players',
    'mafia_count':'players','mafia_team_count':'players','role_count':'players',
    'assigned_role_disabled':'enabled_roles','character_missing':'characters'}

def field_label(path):
    if len(path)>1 and path[0]=='players' and isinstance(path[1],int):
        return f'참가자 {path[1]+1}의 '+FIELD_NAMES.get(str(path[-1]),str(path[-1]))
    if len(path)>1 and path[0]=='characters':
        name=next((c['name'] for c in CATALOG if c['id']==path[1]),'참가자')
        return name+' 캐릭터 '+FIELD_NAMES.get(str(path[-1]),'설정')
    return FIELD_NAMES.get(str(path[-1]),str(path[-1])) if path else '게임 설정'

def validation_response(exc):
    details=[];issues=[]
    for error in exc.errors():
        kind=error['type'];loc=list(error.get('loc',[]));path=[p for p in loc if p not in ('body','query','path')]
        if kind in MODEL_FIELDS:
            path=[MODEL_FIELDS[kind]];message=error['msg']
        else:
            label=field_label(path);ctx=error.get('ctx',{})
            if kind=='missing':message=f'{label}: 필수 항목이 빠졌습니다. 값을 선택하거나 입력하세요.'
            elif kind=='greater_than_equal':message=f'{label}: {ctx["ge"]} 이상으로 입력하세요.'
            elif kind=='less_than_equal':message=f'{label}: {ctx["le"]} 이하로 입력하세요.'
            elif kind in ('int_type','int_parsing','int_from_float'):message=f'{label}: 정수로 입력하세요.'
            elif kind in ('float_type','float_parsing','finite_number'):message=f'{label}: 올바른 숫자를 입력하세요.'
            elif kind in ('literal_error','enum'):message=f'{label}: 지원하지 않는 값입니다. 목록에서 다시 선택하세요.'
            elif kind=='string_pattern_mismatch':message=f'{label}: 올바른 Ollama 모델 태그를 입력하세요.'
            elif kind=='string_too_long':message=f'{label}: {ctx["max_length"]}자 이하로 입력하세요.'
            elif kind=='json_invalid':message='요청 JSON을 읽을 수 없습니다. 화면을 새로고침한 뒤 다시 시도하세요.'
            else:message=f'{label}: 입력 형식이 올바르지 않습니다. 값을 확인하세요.'
        issues.append({'field':'.'.join(map(str,path)),'message':message})
        details.append({'loc':loc,'type':kind,'msg':message})
    return {'code':'invalid_request','message':'입력한 게임 설정을 확인해 주세요.','detail':details,'issues':issues}
