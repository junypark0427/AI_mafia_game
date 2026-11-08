#!/usr/bin/env python3
"""Scan upload candidates and reachable Git blobs without printing secret values.

This is a targeted local check, not a substitute for GitHub secret scanning.
Private runtime files are read only to detect accidental copies of the admin key.
"""
from pathlib import Path
import re,subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT)
PATTERNS=[re.compile(rb'gh[pousr]_[A-Za-z0-9]{30,}'),re.compile(rb'github_pat_[A-Za-z0-9_]{40,}'),
 re.compile(rb'sk-(?:proj-)?[A-Za-z0-9_-]{32,}'),re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')]
key=ROOT/'data/admin-token.txt';actual=key.read_bytes().strip() if key.exists() else b''
def sensitive(data):return (len(actual)>=24 and actual in data) or any(p.search(data) for p in PATTERNS)
def forbidden(name):
 p=Path(name)
 return (p.name.startswith('.env') and p.name!='.env.example') or any(x in p.parts for x in ('node_modules','.venv','.ollama','__pycache__')) or p.suffix in ('.gguf','.sqlite','.sqlite3','.db','.pem','.key','.log') or (name.startswith('data/') and name!='data/.gitkeep') or name.startswith('reports/local-')
issues=[];files=git('ls-files','--cached','--others','--exclude-standard','-z').decode().split('\0');count=0
for name in filter(None,files):
 p=ROOT/name
 if not p.is_file():continue
 count+=1
 if forbidden(name):issues.append(('forbidden upload path',name))
 if sensitive(p.read_bytes()):issues.append(('secret match in working tree',name))
blobs={}
for line in git('rev-list','--objects','--all').decode().splitlines():
 oid,_,name=line.partition(' ')
 if name:blobs.setdefault(oid,name)
for oid,name in blobs.items():
 if git('cat-file','-t',oid).strip()!=b'blob':continue
 if sensitive(git('cat-file','blob',oid)):issues.append(('secret match in Git history',name+' @ '+oid[:12]))
 if forbidden(name):issues.append(('forbidden path in Git history',name))
for kind,name in issues:print(kind+': '+name)
print(f'Scanned {count} upload files and {len(blobs)} reachable objects; findings={len(issues)}')
sys.exit(bool(issues))
