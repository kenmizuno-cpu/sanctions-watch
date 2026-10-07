"""検証結果の共通形式と、既存のBase58Check検証。"""
import hashlib

VALIDATION_VERSION = '2'
BASE58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'

def result(value, *, network='', validation='UNSUPPORTED', method='UNSUPPORTED',
           reason='ネットワーク未確定・検証未対応', detail='対象通貨・ネットワークの検証器は未対応'):
    return dict(network=network, normalized_address=value, validation=validation,
                review_reason=reason, validation_method=method,
                validation_detail=detail, validation_version=VALIDATION_VERSION)

def base58_check(value, prefixes):
    try:
        number = 0
        for char in value:
            number = number * 58 + BASE58.index(char)
        body = number.to_bytes((number.bit_length() + 7) // 8, 'big')
        body = b'\0' * (len(value) - len(value.lstrip('1'))) + body
        return (len(body) == 25 and body[0] in prefixes and
                hashlib.sha256(hashlib.sha256(body[:-4]).digest()).digest()[:4] == body[-4:])
    except (ValueError, OverflowError):
        return False

def validate_base58(value, network, prefixes):
    valid = base58_check(value, prefixes)
    return result(value, network=network, validation='CHECKSUM_VALID' if valid else 'INVALID',
                  method='BASE58CHECK', reason='' if valid else '原文のチェックサム・形式不正',
                  detail='Base58文字・25バイト長・通貨の接頭辞・二重SHA256チェックサムを検証')
