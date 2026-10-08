"""Publish bounded history evidence without modifying official or balance results."""
import argparse
import json
from pathlib import Path
from .crypto_history.runner import collect
from .crypto_history.transport import HistoryTransport
from .persistence import FileWrite,atomic_replace_many

def run(root=Path('.'),*,seconds=300,max_calls=600):
    source=root/'data/crypto/dashboard.json';target=root/'data/crypto/token_history.json'
    snapshot=json.loads(source.read_text(encoding='utf-8'))
    if snapshot['status']!='SUCCESS':raise ValueError('official extraction failed; history scan skipped')
    previous=json.loads(target.read_text(encoding='utf-8')) if target.exists() else {}
    result=collect(snapshot,previous,http=HistoryTransport(seconds=seconds,max_calls=max_calls))
    raw=json.dumps(result,ensure_ascii=False,separators=(',',':'),sort_keys=True)+'\n'
    if len(raw.encode())>2*1024*1024:raise ValueError('token history JSON exceeds 2MiB')
    atomic_replace_many([FileWrite(target,lambda temp:temp.write_text(raw,encoding='utf-8'))])
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('.'))
    p.add_argument('--seconds',type=int,default=300);p.add_argument('--max-calls',type=int,default=600)
    a=p.parse_args();result=run(a.root,seconds=a.seconds,max_calls=a.max_calls)
    print(json.dumps({'counts':result['counts'],'http_calls':result['http_calls']},ensure_ascii=False))

if __name__=='__main__':main()
