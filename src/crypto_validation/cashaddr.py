"""BCH CashAddrの接頭辞・payload・40ビットチェックサム。自動補正しない。"""
from .bech32 import CHARSET, convertbits
from .common import result, validate_base58

def polymod(data):
    value = 1
    generators = (0x98f2bc8e61,0x79b76d99e2,0xf33e5fb3c4,0xae2eabe2a8,0x1e4f43e470)
    for digit in data:
        top = value >> 35
        value = ((value & 0x07ffffffff) << 5) ^ digit
        for i, generator in enumerate(generators):
            if (top >> i) & 1: value ^= generator
    return value ^ 1

def validate_cashaddr(value):
    if value.startswith(('1','3')):
        return validate_base58(value,'bitcoin-cash',{0,5})
    valid = False
    unsupported_type = False
    if value==value.lower() or value==value.upper():
        text=value.lower()
        parts=text.split(':')
        prefix,payload = ('bitcoincash',parts[0]) if len(parts)==1 else (parts if len(parts)==2 else ('',''))
        if prefix=='bitcoincash' and len(payload)>8 and all(c in CHARSET for c in payload):
            digits=[CHARSET.index(c) for c in payload]
            raw=convertbits(digits[:-8],5,8,False)
            checksum=polymod([ord(c)&31 for c in prefix]+[0]+digits)==0
            if raw and checksum and not raw[0]&128:
                sizes=(20,24,28,32,40,48,56,64)
                valid=len(raw)-1==sizes[raw[0]&7]
                unsupported_type=valid and ((raw[0]>>3)&15) not in {0,1}
    state='UNSUPPORTED' if unsupported_type else 'CHECKSUM_VALID' if valid else 'INVALID'
    return result(value,network='bitcoin-cash',validation=state,method='CASHADDR',
        reason='CashAddrのアドレス種別は検証未対応' if unsupported_type else '' if valid else 'CashAddrの接頭辞・長さ・形式・チェックサム不整合',
        detail='BCH mainnetの明示/省略接頭辞、大小文字、version、hash長、padding、40ビットチェックサムを検証')
