import asyncio,json
import pytest,httpx
from app.models import Inference
from app.ollama import Ollama,ModelError

@pytest.fixture
def fake_ollama(monkeypatch):
    import app.ollama as mod
    model='llama3.2:1b';calls=[];state={'loaded':False,'incomplete':False,'active':0,'max_active':0}
    async def handler(request):
        path=request.url.path;payload=json.loads(request.content) if request.content else None;calls.append((path,payload))
        if path=='/api/version':return httpx.Response(200,json={'version':'0.34.3'})
        if path=='/api/tags':return httpx.Response(200,json={'models':[{'name':model,'size':1000000,'digest':'test-digest'}]})
        if path=='/api/ps':return httpx.Response(200,json={'models':[{'name':model}] if state['loaded'] else []})
        if path=='/api/generate':state['loaded']=False;return httpx.Response(200,json={'done':True})
        if path=='/api/chat':
            state['active']+=1;state['max_active']=max(state['max_active'],state['active']);state['loaded']=True
            await asyncio.sleep(.01);state['active']-=1
            chunks=[{'message':{'thinking':'PRIVATE_CHAIN_OF_THOUGHT'}},{'message':{'content':'<think>legacy secret</think>안녕하세요.'}}]
            if not state['incomplete']:chunks.append({'model':model,'done':True,'eval_count':12,'eval_duration':1000000000,'load_duration':100000000})
            return httpx.Response(200,text='\n'.join(json.dumps(c) for c in chunks))
        return httpx.Response(404)
    original=httpx.AsyncClient
    def client(*args,**kwargs):return original(*args,**kwargs,transport=httpx.MockTransport(handler))
    monkeypatch.setattr(mod.httpx,'AsyncClient',client)
    monkeypatch.setattr(mod,'memory_snapshot',lambda:{'total_gb':8,'available_gb':6,'used_gb':2,'swap_gb':0})
    return Ollama(),model,calls,state

async def test_stream_metrics_reasoning_and_unload(fake_ollama):
    o,model,calls,state=fake_ollama
    content,metric=await o.generate(model,[{'role':'user','content':'test'}],Inference().model_dump())
    assert content=='안녕하세요.' and 'PRIVATE' not in str(metric)
    assert metric['tokens']==12 and metric['tps']==12 and metric['load_seconds']==.1
    assert not state['loaded'] and o.active is None
    payload=next(p for path,p in calls if path=='/api/chat')
    assert payload['keep_alive']==0 and payload['options']['num_ctx']==2048

async def test_adapter_serializes_requests(fake_ollama):
    o,model,_,state=fake_ollama
    async def generate():return await o.generate(model,[{'role':'user','content':'test'}],Inference().model_dump())
    await asyncio.gather(generate(),generate())
    assert state['max_active']==1 and not state['loaded']

async def test_incomplete_stream_still_unloads(fake_ollama):
    o,model,_,state=fake_ollama;state['incomplete']=True
    with pytest.raises(ModelError,match='완료 신호'):await o.generate(model,[{'role':'user','content':'test'}],Inference().model_dump())
    assert not state['loaded']

async def test_pressure_cancels_and_unloads(fake_ollama,monkeypatch):
    o,model,calls,state=fake_ollama
    import app.ollama as mod
    readings={'n':0}
    def memory():
        readings['n']+=1
        return {'total_gb':8,'available_gb':6 if readings['n']<3 else .2,'used_gb':2,'swap_gb':0}
    monkeypatch.setattr(mod,'memory_snapshot',memory)
    with pytest.raises(ModelError,match='메모리 압박'):
        await o.generate(model,[{'role':'user','content':'test'}],Inference().model_dump())
    assert not state['loaded'] and any(path=='/api/generate' for path,_ in calls)

async def test_risk_once_uses_same_single_model_and_unloads(fake_ollama,monkeypatch):
    o,model,calls,state=fake_ollama
    import app.ollama as adapter
    monkeypatch.setattr(adapter,'memory_snapshot',lambda:{'total_gb':8,'available_gb':.8,'used_gb':5,'swap_gb':3,'pressure':'warning'})
    with pytest.raises(ModelError):await o.generate(model,[{'role':'user','content':'test'}],Inference().model_dump())
    content,metrics=await o.generate(model,[{'role':'user','content':'test'}],Inference().model_dump(),risk_once=True)
    assert content and metrics['risk_once'] and metrics['unloaded'] and not state['loaded']
    assert len([p for path,p in calls if path=='/api/chat'])==1

async def test_user_cancel_closes_request_and_unloads(fake_ollama):
    o,model,calls,state=fake_ollama
    task=asyncio.create_task(o.generate(model,[{'role':'user','content':'test'}],Inference().model_dump()))
    await asyncio.sleep(.001);task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert not state['loaded'] and any(path=='/api/generate' for path,_ in calls)

async def test_explicit_cpu_option_is_sent_and_recorded(fake_ollama):
    o,model,calls,_=fake_ollama
    options=Inference(num_gpu=0).model_dump()
    _,metrics=await o.generate(model,[{'role':'user','content':'test'}],options)
    payload=next(p for path,p in calls if path=='/api/chat')
    assert payload['options']['num_gpu']==0 and metrics['runtime_options']['num_gpu']==0

async def test_explicit_thinking_option_only_for_capable_model(fake_ollama,monkeypatch):
    o,model,calls,_=fake_ollama
    original_inventory=o.inventory
    async def inventory():
        result=await original_inventory();o.models[model]['capabilities']=['thinking'];return result
    monkeypatch.setattr(o,'inventory',inventory)
    _,metrics=await o.generate(model,[{'role':'user','content':'test'}],Inference(thinking=False).model_dump())
    payload=next(p for path,p in calls if path=='/api/chat')
    assert payload['think'] is False and metrics['thinking_requested'] is False
