"""Monero固有のブロックBase58とKeccak-256先頭4バイトの検証。"""
from .common import BASE58, result

def decode_monero(value):
    sizes={2:1,3:2,5:3,6:4,7:5,9:6,10:7,11:8}
    decoded=bytearray()
    for offset in range(0,len(value),11):
        block=value[offset:offset+11]
        size=sizes.get(len(block))
        if size is None: raise ValueError('Base58ブロック長')
        number=0
        for char in block: number=number*58+BASE58.index(char)
        decoded.extend(number.to_bytes(size,'big'))
    return bytes(decoded)

def validate_monero(value):
    valid=False
    reason='Moneroの95/106文字形式・接頭辞・チェックサム不整合（原文保持）'
    if len(value) in {95,106}:
        try:
            raw=decode_monero(value)
        except (ValueError,OverflowError):
            raw=b''
        if raw and ((len(raw)==69 and raw[0] in {18,42}) or (len(raw)==77 and raw[0]==19)):
            try:
                from Crypto.Hash import keccak
            except ImportError as exc:
                raise ValueError('Monero検証依存が未設定：requirements-crypto.txtをインストールしてください') from exc
            valid=keccak.new(digest_bits=256,data=raw[:-4]).digest()[:4]==raw[-4:]
    return result(value,network='monero',validation='CHECKSUM_VALID' if valid else 'INVALID',
        method='MONERO_BASE58',reason='' if valid else reason,
        detail='標準/サブ/統合アドレスのブロックBase58・mainnet接頭辞・バイト長・Keccakチェックサムを検証。所有者や公開鍵の有効性は判定しない')
