"""MOFA document monitoring, isolated from recipient/master ingestion."""
from __future__ import annotations
import argparse, copy, json, shutil, tempfile
from dataclasses import asdict
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from .fetch import fetch, sha256, read_raw, Fetched
from .mofa_sources import (CATALOG_URL,PRESS_URL,DocumentLink,MofaSchemaError,validate_mofa_url,parse_catalog,parse_release_listing,parse_release_body)
from . import mofa_documents as D, source_audit as A, dashboard as DASH
from .persistence import FileWrite
from .state import append_heartbeat,load_state

PDF_LIMIT=20*1024*1024
HTML_LIMIT=5*1024*1024
JST=timezone(timedelta(hours=9))

class DocumentValidationError(ValueError):
    def __init__(self,message,fetched):
        super().__init__(message); self.fetched=fetched

def fetch_document(root: Path,*,link: DocumentLink,previous: dict,session=None) -> Fetched:
    previous=previous if previous.get('url')==link.url else {}
    f=fetch(link.url,prev=previous,session=session,url_validator=validate_mofa_url,max_bytes=PDF_LIMIT if link.role.endswith('pdf') else HTML_LIMIT,timeout=20)
    if f.not_modified:
        try:
            p=(root/previous['raw_path']).resolve()
            if not p.is_relative_to((root/'data/raw/mofa').resolve()): raise ValueError('unsafe cache path')
            body=read_raw(p)
            if sha256(body)!=previous.get('sha256'): raise ValueError('cache hash mismatch')
            f.body=body; f.raw_path=previous['raw_path']
        except (OSError,ValueError,KeyError,EOFError):
            f=fetch(link.url,session=session,allow_conditional=False,url_validator=validate_mofa_url,max_bytes=PDF_LIMIT if link.role.endswith('pdf') else HTML_LIMIT,timeout=20)
            if f.not_modified: raise DocumentValidationError('unconditional GET returned 304',f)
    if not f.body: raise DocumentValidationError('empty body',f)
    if link.role.endswith('pdf') and not f.body.startswith(b'%PDF-'): raise DocumentValidationError('PDF header missing',f)
    return f

def _months(now,from_month):
    local=now.astimezone(JST); current=date(local.year,local.month,1)
    previous=(current-timedelta(days=1)).replace(day=1)
    first=previous
    if from_month:
        try: first=date.fromisoformat(from_month+'-01')
        except ValueError: raise ValueError('from-month must be YYYY-MM')
        if first>current: raise ValueError('from-month is in the future')
        first=min(first,previous)
    values=[]; d=first
    while d<=current:
        values.append(d.strftime('%Y-%m'))
        d=date(d.year+int(d.month==12),1 if d.month==12 else d.month+1,1)
    return values,previous

def run(root: Path,*,now: datetime,session=None,new_notice_limit: int=30,dry_run: bool=False,from_month: str|None=None,report: Path|None=None) -> int:
    D.stamp(now)
    if new_notice_limit<1 or new_notice_limit>30: raise ValueError('new_notice_limit must be 1..30')
    if dry_run:
        with tempfile.TemporaryDirectory(prefix='mofa-preview-') as temp:
            clone=Path(temp)
            for rel in ['data/mofa','data/review/mofa_document_queue.csv','data/raw/mofa','data/heartbeat','data/state.json','data/manual/meti','data/dashboard/status.csv']:
                p=root/rel
                if p.exists():
                    target=clone/rel; target.parent.mkdir(parents=True,exist_ok=True)
                    if p.is_dir(): shutil.copytree(p,target)
                    else: shutil.copy2(p,target)
            result=run(clone,now=now,session=session,new_notice_limit=new_notice_limit,from_month=from_month,report=report)
            return result
    t=D.stamp(now); audits=[]; failures=[]
    try:
        state,events,queue=D.load_bundle(root)
    except Exception as error:
        _diagnostic(root,now,error)
        if report: _write_report(report,{'exit_code':1,'error':str(error)})
        return 1
    initial=not state['baseline_complete']
    previous_months,previous_start=_months(now,from_month)
    last=state.get('last_complete_scan_at','')
    if last and datetime.fromisoformat(last.replace('Z','+00:00')).astimezone(JST).date()<previous_start and not state['coverage_gap']:
        state['coverage_gap']={'from':last[:7],'to':previous_start.strftime('%Y-%m'),'detected_at':t}
    family_errors={'mofa_catalog':False,'mofa_press':False}
    family_schema={'mofa_catalog':False,'mofa_press':False}
    family_changes={'mofa_catalog':False,'mofa_press':False}
    for family in family_errors:
        state['families'].setdefault(family,{}).update(last_attempt_at=t)

    def obtain(link,family,parser=None,track=True,reason=''):
        f=None
        try:
            f=fetch_document(root,link=link,previous=state['resources'].get(link.key,{}),session=session)
            D.save_raw(root,f,link.role)
            value=parser(f.body) if parser else None
            if track:
                i=D.observe_document(state,events,queue,link=link,fetched=f,when=now,reason=reason)
                family_changes[family]|=bool(i)
            else:
                # Infrastructure and unrelated notices are cached but never become recipient changes.
                _cache(state,link,f,now)
            audits.append(A.entry(family,link.role,'validated',fetched=f,record_count=''))
            return f,value
        except Exception as error:
            family_errors[family]=True
            family_schema[family]|=isinstance(error,MofaSchemaError)
            failures.append({'url':link.url,'error':str(error)})
            if f is not None and not getattr(error,'fetched',None): error.fetched=f
            audits.append(A.error_entry(family,link.role,error,url=link.url,schema_changed=isinstance(error,MofaSchemaError),fetch_failed=not isinstance(error,MofaSchemaError)))
            if track or link.key in state['resources']:
                D.mark_unavailable(state,events,queue,link=link,when=now,reason=str(error))
            return None,None

    catalog=DocumentLink('catalog','catalog_html',CATALOG_URL,'テロ資金対策',baseline=True)
    _,links=obtain(catalog,'mofa_catalog',lambda b:parse_catalog(b,url=CATALOG_URL),track=False)
    if links is not None:
        for link in links:
            link.baseline=initial
            obtain(link,'mofa_catalog',reason='現行リスト')
    else:
        # Still inspect known PDFs when discovery fails; catalog remains an error.
        for key in ['current_un','current_1373']:
            r=state['resources'].get(key)
            if r: obtain(_link(r),'mofa_catalog',reason='保存済み現行リスト・入口異常')

    index_link=DocumentLink('press_index','listing_html',PRESS_URL,'報道発表一覧',baseline=True)
    # Index has a real month boundary lag; discover archive URLs without assigning dates to index entries.
    def index_parser(b):
        from .mofa_sources import _main,_url
        import re
        tree,main=_main(b)
        if '報道発表' not in tree.text(): raise MofaSchemaError('release heading missing')
        archives={}
        for a in main.walk():
            if a.tag!='a': continue
            href=a.attrs.get('href','')
            m=re.search(r'/release/(\d+)_(\d+)_index\.html',href)
            modern=re.search(r'/release/(\d{4})/(\d+)\.html',href)
            if m or modern:
                y,mo=map(int,(m or modern).groups())
                if m:y+=2018
                date(y,mo,1)
                archives[f'{y:04}-{mo:02}']=validate_mofa_url(_url(PRESS_URL,href))
        if not archives: raise MofaSchemaError('archive relationships missing')
        return archives
    index,archives=obtain(index_link,'mofa_press',index_parser,track=False)
    discovered=[]; scanned=[]
    if index is not None:
        for month in previous_months:
            u=archives.get(month)
            if u:
                l=DocumentLink('archive:'+month,'listing_html',u,'報道発表 '+month,baseline=True)
                _,listing=obtain(l,'mofa_press',lambda b,u=u,m=month:parse_release_listing(b,url=u,month=m),track=False)
            elif month==now.astimezone(JST).strftime('%Y-%m'):
                try:
                    listing=parse_release_listing(index.body,url=PRESS_URL,month=month)
                except Exception as error:
                    listing=None; family_errors['mofa_press']=True; family_schema['mofa_press']=True
                    failures.append({'url':PRESS_URL,'error':str(error)})
                    audits.append(A.error_entry('mofa_press','listing_html',error,url=PRESS_URL,schema_changed=True,fetch_failed=False))
            else:
                listing=None; family_errors['mofa_press']=True; family_schema['mofa_press']=True
                audits.append(A.error_entry('mofa_press','listing_html',MofaSchemaError('required archive link missing: '+month),url=PRESS_URL,schema_changed=True,fetch_failed=False))
            if listing is not None:
                scanned.append(month); discovered.extend(listing.notices)
    pending={x['key']:x for x in state['pending_notices']}
    cutoff=(now.astimezone(JST)-timedelta(days=31)).date().isoformat()
    excluded = state.setdefault('initial_out_of_scope', [])
    for l in discovered:
        if initial and not from_month and l.publication_date<cutoff:
            if l.key not in excluded: excluded.append(l.key)
            continue
        if l.key in excluded and not from_month:
            continue
        if l.key not in state['resources'] and l.key not in pending:
            l.baseline=initial or l.key in excluded
            pending[l.key]=asdict(l)
    # Review unresolved known notices even after the 31-day discovery window.
    unresolved={q['document_key'] for q in queue if q['review_status'] not in D.RESOLVED}
    known=[]
    for r in list(state['resources'].values()):
        if r['role']=='notice' and r.get('relevant') and (r.get('publication_date','')>=cutoff or r['key'] in unresolved or any(q['notice_url']==r['url'] and q['review_status'] not in D.RESOLVED for q in queue)):
            known.append(_link(r))
    selected=[DocumentLink(**x) for x in list(pending.values())[:new_notice_limit]]
    for link in known+selected:
        # Validate the body before committing its cache; relevance does not depend on the title.
        f,parsed=obtain(link,'mofa_press',lambda b,l=link:parse_release_body(b,link=l),track=False)
        if f is None: continue
        pending.pop(link.key,None)
        was_relevant = state['resources'][link.key].get('relevant',False)
        state['resources'][link.key]['relevant']=bool(parsed.reasons) or was_relevant
        if parsed.reasons or was_relevant or link.key in unresolved:
            # _cache deliberately leaves the observation chain unchanged.
            old=state['resources'][link.key].pop('_comparison',None)
            if old is None: state['resources'].pop(link.key,None)
            else: state['resources'][link.key]=old
            i=D.observe_document(state,events,queue,link=link,fetched=f,when=now,reason=' / '.join(parsed.reasons))
            family_changes['mofa_press']|=bool(i)
            state['resources'][link.key]['relevant']=True
            state['resources'][link.key]['external_links']=parsed.external_links
            attachments={a.key for a in parsed.attachments}
            for r in list(state['resources'].values()):
                if r['role']=='attachment_pdf' and r.get('notice_url')==link.url and r['key'] not in attachments:
                    D.mark_unavailable(state,events,queue,link=_link(r),when=now,reason='attachment link missing')
                    family_errors['mofa_press']=True
            for a in parsed.attachments: obtain(a,'mofa_press',reason='別添 / '+' / '.join(parsed.reasons))
    # Do not persist the internal comparison snapshot in cache records.
    for r in state['resources'].values(): r.pop('_comparison',None)
    state['pending_notices']=list(pending.values())
    if not family_errors['mofa_press'] and not pending:
        if state['coverage_gap'] and from_month and from_month<=state['coverage_gap']['from'] and all(m in scanned for m in previous_months):
            state.setdefault('coverage_history',[]).append({**state['coverage_gap'],'resolved_at':t,'resolution':'archive scan'})
            state['coverage_gap']=None
        if not state['coverage_gap']:
            state['last_complete_scan_at']=t
            state['baseline_complete']=True
    for family in family_errors:
        value=state['families'][family]
        if not family_errors[family]: value['last_success_at']=t
        if family_changes[family]: value['last_document_change_at']=t
        value['pending_count']=len(pending) if family=='mofa_press' else 0
        value['status']='schema_changed' if family_schema[family] else 'error' if family_errors[family] else 'COVERAGE_GAP' if family=='mofa_press' and state['coverage_gap'] else 'checking' if family=='mofa_press' and pending else 'document_updated' if family_changes[family] else 'unchanged'
        value['sha256']=sha256(json.dumps({k:r['sha256'] for k,r in state['resources'].items() if (k in {'catalog','current_un','current_1373'})==(family=='mofa_catalog')},sort_keys=True).encode())
    hb=[{'source':k,'status':v['status'],'record_count':'','content_hash':v['sha256'],'source_updated':v.get('last_document_change_at','')} for k,v in state['families'].items()]
    result=int(any(family_errors.values()))
    audit_path=root/'data/source_audit'/f'{now:%Y-%m}.csv'
    try:
        # Acquisition evidence is append-only even if the formal bundle rolls back.
        A.append_rows(audit_path,audits,now=now)
        writes=D._projection_writes(root,state,queue)
        writes.append(FileWrite(root/'data/heartbeat'/f'{now:%Y-%m}.csv',lambda p:append_heartbeat(p,hb,now=now.astimezone(timezone.utc)),seed_existing=True))
        D.save_bundle(root,state,events,queue,writes)
    except Exception as error:
        result=1; failures.append({'persistence_error':str(error)}); _diagnostic(root,now,error)
    if report:
        _write_report(report,{'checked_at':t,'exit_code':result,'families':state['families'],'pending_count':len(pending),'baseline_complete':state['baseline_complete'],'coverage_gap':state['coverage_gap'],'document_candidates':DASH.build_mofa_document_rows(queue),'audits':audits,'failures':failures})
    return result

def _cache(state,link,f,now):
    old=copy.deepcopy(state['resources'].get(link.key))
    t=D.stamp(now)
    state['resources'][link.key]={**(old or {}),'key':link.key,'role':link.role,'url':link.url,'title':link.title,'notice_url':link.notice_url,'publication_date':link.publication_date,'publication_precision':link.publication_precision,'baseline':link.baseline,'sha256':f.sha256,'raw_path':f.raw_path,'etag':f.etag,'last_modified':f.last_modified,'first_seen':(old or {}).get('first_seen',t),'last_success_at':t,'last_attempt_at':t,'last_result':'unchanged','available':True,'observation_head':(old or {}).get('observation_head',''),'_comparison':old}

def _link(r):
    return DocumentLink(r['key'],r['role'],r['url'],r.get('title',''),r.get('notice_url',''),r.get('publication_date',''),r.get('publication_precision',''),r.get('baseline',False))

def _write_report(path,value):
    path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')

def _diagnostic(root,now,error):
    _write_report(root/'data/mofa/last_failure.json',{'checked_at':D.stamp(now),'error_type':type(error).__name__,'error':str(error)})

def main(argv=None):
    p=argparse.ArgumentParser(); p.add_argument('--root',type=Path,default=Path('.')); p.add_argument('--dry-run',action='store_true'); p.add_argument('--from-month'); p.add_argument('--report',type=Path)
    a=p.parse_args(argv)
    return run(a.root,now=datetime.now(timezone.utc),dry_run=a.dry_run,from_month=a.from_month,report=a.report)
if __name__=='__main__': raise SystemExit(main())
