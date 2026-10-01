"""Document evidence lifecycle. This module never edits screening master data."""
from __future__ import annotations
import argparse, csv, gzip, hashlib, io, json, re
from datetime import datetime, timezone
from pathlib import Path
from .fetch import Fetched, sha256, read_raw
from .mofa_sources import DocumentLink, validate_mofa_url
from .persistence import FileWrite, atomic_replace_many

STATE_PATH='data/mofa/state.json'
EVENT_PATH='data/mofa/events.csv'
QUEUE_PATH='data/review/mofa_document_queue.csv'
EVENT_COLS=['event_id','checked_at','document_key','role','title','publication_date','publication_precision','notice_url','url','previous_url','source_hash','previous_hash','raw_path','event_type','reason','fetch_status','review_status','reviewer','reviewed_at','note','diff_level','applied']
QUEUE_COLS=EVENT_COLS.copy()
RESOLVED={'REVIEWED_DOCUMENT','CANCELLED_DOCUMENT'}

class DocumentStateError(ValueError):
    pass

def stamp(when):
    if not isinstance(when,datetime) or when.tzinfo is None or when.utcoffset() is None:
        raise DocumentStateError('timezone required')
    return when.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

def empty_bundle():
    return ({'schema_version':1,'resources':{},'families':{},'pending_notices':[], 'baseline_complete':False,'last_complete_scan_at':'','coverage_gap':None},[],[])

def _valid_time(value):
    if value:
        try: stamp(datetime.fromisoformat(value.replace('Z','+00:00')))
        except (ValueError,TypeError): raise DocumentStateError('invalid timestamp')

def validate_bundle(state,events,queue):
    if not isinstance(state,dict) or state.get('schema_version')!=1:
        raise DocumentStateError('unknown MOFA schema')
    for k in empty_bundle()[0]:
        if k not in state: raise DocumentStateError('state field missing: '+k)
    if not isinstance(state['resources'],dict) or not isinstance(state['families'],dict) or not isinstance(state['pending_notices'],list):
        raise DocumentStateError('invalid state containers')
    if not isinstance(state['baseline_complete'],bool): raise DocumentStateError('invalid baseline flag')
    if state.get('mode') == 'manual':
        manual = state.get('manual')
        if not isinstance(manual,dict) or not isinstance(manual.get('last_operation_at'),str):
            raise DocumentStateError('invalid manual state')
        _valid_time(manual['last_operation_at'])
        if any(not isinstance(manual.get(k),dict) for k in ['import_errors','linked_documents']):
            raise DocumentStateError('invalid manual containers')
        for error in manual['import_errors'].values():
            if not isinstance(error,dict) or error.get('family') not in {'mofa_catalog','mofa_press'}:
                raise DocumentStateError('invalid manual import error')
        for links in manual['linked_documents'].values():
            if not isinstance(links,list): raise DocumentStateError('invalid linked documents')
            for link in links:
                if not isinstance(link,dict) or not all(k in link for k in ['key','url','observed_at']):
                    raise DocumentStateError('invalid linked document')
                validate_mofa_url(link['url']); _valid_time(link['observed_at'])
        for family in state['families'].values():
            _valid_time(family.get('last_manual_check_at',''))
    for r in state['resources'].values():
        for k in ['key','role','url','sha256','raw_path','etag','last_modified','first_seen','last_success_at','last_attempt_at','last_result','observation_head','available']:
            if k not in r: raise DocumentStateError('resource field missing: '+k)
        validate_mofa_url(r['url'])
        if r['sha256'] and not re.fullmatch('[a-f0-9]{64}',r['sha256']): raise DocumentStateError('bad hash')
        for k in ['first_seen','last_success_at','last_attempt_at']: _valid_time(r[k])
    _valid_time(state['last_complete_scan_at'])
    ids={}
    for rows in (events,queue):
        own=set()
        for row in rows:
            if set(row)!=set(EVENT_COLS): raise DocumentStateError('invalid event columns')
            i=row['event_id']
            if not re.fullmatch('[a-f0-9]{64}',i) or i in own: raise DocumentStateError('duplicate/invalid event id')
            own.add(i)
            if i in ids and ids[i]!=row['source_hash']: raise DocumentStateError('conflicting event hash')
            ids[i]=row['source_hash']
            if row['source_hash'] and not re.fullmatch('[a-f0-9]{64}',row['source_hash']): raise DocumentStateError('bad event hash')
            if row['diff_level']!='document' or row['applied']!='false': raise DocumentStateError('not document evidence')
            for k in ['checked_at','reviewed_at']: _valid_time(row[k])
    event_ids={x['event_id'] for x in events}
    if any(x['event_id'] not in event_ids for x in queue): raise DocumentStateError('orphan queue event')
    observations = {x['event_id']: x for x in events if x['event_type'] != 'DOCUMENT_REVIEW'}
    queue_map = {x['event_id']: x for x in queue}
    if set(observations) != set(queue_map):
        raise DocumentStateError('missing observation queue row')
    mutable = {'review_status','reviewer','reviewed_at','note'}
    for i, original in observations.items():
        if any(original[k] != queue_map[i][k] for k in EVENT_COLS if k not in mutable):
            raise DocumentStateError('queue evidence differs from original event')

def _csv_bytes(rows,cols):
    s=io.StringIO(newline=''); w=csv.DictWriter(s,fieldnames=cols,lineterminator='\n'); w.writeheader(); w.writerows(rows)
    return s.getvalue().encode()

def _load_csv(path):
    if not path.exists(): return []
    with path.open(encoding='utf-8',newline='') as f:
        r=csv.DictReader(f)
        if r.fieldnames!=EVENT_COLS: raise DocumentStateError('invalid CSV header: '+str(path))
        return list(r)

def load_bundle(root):
    p=root/STATE_PATH
    if not p.exists():
        if (root/EVENT_PATH).exists() or (root/QUEUE_PATH).exists(): raise DocumentStateError('missing state with existing history')
        return empty_bundle()
    try: state=json.loads(p.read_text())
    except (ValueError,OSError) as e: raise DocumentStateError('invalid state') from e
    if not (root/EVENT_PATH).exists() or not (root/QUEUE_PATH).exists(): raise DocumentStateError('incomplete bundle')
    events=_load_csv(root/EVENT_PATH); queue=_load_csv(root/QUEUE_PATH)
    validate_bundle(state,events,queue)
    return state,events,queue

def save_raw(root: Path, fetched: Fetched, role: str):
    if fetched.body is None or sha256(fetched.body)!=fetched.sha256: raise DocumentStateError('raw hash mismatch')
    ext='pdf' if role.endswith('pdf') else 'html'
    p=root/'data/raw/mofa'/f'{fetched.sha256}.{ext}.gz'
    p.parent.mkdir(parents=True,exist_ok=True)
    if p.exists():
        if read_raw(p)!=fetched.body: raise DocumentStateError('existing raw corrupt')
    else:
        atomic_replace_many([FileWrite(p,lambda t:t.write_bytes(gzip.compress(fetched.body,mtime=0)))])
    fetched.raw_path=p.relative_to(root).as_posix()
    return fetched.raw_path

def _event(state,events,queue,link,when,event_type,source_hash,raw_path,reason,fetch_status):
    prev=state['resources'].get(link.key,{})
    identity={'key':link.key,'previous':prev.get('observation_head',''),'url':link.url,'sha256':source_hash,'kind':event_type}
    i=sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode())
    row={k:'' for k in EVENT_COLS}
    row.update(event_id=i,checked_at=stamp(when),document_key=link.key,role=link.role,title=link.title,publication_date=link.publication_date,publication_precision=link.publication_precision,notice_url=link.notice_url,url=link.url,previous_url=prev.get('url',''),source_hash=source_hash,previous_hash=prev.get('sha256',''),raw_path=raw_path,event_type=event_type,reason=reason,fetch_status=fetch_status,review_status='REVIEW_REQUIRED_DOCUMENT',diff_level='document',applied='false')
    events.append(row.copy()); queue.append(row.copy())
    return i

def observe_document(state,events,queue,*,link,fetched,when,reason=''):
    t=stamp(when); prev=state['resources'].get(link.key,{})
    changed=not prev or not prev.get('available',False) or prev['url']!=link.url or prev['sha256']!=fetched.sha256
    i=None
    if changed:
        kind=('BASELINED' if link.baseline else 'DETECTED') if not prev else 'REAPPEARED' if not prev.get('available') else 'LINK_CHANGED_SAME_CONTENT' if prev['sha256']==fetched.sha256 else 'CONTENT_CHANGED'
        i=_event(state,events,queue,link,when,kind,fetched.sha256,fetched.raw_path,reason,'DOCUMENT_VALIDATED')
    state['resources'][link.key]={**prev,'key':link.key,'role':link.role,'url':link.url,'title':link.title,'notice_url':link.notice_url,'publication_date':link.publication_date,'publication_precision':link.publication_precision,'baseline':link.baseline,'sha256':fetched.sha256,'raw_path':fetched.raw_path or prev.get('raw_path',''),'etag':fetched.etag,'last_modified':fetched.last_modified,'first_seen':prev.get('first_seen',t),'last_success_at':t,'last_attempt_at':t,'last_result':'unchanged' if not changed else 'document_updated','observation_head':i or prev.get('observation_head',''),'available':True}
    return i

def mark_unavailable(state,events,queue,*,link,when,reason):
    prev=state['resources'].get(link.key,{})
    # Preserve the last validated cache, including URL and validators.
    i=None
    if prev.get('last_result')!='BLOCKED' or prev.get('failure_reason')!=reason:
        i=_event(state,events,queue,link,when,'UNAVAILABLE',prev.get('sha256',''),prev.get('raw_path',''),reason,'BLOCKED')
    if not prev:
        prev={'key':link.key,'role':link.role,'url':link.url,'sha256':'','raw_path':'','etag':'','last_modified':'','first_seen':stamp(when),'last_success_at':'','observation_head':''}
    state['resources'][link.key]={**prev,'available':False,'last_attempt_at':stamp(when),'last_result':'BLOCKED','failure_reason':reason,'observation_head':i or prev.get('observation_head','')}
    return i

def save_bundle(root,state,events,queue,extra_writes=None):
    if state.get('mode') == 'manual':
        from .mofa_manual import refresh_status
        refresh_status(state,queue)
    validate_bundle(state,events,queue)
    writes=[FileWrite(root/STATE_PATH,lambda p:p.write_text(json.dumps(state,ensure_ascii=False,sort_keys=True,indent=2)+'\n')),
            FileWrite(root/EVENT_PATH,lambda p:p.write_bytes(_csv_bytes(events,EVENT_COLS))),
            FileWrite(root/QUEUE_PATH,lambda p:p.write_bytes(_csv_bytes(queue,QUEUE_COLS)))]
    atomic_replace_many(writes+(extra_writes or []))

def _projection_writes(root,state,queue):
    from .dashboard import build_mofa_document_rows,write_mofa_document_rows,build_status_rows,write_status_rows
    from .state import load_state
    return [FileWrite(root/'data/dashboard/mofa_documents.csv',lambda p:write_mofa_document_rows(p,build_mofa_document_rows(queue))),FileWrite(root/'data/dashboard/status.csv',lambda p:write_status_rows(p,build_status_rows(root,[],load_state(root),mofa_state=state)))]

def review_document(root: Path,*,event_id: str,source_hash: str,reviewer: str,when: datetime,note: str,cancel: bool=False):
    t=stamp(when)
    if not reviewer.strip() or not note.strip(): raise DocumentStateError('reviewer and note required')
    s,e,q=load_bundle(root)
    row=next((x for x in q if x['event_id']==event_id),None)
    if row is None or row['source_hash']!=source_hash: raise DocumentStateError('event/hash mismatch')
    if source_hash:
        path=(root/row['raw_path']).resolve()
        if not path.is_relative_to((root/'data/raw/mofa').resolve()) or sha256(read_raw(path))!=source_hash: raise DocumentStateError('review evidence missing or corrupt')
    row.update(review_status='CANCELLED_DOCUMENT' if cancel else 'REVIEWED_DOCUMENT',reviewer=reviewer,reviewed_at=t,note=note)
    review=dict(row); review['event_id']=sha256(json.dumps({'parent':event_id,'status':row['review_status'],'at':t,'reviewer':reviewer,'note':note},sort_keys=True).encode()); review['event_type']='DOCUMENT_REVIEW'; review['checked_at']=t
    if not any(x['event_id']==review['event_id'] for x in e): e.append(review)
    save_bundle(root,s,e,q,_projection_writes(root,s,q))

def acknowledge_coverage_gap(root: Path,*,reviewer: str,when: datetime,note: str):
    t=stamp(when)
    if not reviewer.strip() or not note.strip(): raise DocumentStateError('reviewer and note required')
    s,e,q=load_bundle(root)
    if not s['coverage_gap']: raise DocumentStateError('no coverage gap')
    s.setdefault('coverage_history',[]).append({**s['coverage_gap'],'reviewer':reviewer,'at':t,'note':note})
    s['coverage_gap']=None
    s['last_complete_scan_at']=t
    save_bundle(root,s,e,q,_projection_writes(root,s,q))

def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument('operation',choices=['review','cancel','coverage-ack']); p.add_argument('--root',type=Path,default=Path('.')); p.add_argument('--event-id'); p.add_argument('--source-hash'); p.add_argument('--reviewer',required=True); p.add_argument('--at',required=True); p.add_argument('--note',required=True)
    a=p.parse_args(argv); when=datetime.fromisoformat(a.at.replace('Z','+00:00'))
    if a.operation=='coverage-ack': acknowledge_coverage_gap(a.root,reviewer=a.reviewer,when=when,note=a.note)
    else: review_document(a.root,event_id=a.event_id,source_hash=a.source_hash,reviewer=a.reviewer,when=when,note=a.note,cancel=a.operation=='cancel')
    return 0
if __name__=='__main__': raise SystemExit(main())
