"""検証結果の共通形式と、既存のBase58Check検証。"""
import hashlib

VALIDATION_VERSION = '3'
BASE58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'

def result(value, *, network='', validation='UNSUPPORTED', method='UNSUPPORTED',
           reason='ネットワーク未確定・検証未対応', detail='対象通貨・ネットワークの検証器は未対応'):
    return dict(network=network, normalized_address=value, validation=validation,
                review_reason=reason, validation_method=method,
                validation_detail=detail, validation_version=VALIDATION_VERSION,
                review_category=('INCONSISTENCY' if validation=='INVALID' else
                                 'UNSUPPORTED' if validation=='UNSUPPORTED' else 'LIMITATION') if reason else '',
                network_candidates=[], network_resolution='UNRESOLVED')

def decode_base58(value, alphabet=BASE58):
    if not value: raise ValueError('空のBase58')
    number = 0
    for char in value:
        number = number * 58 + alphabet.index(char)
    body = number.to_bytes((number.bit_length() + 7) // 8, 'big')
    return b'\0' * (len(value)-len(value.lstrip(alphabet[0]))) + body

def base58_check_bytes(value, prefixes, payload_length=20, alphabet=BASE58):
    try:
        body = decode_base58(value, alphabet)
        return any(len(body)==len(prefix)+payload_length+4 and body.startswith(prefix) and
                   hashlib.sha256(hashlib.sha256(body[:-4]).digest()).digest()[:4]==body[-4:]
                   for prefix in prefixes)
    except (ValueError, OverflowError):
        return False

def base58_check(value, prefixes):
    return base58_check_bytes(value, {bytes([p]) for p in prefixes})

def validate_base58(value, network, prefixes):
    valid = base58_check(value, prefixes)
    return result(value, network=network, validation='CHECKSUM_VALID' if valid else 'INVALID',
                  method='BASE58CHECK', reason='' if valid else '原文のチェックサム・形式不正',
                  detail='Base58文字・25バイト長・通貨の接頭辞・二重SHA256チェックサムを検証')

def review_category(row):
    if not row.get('review_reason'): return ''
    return ('INCONSISTENCY' if row.get('validation')=='INVALID' else
            'UNSUPPORTED' if row.get('validation')=='UNSUPPORTED' else 'LIMITATION')
