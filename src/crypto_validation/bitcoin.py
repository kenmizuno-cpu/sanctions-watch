"""Bitcoin mainnetのBIP-173／BIP-350検証。原文の大小文字と既存IDを保持。"""
from . import bech32
from .common import result, validate_base58

def validate_bitcoin(value):
    if not value.lower().startswith(('bc1', 'tb1', 'bcrt1')):
        return validate_base58(value, 'bitcoin', {0, 5})
    hrp, _, encoding = bech32.bech32_decode(value)
    method = 'BECH32M' if encoding == bech32.Encoding.BECH32M else 'BECH32'
    version, _ = bech32.decode('bc', value)
    valid = version is not None
    reason = '' if valid else ('Bitcoin mainnetの接頭辞ではない' if hrp and hrp != 'bc'
                               else 'Bech32／Bech32mの形式・チェックサム不正')
    return result(value, network='bitcoin', validation='CHECKSUM_VALID' if valid else 'INVALID',
                  method=method, reason=reason,
                  detail='文字・大小文字・接頭辞・長さ・余剰ビット・witness版に対応するチェックサムを検証')
