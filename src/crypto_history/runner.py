"""Independent latest history scan with explicit failures and retained old results."""
from collections import Counter
from datetime import datetime, timezone
from ..crypto_chain.runner import age
from ..crypto_chain.transport import Deferred
from .registry import VERSION, INDEX, targets
from .transport import HistoryTransport
from . import evm,tron

def collect(snapshot,previous,now=None,*,http=None,observe=None):
    now=now or datetime.now(timezone.utc);stamp=now.isoformat().replace('+00:00','Z')
    http=http or HistoryTransport();real=observe is None
    if observe is None:
        def observe(t,address,h):return (tron if t.chain=='tron' else evm).observe(t,address,h)
    old={r['relation_id']:r for r in previous.get('rows',[])} if previous.get('registry_version')==VERSION else {}
    rows=[]
    for official in snapshot['rows']:
        for t in targets(official):
            provider,endpoint=INDEX[t.chain]
            fields=dict(relation_id=official['relation_id'],address=official['address'],symbol=official['symbol'],
                        chain=t.chain,contract=t.contract,issuer_source=t.issuer_source,
                        index_provider=provider,index_endpoint=endpoint)
            prev=old.get(official['relation_id'],{})
            if any(prev.get(k)!=v for k,v in fields.items()):prev={}
            value=prev.get('last_success',{})
            partial=value.get('state')=='PARTIAL'
            cached=prev.get('status')=='SUCCESS' and not partial and 0<=age(value.get('checked_at'),now)<21600
            budget_deferred=prev.get('status')=='DEFERRED' and prev.get('error','').startswith('request/time budget exhausted')
            waiting=not budget_deferred and (prev.get('status') in ('FAILED','DEFERRED') or partial) and 0<=age(prev.get('attempted_at'),now)<3600
            if cached or waiting:
                rows.append(dict(prev));continue
            r={**fields,'attempted_at':datetime.now(timezone.utc).isoformat().replace('+00:00','Z') if real else stamp}
            if value:r['last_success']=dict(value)
            verified=prev.get('last_verified') or (value if value.get('state')=='VERIFIED' else None)
            if verified:r['last_verified']=dict(verified)
            try:
                v=observe(t,official['address'],http)
                v['checked_at']=datetime.now(timezone.utc).isoformat().replace('+00:00','Z') if real else stamp
                r.update(status='SUCCESS',last_success=v)
                if v['state']=='VERIFIED':r['last_verified']=dict(v)
            except Deferred as e:r.update(status='DEFERRED',error=str(e)[:160])
            except Exception as e:
                r.update(status='FAILED',error=type(e).__name__+((' HTTP '+str(e.code)) if hasattr(e,'code') else '')+': token history lookup failed')
            rows.append(r)
    counts=dict(Counter(r['last_success']['state'] if r['status']=='SUCCESS' else r['status'] for r in rows))
    counts['target_relations']=len(rows)
    return dict(schema_version=1,registry_version=VERSION,
                generated_at=datetime.now(timezone.utc).isoformat().replace('+00:00','Z') if real else stamp,
                source_hash=snapshot.get('source',{}).get('sha256',''),counts=counts,rows=rows,http_calls=http.calls,
                scope='過去の発行元トークン送受信の補足証拠。全履歴・実ネットワークの一意確定・所有者・無効性は判定しない。')
