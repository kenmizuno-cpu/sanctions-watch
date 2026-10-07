"""Publish a compact, independent JSON after successful official extraction."""
import argparse
import json
from pathlib import Path
from .crypto_chain import collect
from .crypto_chain.transport import Transport
from .persistence import FileWrite, atomic_replace_many

def run(root=Path('.'), *, seconds=360, max_calls=1200):
    source=root/'data/crypto/dashboard.json';target=root/'data/crypto/chain_observations.json'
    snapshot=json.loads(source.read_text())
    if snapshot['status']!='SUCCESS': raise ValueError('official extraction failed; chain scan skipped')
    previous=json.loads(target.read_text()) if target.exists() else {}
    result=collect(snapshot,previous,http=Transport(seconds=seconds,max_calls=max_calls))
    raw=json.dumps(result,ensure_ascii=False,separators=(',',':'),sort_keys=True)+'\n'
    if len(raw.encode())>2*1024*1024: raise ValueError('chain observation JSON exceeds 2MiB')
    atomic_replace_many([FileWrite(target,lambda temp:temp.write_text(raw,encoding='utf-8'))])
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('.'));p.add_argument('--seconds',type=int,default=360)
    p.add_argument('--max-calls',type=int,default=1200);args=p.parse_args()
    result=run(args.root,seconds=args.seconds,max_calls=args.max_calls)
    print(json.dumps({'counts':result['counts'],'http_calls':result['http_calls']},ensure_ascii=False))
if __name__=='__main__': main()
