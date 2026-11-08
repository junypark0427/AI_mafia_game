import asyncio
from types import SimpleNamespace
import pytest
from app.models import Inference
from app.memory import assess_memory,memory_snapshot,macos_pressure
from app.ollama import Ollama,ModelError
from app.benchmark import run_benchmark
from app.storage import Store


def snapshot(available=4,pressure='normal',swap=3):
    return {'total_gb':8,'available_gb':available,'used_gb':4,'swap_gb':swap,'pressure':pressure}

def test_old_swap_is_not_a_hard_veto():
    a=assess_memory('deepseek-r1:7b',4683075440,snapshot(6,swap=4))
    assert a['state']=='ready' and a['can_override'] and a['notes']

def test_disk_estimate_is_overridable_warning():
    a=assess_memory('deepseek-r1:7b',4683075440,snapshot(1.5))
    assert a['state']=='warning' and a['can_override'] and '참고 예상량' in a['warnings'][0]

@pytest.mark.parametrize('m',[snapshot(.2),snapshot(3,'critical'),snapshot(.5,'unknown',None)])
def test_serious_pressure_cannot_be_overridden(m,monkeypatch):
    import app.ollama as adapter
    o=Ollama();o.models={'test:1b':{'size':1000000}}
    monkeypatch.setattr(adapter,'memory_snapshot',lambda:m)
    with pytest.raises(ModelError) as error:o.guard('test:1b',risk_once=True)
    assert error.value.code=='memory_critical' and not error.value.attempted

def test_risk_once_does_not_change_next_guard(monkeypatch):
    import app.ollama as adapter
    o=Ollama();o.models={'test:3b':{'size':2000000000}}
    monkeypatch.setattr(adapter,'memory_snapshot',lambda:snapshot(.8,'warning'))
    assert o.guard('test:3b',risk_once=True)['available_gb']==.8
    with pytest.raises(ModelError,match='메모리 보호로 실행 보류'):o.guard('test:3b')


def test_macos_used_is_not_total_minus_available(monkeypatch):
    import app.memory as m
    gib=2**30
    monkeypatch.setattr(m.psutil,'virtual_memory',lambda:SimpleNamespace(total=8*gib,available=2*gib,used=4*gib,free=gib,active=2*gib,inactive=gib,wired=2*gib))
    monkeypatch.setattr(m.psutil,'swap_memory',lambda:SimpleNamespace(used=3*gib,sin=10,sout=20))
    monkeypatch.setattr(m,'macos_pressure',lambda:2)
    result=memory_snapshot()
    assert result['used_gb']==4 and result['unavailable_gb']==6 and result['pressure']=='warning'
    assert result['swap_out_bytes']==20

@pytest.mark.parametrize('flag',[1,2,4])
def test_sysctl_dispatch_flags_not_internal_enums(monkeypatch,flag):
    import app.memory as m
    def call(name,ptr,size,new,new_size):ptr._obj.value=flag;return 0
    monkeypatch.setattr(m.sys,'platform','darwin')
    monkeypatch.setattr(m.ctypes,'CDLL',lambda *args,**kwargs:SimpleNamespace(sysctlbyname=call))
    assert macos_pressure()==flag

async def test_deferred_large_model_does_not_block_small_model(tmp_path):
    class Adapter:
        async def generate(self,model,*args,**kwargs):
            if model=='large:7b':raise ModelError('메모리 보호로 실행 보류',code='insufficient_memory')
            return '실제 호출 대역 응답',{'model':model,'attempted':True,'seconds':.1,'tokens':3,'unloaded':True}
    results=await run_benchmark(Adapter(),Store(tmp_path/'test.db'),['large:7b','small:1b'],Inference().model_dump())
    assert [r['outcome'] for r in results]==['deferred','success']
    assert not results[0]['attempted'] and results[1]['attempted']

async def test_risk_once_is_exactly_one_call_without_retry(tmp_path):
    class Adapter:
        calls=0
        async def generate(self,*args,**kwargs):
            self.calls+=1;assert kwargs['risk_once'] is True
            raise ModelError('timeout',code='timeout',attempted=True)
    adapter=Adapter();results=await run_benchmark(adapter,Store(tmp_path/'test.db'),['small:1b'],Inference(retries=2).model_dump(),risk_once=True)
    assert adapter.calls==1 and results[0]['outcome']=='failed' and results[0]['options']['retries']==0
    with pytest.raises(ValueError):await run_benchmark(adapter,Store(tmp_path/'test.db'),['a','b'],Inference().model_dump(),risk_once=True)

async def test_cancelled_attempt_is_saved_separately(tmp_path):
    class Adapter:
        async def generate(self,*args,**kwargs):
            e=asyncio.CancelledError();e.metrics={'attempted':True,'unloaded':True};raise e
    result=await run_benchmark(Adapter(),Store(tmp_path/'test.db'),['small:1b'],Inference().model_dump())
    assert result[0]['outcome']=='cancelled' and result[0]['unloaded']

def test_explicit_low_buffer_cpu_uses_os_pressure_not_file_size_alone():
    options={'num_gpu':0,'num_batch':64,'num_ctx':4096}
    a=assess_memory('ministral-3:3b',2953840808,snapshot(2.6),options)
    assert a['state']=='ready' and a['low_buffer_cpu'] and a['notes']
    assert assess_memory('ministral-3:3b',2953840808,snapshot(2.6,'warning'),options)['state']=='warning'
    assert assess_memory('ministral-3:3b',2953840808,snapshot(2.6,'critical'),options)['state']=='blocked'
    assert assess_memory('deepseek-r1:7b',4683075440,snapshot(2.6),options)['state']=='warning'
    assert assess_memory('ministral-3:3b',2953840808,snapshot(.7),options)['state']=='warning'
