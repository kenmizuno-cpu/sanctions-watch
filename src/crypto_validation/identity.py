"""旧台帳の識別基準。検証候補の追加で掲載関係IDを変更しない。"""
import re

NETWORKS = {'XBT':'bitcoin','ETH':'ethereum','ETC':'ethereum-classic',
            'TRX':'tron','LTC':'litecoin','DOGE':'dogecoin','DASH':'dash'}

def legacy_identity(symbol, value):
    network = NETWORKS.get(symbol, '')
    normalized = value.lower() if symbol in {'ETH','ETC'} and re.fullmatch(r'0x[0-9a-fA-F]{40}',value) else value
    return network, normalized
