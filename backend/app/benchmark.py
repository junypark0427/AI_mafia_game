from .engine import now
from .prompts import RULES
from .ollama import ModelError
SCENARIOS=[
 ('first_day','너는 시민이다. 첫날 아무 정보가 없다. 참가자는 DeepSeek, Gemma, Ministral, EXAONE, Qwen, Llama다. 지금 토론에서 할 말을 2~4문장으로 말하라.'),
 ('accused','너는 시민이다. Gemma가 "당신은 근거 없이 Qwen을 몰아갔으니 마피아야"라고 의심한다. 자연스럽게 대응하라.'),
 ('contradiction','너는 시민이다. Qwen은 어제 "Gemma를 믿는다"고 했고 오늘 "처음부터 Gemma가 수상했다"고 말했다. 이 모순에 반응하라.'),
 ('bluff','너는 실제 마피아다. 경찰을 사칭할지 숨길지는 자유다. Llama가 네 투표 기록을 의심한다. 이기기 위한 말을 하라.'),
 ('defense','너는 시민이다. 동료들이 너를 처형하려 한다. 마지막으로 설득하거나 원하는 반응을 하라.'),
 ('dead','너는 억울하게 처형된 시민이다. 생존한 Qwen이 "잘 처형한 것 같다"고 말했다. 사망자 전용 채팅에서 관전 소감을 말하라.'),
]
async def run_benchmark(ollama,store,models,options,quality=False):
    results=[]
    for model in models:
        for scenario,prompt in SCENARIOS if quality else SCENARIOS[:1]:
            r={'at':now(),'model':model,'scenario':scenario,'quality_status':'사람의 검토 필요','options':options}
            try:
                content,metrics=await ollama.generate(model,[{'role':'system','content':RULES},{'role':'user','content':prompt}],options)
                r.update(metrics,output=content,error=None,hangul_characters=sum('가'<=c<='힣' for c in content))
            except ModelError as e:r.update(error=str(e),output=None)
            store.benchmark(r);results.append(r)
            if r.get('error'):break
    return results
