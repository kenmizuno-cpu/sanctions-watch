"""Finalized Solana address references. Incoming/failed refs are not owner evidence."""
import re
from .values import integer
GENESIS='5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d'

def signatures(reply):
    if not isinstance(reply,list) or len(reply)>5: raise ValueError('invalid signature result')
    successful=[];failed=0
    for r in reply:
        if (not re.fullmatch(r'[1-9A-HJ-NP-Za-km-z]{64,88}',r.get('signature',''))
            or r.get('confirmationStatus')!='finalized' or 'err' not in r):
            raise ValueError('invalid/unfinalized signature')
        slot=integer(r['slot'])
        if r['err'] is None: successful.append({'signature':r['signature'],'slot':slot})
        else: failed+=1
    return {'positive':bool(successful),'successful_references':len(successful),'failed_references':failed,
            'tx_sample':successful[:3],'asset_match':False,
            'scope':'最大5件のfinalized取引参照。取得先の履歴収録範囲に依存。送信者・所有者の証明ではない。'}

def observe(t,base,address,http):
    key=('solana',base)
    if key not in http.contexts:
        if http.rpc(base,'getGenesisHash',[])!=GENESIS: raise ValueError('wrong Solana genesis')
        http.contexts[key]=True
    balance=http.rpc(base,'getBalance',[address,{'commitment':'finalized'}])
    out=signatures(http.rpc(base,'getSignaturesForAddress',[address,{'limit':5,'commitment':'finalized'}]))
    out.update(balance_raw=integer(balance['value']),decimals=9,slot=integer(balance['context']['slot']),confirmation='finalized')
    out['positive']|=int(out['balance_raw'])>0
    return out
