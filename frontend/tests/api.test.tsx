import {describe,it,expect,vi} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {ApiError,request,startGame} from '../src/api';
import RequestErrorBanner from '../src/RequestErrorBanner';
import payload from './fixtures/game-config.json';
import type {Config} from '../src/types';
const config=payload as Config;
const response=(body:unknown,status=200)=>new Response(JSON.stringify(body),{status,headers:{'Content-Type':'application/json'}});

describe('actual frontend start request contract',()=>{
  it('sends exactly the same complete JSON to validation and start, with authentication',async()=>{
    const fetcher=vi.fn<typeof fetch>().mockResolvedValueOnce(response({valid:true})).mockResolvedValueOnce(response({game:{status:'paused'}}));
    const before=JSON.stringify(config);await startGame(config,'test-key',fetcher);
    expect(fetcher.mock.calls.map(([url])=>url)).toEqual(['/api/admin/validate','/api/admin/start']);
    for(const [,options] of fetcher.mock.calls){
      expect(options?.method).toBe('POST');expect(options?.headers).toEqual({'Content-Type':'application/json',Authorization:'Bearer test-key'});
      expect(JSON.parse(options!.body as string)).toEqual(config);
    }
    expect(JSON.stringify(config)).toBe(before);
  });
  it('stops before start when the server rejects role assignment',async()=>{
    const fetcher=vi.fn<typeof fetch>().mockResolvedValue(response({code:'invalid_request',message:'설정을 확인해 주세요.',issues:[{field:'players',message:'마피아는 1~2명을 배정해야 합니다.'}]},422));
    await expect(startGame(config,'test-key',fetcher)).rejects.toMatchObject({status:422,code:'invalid_request',issues:[{field:'players',message:expect.stringContaining('마피아')}]});
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('keeps runtime 503 separate from schema 422 and preserves recovery instructions',async()=>{
    const fetcher=vi.fn<typeof fetch>().mockResolvedValueOnce(response({valid:true})).mockResolvedValueOnce(response({code:'insufficient_memory',detail:'현재 스왑 3.57GB로 실행을 보류합니다.',hint:'앱을 정리하거나 경량 모델을 직접 선택하세요.'},503));
    await expect(startGame(config,'test-key',fetcher)).rejects.toMatchObject({status:503,code:'insufficient_memory',message:expect.stringContaining('3.57GB'),hint:expect.stringContaining('경량')});
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('still handles legacy FastAPI detail arrays with missing fields',async()=>{
    const fetcher=vi.fn<typeof fetch>().mockResolvedValue(response({detail:[{loc:['body','players',1,'role'],type:'missing',msg:'Field required'}]},422));
    await expect(request('/admin/start','POST',config,'test-key',fetcher)).rejects.toMatchObject({issues:[{field:'players.1.role',message:'직업: 필수 항목이 빠졌습니다.'}]});
  });
});

describe('visible persistent request errors',()=>{
  const callbacks={onDismiss:vi.fn(),onSettings:vi.fn(),onModels:vi.fn()};
  it('shows the affected field and how to fix invalid settings',()=>{
    const error=new ApiError('설정 오류',422,'invalid_request',[{field:'players.1.role',message:'참가자 2의 직업: 필수 항목이 빠졌습니다.'}]);
    const html=renderToStaticMarkup(<RequestErrorBanner error={error} {...callbacks}/>);
    expect(html).toContain('role="alert"');expect(html).toContain('참가자 2의 직업');expect(html).toContain('게임 설정에서 수정');
  });
  it('shows memory details and lab navigation without blaming role assignment',()=>{
    const error=new ApiError('현재 스왑 3.57GB',503,'insufficient_memory',[],'경량 모델을 직접 선택하세요.');
    const html=renderToStaticMarkup(<RequestErrorBanner error={error} {...callbacks}/>);
    expect(html).toContain('모델 실행을 보류했습니다.');expect(html).toContain('3.57GB');expect(html).toContain('경량 모델');expect(html).toContain('모델 실험실에서 확인');expect(html).not.toContain('게임 설정을 확인해 주세요.');
  });
  it('renders server/model text as escaped text',()=>{
    const error=new ApiError('<script>alert(1)</script>',503,'model_unavailable');
    const html=renderToStaticMarkup(<RequestErrorBanner error={error} {...callbacks}/>);
    expect(html).not.toContain('<script>');expect(html).toContain('&lt;script&gt;');
  });
});
