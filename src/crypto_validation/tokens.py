"""USDT/USDCのアドレス形式候補。通貨記号からチェーンを確定しない。"""
from .ethereum import validate_ethereum
from .bitcoin import validate_bitcoin
from .solana import validate_solana
from .common import result, validate_base58

def validate_token(value):
    if value.startswith('0x'):
        checked=validate_ethereum(value);family='evm-family'
    elif value.startswith('T') and len(value)==34:
        checked=validate_base58(value,'',{65});family='tron-family'
    elif (value.startswith(('1','3')) and 26<=len(value)<=35) or value.lower().startswith(('bc1','tb1','bcrt1')):
        checked=validate_bitcoin(value);family='bitcoin-family'
    else:
        checked=validate_solana(value);family='solana-family'
        if checked['validation']=='INVALID':
            return result(value,reason='トークンのネットワーク・アドレス種別は未確定（検証未対応）')
    checked['network']=''
    if checked['validation']!='INVALID':
        checked['review_reason']=('形式候補は正常・実ネットワーク未確定。'+checked['review_reason']).rstrip('。')
        checked['review_category']='LIMITATION'
        checked['network_candidates']=[family]
        checked['network_resolution']='FAMILY_ONLY'
    return checked
