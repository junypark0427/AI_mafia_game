#!/usr/bin/env python3
"""Run from root: PYTHONPATH=backend .venv/bin/python scripts/benchmark.py"""
import argparse,asyncio,json
from app.models import CATALOG,Inference
from app.ollama import Ollama
from app.storage import Store
from app.benchmark import run_benchmark
async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--quality',action='store_true');parser.add_argument('--models',nargs='+');args=parser.parse_args()
    results=await run_benchmark(Ollama(),Store('data/mafia.sqlite3'),args.models or [c['model'] for c in CATALOG],Inference().model_dump(),args.quality)
    print(json.dumps(results,ensure_ascii=False,indent=2))
if __name__=='__main__':asyncio.run(main())
