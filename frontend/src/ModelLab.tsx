import {useEffect,useState} from 'react';
import {Activity,Check,Clock,FlaskConical,LoaderCircle,RotateCcw,Square,Users,Zap} from 'lucide-react';
import {request} from './api';
import type {Benchmark,Config,Health,MemoryAssessment,LabStatus} from './types';
const fmt=(n:number|null|undefined,s='')=>n==null?'미측정':`${n}${s}`;
export function outcomeLabel(record:Benchmark){
  return ({success:'실제 응답 성공',deferred:'실행 전 보류',failed:'실제 Ollama 실행 실패',cancelled:'사용자 중단',emergency_stopped:'실행 중 안전 중지'} as Record<string,string>)[record.outcome||'']||(record.error?'기존 실패 / 보류':'실제 응답 성공');
}
export default function ModelLab({health,config,token,busy,onAction,onRefresh,onLogin}:{health:Health|null;config:Config|null;token:string;busy:boolean;onAction:(fn:()=>Promise<void>)=>Promise<void>;onRefresh:()=>void;onLogin:()=>void}){
  const [selected,setSelected]=useState<string[]>([]),[quality,setQuality]=useState(false),[records,setRecords]=useState<Benchmark[]>([]),[assessments,setAssessments]=useState<Record<string,MemoryAssessment>>({}),[status,setStatus]=useState<LabStatus|null>(null),[error,setError]=useState('');
  const running=busy||!!status?.busy;
  const installed=health?.installed||[];
  const options=config?.inference;
  async function refresh(){
    const [history,current]=await Promise.all([request<Benchmark[]>('/admin/benchmarks','GET',undefined,token),request<LabStatus>('/admin/benchmark/status','GET',undefined,token)]);
    setRecords(history);setStatus(current);
  }
  useEffect(()=>{
    if(!token)return;
    let cancelled=false;
    async function poll(){try{await refresh();if(!cancelled)setError('');}catch(e){if(!cancelled)setError((e as Error).message);}}
    poll();const timer=setInterval(poll,1500);return()=>{cancelled=true;clearInterval(timer)};
  },[token]);
  async function check(models:string[]){
    const result=await request<{models:MemoryAssessment[]}>('/admin/benchmark/preflight','POST',{models,inference:options},token);
    setAssessments(previous=>({...previous,...Object.fromEntries(result.models.map(a=>[a.model,a]))}));return result.models;
  }
  async function execute(models:string[],risk=false){
    if(!token){onLogin();return}
    await onAction(async()=>{
      await check(models);
      await request('/admin/benchmark','POST',{models,inference:options,quality:risk?false:quality,risk_once:risk},token);
      await refresh();
    });
  }
  const memory=status?.memory||health?.memory;
  const phase=status?.active?.state;
  return <>
    <div className="page-heading"><div><div className="eyebrow">OLLAMA MODEL LAB</div><h1>경기 전에, 모델을 알아보세요.</h1><p>모델별 점검 · 실제 순차 추론 · 경고 확인 후 1회 실행</p></div><button className="btn" onClick={onRefresh}><RotateCcw size={15}/> 연결 새로고침</button></div>
    <div className="health-grid"><div className="panel health-card"><Activity/><small>OLLAMA SERVER</small><strong>{health?.online?`v${health.version}`:'오프라인'}</strong><p>로컬 HTTP · 127.0.0.1:11434</p></div><div className="panel health-card"><Zap/><small>AVAILABLE MEMORY · GiB</small><strong>{fmt(memory?.available_gb)}</strong><p>OS 압박 {({normal:'정상',warning:'경고',critical:'심각',unknown:'조회 불가'} as Record<string,string>)[memory?.pressure||'unknown']} · 스왑 {fmt(memory?.swap_gb,' GiB')}</p></div><div className="panel health-card"><Users/><small>INSTALLED MODELS</small><strong>{installed.length}<span>개</span></strong><p>전체 RAM {fmt(memory?.total_gb,' GiB')} · 자동 다운로드 없음</p></div></div>
    {health?.loaded?.length? <div className="banner">현재 로드: {health.loaded.map(m=>m.name).join(', ')}. 다른 모델이 남아 있으면 새 모델을 실행하지 않습니다. 같은 모델 한 개는 재사용할 수 있습니다.</div>:null}
    {error&&<div className="banner warning" role="alert">{error}</div>}
    <section className="panel settings-section"><div className="section-title"><h2>설치된 모델 · 개별 실험</h2><span className="outline-chip">한 번에 한 모델</span></div>
      <p className="section-description">파일 크기는 RAM 사용량이 아닙니다. 예상량 부족은 경고이며, 모델별 점검 후 위험을 감수한 1회 실행을 선택할 수 있습니다. OS의 심각한 압박과 비상 하한은 우회할 수 없습니다.</p>
      <div className="lab-models">{installed.map(model=>{
        const a=assessments[model.name];
        return <article className="lab-model" key={model.name}><div className="lab-model-title"><label><input type="checkbox" checked={selected.includes(model.name)} onChange={e=>setSelected(e.target.checked?[...selected,model.name]:selected.filter(m=>m!==model.name))}/><strong>{model.name}</strong></label><small>{(model.size/1e9).toFixed(1)}GB 파일 · {model.digest.slice(0,12)}</small></div>
          <div className="lab-model-actions"><button className="btn" disabled={running} onClick={()=>{if(!token){onLogin();return}onAction(async()=>{await check([model.name]);});}}>메모리 점검</button><button className="btn" disabled={running} onClick={()=>execute([model.name])}><FlaskConical size={14}/> 이 모델만 측정</button></div>
          {a&&<div className={`lab-assessment ${a.state}`}><strong>{a.state==='ready'?'실행 준비됨':a.state==='warning'?'메모리 부족 가능성 · 확인 필요':'실행 불가'}</strong>{a.reason&&<p>{a.reason}</p>}{a.warnings.map((w,i)=><p key={i}>{w}</p>)}{a.notes.map((n,i)=><small key={i}>{n}</small>)}
            {a.state==='warning'&&a.can_override&&<><p>일시적인 느려짐이나 추가 스왑이 발생할 수 있습니다. 이 동의는 한 모델의 응답 1회에만 적용되며 자동 재시도·다중 상황 테스트·게임에는 적용되지 않습니다.</p><button className="btn risk-button" disabled={running} onClick={()=>execute([model.name],true)}>위험을 감수하고 1회 실행</button></>}
          </div>}
        </article>
      })}</div>
      {!installed.length&&<p>Ollama를 실행하고 설치 목록을 새로고침하세요.</p>}
      <div className="benchmark-actions"><label><input type="checkbox" checked={quality} onChange={e=>setQuality(e.target.checked)}/> 일반 측정에 동일 상황 6개 테스트 포함</label><button className="btn primary" disabled={running||!selected.length} onClick={()=>execute(selected)}><FlaskConical size={16}/> 선택 모델 순차 측정</button></div>
      <p className="section-description">각 모델의 보류·실패는 다음 모델의 점검을 막지 않습니다. 경고를 우회한 실행은 위의 개별 버튼으로만 시작하세요.</p>
    </section>
    {running&&<section className="panel lab-progress" aria-live="polite"><LoaderCircle className="spin" size={18}/><div><strong>{status?.active?.model||status?.job?.progress?.model||'실험 준비 중'}</strong><p>{status?.job?.cancel_requested?'중단 요청됨 · 연결 종료와 언로드를 기다립니다.':({loading:'모델 로딩 중',generating:'최종 응답 생성 중',unloading:'모델 언로드 확인 중'} as Record<string,string>)[phase||'']||'모델 점검 또는 재시도 대기'}</p><small>사용(psutil) {fmt(memory?.used_gb,' GiB')} · 가용 {fmt(memory?.available_gb,' GiB')} · 스왑 {fmt(memory?.swap_gb,' GiB')}</small></div><button className="btn danger" disabled={!!status?.job?.cancel_requested} onClick={()=>onAction(async()=>{await request('/admin/benchmark/cancel','POST',{},token);await refresh();})}><Square size={13}/> 실험 중단</button></section>}
    <section className="panel settings-section"><div className="section-title"><div><Clock size={20}/><h2>실제 측정 기록</h2></div></div>{!token?<p>관리자 연결 후 측정 기록을 볼 수 있습니다.</p>:records.length?records.map((r,i)=><details className="benchmark-result" key={i}><summary><span className={`dot ${r.error?'red':'lime'}`}/><strong>{r.model}</strong><span>{r.scenario}</span><span>{outcomeLabel(r)} · {r.error?'':`${fmt(r.seconds,'초')} / ${fmt(r.tps,' t/s')}`}</span><Check size={13}/></summary><div>
      {r.error&&<p className="error-text">{r.error}{r.error_code&&` (${r.error_code})`}</p>}
      <div className="metric-chips"><span>API 요청 시도 {r.attempted?'있음':'없음'}</span><span>{r.risk_once?'위험 확인 후 1회':'일반 보호 모드'}</span><span>첫 토큰 {fmt(r.first_token_seconds,'초')}</span><span>전체 {fmt(r.seconds,'초')}</span><span>로딩 {fmt(r.load_seconds,'초')}</span><span>{fmt(r.tokens,' tokens')}</span><span>최고 사용(psutil) {fmt(r.peak_used_gb,' GiB')}</span><span>생성/중단 시 스왑 변화 {fmt(r.swap_delta_gb,' GiB')}</span><span>언로드 후 스왑 변화 {fmt(r.swap_delta_after_unload_gb,' GiB')}</span><span>언로드 {r.unloaded===true?'확인':r.unloaded===false?'실패':'해당 없음'}</span></div>
      {r.output&&<p className="benchmark-output">{r.output}</p>}
      <small>시스템 전체 관측값이며 모델 전용 메모리가 아닙니다. 최종 출력만 저장하며 내부 추론은 기록하지 않습니다.</small>
    </div></details>):<p>아직 측정 결과가 없습니다.</p>}</section>
    <section className="panel settings-section"><h2>기본 모델 설치 명령</h2><p className="section-description">안내용 명령입니다. 필요한 모델만 직접 설치하세요. 게임 설정에서 참가자별 경량 태그도 선택할 수 있습니다.</p><div className="commands">{(config?.players.map(p=>p.model)||health?.catalog.map(p=>p.model)||[]).map(model=><code key={model}>ollama pull {model}</code>)}</div></section>
  </>;
}
