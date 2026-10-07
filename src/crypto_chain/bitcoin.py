"""Bitcoin mainnet confirmed Esplora stats; never imply Omni token evidence."""
from .values import integer
GENESIS='000000000019d6689c085ae165831e934ff763ae46a2a6c172b3f1b60a8ce26f'

def account(reply,address):
    if reply.get('address')!=address: raise ValueError('wrong Bitcoin address echo')
    stats=reply['chain_stats'];funded=int(integer(stats['funded_txo_sum']));spent=int(integer(stats['spent_txo_sum']))
    if spent>funded: raise ValueError('negative Bitcoin balance')
    tx=integer(stats['tx_count'])
    return {'balance_raw':str(funded-spent),'decimals':8,'tx_count':tx,'positive':int(tx)>0,
            'asset_match':False,'confirmation':'confirmed',
            'scope':'Bitcoin確認済み履歴のみ。USDT/Omni property 31の残高・取引は未確認。'}

def observe(t,base,address,http):
    # Esplora genesis endpoint returns plain text; identity is checked by block object instead.
    key=('bitcoin',base)
    if key not in http.contexts:
        g=http.request(base,'/block/'+GENESIS)
        if g.get('id')!=GENESIS or g.get('height')!=0: raise ValueError('wrong Bitcoin genesis')
        http.contexts[key]=True
    return account(http.request(base,'/address/'+address),address)
