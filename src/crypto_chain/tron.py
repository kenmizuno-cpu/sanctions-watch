"""TRON solidified account and allowlisted issuer balanceOf. Never sign/broadcast."""
import re
from ..crypto_validation.common import decode_base58
from .values import integer

GENESIS='00000000000000001ebf88508a03865c71d452e25f4d51194196a1d22b6653dc'

def observe(t,base,address,http):
    key=('tron',base)
    if key not in http.contexts:
        genesis=http.request(base,'/walletsolidity/getblockbynum',{'num':0})
        if genesis.get('blockID')!=GENESIS: raise ValueError('wrong TRON genesis')
        b=http.request(base,'/walletsolidity/getnowblock',{})
        header=b['block_header']['raw_data']
        if not re.fullmatch(r'[0-9a-f]{64}',b['blockID']): raise ValueError('invalid TRON blockID')
        http.contexts[key]=(integer(header['number']),b['blockID'],integer(header['timestamp']))
    height,block_hash,stamp=http.contexts[key]
    a=http.request(base,'/walletsolidity/getaccount',{'address':address,'visible':True})
    if not isinstance(a,dict): raise ValueError('invalid TRON account')
    # Some nodes echo hex despite visible=true. Compare decoded bytes as well.
    echoed=a.get('address','')
    if echoed and echoed not in (address,decode_base58(address)[:-4].hex()): raise ValueError('wrong account echo')
    out={'block_height':height,'block_hash':block_hash,'block_timestamp':stamp,'confirmation':'solidified',
         'balance_raw':integer(a.get('balance',0)),'decimals':6,'account_present':bool(echoed),
         'asset_match':False,'positive':bool(echoed),
         'scope':'solidified状態。高さは照会開始時点の参考値（残高の固定ブロックではない）。全取引履歴は未確認。'}
    if t.contract:
        r=http.request(base,'/walletsolidity/triggerconstantcontract',{
            'owner_address':address,'contract_address':t.contract,'visible':True,
            'function_selector':'balanceOf(address)',
            'parameter':decode_base58(address)[1:-4].hex().zfill(64)})
        values=r.get('constant_result',[])
        if r.get('result',{}).get('result') is not True or len(values)!=1 or not re.fullmatch(r'[0-9a-fA-F]{64}',values[0]):
            raise ValueError('balanceOf simulation failed')
        out['token_balance_raw']=str(int(values[0],16));out['token_decimals']=6
        out['asset_match']=int(out['token_balance_raw'])>0
        out['positive']|=out['asset_match']
        out['scope']+=' 発行元USDT契約の残高を照合。取引送信なし。'
    return out
