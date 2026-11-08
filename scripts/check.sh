#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v node >/dev/null 2>&1; then
  for task_node in "$PWD"/.tools/node-*/bin; do
    [[ ! -x "$task_node/node" ]] || { export PATH="$task_node:$PATH"; break; }
  done
fi
.venv/bin/python -m pytest -q
npm test --prefix frontend
npm run build --prefix frontend
