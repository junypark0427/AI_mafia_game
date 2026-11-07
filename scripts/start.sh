#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -f .env ]]; then set -a; source .env; set +a; fi
if ! command -v node >/dev/null 2>&1; then
  for task_node in "$PWD"/.tools/node-*/bin; do
    if [[ -x "$task_node/node" ]]; then export PATH="$task_node:$PATH"; break; fi
  done
fi
if ! command -v node >/dev/null 2>&1; then echo 'Node.js 22 이상을 설치하세요: https://nodejs.org'; exit 1; fi
if [[ ! -d .venv ]]; then python3 -m venv .venv; fi
if ! .venv/bin/python -c 'import fastapi,uvicorn,httpx,psutil' >/dev/null 2>&1; then .venv/bin/python -m pip install -r backend/requirements.txt; fi
if [[ ! -d frontend/node_modules ]]; then npm ci --prefix frontend; fi
mkdir -p data
chmod 700 data
if curl -fsS --max-time 2 http://127.0.0.1:8000/api/state >/dev/null 2>&1; then echo '이미 서버가 실행 중입니다: http://127.0.0.1:8000'; exit 1; fi
OLLAMA_STARTED=''
CAFFEINATE_PID=''
API_PID=''
cleanup(){
  [[ -z "$API_PID" ]] || kill "$API_PID" 2>/dev/null || true
  [[ -z "$OLLAMA_STARTED" ]] || kill "$OLLAMA_STARTED" 2>/dev/null || true
  [[ -z "$CAFFEINATE_PID" ]] || kill "$CAFFEINATE_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
if ! curl -fsS --max-time 2 http://127.0.0.1:11434/api/version >/dev/null 2>&1; then
  if command -v ollama >/dev/null 2>&1; then
    OLLAMA_HOST=127.0.0.1:11434 OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_KEEP_ALIVE=0 OLLAMA_NO_CLOUD=1 ollama serve >data/ollama.log 2>&1 &
    OLLAMA_STARTED=$!
    for task_try in {1..30}; do curl -fsS --max-time 1 http://127.0.0.1:11434/api/version >/dev/null 2>&1 && break; sleep 1; done
  else echo 'Ollama가 없습니다. https://ollama.com 에서 설치하세요. Mock 모드는 사용할 수 있습니다.'; fi
else echo '기존 Ollama 서버를 사용합니다. 외부 동시 추론은 중지해 주세요.'; fi
if command -v caffeinate >/dev/null 2>&1; then caffeinate -i -s & CAFFEINATE_PID=$!; fi
npm run build --prefix frontend
.venv/bin/python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 5 &
API_PID=$!
for task_try in {1..40}; do curl -fsS --max-time 1 http://127.0.0.1:8000/api/state >/dev/null 2>&1 && break; sleep 1; done
if ! kill -0 "$API_PID" 2>/dev/null; then echo '백엔드 실행 실패'; exit 1; fi
echo 'AI MAFIA: http://127.0.0.1:8000'
echo '관리자 연결: 웹에서 data/admin-token.txt 파일을 선택하세요. 키를 공개하거나 Git에 추가하지 마세요.'
echo '종료: Control+C (이 스크립트가 시작한 프로세스만 종료합니다.)'
if [[ "${AI_MAFIA_OPEN_BROWSER:-1}" == '1' ]] && command -v open >/dev/null 2>&1; then open http://127.0.0.1:8000; fi
wait "$API_PID"
