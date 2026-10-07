"""Bounded latest observations with current attempt failures and preserved last successes."""
from datetime import datetime, timezone
from collections import Counter
from .registry import PROVIDERS, REGISTRY_VERSION, targets
from .transport import Transport, Deferred
from . import evm, tron, solana, bitcoin

ADAPTERS={'tron':tron,'solana':solana,'bitcoin':bitcoin}

def summarize(probes):
    success=[p for p in probes if p['status']=='SUCCESS']; chains={}
    for p in success: chains.setdefault(p['chain'],set()).add(p['last_success']['positive'])
    assets={}
    for p in success:
        v=p['last_success']
        if p.get('contract') and 'asset_match' in v:
            assets.setdefault((p['chain'],p['contract']),set()).add(v['asset_match'])
    if any(len(v)>1 for v in list(chains.values())+list(assets.values())): return 'CONFLICT'
    if len(success)<len(probes) or any(p['last_success'].get('history_error') for p in success): return 'PARTIAL' if success else 'FAILED'
    return 'POSITIVE' if any(p['last_success']['positive'] for p in success) else 'NO_EVIDENCE'

def age(value,now):
    try: return (now-datetime.fromisoformat(value.replace('Z','+00:00'))).total_seconds()
    except (ValueError,TypeError,AttributeError): return float('inf')

def collect(snapshot,previous,now=None,*,observe=None,http=None):
    now=now or datetime.now(timezone.utc);stamp=now.isoformat().replace('+00:00','Z')
    http=http or Transport()
    real_observer = observe is None
    if observe is None:
        def observe(t,provider,address):
            return ADAPTERS.get(t.chain,evm).observe(t,provider[1],address,http)
    old={r['relation_id']:r for r in previous.get('rows',[]) } if previous.get('registry_version',REGISTRY_VERSION)==REGISTRY_VERSION else {}
    rows=[]
    for r in snapshot['rows']:
        ts=targets(r)
        if not ts: continue
        prior=old.get(r['relation_id'],{})
        if prior.get('address')!=r['address'] or prior.get('symbol')!=r['symbol']: prior={}
        oldprobes={(p['chain'],p['provider'],p.get('contract','')):p for p in prior.get('probes',[])}
        probes=[]
        for t in ts:
            for provider in PROVIDERS[t.chain]:
                prev=oldprobes.get((t.chain,provider[0],t.contract),{})
                # Endpoint changes must not reuse an old provider cache.
                if prev.get('endpoint',provider[1])!=provider[1]: prev={}
                partial_history=bool(prev.get('last_success',{}).get('history_error'))
                cached=(prev.get('status')=='SUCCESS' and not partial_history and 0<=age(prev.get('last_success',{}).get('checked_at'),now)<21600)
                retry_wait=((prev.get('status') in ('FAILED','DEFERRED') or partial_history) and 0<=age(prev.get('attempted_at'),now)<3600)
                if cached or retry_wait:
                    probes.append(dict(prev));continue
                p={'chain':t.chain,'provider':provider[0],'endpoint':provider[1],'contract':t.contract,
                   'asset':t.asset,'issuer_source':t.issuer_source,'attempted_at':stamp}
                if prev.get('last_success'): p['last_success']=dict(prev['last_success'])
                try:
                    value=observe(t,provider,r['address'])
                    value['checked_at']=datetime.now(timezone.utc).isoformat().replace('+00:00','Z') if real_observer else stamp
                    p.update(status='SUCCESS',last_success=value)
                except Deferred as e: p.update(status='DEFERRED',error=str(e)[:160])
                except Exception as e:
                    # No remote response contents/IPs/secrets in public errors.
                    p.update(status='FAILED',error=type(e).__name__+((' HTTP '+str(e.code)) if hasattr(e,'code') else '')+': provider observation failed')
                probes.append(p)
        rows.append({'relation_id':r['relation_id'],'address':r['address'],'symbol':r['symbol'],
                     'state':summarize(probes),'probes':probes})
    counts=dict(Counter(r['state'] for r in rows));counts['target_relations']=len(rows)
    counts['probes']=sum(len(r['probes']) for r in rows)
    counts['successful_probes']=sum(p['status']=='SUCCESS' for r in rows for p in r['probes'])
    return {'schema_version':1,'registry_version':REGISTRY_VERSION,'generated_at':stamp,
            'source_hash':snapshot.get('source',{}).get('sha256',''),'counts':counts,'rows':rows,
            'http_calls':http.calls,'scope':'公開OFACアドレスへの補足照会。公式記号の実ネットワーク・所有者・無効性は確定しない。'}
