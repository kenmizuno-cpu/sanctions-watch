"""Solana 32バイトBase58。PDAを排除する曲線上判定を行わない。"""
from .common import decode_base58, result

def validate_solana(value):
    try:
        valid=len(decode_base58(value))==32
    except (ValueError,OverflowError):
        valid=False
    return result(value,network='solana',validation='FORMAT_ONLY' if valid else 'INVALID',
        method='BASE58_32',reason='形式正常・仕様に文字列チェックサムなし' if valid else 'SolanaのBase58文字・32バイト長に不整合',
        detail='32バイトBase58形式を確認。PDAも許容。文字列チェックサム、実ネットワーク、所有者、活動有無は判定しない')
