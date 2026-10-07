"""通貨別検証の入口。掲載関係IDの旧識別基準と検証候補を分離。"""
from .bitcoin import validate_bitcoin
from .ethereum import validate_ethereum
from .common import result, validate_base58, base58_check
from .cashaddr import validate_cashaddr
from .monero import validate_monero
from .solana import validate_solana
from .xrp import validate_xrp
from .utxo import validate_utxo
from .binance import validate_binance
from .tokens import validate_token
from .identity import legacy_identity

def normalize_address(symbol: str, value: str) -> dict:
    if not value or len(value)>256 or value!=value.strip() or any(c.isspace() for c in value):
        return result(value, validation='INVALID', method='TEXT', reason='原文の形式不正',
                      detail='空欄・空白混入・文字数上限の検証に失敗')
    if symbol=='XBT':
        checked=validate_bitcoin(value)
        if checked['validation']=='INVALID' and base58_check(value,{65}):
            checked.update(network_candidates=['tron-family'],network_resolution='FAMILY_ONLY',
                review_reason='公式通貨記号XBTと形式が不整合：TRON系の形式・チェックサムに一致（原文保持）',
                validation_detail='Bitcoin形式の検証に失敗。TRON系の0x41接頭辞・25バイト・二重SHA256は一致するが、公式通貨記号と実ネットワークを自動修正しない')
    elif symbol in {'ETH','ETC','BSC','ARB'}:
        checked=validate_ethereum(value)
        checked['network']={'ETH':'ethereum','ETC':'ethereum-classic','BSC':'bsc','ARB':'arbitrum'}[symbol]
    elif symbol in {'USDT','USDC'}: checked=validate_token(value)
    elif symbol=='BCH': checked=validate_cashaddr(value)
    elif symbol=='XMR': checked=validate_monero(value)
    elif symbol=='SOL': checked=validate_solana(value)
    elif symbol=='XRP': checked=validate_xrp(value)
    elif symbol=='BNB': checked=validate_binance(value)
    elif symbol in {'LTC','BSV','BTG','XVG','ZEC'}: checked=validate_utxo(symbol,value)
    elif symbol in {'TRX','DOGE','DASH'}:
        network,prefixes={'TRX':('tron',{65}),'DOGE':('dogecoin',{30,22}),'DASH':('dash',{76,16})}[symbol]
        checked=validate_base58(value,network,prefixes)
    else: checked=result(value)
    if not checked['network_candidates'] and checked['network'] and checked['validation'] in {'CHECKSUM_VALID','FORMAT_ONLY'}:
        checked.update(network_candidates=[checked['network']],network_resolution='SYMBOL_AND_FORMAT')
    # 検証器の導入以前から使うID基準を固定。新しい推定は別フィールドのみ。
    checked['network'],checked['normalized_address']=legacy_identity(symbol,value)
    return checked
