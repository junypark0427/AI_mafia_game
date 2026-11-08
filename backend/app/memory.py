"""macOS measurements and a warning/emergency policy, not a model RAM oracle."""
import ctypes
import sys
from datetime import datetime,timezone
import psutil

GIB=2**30

def macos_pressure():
    if sys.platform!='darwin':return None
    try:
        value=ctypes.c_int();size=ctypes.c_size_t(ctypes.sizeof(value))
        fn=ctypes.CDLL(None,use_errno=True).sysctlbyname
        fn.argtypes=[ctypes.c_char_p,ctypes.c_void_p,ctypes.POINTER(ctypes.c_size_t),ctypes.c_void_p,ctypes.c_size_t]
        fn.restype=ctypes.c_int
        if fn(b'kern.memorystatus_vm_pressure_level',ctypes.byref(value),ctypes.byref(size),None,0)!=0:return None
        # This sysctl returns DISPATCH/NOTE flags (1/2/4), NOT XNU's internal 0/1/2/3 enum.
        return value.value if value.value in (1,2,4) else None
    except (AttributeError,OSError):return None

def memory_snapshot():
    v=psutil.virtual_memory()
    try:s=psutil.swap_memory()
    except (OSError,PermissionError):s=None
    level=macos_pressure()
    result={'at':datetime.now(timezone.utc).isoformat(),'total_gb':round(v.total/GIB,3),
        'available_gb':round(v.available/GIB,3),'used_gb':round(v.used/GIB,3),
        'unavailable_gb':round((v.total-v.available)/GIB,3),'free_gb':round(v.free/GIB,3),
        'swap_gb':round(s.used/GIB,3) if s else None,
        'swap_out_bytes':s.sout if s else None,'swap_in_bytes':s.sin if s else None,
        'pressure':{1:'normal',2:'warning',4:'critical'}.get(level,'unknown'),'pressure_level':level,
        'source':'psutil + macOS sysctl' if sys.platform=='darwin' else 'psutil'}
    for key in ('active','inactive','wired'):
        result[key+'_gb']=round(getattr(v,key)/GIB,3) if hasattr(v,key) else None
    return result

def emergency_reason(m):
    if m.get('pressure')=='critical':return 'macOS가 심각한 메모리 압박을 보고했습니다.'
    if m['available_gb']<=.2:return f"가용 메모리가 {m['available_gb']:.2f}GiB로 비상 하한 0.20GiB이하입니다."
    if m.get('pressure','unknown')=='unknown' and m.get('swap_gb') is None and m['available_gb']<.6:
        return '압박·스왑 지표를 모두 읽을 수 없고 가용 메모리도 0.60GiB 미만입니다.'
    return None

def assess_memory(model,size_bytes,m,options=None):
    options=options or {};size=size_bytes/GIB
    estimate=round(size*1.12+.4+max(0,options.get('num_ctx',2048)/2048-1)*.25,2)
    reason=emergency_reason(m);warnings=[];notes=[]
    low_buffer_cpu=(options.get('num_gpu')==0 and options.get('num_batch',512)<=64 and options.get('num_ctx',2048)<=4096 and m.get('pressure')=='normal' and m['available_gb']>=1 and size<=m['total_gb']*.4)
    if m['available_gb']<estimate:
        message=f"가용 {m['available_gb']:.2f}GiB / 파일 크기 기반 참고 예상량 {estimate:.2f}GiB. 실제 요구량은 로딩 후 확인됩니다."
        if low_buffer_cpu:notes.append(message+' CPU·작은 배치·메모리 매핑에서는 이 추정을 차단 조건으로 사용하지 않고 실제 압박을 감시합니다.')
        else:warnings.append(message)
    if m.get('pressure')=='warning':warnings.append('macOS 메모리 압박이 경고 상태입니다.')
    if m.get('pressure','unknown')=='unknown':notes.append('OS 압박 신호를 읽지 못해 가용량과 실행 중 변화를 감시합니다.')
    if m.get('swap_gb') is not None and m['swap_gb']>2:notes.append(f"기존 스왑 {m['swap_gb']:.2f}GiB는 누적 사용량이며 이 모델이 만든 증가량이 아닙니다.")
    return {'model':model,'state':'blocked' if reason else 'warning' if warnings else 'ready',
        'code':'memory_critical' if reason else 'insufficient_memory' if warnings else None,
        'can_override':not bool(reason),'reason':reason,'warnings':warnings,'notes':notes,
        'low_buffer_cpu':low_buffer_cpu,'estimated_gb':estimate,'model_file_gb':round(size,2),'memory':m}
