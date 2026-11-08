# 실제 6인 Ollama 게임 검증 기록

2026-11-08. **정상 완주하지 못했다.** 상세 품질·성능·메모리·회귀 결과는 [최종 품질 보고서](FINAL_QUALITY_REPORT.md)에 기록했다.

실제 태그: `deepseek-r1:1.5b`, `gemma3:1b`, `ministral-3:3b`, `exaone3.5:2.4b`, `qwen3:1.7b`, `llama3.2:3b`. 여섯 태그 모두 설치돼 있으며 실제 응답 또는 최종 응답 소진을 측정했다. 다른 모델 대체·사전 대사·가짜 투표는 사용하지 않았다. 이번 안정화 단계에서 모델을 추가 다운로드하지 않았다.

| 경기 | 설정 | 도달 지점 | 결과 |
|---|---|---|---|
| `0d964a3f-435b-45c3-b570-378ef0ef2677` | 실제6명, Original, 낮1라운드, 최후 변론 | 토론6회+변론6회, 개인 투표3개 선택. 전원 투표 미완료 | 기존 중단 기록 보존, 승자 없음 |
| `cf865ee7-0f65-471f-890b-b48a1d3a6c9c` | 실제6명, Original, 낮3라운드, 무작위 기본 직업, 자동 | Day1 첫 발언 DeepSeek 2회 최종 응답 소진 | 일시정지 원본 보존 후 검증 경기 안전 종료, 승자 없음 |

두 경기 모두 마피아1·경찰1·의사1·시민3 구성이다. 개별 비밀 배정·모델별 개인 기억·전체 호출·경찰 결과 영역은 로컬 `reports/local-real-game/<경기ID>/snapshot.json` 및 인증된 관리자 API에 보존하고 Git에서 제외했다. 실제 밤·조사·치료·사망·승리까지 진행한 기록은 없으므로 해당 실전 검증 성공을 주장하지 않는다. 5라운드 테스트는 실행하지 않았다.

첫 경기에는 여섯 모델의 실제 원문이 있다. EXAONE이 Ministral의 경찰 주장에 반응한 흔적이 있지만 직업 혼동인지 전략적 사칭인지 단정하지 못했다. DeepSeek는 깨진 혼합 문장을 반복했고 Llama도 외국어를 섞었다. 모순 탐지·기억·거짓말의 성공 사례로 과장하지 않는다.

모델별 A/B 응답 시간·한국어 평가와 실제 출력은 [품질 비교](../reports/quality/dialogue-comparison.json)에 있다. 이 파일은 **통제된 가상 상황에 대한 실제 모델 응답**이며 경기 대화가 아니다. EXAONE·Ministral 사망자 반응과 Character ON/OFF도 [별도 통제 실험](../reports/quality/dead-character.json)으로 구분했다. 공식 경기의 사망자 채팅은 아직 미검증이다.

공개 원문 및 로그:

- [첫 경기 대화](../reports/real-games/0d964a3f-435b-45c3-b570-378ef0ef2677/public_chat.jsonl), [이벤트](../reports/real-games/0d964a3f-435b-45c3-b570-378ef0ef2677/events.jsonl), [리플레이](../reports/real-games/0d964a3f-435b-45c3-b570-378ef0ef2677/replay.json), [감사](../reports/real-games/0d964a3f-435b-45c3-b570-378ef0ef2677/audit.json).
- [3라운드 검증 경기 이벤트](../reports/real-games/cf865ee7-0f65-471f-890b-b48a1d3a6c9c/events.jsonl), [리플레이](../reports/real-games/cf865ee7-0f65-471f-890b-b48a1d3a6c9c/replay.json), [감사](../reports/real-games/cf865ee7-0f65-471f-890b-b48a1d3a6c9c/audit.json). AI 발언은 게시되지 않아 해당 public_chat 파일은 비어 있다.

독립 감사는 첫 경기81개・두 번째6개 검사, 불일치0이었다. 전원 공개 투표·밤 기록이 없는 한계가 있다. 공식 승률에는 두 중단 경기 모두 반영하지 않았다. SQLite와 백업은 로컬에 보존했다. 웹의 경기 기록 화면으로 공개 내용을 다시 볼 수 있다.
