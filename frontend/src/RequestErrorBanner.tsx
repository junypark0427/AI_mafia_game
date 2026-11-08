import {X} from 'lucide-react';
import {useEffect,useRef} from 'react';
import type {ApiError} from './api';
export default function RequestErrorBanner({error,onDismiss,onSettings,onModels}:{error:ApiError;onDismiss:()=>void;onSettings:()=>void;onModels:()=>void}){
  const runtime=error.status===503;
  const ref=useRef<HTMLElement>(null);
  useEffect(()=>{ref.current?.focus();},[error]);
  return <section ref={ref} tabIndex={-1} className="request-error" role="alert">
    <div><h2>{runtime?'모델 실행을 보류했습니다.':error.status===422?'게임 설정을 확인해 주세요.':'요청을 처리하지 못했습니다.'}</h2>
      {error.issues.length?<ul>{error.issues.map((issue,i)=><li key={i}>{issue.message}</li>)}</ul>:<p>{error.message}</p>}
      {error.hint&&<p className="request-error-hint">{error.hint}</p>}
      {(runtime||error.status===422)&&<button className="text-button" onClick={runtime?onModels:onSettings}>{runtime?'모델 실험실에서 확인':'게임 설정에서 수정'}</button>}
    </div><button className="icon-button" aria-label="오류 닫기" onClick={onDismiss}><X size={16}/></button>
  </section>;
}
