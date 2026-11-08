from __future__ import annotations
from collections import Counter
from enum import StrEnum
from typing import Literal
from pydantic import BaseModel, Field, model_validator
from pydantic_core import PydanticCustomError

class Role(StrEnum):
    MAFIA='mafia'
    POLICE='police'
    DOCTOR='doctor'
    CITIZEN='citizen'
    BODYGUARD='bodyguard'
    SPY='spy'

ROLE_NAMES={'mafia':'마피아','police':'경찰','doctor':'의사','citizen':'시민','bodyguard':'경호원','spy':'스파이'}
DEFAULT_ROLES=[Role.MAFIA,Role.POLICE,Role.DOCTOR,Role.CITIZEN,Role.CITIZEN,Role.CITIZEN]
CATALOG=[
    {'id':'deepseek','name':'DeepSeek','model':'deepseek-r1:7b','light':'deepseek-r1:1.5b','color':'#6c9cff'},
    {'id':'gemma','name':'Gemma','model':'gemma3:4b','light':'gemma3:1b','color':'#b5a0ff'},
    {'id':'ministral','name':'Ministral','model':'ministral-3:3b','light':'ministral-3:3b','color':'#ffa874'},
    {'id':'exaone','name':'EXAONE','model':'exaone3.5:7.8b','light':'exaone3.5:2.4b','color':'#65d7ba'},
    {'id':'qwen','name':'Qwen','model':'qwen3:4b','light':'qwen3:1.7b','color':'#ef91c7'},
    {'id':'llama','name':'Llama','model':'llama3.2:3b','light':'llama3.2:1b','color':'#e4cc78'},
]

class Character(BaseModel):
    id: str = Field(max_length=80)
    version: int = Field(default=1,ge=1)
    enabled: bool = True
    name: str = Field(max_length=60)
    description: str = Field(default='',max_length=180)
    instructions: str = Field(default='',max_length=350)
    vocabulary: str = Field(default='',max_length=100)
    avoid: str = Field(default='',max_length=100)
    intensity: Literal['weak','normal','strong']='normal'

CHARACTERS={}
for c, name, desc, inst, words in zip(CATALOG,
 ['혁명 연설가','테크 기업 발표자','철학적인 귀족','사극 속 책사','고대 병법가','서부 카우보이'],
 ['허구의 역사극 혁명가','가상의 신제품 발표자','연극 속 철학자 겸 귀족','한국 사극 속 재치 있는 책사','고전 병법을 즐기는 가상 전략가','서부극 속 유쾌한 카우보이'],
 ['연극적인 혁명 연설체로 단결과 기만에 대해 말한다. 실제 정치 선전은 하지 않는다.',
  '명료하고 자신감 있게 발표하듯 말한다. 가끔 기술적 비유와 위트를 쓴다.',
  '우아한 표현과 짧은 철학적 비유를 쓴다. 가끔 프랑스어 감탄사를 쓴다. 간결하게 말한다.',
  '사극풍의 재치 있는 존댓말로 격식을 지키면서 상대 주장에 반박한다.',
  '병법과 고사성어에 빗대어 상대의 의도를 말한다. 혁명 연설체는 사용하지 않는다.',
  '서부극의 직설적인 말투와 카우보이 비유, 가벼운 유머를 쓴다.'],
 ['동지, 단결, 혁명','발견, 업데이트, 발표','Mon ami, 진실','소인, 전하, 하옵니다','지피지기, 병법','친구, 마을, 안장']):
    CHARACTERS[c['id']]=Character(id=c['id']+'-default',name=name,description=desc,instructions=inst,vocabulary=words).model_dump()

class PlayerConfig(BaseModel):
    id: str
    name: str = Field(max_length=30)
    model: str = Field(pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_.:/-]{0,119}$')
    role: Role

class Inference(BaseModel):
    num_ctx: int = Field(default=2048,ge=2048,le=8192)
    num_batch: int = Field(default=128,ge=32,le=512)
    thinking: bool|None = None # Explicit runtime option, independent of persona mode.
    num_gpu: Literal[0]|None = None # Explicit CPU mode; None preserves Ollama auto selection.
    num_predict: int = Field(default=384,ge=128,le=1024)
    reasoning_budget: int = Field(default=768,ge=384,le=2048)
    temperature: float = Field(default=.8,ge=0,le=1.5)
    top_p: float = Field(default=.9,ge=.1,le=1)
    timeout: int = Field(default=150,ge=15,le=300)
    retries: int = Field(default=1,ge=0,le=2)

class GameConfig(BaseModel):
    players: list[PlayerConfig] = Field(default_factory=lambda:[PlayerConfig(**{k:c[k] for k in ('id','name','model')},role=r) for c,r in zip(CATALOG,DEFAULT_ROLES)])
    enabled_roles: list[Role] = Field(default_factory=lambda:list(dict.fromkeys(DEFAULT_ROLES)))
    random_roles: bool = True
    rounds: int = Field(default=3,ge=1,le=5)
    order: Literal['rotate','random']='rotate'
    dead_chat: bool = True
    dead_reactions: int = Field(default=1,ge=0,le=2)
    reveal_roles: bool = False
    auto: bool = False
    last_words: bool = True
    interviews: bool = True
    series_count: int = Field(default=1,ge=1,le=100)
    rotate_roles: bool = True
    max_days: int = Field(default=15,ge=2,le=30)
    mode: Literal['original','character']='original'
    intensity: Literal['weak','normal','strong']='normal'
    characters: dict[str,Character] = Field(default_factory=lambda:{k:Character(**v) for k,v in CHARACTERS.items()})
    inference: Inference = Field(default_factory=Inference)
    provider: Literal['ollama','mock']='ollama'

    @model_validator(mode='after')
    def validate_game(self):
        if len(self.players)!=6 or len({p.id for p in self.players})!=6:
            raise PydanticCustomError('participants_count','서로 다른 ID의 참가자 6명이 필요합니다.')
        if {p.id for p in self.players}!={c['id'] for c in CATALOG}:
            raise PydanticCustomError('player_ids','기본 여섯 참가자 ID를 사용하세요.')
        if self.provider=='ollama' and len({p.model for p in self.players})!=6:
            raise PydanticCustomError('models_distinct','실제 경기는 서로 다른 모델 태그 6개를 선택하세요.')
        counts=Counter(p.role for p in self.players)
        if not 1<=counts[Role.MAFIA]<=2: raise PydanticCustomError('mafia_count','마피아는 1~2명을 배정해야 합니다. 현재 {count}명입니다.',{'count':counts[Role.MAFIA]})
        if counts[Role.MAFIA]+counts[Role.SPY]>2: raise PydanticCustomError('mafia_team_count','마피아와 스파이를 합쳐 최대 2명까지 배정할 수 있습니다.')
        for r in (Role.POLICE,Role.DOCTOR,Role.BODYGUARD,Role.SPY):
            if counts[r]>1: raise PydanticCustomError('role_count','{role}은 최대 1명까지 배정할 수 있습니다. 현재 {count}명입니다.',{'role':ROLE_NAMES[r],'count':counts[r]})
        disabled=[f'{p.name}({ROLE_NAMES[p.role]})' for p in self.players if p.role not in self.enabled_roles]
        if disabled:
            raise PydanticCustomError('assigned_role_disabled','비활성화된 직업이 배정되어 있습니다: {players}. 해당 직업을 다시 켜거나 참가자의 직업을 바꾸세요.',{'players':', '.join(disabled)})
        if not {p.id for p in self.players}<=self.characters.keys():
            raise PydanticCustomError('character_missing','참가자별 캐릭터 설정이 필요합니다. 기본 캐릭터 설정을 복원하세요.')
        return self

def team(role):
    return 'mafia' if role in (Role.MAFIA,Role.SPY) else 'town'
