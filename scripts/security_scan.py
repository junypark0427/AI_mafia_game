#!/usr/bin/env python3
"""Scan staged files and every reachable Git blob without printing secret values."""
import fnmatch,re,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT)
BLOCKED=['.env','.env.*','*.db','*.db-*','*.sqlite*','*.log','*.pem','*.key','*.p12','*.pfx','*.gguf','*.safetensors','*admin-token*','*credentials*']
PATTERNS=[rb'gh[pousr]_[A-Za-z0-9]{30,}',rb'github_pat_[A-Za-z0-9_]{40,}',rb'AKIA[0-9A-Z]{16}',rb'sk-[A-Za-z0-9_-]{32,}',rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----']
known=[]
token_file=ROOT/'data/admin-token.txt'
if token_file.exists():known.append(token_file.read_bytes().strip())
issues=[];scanned=set()
def check(path,blob,source):
    parts=path.split('/')
    if path!='.env.example' and (any(fnmatch.fnmatch(parts[-1],p) for p in BLOCKED) or any(p in parts for p in ('.venv','node_modules','__pycache__','.tools','.ollama')) or path.startswith('data/') and path!='data/.gitkeep' or path.startswith('frontend/dist/') or path.startswith('reports/local-')):
        issues.append(f'{source}: excluded path {path}')
    if blob in scanned:return
    scanned.add(blob);data=git('cat-file','blob',blob)
    if any(re.search(p,data) for p in PATTERNS) or any(v and v in data for v in known):issues.append(f'{source}: possible secret in {path} (value hidden)')
for line in git('ls-files','-s','-z').split(b'\0'):
    if not line:continue
    meta,path=line.split(b'\t',1);blob=meta.split()[1].decode();check(path.decode(),blob,'index')
try:objects=git('rev-list','--objects','--all').decode().splitlines()
except subprocess.CalledProcessError:objects=[]
for line in objects:
    fields=line.split(' ',1)
    if len(fields)==2 and git('cat-file','-t',fields[0]).strip()==b'blob':check(fields[1],fields[0],'history')
if issues:
    print('\n'.join(sorted(set(issues))));sys.exit(1)
print(f'PASS: {len(scanned)} unique staged/history blobs; excluded paths, credential signatures and current local admin key checked. Values were not printed.')
