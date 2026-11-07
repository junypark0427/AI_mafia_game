"""Opt-in actual calls. No model download and no bypass of memory protection."""
import os
import pytest
from app.ollama import Ollama,ModelError
from app.models import Inference,CATALOG
pytestmark=[pytest.mark.ollama,pytest.mark.skipif(os.getenv('RUN_OLLAMA_TESTS')!='1',reason='Actual inference is opt-in')]

async def test_live_inventory():
    result=await Ollama().inventory()
    assert result['online'] and result['version']

@pytest.mark.parametrize('model',[c['model'] for c in CATALOG])
async def test_live_final_response(model):
    o=Ollama();await o.inventory()
    try:o.guard(model)
    except ModelError as e:pytest.skip(str(e))
    content,metric=await o.generate(model,[{'role':'user','content':'마피아 게임 첫날, 시민으로서 동료에게 한국어 두 문장으로 말해 주세요. 내부 사고는 쓰지 마세요.'}],Inference(timeout=120).model_dump())
    assert content and metric['model']==model and metric['tokens']>0
    assert '<think>' not in content
