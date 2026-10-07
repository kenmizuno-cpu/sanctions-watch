"""XRP classicアドレス。X-addressは未対応として残す。"""
from .common import base58_check_bytes, result

ALPHABET='rpshnaf39wBUDNEGHJKLM4PQRST7VWXYZ2bcdeCg65jkm8oFqi1tuvAxyz'

def validate_xrp(value):
    if value.startswith(('X','T')):
        return result(value,method='XRP_XADDRESS',reason='XRP X-address検証未対応')
    valid=base58_check_bytes(value,{b'\0'},alphabet=ALPHABET)
    return result(value,network='xrp-ledger',validation='CHECKSUM_VALID' if valid else 'INVALID',
        method='XRP_BASE58',reason='' if valid else 'XRP classicの接頭辞・文字・長さ・チェックサム不整合',
        detail='XRPL固有のBase58文字表、0接頭辞、20バイトaccount ID、二重SHA256チェックサムを検証。destination tagは原文に含まれない')
