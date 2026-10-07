"""USDT/USDCのアドレス形式候補。通貨記号からチェーンを確定しない。"""
import re
from .ethereum import validate_ethereum
from .utxo import validate_witness
from .solana import validate_solana
from .common import result, validate_base58

def validate_token(value):
    if re.fullmatch(r'0x[0-9a-fA-F]{64}',value):
        checked=result(value,reason='32バイト16進アドレスのチェーン別検証未対応・実ネットワーク未確定')
        checked.update(network_candidates=['hex32-family'],network_resolution='FAMILY_ONLY')
        return checked
    if value.startswith('0x'):
        checked=validate_ethereum(value);family='evm-family'
    elif value.startswith('T') and len(value)==34:
        checked=validate_base58(value,'',{65});family='tron-family'
    elif (value.startswith(('1','3','m','n','2')) and 26<=len(value)<=35) or value.lower().startswith(('bc1','tb1','bcrt1')):
        if value.lower().startswith(('bc1','tb1','bcrt1')):
            hrp=value.lower().split('1',1)[0]
            checked=validate_witness(value,'',hrp)
        else:
            checked=validate_base58(value,'',{0,5,111,196})
        family='bitcoin-family'
    else:
        checked=validate_solana(value);family='solana-family'
        if checked['validation']=='INVALID':
            return result(value,reason='トークンのネットワーク・アドレス種別は未確定（検証未対応）')
    # 接頭辞が似ていても、別の系統の正しい形式を不整合へ落とさない。
    if checked['validation']=='INVALID':
        solana=validate_solana(value)
        if solana['validation']=='FORMAT_ONLY':
            checked=solana;family='solana-family'
    checked['network']=''
    if checked['validation']!='INVALID':
        checked['review_reason']=('形式候補は正常・実ネットワーク未確定。'+checked['review_reason']).rstrip('。')
        checked['review_category']='LIMITATION'
        checked['network_candidates']=[family]
        checked['network_resolution']='FAMILY_ONLY'
    return checked
