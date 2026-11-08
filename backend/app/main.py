from __future__ import annotations
import asyncio,json,os,secrets,hmac
from uuid import uuid4
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from fastapi import FastAPI,Depends,Header,HTTPException,Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import StreamingResponse,FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from .models import GameConfig,Character,CHARACTERS,Inference
from .engine import Engine
from .storage import Store
from .ollama import Ollama,ModelError,memory_snapshot
from .manager import Manager,Conflict
from .benchmark import run_benchmark
from .errors import validation_response

ROOT=Path(__file__).resolve().parents[2]
DATA=Path(os.getenv('AI_MAFIA_DATA',str(ROOT/'data')));DATA.mkdir(parents=True,exist_ok=True)
TOKEN_FILE=DATA/'admin-token.txt'
if os.getenv('ADMIN_TOKEN'):TOKEN=os.environ['ADMIN_TOKEN']
else:
    if not TOKEN_FILE.exists():
        TOKEN_FILE.write_text(secrets.token_urlsafe(32));TOKEN_FILE.chmod(0o600)
    TOKEN=TOKEN_FILE.read_text().strip()
if len(TOKEN)<24:raise RuntimeError('ADMIN_TOKEN은 24자 이상이어야 합니다.')
store=Store(DATA/'mafia.sqlite3');ollama=Ollama(os.getenv('OLLAMA_URL','http://127.0.0.1:11434'));manager=Manager(store,ollama)
benchmark_task=None
benchmark_job=None

@asynccontextmanager
async def lifespan(app):
    store.recover()
    await ollama.inventory()
    yield
    manager.pause_requested=True
    if manager.auto_task and not manager.auto_task.done():
        manager.auto_task.cancel()
        try:await manager.auto_task
        except asyncio.CancelledError:pass
    if benchmark_task and not benchmark_task.done():
        benchmark_task.cancel()
        try:await benchmark_task
        except asyncio.CancelledError:pass

app=FastAPI(title='AI MAFIA',version='0.1.0',lifespan=lifespan)
origins=os.getenv('CORS_ORIGINS','http://127.0.0.1:5173,http://localhost:5173').split(',')
app.add_middleware(CORSMiddleware,allow_origins=origins,allow_methods=['GET','POST','PUT'],allow_headers=['Authorization','Content-Type'])
app.add_middleware(TrustedHostMiddleware,allowed_hosts=os.getenv('ALLOWED_HOSTS','127.0.0.1,localhost,testserver').split(','))

async def admin(authorization:str|None=Header(default=None)):
    if not authorization or not hmac.compare_digest(authorization,'Bearer '+TOKEN):
        raise HTTPException(401,'관리자 키가 필요합니다.')

@app.middleware('http')
async def security_headers(request,call_next):
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Cache-Control']='no-store' if request.url.path.startswith('/api/') else 'no-cache'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
    return response

@app.exception_handler(Conflict)
async def conflict(request,exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=409,content={'detail':str(exc)})
@app.exception_handler(RequestValidationError)
async def invalid_request(request,exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=422,content=validation_response(exc))

@app.exception_handler(ModelError)
async def model_error(request,exc):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=503,content={'detail':str(exc),'message':str(exc),'code':exc.code,'hint':exc.hint})

def public_snapshot():
    s=manager.snapshot()
    if s['error']:s['error']='모델 응답 문제로 일시정지했습니다. 관리자가 상세 내용을 확인할 수 있습니다.'
    return s

@app.get('/api/health')
async def health():return await ollama.inventory()
@app.get('/api/state')
async def state():return public_snapshot()
@app.get('/api/events')
async def events(request:Request):
    async def stream():
        last=None
        while not await request.is_disconnected():
            current=json.dumps(public_snapshot(),ensure_ascii=False)
            if current!=last:
                yield 'data: '+current+'\n\n';last=current
            else:yield ': heartbeat\n\n'
            await asyncio.sleep(1)
    return StreamingResponse(stream(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no','Cache-Control':'no-cache'})
@app.get('/api/games')
async def games():
    return [{k:s.get(k) for k in ('id','created_at','status','winner','day','series_id','series_index','mixed')}|{'mode':'mixed' if s['mixed'] else s['mode_history'][0],'provider':s['config']['provider']} for s in store.all()]
@app.get('/api/games/{gid}')
async def replay(gid:str):
    s=store.get(gid)
    if not s:raise HTTPException(404,'경기를 찾을 수 없습니다.')
    return Engine.restore(s).public()
@app.get('/api/stats')
async def stats(mode:Literal['original','character','mixed']='original'):return store.stats(mode)
@app.get('/api/admin/settings',dependencies=[Depends(admin)])
async def get_settings():return {'config':store.setting('config',GameConfig().model_dump(mode='json')),'defaults':GameConfig().model_dump(mode='json'),'characters':CHARACTERS}
@app.put('/api/admin/settings',dependencies=[Depends(admin)])
async def save_settings(cfg:GameConfig):
    store.set_setting('config',cfg.model_dump(mode='json'));return {'saved':True}
@app.get('/api/admin/state',dependencies=[Depends(admin)])
async def admin_state():return manager.snapshot()|{'admin_events':manager.game.s['admin_events'] if manager.game else []}
@app.get('/api/admin/games/{gid}',dependencies=[Depends(admin)])
async def admin_game(gid:str):
    s=store.get(gid)
    if not s:raise HTTPException(404,'경기 없음')
    return s
@app.post('/api/admin/validate',dependencies=[Depends(admin)])
async def validate(cfg:GameConfig):return {'valid':True}
@app.post('/api/admin/start',dependencies=[Depends(admin)])
async def start(cfg:GameConfig):return await manager.start(cfg)

class Control(BaseModel):
    action:Literal['pause','resume','step','stop']
    revision:int|None=None
@app.post('/api/admin/control',dependencies=[Depends(admin)])
async def control(c:Control):
    if not manager.game:raise Conflict('게임이 없습니다.')
    if c.action=='step':
        if c.revision is None:raise HTTPException(422,'중복 실행 방지를 위한 revision이 필요합니다.')
        return await manager.step(c.revision)
    if c.action=='resume':manager.resume()
    else:manager.pause(c.action=='stop')
    return manager.snapshot()

class StyleUpdate(BaseModel):
    mode:Literal['original','character']
    intensity:Literal['weak','normal','strong']
    characters:dict[str,Character]
@app.put('/api/admin/style',dependencies=[Depends(admin)])
async def style(c:StyleUpdate):
    if manager.game and set(c.characters)!={p['id'] for p in manager.game.s['players']}:raise HTTPException(422,'모든 참가자의 설정이 필요합니다.')
    # Version in every event refers to an immutable saved character snapshot in the audit trail.
    chars={k:v.model_dump() for k,v in c.characters.items()}
    manager.change_style(c.mode,c.intensity,chars)
    return manager.snapshot()

class BenchmarkRequest(BaseModel):
    models:list[str]=Field(min_length=1,max_length=12)
    quality:bool=False
    risk_once:bool=False
    inference:Inference=Field(default_factory=Inference)
@app.post('/api/admin/benchmark/preflight',dependencies=[Depends(admin)])
async def benchmark_preflight(b:BenchmarkRequest):
    inventory=await ollama.inventory()
    assessments=[ollama.assessment(model,b.inference.model_dump()) for model in b.models] if inventory['online'] else [
        {'model':model,'state':'blocked','code':'ollama_offline','can_override':False,'reason':'Ollama 서버에 연결할 수 없습니다.','warnings':[],'notes':[],'memory':inventory['memory']} for model in b.models]
    return {'online':inventory['online'],'loaded':inventory.get('loaded',[]),'models':assessments}

@app.post('/api/admin/benchmark',dependencies=[Depends(admin)])
async def benchmark(b:BenchmarkRequest):
    global benchmark_task,benchmark_job
    if manager.lock.locked() or manager.benchmark_busy or (manager.auto_task and not manager.auto_task.done()):raise Conflict('게임을 일시정지하고 진행 중인 추론이 끝난 후 실행하세요.')
    if b.risk_once and (len(b.models)!=1 or b.quality):raise HTTPException(422,'위험을 감수한 실행은 한 모델·한 응답만 허용합니다. 여러 모델이나 6개 상황 테스트에는 적용할 수 없습니다.')
    options=b.inference.model_dump()
    if b.risk_once:options['retries']=0
    manager.benchmark_busy=True
    benchmark_job={'id':str(uuid4()),'status':'running','models':b.models,'risk_once':b.risk_once,'cancel_requested':False,'progress':{}}
    job=benchmark_job
    async def run():
        try:
            await run_benchmark(ollama,store,b.models,options,b.quality,risk_once=b.risk_once,progress=job['progress'])
            job['status']='cancelled' if job['cancel_requested'] else 'finished'
        except asyncio.CancelledError:job['status']='cancelled'
        except Exception as e:job.update(status='failed',error=f'{type(e).__name__}: {e}')
        finally:manager.benchmark_busy=False
    benchmark_task=asyncio.create_task(run())
    def finished(task):
        manager.benchmark_busy=False
        if task.cancelled():job['status']='cancelled'
    benchmark_task.add_done_callback(finished)
    return {'started':True,'job_id':job['id'],'risk_once':b.risk_once}

@app.get('/api/admin/benchmark/status',dependencies=[Depends(admin)])
async def benchmark_status():
    return {'job':benchmark_job,'active':ollama.active,'busy':manager.benchmark_busy,'memory':memory_snapshot()}

@app.post('/api/admin/benchmark/cancel',dependencies=[Depends(admin)])
async def benchmark_cancel():
    if benchmark_task and not benchmark_task.done() and benchmark_job and not benchmark_job['cancel_requested']:
        benchmark_job['cancel_requested']=True
        benchmark_task.cancel() # generate() closes its stream, unloads, then records cancellation.
    return {'requested':bool(benchmark_job and benchmark_job['cancel_requested'])}

@app.get('/api/admin/benchmarks',dependencies=[Depends(admin)])
async def benchmarks():
    # Keep raw samples in SQLite; avoid resending large time series every UI poll.
    return [{k:v for k,v in r.items() if k!='memory_samples'}|{'sample_count':len(r.get('memory_samples',[]))} for r in store.benchmarks()]

DIST=ROOT/'frontend'/'dist'
if DIST.exists():
    app.mount('/assets',StaticFiles(directory=DIST/'assets'),name='assets')
    @app.get('/')
    async def index():return FileResponse(DIST/'index.html')
