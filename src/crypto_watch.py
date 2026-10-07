"""保存済み原本を検証して独立したアドレス監視台帳を出力する。"""
from __future__ import annotations
import argparse
import csv
import gzip
import hashlib
import json
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path

from .crypto_addresses import extract, PARSER_VERSION
from .crypto_ledger import reconcile
from .persistence import FileWrite, atomic_replace_many

ROOT = Path(__file__).resolve().parent.parent
MAX_RAW_BYTES = 250 * 1024 * 1024

def _read(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default

def _upstream(root):
    out=dict(last_checked='', status='UNKNOWN', http_status='', etag='', last_modified='',
             run_url='', outcome='UNKNOWN')
    attempt=_read(root/'data/monitoring/ofac_attempt.json',{})
    out.update(outcome=attempt.get('outcome','UNKNOWN'), run_url=attempt.get('run_url',''))
    for path in sorted((root/'data/heartbeat').glob('*.csv'), reverse=True):
        with path.open(encoding='utf-8-sig',newline='') as f:
            matches=[r for r in csv.DictReader(f) if r.get('source')=='ofac_sdn']
        if matches:
            out.update(last_checked=matches[-1]['checked_at'], status=matches[-1]['status']); break
    for path in sorted((root/'data/source_audit').glob('*.csv'),reverse=True):
        with path.open(encoding='utf-8-sig',newline='') as f:
            matches=[r for r in csv.DictReader(f) if r.get('source')=='ofac_sdn' and r.get('document_role')=='advanced_xml']
        if matches:
            for key in ('http_status','etag','last_modified'): out[key]=matches[-1].get(key,'')
            out['audit_checked_at']=matches[-1].get('checked_at','')
            break
    return out

def _counts(rows):
    listed=[r for r in rows if r['listing_status']=='LISTED']
    unique={(r['network'] or 'symbol:'+r['symbol'],r['normalized_address']) for r in listed}
    by_symbol={}
    for row in listed: by_symbol[row['symbol']]=by_symbol.get(row['symbol'],0)+1
    return dict(listed_relations=len(listed),unique_addresses=len(unique),
        removal_review=sum(r['listing_status']=='REMOVAL_REVIEW' for r in rows),
        format_review=sum(bool(r.get('review_reason')) for r in listed),by_symbol=by_symbol)

def _json_write(path, value):
    path.write_text(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n',encoding='utf-8')

def run(root: Path, now: str | None = None) -> dict:
    now=now or datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    previous=_read(root/'data/crypto/dashboard.json',{})
    rows=previous.get('rows',[]); events=previous.get('events',[])
    validation_events=previous.get('validation_events',[])
    upstream=_upstream(root)
    snapshot=dict(schema_version=1, generated_at=now, last_success=previous.get('last_success',''),
        status='FAILED', error='', upstream=upstream, source=previous.get('source',{}),
        rows=rows,events=events,validation_events=validation_events,counts=_counts(rows),
        new_event_count=0,new_validation_event_count=0,report=previous.get('report',{}))
    try:
        state=_read(root/'data/state.json',{}).get('ofac_sdn',{})
        raw=state.get('raw_advanced','')
        if not raw: raise ValueError('正常原本のパスがない')
        path=(root/raw).resolve()
        if not path.is_relative_to(root.resolve()): raise ValueError('原本パスがリポジトリ外')
        hasher=hashlib.sha256(); size=0
        with gzip.open(path,'rb') as f:
            while chunk:=f.read(1024*1024):
                size+=len(chunk)
                if size>MAX_RAW_BYTES: raise ValueError('原本サイズ上限超過')
                hasher.update(chunk)
        content_hash=hasher.hexdigest()
        if content_hash!=state.get('advanced_sha256'): raise ValueError('原本SHA256不一致')
        with gzip.open(path,'rb') as f: incoming,report=extract(f)
        old_count=previous.get('report',{}).get('raw_count',0)
        if old_count and report['raw_count'] < old_count*0.8: raise ValueError('アドレス件数20%以上急減・反映停止')
        new_rows,new_events=reconcile(rows,incoming,now,content_hash,PARSER_VERSION,
            previous_parser_version=previous.get('report',{}).get('parser_version',PARSER_VERSION))
        official_events=[e for e in new_events if e['kind']!='REVALIDATED']
        validation_updates=[e for e in new_events if e['kind']=='REVALIDATED']
        snapshot.update(status='SUCCESS',last_success=now,rows=new_rows,events=events+official_events,
            validation_events=validation_events+validation_updates,
            counts=_counts(new_rows), new_event_count=len(official_events),
            new_validation_event_count=len(validation_updates),report=report,
            source=dict(source='OFAC',list_name='SDN',source_type='OFFICIAL_SANCTIONS',
                url=state.get('advanced_url',''),raw_path=raw,sha256=content_hash,
                etag=state.get('advanced_etag',''),last_modified=state.get('advanced_last_modified',''),
                publication_date='',effective_date=''))
    except (ValueError,OSError,EOFError,zlib.error) as error:
        snapshot['error']=str(error)
    writes=[FileWrite(root/'data/crypto/dashboard.json',lambda p:_json_write(p,snapshot))]
    if snapshot['status']=='SUCCESS':
        writes.append(FileWrite(root/'data/crypto/address_master.json',lambda p:_json_write(p,snapshot['rows'])))
    atomic_replace_many(writes)
    return snapshot

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    args=parser.parse_args()
    snap=run(args.root)
    print(json.dumps({k:snap[k] for k in ('status','error','counts','new_event_count')},ensure_ascii=False))
    return 0 if snap['status']=='SUCCESS' else 1

if __name__=='__main__': sys.exit(main())
