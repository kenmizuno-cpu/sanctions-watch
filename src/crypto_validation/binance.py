"""BNB Beacon Chainの旧bnbアドレス形式。稼働状態とは区別。"""
from . import bech32
from .common import result

def validate_binance(value):
    if value.startswith('0x'):
        return result(value,reason='BNB通貨記号だけではBeacon/BSCを確定できない')
    hrp,data,encoding=bech32.bech32_decode(value)
    raw=bech32.convertbits(data,5,8,False) if data is not None else None
    valid=hrp=='bnb' and encoding==bech32.Encoding.BECH32 and raw is not None and len(raw)==20
    return result(value,network='bnb-beacon',validation='CHECKSUM_VALID' if valid else 'INVALID',
        method='BNB_BECH32',reason='' if valid else 'BNB bnb形式の接頭辞・長さ・チェックサム不整合',
        detail='旧Beacon Chainのbnb接頭辞、20バイト、padding、Bech32チェックサムを検証。チェーンの稼働状態は判定しない')
