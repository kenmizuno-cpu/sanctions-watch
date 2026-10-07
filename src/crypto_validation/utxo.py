"""LTC/BSV/BTG/XVG/ZEC透明アドレス。未対応の種別は不正扱いしない。"""
from . import bech32
from .common import base58_check_bytes, result, validate_base58

def validate_witness(value, network, hrp):
    _,_,encoding=bech32.bech32_decode(value)
    version,_=bech32.decode(hrp,value)
    valid=version is not None
    return result(value,network=network,validation='CHECKSUM_VALID' if valid else 'INVALID',
        method='BECH32M' if encoding==bech32.Encoding.BECH32M else 'BECH32',
        reason='' if valid else 'SegWitの接頭辞・形式・チェックサム不整合',
        detail='指定された接頭辞、大小文字、padding、witness版・program長、Bech32/Bech32mを検証')

def validate_utxo(symbol,value):
    settings={'LTC':('litecoin',{48,50,5},'ltc'), 'BSV':('bitcoin-sv',{0,5},''),
              'BTG':('bitcoin-gold',{38,23},'btg'), 'XVG':('verge',{30,33},'vg')}
    if symbol=='LTC' and value.lower().startswith(('ltcmweb1','tltcmweb1')):
        return result(value,reason='Litecoin MWEBアドレス検証未対応')
    if symbol=='ZEC':
        if value.lower().startswith(('zs','zc','u1','utest1','tex1','textest1')):
            return result(value,reason='Zcash shielded/unified/TEXアドレス検証未対応')
        valid=base58_check_bytes(value,{b'\x1c\xb8',b'\x1c\xbd'})
        return result(value,network='zcash',validation='CHECKSUM_VALID' if valid else 'INVALID',
            method='BASE58CHECK',reason='' if valid else 'Zcash透明アドレスの接頭辞・長さ・チェックサム不整合',
            detail='透明アドレスの2バイトmainnet接頭辞、20バイトhash、二重SHA256チェックサムを検証')
    network,prefixes,hrp=settings[symbol]
    if hrp and value.lower().startswith(hrp+'1'):
        return validate_witness(value,network,hrp)
    return validate_base58(value,network,prefixes)
