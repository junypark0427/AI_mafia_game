import {describe,it,expect,vi} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import ModelLab,{outcomeLabel} from '../src/ModelLab';
import type {Benchmark,Health} from '../src/types';
describe('model lab result labels',()=>{
  for(const [outcome,label] of Object.entries({deferred:'실행 전 보류',failed:'실제 Ollama 실행 실패',cancelled:'사용자 중단',emergency_stopped:'실행 중 안전 중지',success:'실제 응답 성공'})){
    it(`distinguishes ${outcome}`,()=>expect(outcomeLabel({outcome} as Benchmark)).toBe(label));
  }
  it('offers independent model actions instead of requiring a full six-model setup',()=>{
    const health={online:true,version:'test',installed:[{name:'large:7b',size:4683075440,digest:'abc'},{name:'small:1b',size:1000000000,digest:'def'}],memory:{total_gb:8,available_gb:3,used_gb:4,swap_gb:4,pressure:'normal'},catalog:[]} as Health;
    const html=renderToStaticMarkup(<ModelLab health={health} config={null} token="test" busy={false} onAction={vi.fn()} onRefresh={vi.fn()} onLogin={vi.fn()}/>);
    expect(html.match(/이 모델만 측정/g)).toHaveLength(2);expect(html).toContain('OS 압박 정상');expect(html).toContain('심각한 압박과 비상 하한은 우회할 수 없습니다.');
  });
});
