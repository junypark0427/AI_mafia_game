import type {Config,Snapshot} from './types';

export interface ApiIssue {field:string;message:string}
export class ApiError extends Error {
  constructor(message:string,public status:number,public code:string,public issues:ApiIssue[]=[],public hint?:string){super(message);this.name='ApiError';}
}
function record(value:unknown):value is Record<string,unknown>{return typeof value==='object'&&value!==null;}
function readIssues(data:Record<string,unknown>):ApiIssue[]{
  if(Array.isArray(data.issues))return data.issues.filter(record).filter(v=>typeof v.field==='string'&&typeof v.message==='string').map(v=>({field:v.field as string,message:v.message as string}));
  if(!Array.isArray(data.detail))return [];
  return data.detail.filter(record).map(d=>{
    const path=Array.isArray(d.loc)?d.loc.filter(v=>v!=='body'):[];
    const labels:Record<string,string>={role:'직업',model:'모델',num_ctx:'컨텍스트 크기',rounds:'낮 토론 라운드'};
    const label=labels[String(path.at(-1))]||'게임 설정';
    const message=d.type==='missing'?`${label}: 필수 항목이 빠졌습니다.`:typeof d.msg==='string'?d.msg.replace(/^Value error, /,''):'입력값을 확인해 주세요.';
    return {field:path.join('.'),message};
  });
}
export async function request<T>(path:string,method='GET',body?:unknown,token?:string,fetcher:typeof fetch=fetch):Promise<T>{
  const response=await fetcher('/api'+path,{method,headers:{...(body!==undefined?{'Content-Type':'application/json'}:{}),...(token?{Authorization:`Bearer ${token}`}:{})},...(body!==undefined?{body:JSON.stringify(body)}:{})});
  let data:unknown;
  try{data=await response.json();}catch{throw new ApiError('서버 응답을 읽을 수 없습니다. 잠시 후 다시 시도하세요.',response.status,'invalid_response');}
  if(!response.ok){
    const payload=record(data)?data:{};const issues=readIssues(payload);
    const message=typeof payload.message==='string'?payload.message:typeof payload.detail==='string'?payload.detail:issues.length?issues.map(i=>i.message).join(' / '):'요청을 처리하지 못했습니다.';
    throw new ApiError(message,response.status,typeof payload.code==='string'?payload.code:response.status===422?'invalid_request':'request_failed',issues,typeof payload.hint==='string'?payload.hint:undefined);
  }
  return data as T;
}
export async function startGame(config:Config,token:string,fetcher:typeof fetch=fetch):Promise<Snapshot>{
  // Both endpoints receive exactly the same JSON; no wrapper or automatic model/role replacement.
  await request('/admin/validate','POST',config,token,fetcher);
  return request<Snapshot>('/admin/start','POST',config,token,fetcher);
}
