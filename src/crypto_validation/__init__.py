"""通貨別検証の入口。ネットワークは公式の通貨記号の対応範囲だけを使う。"""
import re
from .bitcoin import validate_bitcoin
from .ethereum import validate_ethereum
from .common import result, validate_base58

def normalize_address(symbol: str, value: str) -> dict:
    if not value or len(value)>256 or value!=value.strip() or any(c.isspace() for c in value):
        return result(value, validation='INVALID', method='TEXT', reason='原文の形式不正',
                      detail='空欄・空白混入・文字数上限の検証に失敗')
    if symbol == 'XBT':
        return validate_bitcoin(value)
    if symbol == 'ETH':
        return validate_ethereum(value)
    if symbol == 'ETC':
        valid=bool(re.fullmatch(r'0x[0-9a-fA-F]{40}', value))
        return result(value.lower() if valid else value, network='ethereum-classic',
                      validation='FORMAT_ONLY' if valid else 'INVALID', method='HEX20',
                      reason='ETCチェックサム検証未対応' if valid else '原文の形式不正',
                      detail='ETCの20バイト16進形式を確認。チェーン固有のチェックサムは未検証')
    networks={'TRX':('tron',{65}), 'LTC':('litecoin',{48,50,5}),
              'DOGE':('dogecoin',{30,22}), 'DASH':('dash',{76,16})}
    if symbol in networks:
        network, prefixes=networks[symbol]
        if symbol=='LTC' and value.lower().startswith('ltc1'):
            return result(value, network=network, validation='FORMAT_ONLY', method='BECH32_UNVERIFIED',
                          reason='Bech32チェックサム未検証', detail='LitecoinのBech32検証は後続工程')
        return validate_base58(value, network, prefixes)
    return result(value)
