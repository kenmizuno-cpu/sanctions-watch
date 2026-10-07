"""Ethereum EIP-55。Keccak-256を使用し、SHA3-256で代用しない。"""
import re
from .common import result

def validate_ethereum(value):
    if not re.fullmatch(r'0x[0-9a-fA-F]{40}', value):
        return result(value, network='ethereum', validation='INVALID', method='HEX20',
                      reason='原文の形式不正', detail='0x接頭辞と40桁の16進文字を満たさない')
    lowered = value.lower()
    chars = value[2:]
    if chars == chars.lower() or chars == chars.upper():
        return result(lowered, network='ethereum', validation='FORMAT_ONLY', method='HEX20',
                      reason='形式正常・チェックサム情報なし（大小文字の区別なし）',
                      detail='20バイトの16進形式を確認。原文に誤記検出用の大小文字情報がないためチェックサム検証済みとしない')
    try:
        from Crypto.Hash import keccak
    except ImportError as exc:
        raise ValueError('Ethereum検証依存が未設定：requirements-crypto.txtをインストールしてください') from exc
    hashed = keccak.new(digest_bits=256, data=lowered[2:].encode('ascii')).hexdigest()
    checksummed = '0x'+''.join(c.upper() if int(hashed[i],16)>=8 else c for i,c in enumerate(lowered[2:]))
    valid = value == checksummed
    return result(lowered, network='ethereum', validation='CHECKSUM_VALID' if valid else 'INVALID',
                  method='EIP55', reason='' if valid else 'EIP-55チェックサム不一致（原文保持・要確認）',
                  detail='原文の大小文字をKeccak-256に基づくEIP-55と照合。補正した原文へ置き換えない')
