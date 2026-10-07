"""Bounded mainnet/provider/issuer allowlist. Update this file after primary-source review."""
from dataclasses import dataclass
import re
from ..crypto_validation.common import base58_check, decode_base58

TETHER = 'https://tether.to/en/supported-protocols/'
CIRCLE = 'https://developers.circle.com/stablecoins/usdc-contract-addresses'
REGISTRY_VERSION = '2026-10-07.1'

@dataclass(frozen=True)
class Target:
    chain: str
    asset: str = ''
    contract: str = ''
    issuer_source: str = ''

# chainId prevents a wrong/testnet RPC from being accepted. Latest != finalized.
EVM = {
 'ethereum': (1, 'finalized', ('https://ethereum-rpc.publicnode.com', 'https://eth.drpc.org')),
 'ethereum-classic': (61, 'latest', ('https://etc.drpc.org', 'https://geth-at.etc-network.info')),
 'bsc': (56, 'latest', ('https://bsc-rpc.publicnode.com', 'https://bsc.drpc.org')),
 'arbitrum': (42161, 'finalized', ('https://arbitrum-one-rpc.publicnode.com', 'https://arb1.arbitrum.io/rpc')),
 'avalanche': (43114, 'latest', ('https://avalanche-c-chain-rpc.publicnode.com', 'https://api.avax.network/ext/bc/C/rpc')),
 'base': (8453, 'finalized', ('https://base-rpc.publicnode.com', 'https://mainnet.base.org')),
}
PROVIDERS = {
 **{k: [('PublicNode',v[2][0]), ('LlamaNodes' if k=='ethereum' else
        'Rivet' if k=='ethereum-classic' else 'dRPC' if k=='bsc' else 'Network public RPC', v[2][1])]
    for k,v in EVM.items()},
 'ethereum': [('PublicNode',EVM['ethereum'][2][0]),('dRPC',EVM['ethereum'][2][1])],
 'ethereum-classic': [('dRPC',EVM['ethereum-classic'][2][0]),('ETC-Network.info',EVM['ethereum-classic'][2][1])],
 'tron': [('PublicNode','https://tron-solidity-rpc.publicnode.com'),('TronGrid','https://api.trongrid.io')],
 'bitcoin': [('Blockstream','https://blockstream.info/api'),('mempool.space','https://mempool.space/api')],
 'solana': [('Solana public RPC','https://api.mainnet-beta.solana.com'),('PublicNode','https://solana-rpc.publicnode.com')],
}
TOKENS = {
 'USDT': [Target('ethereum','USDT','0xdac17f958d2ee523a2206206994597c13d831ec7',TETHER),
          Target('avalanche','USDT','0x9702230a8ea53601f5cd2dc00fdbc13d4df4a8c7',TETHER)],
 'USDC': [Target('ethereum','USDC','0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',CIRCLE),
          Target('arbitrum','USDC','0xaf88d065e77c8cc2239327c5edb3a432268e5831',CIRCLE),
          Target('base','USDC','0x833589fcd6edb6e08f4c7c32d4f71b54bda02913',CIRCLE)],
}
TRON_USDT = Target('tron','USDT','TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t',TETHER)

def targets(row):
    if (row.get('listing_status')!='LISTED' or row.get('review_category')!='LIMITATION'
        or row.get('validation') in ('INVALID','UNSUPPORTED')): return []
    symbol, a = row['symbol'], row['address']
    if re.fullmatch(r'0x[0-9a-fA-F]{40}',a):
        if symbol in TOKENS: return list(TOKENS[symbol])
        network={'ETH':'ethereum','ETC':'ethereum-classic','BSC':'bsc','ARB':'arbitrum'}.get(symbol)
        return [Target(network)] if network else []
    if symbol=='SOL' and len(decode_base58(a))==32: return [Target('solana')]
    if symbol=='USDT':
        if base58_check(a,{65}): return [TRON_USDT]
        if base58_check(a,{0,5}): return [Target('bitcoin','USDT')]
    return []
